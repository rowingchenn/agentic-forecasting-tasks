"""TSA checkpoint throughput: where the numbers for this target come from.

The agentic-forecasting pipeline imports this file on the host, outside every episode; ALE
uploads tests/ only to verify, so no agent sees it. It needs only the standard library and
pandas. The pipeline passes `spec`, `[metadata.agentic-forecasting]` of task.toml with the
AF_* schedule merged in, and turns the results into archived files.

    snapshot(run_date, spec)  X_d: TSA's daily numbers and the travel calendar as known now
    truth(today, spec)        every day's first published number known now
    baseline(run_date, spec)  none: no public expert forecast of this series exists

tsa.gov refuses this pipeline's automated requests (403), so TSA's numbers are read from two
keyless publishers of them, never from tsa.gov itself:

- the settlement of Kalshi's KXTSAD market for each day from 2026-10-09, whose rules settle
  on TSA's first published number; it usually comes 09:45 to 10:30 ET on the morning TSA
  posts the day;
- the Internet Archive's latest capture of TSA's Passenger Volumes page, for days Kalshi has
  not settled; its captures usually come overnight and around 11:50 ET.

Every day's number is kept in the host's append-only copy, CACHE/tsa_daily.csv (date,
passengers, source, retrieved_at), and never changed once recorded. The first call fills it
with captures of TSA's pages of past years (which carry TSA's year-end revisions) and of
the current year. Every request names this pipeline as its User-Agent and comes at least
REQUEST_PAUSE seconds after the last.
"""

from __future__ import annotations

import gzip
import hashlib
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

TSA = "https://www.tsa.gov/travel/passenger-volumes"
WAYBACK = "https://web.archive.org"
USER_AGENT = "agentic-forecasting/1.0"
CACHE = (
    Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    / "agentic-forecasting"
    / "tsa_checkpoint_throughput"
)
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = "KXTSAD"  # Kalshi's daily TSA passenger screenings, one event per day
KALSHI_START = date(2026, 10, 9)  # the series' first event
LOOKBACK_DAYS = 14  # Kalshi is asked for the days of this window not yet recorded
STALE_DAYS = 5  # X ending this long before the run is reported: longer than a holiday delay
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
COUNT = r"\d{1,3}(?:,\d{3})*|\d+"  # a whole number of passengers, with or without commas
COLUMNS = ["passengers", "source", "retrieved_at"]
REQUEST_PAUSE = 0.6  # seconds between requests
RATE_LIMIT_PAUSE = 5  # seconds to wait after a 429

_last_request = 0.0  # time.monotonic() of the last request


def _now() -> datetime:
    return datetime.now(UTC)


def _get(url: str, retries: int = 3) -> tuple[bytes, dict]:
    """The body at url, and where and when it was retrieved. A 429 is retried after
    RATE_LIMIT_PAUSE seconds, and a server error, timeout or dropped connection after a short
    wait, up to `retries` times; any other HTTP error is raised at once."""
    global _last_request
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(retries + 1):
        time.sleep(max(0.0, _last_request + REQUEST_PAUSE - time.monotonic()))
        _last_request = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read()
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
        return body, meta
    raise AssertionError("unreachable")


class _Tables(HTMLParser):
    """Every table of a page, as rows of cell texts with whitespace collapsed."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._table is not None and self._row is not None:
            self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None


def parse_tsa_page(body: bytes) -> pd.Series:
    """The daily numbers in a TSA Passenger Volumes page's one Date/Numbers table, by date.
    Raises ValueError when the page no longer looks like that."""
    parser = _Tables()
    parser.feed(body.decode("utf-8-sig", errors="replace"))
    tables = [t for t in parser.tables if t and [c.lower() for c in t[0]] == ["date", "numbers"]]
    if len(tables) != 1:
        raise ValueError("TSA page: expected one Date/Numbers table; its layout has changed")
    counts: dict[date, int] = {}
    for row in tables[0][1:]:
        if (
            len(row) != 2
            or not re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", row[0])
            or not re.fullmatch(COUNT, row[1])
        ):
            raise ValueError(f"TSA page: unexpected row {row!r}")
        day = datetime.strptime(row[0], "%m/%d/%Y").date()
        count = int(row[1].replace(",", ""))
        if count <= 0 or day in counts:
            raise ValueError(f"TSA page: a nonpositive count or a repeated date in {row!r}")
        counts[day] = count
    if not counts:
        raise ValueError("TSA page: the Date/Numbers table is empty")
    index = pd.DatetimeIndex(list(counts), name="date")
    return pd.Series(list(counts.values()), index, dtype="int64", name="passengers").sort_index()


def archived_page(url: str) -> pd.DataFrame:
    """TSA's numbers (passengers, source, retrieved_at) in the Internet Archive's latest
    successful capture of the TSA page at `url`; source is that capture's address, which
    carries its time. Raises ValueError if there is none or it is not such a page."""
    query = urllib.parse.urlencode(
        {
            "url": url,
            "filter": "statuscode:200",
            "fl": "timestamp",
            "limit": -1,
            "fastLatest": "true",
        }
    )
    body, _ = _get(f"{WAYBACK}/cdx/search/cdx?{query}")
    stamps = body.decode().split()
    if not stamps or not re.fullmatch(r"\d{14}", stamps[-1]):
        raise ValueError(f"the Internet Archive lists no capture of {url}")
    capture = f"{WAYBACK}/web/{stamps[-1]}id_/{url}"  # id_: the page as captured, unaltered
    body, meta = _get(capture)
    if body[:2] == b"\x1f\x8b":  # some captures are stored gzip-compressed
        body = gzip.decompress(body)
    counts = parse_tsa_page(body)
    return pd.DataFrame(
        {"passengers": counts, "source": capture, "retrieved_at": meta["retrieved_at"]}
    )


def event_ticker(day: date) -> str:
    """Kalshi's KXTSAD event for a day, such as KXTSAD-26OCT29."""
    return f"{SERIES}-{day:%y}{MONTHS[day.month - 1]}{day:%d}"


def _long_date(day: date) -> str:
    """A day as Kalshi's rules write it, such as October 29, 2026."""
    return f"{MONTH_NAMES[day.month - 1]} {day.day}, {day.year}"


def event_markets(day: date) -> tuple[list[dict], dict | None]:
    """The markets of the day's KXTSAD event, and the request that listed them. Kalshi moves
    older markets from /markets to /historical/markets; ([], None) if neither lists them."""
    query = urllib.parse.urlencode({"event_ticker": event_ticker(day), "limit": 100})
    for path in ("markets", "historical/markets"):
        body, meta = _get(f"{KALSHI}/{path}?{query}")
        listed = json.loads(body).get("markets") or []
        if listed:
            return listed, meta
    return [], None


def settled_value(markets: list[dict], day: date) -> int | None:
    """The number the day's markets settled on, or None until all of them have. Raises
    ValueError if a market's rules are not about that day, the markets disagree, or the
    value is not a whole number of passengers."""
    for market in markets:
        if _long_date(day) not in market.get("rules_primary", ""):
            raise ValueError(f"{market.get('ticker')}: its rules are not about {_long_date(day)}")
    values = {market.get("expiration_value") or "" for market in markets}
    settled = values - {""}
    if len(settled) > 1:
        raise ValueError(f"{event_ticker(day)}: markets settled on {sorted(settled)}")
    if not settled or "" in values:
        return None
    value = settled.pop()
    if not re.fullmatch(COUNT, value):
        raise ValueError(f"{event_ticker(day)}: settled on {value!r}, not a number of passengers")
    return int(value.replace(",", ""))


def kalshi_settlements(days: list[date]) -> pd.DataFrame:
    """The settled KXTSAD value of each of `days` that has one (passengers, source,
    retrieved_at). A day whose request fails or whose markets fail the checks is left out
    with a warning."""
    rows = []
    for day in days:
        try:
            listed, meta = event_markets(day)
            value = settled_value(listed, day)
        except (OSError, http.client.HTTPException, ValueError) as exc:
            print(f"warning: {event_ticker(day)} left out: {exc}", file=sys.stderr)
            continue
        if value is not None and meta is not None:
            source = f"{KALSHI}/markets?event_ticker={event_ticker(day)}"
            rows.append((pd.Timestamp(day), value, source, meta["retrieved_at"]))
    frame = pd.DataFrame(rows, columns=["date", *COLUMNS])
    return frame.set_index(pd.DatetimeIndex(frame.pop("date"), name="date"))


def daily_counts(spec: dict, through: date) -> pd.DataFrame:
    """Every day's first published number (passengers, source, retrieved_at) from
    history_start to `through`, as recorded on this host. Days not yet recorded are added:
    those Kalshi has settled within LOOKBACK_DAYS, then those on the latest capture of TSA's
    page. The first call also adds TSA's pages of past years and fails if any of them fails;
    later, a failing capture only leaves the day to Kalshi, with a warning."""
    start = date.fromisoformat(spec["x"]["history_start"])
    path = CACHE / "tsa_daily.csv"
    known = None
    if path.exists():
        known = pd.read_csv(path, index_col="date", parse_dates=["date"])
    recorded = set() if known is None else set(known.index.date)
    first = max(KALSHI_START, through - timedelta(days=LOOKBACK_DAYS))
    asked = [d for d in pd.date_range(first, through).date if d not in recorded]
    parts = [] if known is None else [known]
    parts.append(kalshi_settlements(asked))
    pages = [TSA] + (
        [] if known is not None else [f"{TSA}/{y}" for y in range(start.year, through.year)]
    )
    for url in pages:
        try:
            parts.append(archived_page(url))
        except (OSError, http.client.HTTPException, ValueError) as exc:
            if known is None:
                raise RuntimeError(
                    f"the Internet Archive did not serve {url} ({exc}). The first call needs "
                    f"it; a file with the columns date, {', '.join(COLUMNS)} can be copied to "
                    f"{path} instead."
                ) from exc
            print(f"warning: TSA's page from the Internet Archive left out: {exc}", file=sys.stderr)
    counts = pd.concat(parts)
    counts = counts[~counts.index.duplicated(keep="first")].sort_index()  # first recorded wins
    if known is None or len(counts) > len(known):
        CACHE.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".partial")
        counts.to_csv(partial)
        partial.replace(path)
    return counts.loc[pd.Timestamp(start) : pd.Timestamp(through)].astype({"passengers": "int64"})


def calendar_frame(index: pd.DatetimeIndex) -> pd.DataFrame:
    """The travel calendar of each day on `index`: day of week, US federal holidays on their
    observed dates, the Thanksgiving travel window (Wednesday before to Monday after), the
    winter holidays (December 20 to January 4) and the day of the year."""
    pad = timedelta(days=7)
    holidays = USFederalHolidayCalendar().holidays(
        index.min() - pad, index.max() + pad, return_name=True
    )
    thanksgiving = holidays.index[holidays == "Thanksgiving Day"]
    window = [day + timedelta(days=offset) for day in thanksgiving for offset in range(-1, 5)]
    month, day = index.month, index.day
    winter = ((month == 12) & (day >= 20)) | ((month == 1) & (day <= 4))
    return pd.DataFrame(
        {
            "dow": index.dayofweek,
            "federal_holiday": index.isin(holidays.index).astype(int),
            "thanksgiving_window": index.isin(window).astype(int),
            "winter_holidays": winter.astype(int),
            "day_of_year": index.dayofyear,
        },
        index=index,
    )


def target_history(counts: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """Daily values on `index`, empty after the last published day. A day missing between two
    published days is filled linearly (truth leaves it empty): the pipeline's seasonal naive
    reads the last observed week of X_d and cannot score against a missing value."""
    return counts.astype(float).reindex(index).interpolate(limit_area="inside")


def snapshot(run_date: date, spec: dict) -> dict:
    """X_d as known right now: one row per day from history_start to the last target date,
    `target` empty after the last published day, the calendar for every day."""
    start = date.fromisoformat(spec["x"]["history_start"])
    horizon = max(spec["horizons"])
    counts = daily_counts(spec, run_date - timedelta(days=1))
    last_observed = counts.index.max().date()
    if last_observed < run_date - timedelta(days=STALE_DAYS):
        print(f"warning: TSA's latest number known is for {last_observed}", file=sys.stderr)
    index = pd.date_range(start, run_date + timedelta(days=horizon), freq="D", name="timestamp")
    target = target_history(counts["passengers"], index).rename("target")
    series = pd.concat([target, calendar_frame(index)], axis=1)
    return {
        "series": series.rename_axis("timestamp"),
        "last_observed": last_observed,
        "raw": {"tsa_daily.csv": counts.rename_axis("date").reset_index()},
        "sources": {
            "kalshi": {
                "url": f"{KALSHI}/markets?series_ticker={SERIES}",
                "use": f"the settlement of each day's {SERIES} event from {KALSHI_START}, "
                "which settles on TSA's first published number",
            },
            "internet_archive": {
                "url": f"{WAYBACK}/web/*/{TSA}*",
                "use": "captures of TSA's Passenger Volumes pages, for days without a Kalshi "
                "settlement; tsa.gov refuses this pipeline's requests",
            },
        },
        "covariate_notes": {
            "target": "TSA's first published number of travelers screened at its checkpoints "
            f"that day, nationwide; from {KALSHI_START} the settlement of Kalshi's {SERIES} "
            "market for the day where there is one. A day missing between two published "
            "days is filled linearly from its neighbours",
            "dow": "day of week, Monday = 0",
            "federal_holiday": "1 on a US federal holiday, on its observed date",
            "thanksgiving_window": "1 from the Wednesday before Thanksgiving to the Monday after",
            "winter_holidays": "1 from December 20 to January 4",
            "day_of_year": "day of the year, 1 to 366",
        },
    }


def truth(today: date, spec: dict) -> pd.Series:
    """Every day's first published number up to the day before today, as known now."""
    return daily_counts(spec, today - timedelta(days=1))["passengers"]


def baseline(run_date: date, spec: dict) -> dict:
    """No public expert forecast of TSA's daily number covers the seven days after a run:
    Kalshi's KXTSAD market for a day opens on that day itself. The engine scores against its
    weekly seasonal naive instead."""
    return {
        "source": {"report": "no expert forecast of TSA's daily checkpoint number"},
        "forecasts": [],
    }
