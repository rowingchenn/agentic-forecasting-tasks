"""Host-only USDA weekly corn export inspections: X_d and first-published truth. There is no
official or expert forecast of this series, so there is no B.

USDA's Grain Inspections dataset on AgTransport carries the figures of the weekly report
WA_GR101, one row per inspection record; no key is required. USDA reloads the whole dataset
when it adds a week, so earlier weeks appear as revised since.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from datetime import time as clock
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd

ZONE = ZoneInfo("America/New_York")
PAGE = "https://agtransport.usda.gov/Exports/Grain-Inspections/sruw-w49i"
API = "https://agtransport.usda.gov/resource/sruw-w49i.json"
LIMIT = 50000


def _now():
    return datetime.now(ZONE)


def _guard(run_date, spec):
    now = _now()
    cutoff = datetime.combine(run_date, clock.fromisoformat(spec["schedule"]["cutoff"]), ZONE)
    if spec["schedule"]["timezone"] != ZONE.key:
        raise ValueError("USDA labels its weeks with Eastern dates.")
    if run_date.weekday() != 3 or now.date() != run_date or now >= cutoff:
        raise RuntimeError("Fresh snapshots require the current Thursday before the cutoff.")
    return now


def _get(url):
    for attempt in range(4):
        try:
            request = Request(url, headers={"User-Agent": "agentic-forecasting/1.0"})
            with urlopen(request, timeout=90) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise
            retry = exc.headers.get("Retry-After", "")
            delay = min(120, int(retry)) if retry.isdigit() else 2**attempt
        except (URLError, TimeoutError):
            if attempt == 3:
                raise
            delay = 2**attempt
        time.sleep(delay)
    raise AssertionError("unreachable")


def _weekly(spec, end):
    """Corn inspected for export in each week from history_start to the week labelled `end`
    or before, in metric tons, with the rows by port area it is summed from."""
    start = date.fromisoformat(spec["x"]["history_start"])
    if start.weekday() != 3:
        raise ValueError("USDA weeks end on Thursdays; history_start must be one.")
    query = {
        "$select": "date,port,sum(mt) as metric_tons,count(*) as records,count(mt) as amounts",
        "$where": f"grain = 'CORN' AND date between '{start}T00:00:00' and '{end}T00:00:00'",
        "$group": "date,port",
        "$order": "date,port",
        "$limit": LIMIT,
    }
    url = f"{API}?{urlencode(query)}"
    rows = pd.DataFrame(_get(url))
    if rows.empty or len(rows) >= LIMIT:
        raise ValueError("USDA returned no rows or reached the row limit.")
    rows["date"] = pd.to_datetime(rows["date"], format="ISO8601")
    for column in ("metric_tons", "records", "amounts"):
        rows[column] = pd.to_numeric(rows[column], errors="raise")
    if (rows["amounts"] != rows["records"]).any() or (rows["metric_tons"] < 0).any():
        raise ValueError("Missing or negative metric tons in USDA's records.")
    if (rows["date"].dt.dayofweek != 3).any():
        raise ValueError("USDA week labels are not Thursdays; review the source.")
    totals = rows.groupby("date")["metric_tons"].sum().rename("target")
    totals.index.name = "timestamp"
    raw = rows.drop(columns="amounts").rename(columns={"date": "week_ending"})
    return totals, raw, url


def snapshot(run_date: date, spec: dict) -> dict:
    as_of = _guard(run_date, spec)
    # A week ends on Thursday and USDA adds it on the Monday after, so a Thursday run normally
    # has the week ending seven days before it.
    history, raw, url = _weekly(spec, run_date - timedelta(days=4))
    last = history.index[-1].date()
    expected = pd.date_range(spec["x"]["history_start"], last, freq="W-THU")
    if not history.index.equals(expected):
        raise ValueError("Weeks are missing from USDA's history; do not fill them in.")
    # A release delayed past Thursday (as in a lapse in appropriations) lets the run go ahead
    # with the history USDA has published; older than four weeks, the source is broken.
    if last < run_date - timedelta(days=28):
        raise RuntimeError("USDA's latest published week is more than four weeks old.")
    _guard(run_date, spec)
    targets = [run_date + timedelta(days=h) for h in spec["horizons"]]
    if any(d.weekday() != 3 for d in targets):
        raise ValueError("Target dates must be Thursdays, the days that label USDA's weeks.")
    index = pd.date_range(spec["x"]["history_start"], max(targets), freq="W-THU", name="timestamp")
    series = pd.DataFrame(
        {
            "target": history.reindex(index),
            "iso_week": index.isocalendar().week.to_numpy(dtype=int),
            "month": index.month,
        },
        index=index,
    )
    return {
        "series": series,
        "last_observed": last,
        "raw": {"usda_corn_by_port_area.csv": raw},
        "sources": {
            "inspections": {
                "name": "USDA AMS AgTransport, Grain Inspections (sruw-w49i)",
                "page": PAGE,
                "query": url,
                "vintage": "as published at the snapshot: the latest week as first published, "
                "earlier weeks as revised since",
            },
            "as_of": as_of.isoformat(),
        },
        "covariate_notes": {
            "iso_week": "ISO week number of the Thursday that ends the week; may be 53.",
            "month": "Month of the Thursday that ends the week.",
        },
    }


def truth(today: date, spec: dict) -> pd.Series:
    if today > _now().date():
        raise RuntimeError("Cannot settle future dates.")
    # Only weeks whose Monday release day has come; update_truth keeps the first value seen,
    # so the revision in the following Monday's report is not used.
    return _weekly(spec, today - timedelta(days=4))[0]


def baseline(run_date: date, spec: dict) -> dict:
    return {
        "source": {"name": "none: no official or expert forecast of this series"},
        "forecasts": [],
    }
