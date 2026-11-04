"""Host-only USGS ComCat counts and frozen expanding-mean reference; no credentials.

Query preferred solutions in monthly windows, enforce strict mag > 5 locally, include
real zero days, and fail closed on invalid/truncated data. Only pandas and stdlib.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import UTC, date, datetime, time as clock, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

API = "https://earthquake.usgs.gov/fdsnws/event/1/query"
USER_AGENT = (
    "agentic-forecasting-earthquakes/1.0 "
    "(https://github.com/rowingchenn/agentic-forecasting-tasks)"
)
LIMIT = 20_000
LAG_DAYS = 2
RAW_COLUMNS = ["event_id", "time_utc", "magnitude", "network"]


def _now():
    return datetime.now(UTC)


def _cutoff(run_date, spec):
    if spec["schedule"]["timezone"] != "UTC":
        raise ValueError("This target requires UTC calendar days.")
    return datetime.combine(run_date, clock.fromisoformat(spec["schedule"]["cutoff"]), UTC)


def _guard(run_date, spec):
    now = _now()
    if now.date() != run_date or now >= _cutoff(run_date, spec):
        raise RuntimeError("Fresh snapshots require the current UTC day before cutoff.")
    return now


def _get(url):
    for attempt in range(4):
        try:
            with urlopen(Request(url, headers={
                "User-Agent": USER_AGENT, "Accept": "application/geo+json",
            }), timeout=90) as response:
                # FDSN explicitly uses 204 to signal a successful no-data query.
                if response.status == 204:
                    return {"type": "FeatureCollection", "metadata": {"count": 0}, "features": []}
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


def _number(value, name):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value)):
        raise ValueError(f"Invalid event {name}.")
    return float(value)


def _daily(start, end):
    """Return inclusive daily counts plus qualifying event records, from a live query.

    Monthly queries avoid the result cap; a cap hit fails rather than silently omitting
    records. FDSN boundaries are inclusive, so each chunk is filtered to [start, stop).
    """
    if start > end:
        raise ValueError("History starts after the last eligible day.")
    stop_all = end + timedelta(days=1)
    cursor = start
    events = {}
    while cursor < stop_all:
        next_month = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        stop = min(next_month, stop_all)
        params = {
            "format": "geojson", "starttime": f"{cursor}T00:00:00",
            "endtime": f"{stop}T00:00:00", "minmagnitude": 5,
            "eventtype": "earthquake", "orderby": "time-asc", "limit": LIMIT,
            "includeallorigins": "false", "includeallmagnitudes": "false", "nodata": 204,
        }
        payload = _get(f"{API}?{urlencode(params)}")
        if (not isinstance(payload, dict) or payload.get("type") != "FeatureCollection"
                or not isinstance(payload.get("features"), list)):
            raise ValueError("Unexpected USGS GeoJSON schema.")
        rows = payload["features"]
        metadata = payload.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError("Invalid USGS response metadata.")
        # Some live responses omit metadata.count; feature length still detects the cap.
        count = metadata.get("count", len(rows))
        if type(count) is not int or count != len(rows) or len(rows) >= LIMIT:
            raise ValueError("USGS results are invalid or possibly truncated; reduce query windows.")
        seen = set()
        for item in rows:
            if not isinstance(item, dict) or item.get("type") != "Feature":
                raise ValueError("Invalid USGS feature.")
            event_id = item.get("id")
            props = item.get("properties")
            if (not isinstance(event_id, str) or not event_id or event_id in seen
                    or not isinstance(props, dict) or props.get("type") != "earthquake"):
                raise ValueError("Invalid, duplicate or non-earthquake event.")
            seen.add(event_id)
            mag = _number(props.get("mag"), "magnitude")
            stamp = _number(props.get("time"), "origin time")
            moment = datetime.fromtimestamp(stamp / 1000, UTC)
            left = datetime.combine(cursor, clock.min, UTC)
            right = datetime.combine(stop, clock.min, UTC)
            if not left <= moment <= right:
                raise ValueError("USGS event falls outside its requested window.")
            if moment == right or mag <= 5.0:
                continue
            if event_id in events:
                raise ValueError("Event ID repeated across query windows; retry a stable catalogue.")
            events[event_id] = {
                "event_id": event_id, "time_utc": moment.isoformat(), "magnitude": mag,
                "network": props.get("net"),
            }
        cursor = stop
    index = pd.date_range(start, end, name="timestamp")
    counts = pd.Series(0.0, index=index, name="target")
    for row in events.values():
        day = datetime.fromisoformat(row["time_utc"]).date()
        counts.loc[pd.Timestamp(day)] += 1
    raw = pd.DataFrame(sorted(events.values(), key=lambda e: (e["time_utc"], e["event_id"])),
                       columns=RAW_COLUMNS)
    return counts, raw


def snapshot(run_date: date, spec: dict) -> dict:
    as_of = _guard(run_date, spec)
    start = date.fromisoformat(spec["x"]["history_start"])
    through = run_date - timedelta(days=LAG_DAYS)
    past, raw = _daily(start, through)
    _guard(run_date, spec)
    index = pd.date_range(start, run_date + timedelta(days=max(spec["horizons"])),
                          name="timestamp")
    return {
        "series": pd.DataFrame({
            "target": past.reindex(index), "dow": index.dayofweek, "month": index.month,
        }, index=index),
        "last_observed": through,
        "raw": {"earthquakes.csv": raw},
        "sources": {
            "earthquakes": {"name": "USGS ComCat", "url": API, "eventtype": "earthquake",
                            "magnitude": "preferred mag strictly > 5.0", "timezone": "UTC"},
            "as_of": as_of.isoformat(),
        },
        "covariate_notes": {
            "dow": "UTC weekday, Monday=0; no physical weekly cycle assumed.",
            "month": "Calendar month, 1 through 12, known for all dates.",
        },
    }


def truth(today: date, spec: dict) -> pd.Series:
    if today > _now().date():
        raise RuntimeError("Cannot settle future dates.")
    # af.archive.update_truth retains the first count seen for each eligible date.
    return _daily(date.fromisoformat(spec["x"]["history_start"]),
                  today - timedelta(days=LAG_DAYS))[0]


def _reference(run_date, spec):
    """The pipeline collects yesterday's B; do not fetch revised historical inputs."""
    root = (Path(__file__).resolve().parents[3] / "mock" / spec["target"] /
            run_date.isoformat() / "oracle" / "assets" / "x")
    if not root.exists():
        result = snapshot(run_date, spec)  # same-day pre-cutoff smoke test only
        return result["series"]["target"], result["last_observed"], result["sources"]["as_of"], None
    manifest = json.loads((root / "MANIFEST.json").read_text())
    for name in ("task.json", "series.csv"):
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != manifest["files"].get(name):
            raise ValueError(f"Archived {name} fails its snapshot hash.")
    task = json.loads((root / "task.json").read_text())
    last = date.fromisoformat(task["last_observed"])
    as_of = datetime.fromisoformat(manifest["sources"]["as_of"])
    if (task["run_date"] != str(run_date) or task["task"] != spec["target"]
            or manifest["snapshot"] != f"{spec['target']}/{run_date}"
            or last != run_date - timedelta(days=LAG_DAYS)
            or as_of.tzinfo is None or as_of.astimezone(UTC).date() != run_date
            or as_of >= _cutoff(run_date, spec)):
        raise ValueError("Not a valid pre-cutoff run-day snapshot.")
    series = pd.read_csv(root / "series.csv", index_col="timestamp", parse_dates=True)["target"]
    return series, last, as_of.isoformat(), manifest["snapshot"]


def baseline(run_date: date, spec: dict) -> dict:
    if run_date > _now().date():
        raise RuntimeError("Cannot build a future run's baseline.")
    history, last, as_of, frozen = _reference(run_date, spec)
    start = date.fromisoformat(spec["x"]["history_start"])
    expected = pd.date_range(start, last, name="timestamp")
    past = history.loc[(history.index >= pd.Timestamp(start)) & (history.index <= pd.Timestamp(last))]
    if not past.index.equals(expected) or past.isna().any() or past.empty:
        raise ValueError("Reference needs every historical day, including zero-event days.")
    values = pd.to_numeric(past, errors="raise")
    if any(not math.isfinite(v) or v < 0 or not float(v).is_integer() for v in values):
        raise ValueError("Reference history contains invalid daily counts.")
    point = float(values.mean())
    return {
        "source": {
            "name": "Expanding historical mean (statistical, not expert)", "url": API,
            "as_of": as_of, "snapshot": frozen, "history_start": str(start),
            "last_observed": str(last), "observation_days": len(past),
        },
        "forecasts": [{"target_date": str(run_date + timedelta(days=h)), "point": point}
                      for h in spec["horizons"]],
    }
