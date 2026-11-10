"""US initial jobless claims: where the numbers for this target come from.

The agentic-forecasting pipeline imports this file on the host, outside every episode; ALE
uploads tests/ only to verify, after the agent has finished. It needs only the standard
library and pandas. The pipeline passes `spec`, [metadata.agentic-forecasting] of task.toml
with the AF_* schedule merged in, and turns the results into archived files.

    snapshot(run_date, spec)  X_d: weekly first prints, DOL's seasonal factors, the calendar,
                              and the economists' consensus of earlier weeks
    truth(today, spec)        every week's first print published so far
    baseline(run_date, spec)  the economists' consensus for the first target week, as first
                              seen after the cutoff

The series is weekly: one row per claims week (Sunday to Saturday), dated on its Saturday.
No credentials are needed. A release and a week's consensus are read once and kept on the
host, since neither changes; a cold start reads about 190 releases of 0.5 MB each, a few
minutes, and Nasdaq's calendar for as many weeks, one request each, about 15 minutes.
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
import xml.etree.ElementTree as ElementTree
import zlib
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

TARGET = "us_initial_jobless_claims"
PRESS = "https://oui.doleta.gov/press/{year}/{day:%m%d%y}.pdf"  # DOL's archive of the releases
RELEASE_PAGE = "https://www.dol.gov/ui/data.pdf"  # the current release; refuses this client
DOL_TABLE = "https://oui.doleta.gov/unemploy/wkclaims/report.asp"  # asked by form
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=ICSA"
NASDAQ = "https://api.nasdaq.com/api/calendar/economicevents?date={day}"
NASDAQ_PAGE = "https://www.nasdaq.com/market-activity/economic-calendar"
NASDAQ_EVENT = "Initial Jobless Claims"
SOURCE_RELEASE = "DOL news release"
SOURCE_FRED = "FRED ICSA"
GAP_DAYS = 21  # a week with no release this long after it ends is not asked about again
MAX_FAILURES = 2  # failed requests that end a day's reading of DOL's archive
EASTERN = ZoneInfo("America/New_York")
USER_AGENT = "agentic-forecasting/1.0"
CACHE = (
    Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "agentic-forecasting" / TARGET
)
CACHE_VERSION = "v1"  # in the cache files' names; raise it when a parser changes
FAILURES = (
    OSError,
    http.client.HTTPException,
    ValueError,
    KeyError,
    TypeError,
    AttributeError,
    ArithmeticError,  # these three: answers in a shape or of a size the code does not expect
    ElementTree.ParseError,
)
TIMEOUT = 30  # seconds; agents have 15 minutes to spare, and a morning makes about 8 requests
REQUEST_PAUSE = 1.0  # seconds between requests
RATE_LIMIT_PAUSE = 10  # seconds to wait after a 429

STREAM = re.compile(rb">>\s*stream\r?\n")
SHOWN = re.compile(
    rb"\[((?:[^\]\\(\[]|\((?:[^()\\]|\\.)*\))*)\]\s*TJ|\(((?:[^()\\]|\\.)*)\)\s*Tj", re.S
)
LITERAL = re.compile(rb"\(((?:[^()\\]|\\.)*)\)", re.S)
ESCAPE = re.compile(rb"\\([0-7]{1,3}|.)", re.S)
ESCAPES = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f"}
NOT_TEXT = (b"ObjStm", b"XRef", b"Length1", b"Image", b"Metadata", b"FontFile")
ADVANCE = re.compile(  # on text with all white space removed
    r"Intheweekending([A-Z][a-z]+)\.?(\d{1,2})(?:,?\d{4})?,?theadvancefigureforseasonally"
    r"adjustedinitialclaimswas(\d{1,3}(?:,\d{3})+|\d+)"
)
CALENDAR_NUMBER = re.compile(r"([\d.]+)([KM])")
DOL_COLUMNS = {
    "initial_nsa": ("InitialClaims", "NSA"),
    "seasonal_factor": ("InitialClaims", "SF"),
    "initial_sa": ("InitialClaims", "SA"),
    "initial_sa_4wk": ("InitialClaims", "SA4WK"),
    "continued_nsa": ("ContinuedClaims", "NSA"),
    "continued_sf": ("ContinuedClaims", "SF"),
    "continued_sa": ("ContinuedClaims", "SA"),
    "continued_sa_4wk": ("ContinuedClaims", "SA4WK"),
}

_last_request = 0.0


def _now() -> datetime:
    return datetime.now(UTC)


def _get(url: str, data: bytes | None = None, retries: int = 2) -> tuple[bytes, dict]:
    """The body at url, and where and when it was retrieved; `data` makes it a form post. A 429
    is retried after RATE_LIMIT_PAUSE seconds, and a server error, timeout or dropped connection
    after a short wait, up to `retries` times; any other HTTP error is raised at once."""
    global _last_request
    request = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    for attempt in range(retries + 1):
        time.sleep(max(0.0, _last_request + REQUEST_PAUSE - time.monotonic()))
        _last_request = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
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


def _warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def _at(day: date, clock: str, timezone: str) -> datetime:
    """`clock` ("HH:MM", local) on `day`, in UTC."""
    hour, minute = map(int, clock.split(":"))
    local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZoneInfo(timezone))
    return local.astimezone(UTC)


# ---------------------------------------------------------------- DOL's weekly news release


def _unescape(literal: bytes) -> bytes:
    def replace(match: re.Match) -> bytes:
        code = match.group(1)
        if code[0] in b"01234567":
            return bytes([int(code, 8) & 255])
        return ESCAPES.get(code, code)

    return ESCAPE.sub(replace, literal)


def _shown_text(pdf: bytes) -> str:
    """The text a Word-made PDF shows, in file order. Word writes it as literal strings in
    Flate-compressed content streams, with kerning numbers between the pieces of a word; a
    reader of fonts and page trees is not needed for that."""
    streams = []
    for match in STREAM.finditer(pdf):
        dictionary = pdf[pdf.rfind(b" obj", 0, match.start()) : match.start()]
        if b"FlateDecode" not in dictionary or any(word in dictionary for word in NOT_TEXT):
            continue
        end = pdf.find(b"endstream", match.end())
        try:
            data = zlib.decompressobj().decompress(pdf[match.end() : end])
        except zlib.error:
            continue
        if b"TJ" in data or b"Tj" in data:
            streams.append(data)
    pieces: list[bytes] = []
    for shown in SHOWN.finditer(b" ".join(streams)):
        if shown.group(1) is not None:
            pieces += [_unescape(literal) for literal in LITERAL.findall(shown.group(1))]
        else:
            pieces.append(_unescape(shown.group(2)))
    return b"".join(pieces).decode("latin-1")


def advance_figure(pdf: bytes) -> tuple[str, int, int] | None:
    """The month, day and figure in the release's sentence "In the week ending October 24, the
    advance figure for seasonally adjusted initial claims was 194,000", or None without one."""
    found = ADVANCE.search(re.sub(r"\s+", "", _shown_text(pdf)))
    if not found:
        return None
    return found.group(1), int(found.group(2)), int(found.group(3).replace(",", ""))


def release_days(week: date) -> list[date]:
    """The days DOL publishes a week on, most likely first: the Thursday after it ends, or the
    Wednesday when Thursday is a holiday or a federal office closure (19 of 608 releases)."""
    return [week + timedelta(days=5), week + timedelta(days=4)]


def press_url(day: date) -> str:
    return PRESS.format(year=day.year, day=day)


def read_release(week: date, today: date) -> dict | None:
    """The release of a week from DOL's archive, or None if the archive has no file for it. A
    file that does not give the week's figure raises ValueError, and any HTTP error but a 404
    is raised: neither means that the week has no release."""
    for day in release_days(week):
        if day > today:
            continue
        try:
            body, meta = _get(press_url(day))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue
            raise
        found = advance_figure(body)
        if found is None or found[0][:3] != week.strftime("%b") or found[1] != week.day:
            raise ValueError(f"{press_url(day)} does not give the week ending {week}: {found}")
        return {
            "week": week.isoformat(),
            "value": float(found[2]),
            "release": day.isoformat(),
            "posted": meta.get("last_modified", ""),
            "source": SOURCE_RELEASE,
        }
    return None


def _stored_prints() -> pd.DataFrame:
    """The figures kept so far. A damaged file is read again from DOL's archive."""
    empty = pd.DataFrame(
        {
            "week": pd.Series(dtype=str),
            "value": pd.Series(dtype=float),
            "release": pd.Series(dtype=str),
            "posted": pd.Series(dtype=str),
            "source": pd.Series(dtype=str),
        }
    )
    path = CACHE / f"first_prints.{CACHE_VERSION}.csv"
    if not path.is_file():
        return empty
    try:
        text = {"week": str, "release": str, "posted": str, "source": str}
        stored = pd.read_csv(path, dtype=text)
        if list(stored.columns) != list(empty.columns):
            raise ValueError(f"columns {list(stored.columns)}")
        pd.to_datetime(stored["week"])
        stored["value"] = stored["value"].astype(float)
    except (OSError, ValueError) as exc:
        _warn(f"{path} is damaged, reading DOL's releases again: {exc!r}")
        return empty
    return stored.drop_duplicates("week")


def _store_prints(stored: pd.DataFrame, rows: list[dict]) -> pd.DataFrame:
    """The stored figures with `rows` added, written back to the host. A week already stored
    keeps its row: the first value kept for a week is its first print."""
    added = pd.concat([stored, pd.DataFrame(rows)], ignore_index=True)
    added = added.drop_duplicates("week").sort_values("week")
    path = CACHE / f"first_prints.{CACHE_VERSION}.csv"
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        added.to_csv(path.with_suffix(".tmp"), index=False)
        path.with_suffix(".tmp").replace(path)
    except OSError as exc:  # a full disk must not cost the run; the releases stay online
        _warn(f"first prints not kept on the host: {exc!r}")
    return added


def _prints_table(stored: pd.DataFrame, start: date) -> pd.DataFrame:
    table = stored.assign(week=pd.to_datetime(stored["week"])).set_index("week").sort_index()
    table["value"] = table["value"].astype(float)
    return table[table.index >= pd.Timestamp(start)]


def press_prints(start: date, now: datetime) -> tuple[pd.DataFrame, dict]:
    """Every week's figure from DOL's releases, from `start`, by week; a week with no release
    has an empty value. Releases already read are not asked for again. A week counts as having
    no release when the archive has no file for it, GAP_DAYS after it ended and with a later
    release read from the archive; until then it is asked for every day, so a moved archive or a
    late file writes nothing off. A request that fails or a file that cannot be read leaves its
    week for the next call, and MAX_FAILURES of them in a row end today's reading."""
    today = now.astimezone(EASTERN).date()
    stored = _stored_prints()
    known = set(stored["week"])
    fresh = []
    missing = []
    asked = failures = 0
    for saturday in pd.date_range(start, today, freq="W-SAT"):
        week = saturday.date()
        if week.isoformat() in known or week + timedelta(days=4) > today:
            continue
        asked += 1
        try:
            found = read_release(week, today)
        except FAILURES as exc:
            _warn(f"the release of the week ending {week} not read: {exc!r}")
            failures += 1
            if failures == MAX_FAILURES:
                break
            continue
        failures = 0
        if found:
            fresh.append(found)
        else:
            missing.append(week)
    released = stored["week"][stored["source"] == SOURCE_RELEASE]
    latest = max([row["week"] for row in fresh] + list(released), default="")
    for week in missing:
        if (today - week).days > GAP_DAYS and week.isoformat() < latest:
            nothing = {"value": float("nan"), "release": "", "posted": "", "source": ""}
            fresh.append({"week": week.isoformat()} | nothing)
    if fresh:
        stored = _store_prints(stored, fresh)
    table = _prints_table(stored, start)
    return table, {
        "archive": PRESS,
        "current_release": RELEASE_PAGE,
        "weeks_with_a_release": int((table["source"] == SOURCE_RELEASE).sum()),
        "weeks_asked_today": asked,
        "retrieved_at": now.isoformat(timespec="seconds"),
    }


def fred_values() -> tuple[pd.Series, dict]:
    """FRED's ICSA by week as published now: DOL's newest figure and revised older ones."""
    body, meta = _get(FRED)
    frame = pd.read_csv(io.BytesIO(body))
    if frame.shape[1] != 2:
        raise ValueError(f"FRED's ICSA has columns {list(frame.columns)}")
    values = pd.to_numeric(frame.iloc[:, 1], errors="coerce").astype(float)
    values.index = pd.to_datetime(frame.iloc[:, 0])
    return values.dropna().rename("value"), meta


def first_prints(start: date, now: datetime) -> tuple[pd.DataFrame, dict]:
    """Each week's first published figure, from `start`, by week. DOL's release gives it. FRED's
    ICSA fills in for two kinds of week only. Its newest week, when DOL's archive has no copy of
    the release yet: FRED then holds the same advance figure, and only until the next release
    revises it, so the figure is kept on the host. And weeks with no release, as in a lapse in
    appropriations. An older week that is merely unread, after a failed request, stays empty:
    FRED's value for it is revised, and the pipeline keeps the first value it sees."""
    press, press_meta = press_prints(start, now)
    try:
        fred, fred_meta = fred_values()
    except FAILURES as exc:
        _warn(f"FRED not read: {exc!r}")
        fred, fred_meta = pd.Series(dtype=float, index=pd.DatetimeIndex([])), {"url": FRED}
    if not fred.empty and fred.index.max() >= pd.Timestamp(start):
        newest = fred.index.max()
        if newest not in press.index:
            row = {
                "week": newest.date().isoformat(),
                "value": float(fred[newest]),
                "release": "",
                "posted": "",
                "source": SOURCE_FRED,
            }
            press = _prints_table(_store_prints(_stored_prints(), [row]), start)
    filled = fred.reindex(press.index[press["value"].isna()]).dropna()
    table = press.drop(columns="posted").rename_axis("week")
    table.loc[filled.index, "value"] = filled
    table.loc[filled.index, "source"] = SOURCE_FRED
    return table[table["value"].notna()], {"dol_first_prints": press_meta, "fred": fred_meta}


# ------------------------------------------------------------------------ DOL's weekly table


def _cell(week: ElementTree.Element, group: str, field: str) -> float:
    text = (week.findtext(f"{group}/{field}") or "").replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return float("nan")


def parse_dol_table(body: bytes) -> pd.DataFrame:
    """DOL's weekly national table: claims as revised to date, with a lag of some weeks, and the
    seasonal factors, which DOL publishes ahead of the weeks they apply to."""
    rows = []
    for week in ElementTree.fromstring(body).findall("week"):
        ended = datetime.strptime((week.findtext("weekEnded") or "").strip(), "%m/%d/%Y")
        rows.append(
            {"week": pd.Timestamp(ended)}
            | {name: _cell(week, *path) for name, path in DOL_COLUMNS.items()}
        )
    if not rows:
        raise ValueError("DOL's weekly table has no weeks")
    table = pd.DataFrame(rows).sort_values("week", ignore_index=True)
    if table["seasonal_factor"].isna().all():
        raise ValueError("DOL's weekly table has no seasonal factors")
    return table


def dol_table(first_year: int, last_year: int) -> tuple[pd.DataFrame, dict]:
    form = {"level": "us", "strtdate": first_year, "enddate": last_year, "filetype": "xml"}
    body, meta = _get(DOL_TABLE, data=urllib.parse.urlencode(form).encode())
    return parse_dol_table(body), meta


def seasonal_factors(index: pd.DatetimeIndex, table: pd.DataFrame) -> pd.Series:
    """DOL's seasonal factor of each week. A week DOL has not published one for yet, which can
    happen just before its yearly revision each spring, takes the same week a year earlier."""
    published = table.set_index("week")["seasonal_factor"].dropna()
    year_before = published.copy()
    year_before.index = year_before.index + pd.Timedelta(weeks=52)
    factors = published.reindex(index).fillna(year_before.reindex(index))
    return factors.ffill().bfill().rename("seasonal_factor")


def holidays_per_week(index: pd.DatetimeIndex) -> pd.Series:
    """The number of US federal holidays (as observed) in each week, Sunday to Saturday."""
    days = USFederalHolidayCalendar().holidays(index.min() - pd.Timedelta(days=6), index.max())
    saturdays = days + pd.to_timedelta((5 - days.dayofweek) % 7, unit="D")
    return saturdays.value_counts().reindex(index, fill_value=0).astype(int).rename("holiday")


# ------------------------------------------------------------------- the economists' consensus


def claims_number(text: str | None) -> float | None:
    """A number as the calendars write it, such as 197K or 1,701K; None if there is none."""
    found = CALENDAR_NUMBER.fullmatch((text or "").replace(",", "").strip())
    if not found:
        return None
    return float(found.group(1)) * {"K": 1_000, "M": 1_000_000}[found.group(2)]


def nasdaq_url(day: date) -> str:
    return NASDAQ.format(day=day.isoformat())


def nasdaq_row(week: date) -> tuple[dict | None, dict]:
    """The Nasdaq calendar's row for the release of a week, and the request's metadata. The API
    files an event under the day after it (all 181 releases from 2023-04 to 2026-10), so a
    Thursday release is under Friday's date and a Wednesday one under Thursday's; the release
    day itself is tried last, in case Nasdaq corrects that. The release that ended the 2025
    lapse in appropriations lists the weeks it caught up on as well, oldest first, so the last
    row is the week's own."""
    meta: dict = {}
    for day in (week + timedelta(days=6), week + timedelta(days=5), week + timedelta(days=4)):
        body, meta = _get(nasdaq_url(day))
        rows = [
            row
            for row in (json.loads(body).get("data") or {}).get("rows") or []
            if row.get("eventName") == NASDAQ_EVENT and row.get("country") == "United States"
        ]
        if rows:
            return rows[-1], meta
    return None, meta


def consensus_feed(start: date, last_observed: date, now: datetime) -> tuple[pd.Series, dict]:
    """The consensus of each week from `start` to `last_observed`, by week, from Nasdaq's
    calendar, which keeps the last version before the release. A released week's number never
    changes, so each week is read once and kept on the host; a week whose row has none, as in
    the 2025 lapse in appropriations, is kept empty once it is GAP_DAYS old. A request that
    fails leaves its week for the next call, and MAX_FAILURES of them in a row end today's
    reading, so a morning without an answer uses the weeks read before: a column missing on one
    run morning would leave the -expert TSFMs with no forecast, and the website compares only
    the run days that every TSFM and agent has, so that day would leave every row of its
    leaderboard. Empty if nothing was ever read."""
    path = CACHE / f"consensus.{CACHE_VERSION}.csv"
    stored = pd.Series(dtype=float, index=pd.DatetimeIndex([]), name="consensus")
    if path.is_file():
        try:
            frame = pd.read_csv(path)
            weeks = pd.DatetimeIndex(pd.to_datetime(frame["week"]))
            kept = pd.Series(frame["consensus"].astype(float).to_numpy(), index=weeks)
            stored = kept[~weeks.duplicated()].sort_index().rename("consensus")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            _warn(f"{path} is damaged, reading Nasdaq's calendar again: {exc!r}")
    today = now.astimezone(EASTERN).date()
    fresh: dict[pd.Timestamp, float] = {}
    asked = failures = 0
    for week in pd.date_range(start, last_observed, freq="W-SAT"):
        if week in stored.index:
            continue
        asked += 1
        try:
            row, _ = nasdaq_row(week.date())
        except FAILURES as exc:
            _warn(f"Nasdaq's calendar not read for the week ending {week.date()}: {exc!r}")
            failures += 1
            if failures == MAX_FAILURES:
                break
            continue
        failures = 0
        value = claims_number((row or {}).get("consensus"))
        if value is not None or (today - week.date()).days > GAP_DAYS:
            fresh[week] = float("nan") if value is None else value
    if fresh:
        stored = pd.concat([stored, pd.Series(fresh, dtype=float)]).sort_index()
        try:
            CACHE.mkdir(parents=True, exist_ok=True)
            table = stored.rename("consensus").rename_axis("week").reset_index()
            table.to_csv(path.with_suffix(".tmp"), index=False)
            path.with_suffix(".tmp").replace(path)
        except OSError as exc:
            _warn(f"the consensus not kept on the host: {exc!r}")
    feed = stored.dropna().rename("consensus")
    return feed, {
        "calendar": NASDAQ,
        "weeks_with_a_consensus": int(feed.size),
        "weeks_asked_today": asked,
        "retrieved_at": now.isoformat(timespec="seconds"),
    }


def consensus_as_seen(
    index: pd.DatetimeIndex,
    target: pd.Series,
    feed: pd.Series,
    last_observed: pd.Timestamp,
) -> pd.Series | None:
    """The consensus as a run could see it: for past weeks, that week's own, or the week before's
    print where there is none; for the forecast window, the previous cycle's, for the newest
    published week, carried forward. The consensus for the first target week comes after the
    cutoff and is B, so it is never used. None if no consensus precedes the run."""
    seen = feed[feed.index <= last_observed]
    if seen.empty:
        return None
    column = seen.reindex(index).astype(float)
    past = index <= last_observed
    column[past] = column[past].fillna(target.shift(1)[past]).fillna(target[past])
    column[~past] = seen.iloc[-1]
    return column.rename("consensus")


# ------------------------------------------------------------------ what the pipeline calls


def snapshot(run_date: date, spec: dict) -> dict:
    """X_d as published right now: one row per week from history_start to the last target date,
    `target` empty after the last week published before the start, covariates known for the
    window. Only the target is required: without DOL's table, or with no consensus ever read,
    X_d goes without that column and the run goes on."""
    start = pd.Timestamp(spec["x"]["history_start"])
    targets = [pd.Timestamp(run_date + timedelta(days=h)) for h in spec["horizons"]]
    if any(t.dayofweek != 5 for t in targets):
        raise ValueError(f"target dates must be Saturdays, the ends of claims weeks: {targets}")
    prints, sources = first_prints(start.date(), _now())
    observed = prints["value"]
    observed = observed[observed.index < pd.Timestamp(run_date)]
    if observed.empty:
        raise RuntimeError("no first print between history_start and the run")
    last_observed = observed.index.max()
    latest = run_date - timedelta(days=(run_date.weekday() - 5) % 7 or 7)
    if last_observed.date() < latest:
        _warn(f"the week ending {latest} is not out; X_d ends on {last_observed.date()}")

    index = pd.date_range(start, max(targets), freq="W-SAT")
    past = index <= last_observed
    target = observed.reindex(index).astype(float).rename("target")
    target[past] = target[past].interpolate(limit_area="inside")
    week = pd.Series(index.isocalendar().week.to_numpy(dtype=int), index=index, name="week")
    columns = [target, week, holidays_per_week(index)]
    notes = {
        "target": "seasonally adjusted initial claims for unemployment insurance in the United "
        "States, in the week ending on the date, as DOL first published them: the advance "
        "figure of its weekly news release. Weeks without a release in DOL's archive carry "
        "FRED's ICSA, which is the advance figure for the newest week and the current, revised "
        "figure for the weeks of a lapse in appropriations",
        "week": "ISO week number of the Saturday that ends the week, 1 to 53",
        "holiday": "number of US federal holidays in the week, as observed",
    }
    raw = {
        "dol_first_prints.csv": prints.loc[start:last_observed].reset_index(),
    }

    try:
        table, sources["dol_table"] = dol_table(start.year, run_date.year + 1)
    except FAILURES as exc:
        _warn(f"DOL's weekly table not read, X_d has no seasonal factors: {exc!r}")
    else:
        columns.append(seasonal_factors(index, table))
        notes["seasonal_factor"] = (
            "DOL's seasonal factor for initial claims in the week, as published ahead of it, in "
            "percent: the adjusted figure is the unadjusted count divided by it. A week DOL has "
            "not published one for carries the same week a year earlier"
        )
        raw["dol_national_weekly.csv"] = table

    feed, sources["consensus"] = consensus_feed(start.date(), last_observed.date(), _now())
    seen = consensus_as_seen(index, target, feed, last_observed)
    if seen is not None:
        columns.append(seen)
        notes["consensus"] = (
            "economists' consensus for the week's first print: for past weeks that week's own, "
            "last version before its release, or the week before's print where there is none; "
            "for the forecast window, the consensus for the newest published week, carried "
            "forward. The consensus for the first target week comes after the cutoff and is not "
            "included"
        )
        kept = feed[(feed.index >= start) & (feed.index <= last_observed)]
        raw["consensus_by_week.csv"] = kept.rename_axis("week").reset_index()

    return {
        "series": pd.concat(columns, axis=1).rename_axis("timestamp"),
        "last_observed": last_observed.date(),
        "raw": raw,
        "sources": sources,
        "covariate_notes": notes,
    }


def truth(today: date, spec: dict) -> pd.Series:
    """Every week's first print published so far. The pipeline keeps the first value it sees for
    a week. If nothing can be read, nothing is recorded today and a warning is printed, so that
    the day's run goes on."""
    start = pd.Timestamp(spec["x"]["history_start"])
    try:
        prints, _ = first_prints(start.date(), _now())
    except FAILURES as exc:
        _warn(f"first prints not read, no truth recorded today: {exc!r}")
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    values = prints["value"].astype(float)
    return values[values.index < pd.Timestamp(today)]


def baseline(run_date: date, spec: dict) -> dict:
    """The economists' consensus for the first target week, as first seen after the cutoff, from
    Nasdaq's calendar. It normally appears between the Friday after the run and the Monday
    before the release, and the pipeline asks every day of the week after the run until there
    is one, keeping the first answer, so a morning without an answer from Nasdaq only delays
    it. Later target weeks have no consensus."""
    schedule = spec["schedule"]
    week = run_date + timedelta(days=spec["horizons"][0])
    source: dict = {
        "report": f"Economists' consensus for the week ending {week.isoformat()}, Nasdaq calendar",
        "posted": None,
        "model": "Consensus of economists' forecasts",
        "page": NASDAQ_PAGE,
    }
    if _now() < _at(run_date, schedule["cutoff"], schedule["timezone"]):
        return {"source": source | {"note": "before the cutoff"}, "forecasts": []}
    point = None
    try:
        row, meta = nasdaq_row(week)
    except FAILURES as exc:
        _warn(f"Nasdaq's calendar not read for the week ending {week}: {exc!r}")
    else:
        source["url"] = meta.get("url")
        if row is not None:
            point = claims_number(row.get("consensus"))
            source["posted"] = meta.get("retrieved_at")
            source["calendar_row"] = {k: v for k, v in row.items() if k != "description"}
    if point is None:
        return {"source": source | {"note": "the consensus is not posted yet"}, "forecasts": []}
    return {"source": source, "forecasts": [{"target_date": week.isoformat(), "point": point}]}
