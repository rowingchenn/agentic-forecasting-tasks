"""AAPL official closing price: where the numbers for this target come from.

The pipeline imports this file on the host, outside every episode. No credentials: Yahoo
Finance's daily chart is keyless. Its close equals Nasdaq's official closing price.

    snapshot(run_date, spec)  X_d: the close history through the run date, as published now
    truth(today, spec)        every completed day's close as published now
    baseline(run_date, spec)  no change from the run date's close
"""

from __future__ import annotations

import json
import math
import time
from datetime import date, datetime, timedelta
from datetime import time as clock
from functools import cache
from typing import ClassVar
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
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
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/AAPL"


class ExchangeHolidays(AbstractHolidayCalendar):
    # Regular US equity closures since the task history starts in 2024. For New Year's,
    # Saturday is not observed on Friday (unlike the federal holiday calendar).
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
        Holiday("Carter mourning", year=2025, month=1, day=9),
    ]


def _now():
    return datetime.now(ZONE)


def _get(url, headers=None):
    """Bounded retries, verified system TLS, and errors without credentials/URLs."""
    for attempt in range(4):
        try:
            request = Request(
                url, headers={"User-Agent": "Mozilla/5.0", **(headers or {})}
            )
            with urlopen(request, timeout=45) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise RuntimeError(
                    f"Data provider returned HTTP {exc.code}; check access/plan."
                ) from None
            delay = exc.headers.get("Retry-After", "")
            time.sleep(min(60, max(2**attempt, int(delay) if delay.isdigit() else 0)))
        except (URLError, TimeoutError):
            if attempt == 3:
                raise RuntimeError(
                    "Data provider network/TLS request failed."
                ) from None
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def _sessions(start, end):
    days = pd.date_range(start, end, name="timestamp")
    holidays = ExchangeHolidays().holidays(start, end)
    return days[(days.dayofweek < 5) & ~days.isin(holidays)]


@cache
def _prices(start_text, through_text):
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
    payload = _get(YAHOO + "?" + query)
    chart = payload.get("chart", {}) if isinstance(payload, dict) else {}
    result = chart.get("result")
    if chart.get("error") or not result:
        raise RuntimeError("Yahoo returned no usable AAPL chart result.")
    chart = result[0]
    if chart.get("meta", {}).get("symbol") != "AAPL":
        raise RuntimeError("Yahoo symbol does not match AAPL.")
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
    # For some hours after the close, Yahoo leaves the day's bar without a close; its quote
    # then shows the session's last regular-market price, which is that close.
    meta, last = chart["meta"], frame.index[-1]
    closed = meta.get("currentTradingPeriod", {}).get("regular", {}).get("end", 0)
    if (pd.isna(frame.at[last, "close"]) and meta.get("regularMarketTime", 0) >= closed > 0
            and datetime.fromtimestamp(closed, ZONE).date() == last.date()):
        frame.at[last, "close"] = meta["regularMarketPrice"]
    frame["close"] = frame["close"].round(2)  # cents; Yahoo sends float32 noise
    frame = frame.loc[frame.index <= pd.Timestamp(through)].sort_index()
    if frame.index.has_duplicates:
        raise RuntimeError("Yahoo returned duplicate daily bars.")
    adjusted = chart.get("indicators", {}).get("adjclose", [])
    if adjusted:
        values = pd.Series(
            adjusted[0]["adjclose"],
            index=pd.DatetimeIndex(
                [datetime.fromtimestamp(t, ZONE).date() for t in stamps]
            ),
        )
        frame["adjclose_audit_only"] = values.reindex(frame.index)
    expected = _sessions(start - timedelta(days=14), through)
    # Refuse missing sessions instead of treating outages as holidays.
    if len(expected.difference(frame.index)):
        raise RuntimeError(
            "Yahoo is missing expected sessions; inspect data or exceptional closures."
        )
    closes = pd.to_numeric(frame["close"], errors="coerce")
    if closes.empty or not closes.map(lambda v: math.isfinite(v) and v > 0).all():
        raise RuntimeError("Yahoo returned missing or non-positive closing prices.")
    return frame


def _daily(start, through):
    frame = _prices(start.isoformat(), through.isoformat())
    index = pd.date_range(start, through, name="timestamp")
    series = (
        frame["close"]
        .reindex(frame.index.union(index))
        .sort_index()
        .ffill()
        .reindex(index)
    )
    if series.isna().any():
        raise RuntimeError("No closing-price seed for the beginning of history.")
    return series.rename("target")


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
    through = run_date  # its close is published at 16:00 ET, before the 16:45 start
    past = _daily(start, through)
    _guard(run_date, spec)  # Do not archive a slow collection that crossed the cutoff.
    end = run_date + timedelta(days=max(spec["horizons"]))
    index = pd.date_range(start, end, name="timestamp")
    series = pd.DataFrame(
        {"target": past.reindex(index), "dow": index.dayofweek, "month": index.month},
        index=index,
    )
    prices = _prices(start.isoformat(), through.isoformat())
    return {
        "series": series,
        "last_observed": through,
        "raw": {"prices.csv": prices.reset_index()},
        "sources": {
            "prices": YAHOO,
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
    """No change from the run date's official close (published at 16:00 ET, before the
    cutoff; on a Monday holiday, the previous session's), for every target date. Read from
    Yahoo the day after the run date."""
    source = {"name": "No-change reference: AAPL's official close on the run date", "url": YAHOO}
    if _now() < _cutoff(run_date, spec):
        return {"source": source, "forecasts": []}
    point = float(_daily(run_date, run_date).iloc[0])
    return {
        "source": source,
        "forecasts": [
            {"target_date": (run_date + timedelta(days=h)).isoformat(), "point": point}
            for h in spec["horizons"]
        ],
    }
