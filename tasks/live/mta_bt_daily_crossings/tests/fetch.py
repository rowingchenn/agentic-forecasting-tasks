"""Host-only MTA BT daily counts and frozen 364-day reference; no API key.

Source: Metropolitan Transportation Authority, sayj-mze2. These are preliminary
crossing estimates, not unique vehicles or paid toll transactions. No gap filling.
"""
from __future__ import annotations
import hashlib
import json
import math
import time
from datetime import date, datetime, time as clock, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

API = "https://data.ny.gov/resource/sayj-mze2.json"
PAGE = "https://data.ny.gov/d/sayj-mze2"
OVERVIEW = "https://data.ny.gov/api/views/sayj-mze2/files/7635e5d0-7534-406d-b1c4-be4969ce61df?download=true"
ZONE = ZoneInfo("America/New_York")
PAGE_SIZE = 1000
MAX_LAG_DAYS = 7


def _now():
    return datetime.now(ZONE)


def _cutoff(run_date, spec):
    if spec["schedule"]["timezone"] != "America/New_York":
        raise ValueError("MTA runs use America/New_York.")
    return datetime.combine(run_date, clock.fromisoformat(spec["schedule"]["cutoff"]), ZONE)


def _guard(run_date, spec):
    now = _now()
    if now.date() != run_date or now >= _cutoff(run_date, spec):
        raise RuntimeError("Fresh snapshots require the current local day before cutoff.")
    return now


def _get(url):
    for attempt in range(4):
        try:
            request = Request(url, headers={"Accept": "application/json",
                "User-Agent": "agentic-forecasting-mta/1.0 (https://github.com/rowingchenn/agentic-forecasting-tasks)"})
            with urlopen(request, timeout=90) as response:
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
    """Fetch all pages, enforcing one count for every date through the latest publication.

    end is a completed-date upper bound, not a promise the source has published it.
    Missing dates inside the observed span fail; the unpublished tail is left unknown.
    """
    if start > end or start < date(2024, 1, 1):
        raise ValueError("Require a nonempty 2024+ history.")
    values = {}
    for page in range(100):
        params = {"$select": "date,mode,count",
                  "$where": f"mode='BT' AND date >= '{start}T00:00:00' AND date <= '{end}T00:00:00'",
                  "$order": "date ASC", "$limit": PAGE_SIZE, "$offset": page * PAGE_SIZE}
        rows = _get(f"{API}?{urlencode(params)}")
        if not isinstance(rows, list) or len(rows) > PAGE_SIZE:
            raise ValueError("Unexpected MTA response or page size.")
        for row in rows:
            if not isinstance(row, dict) or row.get("mode") != "BT":
                raise ValueError("MTA returned a different transport mode or invalid row.")
            stamp = pd.Timestamp(row["date"])
            if stamp.tzinfo is not None or stamp != stamp.normalize():
                raise ValueError("Expected MTA's floating daily date labels.")
            day = stamp.date()
            value = row.get("count")
            if isinstance(value, bool) or value is None:
                raise ValueError("Missing daily count; do not substitute zero or carry forward.")
            point = float(value)
            if not math.isfinite(point) or point < 0 or not point.is_integer():
                raise ValueError("MTA crossing counts must be finite non-negative integers.")
            if not start <= day <= end or day in values:
                raise ValueError("Out-of-range or duplicate date; retry a consistent source snapshot.")
            values[day] = int(point)
        if len(rows) < PAGE_SIZE:
            break
    else:
        raise RuntimeError("MTA pagination cap reached; no partial history is returned.")
    if not values:
        raise ValueError("No published BT history returned.")
    index = pd.date_range(start, max(values), name="timestamp")
    if set(values) != set(index.date):
        raise ValueError("Missing dates inside published BT history; do not interpolate.")
    history = pd.Series([values[d.date()] for d in index], index=index, name="target", dtype=float)
    raw = pd.DataFrame({"date": index.strftime("%Y-%m-%d"), "mode": "BT",
                        "count": [values[d.date()] for d in index]})
    return history, raw


def snapshot(run_date: date, spec: dict) -> dict:
    as_of = _guard(run_date, spec)
    start = date.fromisoformat(spec["x"]["history_start"])
    history, raw = _daily(start, run_date - timedelta(days=1))
    last = history.index[-1].date()
    if last < run_date - timedelta(days=MAX_LAG_DAYS):
        raise RuntimeError("Latest BT count is over seven days old; review delayed publication.")
    _guard(run_date, spec)
    index = pd.date_range(start, run_date + timedelta(days=max(spec["horizons"])), name="timestamp")
    holidays = USFederalHolidayCalendar().holidays(index.min(), index.max())
    return {"series": pd.DataFrame({"target": history.reindex(index), "dow": index.dayofweek,
                                   "month": index.month, "holiday": index.isin(holidays).astype(int)}, index=index),
            "last_observed": last, "raw": {"mta_bt_daily.csv": raw},
            "sources": {"traffic": {"name": "MTA", "url": API, "dataset": "sayj-mze2", "mode": "BT",
                                     "methodology": OVERVIEW, "data_use_note": "Overview states no limitations at this time; MTA/TBTA retain reserved rights."},
                        "as_of": as_of.isoformat()},
            "covariate_notes": {"dow": "Monday=0.", "month": "Calendar month.",
                                "holiday": "US federal holiday indicator, not a toll-facility closure indicator."}}


def truth(today: date, spec: dict) -> pd.Series:
    if today > _now().date():
        raise RuntimeError("Cannot settle future dates.")
    # update_truth retains the first observed value for each completed published date.
    return _daily(date.fromisoformat(spec["x"]["history_start"]), today - timedelta(days=1))[0]


def _reference(run_date, spec):
    root = (Path(__file__).resolve().parents[3] / "mock" / spec["target"] / str(run_date)
            / "oracle" / "assets" / "x")
    if not root.exists():
        result = snapshot(run_date, spec)  # direct same-day pre-cutoff smoke check only
        return result["series"]["target"], result["last_observed"], result["sources"]["as_of"], None
    manifest = json.loads((root / "MANIFEST.json").read_text())
    for name in ("task.json", "series.csv"):
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != manifest["files"].get(name):
            raise ValueError(f"Archived {name} fails its hash.")
    task = json.loads((root / "task.json").read_text())
    last = date.fromisoformat(task["last_observed"])
    as_of = datetime.fromisoformat(manifest["sources"]["as_of"])
    if (task["run_date"] != str(run_date) or task["task"] != spec["target"]
            or manifest["snapshot"] != f"{spec['target']}/{run_date}"
            or not run_date - timedelta(days=MAX_LAG_DAYS) <= last < run_date
            or as_of.tzinfo is None or as_of.astimezone(ZONE).date() != run_date
            or as_of >= _cutoff(run_date, spec)):
        raise ValueError("Not a valid pre-cutoff run-day snapshot.")
    history = pd.read_csv(root / "series.csv", index_col="timestamp", parse_dates=True)["target"]
    return history, last, as_of.isoformat(), manifest["snapshot"]


def baseline(run_date: date, spec: dict) -> dict:
    if run_date > _now().date():
        raise RuntimeError("Cannot build a future baseline.")
    history, last, as_of, frozen = _reference(run_date, spec)
    past = history.loc[:str(last)]
    if not past.index.equals(pd.date_range(spec["x"]["history_start"], last)) or past.isna().any():
        raise ValueError("Frozen daily history is incomplete.")
    forecasts = []
    for horizon in spec["horizons"]:
        target = run_date + timedelta(days=horizon)
        anchor = target - timedelta(days=364)
        if anchor > last:
            raise ValueError("Annual anchor is not observed.")
        point = float(past.loc[pd.Timestamp(anchor)])
        if not math.isfinite(point) or point < 0 or not point.is_integer():
            raise ValueError("Invalid 364-day reference count.")
        forecasts.append({"target_date": str(target), "point": point})
    return {"source": {"name": "52-week seasonal naive (statistical, not expert)", "url": PAGE,
                       "as_of": as_of, "snapshot": frozen, "last_observed": str(last),
                       "lag_days": 364, "attribution": "MTA; derived forecast, no endorsement."},
            "forecasts": forecasts}
