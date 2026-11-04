"""Planetary Kp index, daily maximum: where the numbers for this target come from.

The agentic-forecasting pipeline imports this file on the host, outside every episode; the
sandbox's setup stage never runs it, and setup/run.sh makes it unreadable to the agent.
It needs only the standard library and pandas. The pipeline passes `spec`, the target
definition from task.yaml (`extras.agentic-forecasting` plus `params`), and turns the
results into archived files.

    snapshot(run_date, spec)  X_d: daily maximum Kp, SWPC's 3-day forecasts as a run could
                              see them, and the day of the year
    truth(today, spec)        every complete UTC day's maximum in SWPC's Kp product now
    baseline(run_date, spec)  SWPC's 3-day forecast, its first issue after the cutoff

Kp is NOAA SWPC's estimated planetary K index: one value per three-hour block of the UTC day,
in thirds written as decimals (2- = 1.67, 2o = 2.00, 2+ = 2.33). SWPC's Kp product keeps the
last seven days. Older days come from SWPC's Daily Space Weather Indices, which carry the same
values, and past 3-day forecasts from NCEI's archive; both are cached on the host. No
credentials: services.swpc.noaa.gov, www.ngdc.noaa.gov and SWPC's FTP server answer anonymous
requests. NOAA's products are in the public domain; the values derived here are not official.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

TARGET = "kp_index_daily_max"
SWPC = "https://services.swpc.noaa.gov"
KP_PRODUCT = f"{SWPC}/products/noaa-planetary-k-index.json"  # three-hourly, the last 7 days
LIVE_FORECAST = f"{SWPC}/text/3-day-forecast.txt"  # the latest issue only
FORECAST_PAGE = "https://www.swpc.noaa.gov/products/3-day-forecast"
NCEI = "https://www.ngdc.noaa.gov/stp/space-weather/swpc-products/daily_reports"
FORECAST_ARCHIVE = f"{NCEI}/3day_forecast"  # every issue, normally by 04:30 UTC the next day
INDICES_ARCHIVE = f"{NCEI}/space_weather_indices"  # one file per day, two days later
FTP_FORECASTS = "ftp://ftp.swpc.noaa.gov/pub/forecasts/three_day"  # SWPC's last ~75 days
BLOCK_HOURS = (0, 3, 6, 9, 12, 15, 18, 21)  # UTC start of each three-hour block
BLOCK = pd.Timedelta(hours=3)
K_COLUMNS = [f"k{hour:02d}" for hour in BLOCK_HOURS]  # a day's eight values, in cached rows
SLOTS = ("1230", "0030")  # the times SWPC files its 3-day forecast issues under, UTC
MODEL = "NOAA SWPC"
CREDIT = "NOAA Space Weather Prediction Center; public domain, derived values are not official"
USER_AGENT = "agentic-forecasting/1.0"
CACHE = (
    Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "agentic-forecasting" / TARGET
)
CACHE_VERSION = "v1"  # in the cache files' names; raise it when a parser changes
# An archive file still absent this long after its day is recorded as missing, so that it is
# not asked for again every day; until then it may yet be archived late.
MISSING_AFTER = timedelta(days=30)
# what a failed request, or a file that no longer parses, raises
FAILURES = (OSError, http.client.HTTPException, ValueError, KeyError)
REQUEST_PAUSE = 0.25  # seconds between requests
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


def _get_optional(url: str) -> tuple[bytes, dict] | None:
    """The body at url and where and when it was retrieved, or None if the server has none."""
    try:
        return _get(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _at(day: date, clock: str, timezone: str) -> pd.Timestamp:
    """`clock` ("HH:MM", local) on `day`, in UTC without a zone, as every time here."""
    hour, minute = map(int, clock.split(":"))
    local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZoneInfo(timezone))
    return pd.Timestamp(local.astimezone(UTC).replace(tzinfo=None))


def _no_blocks() -> pd.Series:
    return pd.Series(dtype=float, index=pd.DatetimeIndex([]), name="kp")


def parse_kp_product(raw: bytes) -> pd.Series:
    """SWPC's planetary Kp product, Kp by the UTC start of its three-hour block: a list of
    objects (time_tag, Kp, a_running, station_count); its older form, a header row followed
    by rows of strings, is read as well. A block listed twice keeps its last value. A value
    off the 0 to 9 scale, such as a -1 placeholder, is left out with a warning, so that its
    day stays incomplete and the rest of the product still counts; only time tags that do not
    start three-hour blocks make the whole product unreadable."""
    rows = json.loads(raw)
    if not isinstance(rows, list):
        raise ValueError("SWPC's Kp product must be a list of rows")
    if rows and isinstance(rows[0], list):
        rows = [dict(zip(rows[0], row, strict=False)) for row in rows[1:]]
    frame = pd.DataFrame(rows, columns=["time_tag", "Kp"])
    starts = pd.to_datetime(frame["time_tag"], format="ISO8601")
    if starts.dt.tz is not None:
        starts = starts.dt.tz_convert("UTC").dt.tz_localize(None)
    blocks = pd.Series(
        pd.to_numeric(frame["Kp"], errors="coerce").to_numpy(dtype=float),
        index=pd.DatetimeIndex(starts),
        name="kp",
    )
    blocks = blocks[~blocks.index.duplicated(keep="last")].sort_index()
    times = blocks.index
    if (
        times.hasnans
        or not (times.hour.isin(BLOCK_HOURS) & (times.minute == 0) & (times.second == 0)).all()
    ):
        raise ValueError("Kp values must each start a three-hour block at 00, 03, ... 21 UTC")
    off = (blocks < 0) | (blocks > 9)
    if off.any():
        shown = ", ".join(f"{t:%Y-%m-%d %H:%M} {v}" for t, v in blocks[off].items())
        print(f"warning: Kp values off the 0-9 scale left out: {shown}", file=sys.stderr)
        blocks[off] = float("nan")
    return blocks


def daily_max(blocks: pd.Series) -> pd.Series:
    """The maximum of each UTC day's eight three-hourly values, for days that have all eight,
    indexed by the day, to two decimals."""
    blocks = blocks.dropna()
    days = blocks.groupby(blocks.index.normalize()).agg(["max", "count"])
    return days.loc[days["count"] == len(BLOCK_HOURS), "max"].rename("kp").round(2)


def indices_url(day: date) -> str:
    return f"{INDICES_ARCHIVE}/{day:%Y}/{day:%m}/{day:%Y%m%d}dayind.txt"


def forecast_url(stamp: str) -> str:
    """NCEI's copy of the issue SWPC filed as `stamp` (YYYYMMDDHHMM, the slot it was due)."""
    return f"{FORECAST_ARCHIVE}/{stamp[:4]}/{stamp[4:6]}/{stamp}three_day_forecast.txt"


def ftp_url(day: date, slot: str) -> str:
    """SWPC's own copy, named without the year; the server keeps about 75 days."""
    return f"{FTP_FORECASTS}/{day:%m%d}{slot}three_day_forecast.txt"


def parse_indices(raw: bytes) -> tuple[date, list[float]]:
    """The day and its eight estimated planetary K values (three-hourly from 00 UTC, missing
    as NaN) in one of SWPC's Daily Space Weather Indices files: the last eight of the 18
    numbers on the second data line of its Geomagnetic_Indices section, which follow
    College's A and K values and the planetary A."""
    lines = raw.decode("ascii", errors="replace").splitlines()
    header = next(
        (i for i, line in enumerate(lines) if line.startswith(":Geomagnetic_Indices:")), None
    )
    if header is None:
        raise ValueError("no Geomagnetic_Indices section")
    day = datetime.strptime(lines[header].split(":", 2)[2].strip(), "%Y %b %d").date()
    data = []
    for line in lines[header + 1 :]:
        if line.startswith(":"):
            break
        if line.strip() and not line.startswith("#"):
            data.append(line)
    numbers = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", data[1])] if len(data) > 1 else []
    if len(numbers) != 18:
        raise ValueError(f"Geomagnetic_Indices of {day}: expected 18 numbers on the planetary line")
    values = [float("nan") if v < 0 else v for v in numbers[-8:]]  # -1 marks a missing value
    if any(v > 9 for v in values):
        raise ValueError(f"Geomagnetic_Indices of {day}: a planetary K value above 9")
    return day, values


ISSUED = re.compile(r"^:Issued:\s*(\d{4} [A-Z][a-z]{2} \d{1,2} \d{4}) UTC", re.MULTILINE)
DAY_LABEL = re.compile(r"\b([A-Z][a-z]{2}) (\d{1,2})\b")
BLOCK_ROW = re.compile(r"^(\d{2})-\d{2}UT\b(.*)$")
G_SCALE = re.compile(r"\(G\d\)")  # NOAA's storm scale label, which SWPC adds from 4.67 (5-) up


def parse_forecast(raw: bytes) -> tuple[pd.Timestamp, pd.DataFrame]:
    """A SWPC 3-Day Forecast: when it was issued (UTC) and its "NOAA Kp index breakdown", one
    row per three-hour block (named by its start hour, k00 to k21) and one column per UTC
    day, with G-scale labels such as "(G1)" dropped."""
    text = raw.decode("ascii", errors="replace")
    found = ISSUED.search(text)
    if found is None:
        raise ValueError("no :Issued: line")
    issued = pd.Timestamp(datetime.strptime(found.group(1), "%Y %b %d %H%M"))
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if "Kp index breakdown" in line), None)
    if start is None:
        raise ValueError("no NOAA Kp index breakdown")
    following = [line for line in lines[start + 1 :] if line.strip()]
    labels = DAY_LABEL.findall(following[0]) if following else []
    days = [_label_day(month, day, issued) for month, day in labels]
    if len(days) != 3 or any((b - a).days != 1 for a, b in zip(days, days[1:], strict=False)):
        raise ValueError(f"issue of {issued}: the breakdown must have three consecutive days")
    rows = {}
    for line in following[1:]:
        row = BLOCK_ROW.match(line.strip())
        if row is None:
            break
        values = re.findall(r"\d+(?:\.\d+)?", G_SCALE.sub("", row.group(2)))
        rows[f"k{row.group(1)}"] = [float(v) for v in values]
    if list(rows) != K_COLUMNS or any(
        len(v) != 3 or not 0 <= min(v) <= max(v) <= 9 for v in rows.values()
    ):
        raise ValueError(
            f"issue of {issued}: the breakdown must have eight rows of three Kp values"
        )
    table = pd.DataFrame(rows, index=pd.DatetimeIndex(days)).T
    return issued, table


def _label_day(month: str, day: str, issued: pd.Timestamp) -> pd.Timestamp:
    """A breakdown column's "Mon DD" as the date nearest the issue, across a new year either
    way: an issue of Dec 31 covers Jan 01, and a late one issued on Jan 01 still starts on
    Dec 31."""
    stamp = pd.Timestamp(datetime.strptime(f"{issued.year} {month} {int(day):02d}", "%Y %b %d"))
    if stamp < issued - pd.Timedelta(days=180):
        stamp = stamp.replace(year=issued.year + 1)
    elif stamp > issued + pd.Timedelta(days=180):
        stamp = stamp.replace(year=issued.year - 1)
    return stamp


def issue_rows(file: str, issued: pd.Timestamp, table: pd.DataFrame) -> pd.DataFrame:
    """One row (file, issued, day, k00 ... k21) per day an issue covers."""
    rows = table.T.rename_axis("day").reset_index()
    return rows.assign(file=file, issued=issued)[["file", "issued", "day", *K_COLUMNS]]


def kp_product() -> tuple[pd.Series, dict]:
    """SWPC's estimated planetary Kp of the last seven days, as published now."""
    body, meta = _get(KP_PRODUCT)
    return parse_kp_product(body), meta


def _empty(columns: list[str], dates: list[str]) -> pd.DataFrame:
    frame = pd.DataFrame({c: pd.Series(dtype=float) for c in columns})
    return frame.astype({"file": str, **{d: "datetime64[ns]" for d in dates}})


def _read_cache(name: str, columns: list[str], dates: list[str]) -> pd.DataFrame:
    """A cache file's rows. A file that does not read as written, say with a second header
    line from two writers or a cut-off row, is moved aside with a warning and the cache
    starts empty, so that it is filled again from the archive instead of stopping the day."""
    path = CACHE / f"{name}.{CACHE_VERSION}.csv"
    if not path.is_file():
        return _empty(columns, dates)
    try:
        rows = pd.read_csv(path, dtype={"file": str, **dict.fromkeys(K_COLUMNS, float)})
        if not isinstance(rows.index, pd.RangeIndex):  # pandas made extra fields an index
            raise ValueError("a row with more fields than the header")
        rows = rows[columns].copy()
        for column in dates:
            rows[column] = pd.to_datetime(rows[column], format="ISO8601")
        if rows["file"].isna().any():
            raise ValueError("a row without its file")
        return rows
    except (ValueError, TypeError, KeyError) as exc:
        aside = path.with_name(f"{path.name}.damaged-{_now():%Y%m%dT%H%M%S}")
        path.replace(aside)
        print(f"warning: {path} unreadable, moved to {aside.name}: {exc!r}", file=sys.stderr)
        return _empty(columns, dates)


def _append_cache(name: str, rows: pd.DataFrame) -> None:
    """Add rows to a cache file. Only the writer that creates the file writes its header, so
    two runs filling a cold cache at once cannot leave a second header inside it."""
    path = CACHE / f"{name}.{CACHE_VERSION}.csv"
    CACHE.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x") as created:
            created.write(",".join(rows.columns) + "\n")
    except FileExistsError:
        pass
    rows.to_csv(path, mode="a", header=False, index=False, date_format="%Y-%m-%dT%H:%M")


def _concat(parts: list[pd.DataFrame], empty: pd.DataFrame) -> pd.DataFrame:
    parts = [part for part in parts if not part.empty]
    return pd.concat(parts, ignore_index=True) if parts else empty


def _union(preferred: pd.Series, other: pd.Series) -> pd.Series:
    """`preferred`'s values, and `other`'s where `preferred` has none."""
    if preferred.empty or other.empty:
        return (other if preferred.empty else preferred).sort_index()
    return preferred.combine_first(other)


def _blocks(rows: pd.DataFrame) -> pd.Series:
    """Three-hourly values by block start, from rows of (day, k00 ... k21)."""
    long = rows.melt(id_vars="day", value_vars=K_COLUMNS, var_name="block", value_name="kp")
    long = long.dropna(subset=["kp"])
    if long.empty:
        return _no_blocks()
    starts = long["day"] + pd.to_timedelta(long["block"].str[1:].astype(int), unit="h")
    blocks = pd.Series(long["kp"].to_numpy(dtype=float), index=pd.DatetimeIndex(starts), name="kp")
    return blocks[~blocks.index.duplicated(keep="last")].sort_index()


def _missing(day: date) -> bool:
    """Whether a file of `day` that the archive lacks now should be recorded as missing."""
    return _now().date() - day >= MISSING_AFTER


def kp_history(first_day: date, last_day: date) -> tuple[pd.Series, dict]:
    """SWPC's estimated planetary Kp of each three-hour block from first_day to last_day, as
    in SWPC's Daily Space Weather Indices, one file per day in NCEI's archive (normally there
    by 04:30 UTC two days after the day). Each day is read once and cached on the host. If
    NCEI fails, it is not asked again in this call and the cached days stand in, and `error`
    says so: data that only the TSFMs see must not cost a run day."""
    columns = ["file", "day", *K_COLUMNS]
    cached = _read_cache("indices", columns, ["day"])
    have = set(cached["file"])
    meta: dict = {"url": f"{INDICES_ARCHIVE}/", "first_day": first_day.isoformat()}
    parts, read = [cached], 0
    for day in pd.date_range(first_day, last_day).date:
        file = f"{day:%Y%m%d}"
        if file in have:
            continue
        try:
            found = _get_optional(indices_url(day))
        except (OSError, http.client.HTTPException) as exc:
            meta["error"] = f"NCEI failed, the cached days stand in: {exc!r}"
            break
        values = None
        if found is not None:
            try:
                published, values = parse_indices(found[0])
                if published != day:
                    raise ValueError(f"{indices_url(day)} holds {published}")
                read += 1
            except ValueError:
                values = None
        if values is None:
            if not _missing(day):
                continue  # not archived yet, or a file that may still be corrected
            values = [float("nan")] * len(K_COLUMNS)
        fresh = pd.DataFrame([[file, pd.Timestamp(day), *values]], columns=columns)
        _append_cache("indices", fresh)
        parts.append(fresh)
    rows = _concat(parts, cached)
    blocks = _blocks(rows[["day", *K_COLUMNS]])
    end = pd.Timestamp(last_day) + pd.Timedelta(days=1)
    blocks = blocks[(blocks.index >= pd.Timestamp(first_day)) & (blocks.index < end)]
    meta |= {"files_read": read, "days": int(blocks.index.normalize().nunique())}
    return blocks, meta


def cached_history() -> pd.Series:
    """Every three-hourly value in the host's cache of SWPC's daily indices; nothing is
    requested."""
    rows = _read_cache("indices", ["file", "day", *K_COLUMNS], ["day"])
    return _blocks(rows[["day", *K_COLUMNS]])


def forecast_issues(
    first_day: date, run_date: date, as_of: pd.Timestamp
) -> tuple[pd.DataFrame, dict]:
    """Every SWPC 3-day forecast issue published before `as_of` that a run on run_date can
    use, one row (file, issued, day, k00 ... k21) per day it covers: from NCEI's archive the
    12:30 issue of each day from first_day to the day before run_date, or the day's 00:30
    issue where the 12:30 one is missing; and the newest, normally 00:30 on the run date,
    from SWPC's live file. The live file holds only the latest issue, so if it fails, or
    already holds a later one (a snapshot run late or for a past date), the run date's 00:30
    issue is read from NCEI, which has it from the next day, or else from SWPC's FTP server.
    Archived issues are read once and cached on the host. If NCEI fails, it is not asked
    again in this call and the cached issues stand in, and `error` says so."""
    columns, dates = ["file", "issued", "day", *K_COLUMNS], ["issued", "day"]
    cached = _read_cache("forecasts", columns, dates)
    known = {file: rows for file, rows in cached.groupby("file")}
    meta: dict = {"archive": f"{FORECAST_ARCHIVE}/", "first_day": first_day.isoformat()}
    parts: list[pd.DataFrame] = []
    read, down = 0, False

    def archived(stamp: str, day: date) -> pd.DataFrame:
        """The issue NCEI holds as `stamp`, cached or read now; empty if there is none."""
        nonlocal read, down
        if stamp not in known and not down:
            try:
                found = _get_optional(forecast_url(stamp))
            except (OSError, http.client.HTTPException) as exc:
                meta["error"] = f"NCEI failed, the cached issues stand in: {exc!r}"
                down = True
                found = None
            else:
                rows = None
                if found is not None:
                    try:
                        rows = issue_rows(stamp, *parse_forecast(found[0]))
                        read += 1
                    except ValueError:
                        rows = None
                if rows is None and _missing(day):
                    rows = _empty(columns, dates).reindex([0]).assign(file=stamp)
                if rows is not None:
                    _append_cache("forecasts", rows)
                    known[stamp] = rows
        return known.get(stamp, _empty(columns, dates)).dropna(subset=["issued"])

    for day in pd.date_range(first_day, run_date - timedelta(days=1)).date:
        for slot in SLOTS:  # the 00:30 issue is needed only where the 12:30 one is missing
            rows = archived(f"{day:%Y%m%d}{slot}", day)
            if not rows.empty:
                parts.append(rows)
                break
    try:
        body, live = _get(LIVE_FORECAST)
        issued, table = parse_forecast(body)
        meta["live"] = {**live, "issued": issued.isoformat()}
        if issued < as_of:
            parts.append(issue_rows("live", issued, table))
    except FAILURES as exc:
        meta["live_error"] = f"SWPC's live file not read: {exc!r}"

    def lacks_run_date() -> bool:
        """Whether no issue of the run date published before `as_of` is at hand."""
        issued = _concat(parts, _empty(columns, dates))["issued"]
        issued = issued[issued < as_of]
        return issued.empty or issued.max() < pd.Timestamp(run_date)

    if lacks_run_date():  # the run date's 00:30 issue
        parts.append(archived(f"{run_date:%Y%m%d}0030", run_date))
    if lacks_run_date():
        try:
            raw, ftp = _get(ftp_url(run_date, "0030"), retries=1)
            issued, table = parse_forecast(raw)
            if table.columns[0] == pd.Timestamp(run_date) and issued < as_of:
                parts.append(issue_rows("ftp", issued, table))
                meta["ftp"] = {**ftp, "issued": issued.isoformat()}
        except FAILURES as exc:  # a missing file fails like a broken connection
            meta["ftp_error"] = f"SWPC's FTP server has no 00:30 issue for the run date: {exc!r}"
    issues = _concat(parts, _empty(columns, dates))
    issues = issues[issues["issued"] < as_of].drop_duplicates(["issued", "day"], keep="last")
    issues = issues.astype({c: float for c in K_COLUMNS}).sort_values(["issued", "day"])
    meta |= {"files_read": read, "issues": int(issues["issued"].nunique())}
    if not issues.empty:
        meta["newest_issue"] = issues["issued"].max().isoformat()
    return issues.reset_index(drop=True), meta


def swpc_as_seen(
    issues: pd.DataFrame,
    index: pd.DatetimeIndex,
    target: pd.Series,
    last_observed: pd.Timestamp,
    as_of: pd.Timestamp,
) -> pd.Series:
    """SWPC's forecast of each day's maximum on `index` as a run could see it: for a past day,
    from the latest issue published before the day began (normally 12:30 UTC the day before,
    one day ahead); for the window, from the latest issue before the snapshot (normally 00:30
    UTC on the run date), carried forward to a day it does not cover. A past day no issue
    covered carries the day before's maximum, a no-change forecast."""
    kp = issues[K_COLUMNS].max(axis=1)
    rows = issues[["issued", "day"]].assign(kp=kp)
    before = pd.Series(
        [day if day <= last_observed else as_of for day in index], index=index, name="before"
    )
    rows = rows.merge(before, left_on="day", right_index=True)
    rows = rows[rows["issued"] < rows["before"]].sort_values("issued")
    seen = rows.groupby("day")["kp"].last().reindex(index).rename("swpc").astype(float)
    past = index <= last_observed
    seen[past] = seen[past].fillna(target.shift(1)[past])
    seen[~past] = seen[~past].ffill().fillna(target[past].iloc[-1])
    return seen.round(2)


def snapshot(run_date: date, spec: dict) -> dict:
    """X_d as published right now: one row per UTC day from history_start to the last target
    date, `target` empty after the last complete day, covariates known for the window."""
    start = pd.Timestamp(spec["x"]["history_start"])
    as_of = _at(run_date, spec["schedule"]["snapshot"], spec["schedule"]["timezone"])
    run = pd.Timestamp(run_date)
    first = start - pd.Timedelta(days=1)  # the day before, which the first row falls back on
    try:
        product, product_meta = kp_product()
    except FAILURES as exc:
        product = _no_blocks()
        product_meta = {"url": KP_PRODUCT, "error": f"not read, NCEI's archive stands in: {exc!r}"}
    archived, archive_meta = kp_history(first.date(), run_date - timedelta(days=1))
    # The product's values where it has them, its first publication; NCEI's for older days.
    blocks = _union(product, archived)
    blocks = blocks[(blocks.index >= first) & (blocks.index + BLOCK <= as_of)]
    daily = daily_max(blocks)
    daily = daily[daily.index < run]
    if daily.empty or daily.index.max() < start:
        raise RuntimeError("no complete day of Kp between history_start and the run")
    last_observed = daily.index.max()

    index = pd.date_range(first, run + pd.Timedelta(days=max(spec["horizons"])), freq="D")
    past = index <= last_observed
    target = daily.reindex(index)
    target[past] = target[past].interpolate(limit_area="inside")
    issues, issues_meta = forecast_issues(first.date() - timedelta(days=2), run_date, as_of)
    swpc = swpc_as_seen(issues, index, target, last_observed, as_of)
    doy = pd.Series(index.dayofyear, index=index, name="doy")
    series = pd.concat([target.rename("target"), swpc, doy], axis=1).loc[start:]
    shown = blocks[blocks.index >= start]
    source = pd.Series("ncei_daily_indices", index=shown.index)
    source[shown.index.isin(product.index)] = "swpc_kp_product"
    forecasts = issues[issues["day"] >= first]
    forecasts = forecasts.assign(kp=forecasts[K_COLUMNS].max(axis=1)).drop(columns="file")
    return {
        "series": series.rename_axis("timestamp"),
        "last_observed": last_observed.date(),
        "raw": {
            "kp_3hourly.csv": pd.DataFrame(
                {"block_start": shown.index, "kp": shown.to_numpy(), "source": source.to_numpy()}
            ),
            "swpc_forecasts.csv": forecasts,
        },
        "sources": {
            "kp": {**product_meta, "credit": CREDIT},
            "kp_archive": archive_meta,
            "swpc_forecast": {**issues_meta, "page": FORECAST_PAGE, "credit": CREDIT},
        },
        "covariate_notes": {
            "target": "maximum of the UTC day's eight three-hourly estimated planetary Kp "
            "values of NOAA SWPC, in thirds (1.67 = 2-, 2.33 = 2+): the last seven days from "
            "SWPC's Kp product as published at the snapshot, older days from SWPC's Daily Space "
            "Weather Indices (NCEI archive); a missing day is filled linearly",
            "swpc": "maximum of SWPC's 3-day forecast of the day's eight three-hourly Kp "
            "values: for past days from the latest issue published before the day began "
            "(normally 12:30 UTC the day before), for the run date onward from the latest issue "
            "before the snapshot (normally 00:30 UTC on the run date), carried forward past its "
            "last day; a past day no issue covered carries the day before's maximum",
            "doy": "day of the year, 1 to 366; geomagnetic activity is higher around the equinoxes",
        },
    }


def truth(today: date, spec: dict) -> pd.Series:
    """Every complete UTC day's maximum Kp: the last seven days as SWPC's Kp product publishes
    them now, and from the host's cache of SWPC's daily indices, which snapshot fills, the
    older days and any block the product does not show (nothing is requested from NCEI
    here). The pipeline keeps the first value it sees for a day: normally that of the
    morning after it ends. If SWPC's product cannot be read, nothing is recorded today and a
    warning is printed, so that the day's run goes on; if the cache cannot be read, the
    product alone counts."""
    try:
        product, _ = kp_product()
    except FAILURES as exc:
        print(
            f"warning: SWPC's Kp product not read, no truth recorded today: {exc!r}",
            file=sys.stderr,
        )
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    try:
        values = daily_max(_union(product, cached_history()))
    except (*FAILURES, TypeError) as exc:
        print(
            f"warning: the cache of SWPC's daily indices not read, the product alone counts: "
            f"{exc!r}",
            file=sys.stderr,
        )
        values = daily_max(product)
    start = pd.Timestamp(spec["x"]["history_start"])
    return values[(values.index >= start) & (values.index < pd.Timestamp(today))]


def run_date_issues(day: date, after: pd.Timestamp) -> tuple[list, list[str]]:
    """The 3-day forecast issues SWPC filed under `day` whose breakdown starts on `day`:
    (issued, breakdown, retrieval details) for each in NCEI's archive, and from SWPC's FTP
    server if NCEI has none issued at or after `after`; and what failed on the way. A file
    is read for its :Issued: line, not its name: SWPC has filed 12:25 and 12:35 issues under
    0030, and late 12:30 issues, as late as 00:20 the next day, under 1230."""
    found, errors = {}, []
    start = pd.Timestamp(day)
    for slot in SLOTS:
        url = forecast_url(f"{day:%Y%m%d}{slot}")
        try:
            got = _get_optional(url)
            if got is not None:
                issued, table = parse_forecast(got[0])
                if table.columns[0] == start:
                    found[issued] = (issued, table, got[1])
        except FAILURES as exc:
            errors.append(f"{url}: {exc!r}")
    if not any(issued >= after for issued in found):
        for slot in SLOTS:
            try:  # only a fallback: a missing file fails like a broken connection
                raw, meta = _get(ftp_url(day, slot), retries=1)
                issued, table = parse_forecast(raw)
            except FAILURES:
                continue
            if table.columns[0] == start:  # the name has no year
                found.setdefault(issued, (issued, table, meta))
    return sorted(found.values(), key=lambda issue: issue[0]), errors


def baseline(run_date: date, spec: dict) -> dict:
    """SWPC's 3-day forecast the run is compared with: the first issue for the run date
    after the cutoff, normally 12:30 UTC, which the agents cannot see. Its column for each
    target date gives B, the maximum of the day's eight three-hourly values. It is read from
    NCEI's archive, normally there by 04:30 UTC the next day, or from SWPC's FTP server if
    NCEI does not have it. Empty until it is archived, and empty if an issue came out
    between the snapshot and the cutoff, since the agents could see that one."""
    timezone = spec["schedule"]["timezone"]
    opened = _at(run_date, spec["schedule"]["snapshot"], timezone)
    cutoff = _at(run_date, spec["schedule"]["cutoff"], timezone)
    source = {
        "report": f"NOAA SWPC 3-Day Forecast, first issue after the cutoff on {run_date}",
        "posted": None,
        "model": MODEL,
        "credit": CREDIT,
    }
    issues, errors = run_date_issues(run_date, opened)
    early = [issued for issued, *_ in issues if opened <= issued <= cutoff]
    late = [issue for issue in issues if issue[0] > cutoff]
    if early:
        note = f"issued at {early[0]:%H:%M} UTC, before the cutoff: the agents could see it"
        return {"source": source | {"note": note}, "forecasts": []}
    if not late:
        if errors:
            raise RuntimeError(f"no issue after the cutoff: {'; '.join(errors)}")
        note = "not archived yet at NCEI or on SWPC's FTP server"
        return {"source": source | {"note": note}, "forecasts": []}
    issued, table, meta = late[0]
    forecasts = []
    for h in spec["horizons"]:
        day = pd.Timestamp(run_date + timedelta(days=h))
        if day in table.columns:
            forecasts.append(
                {"target_date": day.date().isoformat(), "point": round(float(table[day].max()), 2)}
            )
    source |= {
        "report": f"NOAA SWPC 3-Day Forecast issued {issued:%Y-%m-%d %H:%M} UTC",
        "posted": issued.tz_localize(UTC).isoformat(),
        "url": meta["url"],
        "retrieved_at": meta["retrieved_at"],
        "sha256": meta["sha256"],
    }
    return {"source": source, "forecasts": forecasts}
