"""US Saturday hog slaughter: where the numbers for this target come from.

The pipeline imports this file on the host, outside every episode. No credentials: USDA's
My Market News file repository is public and keeps every issue of the two reports used.

- Report 3658, Actual Slaughter Under Federal Inspection: weekly, Final, posted on the
  Thursday 12 days after the week's Saturday. The target.
- Report 3208, Daily Livestock and Poultry Slaughter (Daily Livestock Slaughter before
  June 2025): estimates, posted each business day early in the afternoon. The Friday
  issue's Saturday estimate is B; each week's Monday-Thursday estimate is a covariate.

The numbers read from each issue are kept on the host with the time the issue was posted,
in ~/.cache/agentic-forecasting/us_saturday_hog_slaughter/, so later calls download only
new issues and retry the ones that failed. The first call downloads about 450 PDFs of
250 kB, about 10 minutes.

    snapshot(run_date, spec)  X_d: weekly rows dated on Saturdays, as published before the start
    truth(today, spec)        every Saturday's count as first published
    baseline(run_date, spec)  USDA's estimate for the Saturday after the run date
"""

from __future__ import annotations

import csv
import os
import re
import sys
import time
import zlib
from datetime import date, datetime, timedelta
from datetime import time as clock
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

ZONE = ZoneInfo("America/New_York")
CENTRAL = ZoneInfo("America/Chicago")  # the repository lists posting times in Central time
USER_AGENT = "agentic-forecasting/1.0"
REPOSITORY = "https://mymarketnews.ams.usda.gov/filerepo/"
FINAL, DAILY = "3658", "3208"
PAUSE = 1.0  # seconds between downloads
LATE = 70  # days: an issue can be posted this long after its report date (65 seen)
MAX_FAILURES = 5  # failed downloads in a row that end a report's reading for the call
MAX_TRIES = 3  # reads of an issue that yield no figure before it is left alone
CACHE_VERSION = "v1"  # in the cache file's name; raise it when a parser changes
ROW = re.compile(
    r"(\d\d-\d\d-\d{4} \d\d:\d\d [ap]m)\s*</td>\s*<td[^>]*>\s*(\d{4}-\d\d-\d\d)\s*</td>\s*"
    r"<td[^>]*>\s*(\w+)\s*</td>\s*<td[^>]*>(?:(?!</tr>).)*?href=\"([^\"]+\.pdf)\"",
    re.S,
)
NUMBER = r"([\d,]+|-)(?: R)?"  # a dash is zero; R marks a revised figure
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
FIELDS = ("report", "report_date", "path", "posted", "kind", "saturday", "value", "tries")


def _now():
    return datetime.now(ZONE)


def _warn(message):
    print(f"warning: us_saturday_hog_slaughter: {message}", file=sys.stderr)


def _cache_file():
    root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return root / "agentic-forecasting" / "us_saturday_hog_slaughter" / f"issues.{CACHE_VERSION}.csv"


def _fetch(url, accept="*/*", attempts=4, timeout=60):
    """Bounded retries, verified system TLS, and errors without URLs. The repository stalls
    on requests without an Accept header."""
    for attempt in range(attempts):
        last = attempt == attempts - 1
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or last:
                raise RuntimeError(
                    f"Data provider returned HTTP {exc.code}; check access."
                ) from None
            delay = exc.headers.get("Retry-After", "")
            time.sleep(min(60, max(2**attempt, int(delay) if delay.isdigit() else 0)))
        except (OSError, HTTPException):  # also connections dropped while reading the body
            if last:
                raise RuntimeError("Data provider network/TLS request failed.") from None
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def _issues(report, since):
    """Final issues of a report dated on or after `since`, as dicts with the report date,
    the PDF path and the posting time (New York). The listing runs newest first."""
    found, seen = [], set()
    for page in range(80):
        query = urlencode(
            {
                "field_slug_id_value": report,
                "name": "",
                "field_slug_title_value": "",
                "field_published_date_value": "",
                "field_report_date_end_value": "",
                "field_api_market_types_target_id": "All",
                "page": page,
            }
        )
        text = _fetch(REPOSITORY + "reports?" + query, "text/html").decode("utf-8", "replace")
        time.sleep(PAUSE)
        rows = [row for row in ROW.findall(text) if row[3] not in seen]
        if not rows:
            if page == 0:
                _warn(f"the listing of report {report} shows no issues; has its page changed?")
            break
        for posted, day, status, path in rows:
            seen.add(path)
            stamp = datetime.strptime(posted, "%m-%d-%Y %I:%M %p").replace(tzinfo=CENTRAL)
            if status == "Final" and date.fromisoformat(day) >= since:
                found.append(
                    {"report_date": date.fromisoformat(day), "path": path,
                     "posted": stamp.astimezone(ZONE)}
                )
        if max(date.fromisoformat(row[1]) for row in rows) < since:
            break
    return found


def _pdf_text(blob):
    """The literal text of a PDF: inflate each content stream and join its string operands.
    USDA's market news PDFs write their text this way."""
    escapes = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f"}

    def unescape(match):
        code = match.group(1)
        return chr(int(code, 8)) if code[0].isdigit() else escapes.get(code, code)

    parts = []
    for stream in re.finditer(rb"stream\r?\n(.*?)endstream", blob, re.S):
        try:
            data = zlib.decompress(stream.group(1))
        except zlib.error:
            continue
        for literal in re.finditer(rb"\((?:\\.|[^\\()])*\)", data, re.S):
            text = literal.group(0)[1:-1].decode("latin-1")
            parts.append(re.sub(r"\\([0-7]{1,3}|.)", unescape, text, flags=re.S))
    return re.sub(r"\s+", " ", " ".join(parts))


def _count(token):
    return 0 if token == "-" else int(token.replace(",", ""))


def _final_saturday(text):
    """(Saturday, hogs) from a Final issue of report 3658, or None. Its daily rows read
    'Saturday, MM/DD/YYYY cattle calves hogs sheep goats bison', with a dash for zero."""
    if "- Final" not in text:
        return None
    m = re.search(rf"Saturday, (\d\d)/(\d\d)/(\d{{4}}) {NUMBER} {NUMBER} {NUMBER}", text)
    if not m:
        return None
    return date(int(m[3]), int(m[1]), int(m[2])), _count(m[6])


def _daily_block(text, day):
    """(day's hogs, week-to-date hogs) from the block of a report 3208 issue headed by `day`,
    e.g. 'Sat Nov 7, 2026 Week Ago Year Ago WTD ... Hogs 212,000 190,000 225,669 2,664,000',
    or None. The search stays within that block."""
    head = f"{WEEKDAYS[day.weekday()]} {MONTHS[day.month - 1]} {day.day}, {day.year} Week Ago"
    start = text.find(head)
    if start < 0:
        return None
    following = text.find(" Week Ago ", start + len(head))
    block = text[start: following if following > 0 else len(text)]
    m = re.search(rf"Hogs {NUMBER} {NUMBER} {NUMBER} {NUMBER}", block)
    if not m:
        return None
    return _count(m[1]), _count(m[4])


def _load():
    path = _cache_file()
    if not path.exists():
        return []
    try:
        with open(path, newline="") as fh:
            rows = list(csv.DictReader(fh))
        for row in rows:
            row["report_date"] = date.fromisoformat(row["report_date"])
            row["posted"] = datetime.fromisoformat(row["posted"])
            row["saturday"] = date.fromisoformat(row["saturday"]) if row["saturday"] else None
            row["value"] = int(row["value"]) if row["value"] else None
            row["tries"] = int(row.get("tries") or 0)
        return rows
    except (OSError, ValueError, KeyError, csv.Error) as exc:
        _warn(f"the cache could not be read ({exc!r}); it is set aside and rebuilt")
        path.replace(path.with_suffix(".damaged"))
        return []


def _save(rows):
    path = _cache_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        with open(temporary, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    row | {"report_date": row["report_date"].isoformat(),
                           "posted": row["posted"].isoformat(),
                           "saturday": row["saturday"].isoformat() if row["saturday"] else "",
                           "value": "" if row["value"] is None else row["value"]}
                )
        temporary.replace(path)
    except OSError as exc:
        _warn(f"the cache could not be written ({exc!r}); this call's downloads are not kept")


def _since(rows, report, start):
    """How far back to list: the whole history the first time; then the last LATE days, in
    case an issue is posted late, or back to the oldest issue still to be read."""
    earliest = start - timedelta(days=7)
    mine = [row for row in rows if row["report"] == report]
    if not mine:
        return earliest
    since = max(row["report_date"] for row in mine) - timedelta(days=LATE)
    pending = [row["report_date"] for row in mine if _retry(row)]
    if pending:
        since = min(since, min(pending))
    return max(earliest, since)


def _retry(row):
    return row["value"] is None and row["tries"] < MAX_TRIES


def _saturday_of(day):
    return day + timedelta(days=5 - day.weekday())


def _read(report, issue, kind):
    """The cache row for one issue. A download that fails or holds no figure leaves the
    value empty, so the issue is read again on a later call."""
    row = _pending(report, issue, kind)
    saturday = row["saturday"]
    blob = _fetch(REPOSITORY + issue["path"].lstrip("/"), "application/pdf, */*")
    time.sleep(PAUSE)
    if not blob.startswith(b"%PDF-"):
        raise RuntimeError("The repository returned something other than a PDF.")
    text = _pdf_text(blob)
    if kind == "final":
        found = _final_saturday(text)
        if found:
            row["saturday"], row["value"] = found
    else:
        block = _daily_block(text, saturday if kind == "saturday" else issue["report_date"])
        if block:
            row["value"] = block[0] if kind == "saturday" else block[1]
    if row["value"] is None:
        _warn(f"{issue['path']} holds no {kind} figure")
    return row


def _weekday_issue_day(saturday):
    """The issue whose week-to-date figure covers Monday to Thursday: Thursday's, or the day
    before when that Thursday is a federal holiday (Thanksgiving)."""
    thursday = saturday - timedelta(days=2)
    if len(USFederalHolidayCalendar().holidays(thursday, thursday)):
        return thursday - timedelta(days=1)
    return thursday


def _wanted(report, issues):
    """(issue, kind) pairs to read: every Final issue of report 3658; of report 3208, each
    Friday issue posted that Friday, and each week's Monday-Thursday issue."""
    if report == FINAL:
        return [(issue, "final") for issue in issues]
    wanted = []
    for issue in issues:
        day = issue["report_date"]
        if day.weekday() == 4 and issue["posted"].date() == day:
            wanted.append((issue, "saturday"))
        elif day.weekday() <= 3 and day == _weekday_issue_day(_saturday_of(day)):
            wanted.append((issue, "weekday"))
    return wanted


def _pending(report, issue, kind):
    """A cache row for an issue still to be read: it keeps the issue in the next listings."""
    return {"report": report, "report_date": issue["report_date"], "path": issue["path"],
            "posted": issue["posted"], "kind": kind,
            "saturday": None if kind == "final" else _saturday_of(issue["report_date"]),
            "value": None, "tries": 0}


def _update(start, daily):
    """Add the issues not yet read to the cache, newest first, then try again those that
    failed or held no figure (up to MAX_TRIES reads). When the repository does not answer,
    the cache is used as it stands. After MAX_FAILURES failed downloads in a row, a report's
    remaining issues wait for the next call."""
    rows = _load()
    by_path = {row["path"]: row for row in rows}
    for report in (FINAL, DAILY) if daily else (FINAL,):
        since = _since(rows, report, start)
        try:
            issues = _issues(report, since)
        except RuntimeError as exc:
            _warn(f"the listing of report {report} could not be read ({exc}); using the cache")
            continue
        listed = {issue["path"] for issue in issues}
        for path, row in list(by_path.items()):  # pending issues the listing no longer shows
            if (row["report"] == report and row["value"] is None
                    and row["report_date"] >= since and path not in listed):
                del by_path[path]
        wanted = sorted(_wanted(report, issues), key=lambda pair: pair[0]["report_date"], reverse=True)
        fresh = [pair for pair in wanted if pair[0]["path"] not in by_path]
        again = [pair for pair in wanted
                 if pair[0]["path"] in by_path and _retry(by_path[pair[0]["path"]])]
        failures = 0
        for issue, kind in fresh + again:
            tries = by_path[issue["path"]]["tries"] if issue["path"] in by_path else 0
            if failures >= MAX_FAILURES:
                by_path.setdefault(issue["path"], _pending(report, issue, kind))
                continue
            try:
                row = _read(report, issue, kind)
                failures = 0
            except RuntimeError as exc:
                failures += 1
                _warn(f"{issue['path']} could not be read ({exc}); trying it again later")
                row = _pending(report, issue, kind)
            row["tries"] = tries + 1 if row["value"] is None else 0
            by_path[issue["path"]] = row
        if failures >= MAX_FAILURES:
            _warn(f"{MAX_FAILURES} downloads of report {report} failed in a row; the rest wait")
    rows = list(by_path.values())
    _save(rows)
    if not any(row["report"] == FINAL and row["value"] is not None for row in rows):
        raise RuntimeError("No final Saturday count could be read from USDA.")
    return rows


def _series(rows, kind, start, now):
    """Saturday -> value posted by `now`: the first posted for a final count, else the
    newest issue's."""
    chosen = {}
    for row in sorted(rows, key=lambda r: (r["report_date"], r["posted"])):
        if (row["kind"] == kind and row["value"] is not None and row["posted"] <= now
                and row["saturday"] and row["saturday"] >= start):
            if kind == "final":
                chosen.setdefault(row["saturday"], row["value"])
            else:
                chosen[row["saturday"]] = row["value"]
    series = pd.Series(chosen, dtype="float64").sort_index()
    series.index = pd.DatetimeIndex(series.index, name="timestamp")
    return series


def _saturdays(start, end):
    first = start + timedelta(days=(5 - start.weekday()) % 7)
    return pd.date_range(first, end, freq="7D", name="timestamp")


def _cutoff(run_date, spec):
    if spec["schedule"]["timezone"] != "America/New_York":
        raise ValueError("This target requires America/New_York calendar dates.")
    return datetime.combine(run_date, clock.fromisoformat(spec["schedule"]["cutoff"]), ZONE)


def _guard(run_date, spec):
    now = _now()
    if run_date != now.date() or now >= _cutoff(run_date, spec):
        raise RuntimeError("Snapshots require today and must finish before the cutoff.")
    return now


NOTES = {
    "weekday_estimate": "USDA's Monday-Thursday estimate of the week (report 3208), known at the start",
    "holidays": "US federal holidays from Monday to Friday of the week, known ahead",
    "usda_estimate": "USDA's Friday estimate for each past Saturday (report 3208); the target "
    "week carries the week before's (expert)",
}


def snapshot(run_date, spec):
    """X_d as published right now: one row per Saturday from history_start to the target.
    Only the target is required: a covariate the window lacks is left out, with a warning,
    and the run goes on."""
    as_of = _guard(run_date, spec)
    start = date.fromisoformat(spec["x"]["history_start"])
    rows = _update(start, daily=True)
    now = _guard(run_date, spec)  # Do not archive a slow collection that crossed the cutoff.
    index = _saturdays(start, run_date + timedelta(days=max(spec["horizons"])))
    final = _series(rows, "final", start, now)
    if final.empty:
        raise RuntimeError("No final Saturday count is known yet.")
    last_observed = final.index.max()
    if last_observed < pd.Timestamp(run_date - timedelta(days=13)):
        _warn(f"the newest final count is for {last_observed.date()}; X_d ends there")
    target = final.reindex(index)
    past = index <= last_observed
    target[past] = target[past].interpolate(limit_area="inside")
    holidays = USFederalHolidayCalendar().holidays(index[0] - timedelta(days=5), index[-1])
    columns = {
        "target": target,
        "weekday_estimate": _series(rows, "weekday", start, now).reindex(index),
        "holidays": pd.Series([sum(1 for h in holidays if 0 < (s - h).days <= 5) for s in index],
                              index=index, dtype="float64"),
        # The target's own estimate comes out after the cutoff: carry the week before's.
        "usda_estimate": _series(rows, "saturday", start, now).reindex(index).ffill(),
    }
    series = pd.DataFrame(columns, index=index)
    window = ~past
    for name in ("weekday_estimate", "usda_estimate"):
        if series.loc[window, name].isna().any():
            _warn(f"{name} is missing for the forecast window; X_d goes without it")
            series = series.drop(columns=name)
        else:
            series[name] = series[name].ffill().bfill()  # gaps in the history only
    known = [row for row in rows if row["posted"] <= now and row["value"] is not None]
    raw = {
        "final_counts.csv": pd.DataFrame([r for r in known if r["kind"] == "final"], columns=FIELDS),
        "daily_estimates.csv": pd.DataFrame([r for r in known if r["kind"] != "final"], columns=FIELDS),
    }
    return {
        "series": series,
        "last_observed": last_observed.date(),
        "raw": raw,
        "sources": {
            "final_counts": REPOSITORY + "reports?field_slug_id_value=" + FINAL,
            "daily_estimates": REPOSITORY + "reports?field_slug_id_value=" + DAILY,
            "as_of": as_of.isoformat(),
        },
        "covariate_notes": {name: note for name, note in NOTES.items() if name in series.columns},
    }


def truth(today, spec):
    if today > _now().date():
        raise RuntimeError("Cannot settle a future calendar day.")
    start = date.fromisoformat(spec["x"]["history_start"])
    rows = _update(start, daily=False)
    return _series(rows, "final", start, _now()).rename("target")


def baseline(run_date, spec):
    """USDA's estimate for the Saturday after the run date, from the Friday issue of report
    3208 posted that Friday after the cutoff. Read the day after the run date."""
    source = {
        "name": "USDA AMS report 3208, Friday issue: estimated Saturday hog slaughter",
        "url": REPOSITORY + "reports?field_slug_id_value=" + DAILY,
    }
    if _now().date() <= run_date:
        return {"source": source, "forecasts": []}
    saturday = run_date + timedelta(days=1)
    cutoff = _cutoff(run_date, spec)
    rows = _update(date.fromisoformat(spec["x"]["history_start"]), daily=True)
    issues = sorted(
        (row for row in rows
         if row["kind"] == "saturday" and row["report_date"] == run_date
         and row["saturday"] == saturday and row["value"] is not None
         and row["posted"].date() == run_date and row["posted"] > cutoff),
        key=lambda row: row["posted"],
    )
    if not issues:
        return {"source": source, "forecasts": []}
    first = issues[0]
    return {
        "source": source | {"posted": first["posted"].isoformat(), "path": first["path"]},
        "forecasts": [{"target_date": saturday.isoformat(), "point": float(first["value"])}],
    }
