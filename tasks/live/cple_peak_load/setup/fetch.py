"""Duke Energy Progress East daily peak load: where the numbers for this target come from.

The agentic-forecasting pipeline imports this file on the host, outside every episode; the
sandbox's setup stage never runs it, and setup/run.sh makes it unreadable to the agent.
It needs only the standard library and pandas. The pipeline passes `spec`, the target
definition from task.yaml (`extras.agentic-forecasting` plus `params`), and turns the
results into archived files.

    snapshot(run_date, spec)  X_d: peak history and known covariates as published now
    truth(today, spec)        the peak of every day old enough to settle, as published now
    baseline(run_date, spec)  the BA's day-ahead forecast, as first published on the target day

No credentials: EIA's Hourly Electric Grid Monitor API (930-api) and Open-Meteo are keyless.
The balancing authority (BA) reports its hourly demand and its day-ahead demand forecast to
EIA on Form EIA-930.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

EIA_API = "https://www.eia.gov/electricity/930-api/region_data/series_data"
BA = "CPLE"  # EIA-930 respondent ID
BA_NAME = "Duke Energy Progress East"
WEATHER_MODEL = "ecmwf_ifs025"
SPIKE_RATIO = 1.3  # an hour (or pair of hours) this many times the hours on both sides
MAX_BAD_HOURS = 1  # a day with more missing, non-positive or spike hours has no value
SETTLE_DAYS = 2  # BAs revise the last days' hours; a day settles this many days later
RATE_LIMIT_PAUSE = 61  # seconds; Open-Meteo answers 429 until its minute's quota refills
# Load-centre city: (name, lat, lon)
CITIES = {
    "raleigh": ("Raleigh", 35.78, -78.64),
    "fayetteville": ("Fayetteville", 35.05, -78.88),
    "wilmington": ("Wilmington", 34.21, -77.89),
    "greenville_nc": ("Greenville, NC", 35.61, -77.37),
    "florence_sc": ("Florence, SC", 34.20, -79.76),
}
WEATHER_COLUMNS = [f"t{stat}_{key}" for key in CITIES for stat in ("max", "min")]


def _get(url: str, retries: int = 5) -> bytes:
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=180) as response:
                return response.read()
        except OSError as exc:
            if attempt == retries - 1:
                raise
            time.sleep(RATE_LIMIT_PAUSE if getattr(exc, "code", None) == 429 else 2**attempt)
    raise AssertionError("unreachable")


def _now(timezone: str) -> datetime:
    return datetime.now(ZoneInfo(timezone))


def _openmeteo(base: str, **params: object) -> list[dict]:
    """One Open-Meteo response per requested location, in request order."""
    payload = json.loads(_get(f"{base}?{urllib.parse.urlencode(params)}"))
    return payload if isinstance(payload, list) else [payload]


def _coords() -> dict[str, str]:
    return {
        "latitude": ",".join(str(lat) for *_, lat, _ in CITIES.values()),
        "longitude": ",".join(str(lon) for *_, lon in CITIES.values()),
    }


def _utc(day: date, timezone: str) -> datetime:
    """Local midnight at the start of `day`, in UTC."""
    return datetime(day.year, day.month, day.day, tzinfo=ZoneInfo(timezone)).astimezone(UTC)


def local_day(utc_end: pd.Series, timezone: str) -> pd.Series:
    """The local day of each hour, from the UTC time the hour ends: EIA's hours ending 01:00
    to 24:00 (midnight) make one day."""
    start = pd.to_datetime(utc_end, utc=True) - pd.Timedelta(minutes=1)
    return start.dt.tz_convert(timezone).dt.date


def hours_in_day(day: date, timezone: str) -> int:
    """24, or 23 and 25 on the days daylight saving time starts and ends."""
    span = _utc(day + timedelta(days=1), timezone) - _utc(day, timezone)
    return int(span.total_seconds()) // 3600


def eia_hourly(kind: str, first_day: date, last_day: date, timezone: str) -> pd.DataFrame:
    """The BA's hourly values (MW) on Form EIA-930 for the local days first_day to last_day:
    kind D is demand, DF the day-ahead demand forecast. One row per hour (utc_end, value),
    the value empty where the BA reported none; EIA's imputed values are never used."""
    stamps = pd.date_range(
        _utc(first_day, timezone) + timedelta(hours=1),
        _utc(last_day + timedelta(days=1), timezone),
        freq="h",
    )
    parts = []
    for year in sorted(set(stamps.year)):  # one request per UTC year stays under 50,000 rows
        span = stamps[stamps.year == year]
        query = urllib.parse.urlencode(
            {
                "type[0]": kind,
                "respondent[0]": BA,
                "start": f"{span[0]:%m%d%Y %H:%M:%S}",  # UTC hour ending, both ends included
                "end": f"{span[-1]:%m%d%Y %H:%M:%S}",
                "frequency": "hourly",
            }
        )
        block = json.loads(_get(f"{EIA_API}?{query}"))[0]
        if block["recordCount"] != block["totalCount"]:
            raise RuntimeError(
                f"930-api truncated {BA} {kind} {year}: "
                f"{block['recordCount']} of {block['totalCount']} records"
            )
        for series in block["data"]:
            values = series["VALUES"]
            index = pd.to_datetime(values["DATES"], format="%m/%d/%Y %H:%M:%S", utc=True)
            parts.append(pd.Series(values["DATA_REPORTED"], index=index, dtype=float))
    reported = pd.concat(parts) if parts else pd.Series(dtype=float)
    reported = reported[~reported.index.duplicated()]
    return pd.DataFrame({"utc_end": stamps, "value": reported.reindex(stamps).to_numpy()})


def bad_hours(values: pd.Series) -> pd.Series:
    """Hours to leave out of consecutive hourly values: missing, zero or negative, or more
    than SPIKE_RATIO times the hours on both sides, alone or as a pair. A neighbour that is
    itself missing or not positive is replaced by the next hour out on that side."""
    v = values.where(values > 0)
    before = v.shift(1).fillna(v.shift(2))
    after = v.shift(-1).fillna(v.shift(-2))
    spike = (v > SPIKE_RATIO * before) & (v > SPIKE_RATIO * after)
    # a pair of hours i, i + 1, against the hour before i and the hour after i + 1
    after_pair = v.shift(-2).fillna(v.shift(-3))
    low = pd.concat([before, after_pair], axis=1).max(axis=1, skipna=False) * SPIKE_RATIO
    pair = (v > low) & (v.shift(-1) > low)
    return v.isna() | spike | pair | pair.shift(1, fill_value=False)


def daily_peak(hourly: pd.DataFrame, timezone: str) -> pd.Series:
    """Highest good hour of each local day that has all its hours and at most MAX_BAD_HOURS
    bad ones, indexed by the local date. `hourly` holds consecutive hours (utc_end, value)."""
    hourly = hourly.sort_values("utc_end").reset_index(drop=True)
    good = hourly["value"].where(~bad_hours(hourly["value"]))
    frame = pd.DataFrame({"day": local_day(hourly["utc_end"], timezone), "good": good})
    days = frame.groupby("day")["good"].agg(["max", "count", "size"])
    full = days["size"] == [hours_in_day(day, timezone) for day in days.index]
    peaks = days.loc[full & (days["size"] - days["count"] <= MAX_BAD_HOURS), "max"]
    peaks.index = pd.to_datetime(peaks.index)
    return peaks


def target_history(peaks: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """Daily peaks on `index`, empty after the last observed day. A day missing between two
    observed days is filled linearly (truth leaves it empty): the pipeline's seasonal naive
    reads the last observed week of X_d and cannot score against a missing value."""
    return peaks.reindex(index).interpolate(limit_area="inside")


def weather_past(start: date, end: date, timezone: str) -> pd.DataFrame:
    """Daily max/min 2 m temperature per city from ERA5 reanalysis (Open-Meteo archive)."""
    responses = _openmeteo(
        "https://archive-api.open-meteo.com/v1/archive",
        **_coords(),
        start_date=start,
        end_date=end,
        daily="temperature_2m_max,temperature_2m_min",
        timezone=timezone,
    )
    frame = pd.DataFrame(index=pd.to_datetime(responses[0]["daily"]["time"]))
    for city, response in zip(CITIES, responses, strict=True):
        frame[f"tmax_{city}"] = response["daily"]["temperature_2m_max"]
        frame[f"tmin_{city}"] = response["daily"]["temperature_2m_min"]
    return frame[WEATHER_COLUMNS]


def weather_forecast(horizon: int, timezone: str) -> tuple[pd.DataFrame, dict[str, object]]:
    """Latest published hourly 2 m temperature forecast per city."""
    params: dict[str, object] = {
        **_coords(),
        "hourly": "temperature_2m",
        "models": WEATHER_MODEL,
        "timezone": timezone,
        "forecast_days": horizon + 3,
        "past_days": 7,  # covers the last days if the reanalysis archive has not caught up
    }
    base = "https://api.open-meteo.com/v1/forecast"
    frames = [
        pd.DataFrame(
            {
                "time": pd.to_datetime(response["hourly"]["time"]),
                "city": city,
                "temperature_2m": response["hourly"]["temperature_2m"],
            }
        )
        for city, response in zip(CITIES, _openmeteo(base, **params), strict=True)
    ]
    source = {"url": base, **{k: v for k, v in params.items() if k not in _coords()}}
    return pd.concat(frames, ignore_index=True).dropna(), source


def daily_from_hourly(hourly: pd.DataFrame) -> pd.DataFrame:
    hourly = hourly.assign(day=hourly["time"].dt.normalize())
    full = hourly.groupby(["day", "city"])["temperature_2m"].agg(["max", "min", "size"])
    full = full[full["size"] >= 23]
    wide = full[["max", "min"]].unstack("city")
    wide.columns = [f"t{stat}_{city}" for stat, city in wide.columns]
    return wide[WEATHER_COLUMNS]


def calendar(index: pd.DatetimeIndex) -> pd.DataFrame:
    holidays = USFederalHolidayCalendar().holidays(index.min(), index.max())
    return pd.DataFrame(
        {"dow": index.dayofweek, "holiday": index.isin(holidays).astype(int)}, index=index
    )


def snapshot(run_date: date, spec: dict) -> dict:
    """X_d as published right now: one row per day from history_start to the last target
    date, `target` empty after the last complete day, covariates known for the window."""
    start = date.fromisoformat(spec["x"]["history_start"])
    timezone = spec["schedule"]["timezone"]
    horizon = max(spec["horizons"])
    # up to now: the run day's first hours are the next ones the day before is compared with
    hourly = eia_hourly("D", start, run_date, timezone)
    peaks = daily_peak(hourly, timezone).loc[: pd.Timestamp(run_date - timedelta(days=1))]
    last_observed = peaks.index.max().date()
    forecast_hourly, forecast_source = weather_forecast(horizon, timezone)

    index = pd.date_range(start, run_date + timedelta(days=horizon), freq="D", name="timestamp")
    forecast = daily_from_hourly(forecast_hourly).reindex(index)
    # Reanalysis up to yesterday (forecast fills days the archive lacks), forecast from today.
    weather = weather_past(start, run_date - timedelta(days=1), timezone).reindex(index)
    weather = weather.combine_first(forecast)
    future = index >= pd.Timestamp(run_date)
    weather.loc[future] = forecast.loc[future]
    target = target_history(peaks, index).rename("target")
    series = pd.concat([target, weather, calendar(index)], axis=1)
    observed = local_day(hourly["utc_end"], timezone) <= last_observed
    return {
        "series": series.rename_axis("timestamp"),
        "last_observed": last_observed,
        "raw": {
            "demand_hourly.csv": hourly[observed],
            "temperature_forecast_hourly.csv": forecast_hourly[
                forecast_hourly["time"] >= pd.Timestamp(run_date)
            ],
        },
        "sources": {
            "demand": {"url": EIA_API, "respondent": BA, "type": "D", "values": "DATA_REPORTED"},
            "weather_past": {"url": "https://archive-api.open-meteo.com/v1/archive"},
            "weather_forecast": forecast_source,
        },
        "covariate_notes": {
            "target": "daily peak demand (MW) as published at the snapshot; a day without a "
            f"value (more than {MAX_BAD_HOURS} missing, non-positive or spike hour) is filled "
            "linearly from its neighbours",
            "tmax_<city>, tmin_<city>": "daily max/min 2 m temperature (deg C) at the city; "
            "dates before run_date are ERA5 reanalysis, run_date onward is the "
            f"{WEATHER_MODEL} forecast issued before the snapshot",
            "cities": {city: name for city, (name, *_) in CITIES.items()},
            "dow": "day of week, Monday = 0",
            "holiday": "US federal holiday (1) or not (0)",
        },
    }


def truth(today: date, spec: dict) -> pd.Series:
    """The peak of every day up to SETTLE_DAYS before today, as published now."""
    timezone = spec["schedule"]["timezone"]
    last = today - timedelta(days=SETTLE_DAYS)
    start = date.fromisoformat(spec["x"]["history_start"])
    # a day more, so that the last hour has a next one to be compared with
    hourly = eia_hourly("D", start, last + timedelta(days=1), timezone)
    return daily_peak(hourly, timezone).loc[: pd.Timestamp(last)]


def baseline(run_date: date, spec: dict) -> dict:
    """The BA's day-ahead forecast as first published: the maximum of its hourly values on
    each target date. EIA posts it on the target date itself, so only a fetch on that date
    records it; on any other day the forecasts are empty, as the BA may revise it later."""
    timezone = spec["schedule"]["timezone"]
    now = _now(timezone)
    forecasts, posted = [], None
    for h in spec["horizons"]:
        target = run_date + timedelta(days=h)
        if now.date() != target:
            continue
        values = eia_hourly("DF", target, target, timezone)["value"]
        if len(values) == hours_in_day(target, timezone) and (values > 0).all():
            point = round(float(values.max()), 0)
            forecasts.append({"target_date": target.isoformat(), "point": point})
            posted = now.isoformat(timespec="seconds")  # the BA's posting time is unknown
    return {
        "source": {
            "report": f"EIA-930 day-ahead demand forecast (DF) for {BA}",
            "posted": posted,
            "model": BA,
        },
        "forecasts": forecasts,
    }
