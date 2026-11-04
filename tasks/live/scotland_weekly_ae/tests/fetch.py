"""Host-only PHS weekly A&E data: X_d and first-published truth. There is no official or
expert forecast of this series, so there is no B.

Public Health Scotland, UK Open Government Licence. Aggregation and forecasts are
our transformations, not PHS forecasts or endorsement. No API key is required.
"""
from __future__ import annotations
import hashlib
import io
import json
import math
import time
from datetime import date, datetime, time as clock, timedelta
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
import pandas as pd

CATALOGUE = "https://www.opendata.nhs.scot/api/3/action/package_show?id=weekly-accident-and-emergency-activity-and-waiting-times"
ZONE = ZoneInfo("Europe/London")
# Fixed reporting panel present throughout the selected history, rather than a changing sum.
SITES = frozenset("A111H A210H B120H C121H C313H C418H F704H G107H G405H G513H H103H H202H H212H L106H L302H L308H N101H N121H N411H R103H S308H S314H S319H T101H T202H V217H W107H Y144H Y146H Z102H".split())
# Scotland's bank holidays at Christmas and New Year, when attendances fall most.
FESTIVE = {(12, 25), (12, 26), (1, 1), (1, 2)}
COLUMNS = ["WeekEndingDate", "Country", "HBT", "TreatmentLocation", "DepartmentType",
           "AttendanceCategory", "NumberOfAttendancesEpisode"]


def _now():
    return datetime.now(ZONE)


def _cutoff(run_date, spec):
    if spec["schedule"]["timezone"] != "Europe/London":
        raise ValueError("PHS weeks use Europe/London dates.")
    return datetime.combine(run_date, clock.fromisoformat(spec["schedule"]["cutoff"]), ZONE)


def _guard(run_date, spec):
    now = _now()
    if now.date() != run_date or now >= _cutoff(run_date, spec) or run_date.weekday() != 6:
        raise RuntimeError("Fresh snapshots require the current Sunday before cutoff.")
    return now


def _get(url):
    for attempt in range(4):
        try:
            request = Request(url, headers={"User-Agent": "agentic-forecasting-phs/1.0 (https://github.com/rowingchenn/agentic-forecasting-tasks)"})
            with urlopen(request, timeout=90) as response:
                return response.read()
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


def _weekly(today, spec):
    start = pd.Timestamp(spec["x"]["history_start"])
    if start.dayofweek != 6 or start < pd.Timestamp("2024-01-07"):
        raise ValueError("History must start on a Sunday from 2024-01-07 onward.")
    payload = json.loads(_get(CATALOGUE))
    if payload.get("success") is not True:
        raise ValueError("PHS catalogue request failed.")
    package = payload["result"]
    if package.get("license_title") != "UK Open Government Licence (OGL)":
        raise ValueError("Source licence changed; review reuse terms before collection.")
    resources = [r for r in package["resources"] if str(r.get("format", "")).lower() == "csv"
                 and "weekly" in r.get("name", "").lower() and "activity" in r.get("name", "").lower()]
    if len(resources) != 1:
        raise ValueError("Expected one unambiguous weekly activity CSV.")
    resource = resources[0]
    url = resource["url"]
    if not url.startswith("https://www.opendata.nhs.scot/"):
        raise ValueError("Unexpected resource host; review source changes.")
    csv_bytes = _get(url)
    frame = pd.read_csv(io.BytesIO(csv_bytes), dtype=str)
    if not set(COLUMNS).issubset(frame.columns):
        raise ValueError("PHS weekly schema changed.")
    rows = frame.loc[(frame["Country"] == "S92000003") & (frame["DepartmentType"] == "Type 1")
                     & (frame["AttendanceCategory"] == "Unplanned"), COLUMNS].copy()
    rows["week"] = pd.to_datetime(rows["WeekEndingDate"], format="%Y%m%d", errors="raise")
    rows = rows[(rows["week"] >= start) & (rows["week"] < pd.Timestamp(today))]
    if rows.empty or (rows["week"].dt.dayofweek != 6).any():
        raise ValueError("Missing weekly history or non-Sunday dates.")
    if rows.duplicated(["week", "TreatmentLocation"]).any():
        raise ValueError("Ambiguous duplicate site/week keys; do not sum or guess.")
    values = pd.to_numeric(rows["NumberOfAttendancesEpisode"], errors="raise")
    if values.isna().any() or any(not math.isfinite(v) or v < 0 or v != int(v) for v in values):
        raise ValueError("Invalid or missing attendance counts.")
    rows["NumberOfAttendancesEpisode"] = values.astype("int64")
    panels = rows.groupby("week")["TreatmentLocation"].apply(frozenset)
    if any(panel != SITES for panel in panels):
        raise ValueError("Reporting panel changed or incomplete; maintainer review required.")
    totals = rows.groupby("week")["NumberOfAttendancesEpisode"].sum().sort_index().rename("target")
    expected = pd.date_range(start, totals.index.max(), freq="W-SUN", name="timestamp")
    totals.index.name = "timestamp"
    if not totals.index.equals(expected):
        raise ValueError("Missing weeks; do not fabricate totals or interpolate.")
    sources = {"name": "Public Health Scotland", "catalogue": CATALOGUE, "url": url,
               "resource_id": resource["id"], "licence": package.get("license_title"),
               "licence_url": package.get("license_url"),
               "source_csv_sha256": hashlib.sha256(csv_bytes).hexdigest(),
               "transformation": "Sum Unplanned Type 1 attendance episodes for the fixed 30-site panel; derived, no endorsement."}
    return totals, rows.drop(columns="week").sort_values(["WeekEndingDate", "TreatmentLocation"]), sources


def snapshot(run_date: date, spec: dict) -> dict:
    as_of = _guard(run_date, spec)
    history, raw, source = _weekly(run_date, spec)
    last = history.index[-1].date()
    # PHS releases on Tuesdays the week that ended nine days earlier, so a Sunday run normally
    # has the week ending 14 days before it. 28 days lets a skipped or late release (Christmas)
    # through with a week-older history instead of losing the run day.
    if last < run_date - timedelta(days=28):
        raise RuntimeError("PHS release is older than the expected reporting-lag allowance.")
    _guard(run_date, spec)
    targets = [run_date + timedelta(days=h) for h in spec["horizons"]]
    if any(d.weekday() != 6 for d in targets):
        raise ValueError("Forecast targets must be Sunday week-ending dates.")
    index = pd.date_range(spec["x"]["history_start"], max(targets), freq="W-SUN", name="timestamp")
    festive = [sum((d.month, d.day) in FESTIVE for d in pd.date_range(end - pd.Timedelta(days=6), end))
               for end in index]
    return {"series": pd.DataFrame({"target": history.reindex(index),
                                   "iso_week": index.isocalendar().week.to_numpy(dtype=int),
                                   "month": index.month, "festive": festive}, index=index),
            "last_observed": last, "raw": {"phs_hospital_weeks.csv": raw},
            "sources": {"attendances": source, "as_of": as_of.isoformat()},
            "covariate_notes": {"iso_week": "ISO week number of Sunday; may be 53.",
                                "month": "Month of the Sunday week-ending date.",
                                "festive": "Days of the week that are Christmas Day, Boxing Day, 1 or 2 January."}}


def truth(today: date, spec: dict) -> pd.Series:
    if today > _now().date():
        raise RuntimeError("Cannot settle future dates.")
    # update_truth keeps the first published value of each week; later revisions are not used.
    return _weekly(today, spec)[0]


def baseline(run_date: date, spec: dict) -> dict:
    return {"source": {"name": "none: no official or expert forecast of this series"}, "forecasts": []}
