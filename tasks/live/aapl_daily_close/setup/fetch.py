"""Host-side Yahoo prices and no-change reference; stdlib + pandas only.

No API key or other credential is required.
Calls in the same process share a cached Yahoo response. Separate CLI commands
refetch the completed-session history. The pipeline retains first-seen truth.
No publisher scraping or credentials are exposed to the evaluated agent.
"""

from __future__ import annotations

import json
import math
import time
from datetime import date, datetime, timedelta
from datetime import time as clock
from functools import cache
from pathlib import Path
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


def _guard(run_date, spec):
    now = _now()
    cutoff = datetime.combine(
        run_date, clock.fromisoformat(spec["schedule"]["cutoff"]), ZONE
    )
    if run_date != now.date() or now >= cutoff:
        raise RuntimeError(
            "Snapshots/baselines require today and must finish before the cutoff."
        )
    return now


def snapshot(run_date, spec):
    as_of = _guard(run_date, spec)
    start = date.fromisoformat(spec["x"]["history_start"])
    through = run_date - timedelta(days=1)
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


def _snapshot_reference(run_date, spec):
    """The daily pipeline collects B on the next day; use the frozen run-day X_d."""
    x = (
        Path(__file__).resolve().parents[3]
        / "mock"
        / spec["target"]
        / run_date.isoformat()
        / "oracle"
        / "assets"
        / "x"
    )
    if not x.exists():
        return None
    task = json.loads((x / "task.json").read_text())
    manifest = json.loads((x / "MANIFEST.json").read_text())
    observed = date.fromisoformat(task["last_observed"])
    as_of = datetime.fromisoformat(manifest["sources"]["as_of"])
    cutoff = datetime.combine(
        run_date, clock.fromisoformat(spec["schedule"]["cutoff"]), ZONE
    )
    if (
        task["run_date"] != run_date.isoformat()
        or observed >= run_date
        or as_of.tzinfo is None
        or as_of.astimezone(ZONE).date() != run_date
        or as_of >= cutoff
    ):
        raise RuntimeError(
            "Archived reference is not a valid pre-cutoff run-day snapshot."
        )
    series = pd.read_csv(x / "series.csv", index_col="timestamp", parse_dates=True)
    point = float(series.loc[pd.Timestamp(observed), "target"])
    if not math.isfinite(point) or point <= 0:
        raise RuntimeError("Archived reference closing price is invalid.")
    return (
        point,
        observed.isoformat(),
        as_of.isoformat(),
        f"{spec['target']}/{run_date}",
    )


def baseline(run_date, spec):
    if run_date > _now().date():
        raise RuntimeError("Cannot build a baseline for a future run day.")
    saved = _snapshot_reference(run_date, spec)
    if saved is not None:
        point, last_observed, as_of, snapshot_path = saved
        source = {
            "name": "No-change reference",
            "url": YAHOO,
            "as_of": as_of,
            "last_observed": last_observed,
            "snapshot": snapshot_path,
        }
    else:
        # A direct pre-cutoff check is allowed, but no historical reconstruction.
        as_of = _guard(run_date, spec)
        past = _daily(
            date.fromisoformat(spec["x"]["history_start"]), run_date - timedelta(days=1)
        )
        point = float(past.iloc[-1])
        _guard(run_date, spec)
        source = {
            "name": "No-change reference",
            "url": YAHOO,
            "as_of": as_of.isoformat(),
            "last_observed": past.index[-1].date().isoformat(),
        }
    return {
        "source": source,
        "forecasts": [
            {"target_date": (run_date + timedelta(days=h)).isoformat(), "point": point}
            for h in spec["horizons"]
        ],
    }
