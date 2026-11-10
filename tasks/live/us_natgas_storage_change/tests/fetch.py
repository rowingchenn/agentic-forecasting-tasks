"""Host-only EIA weekly natural gas storage: X_d, first-published truth, and B, the economists'
consensus for the first target week.

The agentic-forecasting pipeline imports this file on the host, outside every episode, with
the standard library and pandas only.

    snapshot(run_date, spec)  X_d: the weekly change since 2010, the calendar, and the earlier
                              economists' consensus (expert)
    truth(today, spec)        EIA's first print of the newest week, and the weekly history for
                              older weeks the pipeline did not see first-hand
    baseline(run_date, spec)  the consensus for the first target week, as first seen after the
                              cutoff

Sources. EIA, public domain: the newest week from https://ir.eia.gov/ngs/wngsr.json, which
redirects to a signed copy valid until the next release, and the weekly levels as published
now from https://www.eia.gov/dnav/ng/hist/nw2_epg0_swo_r48_bcfw.htm. The consensus from Forex
Factory's weekly calendar feed, https://nfs.faireconomy.media/ff_calendar_thisweek.json (its
robots.txt allows every path). The feed lists the current week only and limits its readers,
so it is read at most once an hour and never retried at once, and what it gave is kept on the
host. No key is needed.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from datetime import time as clock
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd

TARGET = "us_natgas_storage_change"
EIA_WEEK = "https://ir.eia.gov/ngs/wngsr.json"
EIA_HISTORY = "https://www.eia.gov/dnav/ng/hist/nw2_epg0_swo_r48_bcfw.htm"
FEED = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FEED_PAGE = "https://www.forexfactory.com/calendar"
FEED_TITLE = "Natural Gas Storage"
LOWER_48 = "total lower 48 states"
ZONE = ZoneInfo("America/New_York")
USER_AGENT = "agentic-forecasting/1.0"
TRIES = 4  # requests to EIA; the feed gets one, and the next day's call is its retry
TIMEOUT = 30  # seconds
FEED_REUSE = timedelta(hours=1)  # the feed is read at most once an hour
FEED_STALE = timedelta(days=2)  # a copy this recent answers when the feed refuses or fails
LATE_TRIES = 5  # asks again for EIA's weekly file when a release was due this morning ...
LATE_WAIT = 60  # ... this many seconds apart, within the start's slack before the agents
CACHE_VERSION = "v1"  # in the host files' names; raise it when a file's meaning changes
CACHE_COLUMNS = ["week", "release", "forecast", "read_at"]
FAILURES = (OSError, HTTPException, ValueError)
MONTHS = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}
RELEASE_DATE = re.compile(r"^(\d{4})-([A-Za-z]{3})-(\d{1,2})\b")
NUMBER = re.compile(r"^(-?\d+(?:\.\d+)?)B$")
MONTH_ROW = re.compile(r"<td class='B6'>&nbsp;&nbsp;(\d{4})-\w{3}</td>(.*?)</tr>", re.S)
WEEK_CELL = re.compile(r"<td class='B5'>(\d\d)/(\d\d)&nbsp;</td>\s*<td class='B3'>([\d,]+)&nbsp;")


def _now() -> datetime:
    return datetime.now(ZONE)


def _warn(message: str) -> None:
    print(f"{TARGET}: {message}", file=sys.stderr)


def _cutoff(run_date: date, spec: dict) -> datetime:
    if spec["schedule"]["timezone"] != "America/New_York":
        raise ValueError("EIA's release times are in Eastern Time.")
    return datetime.combine(run_date, clock.fromisoformat(spec["schedule"]["cutoff"]), ZONE)


def _guard(run_date: date, spec: dict) -> datetime:
    now = _now()
    if now.date() != run_date or now >= _cutoff(run_date, spec) or run_date.weekday() != 3:
        raise RuntimeError("Fresh snapshots require the current Thursday before the cutoff.")
    return now


def _fetch(url: str, parse, tries: int = TRIES):
    """parse(body) of the response, asking again after server errors, dropped connections and
    a body that does not parse (a maintenance page served with status 200), up to `tries`
    times. A rate limit (429) is not asked again."""
    for attempt in range(tries):
        try:
            headers = {"User-Agent": USER_AGENT, "Accept": "application/json, text/html;q=0.9"}
            with urlopen(Request(url, headers=headers), timeout=TIMEOUT) as response:
                return parse(response.read())
        except HTTPError as exc:
            if exc.code not in (500, 502, 503, 504) or attempt == tries - 1:
                raise
            retry = str(exc.headers.get("Retry-After", "")) if exc.headers else ""
            delay = min(120, int(retry)) if retry.isdigit() else 2**attempt
        except FAILURES:
            if attempt == tries - 1:
                raise
            delay = 2**attempt
        time.sleep(delay)
    raise AssertionError("unreachable")


def _host_file(name: str) -> Path:
    """A file kept on the host, under ~/.cache/agentic-forecasting/<target>/."""
    root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return root / "agentic-forecasting" / TARGET / name


def _write(path: Path, text: str) -> None:
    """Replace the host file at once, so that a reader never sees half of it."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.with_suffix(".tmp").write_text(text)
        path.with_suffix(".tmp").replace(path)
    except OSError as exc:
        _warn(f"{path.name} could not be kept: {exc!r}")


# --- EIA -------------------------------------------------------------------------------------


def _parse_week(body: bytes) -> dict:
    doc = json.loads(body.decode("utf-8-sig"))
    try:
        total = next(s for s in doc["series"] if s["name"] == LOWER_48)
        week = date.fromisoformat(doc["current_week"])
        year, month, day = RELEASE_DATE.match(doc["release_date"]).groups()
        release = date(int(year), MONTHS[month.title()], int(day))
        flow = float(total["calculated"]["implied_flow"])
        net = float(total["calculated"]["net_change"])
    except (KeyError, StopIteration, TypeError, AttributeError) as exc:
        raise ValueError(f"EIA's weekly file has changed: {exc!r}") from exc
    if week.weekday() != 4 or not 0 < (release - week).days <= 14:
        raise ValueError(f"EIA's weekly file is inconsistent: week {week}, release {release}")
    if not (math.isfinite(flow) and math.isfinite(net)):
        raise ValueError("EIA's weekly file has no number for the Lower 48.")
    return {"week": week, "release": release, "implied_flow": flow, "net_change": net}


def eia_week() -> dict:
    """The newest week EIA has published: its Friday, the release date, and the Lower 48
    implied flow and net change in Bcf, as first published."""
    return _fetch(EIA_WEEK, _parse_week)


def _parse_levels(body: bytes) -> pd.Series:
    levels = {}
    for year, row in MONTH_ROW.findall(body.decode("utf-8", errors="replace")):
        for month, day, value in WEEK_CELL.findall(row):
            levels[pd.Timestamp(int(year), int(month), int(day))] = float(value.replace(",", ""))
    if len(levels) < 100:
        raise ValueError("EIA's weekly history page has changed.")
    series = pd.Series(levels, dtype=float).sort_index()
    if (series.index.dayofweek != 4).any() or series.index.duplicated().any():
        raise ValueError("EIA's weekly levels must be dated on distinct Fridays.")
    return series.rename_axis("week").rename("level")


def _levels() -> pd.Series:
    """EIA's weekly Lower 48 levels as published now. Each read is kept on the host, and when
    the page does not answer the levels kept answer instead, with a warning."""
    path = _host_file(f"levels.{CACHE_VERSION}.csv")
    try:
        levels = _fetch(EIA_HISTORY, _parse_levels)
    except FAILURES as exc:
        try:
            kept = pd.read_csv(path, parse_dates=["week"]).set_index("week")["level"].astype(float)
            if kept.empty or kept.isna().any() or (kept.index.dayofweek != 4).any():
                raise ValueError("the kept levels are incomplete")
        except (OSError, ValueError, KeyError, pd.errors.ParserError):
            raise exc from None
        _warn(
            f"EIA's weekly history page was not read ({exc!r}); using the levels kept on the "
            f"host, to the week ending {kept.index[-1].date()}"
        )
        return kept
    _write(path, levels.reset_index().to_csv(index=False, date_format="%Y-%m-%d"))
    return levels


def _changes(levels: pd.Series, spec: dict) -> pd.Series:
    """The week-to-week change of the levels from x.history_start; a change next to a week
    without a level is left empty."""
    weeks = pd.date_range(levels.index[0], levels.index[-1], freq="W-FRI", name="timestamp")
    change = levels.reindex(weeks).diff()
    return change[change.index >= pd.Timestamp(spec["x"]["history_start"])].rename("target")


def eia_history(spec: dict) -> pd.Series:
    """The weekly change in Lower 48 working gas, from EIA's weekly levels as published now."""
    return _changes(_levels(), spec)


# --- Forex Factory -----------------------------------------------------------------------------


def _number(text: object, field: str) -> float | None:
    text = str(text or "").strip()
    if not text:
        return None
    found = NUMBER.match(text)
    if not found:
        _warn(f"Forex Factory's {field} {text!r} is not read")
        return None
    return float(found[1])


def _parse_feed(body: bytes) -> list[dict]:
    events = json.loads(body.decode("utf-8-sig"))
    if not isinstance(events, list):
        raise ValueError("Forex Factory's feed has changed.")
    found = []
    for event in events:
        if not isinstance(event, dict) or event.get("title") != FEED_TITLE:
            continue
        if event.get("country") != "USD":
            continue
        try:
            when = datetime.fromisoformat(event["date"]).astimezone(ZONE)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Forex Factory's event date is not read: {event!r}") from exc
        found.append(
            {
                "time": when,
                "forecast": _number(event.get("forecast"), "forecast"),
                "previous": _number(event.get("previous"), "previous"),
            }
        )
    return sorted(found, key=lambda e: e["time"])


def _feed_file() -> Path:
    return _host_file(f"feed.{CACHE_VERSION}.json")


def _feed_copy(now: datetime) -> tuple[list[dict], timedelta] | None:
    """The events of the copy of the feed kept on the host, and its age."""
    try:
        kept = json.loads(_feed_file().read_text())
        return _parse_feed(kept["body"].encode()), now - datetime.fromisoformat(kept["read_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def ff_events() -> list[dict]:
    """The Natural Gas Storage releases in Forex Factory's feed for the current week. The feed
    is read at most once an hour, with one request; when it refuses or fails, a copy from the
    last two days answers."""
    now = _now()
    copy = _feed_copy(now)
    if copy is not None and timedelta(0) <= copy[1] < FEED_REUSE:
        return copy[0]
    try:
        events, body = _fetch(FEED, lambda b: (_parse_feed(b), b), tries=1)
    except FAILURES as exc:
        if copy is not None and timedelta(0) <= copy[1] < FEED_STALE:
            _warn(f"Forex Factory's feed was not read ({exc!r}); using the copy read {copy[1]} ago")
            return copy[0]
        raise
    _write(_feed_file(), json.dumps({"read_at": now.isoformat(), "body": body.decode("utf-8-sig")}))
    return events


def match_weeks(events: list[dict], newest: dict) -> dict[date, dict]:
    """Each release in the feed with its storage week, counted from EIA's newest release: a
    release on that day is the newest week's, the ones after it are the following weeks' in
    order, and the ones before it the earlier weeks' in reverse order. Counting rather than
    dating keeps a week with two releases (Christmas 2025: Monday and Wednesday) right. A
    release matched to the week after EIA's newest must list EIA's newest print as its
    previous one; one that does not (a release the feed dates after EIA's actual day) is
    left out."""
    matched: dict[date, dict] = {}
    later = [e for e in events if e["time"].date() > newest["release"]]
    same = [e for e in events if e["time"].date() == newest["release"]]
    earlier = [e for e in events if e["time"].date() < newest["release"]]
    if same:
        matched[newest["week"]] = same[0]
    for n, event in enumerate(later, 1):
        matched[newest["week"] + timedelta(days=7 * n)] = event
    for n, event in enumerate(reversed(earlier), 1):
        matched[newest["week"] - timedelta(days=7 * n)] = event
    following = newest["week"] + timedelta(days=7)
    event = matched.get(following)
    prints = (newest["implied_flow"], newest["net_change"])
    listed = event["previous"] if event else None
    if listed is not None and min(abs(listed - p) for p in prints) > 0.5:
        _warn(
            f"the feed's release of {event['time']:%Y-%m-%d} lists {event['previous']:g} as the "
            f"previous print, not EIA's {newest['implied_flow']:g} for the week ending "
            f"{newest['week']}; it is not taken for the week ending {following}"
        )
        del matched[following]
    return matched


# --- the consensus kept on the host -------------------------------------------------------------


def _cache_file() -> Path:
    return _host_file(f"consensus.{CACHE_VERSION}.csv")


def _load() -> pd.DataFrame:
    path = _cache_file()
    if not path.is_file():
        return pd.DataFrame(columns=CACHE_COLUMNS)
    try:
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        if list(frame.columns) != CACHE_COLUMNS:
            raise ValueError(f"columns {list(frame.columns)}")
        if (frame["week"] == "").any() or (frame["forecast"] == "").any():
            raise ValueError("a week or a forecast is missing")
        pd.to_datetime(frame["week"], format="%Y-%m-%d")
        pd.to_numeric(frame["forecast"])
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        damaged = path.with_suffix(".damaged")
        _warn(f"the kept consensus file is damaged ({exc!r}); set aside as {damaged.name}")
        try:
            path.replace(damaged)
        except OSError as err:
            _warn(f"could not set the damaged file aside: {err!r}")
        return pd.DataFrame(columns=CACHE_COLUMNS)
    return frame


def _consensus() -> dict[date, float]:
    """The consensus kept for each storage week."""
    frame = _load()
    weeks, forecasts = frame["week"], frame["forecast"]
    return {date.fromisoformat(w): float(f) for w, f in zip(weeks, forecasts, strict=True)}


def _remember(matched: dict[date, dict], now: datetime) -> None:
    """Keep each consensus read. Before its release a newer version replaces the one kept;
    after the release a number is only added where none was kept."""
    rows = {r["week"]: r for r in _load().to_dict("records")}
    changed = False
    for week, event in matched.items():
        if event["forecast"] is None:
            continue
        key = week.isoformat()
        released = now >= event["time"]
        if key in rows and (released or float(rows[key]["forecast"]) == event["forecast"]):
            continue
        rows[key] = {
            "week": key,
            "release": event["time"].isoformat(),
            "forecast": event["forecast"],
            "read_at": now.isoformat(timespec="seconds"),
        }
        changed = True
    if changed:
        ordered = sorted(rows.values(), key=lambda r: r["week"])
        _write(_cache_file(), pd.DataFrame(ordered, columns=CACHE_COLUMNS).to_csv(index=False))


def _expert(target: pd.Series, kept: dict[date, float], last: date, first: date) -> pd.Series:
    """Past weeks: the week's own consensus, else the week before's print. The forecast window:
    the newest consensus kept for a week before the first target week, carried forward."""
    past = target.index <= pd.Timestamp(last)
    own = pd.Series({pd.Timestamp(w): v for w, v in kept.items()}, dtype=float)
    column = own.reindex(target.index).where(past).fillna(target.shift(1).where(past))
    column[past] = column[past].ffill().bfill()
    earlier = [w for w in kept if w < first]
    column[~past] = kept[max(earlier)] if earlier else column[past].iloc[-1]
    return column.rename("consensus")


# --- the pipeline's functions -------------------------------------------------------------------


def _newest() -> dict | None:
    try:
        return eia_week()
    except FAILURES as exc:
        _warn(f"EIA's weekly file was not read ({exc!r}); the newest week is the history page's")
        return None


def _feed() -> list[dict] | None:
    try:
        return ff_events()
    except FAILURES as exc:
        _warn(f"Forex Factory's feed was not read ({exc!r})")
        return None


def snapshot(run_date: date, spec: dict) -> dict:
    as_of = _guard(run_date, spec)
    levels = _levels()
    newest, events = _newest(), _feed()
    # The week ending the Friday before the run date normally comes out at 10:30 that morning.
    due = run_date - timedelta(days=6)

    def last_week(newest: dict | None) -> date:
        latest = levels.index[-1].date()
        return max(latest, newest["week"]) if newest else latest

    # When the feed shows a release due this morning and neither of EIA's sources has it yet,
    # EIA's weekly file is asked again a few times before the run goes on without it.
    due_today = events is not None and any(
        e["time"].date() == run_date and e["time"] <= _now() for e in events
    )
    for _ in range(LATE_TRIES if due_today else 0):
        if last_week(newest) >= due:
            break
        time.sleep(LATE_WAIT)
        newest = _newest() or newest
    history = _changes(levels, spec)
    if newest is not None:
        # The newest week is EIA's first print, which the history page may not show yet.
        history.loc[pd.Timestamp(newest["week"])] = newest["implied_flow"]
        history = history.sort_index()
        if events is not None:
            _remember(match_weeks(events, newest), _now())
    last = history.dropna().index[-1].date()
    # In some holiday weeks the week comes out after the cutoff (on Friday, or the next
    # Monday); neither the agents nor the TSFMs then have it, and the run goes on from the
    # week before. Two missing weeks stop the run.
    if last < due - timedelta(days=7):
        raise RuntimeError(f"EIA's newest week is the one ending {last}; two weeks are missing.")
    if last < due:
        _warn(f"EIA has not published the week ending {due}; X_d ends with the one ending {last}")
    _guard(run_date, spec)
    targets = [run_date + timedelta(days=h) for h in spec["horizons"]]
    if any(t.weekday() != 4 for t in targets):
        raise ValueError("Targets must be Fridays that end storage weeks.")
    index = pd.date_range(spec["x"]["history_start"], max(targets), freq="W-FRI", name="timestamp")
    series = pd.DataFrame(
        {
            "target": history.reindex(index),
            "iso_week": index.isocalendar().week.to_numpy(dtype=int),
            "month": index.month,
        },
        index=index,
    )
    notes = {
        "iso_week": "ISO week number of the Friday that ends the storage week; may be 53.",
        "month": "Month of the Friday that ends the storage week.",
    }
    kept = _consensus()
    if kept:
        series["consensus"] = _expert(series["target"], kept, last, min(targets))
        notes["consensus"] = (
            "Economists' consensus in Bcf: a past week's own where one was read, else the week "
            "before's print; in the forecast window, the newest one read for a week before the "
            "first target week."
        )
    return {
        "series": series,
        "last_observed": last,
        "raw": {
            "eia_lower48_levels.csv": levels.reset_index(),
            "consensus.csv": _load(),
        },
        "sources": {
            "eia_week": EIA_WEEK,
            "eia_history": EIA_HISTORY,
            "consensus": FEED,
            "weeks": {"newest": last.isoformat(), "due": due.isoformat()},
            "as_of": as_of.isoformat(),
        },
        "covariate_notes": notes,
    }


def truth(today: date, spec: dict) -> pd.Series:
    """EIA's first print of the newest week, and for older weeks the weekly history, which
    update_truth uses only for a week it never saw first-hand. The newest week of the history
    page is left out, so that a week is never settled on it before EIA's first print is read."""
    if today > _now().date():
        raise RuntimeError("Cannot settle future dates.")
    values: dict[pd.Timestamp, float] = {}
    page_newest = None
    try:
        older = eia_history(spec).dropna()
    except FAILURES as exc:
        _warn(f"EIA's weekly history page was not read ({exc!r})")
    else:
        page_newest = older.index[-1]
        values.update(older.iloc[:-1].to_dict())
    try:
        newest = eia_week()
    except FAILURES as exc:
        _warn(f"EIA's weekly file was not read ({exc!r})")
    else:
        values[pd.Timestamp(newest["week"])] = newest["implied_flow"]
        if page_newest is not None and pd.Timestamp(newest["week"]) < page_newest:
            _warn(
                f"EIA's weekly file shows the week ending {newest['week']}, older than the "
                f"history page's {page_newest.date()}"
            )
    index = pd.DatetimeIndex(sorted(values), name="timestamp")
    return pd.Series([values[k] for k in index], index=index, dtype=float, name="target")


def baseline(run_date: date, spec: dict) -> dict:
    """The consensus for the first target week, as first seen after the cutoff. The feed lists
    it from the Sunday before its release, normally by Tuesday or Wednesday evening, and keeps
    it after the release; the pipeline asks every day until there is one, keeping the first
    answer. A number read earlier and kept on the host answers once the feed has moved on.
    Later target weeks have no consensus."""
    week = run_date + timedelta(days=spec["horizons"][0])
    source = {
        "report": f"Economists' consensus for the storage week ending {week.isoformat()}",
        "posted": None,
        "model": "Consensus of economists' forecasts",
        "page": FEED_PAGE,
        "url": FEED,
    }
    if _now() < _cutoff(run_date, spec):
        return {"source": source | {"note": "before the cutoff"}, "forecasts": []}
    try:
        newest = eia_week()
        events = ff_events()
    except FAILURES as exc:
        _warn(f"the consensus for the week ending {week} was not read ({exc!r})")
    else:
        matched = match_weeks(events, newest)
        _remember(matched, _now())
        event = matched.get(week)
        if event is not None and event["forecast"] is not None:
            source |= {
                "posted": _now().isoformat(timespec="seconds"),
                "release": event["time"].isoformat(),
            }
            point = {"target_date": week.isoformat(), "point": event["forecast"]}
            return {"source": source, "forecasts": [point]}
    kept = _consensus().get(week)
    if kept is not None:
        source["note"] = "read earlier and kept on the host"
        return {"source": source, "forecasts": [{"target_date": week.isoformat(), "point": kept}]}
    return {"source": source | {"note": "the consensus is not posted yet"}, "forecasts": []}
