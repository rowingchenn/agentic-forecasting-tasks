"""Host-only Wikimedia history, delayed truth and frozen seasonal-naive reference.

No API key. Use a descriptive User-Agent per Wikimedia policy. Requests use the fixed
article title, all access methods, and user-classified traffic. Missing days fail closed.
Only calendar covariates are curated; live agents can acquire their own public sources.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import UTC, date, datetime, time as clock, timedelta
from pathlib import Path
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
    """Bound requests to one year; require one validated integer count per UTC day."""
    if start > end:
        raise ValueError("History start is after the last eligible observation.")
    records = []
    cursor = start
    while cursor <= end:
        stop = min(end, date(cursor.year, 12, 31))
        payload = _get(f"{API}/{cursor:%Y%m%d}00/{stop:%Y%m%d}00")
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise ValueError("Unexpected Wikimedia response schema.")
        expected = pd.date_range(cursor, stop)
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
        if set(batch) != set(expected.date):
            raise ValueError(f"Incomplete page-view history for {cursor} through {stop}.")
        records.extend({"timestamp": d, "views": batch[d], **FILTERS} for d in sorted(batch))
        cursor = stop + timedelta(days=1)
    raw = pd.DataFrame(records)
    counts = pd.Series(raw["views"].to_numpy(), index=pd.to_datetime(raw["timestamp"]),
                       name="target", dtype=float)
    return counts.rename_axis("timestamp"), raw


def snapshot(run_date: date, spec: dict) -> dict:
    as_of = _guard(run_date, spec)
    start = date.fromisoformat(spec["x"]["history_start"])
    through = run_date - timedelta(days=2)
    past, raw = _daily(start, through)
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
    # af.archive.update_truth appends unseen dates and preserves first-seen values.
    return _daily(date.fromisoformat(spec["x"]["history_start"]),
                  today - timedelta(days=2))[0]


def _reference(run_date, spec):
    """Next-day B collection must use hashed run-day X_d, not revised API history."""
    root = (Path(__file__).resolve().parents[3] / "mock" / spec["target"] /
            run_date.isoformat() / "oracle" / "assets" / "x")
    if not root.exists():
        # This path supports a direct same-day smoke test; never backfill a past run.
        result = snapshot(run_date, spec)
        return (result["series"]["target"], result["last_observed"],
                result["sources"]["as_of"], None)
    manifest = json.loads((root / "MANIFEST.json").read_text())
    for name in ("task.json", "series.csv"):
        actual = hashlib.sha256((root / name).read_bytes()).hexdigest()
        if actual != manifest["files"].get(name):
            raise ValueError(f"Archived {name} does not match its snapshot hash.")
    task = json.loads((root / "task.json").read_text())
    observed = date.fromisoformat(task["last_observed"])
    as_of = datetime.fromisoformat(manifest["sources"]["as_of"])
    if (task["run_date"] != run_date.isoformat() or task["task"] != spec["target"]
            or manifest["snapshot"] != f"{spec['target']}/{run_date}"
            or observed != run_date - timedelta(days=2)
            or as_of.tzinfo is None or as_of.astimezone(UTC).date() != run_date
            or as_of >= _cutoff(run_date, spec)):
        raise ValueError("Archive is not a valid pre-cutoff run-day snapshot.")
    frame = pd.read_csv(root / "series.csv", index_col="timestamp", parse_dates=True)
    if not frame.index.is_unique:
        raise ValueError("Duplicate dates in frozen history.")
    return frame["target"], observed, as_of.isoformat(), manifest["snapshot"]


def baseline(run_date: date, spec: dict) -> dict:
    if run_date > _now().date():
        raise RuntimeError("Cannot build a reference for a future run date.")
    history, last, as_of, frozen = _reference(run_date, spec)
    forecasts = []
    for horizon in spec["horizons"]:
        target = run_date + timedelta(days=horizon)
        anchor = target
        while anchor > last:
            anchor -= timedelta(days=7)
        point = float(history.loc[pd.Timestamp(anchor)])
        if not math.isfinite(point) or point < 0 or not point.is_integer():
            raise ValueError("Invalid seasonal-naive anchor count.")
        forecasts.append({"target_date": target.isoformat(), "point": point})
    return {
        "source": {"name": "Weekly seasonal naive (statistical, not expert)",
                   "url": API, "as_of": as_of, "last_observed": last.isoformat(),
                   "snapshot": frozen, "period_days": 7},
        "forecasts": forecasts,
    }
