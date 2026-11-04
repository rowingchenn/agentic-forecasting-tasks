"""Host-only Bank of Canada USD/CAD reference data; no credentials.

Bank of Canada source data; calendar carry-forwards and forecasts are derived, not
Bank publications. Retain attribution and indicate transformations on redistribution.
Terms: https://www.bankofcanada.ca/terms/ (including Exchange Rates and third-party terms).
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import date, datetime, time as clock, timedelta
from decimal import Decimal, InvalidOperation
from functools import cache
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd
from pandas.tseries.holiday import GoodFriday

API = "https://www.bankofcanada.ca/valet/observations/FXUSDCAD/json"
TERMS = "https://www.bankofcanada.ca/terms/"
ZONE = ZoneInfo("America/Toronto")


def _now():
    return datetime.now(ZONE)


def _cutoff(run_date, spec):
    if spec["schedule"]["timezone"] != "America/Toronto":
        raise ValueError("This reference series requires America/Toronto calendar dates.")
    return datetime.combine(run_date, clock.fromisoformat(spec["schedule"]["cutoff"]), ZONE)


def _guard(run_date, spec):
    now = _now()
    if now.date() != run_date or now >= _cutoff(run_date, spec):
        raise RuntimeError("Fresh collection requires the current local day before cutoff.")
    return now


def _monday(year, month, occurrence):
    first = date(year, month, 1)
    return first + timedelta(days=(7 - first.weekday()) % 7 + 7 * (occurrence - 1))


@cache
def _holidays(year):
    """BoC non-publication calendar; stop on any observed deviation rather than filling it.

    Includes Family Day and Civic Holiday, not Easter Monday. Christmas/Boxing Day
    substitutions must not collide. Calendar changes need review against the Bank schedule.
    """
    days = {_monday(year, 2, 3), _monday(year, 8, 1), _monday(year, 9, 1),
            _monday(year, 10, 2), GoodFriday.dates(f"{year}-01-01", f"{year}-12-31")[0].date()}
    may24 = date(year, 5, 24)
    days.add(may24 - timedelta(days=may24.weekday()))
    fixed = [date(year, 1, 1), date(year, 7, 1), date(year, 11, 11)]
    if year >= 2021:
        fixed.append(date(year, 9, 30))
    for day in fixed:
        days.add(day + timedelta(days=7 - day.weekday()) if day.weekday() >= 5 else day)
    christmas = [date(year, 12, 25), date(year, 12, 26)]
    days.update(day for day in christmas if day.weekday() < 5)
    for day in christmas:
        if day.weekday() >= 5:
            observed = day + timedelta(days=7 - day.weekday())
            while observed.weekday() >= 5 or observed in days:
                observed += timedelta(days=1)
            days.add(observed)
    return frozenset(days)


def publication_day(day):
    return day.weekday() < 5 and day not in _holidays(day.year)


def _get(url):
    for attempt in range(4):
        try:
            request = Request(url, headers={
                "User-Agent": "agentic-forecasting-boc/1.0 (https://github.com/rowingchenn/agentic-forecasting-tasks)",
                "Accept": "application/json",
            })
            with urlopen(request, timeout=90) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise
            retry = exc.headers.get("Retry-After", "")
            delay = min(120, int(retry)) if retry.isdigit() else 2 ** attempt
        except (URLError, TimeoutError):
            if attempt == 3:
                raise
            delay = 2 ** attempt
        time.sleep(delay)
    raise AssertionError("unreachable")


def _daily(start, end):
    if start > end or start < date(2017, 5, 1):
        raise ValueError("Require a nonempty history under the current daily-average methodology.")
    seed = start - timedelta(days=1)
    while not publication_day(seed):
        seed -= timedelta(days=1)
    payload = _get(f"{API}?{urlencode({'start_date': seed, 'end_date': end})}")
    if (not isinstance(payload, dict) or not isinstance(payload.get("observations"), list)
            or payload.get("seriesDetail", {}).get("FXUSDCAD", {}).get("label") != "USD/CAD"):
        raise ValueError("Unexpected Bank of Canada response or currency series.")
    rates = {}
    for row in payload["observations"]:
        if not isinstance(row, dict):
            raise ValueError("Invalid observation record.")
        day = date.fromisoformat(row["d"])
        value = row.get("FXUSDCAD", {}).get("v")
        if isinstance(value, bool) or value is None:
            raise ValueError(f"Missing reference rate on {day}; retry after publication.")
        try:
            rate = Decimal(str(value))
        except InvalidOperation:
            raise ValueError("Invalid decimal reference rate.") from None
        if not rate.is_finite() or rate <= 0:
            raise ValueError("Reference rates must be finite and positive.")
        if day in rates or not seed <= day <= end or not publication_day(day):
            raise ValueError("Duplicate, out-of-range or unexpected publication date; review calendar.")
        rates[day] = rate
    expected = {d.date() for d in pd.date_range(seed, end) if publication_day(d.date())}
    if set(rates) != expected:
        raise ValueError("Missing expected publication-day rate; do not fill a delayed observation.")
    # af.target writes binary floats with %.2f. Exact decimal objects preserve the
    # published four-decimal FX precision in CSV; downstream readers infer numbers.
    observed = pd.Series(rates, dtype=object).sort_index()
    observed.index = pd.to_datetime(observed.index)
    index = pd.date_range(seed, end, name="timestamp")
    daily = observed.reindex(index).ffill().loc[str(start):].rename("target")
    raw = pd.DataFrame({"date": [d.date().isoformat() for d in observed.index],
                        "FXUSDCAD": observed.to_numpy()})
    return daily, raw


def snapshot(run_date: date, spec: dict) -> dict:
    as_of = _guard(run_date, spec)
    start = date.fromisoformat(spec["x"]["history_start"])
    through = run_date - timedelta(days=1)
    history, raw = _daily(start, through)
    _guard(run_date, spec)
    index = pd.date_range(start, run_date + timedelta(days=max(spec["horizons"])), name="timestamp")
    return {
        "series": pd.DataFrame({
            "target": history.reindex(index), "dow": index.dayofweek,
            "publication_day": [int(publication_day(d.date())) for d in index],
        }, index=index),
        "last_observed": through,
        "raw": {"boc_reference_rates.csv": raw},
        "sources": {
            "exchange_rate": {"name": "Bank of Canada", "url": API, "series": "FXUSDCAD",
                              "unit": "CAD per USD", "terms": TERMS,
                              "transformation": "Previous rate on scheduled closures; derived data, no endorsement."},
            "as_of": as_of.isoformat(),
        },
        "covariate_notes": {"dow": "Monday=0.", "publication_day": "Scheduled BoC rate publication day, 1 or 0."},
    }


def truth(today: date, spec: dict) -> pd.Series:
    if today > _now().date():
        raise RuntimeError("Cannot settle future dates.")
    return _daily(date.fromisoformat(spec["x"]["history_start"]), today - timedelta(days=1))[0]


def _reference(run_date, spec):
    root = (Path(__file__).resolve().parents[3] / "mock" / spec["target"] /
            str(run_date) / "oracle" / "assets" / "x")
    if not root.exists():
        result = snapshot(run_date, spec)  # direct same-day pre-cutoff smoke test only
        return result["series"]["target"], result["last_observed"], result["sources"]["as_of"], None
    manifest = json.loads((root / "MANIFEST.json").read_text())
    for name in ("task.json", "series.csv"):
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != manifest["files"].get(name):
            raise ValueError(f"Archived {name} fails its snapshot hash.")
    task = json.loads((root / "task.json").read_text())
    last = date.fromisoformat(task["last_observed"])
    as_of = datetime.fromisoformat(manifest["sources"]["as_of"])
    if (task["run_date"] != str(run_date) or task["task"] != spec["target"]
            or manifest["snapshot"] != f"{spec['target']}/{run_date}"
            or last != run_date - timedelta(days=1) or as_of.tzinfo is None
            or as_of.astimezone(ZONE).date() != run_date or as_of >= _cutoff(run_date, spec)):
        raise ValueError("Not a valid pre-cutoff run-day snapshot.")
    history = pd.read_csv(root / "series.csv", index_col="timestamp", parse_dates=True)["target"]
    if not history.index.is_unique:
        raise ValueError("Duplicate dates in frozen history.")
    return history, last, as_of.isoformat(), manifest["snapshot"]


def baseline(run_date: date, spec: dict) -> dict:
    if run_date > _now().date():
        raise RuntimeError("Cannot build a future run's baseline.")
    history, last, as_of, frozen = _reference(run_date, spec)
    point = float(history.loc[pd.Timestamp(last)])
    if not math.isfinite(point) or point <= 0:
        raise ValueError("Invalid no-change reference rate.")
    return {
        "source": {"name": "No-change reference (statistical, not expert)", "url": API,
                   "as_of": as_of, "last_observed": str(last), "snapshot": frozen,
                   "attribution": "Bank of Canada; derived forecast, no endorsement."},
        "forecasts": [{"target_date": str(run_date + timedelta(days=h)), "point": point}
                      for h in spec["horizons"]],
    }
