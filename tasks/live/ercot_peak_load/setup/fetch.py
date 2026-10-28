"""ERCOT daily peak load: where the numbers for this target come from.

The agentic-forecasting pipeline imports this file on the host, outside every episode; the
sandbox's setup stage never runs it, and setup/run.sh makes it unreadable to the agent.
It needs only the standard library and pandas. The pipeline passes `spec`, the target
definition from task.yaml (`extras.agentic-forecasting` plus `params`), and turns the
results into archived files.

    snapshot(run_date, spec)  X_d: target history and known covariates as published now
    truth(today, spec)        every completed operating day's peak as published now
    baseline(run_date, spec)  ERCOT's own forecast, the first version after the cutoff

Credentials come from the environment: ERCOT_USERNAME, ERCOT_PASSWORD and
ERCOT_SUBSCRIPTION_KEY (a free ERCOT Public API account).
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

ERCOT_API = "https://api.ercot.com/api/public-reports"
ERCOT_TOKEN_URL = "https://ercotb2c.b2clogin.com/ercotb2c.onmicrosoft.com/B2C_1_PUBAPI-ROPC-FLOW/oauth2/v2.0/token"
ERCOT_CLIENT_ID = "fec253ea-0d06-4272-a5e6-b478baeecd70"
LOAD_REPORT = "np6-345-cd/act_sys_load_by_wzn"
FORECAST_REPORT = "np3-565-cd/lf_by_model_weather_zone"
WEATHER_MODEL = "ecmwf_ifs025"
# ERCOT weather zone: (NP6-345-CD field, representative city, lat, lon)
ZONES = {
    "coast": ("coast", "Houston", 29.76, -95.37),
    "east": ("east", "Tyler", 32.35, -95.30),
    "far_west": ("farWest", "Midland", 32.00, -102.08),
    "north": ("north", "Wichita Falls", 33.91, -98.49),
    "north_central": ("northC", "Dallas", 32.78, -96.80),
    "south_central": ("southC", "Austin", 30.27, -97.74),
    "southern": ("southern", "Corpus Christi", 27.80, -97.40),
    "west": ("west", "Abilene", 32.45, -99.73),
}
WEATHER_COLUMNS = [f"t{stat}_{zone}" for zone in ZONES for stat in ("max", "min")]
# Past versions of ERCOT's forecast, one per day, as they stood at that day's snapshot.
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "agentic-forecasting"
VERSIONS = CACHE / "ercot_peak_load" / "official_versions.csv"
VERSION_COLUMNS = ["version_day", "posted", "day", "peak"]
ERCOT_PAUSE = 2.1  # ponytail: seconds between archive requests, under 30 a minute


def _get(url: str, headers: dict[str, str] | None = None, retries: int = 4) -> object:
    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(request, timeout=180) as response:
                return json.load(response)
        except OSError:
            if attempt == retries - 1:
                raise
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


_token: tuple[float, str] | None = None  # (time.monotonic() when issued, ID token)


def _ercot_token() -> str:
    """An ID token for the ERCOT Public API. Tokens last an hour; this one is renewed after
    50 minutes, for long jobs such as the first fill of the forecast cache."""
    global _token
    if _token is None or time.monotonic() - _token[0] > 3000:
        _token = (time.monotonic(), _new_token())
    return _token[1]


def _new_token() -> str:
    body = urllib.parse.urlencode(
        {
            "username": os.environ["ERCOT_USERNAME"],
            "password": os.environ["ERCOT_PASSWORD"],
            "grant_type": "password",
            "scope": f"openid {ERCOT_CLIENT_ID} offline_access",
            "client_id": ERCOT_CLIENT_ID,
            "response_type": "id_token",
        }
    ).encode()
    with urllib.request.urlopen(ERCOT_TOKEN_URL, data=body, timeout=60) as response:
        return json.load(response)["id_token"]


def _ercot(report: str, **params: object) -> list[dict[str, object]]:
    """All rows of an ERCOT public report."""
    headers = {
        "Authorization": f"Bearer {_ercot_token()}",
        "Ocp-Apim-Subscription-Key": os.environ["ERCOT_SUBSCRIPTION_KEY"],
    }
    rows: list[dict[str, object]] = []
    page = 1
    while True:
        query = urllib.parse.urlencode({**params, "size": 100_000, "page": page})
        payload = _get(f"{ERCOT_API}/{report}?{query}", headers)
        assert isinstance(payload, dict), payload
        names = [field["name"] for field in payload["fields"]]
        rows += [dict(zip(names, row, strict=True)) for row in payload["data"]]
        if page >= payload["_meta"]["totalPages"]:
            return rows
        page += 1


def _openmeteo(base: str, **params: object) -> list[dict]:
    """One Open-Meteo response per requested location, in request order."""
    payload = _get(f"{base}?{urllib.parse.urlencode(params)}")
    return payload if isinstance(payload, list) else [payload]  # type: ignore[return-value]


def _coords() -> dict[str, str]:
    return {
        "latitude": ",".join(str(lat) for *_, lat, _ in ZONES.values()),
        "longitude": ",".join(str(lon) for *_, lon in ZONES.values()),
    }


def load_hourly(first_day: date, last_day: date) -> pd.DataFrame:
    rows = _ercot(LOAD_REPORT, operatingDayFrom=first_day, operatingDayTo=last_day)
    frame = pd.DataFrame(rows).rename(
        columns={field: zone for zone, (field, *_) in ZONES.items()}
        | {"operatingDay": "operating_day", "hourEnding": "hour_ending", "DSTFlag": "dst"}
    )
    frame["hour_ending"] = frame["hour_ending"].str.split(":").str[0].astype(int)
    return frame.sort_values(["operating_day", "hour_ending", "dst"]).reset_index(drop=True)


def daily_peak(hourly: pd.DataFrame) -> pd.Series:
    """Max hourly system load per operating day, for complete days only (23-25 hours)."""
    days = hourly.groupby("operating_day")["total"].agg(["max", "size"])
    complete = days[days["size"] >= 23]["max"]
    complete.index = pd.to_datetime(complete.index)
    return complete


def weather_past(start: date, end: date, timezone: str) -> pd.DataFrame:
    """Daily max/min 2 m temperature per zone from ERA5 reanalysis (Open-Meteo archive)."""
    responses = _openmeteo(
        "https://archive-api.open-meteo.com/v1/archive",
        **_coords(),
        start_date=start,
        end_date=end,
        daily="temperature_2m_max,temperature_2m_min",
        timezone=timezone,
    )
    frame = pd.DataFrame(index=pd.to_datetime(responses[0]["daily"]["time"]))
    for zone, response in zip(ZONES, responses, strict=True):
        frame[f"tmax_{zone}"] = response["daily"]["temperature_2m_max"]
        frame[f"tmin_{zone}"] = response["daily"]["temperature_2m_min"]
    return frame[WEATHER_COLUMNS]


def weather_forecast(horizon: int, timezone: str) -> tuple[pd.DataFrame, dict[str, object]]:
    """Latest published hourly 2 m temperature forecast per zone city."""
    params: dict[str, object] = {
        **_coords(),
        "hourly": "temperature_2m",
        "models": WEATHER_MODEL,
        "timezone": timezone,
        "forecast_days": horizon + 3,
        "past_days": 2,  # covers yesterday if the reanalysis archive has not caught up
    }
    base = "https://api.open-meteo.com/v1/forecast"
    frames = [
        pd.DataFrame(
            {
                "time": pd.to_datetime(response["hourly"]["time"]),
                "zone": zone,
                "temperature_2m": response["hourly"]["temperature_2m"],
            }
        )
        for zone, response in zip(ZONES, _openmeteo(base, **params), strict=True)
    ]
    source = {"url": base, **{k: v for k, v in params.items() if k not in _coords()}}
    return pd.concat(frames, ignore_index=True).dropna(), source


def daily_from_hourly(hourly: pd.DataFrame) -> pd.DataFrame:
    hourly = hourly.assign(day=hourly["time"].dt.normalize())
    full = hourly.groupby(["day", "zone"])["temperature_2m"].agg(["max", "min", "size"])
    full = full[full["size"] >= 23]
    wide = full[["max", "min"]].unstack("zone")
    wide.columns = [f"t{stat}_{zone}" for stat, zone in wide.columns]
    return wide[WEATHER_COLUMNS]


def calendar(index: pd.DatetimeIndex) -> pd.DataFrame:
    holidays = USFederalHolidayCalendar().holidays(index.min(), index.max())
    return pd.DataFrame(
        {"dow": index.dayofweek, "holiday": index.isin(holidays).astype(int)}, index=index
    )


def _snapshot_at(day: date, spec: dict) -> datetime:
    """`day`'s snapshot in Central time, without a zone, as ERCOT's postedDatetime."""
    hour, minute = map(int, spec["schedule"]["snapshot"].split(":"))
    return datetime(day.year, day.month, day.day, hour, minute)


def official_version(day: date, spec: dict) -> pd.DataFrame:
    """ERCOT's in-use seven-day forecast as it stood at `day`'s snapshot (the latest version
    posted in the six hours before it), one row (version_day, posted, day, peak) per delivery
    date, with peak the daily maximum of its hourly system total (MW)."""
    at = _snapshot_at(day, spec)
    rows = _ercot(
        FORECAST_REPORT,
        postedDatetimeFrom=(at - timedelta(hours=6)).isoformat(),
        postedDatetimeTo=at.isoformat(),
        inUseFlag="true",
    )
    if not rows:
        return pd.DataFrame(columns=VERSION_COLUMNS)
    frame = pd.DataFrame(rows)
    latest = frame[frame["postedDatetime"] == frame["postedDatetime"].max()]
    peaks = latest.groupby("deliveryDate")["systemTotal"].max()
    return pd.DataFrame(
        {
            "version_day": day.isoformat(),
            "posted": latest["postedDatetime"].iloc[0],
            "day": peaks.index,
            "peak": peaks.round(2).to_numpy(),
        }
    )


def official_versions(first_day: date, run_date: date, spec: dict) -> pd.DataFrame:
    """official_version of every day from first_day to run_date. Past versions never change,
    so each is fetched once and kept in VERSIONS; a day's is kept once its snapshot passed."""
    cached = (
        pd.read_csv(VERSIONS, dtype={"version_day": str, "posted": str, "day": str})
        if VERSIONS.is_file()
        else pd.DataFrame(columns=VERSION_COLUMNS)
    )
    have = set(cached["version_day"])
    now = datetime.now(ZoneInfo(spec["schedule"]["timezone"])).replace(tzinfo=None)
    parts = [cached]
    for day in pd.date_range(first_day, run_date).date:
        if day.isoformat() in have:
            continue
        version = official_version(day, spec)
        parts.append(version)
        if now >= _snapshot_at(day, spec) and not version.empty:
            VERSIONS.parent.mkdir(parents=True, exist_ok=True)
            version.to_csv(VERSIONS, mode="a", header=not VERSIONS.is_file(), index=False)
        time.sleep(ERCOT_PAUSE)
    return pd.concat([p for p in parts if not p.empty], ignore_index=True)


def official_as_seen(versions: pd.DataFrame, index: pd.DatetimeIndex, run_date: date) -> pd.Series:
    """ERCOT's forecast of each day's peak as a run could see it: from the version at the
    snapshot of the day before, or of the run date for the days after it. So the history
    holds one-day-ahead forecasts and the window the run date's version. A past day without
    one is filled in linearly."""
    peak = versions.set_index(["version_day", "day"])["peak"].to_dict()
    keys = [
        (min(t.date() - timedelta(days=1), run_date).isoformat(), t.date().isoformat())
        for t in index
    ]
    seen = pd.Series([peak.get(key) for key in keys], index=index, dtype=float)
    past = index < pd.Timestamp(run_date)
    seen[past] = seen[past].interpolate(limit_area="inside")
    return seen


def snapshot(run_date: date, spec: dict) -> dict:
    """X_d as published right now: one row per day from history_start to the last target
    date, `target` empty after the last complete day, covariates known for the window."""
    start = date.fromisoformat(spec["x"]["history_start"])
    timezone = spec["schedule"]["timezone"]
    horizon = max(spec["horizons"])
    hourly = load_hourly(start, run_date - timedelta(days=1))
    peaks = daily_peak(hourly)
    last_observed = peaks.index.max().date()
    forecast_hourly, forecast_source = weather_forecast(horizon, timezone)

    index = pd.date_range(start, run_date + timedelta(days=horizon), freq="D", name="timestamp")
    forecast = daily_from_hourly(forecast_hourly).reindex(index)
    # Reanalysis up to yesterday (forecast fills a day the archive lacks), forecast from today.
    weather = weather_past(start, run_date - timedelta(days=1), timezone).reindex(index)
    weather = weather.combine_first(forecast)
    future = index >= pd.Timestamp(run_date)
    weather.loc[future] = forecast.loc[future]
    versions = official_versions(start - timedelta(days=1), run_date, spec)
    official = official_as_seen(versions, index, run_date).rename("ercot_official")
    latest = versions[versions["version_day"] == run_date.isoformat()]
    target = peaks.rename("target").reindex(index)
    series = pd.concat([target, official, weather, calendar(index)], axis=1)
    return {
        "series": series.rename_axis("timestamp"),
        "last_observed": last_observed,
        "raw": {
            "load_hourly.csv": hourly[hourly["operating_day"] <= last_observed.isoformat()],
            "load_forecast_latest.csv": latest,
            "temperature_forecast_hourly.csv": forecast_hourly[
                forecast_hourly["time"] >= pd.Timestamp(run_date)
            ],
        },
        "sources": {
            "load": {"url": f"{ERCOT_API}/{LOAD_REPORT}"},
            "official": {
                "url": f"{ERCOT_API}/{FORECAST_REPORT}",
                "posted": latest["posted"].iloc[0] if len(latest) else None,
            },
            "weather_past": {"url": "https://archive-api.open-meteo.com/v1/archive"},
            "weather_forecast": forecast_source,
        },
        "covariate_notes": {
            "ercot_official": "ERCOT's in-use seven-day forecast of the day's peak (MW), the "
            "latest version before a snapshot: the previous day's snapshot (one day ahead) up "
            "to run_date, this snapshot after it",
            "tmax_<zone>, tmin_<zone>": "daily max/min 2 m temperature (deg C) at the zone's "
            "representative city; dates before run_date are ERA5 reanalysis, run_date onward "
            f"is the {WEATHER_MODEL} forecast issued before the snapshot",
            "zones": {zone: city for zone, (_, city, *_) in ZONES.items()},
            "dow": "day of week, Monday = 0",
            "holiday": "US federal holiday (1) or not (0)",
        },
    }


def truth(today: date, spec: dict) -> pd.Series:
    """Daily peaks of every complete operating day published so far."""
    return daily_peak(load_hourly(date.fromisoformat(spec["x"]["history_start"]), today))


def baseline(run_date: date, spec: dict) -> dict:
    """ERCOT's in-use seven-day forecast the run is compared with: the first version posted
    after the cutoff, which the agents cannot see, as the daily maximum of its hourly system
    total. Empty until it is posted; ERCOT keeps every version."""
    zone = ZoneInfo(spec["schedule"]["timezone"])
    hour, minute = map(int, spec["schedule"]["cutoff"].split(":"))
    cutoff = datetime(run_date.year, run_date.month, run_date.day, hour, minute, tzinfo=zone)
    local_cutoff = cutoff.replace(tzinfo=None)  # postedDatetime is Central time
    rows = _ercot(
        FORECAST_REPORT,
        postedDatetimeFrom=(local_cutoff + timedelta(minutes=1)).isoformat(),
        postedDatetimeTo=(local_cutoff + timedelta(hours=6)).isoformat(),
        inUseFlag="true",
    )
    if not rows:
        source = {"report": FORECAST_REPORT, "posted": None, "model": None}
        return {"source": source, "forecasts": []}
    frame = pd.DataFrame(rows)
    first = frame[frame["postedDatetime"] == frame["postedDatetime"].min()]
    peaks = first.groupby("deliveryDate")["systemTotal"].max()
    targets = [(run_date + timedelta(days=h)).isoformat() for h in spec["horizons"]]
    return {
        "source": {
            "report": FORECAST_REPORT,
            "posted": first["postedDatetime"].iloc[0],
            "model": first["model"].iloc[0],
        },
        "forecasts": [{"target_date": t, "point": round(float(peaks[t]), 2)} for t in targets],
    }
