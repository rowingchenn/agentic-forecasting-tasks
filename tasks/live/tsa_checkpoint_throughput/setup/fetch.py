"""TSA checkpoint throughput: where the numbers for this target come from.

The agentic-forecasting pipeline imports this file on the host, outside every episode; the
sandbox's setup stage never runs it, and setup/run.sh makes it unreadable to the agent.
It needs only the standard library and pandas. The pipeline passes `spec`, the target
definition from task.yaml (`extras.agentic-forecasting` plus `params`), and turns the
results into archived files.

    snapshot(run_date, spec)  X_d: TSA's daily numbers and the travel calendar as known now
    truth(today, spec)        every day's first published number known now
    baseline(run_date, spec)  Kalshi's KXTSAD market for the run date as it stood at the cutoff

No credentials: both sources are keyless. TSA's Passenger Volumes pages (one per past year
and the current one) are fetched once, while the host has no copy, since tsa.gov may refuse
automated clients. From 2026-10-09 every day's number comes from the settlement of Kalshi's
KXTSAD market for it, which settles on TSA's first published number. Every request names
this pipeline as its User-Agent and comes at least REQUEST_PAUSE seconds after the last.
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
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

TSA = "https://www.tsa.gov/travel/passenger-volumes"
USER_AGENT = "agentic-forecasting/1.0"
CACHE = (
    Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    / "agentic-forecasting"
    / "tsa_checkpoint_throughput"
)
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = "KXTSAD"  # Kalshi's daily TSA passenger screenings, one event per day
KALSHI_START = date(2026, 10, 9)  # the series' first event
LOOKBACK_DAYS = 30  # once the host has Kalshi values, only this many days back are asked again
STALE_DAYS = 5  # a day still without a value this long is reported: longer than a holiday delay
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
CONTRACT_TERMS = "https://assets.kalshi.com/contract_terms/ECONSTAT.pdf"
CANDLES_FROM = "08:55"  # local; where candles start if the markets give no open_time
TIE = 1e-12  # a bin must be more probable than every other by more than this
METHOD = (
    "each contract's price at the cutoff is the midpoint of its best bid and ask in its last "
    "one-minute candle, if both are quoted and at most max_quote_spread apart; the prices are "
    "made non-increasing by pooling adjacent violators; the point is the middle of the most "
    "probable bin with quoted strikes on both sides, if it is more probable than every other "
    "bin, both tails and every range between unquoted strikes"
)
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


def _seed_page(url: str) -> pd.DataFrame:
    body, meta = _get(url)
    counts = parse_tsa_page(body)
    return pd.DataFrame({"passengers": counts, "source": url, "retrieved_at": meta["retrieved_at"]})


def tsa_seed(spec: dict, today: date) -> pd.DataFrame:
    """TSA's daily numbers (passengers, source, retrieved_at) from the host's copy, made on
    the first call: TSA's page of every past year from history_start's on, which carries
    TSA's year-end revisions, then its current page, whose rows win. In early January last
    year may have no page yet; the current page then still shows it."""
    path = CACHE / "tsa_seed.csv"
    if not path.exists():
        first_year = date.fromisoformat(spec["x"]["history_start"]).year
        parts = []
        try:
            for year in range(first_year, today.year):
                try:
                    parts.append(_seed_page(f"{TSA}/{year}"))
                except urllib.error.HTTPError as exc:
                    if exc.code != 404 or year != today.year - 1:
                        raise
            parts.append(_seed_page(TSA))
        except OSError as exc:
            raise RuntimeError(
                f"tsa.gov did not serve TSA's numbers ({exc}). They are fetched only once: "
                f"a seed file with the columns date, passengers, source and retrieved_at can "
                f"be copied to {path} instead."
            ) from exc
        seed = pd.concat(parts)
        seed = seed[~seed.index.duplicated(keep="last")].sort_index()
        CACHE.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".partial")
        seed.to_csv(partial)
        partial.replace(path)
    return pd.read_csv(path, index_col="date", parse_dates=["date"])


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


def kalshi_settlements(through: date) -> pd.DataFrame:
    """Every day's settled KXTSAD value (passengers, event_ticker, retrieved_at) up to
    `through`, from the host's append-only copy. Days it lacks are asked for: every day
    since KALSHI_START the first time, afterwards those of the last LOOKBACK_DAYS. A day whose
    request fails or whose markets fail the checks is left out with a warning and asked for
    again later; so is one still unlisted or unsettled STALE_DAYS on."""
    path = CACHE / "kalshi_settled.csv"
    columns = ["passengers", "event_ticker", "retrieved_at"]
    if path.exists():
        cached = pd.read_csv(path, index_col="date", parse_dates=["date"])
        first = max(KALSHI_START, through - timedelta(days=LOOKBACK_DAYS))
    else:
        cached = pd.DataFrame(columns=columns, index=pd.DatetimeIndex([], name="date"))
        first = KALSHI_START
    rows = []
    for day in pd.date_range(first, through, freq="D").date:
        if pd.Timestamp(day) in cached.index:
            continue
        try:
            listed, meta = event_markets(day)
            value = settled_value(listed, day)
        except (OSError, http.client.HTTPException, ValueError) as exc:
            print(f"warning: {event_ticker(day)} left out: {exc}", file=sys.stderr)
            continue
        if value is not None and meta is not None:
            rows.append((pd.Timestamp(day), value, event_ticker(day), meta["retrieved_at"]))
        elif day <= through - timedelta(days=STALE_DAYS):
            state = "not settled" if listed else "not listed"
            print(f"warning: {event_ticker(day)} is still {state}", file=sys.stderr)
    if rows:
        new = pd.DataFrame(rows, columns=["date", *columns]).set_index("date")
        CACHE.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".partial")
        pd.concat([cached, new]).sort_index().to_csv(partial)
        partial.replace(path)
        cached = pd.read_csv(path, index_col="date", parse_dates=["date"])
    return cached.loc[: pd.Timestamp(through)]


def daily_counts(spec: dict, through: date) -> pd.DataFrame:
    """Every day's first published number (passengers, source) from history_start to
    `through`, as known now: Kalshi's settlement where there is one, TSA's pages elsewhere."""
    start = pd.Timestamp(spec["x"]["history_start"])
    seed = tsa_seed(spec, through + timedelta(days=1))[["passengers", "source"]]
    kalshi = kalshi_settlements(through)
    settled = pd.DataFrame(
        {
            "passengers": kalshi["passengers"],
            "source": f"{KALSHI}/markets?event_ticker=" + kalshi["event_ticker"],
        }
    )
    counts = pd.concat([seed, settled])
    counts = counts[~counts.index.duplicated(keep="last")].sort_index()
    return counts.loc[start : pd.Timestamp(through)].astype({"passengers": "int64"})


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
    index = pd.date_range(start, run_date + timedelta(days=horizon), freq="D", name="timestamp")
    target = target_history(counts["passengers"], index).rename("target")
    series = pd.concat([target, calendar_frame(index)], axis=1)
    return {
        "series": series.rename_axis("timestamp"),
        "last_observed": last_observed,
        "raw": {"tsa_daily.csv": counts.rename_axis("date").reset_index()},
        "sources": {
            "tsa": {
                "url": TSA,
                "use": "days without a Kalshi settlement, from TSA's pages as fetched once",
            },
            "kalshi": {
                "url": f"{KALSHI}/markets?series_ticker={SERIES}",
                "use": f"the settlement of each day's {SERIES} event from {KALSHI_START}",
            },
        },
        "covariate_notes": {
            "target": "TSA's first published number of travelers screened at its checkpoints "
            f"that day, nationwide; from {KALSHI_START} the settlement of Kalshi's {SERIES} "
            "market for the day. A day missing between two published days is filled "
            "linearly from its neighbours",
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


def _local(day: date, clock: str, timezone: str) -> datetime:
    """`clock` ("HH:MM") on `day`, local time."""
    hour, minute = map(int, clock.split(":"))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZoneInfo(timezone))


def ladder(markets: list[dict], day: date) -> list[dict]:
    """The day's contracts, "at least `strike` passengers" each, by strike. Raises ValueError
    if any is not such a contract on the day's first published number."""
    rungs = []
    for market in markets:
        strike = market.get("floor_strike")
        whole = isinstance(strike, int | float) and float(strike).is_integer()
        rule = (
            "If the first published number of nationwide TSA passenger screenings on "
            f"{_long_date(day)} is at least {int(strike):,} passengers, then the market "
            "resolves to Yes."
            if whole
            else None
        )
        if (
            market.get("event_ticker") != event_ticker(day)
            or market.get("market_type") != "binary"
            or market.get("strike_type") != "greater_or_equal"
            or not whole
            or market.get("rules_primary") != rule
        ):
            raise ValueError(
                f"{market.get('ticker')}: not a {SERIES} contract on {day} as expected"
            )
        rungs.append({"ticker": market["ticker"], "strike": int(strike)})
    if len({r["strike"] for r in rungs}) != len(rungs):
        raise ValueError(f"{event_ticker(day)}: two contracts share a strike")
    return sorted(rungs, key=lambda r: r["strike"])


def quotes_at(
    candles: dict[str, list[dict]], tickers: list[str], at: datetime, max_spread: float
) -> dict[str, float | None]:
    """Each contract's price at `at`: the midpoint of the best bid and ask in its last
    one-minute candle ending by then, if both are quoted and at most max_spread apart."""
    mids: dict[str, float | None] = {}
    for ticker in tickers:
        before = [c for c in candles.get(ticker) or [] if c["end_period_ts"] <= at.timestamp()]
        mids[ticker] = None
        if before:
            last = max(before, key=lambda c: c["end_period_ts"])
            bid = float((last.get("yes_bid") or {}).get("close_dollars") or 0)
            ask = float((last.get("yes_ask") or {}).get("close_dollars") or 1)
            # prices are whole cents: the tolerance keeps a spread of exactly max_spread
            if 0 < bid <= ask < 1 and ask - bid <= max_spread + 1e-9:
                mids[ticker] = (bid + ask) / 2
    return mids


def _non_increasing(values: list[float]) -> list[float]:
    """The non-increasing sequence closest to `values` (pool adjacent violators)."""
    blocks: list[list[float]] = []  # [sum, count] of each pooled block
    for value in values:
        blocks.append([value, 1])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] < blocks[-1][0] / blocks[-1][1]:
            total, count = blocks.pop()
            blocks[-1][0] += total
            blocks[-1][1] += count
    return [total / count for total, count in blocks for _ in range(int(count))]


def _span(part: dict) -> str:
    if part["low"] is None:
        return f"below {part['high']:,}"
    if part["high"] is None:
        return f"{part['low']:,} or more"
    return f"{part['low']:,} to {part['high']:,}"


def modal_bin(strikes: list[int], mids: list[float | None]) -> dict:
    """The market's most probable bin. mids[i] is the price of "at least strikes[i]", None
    if unquoted. The quoted prices, made non-increasing, are the probabilities of reaching
    each strike: between two adjacent quoted strikes their difference is the probability of
    that bin, and across unquoted strikes that of an unresolved range. The point is the middle
    of the most probable bin with strikes on both sides, if it is more probable than every
    other bin, both tails and every unresolved range by more than TIE."""
    quoted = [i for i, mid in enumerate(mids) if mid is not None]
    fitted = _non_increasing([mids[i] for i in quoted])
    n = len(strikes)
    known = [(-1, 1.0), *zip(quoted, fitted, strict=True), (n, 0.0)]
    intervals, unresolved = [], []
    for (left, reach_left), (right, reach_right) in pairwise(known):
        part = {
            "low": strikes[left] if left >= 0 else None,
            "high": strikes[right] if right < n else None,
            "probability": reach_left - reach_right,
        }
        (intervals if right == left + 1 else unresolved).append(part)
    result = {"point": None, "intervals": intervals, "unresolved": unresolved, "reason": None}
    bounded = [b for b in intervals if b["low"] is not None and b["high"] is not None]
    if not bounded:
        return result | {"reason": "no bin has quoted strikes on both sides"}
    best = max(bounded, key=lambda b: b["probability"])
    rival = max(
        (b for b in intervals + unresolved if b is not best), key=lambda b: b["probability"]
    )
    if best["probability"] <= rival["probability"] + TIE:
        return result | {
            "reason": f"the most probable bin, {_span(best)} ({best['probability']:.3f}), is "
            f"not more probable than {_span(rival)} ({rival['probability']:.3f})"
        }
    return result | {"point": (best["low"] + best["high"]) / 2}


def _no_point(run_date: date, source: dict, reason: str) -> dict:
    """A baseline record without a forecast; the reason is printed for the daily log too."""
    print(f"note: no Kalshi baseline for {run_date}: {reason}", file=sys.stderr)
    return {"source": source | {"reason": reason}, "forecasts": []}


def baseline(run_date: date, spec: dict) -> dict:
    """Kalshi's market on the run date's number as it stood at the cutoff, read the next day
    from its one-minute candlesticks; a day's market opens that morning, so only the run date
    has one. Empty before the cutoff, if the event is not listed, or if the quotes leave no
    single most probable bin; the reason is then printed for the daily log too."""
    timezone, clock = spec["schedule"]["timezone"], spec["schedule"]["cutoff"]
    cutoff = _local(run_date, clock, timezone)
    event = event_ticker(run_date)
    source = {
        "report": f"Kalshi {event}, quotes at {clock} ET",
        "posted": None,
        "model": f"Kalshi {SERIES}",
    }
    if _now() < cutoff:
        return _no_point(run_date, source, "before the cutoff")
    listed, _ = event_markets(run_date)
    if not listed:
        return _no_point(run_date, source, f"{event} is not listed")
    rungs = ladder(listed, run_date)
    # candles from the market's open, whatever clock Kalshi keeps it on
    opened = [datetime.fromisoformat(m["open_time"]) for m in listed if m.get("open_time")]
    start = min(opened, default=_local(run_date, CANDLES_FROM, timezone))
    if start >= cutoff:
        return _no_point(run_date, source, f"{event} opened after the cutoff")
    query = urllib.parse.urlencode(
        {
            "start_ts": int(start.timestamp()),
            "end_ts": int(cutoff.timestamp()),
            "period_interval": 1,
        }
    )
    body, meta = _get(f"{KALSHI}/series/{SERIES}/events/{event}/candlesticks?{query}")
    payload = json.loads(body)
    if payload.get("adjusted_end_ts", cutoff.timestamp()) < cutoff.timestamp():
        raise ValueError(f"{event}: Kalshi's candlesticks stop before the cutoff")
    candles = dict(
        zip(
            payload.get("market_tickers") or [],
            payload.get("market_candlesticks") or [],
            strict=True,
        )
    )
    spread = spec["baseline"]["max_quote_spread"]
    mids = quotes_at(candles, [r["ticker"] for r in rungs], cutoff, spread)
    quoted = [r["ticker"] for r in rungs if mids[r["ticker"]] is not None]
    fitted = dict(zip(quoted, _non_increasing([mids[t] for t in quoted]), strict=True))
    mode = modal_bin([r["strike"] for r in rungs], [mids[r["ticker"]] for r in rungs])
    forecasts = []
    if mode["point"] is not None:
        forecasts.append({"target_date": run_date.isoformat(), "point": mode["point"]})
    else:
        print(f"note: no Kalshi baseline for {run_date}: {mode['reason']}", file=sys.stderr)
    return {
        "source": source
        | {
            "posted": cutoff.isoformat(),
            "event_ticker": event,
            "method": METHOD,
            "max_quote_spread": spread,
            "ladder": [
                r | {"mid": mids[r["ticker"]], "fitted": fitted.get(r["ticker"])} for r in rungs
            ],
            "intervals": mode["intervals"],
            "unresolved": mode["unresolved"],
            "reason": mode["reason"],
            "retrieved_at": meta["retrieved_at"],
            "contract_terms_url": CONTRACT_TERMS,
        },
        "forecasts": forecasts,
    }
