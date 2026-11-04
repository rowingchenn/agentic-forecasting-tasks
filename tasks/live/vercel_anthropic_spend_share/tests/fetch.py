"""Anthropic's share of Vercel AI Gateway spend: where the numbers for this target come from.

The agentic-forecasting pipeline imports this file on the host, outside every episode; ALE
uploads tests/ only to verify, after the agent has finished. It needs only the standard
library and pandas. The pipeline passes `spec`, [metadata.agentic-forecasting] of task.toml
with the AF_* schedule merged in, and turns the results into archived files.

    snapshot(run_date, spec)  X_d: Anthropic's daily share of spend up to the last complete
                              UTC day, the day of the week, and Kalshi's market median one
                              cycle before B's
    truth(today, spec)        every day's share once a full day has passed since it ended
    baseline(run_date, spec)  the median of Kalshi's KXANTHVSPEND market on each target date
                              that has one, as it stood at the cutoff, read the next day

The leaderboard's days are taken to be UTC days; Vercel does not document them. Vercel's
export endpoint serves every day from 2025-10-01 by date range, and its latest row is the
current, incomplete day. It caches each answer for up to 24 hours, so a day counts only once
the answer was built (when it was retrieved, less the Age its cache reports) long enough
after the day ended. The range asked for ends on the run date: no agent of the previous run
day asks for that URL, which names a date still to come for it, so its answer cannot be the
one served. Kalshi lists an event for even calendar dates only. A reading of its market at a
past moment never changes, so each is kept on the host once taken. No credentials: both
sources are keyless. Every request names this pipeline as its User-Agent and comes at least
REQUEST_PAUSE seconds after the last.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

TARGET = "vercel_anthropic_spend_share"
EXPORT = "https://vercel.com/api/ai/leaderboard-export"  # Vercel's open-data export
LEADERBOARD = "https://vercel.com/ai-gateway/leaderboards/labs"  # the chart Kalshi settles on
EXPORT_DOCS = "https://vercel.com/docs/ai-gateway/leaderboards"
LAB = "anthropic"  # the lab's name in the export
METRIC = "spend"
COLUMNS = ["date", "name", "share_percent"]
SETTLED = timedelta(hours=12)  # a day enters X_d once the export was built this long after it
FULL_DAY = timedelta(days=1)  # and truth once a full day has passed
CACHED_FOR = timedelta(hours=24)  # how long Vercel's cache may keep an answer (export docs)
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = "KXANTHVSPEND"  # Kalshi's Anthropic Vercel spend share, one event per even date
CONTRACT_TERMS = "https://assets.kalshi.com/contract_terms/VAIMARKETSHARE.pdf"
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
RULE = re.compile(  # Kalshi writes a whole strike as "54" or "35.0"
    r"If the Anthropic Vercel share of AI Gateway spend across the top labs for "
    r"(?P<day>[A-Z][a-z]{2} \d{1,2}, \d{4}) is above (?P<strike>\d+(?:\.\d+)?)%, then the "
    r"market resolves to Yes\."
)
ROUNDING_RULE = (
    "This event resolves using the Anthropic Vercel share of AI Gateway spend across the top "
    "labs, rounded to one decimal."
)
ROUNDING = 0.05  # "above k" pays when the share rounded to one decimal exceeds k: from k + 0.05
CANDLE_LIMIT = 5000  # one-minute candles Kalshi answers per request, across an event's markets
METHOD = (
    "a contract 'above k%' pays when the share rounded to one decimal exceeds k, so its price "
    "is read as the probability that the share is at least k + 0.05; its price at the cutoff is "
    "the midpoint of the best bid and ask in its last one-minute candle by then, if both are "
    "quoted and at most max_quote_spread apart; the prices are made non-increasing by pooling "
    "adjacent violators and joined linearly between quoted strikes; the point is the median, "
    "where they cross one half, if the quoted strikes bracket it"
)
USER_AGENT = "agentic-forecasting/1.0"
CACHE = (
    Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "agentic-forecasting" / TARGET
)
CACHE_VERSION = "v1"  # in the cache file's name; raise it when the parser changes
# what a failed request, or an answer that no longer parses, raises
FAILURES = (OSError, http.client.HTTPException, ValueError, KeyError)
REQUEST_PAUSE = 0.6  # seconds between requests
RATE_LIMIT_PAUSE = 5  # seconds to wait after a 429

_last_request = 0.0  # time.monotonic() of the last request


def _now() -> datetime:
    return datetime.now(UTC)


def _get(url: str, retries: int = 3) -> tuple[bytes, dict]:
    """The body at url, and where and when it was retrieved, with the Age a cache gave it. A
    429 is retried after RATE_LIMIT_PAUSE seconds, and a server error, timeout or dropped
    connection after a short wait, up to `retries` times; any other HTTP error is raised at
    once."""
    global _last_request
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(retries + 1):
        time.sleep(max(0.0, _last_request + REQUEST_PAUSE - time.monotonic()))
        _last_request = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read()
                age = response.headers.get("Age")
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
        if age and age.strip().isdigit():
            meta["age"] = int(age)
        return body, meta
    raise AssertionError("unreachable")


def parse_export(body: bytes) -> tuple[pd.DataFrame, dict]:
    """Every lab's share of AI Gateway spend per day in a labs export (date, name,
    share_percent), and the export's own fields: licence and date range. Raises ValueError if
    the answer is not the labs dataset of all modalities with one share per lab and day."""
    data = json.loads(body)
    if (
        not isinstance(data, dict)
        or data.get("dataset") != "labs"
        or data.get("modality") != "all"
        or not isinstance(data.get("rows"), list)
    ):
        raise ValueError("Vercel export: not the labs dataset of all modalities")
    rows = pd.DataFrame(
        [row for row in data["rows"] if isinstance(row, dict)],
        columns=["date", "group", "name", "metric", "modality", "share_percent"],
    )
    rows = rows[rows["metric"] == METRIC]
    if not ((rows["group"] == "lab") & (rows["modality"] == "all")).all():
        raise ValueError("Vercel export: a spend row that is not a lab's, over all modalities")
    spend = pd.DataFrame(
        {
            "date": pd.to_datetime(rows["date"], format="%Y-%m-%d", errors="coerce"),
            "name": rows["name"],
            "share_percent": pd.to_numeric(rows["share_percent"], errors="coerce"),
        }
    )
    if spend.isna().any().any() or not spend["share_percent"].between(0, 100).all():
        raise ValueError("Vercel export: a spend row without a date, a lab or a share in 0-100")
    if spend.duplicated(["date", "name"]).any():
        raise ValueError("Vercel export: two spend shares for one lab on one day")
    info = {key: data.get(key) for key in ("license", "license_url", "from", "to")}
    return spend.sort_values(["date", "name"]).reset_index(drop=True), info


def labs_spend(start: date, end: date) -> tuple[pd.DataFrame, dict]:
    """Every lab's daily share of spend from start to end in Vercel's export, and the
    request's metadata with the export's licence and `built_at`, when Vercel built the
    answer: when it was retrieved, less the Age its cache reports, or less CACHED_FOR if it
    reports none."""
    query = {
        "dataset": "labs",
        "modality": "all",
        "format": "json",
        "from": start.isoformat(),
        "to": end.isoformat(),
    }
    body, meta = _get(f"{EXPORT}?{urllib.parse.urlencode(query)}")
    spend, info = parse_export(body)
    age = timedelta(seconds=meta["age"]) if "age" in meta else CACHED_FOR
    built_at = datetime.fromisoformat(meta["retrieved_at"]) - age
    return spend, meta | info | {"built_at": built_at}


def lab_share(spend: pd.DataFrame) -> pd.Series:
    """Anthropic's daily share of spend, by date. Raises ValueError if the export has none, as
    when Vercel renames the lab."""
    rows = spend[spend["name"] == LAB]
    if rows.empty:
        raise ValueError(f"Vercel export: no spend shares for {LAB!r}")
    return rows.set_index("date")["share_percent"].astype(float)


def _complete(days: pd.Series | pd.DatetimeIndex, built_at: datetime, wait: timedelta):
    """Whether each UTC day had ended at least `wait` before built_at."""
    latest = built_at.astimezone(UTC) - timedelta(days=1) - wait  # the latest such day starts
    return days <= pd.Timestamp(latest.replace(tzinfo=None))


def _store(spend: pd.DataFrame, built_at: datetime) -> None:
    """Keep the complete days of the last export read on the host, for an outage."""
    path = CACHE / f"labs_spend.{CACHE_VERSION}.csv"
    CACHE.mkdir(parents=True, exist_ok=True)
    spend[COLUMNS].assign(built_at=built_at.isoformat()).to_csv(
        path.with_suffix(".tmp"), index=False, date_format="%Y-%m-%d"
    )
    path.with_suffix(".tmp").replace(path)


def _stored() -> tuple[pd.DataFrame, str] | None:
    """The host's copy of the last export read, and when Vercel built it; None if there is
    none."""
    path = CACHE / f"labs_spend.{CACHE_VERSION}.csv"
    if not path.is_file():
        return None
    frame = pd.read_csv(path, dtype={"name": str, "built_at": str}, parse_dates=["date"])
    if frame.empty:
        return None
    return frame[COLUMNS], frame["built_at"].iloc[0]


def snapshot(run_date: date, spec: dict) -> dict:
    """X_d as published right now: one row per UTC day from history_start to the last target
    date, `target` empty after the last day the export holds complete (normally the day before
    the run date), the day of the week for every day, and Kalshi's earlier median (see
    kalshi_as_seen). If the export cannot be read, the host's copy from the last snapshot
    stands in; if Kalshi cannot be read, X_d has no Kalshi column. sources and a warning say
    so: data that only the TSFMs see must not cost the agents' run day."""
    start = date.fromisoformat(spec["x"]["history_start"])
    last_target = run_date + timedelta(days=max(spec["horizons"]))
    before_run = pd.Timestamp(run_date)
    try:
        spend, meta = labs_spend(start, run_date)
        complete = _complete(spend["date"], meta["built_at"], SETTLED)
        spend = spend[complete & (spend["date"] < before_run)]
        values = lab_share(spend)
        source = meta | {"built_at": meta["built_at"].isoformat()}
        try:
            _store(spend, meta["built_at"])
        except OSError as exc:  # a full disk must not cost the X_d just read
            print(f"warning: host copy of Vercel's export not written: {exc!r}", file=sys.stderr)
    except FAILURES as exc:
        stored = _stored()
        if stored is None:
            raise RuntimeError("Vercel's export failed and the host has no copy of it") from exc
        spend, built = stored
        spend = spend[spend["date"] < before_run]
        values = lab_share(spend)
        error = f"the export failed, the host's copy built {built} stands in: {exc!r}"
        print(f"warning: {error}", file=sys.stderr)
        source = {"error": error, "built_at": built}
    last_observed = values.index.max()
    index = pd.date_range(start, last_target, freq="D", name="timestamp")
    # a day missing between two complete days is filled linearly (truth leaves it empty)
    target = values.reindex(index).interpolate(limit_area="inside")
    series = pd.DataFrame({"target": target, "dow": index.dayofweek}, index=index)
    used = spend[(spend["date"] >= pd.Timestamp(start)) & (spend["date"] <= last_observed)]
    raw = {"vercel_labs_spend.csv": used[COLUMNS].reset_index(drop=True)}
    notes = {
        "target": "Anthropic's share, in percent, of all AI Gateway spend on the UTC day, "
        "from the labs leaderboard (spend) in Vercel's open-data export, for every day that "
        "had ended at least 12 hours before Vercel built the answer. A day missing between "
        "two such days is filled linearly",
        "dow": "day of the week of the UTC day, Monday = 0",
    }
    kalshi_source = {"url": f"{KALSHI}/markets?series_ticker={SERIES}"}
    try:
        kalshi, taken = kalshi_as_seen(index, target, last_observed, run_date, spec)
        kalshi_source["readings"] = len(taken)
    except FAILURES as exc:
        kalshi = None
        kalshi_source["error"] = f"Kalshi not read, X_d has no Kalshi column: {exc!r}"
        print(f"warning: {kalshi_source['error']}", file=sys.stderr)
    if kalshi is not None:
        series["kalshi"] = kalshi
        raw["kalshi_quotes.csv"] = pd.DataFrame(
            [
                {"target_date": r["target_date"], "read_at": r["read_at"], "median": r["point"]}
                | rung
                for r in taken
                for rung in r["ladder"]
            ]
        )
        notes["kalshi"] = (
            "median of Kalshi's KXANTHVSPEND market for the day, read one cycle before B's own "
            "reading: for past days at the cutoff two days before, for the window at the previous "
            "run day's cutoff, carried forward to the days it does not reach; a past day without "
            "a reading carries the day before's share"
        )
    elif "error" not in kalshi_source:
        kalshi_source["note"] = "no day of the window has a reading, so X_d has no Kalshi column"
    return {
        "series": series,
        "last_observed": last_observed.date(),
        "raw": raw,
        "sources": {
            "vercel": source
            | {
                "page": LEADERBOARD,
                "docs": EXPORT_DOCS,
                "days": int(len(values)),
                "use": "Anthropic's share of spend on each UTC day that had ended at least 12 "
                "hours before Vercel built the answer",
            },
            "kalshi": kalshi_source,
        },
        "covariate_notes": notes,
    }


def truth(today: date, spec: dict) -> pd.Series:
    """Every day's share once a full day had passed since its UTC day ended when Vercel built
    the answer: at the 16:45 timer, up to the day before yesterday. It asks for the same range
    as the snapshot of the same day, so both read one answer. The pipeline keeps the first
    value it sees for a day. If the export cannot be read, nothing is recorded today and a
    warning is printed, so that the day's run goes on."""
    start = date.fromisoformat(spec["x"]["history_start"])
    try:
        spend, meta = labs_spend(start, today)
        values = lab_share(spend)
    except FAILURES as exc:
        print(
            f"warning: Vercel's export not read, no truth recorded today: {exc!r}", file=sys.stderr
        )
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    return values[_complete(values.index, meta["built_at"], FULL_DAY)]


def event_ticker(day: date) -> str:
    """Kalshi's KXANTHVSPEND event for a date, such as KXANTHVSPEND-02NOV26."""
    return f"{SERIES}-{day:%d}{MONTHS[day.month - 1]}{day:%y}"


def _rule_date(day: date) -> str:
    """A date as Kalshi's rules write it, such as Nov 2, 2026."""
    return f"{MONTHS[day.month - 1].title()} {day.day}, {day.year}"


def series_markets() -> dict[str, list[dict]]:
    """Every KXANTHVSPEND market Kalshi lists, by event ticker. Kalshi moves older markets from
    /markets to /historical/markets, so both are read."""
    found: dict[str, dict] = {}
    for path in ("markets", "historical/markets"):
        query = {"series_ticker": SERIES, "limit": 1000}
        while True:
            body, _ = _get(f"{KALSHI}/{path}?{urllib.parse.urlencode(query)}")
            page = json.loads(body)
            for market in page.get("markets") or []:
                found.setdefault(market["ticker"], market)
            if not page.get("cursor"):
                break
            query["cursor"] = page["cursor"]
    events: dict[str, list[dict]] = {}
    for market in found.values():
        events.setdefault(market["event_ticker"], []).append(market)
    return events


def ladder(markets: list[dict], day: date) -> list[dict]:
    """The date's contracts, "above `strike`%" each, by strike. Raises ValueError if any is
    not such a contract on the date's share rounded to one decimal."""
    rungs = []
    for market in markets:
        strike = market.get("floor_strike")
        number = (
            isinstance(strike, int | float)
            and not isinstance(strike, bool)
            and math.isfinite(strike)
        )
        rule = RULE.fullmatch(str(market.get("rules_primary") or ""))
        if (
            market.get("event_ticker") != event_ticker(day)
            or market.get("market_type") != "binary"
            or market.get("strike_type") != "greater"
            or not number
            or not rule
            or rule["day"] != _rule_date(day)
            or float(rule["strike"]) != float(strike)
            or not str(market.get("rules_secondary") or "").startswith(ROUNDING_RULE)
        ):
            raise ValueError(
                f"{market.get('ticker')}: not a {SERIES} contract on {day} as expected"
            )
        rungs.append({"ticker": market["ticker"], "strike": float(strike)})
    if len({r["strike"] for r in rungs}) != len(rungs):
        raise ValueError(f"{event_ticker(day)}: two contracts share a strike")
    return sorted(rungs, key=lambda r: r["strike"])


def last_candles(
    event: str, tickers: list[str], opened: datetime, cutoff: datetime
) -> tuple[dict[str, dict], dict]:
    """Each market's last one-minute candle ending by the cutoff, and the last request's
    metadata. A candle appears only when a market's quotes or trades change, so the last one
    holds its quotes at the cutoff. Kalshi answers at most CANDLE_LIMIT candles a request
    across the event's markets and cuts a longer window short, so the window is read
    backwards from the cutoff in pieces that fit, until every market has a candle or the
    markets' open is reached."""
    span = max(1, CANDLE_LIMIT // len(tickers)) * 60
    first, end = int(opened.timestamp()), int(cutoff.timestamp())
    last: dict[str, dict] = {}
    meta: dict = {}
    while end > first and len(last) < len(tickers):
        start = max(first, end - span)
        query = urllib.parse.urlencode({"start_ts": start, "end_ts": end, "period_interval": 1})
        body, meta = _get(f"{KALSHI}/series/{SERIES}/events/{event}/candlesticks?{query}")
        payload = json.loads(body)
        if payload.get("adjusted_end_ts", end) < end:
            raise ValueError(f"{event}: Kalshi cut the candlesticks short of {end}")
        pages = zip(payload["market_tickers"], payload["market_candlesticks"], strict=True)
        for ticker, candles in pages:
            ended = [c for c in candles or [] if c["end_period_ts"] <= cutoff.timestamp()]
            if ticker in tickers and ticker not in last and ended:
                last[ticker] = max(ended, key=lambda c: c["end_period_ts"])
        end = start
    return last, meta


def quote(candle: dict | None, max_spread: float) -> dict:
    """A contract's best bid and ask in its last candle, and their midpoint if both are
    quoted, not crossed and at most max_spread apart (None otherwise)."""
    if candle is None:
        return {"bid": None, "ask": None, "quoted_at": None, "mid": None}
    bid = float((candle.get("yes_bid") or {}).get("close_dollars") or 0)
    ask = float((candle.get("yes_ask") or {}).get("close_dollars") or 1)
    # prices are whole cents: the tolerance keeps a spread of exactly max_spread
    usable = 0 < bid <= ask < 1 and ask - bid <= max_spread + 1e-9
    return {
        "bid": bid,
        "ask": ask,
        "quoted_at": datetime.fromtimestamp(candle["end_period_ts"], UTC).isoformat(),
        "mid": round((bid + ask) / 2, 4) if usable else None,
    }


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


def implied_median(strikes: list[float], mids: list[float | None]) -> dict:
    """The market's median of the share. mids[i] is the price of "above strikes[i]", None if
    unquoted; it is read as the probability that the share is at least strikes[i] + ROUNDING.
    The quoted prices, made non-increasing, are joined linearly between their strikes, and the
    median is where they cross one half (the middle of the range where they equal it, if they
    do on a range). It exists only when the quoted strikes bracket it."""
    quoted = [i for i, mid in enumerate(mids) if mid is not None]
    fitted = _non_increasing([mids[i] for i in quoted])
    curve = [(strikes[i] + ROUNDING, p) for i, p in zip(quoted, fitted, strict=True)]
    result = {
        "point": None,
        "fitted": [None] * len(strikes),
        "bracket": None,
        "reason": None,
    }
    for i, p in zip(quoted, fitted, strict=True):
        result["fitted"][i] = round(p, 4)
    if not curve:
        return result | {"reason": "no contract is quoted within max_quote_spread"}
    if curve[0][1] < 0.5:
        return result | {
            "reason": "the market puts more than half its probability below the lowest quoted "
            f"strike, {strikes[quoted[0]]:g}%"
        }
    if curve[-1][1] > 0.5:
        return result | {
            "reason": "the market puts more than half its probability above the highest quoted "
            f"strike, {strikes[quoted[-1]]:g}%"
        }
    crossings = [curve[0][0]] if len(curve) == 1 else []  # a single quote of exactly 0.5
    for (low, p_low), (high, p_high) in pairwise(curve):
        if p_low >= 0.5 >= p_high:
            if p_low == p_high:
                crossings += [low, high]
            else:
                crossings.append(low + (p_low - 0.5) / (p_low - p_high) * (high - low))
    point = (min(crossings) + max(crossings)) / 2
    # the quoted strikes on either side of the median
    below = max(strikes[i] for i in quoted if strikes[i] + ROUNDING <= point + 1e-9)
    above = min(strikes[i] for i in quoted if strikes[i] + ROUNDING >= point - 1e-9)
    return result | {"point": point, "bracket": [below, above]}


def _local(day: date, clock: str, timezone: str) -> datetime:
    """`clock` ("HH:MM") on `day`, local time."""
    hour, minute = map(int, clock.split(":"))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZoneInfo(timezone))


def market_median(day: date, at: datetime, markets: list[dict], spread: float) -> dict:
    """The median the date's KXANTHVSPEND event implied at `at`, from each contract's last
    one-minute candle by then, with the quotes it came from. `point` is None, with the
    reason, if the event had not opened or its quotes do not bracket the median."""
    event = event_ticker(day)
    rungs = ladder(markets, day)
    opened = [datetime.fromisoformat(m["open_time"]) for m in markets if m.get("open_time")]
    if not opened:
        raise ValueError(f"{event}: Kalshi gives no open_time for its markets")
    reading = {"target_date": day.isoformat(), "event_ticker": event, "read_at": at.isoformat()}
    if min(opened) >= at:
        reason = f"{event} opened after {at.isoformat()}"
        return reading | {"point": None, "bracket": None, "reason": reason, "ladder": []}
    candles, meta = last_candles(event, [r["ticker"] for r in rungs], min(opened), at)
    quotes = [quote(candles.get(r["ticker"]), spread) for r in rungs]
    median = implied_median([r["strike"] for r in rungs], [q["mid"] for q in quotes])
    return reading | {
        "point": None if median["point"] is None else round(median["point"], 2),
        "bracket": median["bracket"],
        "reason": median["reason"],
        "ladder": [
            rung | q | {"fitted": fitted}
            for rung, q, fitted in zip(rungs, quotes, median["fitted"], strict=True)
        ],
        "retrieved_at": meta.get("retrieved_at"),
    }


def readings(wanted: list[tuple[date, datetime]], spread: float) -> list[dict | None]:
    """market_median for each (date, moment) wanted, None for a date Kalshi lists no event for.
    A reading of a past moment never changes, so each is kept on the host once taken and
    Kalshi is asked only for the others."""
    path = CACHE / f"kalshi_readings.{CACHE_VERSION}.json"
    kept = json.loads(path.read_text()) if path.is_file() else {}
    keys = [f"{event_ticker(day)} {at.isoformat()}" for day, at in wanted]
    missing = [(key, *w) for key, w in zip(keys, wanted, strict=True) if key not in kept]
    if missing:
        if max(at for _, _, at in missing) > _now():
            raise ValueError("Kalshi: a reading of a moment still to come")
        markets = series_markets()
        for key, day, at in missing:
            if listed := markets.get(event_ticker(day)):
                kept[key] = market_median(day, at, listed, spread)
                try:
                    CACHE.mkdir(parents=True, exist_ok=True)
                    path.with_suffix(".tmp").write_text(json.dumps(kept))
                    path.with_suffix(".tmp").replace(path)
                except OSError as exc:  # a full disk must not cost the reading just taken
                    print(f"warning: Kalshi reading not kept on the host: {exc!r}", file=sys.stderr)
    return [kept.get(key) for key in keys]


def kalshi_as_seen(
    index: pd.DatetimeIndex,
    target: pd.Series,
    last_observed: pd.Timestamp,
    run_date: date,
    spec: dict,
) -> tuple[pd.Series | None, list[dict]]:
    """Kalshi's median for each day on `index` one cycle before B's own reading, and the
    readings taken: for a past day, at the cutoff two days before it (the run day before the
    one whose first target date it is); for the window, at the previous run day's cutoff,
    carried forward to the days it does not reach (Kalshi opens an event about four days
    ahead, for even dates only). A past day without a reading carries the day before's share,
    a no-change forecast. The series is None if no day of the window has a reading."""
    timezone, clock = spec["schedule"]["timezone"], spec["schedule"]["cutoff"]
    wanted = []
    for d in index:
        compared_on = d.date() - timedelta(days=1) if d <= last_observed else run_date
        wanted.append((d.date(), _local(compared_on - timedelta(days=1), clock, timezone)))
    taken = readings(wanted, spec["baseline"]["max_quote_spread"])
    seen = pd.Series(
        [r["point"] if r and r["point"] is not None else math.nan for r in taken],
        index=index,
        name="kalshi",
        dtype=float,
    )
    past = index <= last_observed
    taken = [r for r in taken if r]
    if seen[~past].isna().all():
        return None, taken
    seen[past] = seen[past].fillna(target.shift(1)[past]).fillna(target[past])
    seen[~past] = seen[~past].ffill().fillna(target[past].iloc[-1])
    return seen, taken


def baseline(run_date: date, spec: dict) -> dict:
    """The median of Kalshi's market on each target date that has a KXANTHVSPEND event, as it
    stood at the cutoff, read the next day from its one-minute candlesticks. Kalshi lists even
    dates only, each from about four days ahead, so normally two of the seven target dates
    have one. Empty before the cutoff; a date without an event, or whose quotes do not
    bracket the median, has no forecast, and the reason is printed for the daily log too."""
    timezone, clock = spec["schedule"]["timezone"], spec["schedule"]["cutoff"]
    cutoff = _local(run_date, clock, timezone)
    days = [run_date + timedelta(days=h) for h in spec["horizons"]]
    source = {
        "report": f"Kalshi {SERIES}, quotes at {clock} ET on {run_date.isoformat()}",
        "posted": None,
        "model": f"Kalshi {SERIES}",
    }
    if _now() < cutoff:
        print(f"note: no Kalshi baseline for {run_date}: before the cutoff", file=sys.stderr)
        return {"source": source | {"reason": "before the cutoff"}, "forecasts": []}
    spread = spec["baseline"]["max_quote_spread"]
    taken = readings([(day, cutoff) for day in days], spread)
    forecasts, read = [], {}
    for day, reading in zip(days, taken, strict=True):
        if reading is None:
            even = "; Kalshi lists this series for even dates only" if day.day % 2 else ""
            reading = {"point": None, "reason": f"{event_ticker(day)} is not listed{even}"}
        if reading["point"] is None:
            print(
                f"note: no Kalshi baseline for {day} on {run_date}: {reading['reason']}",
                file=sys.stderr,
            )
        else:
            forecasts.append({"target_date": day.isoformat(), "point": reading["point"]})
        read[day.isoformat()] = reading
    return {
        "source": source
        | {
            "posted": cutoff.isoformat(),
            "method": METHOD,
            "max_quote_spread": spread,
            "readings": read,
            "contract_terms_url": CONTRACT_TERMS,
        },
        "forecasts": forecasts,
    }
