"""Southwest Power Pool daily wind generation: where the numbers for this target come from.

The pipeline imports this file on the host, outside every episode. No credentials: EIA's
six-month EIA-930 BALANCE files are public downloads, and Open-Meteo is keyless. EIA
rewrites every six-month file once a day, at about 09:30 to 10:05 Central, and that
rewrite adds the previous day's net generation by energy source. The target uses SWPP's
column for wind without integrated battery storage (fuel code WND) only. A day with a
missing or blank hour has no value (NaN), never a partial sum. A half-year that ended more
than CLOSED_AFTER days ago is read once and cached on the host (SWPP's values in closed
half-years were the same in EIA's file versions of October and November 2026); the others
are read on every call.

    snapshot(run_date, spec)  X_d: daily totals through the latest complete day, and wind
                              at hub height across SPP's wind belt
    truth(today, spec)        every complete day's total as published now
    baseline(run_date, spec)  none: this target has no expert forecast
"""

from __future__ import annotations

import http.client
import io
import json
import os
import time
from datetime import UTC, date, datetime, timedelta
from datetime import time as clock
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd

TARGET = "swpp_daily_wind"
ZONE = ZoneInfo("America/Chicago")
FILES = "https://www.eia.gov/electricity/gridmonitor/sixMonthFiles"
PAGE = "https://www.eia.gov/electricity/gridmonitor/"
BA = "Balancing Authority"
HOUR = "UTC Time at End of Hour"
WIND = "Net Generation (MW) from Wind without Integrated Battery Storage"
FIRST_SPLIT = date(2024, 7, 1)  # the first six-month file with WND and WNB in separate columns
CLOSED_AFTER = 31  # days after a half-year ends before its file is cached
MAX_LAG_DAYS = 7
USER_AGENT = "agentic-forecasting/1.0"
CACHE = (
    Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "agentic-forecasting" / TARGET
)
CACHE_VERSION = "v1"  # in the cache files' names; raise it when the parser changes
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
FORECAST = "https://api.open-meteo.com/v1/forecast"
WEATHER_MODEL = "ecmwf_ifs025"
# Places in the wind belt of the SPP balancing authority: (latitude, longitude, description)
SITES = {
    "woodward_ok": (36.43, -99.39, "Woodward, northwest Oklahoma"),
    "weatherford_ok": (35.53, -98.71, "Weatherford, western Oklahoma"),
    "dodge_city_ks": (37.75, -100.02, "Dodge City, southwest Kansas"),
    "salina_ks": (38.84, -97.61, "Salina, central Kansas"),
    "amarillo_tx": (35.22, -101.83, "Amarillo, Texas Panhandle"),
    "clovis_nm": (34.40, -103.20, "Clovis, eastern New Mexico"),
    "north_platte_ne": (41.12, -100.77, "North Platte, western Nebraska"),
    "bismarck_nd": (46.81, -100.78, "Bismarck, North Dakota"),
}
WEATHER_COLUMNS = [f"wind100_{site}" for site in SITES]


def _now() -> datetime:
    return datetime.now(ZONE)


def _guard(run_date: date, spec: dict) -> datetime:
    if spec["schedule"]["timezone"] != ZONE.key:
        raise ValueError("SWPP reports Central-time days; the schedule must use America/Chicago.")
    now = _now()
    cutoff = datetime.combine(run_date, clock.fromisoformat(spec["schedule"]["cutoff"]), ZONE)
    if now.date() != run_date or now >= cutoff:
        raise RuntimeError("A fresh snapshot requires the run date's local day before the cutoff.")
    return now


def _get(url: str, retries: int = 4) -> bytes | None:
    """The body of url; None if the server has no such file. Outages last minutes, so
    transient failures are retried with a growing pause."""
    for attempt in range(retries):
        try:
            with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=180) as response:
                return response.read()
        except HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code not in (429, 500, 502, 503, 504) or attempt == retries - 1:
                raise
        except (OSError, http.client.HTTPException):
            if attempt == retries - 1:
                raise
        time.sleep(15 * 2**attempt)
    raise AssertionError("unreachable")


def _day_hours(day: date) -> list[pd.Timestamp]:
    """The UTC hour endings of a Central-time day: 23, 24 or 25 of them."""
    first = datetime.combine(day, clock(), ZONE).astimezone(UTC)
    last = datetime.combine(day + timedelta(days=1), clock(), ZONE).astimezone(UTC)
    return list(pd.date_range(first + timedelta(hours=1), last, freq="h"))


def _half_years(start: date, end: date) -> list[tuple[date, date, str]]:
    """(first day, last day, file name) of each six-month file covering start..end."""
    halves = []
    for year in range(start.year, end.year + 1):
        for first, last, name in (
            (date(year, 1, 1), date(year, 6, 30), f"EIA930_BALANCE_{year}_Jan_Jun.csv"),
            (date(year, 7, 1), date(year, 12, 31), f"EIA930_BALANCE_{year}_Jul_Dec.csv"),
        ):
            if first <= end and last >= start:
                halves.append((first, last, name))
    return halves


def _parse(body: bytes, name: str) -> pd.DataFrame:
    """SWPP's hourly WND values in one six-month file; a blank or non-numeric value is NaN."""
    try:
        frame = pd.read_csv(
            io.BytesIO(body), usecols=[BA, HOUR, WIND], dtype=str, keep_default_na=False
        )
    except ValueError as exc:
        raise ValueError(f"{name}: EIA-930 columns changed; the WND column is required") from exc
    frame = frame[frame[BA].str.strip() == "SWPP"]
    hours = pd.to_datetime(frame[HOUR], format="%m/%d/%Y %I:%M:%S %p").dt.tz_localize(UTC)
    values = pd.to_numeric(frame[WIND].str.strip(), errors="coerce").astype(float)
    return pd.DataFrame(
        {"utc_hour_ending": hours.reset_index(drop=True), "wnd_mw": values.reset_index(drop=True)}
    )


def _half_year(first: date, last: date, name: str, today: date, newest: bool) -> pd.DataFrame:
    """One six-month file's SWPP rows, from the host cache when the half-year is closed.
    The newest file may not exist yet in the first days of a half-year; it is then empty."""
    closed = (today - last).days > CLOSED_AFTER
    path = CACHE / f"{name.removesuffix('.csv')}_SWPP.{CACHE_VERSION}.csv"
    if closed and path.is_file():
        cached = pd.read_csv(path)
        cached["utc_hour_ending"] = pd.to_datetime(cached["utc_hour_ending"], utc=True)
        return cached
    body = _get(f"{FILES}/{name}")
    if body is None:
        if newest:
            return pd.DataFrame(
                {
                    "utc_hour_ending": pd.Series(dtype="datetime64[ns, UTC]"),
                    "wnd_mw": pd.Series(dtype=float),
                }
            )
        raise RuntimeError(f"EIA no longer serves {FILES}/{name}")
    rows = _parse(body, name)
    expected = sum(len(_day_hours(day.date())) for day in pd.date_range(first, last))
    if closed and rows["utc_hour_ending"].nunique() == len(rows) == expected:
        CACHE.mkdir(parents=True, exist_ok=True)
        part = path.with_name(f"{path.name}.{os.getpid()}.part")
        rows.to_csv(part, index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
        part.replace(path)
    return rows


def hourly(start: date, end: date) -> pd.DataFrame:
    """SWPP's hourly WND values for the Central-time days start..end, as published now."""
    if start < FIRST_SPLIT or start > end:
        raise ValueError(f"History must start on or after {FIRST_SPLIT} and before {end}.")
    today = _now().date()
    halves = _half_years(start, end)
    rows = pd.concat(
        [_half_year(*half, today, newest=half == halves[-1]) for half in halves],
        ignore_index=True,
    )
    begin = rows["utc_hour_ending"] - pd.Timedelta(hours=1)
    rows.insert(0, "date", begin.dt.tz_convert(ZONE).dt.strftime("%Y-%m-%d"))
    rows = rows[(rows["date"] >= start.isoformat()) & (rows["date"] <= end.isoformat())]
    return rows.sort_values("utc_hour_ending").reset_index(drop=True)


def daily(rows: pd.DataFrame, start: date, end: date) -> pd.Series:
    """Each Central-time day's total in MWh: the sum of its hourly values (one hour at
    x MW is x MWh), NaN unless every hour of the day has exactly one value."""
    rows = rows.dropna(subset=["wnd_mw"]).drop_duplicates(["utc_hour_ending", "wnd_mw"])
    days = rows.groupby("date").agg(
        total=("wnd_mw", "sum"), n=("wnd_mw", "size"), hours=("utc_hour_ending", "nunique")
    )
    index = pd.date_range(start, end, name="timestamp")
    expected = pd.Series([len(_day_hours(d)) for d in index.date], index=index.strftime("%Y-%m-%d"))
    days = days.reindex(expected.index)
    complete = (days["n"] == expected) & (days["hours"] == expected)
    values = days["total"].where(complete).to_numpy(dtype=float)
    return pd.Series(values, index=index, name="target")


def _openmeteo(base: str, params: dict) -> pd.DataFrame:
    """Hourly 100 m wind speed (m/s) at every site, one column per site, indexed by UTC hour."""
    query = {
        "latitude": ",".join(str(lat) for lat, _, _ in SITES.values()),
        "longitude": ",".join(str(lon) for _, lon, _ in SITES.values()),
        "hourly": "wind_speed_100m",
        "wind_speed_unit": "ms",
        "timeformat": "unixtime",
        "timezone": "GMT",
    } | params
    body = _get(f"{base}?{urlencode(query)}")
    if body is None:
        raise RuntimeError(f"Open-Meteo has no {base}")
    payload = json.loads(body)
    responses = payload if isinstance(payload, list) else [payload]
    columns = {}
    for column, response in zip(WEATHER_COLUMNS, responses, strict=True):
        times = pd.to_datetime(response["hourly"]["time"], unit="s", utc=True)
        columns[column] = pd.Series(response["hourly"]["wind_speed_100m"], index=times, dtype=float)
    return pd.DataFrame(columns).rename_axis("utc_time")


def _daily_mean(frame: pd.DataFrame) -> pd.DataFrame:
    """Mean over each Central-time day, for days with a value at every hour (23 to 25)."""
    days = frame.index.tz_convert(ZONE).strftime("%Y-%m-%d")
    grouped = frame.groupby(days)
    means = grouped.mean().where(grouped.count() >= 23)
    means.index = pd.DatetimeIndex(means.index, name="timestamp")
    return means


def weather(start: date, run_date: date, horizon: int) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Daily mean 100 m wind per site: Open-Meteo's historical weather up to the day before
    run_date, the ECMWF forecast published now from run_date on. The forecast's past days
    fill a recent day the archive still lacks."""
    params = {"models": WEATHER_MODEL, "past_days": 7, "forecast_days": horizon + 2}
    forecast_hourly = _openmeteo(FORECAST, params)
    past_hourly = _openmeteo(ARCHIVE, {"start_date": start, "end_date": run_date})
    index = pd.date_range(start, run_date + timedelta(days=horizon), name="timestamp")
    forecast = _daily_mean(forecast_hourly).reindex(index)
    frame = _daily_mean(past_hourly).reindex(index).combine_first(forecast)
    future = index >= pd.Timestamp(run_date)
    frame.loc[future] = forecast.loc[future]
    window = forecast_hourly[forecast_hourly.index >= datetime.combine(run_date, clock(), ZONE)]
    return frame, window.reset_index(), {"url": FORECAST, **params}


def snapshot(run_date: date, spec: dict) -> dict:
    as_of = _guard(run_date, spec)
    start = date.fromisoformat(spec["x"]["history_start"])
    horizon = max(spec["horizons"])
    end = run_date - timedelta(days=1)
    rows = hourly(start, end)
    history = daily(rows, start, end)
    last = history.last_valid_index()
    if last is None or last.date() < run_date - timedelta(days=MAX_LAG_DAYS):
        raise RuntimeError("EIA has published no complete SWPP day in the past week.")
    wind, forecast_hourly, forecast_source = weather(start, run_date, horizon)
    _guard(run_date, spec)  # a slow collection crossing the cutoff must not be archived
    index = pd.date_range(start, run_date + timedelta(days=horizon), name="timestamp")
    series = pd.concat(
        [
            history.reindex(index),
            wind,
            pd.DataFrame({"month": index.month, "day_of_year": index.dayofyear}, index=index),
        ],
        axis=1,
    )
    raw_rows = rows.assign(utc_hour_ending=rows["utc_hour_ending"].dt.strftime("%Y-%m-%dT%H:%MZ"))
    return {
        "series": series,
        "last_observed": last.date(),
        "raw": {
            "swpp_wnd_hourly.csv": raw_rows,
            "wind_forecast_hourly.csv": forecast_hourly.assign(
                utc_time=forecast_hourly["utc_time"].dt.strftime("%Y-%m-%dT%H:%MZ")
            ),
        },
        "sources": {
            "wind_generation": {
                "name": "U.S. Energy Information Administration, Form EIA-930",
                "url": PAGE,
                "files": [f"{FILES}/{name}" for *_, name in _half_years(start, end)],
                "balancing_authority": "SWPP",
                "column": WIND,
            },
            "weather_past": {"url": ARCHIVE},
            "weather_forecast": forecast_source,
            "as_of": as_of.isoformat(),
        },
        "covariate_notes": {
            "wind100_<site>": "mean wind speed at 100 m (m/s) over the Central-time day at "
            "the site; dates before run_date are Open-Meteo's historical weather, run_date "
            f"onward the {WEATHER_MODEL} forecast published before the snapshot",
            "sites": {site: place for site, (*_, place) in SITES.items()},
            "month": "calendar month, 1 to 12",
            "day_of_year": "day of the year, 1 to 366",
        },
    }


def truth(today: date, spec: dict) -> pd.Series:
    """Every complete day's total published so far; incomplete days are left out, so
    update_truth records a day once EIA has all its hours."""
    if today > _now().date():
        raise RuntimeError("Cannot settle future dates.")
    start = date.fromisoformat(spec["x"]["history_start"])
    end = today - timedelta(days=1)
    return daily(hourly(start, end), start, end).dropna()


def baseline(run_date: date, spec: dict) -> dict:
    """This target has no expert forecast; the engine scores against its weekly seasonal
    naive instead."""
    return {
        "source": {"name": "none: no expert forecast of SWPP's EIA-930 wind generation"},
        "forecasts": [],
    }
