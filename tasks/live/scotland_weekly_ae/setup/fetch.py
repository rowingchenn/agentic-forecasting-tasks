"""Host-only PHS weekly A&E data, first-retained truth and frozen annual reference.

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
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
import pandas as pd

CATALOGUE = "https://www.opendata.nhs.scot/api/3/action/package_show?id=weekly-accident-and-emergency-activity-and-waiting-times"
PAGE = "https://www.opendata.nhs.scot/dataset/weekly-accident-and-emergency-activity-and-waiting-times"
ZONE = ZoneInfo("Europe/London")
# Fixed reporting panel present throughout the selected history, rather than a changing sum.
SITES = frozenset("A111H A210H B120H C121H C313H C418H F704H G107H G405H G513H H103H H202H H212H L106H L302H L308H N101H N121H N411H R103H S308H S314H S319H T101H T202H V217H W107H Y144H Y146H Z102H".split())
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
    if now.date() != run_date or now >= _cutoff(run_date, spec) or run_date.weekday() != 0:
        raise RuntimeError("Fresh snapshots require the current Monday before cutoff.")
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
    # Monday runs normally have a latest released week from 15 days earlier.
    if last < run_date - timedelta(days=15):
        raise RuntimeError("PHS release is older than the expected reporting-lag allowance.")
    _guard(run_date, spec)
    targets = [run_date + timedelta(days=h) for h in spec["horizons"]]
    if any(d.weekday() != 6 for d in targets):
        raise ValueError("Forecast targets must be Sunday week-ending dates.")
    index = pd.date_range(spec["x"]["history_start"], max(targets), freq="W-SUN", name="timestamp")
    return {"series": pd.DataFrame({"target": history.reindex(index),
                                   "iso_week": index.isocalendar().week.to_numpy(dtype=int),
                                   "month": index.month}, index=index),
            "last_observed": last, "raw": {"phs_hospital_weeks.csv": raw},
            "sources": {"attendances": source, "as_of": as_of.isoformat()},
            "covariate_notes": {"iso_week": "ISO week number of Sunday; may be 53.",
                                "month": "Month of the Sunday week-ending date."}}


def truth(today: date, spec: dict) -> pd.Series:
    if today > _now().date():
        raise RuntimeError("Cannot settle future dates.")
    # Pipeline update_truth preserves the first retained value for each published week.
    return _weekly(today, spec)[0]


def _reference(run_date, spec):
    root = (Path(__file__).resolve().parents[3] / "mock" / spec["target"] / str(run_date)
            / "oracle" / "assets" / "x")
    if not root.exists():
        result = snapshot(run_date, spec)  # current pre-cutoff smoke test only
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
            or last.weekday() != 6 or not run_date - timedelta(days=15) <= last < run_date
            or as_of.tzinfo is None or as_of.astimezone(ZONE).date() != run_date
            or as_of >= _cutoff(run_date, spec)):
        raise ValueError("Invalid pre-cutoff run-day snapshot.")
    history = pd.read_csv(root / "series.csv", index_col="timestamp", parse_dates=True)["target"]
    return history, last, as_of.isoformat(), manifest["snapshot"]


def baseline(run_date: date, spec: dict) -> dict:
    if run_date > _now().date() or run_date.weekday() != 0:
        raise RuntimeError("Reference requires a current or archived Monday run.")
    history, last, as_of, frozen = _reference(run_date, spec)
    past = history.loc[:str(last)]
    expected = pd.date_range(spec["x"]["history_start"], last, freq="W-SUN")
    if not past.index.equals(expected) or past.isna().any():
        raise ValueError("Incomplete frozen weekly history.")
    forecasts = []
    for horizon in spec["horizons"]:
        target = run_date + timedelta(days=horizon)
        anchor = target - timedelta(weeks=52)
        if target.weekday() != 6 or anchor > last:
            raise ValueError("Invalid annual anchor or target date.")
        point = float(past.loc[pd.Timestamp(anchor)])
        if not math.isfinite(point) or point < 0 or not point.is_integer():
            raise ValueError("Invalid annual attendance anchor.")
        forecasts.append({"target_date": str(target), "point": point})
    return {"source": {"name": "52-week seasonal naive (statistical, not expert)", "url": PAGE,
                       "as_of": as_of, "snapshot": frozen, "last_observed": str(last),
                       "lag_weeks": 52, "attribution": "Public Health Scotland; derived forecast, UK OGL."},
            "forecasts": forecasts}
