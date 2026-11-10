"""CBOT corn futures settlement: where the numbers for this target come from.

The pipeline imports this file on the host, outside every episode. No credentials: both
sources are keyless.

- Yahoo Finance's daily chart of ZC=F, the nearby corn contract, read the day after a
  session: its close then equals the CBOT settlement that USDA Market News republishes.
  B also reads the dated chart of the next contract (ZCH27.CBT for March 2027).
- USDA Market News's daily grain reports, for the run date's own settlement at the snapshot.
  On a trading day Yahoo's newest bar can still be open then, and its live quote follows the
  most active contract, which weeks before a last trading day is not the nearby one.

    snapshot(run_date, spec)  X_d: the settlement history through the run date, as published now
    truth(today, spec)        every completed day's settlement as published now
    baseline(run_date, spec)  no change from the run date's settlement of each date's contract
"""

from __future__ import annotations

import json
import math
import re
import time
import zlib
from datetime import date, datetime, timedelta
from datetime import time as clock
from functools import cache
from typing import ClassVar
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd
from pandas.tseries.holiday import (
    AbstractHolidayCalendar,
    GoodFriday,
    Holiday,
    USLaborDay,
    USMartinLutherKingJr,
    USMemorialDay,
    USPresidentsDay,
    USThanksgivingDay,
    nearest_workday,
    sunday_to_monday,
)

ZONE = ZoneInfo("America/New_York")
SYMBOL = "ZC=F"
CHART = "https://query1.finance.yahoo.com/v8/finance/chart/"
YAHOO = CHART + "ZC%3DF"
USER_AGENT = "agentic-forecasting/1.0"
# Afternoon USDA reports whose "Futures Settlements" block lists the day's CBOT settlements,
# nearest contract first. Of the 200 days from 2026-01-22 to 11-06 with an issue, one of the
# two was posted by 15:45 CT (16:45 ET) on all 200: Southern Minnesota by then on 199, North
# Dakota on 182. USDA publishes nothing on federal holidays when corn still trades, such as
# Columbus Day; the run date's value then stays empty.
USDA_REPORTS = (
    "https://www.ams.usda.gov/mnreports/ams_3049.pdf",  # Southern Minnesota Daily Grain Bids
    "https://www.ams.usda.gov/mnreports/ams_3878.pdf",  # North Dakota Daily Grain Bids
)
CONTRACT_MONTHS = {"Mar": 3, "May": 5, "Jul": 7, "Sep": 9, "Dec": 12}
MONTH_CODES = {3: "H", 5: "K", 7: "N", 9: "U", 12: "Z"}
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")


class GrainHolidays(AbstractHolidayCalendar):
    # Weekdays without a CBOT grain session since the task history starts in 2024. Unlike
    # the stock exchanges, the grain markets traded on 2025-01-09 (the Carter day of
    # mourning). Saturday New Year's Day is not observed on the Friday before.
    rules: ClassVar[list] = [
        Holiday("New Year", month=1, day=1, observance=sunday_to_monday),
        USMartinLutherKingJr,
        USPresidentsDay,
        GoodFriday,
        USMemorialDay,
        Holiday("Juneteenth", month=6, day=19, observance=nearest_workday),
        Holiday("Independence Day", month=7, day=4, observance=nearest_workday),
        USLaborDay,
        USThanksgivingDay,
        Holiday("Christmas", month=12, day=25, observance=nearest_workday),
    ]


def _now():
    return datetime.now(ZONE)


def _fetch(url, accept="application/json", attempts=4, timeout=45):
    """Bounded retries, verified system TLS, and errors without URLs."""
    for attempt in range(attempts):
        last = attempt == attempts - 1
        try:
            # USDA's servers stall on requests that carry no Accept header.
            request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or last:
                raise RuntimeError(
                    f"Data provider returned HTTP {exc.code}; check access."
                ) from None
            delay = exc.headers.get("Retry-After", "")
            time.sleep(min(60, max(2**attempt, int(delay) if delay.isdigit() else 0)))
        except (OSError, HTTPException):  # also connections dropped while reading the body
            if last:
                raise RuntimeError("Data provider network/TLS request failed.") from None
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def _get(url):
    return json.loads(_fetch(url))


def _sessions(start, end):
    days = pd.date_range(start, end, name="timestamp")
    holidays = GrainHolidays().holidays(start, end)
    return days[(days.dayofweek < 5) & ~days.isin(holidays)]


def _last_trading_day(year, month):
    """CBOT corn: the business day before the 15th calendar day of the contract month."""
    day = date(year, month, 14)
    while not len(_sessions(day, day)):
        day -= timedelta(days=1)
    return day


def _nearby(day):
    """(year, month) of the nearby contract on a date: the nearest listed month whose last
    trading day is on or after it."""
    for year in (day.year, day.year + 1):
        for month in CONTRACT_MONTHS.values():
            if _last_trading_day(year, month) >= day:
                return year, month
    raise AssertionError("unreachable")


def _settling(day):
    """(year, month) of the contract a date's value is a settlement of: the nearby contract
    of its session, or on a weekend or holiday of the previous session."""
    return _nearby(_sessions(day - timedelta(days=10), day)[-1].date())


@cache
def _prices(start_text, through_text, symbol=SYMBOL):
    start, through = date.fromisoformat(start_text), date.fromisoformat(through_text)
    # Fetch a seed before history_start so a holiday/weekend can be forward-filled.
    begin = datetime.combine(start - timedelta(days=14), clock(), ZONE)
    end = datetime.combine(through + timedelta(days=1), clock(), ZONE)
    query = urlencode(
        {
            "period1": int(begin.timestamp()),
            "period2": int(end.timestamp()),
            "interval": "1d",
            "includePrePost": "false",
            "events": "div,splits",
        }
    )
    payload = _get(CHART + quote(symbol, safe=".") + "?" + query)
    chart = payload.get("chart", {}) if isinstance(payload, dict) else {}
    result = chart.get("result")
    if chart.get("error") or not result:
        raise RuntimeError(f"Yahoo returned no usable {symbol} chart result.")
    chart = result[0]
    if chart.get("meta", {}).get("symbol") != symbol:
        raise RuntimeError(f"Yahoo symbol does not match {symbol}.")
    stamps = chart.get("timestamp", [])
    quotes = chart.get("indicators", {}).get("quote", [])
    if not stamps or not quotes:
        raise RuntimeError("Yahoo returned an empty daily price history.")
    frame = pd.DataFrame(quotes[0])
    if len(frame) != len(stamps) or not {
        "open",
        "high",
        "low",
        "close",
        "volume",
    } <= set(frame):
        raise RuntimeError("Yahoo OHLCV shape/columns are invalid.")
    frame.index = pd.DatetimeIndex(
        [datetime.fromtimestamp(t, ZONE).date() for t in stamps], name="timestamp"
    )
    frame = frame.loc[frame.index <= pd.Timestamp(through)].sort_index()
    if frame.index.has_duplicates:
        raise RuntimeError("Yahoo returned duplicate daily bars.")
    expected = _sessions(start - timedelta(days=14), through)
    # Yahoo sometimes lists an exchange holiday as a bar without prices; it is not a session.
    frame = frame.loc[frame.index.isin(expected) | frame["close"].notna()]
    frame["close"] = frame["close"].round(2)  # quarter cents; Yahoo sends float32 noise
    # Refuse missing sessions instead of treating outages as holidays.
    if len(expected.difference(frame.index)):
        raise RuntimeError(
            "Yahoo is missing expected sessions; inspect data or exceptional closures."
        )
    closes = pd.to_numeric(frame["close"], errors="coerce")
    if closes.empty or not closes.map(lambda v: math.isfinite(v) and v > 0).all():
        raise RuntimeError("Yahoo returned missing or non-positive settlement prices.")
    return frame


def _daily(start, through, symbol=SYMBOL):
    frame = _prices(start.isoformat(), through.isoformat(), symbol)
    index = pd.date_range(start, through, name="timestamp")
    series = (
        frame["close"]
        .reindex(frame.index.union(index))
        .sort_index()
        .ffill()
        .reindex(index)
    )
    if series.isna().any():
        raise RuntimeError("No settlement seed for the beginning of history.")
    return series.rename("target")


def _pdf_text(blob):
    """The literal text of a PDF: inflate each content stream and join its string operands.
    USDA's report PDFs write their text this way."""
    escapes = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f"}

    def unescape(match):
        code = match.group(1)
        return chr(int(code, 8)) if code[0].isdigit() else escapes.get(code, code)

    parts = []
    for stream in re.finditer(rb"stream\r?\n(.*?)endstream", blob, re.S):
        try:
            data = zlib.decompress(stream.group(1))
        except zlib.error:
            continue
        for literal in re.finditer(rb"\((?:\\.|[^\\()])*\)", data, re.S):
            text = literal.group(0)[1:-1].decode("latin-1")
            parts.append(re.sub(r"\\([0-7]{1,3}|.)", unescape, text, flags=re.S))
    return re.sub(r"\s+", " ", " ".join(parts))


def _usda_settlement(text):
    """(report date, settlement, (year, month)) of the first CBOT corn contract in a USDA
    report's "Futures Settlements" block, or None. The report date is the date in its
    header, which equalled the block's "as of" date on all 130 issues of report 3878 and 42
    of report 3049 checked (2026); the "as of" line itself is not plain text in the PDF."""
    header = re.search(rf"({'|'.join(MONTHS)})\s+(\d{{1,2}}),\s+(\d{{4}})", text)
    block = text.find("Futures Settlements")
    if not header or block < 0:
        return None
    corn = re.compile(r"CBOT\s+Corn\s+(\d+\.\d+)\s*\((Mar|May|Jul|Sep|Dec)\s+(\d\d)\)")
    found = corn.search(text, block)
    if not found:
        return None
    day = date(int(header[3]), MONTHS.index(header[1]) + 1, int(header[2]))
    return day, float(found[1]), (2000 + int(found[3]), CONTRACT_MONTHS[found[2]])


def _run_date_settlement(run_date):
    """The run date's settlement of the nearby contract from the first USDA report that
    already lists it, or (None, None). Optional by design: any failure leaves the run date
    empty instead of stopping the snapshot, and one short attempt per report keeps a
    stalling server from delaying the run."""
    for url in USDA_REPORTS:
        try:
            blob = _fetch(url, accept="application/pdf, */*", attempts=1, timeout=20)
            found = _usda_settlement(_pdf_text(blob))
        except Exception:  # noqa: BLE001 - any failure of this optional read is a miss
            continue
        if found and found[0] == run_date and found[2] == _nearby(run_date):
            return found[1], url
    return None, None


def _cutoff(run_date, spec):
    if spec["schedule"]["timezone"] != "America/New_York":
        raise ValueError("This target requires America/New_York calendar dates.")
    return datetime.combine(run_date, clock.fromisoformat(spec["schedule"]["cutoff"]), ZONE)


def _guard(run_date, spec):
    now = _now()
    if run_date != now.date() or now >= _cutoff(run_date, spec):
        raise RuntimeError("Snapshots require today and must finish before the cutoff.")
    return now


def snapshot(run_date, spec):
    as_of = _guard(run_date, spec)
    start = date.fromisoformat(spec["x"]["history_start"])
    before = run_date - timedelta(days=1)
    past = _daily(start, before)
    # The run date's settlement is published about 14:15 ET, before the 16:45 start, and
    # read from USDA; on a Monday holiday the value is the previous session's.
    value, report = float(past.iloc[-1]), None
    note = "no session on the run date: the previous session's settlement, from Yahoo"
    if len(_sessions(run_date, run_date)):
        value, report = _run_date_settlement(run_date)
        note = report or "not listed by USDA at the snapshot: left empty"
    _guard(run_date, spec)  # Do not archive a slow collection that crossed the cutoff.
    end = run_date + timedelta(days=max(spec["horizons"]))
    index = pd.date_range(start, end, name="timestamp")
    target = past.reindex(index)
    target[pd.Timestamp(run_date)] = math.nan if value is None else value
    series = pd.DataFrame(
        {"target": target, "dow": index.dayofweek, "month": index.month},
        index=index,
    )
    raw = {"prices.csv": _prices(start.isoformat(), before.isoformat()).reset_index()}
    if report:
        year, month = _nearby(run_date)
        raw["run_date_settlement.csv"] = pd.DataFrame(
            [{"date": run_date, "contract": f"{year}-{month:02d}", "settlement": value,
              "source": report}]
        )
    return {
        "series": series,
        "last_observed": before if value is None else run_date,
        "raw": raw,
        "sources": {
            "prices": YAHOO,
            "run_date_settlement": note,
            "as_of": as_of.isoformat(),
        },
        "covariate_notes": {
            "dow": "Calendar weekday, Monday=0, known for all dates.",
            "month": "Calendar month, 1 through 12, known for all dates.",
        },
    }


def truth(today, spec):
    if today > _now().date():
        raise RuntimeError("Cannot settle a future calendar day.")
    return _daily(
        date.fromisoformat(spec["x"]["history_start"]), today - timedelta(days=1)
    )


def baseline(run_date, spec):
    """No change from the run date's settlement (published about 14:15 ET, before the
    cutoff; on a Monday holiday, the previous session's) of the contract each target date
    settles on: the nearby one, or for dates after its last trading day the next contract.
    Read from Yahoo the day after the run date, once the day's bars hold the settlements:
    ZC=F for the nearby contract, the dated symbol (ZCH27.CBT) for the next."""
    name = "No-change reference: the run date's corn settlement of each date's contract"
    source = {"name": name, "url": YAHOO}
    if _now().date() <= run_date:
        return {"source": source, "forecasts": []}
    nearby, forecasts = _settling(run_date), []
    for h in spec["horizons"]:
        day = run_date + timedelta(days=h)
        year, month = _settling(day)
        dated = f"ZC{MONTH_CODES[month]}{year % 100:02d}.CBT"
        symbol = SYMBOL if (year, month) == nearby else dated
        point = float(_daily(run_date, run_date, symbol).iloc[0])
        forecasts.append({"target_date": day.isoformat(), "point": point})
    return {"source": source, "forecasts": forecasts}
