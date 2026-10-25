"""Host-side Yahoo prices and Finnhub news; stdlib + pandas only.

FINNHUB_API_KEY is required only by snapshot(), never by truth() or baseline().
Calls in the same process share a cached Yahoo response. Separate CLI commands
refetch the completed-session history. The pipeline retains first-seen truth.
No publisher scraping or credentials are exposed to the evaluated agent.
"""

from __future__ import annotations

import json
import math
import os
import time
from datetime import date, datetime, timedelta, timezone
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
FINNHUB = "https://finnhub.io/api/v1/company-news"
NEWS_COLUMNS = ["id", "published_at_utc", "headline", "summary", "source", "url"]


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


def _news(run_date, as_of, lookback):
    token = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not token:
        raise RuntimeError(
            "Set FINNHUB_API_KEY securely on the pipeline host for company-news access."
        )
    start = as_of - timedelta(days=lookback)
    records = {}
    # One query per UTC date mirrors the supplied extractor and limits truncation risk.
    day = start.astimezone(timezone.utc).date()
    end = as_of.astimezone(timezone.utc).date()
    while day <= end:
        query = urlencode(
            {"symbol": "AAPL", "from": day.isoformat(), "to": day.isoformat()}
        )
        payload = _get(FINNHUB + "?" + query, {"X-Finnhub-Token": token})
        if not isinstance(payload, list) or any(
            not isinstance(a, dict) for a in payload
        ):
            raise RuntimeError(
                "Finnhub returned an error or an invalid company-news response."
            )
        for article in payload:
            try:
                published = datetime.fromtimestamp(
                    int(article["datetime"]), timezone.utc
                )
            except (KeyError, TypeError, ValueError, OverflowError, OSError):
                raise RuntimeError(
                    "Finnhub article has an invalid publication timestamp."
                ) from None
            if not start <= published <= as_of:
                continue
            record = {
                "id": str(article.get("id", "")),
                "published_at_utc": published.isoformat(),
                **{key: str(article.get(key) or "") for key in NEWS_COLUMNS[2:]},
            }
            records[(record["id"], record["url"], record["published_at_utc"])] = record
        day += timedelta(days=1)
        if day <= end:
            time.sleep(1.1)
    return pd.DataFrame(
        sorted(records.values(), key=lambda a: a["published_at_utc"]),
        columns=NEWS_COLUMNS,
    )


def snapshot(run_date, spec):
    as_of = _guard(run_date, spec)
    if not os.environ.get("FINNHUB_API_KEY", "").strip():
        raise RuntimeError(
            "Set FINNHUB_API_KEY securely on the pipeline host for company-news access."
        )
    start = date.fromisoformat(spec["x"]["history_start"])
    through = run_date - timedelta(days=1)
    past = _daily(start, through)
    news = _news(run_date, as_of, int(spec["x"]["news_lookback_days"]))
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
        "raw": {"prices.csv": prices.reset_index(), "news.csv": news},
        "sources": {
            "prices": YAHOO,
            "news": FINNHUB,
            "as_of": as_of.isoformat(),
            "news_rows": len(news),
            "news_coverage": "Provider response only; completeness is not guaranteed.",
        },
        "covariate_notes": {
            "dow": "Calendar weekday, Monday=0, known for all dates.",
            "month": "Calendar month, 1 through 12, known for all dates.",
            "news": "Text in raw/news.csv; not consumed by the numeric TSFM interface.",
        },
    }


def truth(today, spec):
    if today > _now().date():
        raise RuntimeError("Cannot settle a future calendar day.")
    return _daily(
        date.fromisoformat(spec["x"]["history_start"]), today - timedelta(days=1)
    )


def baseline(run_date, spec):
    as_of = _guard(run_date, spec)
    past = _daily(
        date.fromisoformat(spec["x"]["history_start"]), run_date - timedelta(days=1)
    )
    point = float(past.iloc[-1])
    _guard(run_date, spec)
    return {
        "source": {
            "name": "No-change reference; not an expert forecast",
            "url": YAHOO,
            "as_of": as_of.isoformat(),
            "last_observed": past.index[-1].date().isoformat(),
        },
        "forecasts": [
            {"target_date": (run_date + timedelta(days=h)).isoformat(), "point": point}
            for h in spec["horizons"]
        ],
    }
