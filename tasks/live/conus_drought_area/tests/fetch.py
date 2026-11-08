"""Host-only U.S. Drought Monitor statistics: X_d and first-published truth. There is no
official or expert forecast of this series, so there is no B.

The U.S. Drought Monitor is jointly produced by the National Drought Mitigation Center at the
University of Nebraska-Lincoln, the U.S. Department of Agriculture, the National Oceanic and
Atmospheric Administration and NASA. Data courtesy of NDMC, via
https://droughtmonitor.unl.edu. The Drought Monitor authorizes use by third parties with this
credit (https://droughtmonitor.unl.edu/About/Permission.aspx). Its statistics service needs
no key.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import date, datetime, timedelta
from datetime import time as clock
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd

ZONE = ZoneInfo("America/New_York")
API = "https://usdmdataservices.unl.edu/api/USStatistics/GetDroughtSeverityStatisticsByAreaPercent"
USER_AGENT = "agentic-forecasting/1.0"
FIRST_MAP = date(2000, 1, 4)
# Cumulative ("traditional") statistics: d1 is the share in D1 to D4, d2 in D2 to D4, and so on.
LEVELS = ["none", "d0", "d1", "d2", "d3", "d4"]
CREDIT = (
    "The U.S. Drought Monitor is jointly produced by the National Drought Mitigation Center at "
    "the University of Nebraska-Lincoln, the U.S. Department of Agriculture, the National "
    "Oceanic and Atmospheric Administration and NASA. Data courtesy of NDMC, via "
    "https://droughtmonitor.unl.edu."
)
TRIES = 4
# Missing weeks a snapshot tolerates in the history (left empty, never filled in); the
# Drought Monitor has published every week since 2000.
MAX_GAPS = 4


def _now() -> datetime:
    return datetime.now(ZONE)


def _warn(message: str) -> None:
    print(f"conus_drought_area: {message}", file=sys.stderr)


def _cutoff(run_date: date, spec: dict) -> datetime:
    if spec["schedule"]["timezone"] != "America/New_York":
        raise ValueError("The Drought Monitor's release times are in Eastern Time.")
    return datetime.combine(run_date, clock.fromisoformat(spec["schedule"]["cutoff"]), ZONE)


def _guard(run_date: date, spec: dict) -> datetime:
    now = _now()
    if now.date() != run_date or now >= _cutoff(run_date, spec) or run_date.weekday() != 3:
        raise RuntimeError("Fresh snapshots require the current Thursday before the cutoff.")
    return now


def _json(url: str) -> object:
    """The decoded response, asking again after server errors, dropped connections and a
    page that is not JSON (a maintenance page served with status 200)."""
    for attempt in range(TRIES):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urlopen(request, timeout=60) as response:
                return json.loads(response.read())
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == TRIES - 1:
                raise
            retry = str(exc.headers.get("Retry-After", "")) if exc.headers else ""
            delay = min(120, int(retry)) if retry.isdigit() else 2**attempt
        except (OSError, HTTPException, ValueError):
            if attempt == TRIES - 1:
                raise
            delay = 2**attempt
        time.sleep(delay)
    raise AssertionError("unreachable")


def _day(value: date) -> str:
    return f"{value.month}/{value.day}/{value.year}"


def _maps(today: date, spec: dict) -> tuple[pd.Series, pd.DataFrame, dict]:
    """Every map published from x.history_start to today: the D1-D4 share indexed by the map's
    Tuesday, the rows as published, and the source. A week never published is absent."""
    start = date.fromisoformat(spec["x"]["history_start"])
    if start < FIRST_MAP or start.weekday() != 1:
        raise ValueError("History must start on a map Tuesday from 2000-01-04 onward.")
    query = {"aoi": "conus", "startdate": _day(start), "enddate": _day(today), "statisticsType": 1}
    url = f"{API}?{urlencode(query)}"
    rows = _json(url)
    if not isinstance(rows, list) or not rows or not all(isinstance(r, dict) for r in rows):
        raise ValueError("The Drought Monitor returned no maps.")
    # The service's JSON keys are camelCase (mapDate, d1); its CSV capitalises them (MapDate, D1).
    frame = pd.DataFrame([{k[:1].lower() + k[1:]: v for k, v in r.items()} for r in rows])
    needed = {"mapDate", "validStart", "validEnd", "areaOfInterest", "statisticFormatID", *LEVELS}
    if not needed <= set(frame.columns):
        raise ValueError(f"The statistics lack {sorted(needed - set(frame.columns))}.")
    form = pd.to_numeric(frame["statisticFormatID"], errors="coerce")
    if set(frame["areaOfInterest"].astype(str).str.upper()) != {"CONUS"} or not (form == 1).all():
        raise ValueError("Expected cumulative percent-of-area statistics for CONUS.")
    values = frame[LEVELS].apply(pd.to_numeric, errors="coerce").astype(float)
    if values.isna().any().any() or ((values < 0) | (values > 100)).any().any():
        raise ValueError("Every percentage must be a number from 0 to 100.")
    cumulative = values[LEVELS[1:]].to_numpy()
    if (cumulative[:, 1:] > cumulative[:, :-1]).any():
        raise ValueError("A cumulative share grows with severity; the statistics are not cumulative.")
    if ((values["none"] + values["d0"] - 100).abs() > 0.02).any():
        raise ValueError("None and D0-D4 do not add up to 100.")
    # A map is labelled with a calendar date; any time or zone suffix after it is ignored.
    when = pd.to_datetime(frame["mapDate"].astype(str).str[:10], format="%Y-%m-%d", errors="coerce")
    if when.isna().any() or (when.dt.dayofweek != 1).any():
        raise ValueError("Every map is labelled with a Tuesday.")
    keep = ((when >= pd.Timestamp(start)) & (when <= pd.Timestamp(today))).to_numpy()
    if not keep.any():
        raise ValueError("No map between the history start and today.")
    target = pd.Series(
        values["d1"].to_numpy()[keep], index=pd.DatetimeIndex(when[keep], name="timestamp"), name="target"
    ).sort_index()
    if target.index.duplicated().any():
        raise ValueError("A map appears twice with possibly different values.")
    raw = (
        frame.loc[keep, ["mapDate", "validStart", "validEnd", *LEVELS]]
        .sort_values("mapDate")
        .reset_index(drop=True)
    )
    source = {
        "name": "U.S. Drought Monitor statistics, percent of area, CONUS, cumulative",
        "url": url,
        "credit": CREDIT,
        "permission": "https://droughtmonitor.unl.edu/About/Permission.aspx",
        "transformation": "the d1 column, the share of CONUS in D1 to D4",
    }
    return target, raw, source


def snapshot(run_date: date, spec: dict) -> dict:
    as_of = _guard(run_date, spec)
    history, raw, source = _maps(run_date, spec)
    last = history.index[-1].date()
    # This week's map (labelled with the Tuesday two days before the run date) comes out at
    # 08:30 ET on Thursday, or on Wednesday in a holiday week, before the snapshot. If it is
    # late, the run goes on from last week's map with a warning; two missing maps stop it.
    due = run_date - timedelta(days=2)
    if last < due - timedelta(days=7):
        raise RuntimeError(f"The newest Drought Monitor map is for {last}; two maps are missing.")
    if last < due:
        _warn(f"the map for {due} is not out yet; X_d ends with the map for {last}")
    # A week the Drought Monitor never published stays empty in X_d (the TSFM step fills the
    # target's gaps itself); a history cut short or full of holes is not used.
    weeks = pd.date_range(spec["x"]["history_start"], history.index[-1], freq="W-TUE")
    gaps = weeks.difference(history.index)
    if history.index[0] != weeks[0] or len(gaps) > MAX_GAPS:
        raise ValueError(f"The history lacks {len(gaps)} weeks or starts late; not used.")
    if len(gaps):
        _warn(f"no map was published for {', '.join(gaps.strftime('%Y-%m-%d'))}; left empty")
    _guard(run_date, spec)
    targets = [run_date + timedelta(days=h) for h in spec["horizons"]]
    if any(t.weekday() != 1 for t in targets):
        raise ValueError("Targets must be map Tuesdays.")
    index = pd.date_range(spec["x"]["history_start"], max(targets), freq="W-TUE", name="timestamp")
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
        "raw": {"usdm_conus_percent_area.csv": raw},
        "sources": {
            "drought": source,
            "maps": {"newest": last.isoformat(), "due": due.isoformat()},
            "as_of": as_of.isoformat(),
        },
        "covariate_notes": {
            "iso_week": "ISO week number of the map's Tuesday; may be 53.",
            "month": "Month of the map's Tuesday.",
        },
    }


def truth(today: date, spec: dict) -> pd.Series:
    if today > _now().date():
        raise RuntimeError("Cannot settle future dates.")
    # update_truth keeps the first value seen for each map; later changes are not used.
    return _maps(today, spec)[0]


def baseline(run_date: date, spec: dict) -> dict:
    return {"source": {"name": "none: no official or expert forecast of this series"}, "forecasts": []}
