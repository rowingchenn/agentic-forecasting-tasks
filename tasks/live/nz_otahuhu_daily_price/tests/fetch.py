"""Host-only New Zealand wholesale electricity price at Otahuhu (OTA2201): X_d, first-published
truth, and B, the pricing manager's PRSL forecast of the next day.

The agentic-forecasting pipeline imports this file on the host, outside every episode, with
the standard library and pandas only.

    snapshot(run_date, spec)  X_d: the daily mean price at OTA2201 since 2023, the calendar, and
                              the PRSL forecast issued the day before (expert)
    truth(today, spec)        the interim prices EMI first publishes for each day, and EMI's
                              single-node report for days the pipeline did not read first-hand
    baseline(run_date, spec)  the PRSL forecast of the next day, from the first run at or after
                              the cutoff that covers all of that day
    backfill(today, spec)     PRSL forecasts of past days for the expert column; each snapshot
                              reads a few, and the maintainer can fill the year once (below)

Data: prices from the Electricity Authority's EMI website, credited as "Electricity Authority,
EMI (www.emi.ea.govt.nz)"; the Authority licenses its own works under CC BY 4.0, with
exclusions. B and the expert column are the pricing manager's forecast prices, credited as
"Forecast prices prepared by the pricing manager, published by the Electricity Authority on
EMI (www.emi.ea.govt.nz)". No key is needed; EMI has no robots.txt (404). Every request names
the client and comes at least a second after the one before; a request that times out is not
asked again, and the rest of the process asks EMI nothing (truth, baseline and snapshot run as
separate processes), so the host copies answer instead.

- Interim prices of day D: Wholesale/Datasets/DispatchAndPricing/FinalEnergyPrices/
  D_FinalEnergyPrices_I.csv. EMI posts its price files in one batch at about 07:05 NZ local
  time: the interim file of D the next morning, and the final file of D once the prices are
  declared final, which replaces it. For a Monday-to-Thursday trading day that is the next
  batch, so the interim file is up for about 24 hours; at weekends and holidays, longer.
- History: EMI's single-node report for OTA2201 (Wholesale/Reports/CLA3WR) as CSV, each
  half-hour with its status, Interim or Final. Its final prices equal EMI's monthly final price
  files (17,520 half-hours of 2025-10 to 2026-10 compared, none differ), at about 35 bytes a
  half-hour against 10 MB for a month of those files. It is read in full once, then for the
  last four weeks.
- B: Wholesale/Datasets/ForecastPrices/PRSL/YYYY/PRSLYYYYMMDD.zip, the day's twelve runs of the
  long price-responsive schedule, about 1 MB, posted at 16:10 UTC that day: 05:10 NZDT or
  04:10 NZST the next morning.

Kept on the host, in ~/.cache/agentic-forecasting/nz_otahuhu_daily_price/: the report's
complete days, the first print read for each day, and the B read for each run date.

Before the first run day, the maintainer can fill the expert column's year once, 366 zips
(about 370 MB, half an hour), from the pipeline repository:

    uv run python -c "from af.archive import spec, today; from af.target import fetch; \\
        t = 'nz_otahuhu_daily_price'; print(fetch(t).backfill(today(t), spec(t)))"

Without it, each snapshot reads up to ten runs not kept yet, newest first, until the year is
complete.
"""

from __future__ import annotations

import csv
import io
import math
import os
import re
import sys
import time
import zipfile
import zlib
from collections import defaultdict
from datetime import date, datetime, timedelta
from datetime import time as clock
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd

TARGET = "nz_otahuhu_daily_price"
NODE = "OTA2201"
ZONE = ZoneInfo("Pacific/Auckland")
EMI = "https://www.emi.ea.govt.nz/Wholesale"
PRICES_PAGE = EMI + "/Datasets/DispatchAndPricing/FinalEnergyPrices"
INTERIM = PRICES_PAGE + "/{day:%Y%m%d}_FinalEnergyPrices_I.csv"
REPORT_PAGE = EMI + "/Reports/CLA3WR"
REPORT = (
    EMI + "/Download/DataReport/CSV/CLA3WR?DateFrom={first:%Y%m%d}&DateTo={last:%Y%m%d}"
    "&POC=" + NODE + "&_si=v%7C3"
)
PRSL_PAGE = EMI + "/Datasets/ForecastPrices/PRSL"
PRSL = PRSL_PAGE + "/{day:%Y}/PRSL{day:%Y%m%d}.zip"
PRICE_CREDIT = "Electricity Authority, EMI (www.emi.ea.govt.nz)"
PRSL_CREDIT = (
    "Forecast prices prepared by the pricing manager, published by the Electricity Authority on "
    "EMI (www.emi.ea.govt.nz)"
)
USER_AGENT = "agentic-forecasting/1.0"
TRIES = 4  # requests per file, after server errors, dropped connections and pages that do not parse
TIMEOUT = 20  # seconds for a request: a price file, a PRSL zip, the report's last weeks
FULL_TIMEOUT = 120  # seconds for the report's first reading, the whole history (about 10 s)
PAUSE = 1.0  # seconds from one request to EMI to the next
LATE_TRIES = 1  # asks again once for yesterday's interim prices when the snapshot finds none,
LATE_WAIT = 60  # a minute later: EMI posts them at about 07:05, so at 10:10 they are hours late
DEADLINE = timedelta(minutes=100)  # from this long before the cutoff the snapshot no longer
# waits or reads past PRSL runs, leaving the agents their 90 minutes and the sandbox's setup
PENDING = 7  # days back that truth asks for interim prices not read yet
REFRESH = 28  # days of the report read again each time, where prices are declared final
REUSE = timedelta(hours=1)  # the report is read at most once an hour
EXPERT_DAYS = 365  # days back the expert column holds PRSL forecasts
BACKFILL = 10  # PRSL runs a snapshot reads at most ...
BUDGET = 90  # ... and seconds it spends on them
CACHE_VERSION = "v1"  # in the host files' names; raise it when a file's meaning changes
FAILURES = (OSError, HTTPException, ValueError, csv.Error)
REPORT_HEADER = "Point of connection,Trading date,Trading period,$/MWh,Price status"
PRICES_HEADER = ["TradingDate", "TradingPeriod", "PointOfConnection", "DollarsPerMegawattHour"]
RUN_NAME = re.compile(r"PRSL(\d{8})(\d{6})\.csv")
REPORT_COLUMNS = ["date", "price", "periods", "status", "read_at"]
PRINT_COLUMNS = ["date", "price", "periods", "source", "read_at"]
PRSL_COLUMNS = ["run_date", "run", "issued", "target_date", "forecast", "periods", "read_at"]
_last_request = -math.inf  # time.monotonic() when the last request to EMI ended
_unresponsive = False  # set at the first timeout: the rest of the process asks EMI nothing


def _now() -> datetime:
    return datetime.now(ZONE)


def _warn(message: str) -> None:
    print(f"{TARGET}: {message}", file=sys.stderr)


def half_hours(day: date) -> int:
    """Trading periods in a New Zealand day: 48, or 46 and 50 when the clocks change. Aware
    datetimes in one zone subtract as wall-clock times, so the length comes from timestamps."""
    start = datetime.combine(day, clock(), ZONE).timestamp()
    end = datetime.combine(day + timedelta(days=1), clock(), ZONE).timestamp()
    return round((end - start) / 1800)


def _cutoff(run_date: date, spec: dict) -> datetime:
    if spec["schedule"]["timezone"] != "Pacific/Auckland":
        raise ValueError("EMI's trading days and run times are New Zealand time.")
    return datetime.combine(run_date, clock.fromisoformat(spec["schedule"]["cutoff"]), ZONE)


def _guard(run_date: date, spec: dict) -> datetime:
    now = _now()
    if now.date() != run_date or now >= _cutoff(run_date, spec):
        raise RuntimeError("Fresh snapshots require the current day before the cutoff.")
    return now


def _timed_out(exc: BaseException) -> bool:
    return isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError)


def _fetch(url: str, parse, timeout: float = TIMEOUT):
    """parse(body) of the response, asking again after server errors, dropped connections and
    a body that does not parse (a maintenance page served with status 200), up to TRIES
    times. A rate limit (429) or another client error is not asked again. Nor is a timeout,
    after which the rest of the process asks EMI nothing, so that a hanging EMI costs each of
    the pipeline's steps one timeout."""
    global _last_request, _unresponsive
    if _unresponsive:
        raise TimeoutError("EMI timed out earlier in this run and is not asked again")
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    for attempt in range(TRIES):
        wait = PAUSE - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        try:
            with urlopen(Request(url, headers=headers), timeout=timeout) as response:
                return parse(response.read())
        except HTTPError as exc:
            if exc.code not in (500, 502, 503, 504) or attempt == TRIES - 1:
                raise
            retry = str(exc.headers.get("Retry-After", "")) if exc.headers else ""
            delay = min(120, int(retry)) if retry.isdigit() else 2**attempt
        except FAILURES as exc:
            if _timed_out(exc):
                _unresponsive = True
                raise
            if attempt == TRIES - 1:
                raise
            delay = 2**attempt
        finally:
            _last_request = time.monotonic()
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


def _load(path: Path, columns: list[str], check) -> pd.DataFrame:
    """A host file as text; a damaged one is set aside and an empty table returned."""
    if not path.is_file():
        return pd.DataFrame(columns=columns, dtype=str)
    try:
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        if list(frame.columns) != columns:
            raise ValueError(f"columns {list(frame.columns)}")
        check(frame)
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        damaged = path.with_suffix(".damaged")
        _warn(f"the kept file {path.name} is damaged ({exc!r}); set aside as {damaged.name}")
        try:
            path.replace(damaged)
        except OSError as err:
            _warn(f"could not set the damaged file aside: {err!r}")
        return pd.DataFrame(columns=columns, dtype=str)
    return frame


def _numbers(values: pd.Series) -> None:
    if not all(math.isfinite(v) for v in pd.to_numeric(values)):
        raise ValueError("a number is not finite")


def _times(values: pd.Series) -> None:
    for value in values:
        datetime.fromisoformat(value)


# --- EMI's single-node report: the history -------------------------------------------------------


def _parse_report(body: bytes) -> pd.DataFrame:
    """EMI's single-node report as one row per complete trading day: the date, the mean price,
    the number of half-hours and the status, Final when every half-hour is. A half-hour listed
    both as Interim and as Final (all 48 of 2023-03-16, at equal prices) counts once, as Final;
    a day without exactly its trading periods 1 to 46, 48 or 50 is left out with a warning."""
    lines = body.decode("utf-8-sig", errors="replace").splitlines()
    try:
        head = lines.index(REPORT_HEADER)
    except ValueError:
        raise ValueError("EMI's single-node report has changed, or this is not one") from None
    seen: dict[tuple[date, int], dict[str, float]] = defaultdict(dict)
    clashes = set()
    for row in csv.reader(lines[head + 1 :]):
        if not row:
            continue
        if len(row) != 5 or row[0] != NODE or row[4] not in ("Interim", "Final"):
            raise ValueError(f"EMI's single-node report has the row {row!r}")
        day = datetime.strptime(row[1], "%d/%m/%Y").date()
        period, price = int(row[2]), float(row[3])
        if not math.isfinite(price):
            raise ValueError(f"EMI's single-node report has the row {row!r}")
        if seen[(day, period)].get(row[4], price) != price:
            clashes.add(day)
        seen[(day, period)][row[4]] = price
    if not seen:
        raise ValueError("EMI's single-node report has no prices")
    by_day: dict[date, dict[int, tuple[float, str]]] = defaultdict(dict)
    for (day, period), states in seen.items():
        status = "Final" if "Final" in states else "Interim"
        by_day[day][period] = (states[status], status)
    rows = []
    for day in sorted(by_day):
        prices, need = by_day[day], half_hours(day)
        if day in clashes or sorted(prices) != list(range(1, need + 1)):
            _warn(
                f"EMI's single-node report has {len(prices)} of the {need} half-hours of "
                f"{day}, or two prices for one; the day is left out"
            )
            continue
        final = all(status == "Final" for _, status in prices.values())
        mean = sum(price for price, _ in prices.values()) / need
        rows.append((day.isoformat(), round(mean, 6), need, "Final" if final else "Interim"))
    return pd.DataFrame(rows, columns=REPORT_COLUMNS[:4])


def _report_url(first: date, last: date) -> str:
    """The single-node report of OTA2201 for the trading days from `first` to `last`, as CSV."""
    return REPORT.format(first=first, last=last)


def _report_file() -> Path:
    return _host_file(f"report.{CACHE_VERSION}.csv")


def _check_report(frame: pd.DataFrame) -> None:
    pd.to_datetime(frame["date"], format="%Y-%m-%d")
    _numbers(frame["price"])
    frame["periods"].astype(int)
    if not frame["status"].isin(["Interim", "Final"]).all():
        raise ValueError("a status is neither Interim nor Final")
    _times(frame["read_at"])


def _report_kept() -> pd.DataFrame:
    frame = _load(_report_file(), REPORT_COLUMNS, _check_report)
    return frame.astype({"price": float, "periods": int}).sort_values("date", ignore_index=True)


def _report(today: date, spec: dict) -> pd.DataFrame:
    """Every complete day of the single-node report from x.history_start to the day before
    `today`, as published now. The first reading takes the whole span, later ones the last four
    weeks, where prices are declared final, and any gap since the newest day kept; a day they
    do not have complete keeps the value kept for it on the host. The copy also answers, with a
    warning, when the report does not; it is read at most once an hour."""
    kept = _report_kept()
    now = _now()
    first = date.fromisoformat(spec["x"]["history_start"])
    if not kept.empty:
        read = max(datetime.fromisoformat(t) for t in kept["read_at"])
        if timedelta(0) <= now - read < REUSE and read.date() == now.date():
            return kept
        newest = date.fromisoformat(kept["date"].iloc[-1])
        first = max(first, min(today - timedelta(days=REFRESH), newest + timedelta(days=1)))
    url = _report_url(first, today - timedelta(days=1))
    try:
        fresh = _fetch(url, _parse_report, timeout=FULL_TIMEOUT if kept.empty else TIMEOUT)
    except FAILURES as exc:
        if kept.empty:
            raise
        _warn(
            f"EMI's single-node report was not read ({exc!r}); using the copy kept on the "
            f"host, to {kept['date'].iloc[-1]}"
        )
        return kept
    fresh["read_at"] = now.isoformat(timespec="seconds")
    kept = kept[~kept["date"].isin(fresh["date"])]
    merged = pd.concat([kept, fresh], ignore_index=True) if not kept.empty else fresh
    merged = merged.sort_values("date", ignore_index=True)
    _write(_report_file(), merged.to_csv(index=False))
    return merged


def _report_days(report: pd.DataFrame) -> dict[date, dict]:
    return {date.fromisoformat(r["date"]): r for r in report.to_dict("records")}


# --- interim prices: the first print ------------------------------------------------------------


def _parse_prices(body: bytes) -> dict[date, dict[int, float]]:
    """An EMI price file: OTA2201's price for each trading period, by trading day."""
    reader = csv.reader(io.StringIO(body.decode("utf-8-sig", errors="replace")))
    if next(reader, None) != PRICES_HEADER:
        raise ValueError("EMI's price file has changed, or this is not one")
    found: dict[date, dict[int, float]] = defaultdict(dict)
    for row in reader:
        if len(row) != 4 or row[2] != NODE:
            continue
        day, period, price = date.fromisoformat(row[0]), int(row[1]), float(row[3])
        if period in found[day] or not math.isfinite(price):
            raise ValueError(f"EMI's price file has the row {row!r}")
        found[day][period] = price
    return dict(found)


def _read_first_print(day: date, report: pd.DataFrame) -> dict | None:
    """The day's first published value: the mean of its interim prices, when EMI has the file
    with every half-hour of the day. Once EMI has replaced the file with the final one, the
    report's final prices stand in, with a warning. None while neither is there; a failed
    request raises."""
    try:
        found = _fetch(INTERIM.format(day=day), _parse_prices)
    except HTTPError as exc:
        if exc.code != 404:
            raise
        final = _report_days(report).get(day)
        if final is None or final["status"] != "Final":
            return None
        _warn(
            f"the interim prices of {day} were not read before EMI declared them final; the "
            "day settles on the final prices"
        )
        return {"price": final["price"], "periods": final["periods"], "source": "final"}
    prices, need = found.get(day, {}), half_hours(day)
    if sorted(prices) != list(range(1, need + 1)):
        _warn(f"EMI's interim prices of {day} cover {len(prices)} of its {need} half-hours")
        return None
    return {"price": round(sum(prices.values()) / need, 6), "periods": need, "source": "interim"}


def _prints_file() -> Path:
    return _host_file(f"first_prints.{CACHE_VERSION}.csv")


def _check_prints(frame: pd.DataFrame) -> None:
    pd.to_datetime(frame["date"], format="%Y-%m-%d")
    _numbers(frame["price"])
    frame["periods"].astype(int)
    if not frame["source"].isin(["interim", "final"]).all():
        raise ValueError("a source is neither interim nor final")
    _times(frame["read_at"])


def _prints() -> dict[date, dict]:
    """The first print kept for each day."""
    frame = _load(_prints_file(), PRINT_COLUMNS, _check_prints)
    return {
        date.fromisoformat(r["date"]): {
            "price": float(r["price"]),
            "periods": int(r["periods"]),
            "source": r["source"],
            "read_at": r["read_at"],
        }
        for r in frame.to_dict("records")
    }


def _remember_print(day: date, found: dict) -> dict[date, dict]:
    """Keep a day's first print; one kept is never replaced."""
    prints = _prints()
    if day not in prints:
        prints[day] = found | {"read_at": _now().isoformat(timespec="seconds")}
        rows = [{"date": d.isoformat(), **prints[d]} for d in sorted(prints)]
        _write(_prints_file(), pd.DataFrame(rows, columns=PRINT_COLUMNS).to_csv(index=False))
    return prints


# --- the PRSL schedule: B and the expert column --------------------------------------------------


def _parse_zip(body: bytes) -> zipfile.ZipFile:
    """A day's PRSL zip, with every member's checksum verified."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(body))
        damaged = archive.testzip()
    except (zipfile.BadZipFile, zlib.error, EOFError) as exc:
        raise ValueError(f"the PRSL zip does not unpack: {exc}") from exc
    if damaged is not None:
        raise ValueError(f"{damaged} in the PRSL zip is damaged")
    return archive


def _forecast_prices(text: str, day: date) -> dict[int, float]:
    """A PRSL run's forecast prices at OTA2201 for one trading day, by trading period. Rows read
    `node,dd/mm/yyyy,period,price,run time,G,flag`."""
    wanted, prices = day.strftime("%d/%m/%Y"), {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 4 or row[0] != NODE or row[1] != wanted:
            continue
        period, price = int(row[2]), float(row[3])
        if period in prices or not math.isfinite(price):
            raise ValueError(f"the PRSL row {row!r} is not read")
        prices[period] = price
    return prices


def prsl_issue(run_date: date, spec: dict) -> dict | None:
    """B of a run date: of the PRSL runs stamped at or after the cutoff, the first with a
    forecast price at OTA2201 for every trading period of the next day, and the mean of those
    prices. The 12:10 run reaches the end of the next day; before a 50-half-hour day it stops
    two half-hours short and the 14:10 run is the one. None, with a warning, when EMI has no
    zip for the day yet or no run covers the next day; a failed request raises."""
    try:
        archive = _fetch(PRSL.format(day=run_date), _parse_zip)
    except HTTPError as exc:
        if exc.code != 404:
            raise
        _warn(f"EMI has no PRSL zip for {run_date} yet")
        return None
    tomorrow = run_date + timedelta(days=1)
    need = half_hours(tomorrow)
    cutoff = clock.fromisoformat(spec["schedule"]["cutoff"])
    runs = []
    for name in archive.namelist():
        found = RUN_NAME.fullmatch(name)
        if found and found[1] == f"{run_date:%Y%m%d}":
            issued = datetime.strptime(found[1] + found[2], "%Y%m%d%H%M%S").replace(tzinfo=ZONE)
            if issued.time() >= cutoff:
                runs.append((issued, name))
    for issued, name in sorted(runs):
        prices = _forecast_prices(archive.read(name).decode("latin-1"), tomorrow)
        if sorted(prices) == list(range(1, need + 1)):
            return {
                "run_date": run_date,
                "run": name,
                "issued": issued,
                "target_date": tomorrow,
                "forecast": round(sum(prices.values()) / need, 6),
                "periods": need,
            }
    _warn(f"no PRSL run of {run_date} from {cutoff:%H:%M} covers all {need} periods of {tomorrow}")
    return None


def _prsl_file() -> Path:
    return _host_file(f"prsl.{CACHE_VERSION}.csv")


def _check_prsl(frame: pd.DataFrame) -> None:
    pd.to_datetime(frame["run_date"], format="%Y-%m-%d")
    pd.to_datetime(frame["target_date"], format="%Y-%m-%d")
    issued = frame["forecast"] != ""
    _numbers(frame.loc[issued, "forecast"])
    frame.loc[issued, "periods"].astype(int)
    _times(frame.loc[issued, "issued"])
    if not frame.loc[issued, "run"].map(lambda name: bool(RUN_NAME.fullmatch(name))).all():
        raise ValueError("a run is not named like a PRSL run")
    _times(frame["read_at"])


def _prsl_rows() -> dict[date, dict]:
    """The B read for each run date; an empty forecast where EMI had none."""
    frame = _load(_prsl_file(), PRSL_COLUMNS, _check_prsl)
    return {date.fromisoformat(r["run_date"]): r for r in frame.to_dict("records")}


def _prsl_kept() -> dict[date, float]:
    return {day: float(r["forecast"]) for day, r in _prsl_rows().items() if r["forecast"] != ""}


def _remember_prsl(run_date: date, issue: dict | None) -> None:
    """Keep a run date's B, or that EMI had none for it; a B once kept is never replaced."""
    rows = _prsl_rows()
    if run_date in rows and rows[run_date]["forecast"] != "":
        return
    row = {
        "run_date": run_date.isoformat(),
        "run": "",
        "issued": "",
        "target_date": (run_date + timedelta(days=1)).isoformat(),
        "forecast": "",
        "periods": "",
        "read_at": _now().isoformat(timespec="seconds"),
    }
    if issue is not None:
        row |= {
            "run": issue["run"],
            "issued": issue["issued"].isoformat(),
            "forecast": str(issue["forecast"]),
            "periods": str(issue["periods"]),
        }
    rows[run_date] = row
    frame = pd.DataFrame([rows[day] for day in sorted(rows)], columns=PRSL_COLUMNS)
    _write(_prsl_file(), frame.to_csv(index=False))


def backfill(today: date, spec: dict, limit: int | None = None, budget: float | None = None) -> int:
    """Read the B of past run dates not kept on the host yet, newest first, for the expert
    column: from the day before `today` back a year, or to the day before x.history_start.
    Stops at the first failed request, and after `limit` run dates or `budget` seconds when
    given; the snapshot gives BACKFILL and BUDGET, the maintainer's warm-up neither. A run
    date without B is kept as such and asked again only within a week. Returns the number of
    run dates read."""
    first = max(
        date.fromisoformat(spec["x"]["history_start"]) - timedelta(days=1),
        today - timedelta(days=EXPERT_DAYS + 1),
    )
    rows = _prsl_rows()
    todo = []
    day = today - timedelta(days=1)
    while day >= first:
        row = rows.get(day)
        if row is None or (row["forecast"] == "" and day >= today - timedelta(days=PENDING)):
            todo.append(day)
        day -= timedelta(days=1)
    started, read = time.monotonic(), 0
    for day in todo if limit is None else todo[:limit]:
        if budget is not None and time.monotonic() - started > budget:
            _warn(f"PRSL runs from {day} back are left for the next call")
            break
        try:
            issue = prsl_issue(day, spec)
        except FAILURES as exc:
            _warn(f"the PRSL zip of {day} was not read ({exc!r}); the rest wait for the next call")
            break
        _remember_prsl(day, issue)
        read += 1
    return read


def _expert(target: pd.Series, kept: dict[date, float], last: date, run_date: date) -> pd.Series:
    """Each day's PRSL forecast as a run could see it. A day up to the run date: the forecast for
    it issued the day before (B of the day before); a past day without one carries the previous
    day's price, and a later one the value of the day before. The target dates carry the run
    date's. B's own issue, the run of the run date, is never used."""
    index = target.index
    own = pd.Series(
        [kept.get(t.date() - timedelta(days=1)) if t.date() <= run_date else None for t in index],
        index=index,
        dtype=float,
    )
    past = index <= pd.Timestamp(last)
    column = own.where(~past, own.fillna(target.shift(1)))
    column[past] = column[past].ffill().bfill()
    return column.ffill().rename("prsl")


# --- the pipeline's functions -------------------------------------------------------------------


def snapshot(run_date: date, spec: dict) -> dict:
    as_of = _guard(run_date, spec)
    deadline = _cutoff(run_date, spec) - DEADLINE
    report = _report(run_date, spec)
    days = _report_days(report)
    newest = run_date - timedelta(days=1)
    prints = _prints()
    # Yesterday's interim prices come out at about 07:05. When they are not on EMI, or not
    # complete, they are asked for again once, a minute later if that is before the deadline,
    # and then the run goes on without them.
    for attempt in range(LATE_TRIES + 1):
        if newest in prints:
            break
        if attempt:
            if _now() + timedelta(seconds=LATE_WAIT) > deadline:
                break
            time.sleep(LATE_WAIT)
        try:
            found = _read_first_print(newest, report)
        except FAILURES as exc:
            _warn(f"EMI's interim prices of {newest} were not read ({exc!r})")
            break
        if found is not None:
            prints = _remember_print(newest, found)
    # Yesterday from its first print; earlier days from the report as published now, mostly
    # final prices, or from a first print where the report lacks the day.
    values = {day: p["price"] for day, p in prints.items() if day <= newest}
    values.update({day: r["price"] for day, r in days.items() if day < newest})
    last = max(values, default=None)
    if last is None or last < newest - timedelta(days=1):
        raise RuntimeError(f"The newest price at {NODE} is for {last}; two days are missing.")
    if last < newest:
        _warn(f"EMI has not published complete interim prices of {newest}; X_d ends with {last}")
    left = (deadline - _now()).total_seconds()
    if left > 0:
        backfill(run_date, spec, BACKFILL, min(BUDGET, left))
    else:
        _warn(f"it is past {deadline:%H:%M}, so no past PRSL runs are read before the agents start")
    _guard(run_date, spec)
    targets = [run_date + timedelta(days=h) for h in spec["horizons"]]
    index = pd.date_range(spec["x"]["history_start"], max(targets), freq="D", name="timestamp")
    target = pd.Series({pd.Timestamp(day): v for day, v in values.items()}, dtype=float)
    series = pd.DataFrame(
        {"target": target.reindex(index), "weekday": index.dayofweek, "month": index.month},
        index=index,
    )
    series["prsl"] = _expert(series["target"], _prsl_kept(), last, run_date)
    return {
        "series": series,
        "last_observed": last,
        "raw": {
            "ota2201_daily.csv": report[REPORT_COLUMNS[:4]],
            "first_prints.csv": _load(_prints_file(), PRINT_COLUMNS, _check_prints),
            "prsl_forecasts.csv": _load(_prsl_file(), PRSL_COLUMNS, _check_prsl),
        },
        "sources": {
            "history": REPORT_PAGE,
            "interim_prices": PRICES_PAGE,
            "prsl": PRSL_PAGE,
            "credit": {"prices": PRICE_CREDIT, "prsl": PRSL_CREDIT},
            "days": {"newest": last.isoformat(), "due": newest.isoformat()},
            "as_of": as_of.isoformat(),
        },
        "covariate_notes": {
            "weekday": "Day of the week of the trading day, 0 for Monday to 6 for Sunday.",
            "month": "Month of the trading day.",
            "prsl": (
                "The pricing manager's PRSL forecast of the day's mean price at OTA2201, in "
                "NZ$/MWh, from the first run from 12:00 the day before that covers the whole "
                "day; a past day without one carries the previous day's price. The target date "
                "carries the run date's, since its own is issued after the cutoff."
            ),
        },
    }


def truth(today: date, spec: dict) -> pd.Series:
    """Each day's first published value: the mean of the interim prices EMI posts at about
    07:05 the next morning, read at the pipeline's morning call and kept on the host. A day
    whose interim file was missed settles on the report: on its final prices once EMI shows
    them, normally the next morning, or after a week on the report as published then. The
    interim file is asked for first each morning for a week, so a day is never settled on
    final prices before its interim file is asked for. A failed request does not raise:
    truth returns what it has and warns."""
    if today > _now().date():
        raise RuntimeError("Cannot settle future dates.")
    try:
        report = _report(today, spec)
    except FAILURES as exc:
        _warn(f"EMI's single-node report was not read ({exc!r}) and no copy is kept")
        report = pd.DataFrame(columns=REPORT_COLUMNS)
    prints = _prints()
    first = max(date.fromisoformat(spec["x"]["history_start"]), today - timedelta(days=PENDING))
    # newest first, so that a failing older file never holds back the day just published
    day = today - timedelta(days=1)
    while day >= first:
        if day not in prints:
            try:
                found = _read_first_print(day, report)
            except FAILURES as exc:
                _warn(f"EMI's interim prices of {day} were not read ({exc!r}); the rest wait")
                break
            if found is not None:
                prints = _remember_print(day, found)
        day -= timedelta(days=1)
    values = {day: r["price"] for day, r in _report_days(report).items() if day < first}
    values.update({day: p["price"] for day, p in prints.items()})
    index = pd.DatetimeIndex(sorted(pd.Timestamp(day) for day in values), name="timestamp")
    return pd.Series([values[t.date()] for t in index], index=index, dtype=float, name="target")


def baseline(run_date: date, spec: dict) -> dict:
    """B: the PRSL forecast of the day after the run date, from the first run stamped at or
    after the cutoff that covers every half-hour of that day. Over 402 run days from 2025-10 it
    was the 12:10 run on 397 and runs stamped 12:19 to 12:44 on the others, and the 14:10 run
    before the 50-half-hour day. EMI posts each day's runs at 16:10 UTC, 05:10 NZDT or 04:10
    NZST the next morning, and the pipeline reads them at its 10:10 call; until they are
    posted `forecasts` is empty, and the pipeline asks again the next day."""
    tomorrow = run_date + timedelta(days=1)
    source = {
        "report": (
            f"PRSL forecast prices at {NODE} for {tomorrow}, from the first run of {run_date} "
            f"from {spec['schedule']['cutoff']} NZ that covers the day"
        ),
        "posted": None,
        "model": "Long price-responsive schedule (PRSL), published by the pricing manager",
        "page": PRSL_PAGE,
        "url": PRSL.format(day=run_date),
        "credit": PRSL_CREDIT,
    }
    if _now() < _cutoff(run_date, spec):
        return {"source": source | {"note": "before the cutoff"}, "forecasts": []}
    row = _prsl_rows().get(run_date)
    if row is not None and row["forecast"] != "":
        run, issued = row["run"], row["issued"]
        point, periods = float(row["forecast"]), int(row["periods"])
    else:
        try:
            issue = prsl_issue(run_date, spec)
        except FAILURES as exc:
            _warn(f"the PRSL forecast of {run_date} was not read ({exc!r})")
            return {"source": source | {"note": "EMI was not read"}, "forecasts": []}
        if issue is None:
            return {"source": source | {"note": "no run of the day on EMI yet"}, "forecasts": []}
        _remember_prsl(run_date, issue)
        run, issued = issue["run"], issue["issued"].isoformat()
        point, periods = issue["forecast"], issue["periods"]
    source |= {"posted": issued, "run": run, "periods": periods}
    return {"source": source, "forecasts": [{"target_date": tomorrow.isoformat(), "point": point}]}
