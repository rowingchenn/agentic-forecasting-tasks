"""US weekly influenza hospital admissions: where the numbers for this target come from.

The agentic-forecasting pipeline imports this file on the host, outside every episode; ALE
uploads tests/ only to verify, after the agent has finished. It needs only the standard
library and pandas. The pipeline passes `spec`, [metadata.agentic-forecasting] of task.toml
with the AF_* schedule merged in, and turns the results into archived files.

    snapshot(run_date, spec)  X_d: weekly admissions as first printed, the FluSight ensemble
                              as a run could see it, and the calendar
    truth(today, spec)        every week's admissions in NHSN's preliminary dataset now
    baseline(run_date, spec)  the FluSight-ensemble of the run date's round, as first
                              committed after the cutoff

The series is weekly: one row per MMWR week (Sunday to Saturday), dated on its Saturday.
The target runs year-round. Between FluSight seasons (about June to early October) there
is no round: X_d has no FluSight column and B has no forecasts.
No credentials are needed: data.cdc.gov and GitHub answer anonymous requests. GITHUB_TOKEN,
if set, lifts GitHub's limit of 60 API requests an hour per address; a run day makes three.
"""

from __future__ import annotations

import hashlib
import http.client
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

TARGET = "us_flu_hospital_admissions"
NHSN = "https://data.cdc.gov/resource/mpgq-jmmr.json"  # NHSN HRD, preliminary (Wednesdays)
NHSN_PAGE = (
    "https://data.cdc.gov/Public-Health-Surveillance/"
    "Weekly-Hospital-Respiratory-Data-HRD-Metrics-by-Ju/mpgq-jmmr"
)
JURISDICTION = "USA"
ADMISSIONS = "totalconfflunewadm"  # new admissions with laboratory-confirmed influenza
REPORTING = "totalconfflunewadmperchosprep"  # percent of hospitals that reported them
HUB = "cdcepi/FluSight-forecast-hub"
ENSEMBLE_DIR = "model-output/FluSight-ensemble"
ENSEMBLE_FILE = re.compile(r"(\d{4}-\d{2}-\d{2})-FluSight-ensemble\.csv")
ARCHIVE_DIR = "auxiliary-data/target-data-archive"  # the hub's weekly copies of NHSN's data
ARCHIVE_FILE = re.compile(r"target-hospital-admissions_(\d{4}-\d{2}-\d{2})\.csv")
FIRST_PRINT_LAG = timedelta(days=14)  # a copy this much newer than a week still counts
GITHUB_API = "https://api.github.com"
GITHUB_RAW = "https://raw.githubusercontent.com"
LOCATION = "US"  # the hub's code for the national total
FLU_TARGET = "wk inc flu hosp"
LICENSE = "CC BY 4.0"  # model-metadata/FluSight-ensemble.yml in the hub
USER_AGENT = "agentic-forecasting/1.0"
CACHE = (
    Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "agentic-forecasting" / TARGET
)
CACHE_VERSION = "v1"  # in the cache files' names; raise it when a parser changes
ROUND_COLUMNS = ["reference_date", "horizon", "target_end_date", "quantile", "value"]
COPY_COLUMNS = ["week", "value"]
# what a failed request, or a file that no longer parses, raises
FAILURES = (OSError, http.client.HTTPException, ValueError, KeyError)
REQUEST_PAUSE = 0.5  # seconds between requests
RATE_LIMIT_PAUSE = 10  # seconds to wait after a 429

_last_request = 0.0  # time.monotonic() of the last request


def _now() -> datetime:
    return datetime.now(UTC)


def _get(url: str, headers: dict | None = None, retries: int = 3) -> tuple[bytes, dict]:
    """The body at url, and where and when it was retrieved. A 429 is retried after
    RATE_LIMIT_PAUSE seconds, and a server error, timeout or dropped connection after a short
    wait, up to `retries` times; any other HTTP error is raised at once."""
    global _last_request
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    for attempt in range(retries + 1):
        time.sleep(max(0.0, _last_request + REQUEST_PAUSE - time.monotonic()))
        _last_request = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = response.read()
                modified = response.headers.get("Last-Modified")
        except urllib.error.HTTPError as exc:
            if attempt == retries or (exc.code != 429 and exc.code < 500):
                raise
            time.sleep(RATE_LIMIT_PAUSE if exc.code == 429 else 2**attempt)
            continue
        except (OSError, http.client.HTTPException):  # timeouts, refused or dropped connections
            if attempt == retries:
                raise
            time.sleep(2**attempt)
            continue
        meta = {
            "url": url,
            "retrieved_at": _now().isoformat(timespec="seconds"),
            "sha256": hashlib.sha256(body).hexdigest(),
        }
        if modified:
            meta["last_modified"] = modified
        return body, meta
    raise AssertionError("unreachable")


def _github(path: str) -> tuple[object, dict]:
    """A GitHub REST API answer, parsed; GITHUB_TOKEN, if set, authenticates the request."""
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body, meta = _get(f"{GITHUB_API}{path}", headers)
    return json.loads(body), meta


def _at(day: date, clock: str, timezone: str) -> datetime:
    """`clock` ("HH:MM", local) on `day`, in UTC."""
    hour, minute = map(int, clock.split(":"))
    local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZoneInfo(timezone))
    return local.astimezone(UTC)


def reference_date(run_date: date) -> date:
    """FluSight's reference date for a round run on run_date: the Saturday that ends its week."""
    return run_date + timedelta(days=(5 - run_date.weekday()) % 7)


def mmwr_week(saturday: date) -> int:
    """MMWR week number of the Sunday-to-Saturday week ending on `saturday`. Week 1 is the
    week that holds January 4 of the year its Wednesday falls in."""
    january4 = date((saturday - timedelta(days=3)).year, 1, 4)
    first_sunday = january4 - timedelta(days=(january4.weekday() + 1) % 7)
    return (saturday - timedelta(days=6) - first_sunday).days // 7 + 1


def holidays_per_week(index: pd.DatetimeIndex) -> pd.Series:
    """The number of US federal holidays (as observed) in each week, Sunday to Saturday."""
    days = USFederalHolidayCalendar().holidays(index.min() - pd.Timedelta(days=6), index.max())
    saturdays = days + pd.to_timedelta((5 - days.dayofweek) % 7, unit="D")
    return saturdays.value_counts().reindex(index, fill_value=0).astype(int)


def nhsn_weekly(reporting: bool = False) -> tuple[pd.DataFrame, dict]:
    """NHSN's national rows as published now, oldest first: the week (its Saturday) and the
    admissions, and with `reporting` the percent of hospitals that reported them."""
    columns = [ADMISSIONS, *([REPORTING] if reporting else [])]
    query = {
        "$select": ",".join(["weekendingdate", *columns]),
        "$where": f"jurisdiction='{JURISDICTION}'",
        "$order": "weekendingdate",
        "$limit": "10000",
    }
    body, meta = _get(f"{NHSN}?{urllib.parse.urlencode(query)}")
    rows = pd.DataFrame(json.loads(body), columns=["weekendingdate", *columns])
    weeks = pd.to_datetime(rows["weekendingdate"].str[:10], format="%Y-%m-%d")
    if weeks.empty or (weeks.dt.dayofweek != 5).any() or weeks.duplicated().any():
        raise ValueError("NHSN's national rows must be one per week, each dated on its Saturday")
    frame = pd.DataFrame(
        {"week": weeks, "admissions": pd.to_numeric(rows[ADMISSIONS], errors="coerce")}
    )
    if reporting:
        frame["hospitals_reporting_pct"] = pd.to_numeric(rows[REPORTING], errors="coerce")
    return frame.sort_values("week").reset_index(drop=True), meta


def parse_ensemble(raw: bytes) -> pd.DataFrame:
    """The national weekly-admissions quantiles in a FluSight-ensemble file."""
    frame = pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False)
    rows = frame[
        (frame["location"] == LOCATION)
        & (frame["target"] == FLU_TARGET)
        & (frame["output_type"] == "quantile")
    ]
    return pd.DataFrame(
        {
            "reference_date": pd.to_datetime(rows["reference_date"], format="%Y-%m-%d"),
            "horizon": pd.to_numeric(rows["horizon"]).astype(int),
            "target_end_date": pd.to_datetime(rows["target_end_date"], format="%Y-%m-%d"),
            "quantile": pd.to_numeric(rows["output_type_id"]).round(6),
            "value": pd.to_numeric(rows["value"]),
        }
    ).reset_index(drop=True)


def parse_copy(raw: bytes) -> pd.DataFrame:
    """The national weekly admissions in one of the hub's archived copies of NHSN's data."""
    frame = pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False)
    rows = frame[frame["location"] == LOCATION]
    copy = pd.DataFrame(
        {
            "week": pd.to_datetime(rows["date"], format="%Y-%m-%d"),
            "value": pd.to_numeric(rows["value"], errors="coerce"),
        }
    )
    return copy.dropna().reset_index(drop=True)


def _blob(raw: bytes) -> str:
    """Git's blob id of a file's content, as GitHub's listings give it."""
    return hashlib.sha1(b"blob %d\0" % len(raw) + raw).hexdigest()


def _hub_files(
    folder: str,
    pattern: re.Pattern,
    parse: Callable[[bytes], pd.DataFrame],
    columns: list[str],
    dates: list[str],
    name: str,
) -> tuple[pd.DataFrame, dict]:
    """Rows parsed from each file in a folder of the hub's main branch whose name matches
    `pattern`, with the file's key (the pattern's group) and its git blob id. Files are cached
    on the host by blob, so only new or changed files are downloaded. If GitHub fails, the
    cached rows stand in for the listing or for a file, and `error` says so: data that only
    the TSFMs see must not cost a run day."""
    path = CACHE / f"{name}.{CACHE_VERSION}.csv"
    empty = pd.DataFrame(columns=["key", "blob", *columns]).astype(
        {d: "datetime64[ns]" for d in dates}
    )
    cached = (
        pd.read_csv(path, dtype={"key": str, "blob": str}, parse_dates=dates)
        if path.is_file()
        else empty
    )
    meta: dict = {"folder": folder}
    try:
        listing, listed = _github(f"/repos/{HUB}/contents/{folder}?ref=main")
        files = {m.group(1): e for e in listing if (m := pattern.fullmatch(e.get("name", "")))}
    except FAILURES as exc:
        meta["error"] = f"listing failed, the cached files stand in: {exc!r}"
        return cached, meta
    meta |= {"url": listed["url"], "retrieved_at": listed["retrieved_at"], "files": len(files)}
    parts, failed = [], []
    for key, entry in sorted(files.items()):
        old = cached[cached["key"] == key]
        if not old.empty and (old["blob"] == entry["sha"]).all():
            parts.append(old)
            continue
        try:
            raw, _ = _get(entry["download_url"])
            parts.append(parse(raw).assign(key=key, blob=_blob(raw))[empty.columns])
        except FAILURES:
            failed.append(key)
            parts.append(old)  # its last cached version, if there is one
    if failed:
        meta["error"] = f"downloads failed, last cached versions stand in: {', '.join(failed)}"
    parts = [part for part in parts if not part.empty]
    rows = pd.concat(parts, ignore_index=True) if parts else empty
    CACHE.mkdir(parents=True, exist_ok=True)
    rows.to_csv(path.with_suffix(".tmp"), index=False, date_format="%Y-%m-%d")
    path.with_suffix(".tmp").replace(path)
    return rows, meta


def ensemble_rounds() -> tuple[pd.DataFrame, dict]:
    """Every FluSight-ensemble round's national quantiles as now on the hub's main branch."""
    dates = ["reference_date", "target_end_date"]
    return _hub_files(ENSEMBLE_DIR, ENSEMBLE_FILE, parse_ensemble, ROUND_COLUMNS, dates, "ensemble")


def first_prints(before: pd.Timestamp) -> tuple[pd.Series, dict]:
    """Each week's national admissions as first printed: from the earliest of the hub's weekly
    archived copies of NHSN's data that holds the week, among copies named before `before`
    and at most FIRST_PRINT_LAG after the week. A copy is named after the latest week it
    holds and is normally made on the Wednesday NHSN posts that week, so a week's earliest
    copy is normally its first print, or its second where the archive skips a week."""
    copies, meta = _hub_files(ARCHIVE_DIR, ARCHIVE_FILE, parse_copy, COPY_COLUMNS, ["week"], "nhsn")
    named = pd.to_datetime(copies["key"], format="%Y-%m-%d")
    usable = copies.assign(named=named)[
        (named < before) & (named - copies["week"] <= pd.Timedelta(FIRST_PRINT_LAG))
    ]
    firsts = usable.sort_values("named").groupby("week")["value"].first().astype(float)
    return firsts, meta | {"copies": int(usable["key"].nunique())}


def flusight_as_seen(
    rounds: pd.DataFrame,
    index: pd.DatetimeIndex,
    target: pd.Series,
    reference: pd.Timestamp,
    last_observed: pd.Timestamp,
) -> pd.Series:
    """The FluSight-ensemble median of each week on `index` as a run could see it: for a past
    week, from the latest round before the run whose first target week it was (normally one
    week ahead); for the forecast window, from the latest round before this run, carried
    forward to weeks it does not reach. A past week no round covered carries the week
    before's admissions, a no-change forecast."""
    medians = rounds[rounds["quantile"] == 0.5]
    seen = pd.Series(float("nan"), index=index, name="flusight")
    for week in index:
        before = week if week <= last_observed else reference
        rows = medians[(medians["target_end_date"] == week) & (medians["reference_date"] < before)]
        if not rows.empty:
            seen[week] = rows.sort_values("reference_date")["value"].iloc[-1]
    past = index <= last_observed
    seen[past] = seen[past].fillna(target.shift(1)[past]).fillna(target[past])
    seen[~past] = seen[~past].ffill().fillna(target[past].iloc[-1])
    return seen


def snapshot(run_date: date, spec: dict) -> dict:
    """X_d as published right now: one row per week from history_start to the last target
    date, `target` empty after the last week NHSN has published, covariates known for the
    window."""
    start = pd.Timestamp(spec["x"]["history_start"])
    reference = pd.Timestamp(reference_date(run_date))
    targets = [pd.Timestamp(run_date + timedelta(days=h)) for h in spec["horizons"]]
    if any(t.dayofweek != 5 for t in targets):
        raise ValueError(f"target dates must be Saturdays, the ends of MMWR weeks: {targets}")
    try:
        nhsn, nhsn_meta = nhsn_weekly(reporting=True)
    except urllib.error.HTTPError as exc:
        if exc.code != 400:
            raise
        nhsn, nhsn_meta = nhsn_weekly()  # NHSN has renamed or dropped the reporting column
    current = nhsn.set_index("week")["admissions"].dropna()
    current = current[current.index < pd.Timestamp(run_date)]
    if current.empty or current.index.max() < start:
        raise RuntimeError("NHSN has no national admissions between history_start and the run")
    last_observed = current.index.max()
    if last_observed != reference - pd.Timedelta(days=7):
        # NHSN posts last week on the Wednesday, normally by 12:05 ET; without it the run would
        # forecast from a staler history than the forecasters it is compared with.
        week = (reference - pd.Timedelta(days=7)).date()
        raise RuntimeError(
            f"NHSN has not posted the week ending {week} (latest {last_observed.date()})"
        )
    # The newest week as NHSN publishes it now, its first print; older weeks as first
    # printed where the hub's archive holds them, as revised since where it does not.
    firsts, copies_meta = first_prints(pd.Timestamp(run_date))
    firsts = firsts[firsts.index < last_observed]
    history = firsts.combine_first(current)

    # One week before history_start, so that the first row has a week before it.
    index = pd.date_range(start - pd.Timedelta(days=7), max(targets), freq="W-SAT")
    past = index <= last_observed
    target = history.reindex(index)
    target[past] = target[past].interpolate(limit_area="inside")
    rounds, rounds_meta = ensemble_rounds()
    visible = rounds[rounds["reference_date"] < reference]  # this run's round is B
    # Between seasons no round reaches the forecast window: X_d then has no FluSight column.
    in_season = visible["target_end_date"].isin(index[~past]).any()
    flusight = (
        [flusight_as_seen(visible, index, target, reference, last_observed)] if in_season else []
    )
    week = pd.Series([mmwr_week(day.date()) for day in index], index=index, name="week")
    holiday = holidays_per_week(index).rename("holiday")
    series = pd.concat([target.rename("target"), *flusight, week, holiday], axis=1).loc[start:]
    shown = firsts.index >= start
    observed = nhsn[(nhsn["week"] >= start) & (nhsn["week"] <= last_observed)]
    x_d = {
        "series": series.rename_axis("timestamp"),
        "last_observed": last_observed.date(),
        "raw": {
            "nhsn_us_weekly.csv": observed,
            "nhsn_us_first_prints.csv": firsts[shown]
            .rename("value")
            .rename_axis("week")
            .reset_index(),
            "flusight_ensemble_us.csv": visible.drop(columns="key").sort_values(
                ["reference_date", "target_end_date", "quantile"]
            ),
        },
        "sources": {
            "nhsn": {**nhsn_meta, "page": NHSN_PAGE, "jurisdiction": JURISDICTION},
            "nhsn_first_prints": {
                "repository": f"https://github.com/{HUB}",
                **copies_meta,
                "weeks": int(shown.sum()),
            },
            "flusight": {
                "repository": f"https://github.com/{HUB}",
                **rounds_meta,
                "rounds_before_run": int(visible["reference_date"].nunique()),
                "license": LICENSE,
            },
        },
        "covariate_notes": {
            "target": "new hospital admissions with laboratory-confirmed influenza in the "
            "United States in the MMWR week ending on the date (NHSN totalconfflunewadm, "
            "jurisdiction USA, preliminary dataset). The newest week is as NHSN publishes it at "
            "the snapshot, its first print; older weeks are as first printed, from the earliest "
            "of the FluSight hub's weekly archived copies of NHSN's data made within two weeks "
            "of the week, and NHSN's current, revised value where there is none (before "
            "September 2023 and in archive gaps). A missing week is filled linearly",
            "flusight": "median of the CDC FluSight ensemble for the week: for past weeks from "
            "the latest round before the run whose first target week it was (normally one "
            "week ahead), for the forecast window from the latest round before this run, "
            "carried forward past its last week; past weeks no round covered (before October "
            "2023, between seasons) carry the week before's admissions",
            "week": "MMWR week number of the year, 1 to 53",
            "holiday": "number of US federal holidays in the week, as observed",
        },
    }
    if not in_season:
        del x_d["covariate_notes"]["flusight"]
    return x_d


def truth(today: date, spec: dict) -> pd.Series:
    """Every week's admissions in NHSN's preliminary dataset as published now. The pipeline
    keeps the first value it sees for a week: that of the Wednesday release after it ends.
    If NHSN cannot be read, nothing is recorded today and a warning is printed, so that the
    day's run goes on."""
    try:
        nhsn, _ = nhsn_weekly()
    except FAILURES as exc:
        print(f"warning: NHSN not read, no truth recorded today: {exc!r}", file=sys.stderr)
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    values = nhsn.set_index("week")["admissions"].dropna()
    start = pd.Timestamp(spec["x"]["history_start"])
    return values[(values.index >= start) & (values.index < pd.Timestamp(today))]


def baseline(run_date: date, spec: dict) -> dict:
    """The FluSight-ensemble of the run date's round, as first committed after the cutoff,
    which the agents cannot see: normally on Thursday morning, after teams submit by 11 p.m.
    ET on Wednesday. Empty until it is committed, and empty if a version was committed
    before the cutoff, since the agents could have seen that one."""
    timezone = spec["schedule"]["timezone"]
    cutoff = _at(run_date, spec["schedule"]["cutoff"], timezone)
    reference = reference_date(run_date)
    path = f"{ENSEMBLE_DIR}/{reference.isoformat()}-FluSight-ensemble.csv"
    query = urllib.parse.urlencode({"path": path, "per_page": 100})
    commits, _ = _github(f"/repos/{HUB}/commits?{query}")
    versions = sorted(
        (datetime.fromisoformat(c["commit"]["committer"]["date"].replace("Z", "+00:00")), c["sha"])
        for c in commits
    )
    source = {
        "report": f"CDC FluSight-ensemble, round of {reference.isoformat()}",
        "posted": None,
        "model": "FluSight-ensemble",
        "repository": f"https://github.com/{HUB}",
        "file": path,
        "license": LICENSE,
    }
    early = [when for when, _ in versions if when <= cutoff]
    late = [(when, sha) for when, sha in versions if when > cutoff]
    if early:
        note = f"committed before the cutoff ({early[0].isoformat()}), visible to the agents"
        return {"source": source | {"note": note}, "forecasts": []}
    if not late:
        return {"source": source | {"note": "not committed yet"}, "forecasts": []}
    committed, sha = late[0]
    raw, meta = _get(f"{GITHUB_RAW}/{HUB}/{sha}/{path}")
    rows = parse_ensemble(raw)
    forecasts = []
    for h in spec["horizons"]:
        day = run_date + timedelta(days=h)
        found = rows[rows["target_end_date"] == pd.Timestamp(day)].set_index("quantile")["value"]
        levels = {
            str(q): round(float(found[round(q, 6)]), 1)
            for q in spec["quantiles"]
            if round(q, 6) in found.index
        }
        if len(levels) == len(spec["quantiles"]):
            forecasts.append({"target_date": day.isoformat(), "quantiles": levels})
    source |= {
        "posted": committed.isoformat(),
        "commit": sha,
        "url": meta["url"],
        "retrieved_at": meta["retrieved_at"],
        "sha256": meta["sha256"],
    }
    return {"source": source, "forecasts": forecasts}
