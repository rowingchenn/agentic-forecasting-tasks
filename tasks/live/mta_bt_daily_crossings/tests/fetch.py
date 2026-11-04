"""Host-only MTA BT daily counts and New York City weather; no API key.

Sources: Metropolitan Transportation Authority, sayj-mze2 (preliminary crossing estimates,
not unique vehicles or paid toll transactions; no gap filling), and Open-Meteo (ERA5
reanalysis for past days, the ECMWF IFS forecast for the forecast window). No expert
forecast of this series exists, so baseline() returns none.
"""
from __future__ import annotations
import json
import math
import time
from datetime import date, datetime, time as clock, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

API = "https://data.ny.gov/resource/sayj-mze2.json"
OVERVIEW = "https://data.ny.gov/api/views/sayj-mze2/files/7635e5d0-7534-406d-b1c4-be4969ce61df?download=true"
ZONE = ZoneInfo("America/New_York")
PAGE_SIZE = 1000
MAX_LAG_DAYS = 7
LAT, LON = 40.7128, -74.0060  # New York City, where the weather covariates are taken
WEATHER_MODEL = "ecmwf_ifs025"
DAILY = {"temperature_2m_max": "tmax", "precipitation_sum": "precip", "snowfall_sum": "snow"}
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
FORECAST = "https://api.open-meteo.com/v1/forecast"


def _now():
    return datetime.now(ZONE)


def _cutoff(run_date, spec):
    if spec["schedule"]["timezone"] != "America/New_York":
        raise ValueError("MTA runs use America/New_York.")
    return datetime.combine(run_date, clock.fromisoformat(spec["schedule"]["cutoff"]), ZONE)


def _guard(run_date, spec):
    now = _now()
    if now.date() != run_date or now >= _cutoff(run_date, spec):
        raise RuntimeError("Fresh snapshots require the current local day before cutoff.")
    return now


def _get(url):
    for attempt in range(4):
        try:
            request = Request(url, headers={"Accept": "application/json",
                "User-Agent": "agentic-forecasting-mta/1.0 (https://github.com/rowingchenn/agentic-forecasting-tasks)"})
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
    """Fetch all pages, enforcing one count for every date through the latest publication.

    end is a completed-date upper bound, not a promise the source has published it.
    Missing dates inside the observed span fail; the unpublished tail is left unknown.
    """
    if start > end or start < date(2024, 1, 1):
        raise ValueError("Require a nonempty 2024+ history.")
    values = {}
    for page in range(100):
        params = {"$select": "date,mode,count",
                  "$where": f"mode='BT' AND date >= '{start}T00:00:00' AND date <= '{end}T00:00:00'",
                  "$order": "date ASC", "$limit": PAGE_SIZE, "$offset": page * PAGE_SIZE}
        rows = _get(f"{API}?{urlencode(params)}")
        if not isinstance(rows, list) or len(rows) > PAGE_SIZE:
            raise ValueError("Unexpected MTA response or page size.")
        for row in rows:
            if not isinstance(row, dict) or row.get("mode") != "BT":
                raise ValueError("MTA returned a different transport mode or invalid row.")
            stamp = pd.Timestamp(row["date"])
            if stamp.tzinfo is not None or stamp != stamp.normalize():
                raise ValueError("Expected MTA's floating daily date labels.")
            day = stamp.date()
            value = row.get("count")
            if isinstance(value, bool) or value is None:
                raise ValueError("Missing daily count; do not substitute zero or carry forward.")
            point = float(value)
            if not math.isfinite(point) or point < 0 or not point.is_integer():
                raise ValueError("MTA crossing counts must be finite non-negative integers.")
            if not start <= day <= end or day in values:
                raise ValueError("Out-of-range or duplicate date; retry a consistent source snapshot.")
            values[day] = int(point)
        if len(rows) < PAGE_SIZE:
            break
    else:
        raise RuntimeError("MTA pagination cap reached; no partial history is returned.")
    if not values:
        raise ValueError("No published BT history returned.")
    index = pd.date_range(start, max(values), name="timestamp")
    if set(values) != set(index.date):
        raise ValueError("Missing dates inside published BT history; do not interpolate.")
    history = pd.Series([values[d.date()] for d in index], index=index, name="target", dtype=float)
    raw = pd.DataFrame({"date": index.strftime("%Y-%m-%d"), "mode": "BT",
                        "count": [values[d.date()] for d in index]})
    return history, raw


def _weather(url, params):
    """Open-Meteo's daily block as tmax, precip, snow, indexed by local date."""
    query = {"latitude": LAT, "longitude": LON, "daily": ",".join(DAILY),
             "timezone": ZONE.key} | params
    daily = _get(f"{url}?{urlencode(query)}")["daily"]
    frame = pd.DataFrame({DAILY[k]: pd.Series(daily[k], dtype=float) for k in DAILY})
    return frame.set_index(pd.DatetimeIndex(pd.to_datetime(daily["time"]), name="timestamp"))


def weather(start, run_date, horizon):
    """Reanalysis up to the day before run_date, the forecast published now from run_date on;
    the forecast's past days fill any recent day the archive still lacks."""
    forecast_params = {"models": WEATHER_MODEL, "past_days": 7, "forecast_days": horizon + 1}
    forecast = _weather(FORECAST, forecast_params)
    past = _weather(ARCHIVE, {"start_date": start, "end_date": run_date - timedelta(days=1)})
    index = pd.date_range(start, run_date + timedelta(days=horizon), name="timestamp")
    future = index >= pd.Timestamp(run_date)
    frame = past.reindex(index).combine_first(forecast.reindex(index))
    frame.loc[future] = forecast.reindex(index).loc[future]
    return frame, forecast.loc[pd.Timestamp(run_date):], {"url": FORECAST, **forecast_params}


def snapshot(run_date: date, spec: dict) -> dict:
    as_of = _guard(run_date, spec)
    start = date.fromisoformat(spec["x"]["history_start"])
    horizon = max(spec["horizons"])
    history, raw = _daily(start, run_date - timedelta(days=1))
    last = history.index[-1].date()
    if last < run_date - timedelta(days=MAX_LAG_DAYS):
        raise RuntimeError("Latest BT count is over seven days old; review delayed publication.")
    weather_daily, forecast_daily, forecast_source = weather(start, run_date, horizon)
    _guard(run_date, spec)
    index = pd.date_range(start, run_date + timedelta(days=horizon), name="timestamp")
    holidays = USFederalHolidayCalendar().holidays(index.min(), index.max())
    calendar = pd.DataFrame({"dow": index.dayofweek, "month": index.month,
                             "holiday": index.isin(holidays).astype(int)}, index=index)
    series = pd.concat([history.reindex(index), weather_daily, calendar], axis=1)
    return {"series": series,
            "last_observed": last,
            "raw": {"mta_bt_daily.csv": raw,
                    "weather_forecast_daily.csv": forecast_daily.rename_axis("date").reset_index()},
            "sources": {"traffic": {"name": "MTA", "url": API, "dataset": "sayj-mze2", "mode": "BT",
                                     "methodology": OVERVIEW, "data_use_note": "Overview states no limitations at this time; MTA/TBTA retain reserved rights."},
                        "weather_past": {"url": ARCHIVE},
                        "weather_forecast": forecast_source,
                        "as_of": as_of.isoformat()},
            "covariate_notes": {"tmax": "daily maximum 2 m temperature (deg C)",
                                "precip": "daily precipitation (mm)",
                                "snow": "daily snowfall (cm)",
                                "weather": f"at {LAT}, {LON}, local days; dates before run_date are "
                                f"ERA5 reanalysis, run_date onward the {WEATHER_MODEL} forecast "
                                "published before the snapshot",
                                "dow": "Monday=0.", "month": "Calendar month.",
                                "holiday": "US federal holiday indicator, not a toll-facility closure indicator."}}


def truth(today: date, spec: dict) -> pd.Series:
    if today > _now().date():
        raise RuntimeError("Cannot settle future dates.")
    # update_truth retains the first observed value for each completed published date.
    return _daily(date.fromisoformat(spec["x"]["history_start"]), today - timedelta(days=1))[0]


def baseline(run_date: date, spec: dict) -> dict:
    """No public expert forecast of daily BT crossings exists; the engine scores against its
    weekly seasonal naive instead."""
    return {"source": {"report": "no expert forecast of daily MTA BT crossings"}, "forecasts": []}
