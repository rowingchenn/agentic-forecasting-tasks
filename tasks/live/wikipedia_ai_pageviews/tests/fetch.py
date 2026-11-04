"""Wikipedia "Artificial intelligence" daily page views: where the numbers come from.

The pipeline imports this file on the host, outside every episode. No credentials: the
Wikimedia pageviews API is keyless; requests send a descriptive User-Agent, as Wikimedia
asks. Requests use the fixed article title, all access methods and user-classified
traffic. A day the API does not report is missing (NaN in X_d), never zero.

    snapshot(run_date, spec)  X_d: the view history through the latest complete day
    truth(today, spec)        every completed day's views as published now
    baseline(run_date, spec)  none: no expert forecast of article views exists
"""

from __future__ import annotations

import json
import time
from datetime import UTC, date, datetime, time as clock, timedelta
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd

API = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
    "en.wikipedia.org/all-access/user/Artificial_intelligence/daily"
)
USER_AGENT = (
    "agentic-forecasting-wikipedia/1.0 "
    "(https://github.com/rowingchenn/agentic-forecasting-tasks)"
)
FILTERS = {
    "project": "en.wikipedia.org", "access": "all-access", "agent": "user",
    "article": "Artificial_intelligence", "granularity": "daily",
}


def _now():
    return datetime.now(UTC)


def _cutoff(run_date, spec):
    if spec["schedule"]["timezone"] != "UTC":
        raise ValueError("This target uses UTC day boundaries.")
    return datetime.combine(run_date, clock.fromisoformat(spec["schedule"]["cutoff"]), UTC)


def _guard(run_date, spec):
    now = _now()
    if now.date() != run_date or now >= _cutoff(run_date, spec):
        raise RuntimeError("A fresh snapshot requires the current UTC day before cutoff.")
    return now


def _get(url):
    """Retry transient failures only; do not retry missing articles or access denials."""
    for attempt in range(4):
        try:
            with urlopen(Request(url, headers={
                "User-Agent": USER_AGENT, "Accept": "application/json",
            }), timeout=60) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise
            retry = exc.headers.get("Retry-After", "")
            delay = min(120, int(retry)) if retry.isdigit() else 2 ** attempt
        except (URLError, TimeoutError):
            if attempt == 3:
                raise
            delay = 2 ** attempt
        time.sleep(delay)
    raise AssertionError("unreachable")


def _daily(start, end):
    """One request per calendar year; validated integer counts, NaN for a day the API lacks."""
    if start > end:
        raise ValueError("History start is after the last eligible observation.")
    records = []
    cursor = start
    while cursor <= end:
        stop = min(end, date(cursor.year, 12, 31))
        payload = _get(f"{API}/{cursor:%Y%m%d}00/{stop:%Y%m%d}00")
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise ValueError("Unexpected Wikimedia response schema.")
        batch = {}
        for item in payload["items"]:
            if not isinstance(item, dict):
                raise ValueError("Wikimedia returned a non-object daily record.")
            # Wikimedia normalizes en.wikipedia.org to en.wikipedia in responses.
            project = item.get("project")
            if project not in ("en.wikipedia", "en.wikipedia.org") or any(
                item.get(k) != v for k, v in FILTERS.items() if k != "project"
            ):
                raise ValueError("Wikimedia returned a different article or traffic filter.")
            stamp = item.get("timestamp")
            if not isinstance(stamp, str) or len(stamp) != 10 or not stamp.endswith("00"):
                raise ValueError("Invalid daily UTC timestamp.")
            day = datetime.strptime(stamp, "%Y%m%d%H").date()
            views = item.get("views")
            if type(views) is not int or views < 0:
                raise ValueError("Page views must be a non-negative integer.")
            if not cursor <= day <= stop or day in batch:
                raise ValueError("Duplicate or out-of-range daily record.")
            batch[day] = views
        records.extend({"timestamp": d, "views": batch[d], **FILTERS} for d in sorted(batch))
        cursor = stop + timedelta(days=1)
    raw = pd.DataFrame(records, columns=["timestamp", "views", *FILTERS])
    counts = pd.Series(raw["views"].to_numpy(), index=pd.to_datetime(raw["timestamp"]),
                       name="target", dtype=float)
    return counts.reindex(pd.date_range(start, end, name="timestamp")), raw


def snapshot(run_date: date, spec: dict) -> dict:
    as_of = _guard(run_date, spec)
    start = date.fromisoformat(spec["x"]["history_start"])
    # Wikimedia loads a UTC day a few hours after it ends, well before the 11:55 start;
    # when it is late, X_d ends a day earlier.
    past, raw = _daily(start, run_date - timedelta(days=1))
    last = past.last_valid_index()
    if last is None or last.date() < run_date - timedelta(days=2):
        raise RuntimeError("Wikimedia has not published the last two days' page views.")
    through = last.date()
    _guard(run_date, spec)  # A slow collection crossing the cutoff must not be archived.
    index = pd.date_range(start, run_date + timedelta(days=max(spec["horizons"])),
                          name="timestamp")
    return {
        "series": pd.DataFrame({
            "target": past.reindex(index), "dow": index.dayofweek, "month": index.month,
        }, index=index),
        "last_observed": through,
        "raw": {"pageviews_daily.csv": raw},
        "sources": {"pageviews": {"url": API, **FILTERS}, "as_of": as_of.isoformat()},
        "covariate_notes": {
            "dow": "UTC weekday, Monday=0, known for all dates.",
            "month": "Calendar month, 1 through 12, known for all dates.",
        },
    }


def truth(today: date, spec: dict) -> pd.Series:
    if today > _now().date():
        raise RuntimeError("Cannot settle future observations.")
    return _daily(date.fromisoformat(spec["x"]["history_start"]),
                  today - timedelta(days=1))[0].dropna()


def baseline(run_date: date, spec: dict) -> dict:
    """No public expert forecast of article views exists; the engine scores against its
    weekly seasonal naive instead."""
    return {"source": {"name": "none: no expert forecast of Wikipedia page views"}, "forecasts": []}
