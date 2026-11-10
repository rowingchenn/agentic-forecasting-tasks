"""GDELT daily news articles about AI: where the numbers for this target come from.

The agentic-forecasting pipeline imports this file on the host, outside every episode; ALE
uploads tests/ only to verify, after the agent has finished. It needs only the standard
library and pandas. The pipeline passes `spec`, [metadata.agentic-forecasting] of task.toml
with the AF_* schedule merged in, and turns the results into archived files.

    snapshot(run_date, spec)  X_d: the daily count through the day before the run date
    truth(today, spec)        each day's count, from every file published so far
    baseline(run_date, spec)  none: no public forecast of this count exists

The count for a UTC date comes from GDELT 1.0's daily event file for that date, which GDELT
posts at about 07:00 UTC the next day and does not change afterwards. No credentials are
needed. A file is about 7 MB; its counts are kept on the host once read, so a morning reads
one new file, and a cold start reads one file per day since history_start (657 files and
4.5 GB from 2025-01-01, 12 minutes on 2026-11-08). The data are GDELT's, free to use with a
citation of the GDELT Project and a link to https://www.gdeltproject.org/.
"""

from __future__ import annotations

import csv
import http.client
import io
import os
import re
import string
import sys
import time
import urllib.error
import urllib.request
import zipfile
import zlib
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

TARGET = "gdelt_ai_news_volume"
FILES = "https://data.gdeltproject.org/events/{day:%Y%m%d}.export.CSV.zip"
INDEX = "https://data.gdeltproject.org/events/index.html"
CITATION = "The GDELT Project, https://www.gdeltproject.org/"
USER_AGENT = "agentic-forecasting/1.0"
CACHE = (
    Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "agentic-forecasting" / TARGET
)
CACHE_VERSION = "v1"  # in the cache file's name; raise it when the rule or the reader changes
FIELDS = 58  # tab-separated fields of a GDELT 1.0 event row; SOURCEURL is the last
GAP_DAYS = 14  # a day with no file this long after it ended is recorded as having none
MAX_FAILURES = 3  # failed connections in a row that end a call's reading
TIMEOUT = 60  # seconds
REQUEST_PAUSE = 1.0  # seconds between requests
RATE_LIMIT_PAUSE = 30  # seconds to wait after a 429
READ = "read"
NO_FILE = "no file"
COUNTS = ["ai_urls", "distinct_urls", "rows", "skipped_rows", "bytes"]
COLUMNS = ["date", "status", *COUNTS, "last_modified", "read_at"]
ASCII_LOWER = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)
SEPARATORS = re.compile(r"[^a-z0-9]+")
FAILURES = (
    OSError,  # with urllib's URLError and HTTPError, timeouts and refused connections
    http.client.HTTPException,
    ValueError,
    zipfile.BadZipFile,
    zlib.error,
    EOFError,  # these three: a damaged or truncated file
    RuntimeError,  # an encrypted zip, or one compressed in a way zipfile cannot read
)
CONNECTION_FAILURES = (OSError, http.client.HTTPException)  # the rest of FAILURES: a bad file
NOTES = {
    "target": "number of distinct article URLs about AI in GDELT 1.0's daily event file for "
    "the UTC date: SOURCEURL values whose path has the word 'ai', or 'artificial' followed by "
    "'intelligence'. Empty from the run date on, on a day GDELT posted no file, and on a day "
    "whose file could not be read",
    "dow": "UTC weekday, Monday=0, known for all dates",
    "holiday": "1 on US federal holidays as observed, else 0, known for all dates",
}

_last_request = 0.0


def _now() -> datetime:
    return datetime.now(UTC)


def _warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def _get(url: str, retries: int = 2) -> tuple[bytes, dict]:
    """The body at url, with its Last-Modified time and when it was read. A 429 is retried
    after RATE_LIMIT_PAUSE seconds, and a server error, timeout or dropped connection after a
    short wait, up to `retries` times; any other HTTP error, such as the 404 of a file not
    posted yet, is raised at once."""
    global _last_request
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(retries + 1):
        time.sleep(max(0.0, _last_request + REQUEST_PAUSE - time.monotonic()))
        _last_request = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                body = response.read()
                modified = response.headers.get("Last-Modified") or ""
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
        return body, {"last_modified": modified, "read_at": _now().isoformat(timespec="seconds")}
    raise AssertionError("unreachable")


# ------------------------------------------------------------------------------ the URL rule


def url_path(url: str) -> str:
    """The URL with the letters A-Z in lower case, cut at its first ? or #, without the scheme
    and host: what follows them from the first /, or nothing if there is no / after them."""
    lower = url.translate(ASCII_LOWER)
    cuts = [i for i in (lower.find("?"), lower.find("#")) if i >= 0]
    lower = lower[: min(cuts)] if cuts else lower
    if "://" in lower:
        lower = lower.split("://", 1)[1]
    slash = lower.find("/")
    return lower[slash:] if slash >= 0 else ""


def about_ai(url: str) -> bool:
    """Whether a SOURCEURL counts: its path, split at every character other than a-z and 0-9,
    has the word "ai", or the word "artificial" directly followed by "intelligence"."""
    words = SEPARATORS.split(url_path(url))
    return "ai" in words or any(
        first == "artificial" and second == "intelligence"
        for first, second in zip(words, words[1:], strict=False)
    )


def count_file(body: bytes) -> dict:
    """The counts of one daily file: rows, distinct SOURCEURL values (the last of a row's 58
    tab-separated fields) and those about AI. Lines without 58 fields are skipped. A zip that
    does not hold exactly one file, or a file with no event row, raises ValueError."""
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        names = archive.namelist()
        if len(names) != 1:
            raise ValueError(f"the zip holds {names}, not one file")
        urls: set[str] = set()
        rows = skipped = 0
        with archive.open(names[0]) as member:
            text = io.TextIOWrapper(member, encoding="utf-8", errors="replace", newline="\n")
            for line in text:
                fields = line.rstrip("\n").split("\t")
                if len(fields) != FIELDS:
                    skipped += 1
                    continue
                rows += 1
                urls.add(fields[-1])
    if not rows:
        raise ValueError(f"{names[0]} has no row of {FIELDS} fields")
    return {
        "ai_urls": sum(about_ai(url) for url in urls),
        "distinct_urls": len(urls),
        "rows": rows,
        "skipped_rows": skipped,
    }


# ------------------------------------------------------------------- the files and the cache


def _cache_file() -> Path:
    return CACHE / f"daily_counts.{CACHE_VERSION}.csv"


def _stored() -> dict[str, dict]:
    """The days kept on the host, by ISO date. A damaged file is read again from GDELT."""
    path = _cache_file()
    if not path.is_file():
        return {}
    kept: dict[str, dict] = {}
    try:
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != COLUMNS:
                raise ValueError(f"columns {reader.fieldnames}")
            for row in reader:
                if None in row or None in row.values():
                    raise ValueError(f"a row with the wrong number of fields: {row}")
                date.fromisoformat(row["date"])
                if row["status"] == READ:
                    if any(int(row[column]) < 0 for column in COUNTS):
                        raise ValueError(f"a negative count on {row['date']}")
                elif row["status"] != NO_FILE:
                    raise ValueError(f"status {row['status']!r} on {row['date']}")
                kept.setdefault(row["date"], row)
    except (OSError, ValueError, TypeError, csv.Error) as exc:
        _warn(f"{path} is damaged, reading GDELT's files again: {exc!r}")
        return {}
    return kept


def _store(kept: dict[str, dict]) -> None:
    path = _cache_file()
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        with path.with_suffix(".tmp").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(kept[day] for day in sorted(kept))
        path.with_suffix(".tmp").replace(path)
    except OSError as exc:  # a full disk must not cost the run; the files stay online
        _warn(f"counts not kept on the host: {exc!r}")


def read_day(day: date) -> dict | None:
    """One day's counts from GDELT's file for it, or None when there is no file (404)."""
    try:
        body, meta = _get(FILES.format(day=day))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    counts = count_file(body) | {"bytes": len(body)}
    return {"date": day.isoformat(), "status": READ} | {k: str(v) for k, v in counts.items()} | meta


def daily_counts(start: date, end: date, now: datetime) -> tuple[pd.DataFrame, dict]:
    """Each day's counts from start through end, by date. Days kept on the host are not read
    again; the others are read newest first. A day without a file, or whose read fails, is
    left out and asked for again on the next call, except that a day with still no file
    GAP_DAYS after it ended, when a later day's file has been read, is recorded as having
    none and not asked for again. MAX_FAILURES failed connections in a row end the call's
    reading; a damaged file only skips its own day."""
    kept = _stored()
    read = failures = 0
    missing = []
    for day in (end - timedelta(days=n) for n in range((end - start).days + 1)):
        if day.isoformat() in kept:
            continue
        try:
            row = read_day(day)
        except FAILURES as exc:
            _warn(f"GDELT's file for {day} not read: {exc!r}")
            if not isinstance(exc, CONNECTION_FAILURES):
                failures = 0  # the host answered; only this file is bad
                continue
            failures += 1
            if failures == MAX_FAILURES:
                _warn(f"{failures} failed connections in a row; the rest is read on the next call")
                break
            continue
        failures = 0
        if row is None:
            missing.append(day)
            continue
        kept[row["date"]] = row
        read += 1
        _store(kept)  # after each file, so that an interrupted cold start keeps its work
    newest = max((day for day, row in kept.items() if row["status"] == READ), default="")
    gaps = [
        day for day in missing if (now.date() - day).days > GAP_DAYS and day.isoformat() < newest
    ]
    for day in gaps:
        nothing = dict.fromkeys([*COUNTS, "last_modified"], "")
        kept[day.isoformat()] = nothing | {
            "date": day.isoformat(),
            "status": NO_FILE,
            "read_at": now.isoformat(timespec="seconds"),
        }
    if gaps:
        _store(kept)
    rows = [kept[day] for day in sorted(kept) if start.isoformat() <= day <= end.isoformat()]
    table = pd.DataFrame(rows, columns=COLUMNS)
    for column in COUNTS:  # integers, empty on a day without a file
        table[column] = pd.to_numeric(table[column], errors="coerce").astype("Int64")
    table.index = pd.DatetimeIndex(pd.to_datetime(table.pop("date")), name="date")
    return table, {
        "files": FILES,
        "index": INDEX,
        "citation": CITATION,
        "files_read_now": read,
        "days_without_a_file": int((table["status"] == NO_FILE).sum()),
        "retrieved_at": now.isoformat(timespec="seconds"),
    }


# ------------------------------------------------------------------ what the pipeline calls


def snapshot(run_date: date, spec: dict) -> dict:
    """X_d as published right now: one row per UTC day from history_start to the last target
    date, `target` through the day before the run date, whose file GDELT posts at about 07:00
    UTC on the run date, and empty after it; on a day that file is late, through the day
    before. A day without a file, or whose file could not be read, is left empty."""
    if spec["schedule"]["timezone"] != "UTC":
        raise ValueError("this target counts UTC days")
    start = date.fromisoformat(spec["x"]["history_start"])
    latest = run_date - timedelta(days=1)
    table, sources = daily_counts(start, latest, _now())
    counts = table.loc[table["status"] == READ, "ai_urls"]
    if pd.Timestamp(latest) not in counts.index:
        if pd.Timestamp(latest - timedelta(days=1)) not in counts.index:
            raise RuntimeError(
                f"GDELT's files for {latest - timedelta(days=1)} and {latest} are not read; "
                f"they are normally posted at 07:00 UTC the next day: {FILES.format(day=latest)}"
            )
        _warn(f"GDELT's file for {latest} is not read; X_d ends a day earlier")
        latest -= timedelta(days=1)
    index = pd.date_range(start, run_date + timedelta(days=max(spec["horizons"])), name="timestamp")
    holidays = USFederalHolidayCalendar().holidays(index.min(), index.max())
    series = pd.DataFrame(
        {
            "target": counts.reindex(index).astype(float),
            "dow": index.dayofweek,
            "holiday": index.isin(holidays).astype(int),
        },
        index=index,
    )
    return {
        "series": series,
        "last_observed": latest,
        "raw": {"gdelt_daily_counts.csv": table.reset_index()},
        "sources": {"gdelt_events": sources},
        "covariate_notes": NOTES,
    }


def truth(today: date, spec: dict) -> pd.Series:
    """Every day's count from history_start through the day before `today`, from the files
    read so far; a day without a file, or whose file could not be read, is left out. If
    nothing can be read, nothing new is recorded today and a warning is printed, so that the
    day's run goes on."""
    start = date.fromisoformat(spec["x"]["history_start"])
    try:
        table, _ = daily_counts(start, today - timedelta(days=1), _now())
    except FAILURES as exc:
        _warn(f"GDELT's files not read, no truth recorded today: {exc!r}")
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    return table.loc[table["status"] == READ, "ai_urls"].astype(float)


def baseline(run_date: date, spec: dict) -> dict:
    """No public forecast of this count exists; the engine scores against its weekly
    seasonal naive instead."""
    return {"source": {"name": "none: no public forecast of this count exists"}, "forecasts": []}
