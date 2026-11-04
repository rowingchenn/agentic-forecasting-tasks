"""Bank of Canada USD/CAD daily average: where the numbers for this target come from.

The pipeline imports this file on the host, outside every episode. No credentials: the
Bank's Valet API is keyless.

    snapshot(run_date, spec)  X_d: the rate history through the run date, as published now
    truth(today, spec)        every completed day's rate as published now
    baseline(run_date, spec)  no change from the latest rate published by the cutoff

Bank of Canada source data; calendar carry-forwards and forecasts are derived, not
Bank publications. Retain attribution and indicate transformations on redistribution.
Terms: https://www.bankofcanada.ca/terms/ (including Exchange Rates and third-party terms).
"""
from __future__ import annotations

import json
import math
import time
from datetime import date, datetime, time as clock, timedelta
from functools import cache
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
            rate = float(value)  # four decimals; the pipeline writes series.csv with %.10g
        except (TypeError, ValueError):
            raise ValueError("Invalid reference rate.") from None
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError("Reference rates must be finite and positive.")
        if day in rates or not seed <= day <= end or not publication_day(day):
            raise ValueError("Duplicate, out-of-range or unexpected publication date; review calendar.")
        rates[day] = rate
    expected = {d.date() for d in pd.date_range(seed, end) if publication_day(d.date())}
    if set(rates) != expected:
        raise ValueError("Missing expected publication-day rate; do not fill a delayed observation.")
    observed = pd.Series(rates, dtype=float).sort_index()
    observed.index = pd.to_datetime(observed.index)
    index = pd.date_range(seed, end, name="timestamp")
    daily = observed.reindex(index).ffill().loc[str(start):].rename("target")
    raw = pd.DataFrame({"date": [d.date().isoformat() for d in observed.index],
                        "FXUSDCAD": observed.to_numpy()})
    return daily, raw


def snapshot(run_date: date, spec: dict) -> dict:
    as_of = _guard(run_date, spec)
    start = date.fromisoformat(spec["x"]["history_start"])
    through = run_date  # its rate is published by 16:30 ET, before the 17:05 start
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


def baseline(run_date: date, spec: dict) -> dict:
    """No change from the latest rate published by the cutoff: the run date's (by 16:30 ET),
    or on a Bank holiday the last one before it, for every target date. Read from Valet."""
    source = {"name": "No-change reference: the latest Bank of Canada FXUSDCAD rate at the cutoff",
              "url": API, "attribution": "Bank of Canada; derived forecast, no endorsement."}
    if _now() < _cutoff(run_date, spec):
        return {"source": source, "forecasts": []}
    published = run_date
    while not publication_day(published):
        published -= timedelta(days=1)
    point = float(_daily(published, published)[0].iloc[0])
    return {
        "source": source | {"rate_date": str(published)},
        "forecasts": [{"target_date": str(run_date + timedelta(days=h)), "point": point}
                      for h in spec["horizons"]],
    }
