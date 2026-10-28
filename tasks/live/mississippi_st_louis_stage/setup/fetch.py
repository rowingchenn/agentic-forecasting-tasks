"""Mississippi River stage at St. Louis: where the numbers for this target come from.

The agentic-forecasting pipeline imports this file on the host, outside every episode; the
sandbox's setup stage never runs it, and setup/run.sh makes it unreadable to the agent.
It needs only the standard library and pandas. The pipeline passes `spec`, the target
definition from task.yaml (`extras.agentic-forecasting` plus `params`), and turns the
results into archived files.

    snapshot(run_date, spec)  X_d: stage history and known covariates as published now
    truth(today, spec)        every completed day's mean stage as published now
    baseline(run_date, spec)  the NWS river forecast, the first issue after the cutoff

No credentials: the USGS Water Data API, the Iowa Environmental Mesonet's archive of NWS
river forecasts and Open-Meteo are keyless. The NWS shows the same USGS readings for this
gauge (EADM7).
"""

from __future__ import annotations

import io
import json
import time
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

USGS_API = "https://api.waterdata.usgs.gov/ogcapi/v1/collections/continuous/items"
USGS_SITE = "USGS-07010000"  # Mississippi River at St. Louis, MO
GAUGE = "EADM7"  # the same gauge's NWS location ID
RFC = "NCRFC"  # the NWS river forecast center that forecasts it
IEM_HML = "https://mesonet.agron.iastate.edu/cgi-bin/request/hml.py"
WEATHER_MODEL = "ecmwf_ifs025"
MIN_HOURS = 12  # a day's mean needs readings in at least half of its hours
RATE_LIMIT_PAUSE = 61  # seconds; Open-Meteo answers 429 until its minute's quota refills
# Sub-basin draining to St. Louis: (representative city, lat, lon)
BASINS = {
    "upper_mississippi": ("La Crosse", 43.80, -91.24),
    "iowa": ("Des Moines", 41.59, -93.62),
    "illinois": ("Peoria", 40.69, -89.59),
    "missouri_platte": ("Omaha", 41.26, -95.94),
    "kansas": ("Kansas City", 39.10, -94.58),
    "missouri_lower": ("Columbia", 38.95, -92.33),
}
PRECIP_COLUMNS = [f"precip_{basin}" for basin in BASINS]


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


def _utc(day: date, timezone: str) -> datetime:
    """Local midnight at the start of `day`, in UTC."""
    return datetime(day.year, day.month, day.day, tzinfo=ZoneInfo(timezone)).astimezone(UTC)


def _at(day: date, clock: str, timezone: str) -> datetime:
    """`clock` ("HH:MM", local) on `day`, in UTC."""
    hour, minute = map(int, clock.split(":"))
    return datetime(
        day.year, day.month, day.day, hour, minute, tzinfo=ZoneInfo(timezone)
    ).astimezone(UTC)


def _iem(kind: str, start: datetime, end: datetime) -> pd.DataFrame:
    """IEM's processed HML data for the gauge, `obs` or `forecasts`, times in UTC."""
    query = urllib.parse.urlencode(
        {
            "station": GAUGE,
            "kind": kind,
            "sts": f"{start:%Y-%m-%dT%H:%MZ}",
            "ets": f"{end:%Y-%m-%dT%H:%MZ}",
            "tz": "UTC",
            "fmt": "csv",
        }
    )
    return pd.read_csv(io.BytesIO(_get(f"{IEM_HML}?{query}")))


def _openmeteo(base: str, **params: object) -> list[dict]:
    """One Open-Meteo response per requested location, in request order."""
    payload = json.loads(_get(f"{base}?{urllib.parse.urlencode(params)}"))
    return payload if isinstance(payload, list) else [payload]


def _coords() -> dict[str, str]:
    return {
        "latitude": ",".join(str(lat) for *_, lat, _ in BASINS.values()),
        "longitude": ",".join(str(lon) for *_, lon in BASINS.values()),
    }


def stage_readings(first_day: date, end_day: date, timezone: str) -> pd.DataFrame:
    """Every gauge height reading (ft) on the local days first_day to end_day - 1."""
    start, end = _utc(first_day, timezone), _utc(end_day, timezone)
    query = urllib.parse.urlencode(
        {
            "monitoring_location_id": USGS_SITE,
            "parameter_code": "00065",  # gage height
            "datetime": f"{start:%Y-%m-%dT%H:%M:%SZ}/{end:%Y-%m-%dT%H:%M:%SZ}",
            "properties": "time,value",
            "skipGeometry": "true",
            "limit": 50_000,  # the API's maximum; longer spans come in pages
            "f": "json",
        }
    )
    url: str | None = f"{USGS_API}?{query}"
    rows: list[dict] = []
    while url:
        page = json.loads(_get(url))
        rows += [feature["properties"] for feature in page["features"]]
        url = next((link["href"] for link in page["links"] if link["rel"] == "next"), None)
    frame = pd.DataFrame(
        {
            "time": pd.to_datetime([row["time"] for row in rows], utc=True),
            "stage": pd.to_numeric([row["value"] for row in rows], errors="coerce"),
        }
    ).dropna()
    return frame[frame["time"] < end].sort_values("time").reset_index(drop=True)


def daily_mean(readings: pd.DataFrame, timezone: str) -> pd.Series:
    """Mean stage of each local calendar day with readings in at least MIN_HOURS hours."""
    day = readings["time"].dt.tz_convert(timezone).dt.tz_localize(None).dt.normalize()
    hours = readings["time"].dt.floor("h").groupby(day).nunique()  # UTC hours: no DST repeats
    means = readings["stage"].groupby(day).mean()
    return means[hours >= MIN_HOURS]


def target_history(stage: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """Daily means on `index`, empty after the last observed day. A day missing between two
    observed days is filled linearly (truth leaves it empty): the pipeline's seasonal naive
    reads the last observed week of X_d and cannot score against a missing value."""
    return stage.reindex(index).interpolate(limit_area="inside")


def year_spans(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """[start, end) (UTC) cut at each New Year: IEM serves forecasts one UTC year per request,
    selecting start <= issued < end."""
    spans = []
    while start.year < end.year:
        new_year = datetime(start.year + 1, 1, 1, tzinfo=UTC)
        spans.append((start, new_year))
        start = new_year
    return [*spans, (start, end)]


def nws_forecasts(start: datetime, end: datetime) -> pd.DataFrame:
    """Every NWS stage forecast (ft) for the gauge issued from start to end (UTC), one row
    per issuance and valid time."""
    raw = pd.concat([_iem("forecasts", *span) for span in year_spans(start, end)])
    if raw.empty:  # IEM's header for no rows lacks the [UTC] suffixes
        return pd.DataFrame(columns=["issued", "valid", "stage"])
    raw = raw[(raw["primaryname"] == "Stage") & (raw["primaryunits"] == "ft")]
    frame = pd.DataFrame(
        {
            "issued": pd.to_datetime(raw["issued[UTC]"], utc=True),
            "valid": pd.to_datetime(raw["forecast_valid[UTC]"], utc=True),
            "stage": pd.to_numeric(raw["primary_value"]),
        }
    )
    # IEM repeats a valid time within some issuances, with the same value
    return frame.drop_duplicates(["issued", "valid"]).reset_index(drop=True)


def forecast_daily(rows: pd.DataFrame, timezone: str) -> pd.Series:
    """Mean of each local day's four six-hourly forecast values; days the forecast covers
    only in part, its first and last, are left out."""
    day = rows["valid"].dt.tz_convert(timezone).dt.tz_localize(None).dt.normalize()
    days = rows["stage"].groupby(day).agg(["mean", "size"])
    return days.loc[days["size"] == 4, "mean"]


def issue_daily(forecasts: pd.DataFrame, timezone: str) -> pd.DataFrame:
    """Each issuance's daily means: one row (issued, day, value) per day it covers in full."""
    parts = [
        forecast_daily(rows, timezone)
        .rename("value")
        .rename_axis("day")
        .reset_index()
        .assign(issued=issued)
        for issued, rows in forecasts.groupby("issued")
    ]
    return pd.concat(parts, ignore_index=True)


def nws_as_seen(
    daily: pd.DataFrame, index: pd.DatetimeIndex, run_date: date, spec: dict
) -> pd.Series:
    """The NWS forecast of each day on `index` as a run could see it: from the latest issuance
    before the snapshot of the day before, or of the run date for the days after it. So the
    history holds the forecasts one day ahead, and the forecast window today's latest issue.
    A past day no issuance covered is filled in linearly."""
    snapshot, timezone = spec["schedule"]["snapshot"], spec["schedule"]["timezone"]
    as_of = pd.Series(
        [_at(min(day.date() - timedelta(days=1), run_date), snapshot, timezone) for day in index],
        index=index,
        name="as_of",
    )
    rows = daily.merge(as_of, left_on="day", right_index=True)
    rows = rows[rows["issued"] <= rows["as_of"]].sort_values("issued")
    seen = rows.groupby("day")["value"].last().reindex(index)
    past = index < pd.Timestamp(run_date)
    seen[past] = seen[past].interpolate(limit_area="inside")
    return seen


def precipitation_past(start: date, end: date, timezone: str) -> pd.DataFrame:
    """Daily precipitation (mm) per basin city from ERA5 reanalysis (Open-Meteo archive)."""
    responses = _openmeteo(
        "https://archive-api.open-meteo.com/v1/archive",
        **_coords(),
        start_date=start,
        end_date=end,
        daily="precipitation_sum",
        timezone=timezone,
    )
    frame = pd.DataFrame(index=pd.to_datetime(responses[0]["daily"]["time"]))
    for basin, response in zip(BASINS, responses, strict=True):
        frame[f"precip_{basin}"] = response["daily"]["precipitation_sum"]
    return frame[PRECIP_COLUMNS]


def precipitation_forecast(horizon: int, timezone: str) -> tuple[pd.DataFrame, dict[str, object]]:
    """Latest published hourly precipitation forecast per basin city."""
    params: dict[str, object] = {
        **_coords(),
        "hourly": "precipitation",
        "models": WEATHER_MODEL,
        "timezone": timezone,
        "forecast_days": horizon + 3,  # 16 at most
        "past_days": 7,  # covers the last days if the reanalysis archive has not caught up
    }
    base = "https://api.open-meteo.com/v1/forecast"
    frames = [
        pd.DataFrame(
            {
                "time": pd.to_datetime(response["hourly"]["time"]),
                "basin": basin,
                "precipitation": response["hourly"]["precipitation"],
            }
        )
        for basin, response in zip(BASINS, _openmeteo(base, **params), strict=True)
    ]
    source = {"url": base, **{k: v for k, v in params.items() if k not in _coords()}}
    return pd.concat(frames, ignore_index=True).dropna(), source


def daily_from_hourly(hourly: pd.DataFrame) -> pd.DataFrame:
    hourly = hourly.assign(day=hourly["time"].dt.normalize())
    full = hourly.groupby(["day", "basin"])["precipitation"].agg(["sum", "size"])
    full = full[full["size"] >= 23]
    wide = full["sum"].unstack("basin")
    wide.columns = [f"precip_{basin}" for basin in wide.columns]
    return wide[PRECIP_COLUMNS]


def snapshot(run_date: date, spec: dict) -> dict:
    """X_d as published right now: one row per day from history_start to the last target
    date, `target` empty after the last complete day, covariates known for the window."""
    start = date.fromisoformat(spec["x"]["history_start"])
    timezone = spec["schedule"]["timezone"]
    horizon = max(spec["horizons"])
    readings = stage_readings(start, run_date, timezone)
    stage = daily_mean(readings, timezone)
    last_observed = stage.index.max().date()
    forecast_hourly, forecast_source = precipitation_forecast(horizon, timezone)

    index = pd.date_range(start, run_date + timedelta(days=horizon), freq="D", name="timestamp")
    forecast = daily_from_hourly(forecast_hourly).reindex(index)
    # Reanalysis up to yesterday (forecast fills days the archive lacks), forecast from today.
    rain = precipitation_past(start, run_date - timedelta(days=1), timezone).reindex(index)
    rain = rain.combine_first(forecast)
    future = index >= pd.Timestamp(run_date)
    rain.loc[future] = forecast.loc[future]
    # every issuance from the one before history_start's first run to now
    forecasts = nws_forecasts(_utc(start - timedelta(days=3), timezone), datetime.now(UTC))
    nws = nws_as_seen(issue_daily(forecasts, timezone), index, run_date, spec).rename("nws")
    latest = forecasts[forecasts["issued"] == forecasts["issued"].max()]
    series = pd.concat([target_history(stage, index).rename("target"), nws, rain], axis=1)
    return {
        "series": series.rename_axis("timestamp"),
        "last_observed": last_observed,
        "raw": {
            "stage_readings.csv": readings,
            "nws_forecast_latest.csv": latest,
            "precipitation_forecast_hourly.csv": forecast_hourly[
                forecast_hourly["time"] >= pd.Timestamp(run_date)
            ],
        },
        "sources": {
            "stage": {"url": USGS_API, "site": USGS_SITE, "parameter_code": "00065"},
            "nws": {"url": IEM_HML, "station": GAUGE, "latest_issued": str(latest["issued"].max())},
            "precipitation_past": {"url": "https://archive-api.open-meteo.com/v1/archive"},
            "precipitation_forecast": forecast_source,
        },
        "covariate_notes": {
            "target": "daily mean stage (ft); a day with readings in fewer than "
            f"{MIN_HOURS} hours is filled linearly from its neighbours",
            "nws": "NWS river forecast of the day's mean stage (ft), the mean of its four "
            "six-hourly values: from the latest issuance before the previous day's snapshot "
            "(one day ahead) up to run_date, from the latest before this snapshot after it",
            "precip_<basin>": "daily precipitation total (mm) at the basin's representative "
            "city; dates before run_date are ERA5 reanalysis, run_date onward is the "
            f"{WEATHER_MODEL} forecast issued before the snapshot",
            "basins": {basin: city for basin, (city, *_) in BASINS.items()},
        },
    }


def truth(today: date, spec: dict) -> pd.Series:
    """Mean stage of every completed day published so far."""
    timezone = spec["schedule"]["timezone"]
    start = date.fromisoformat(spec["x"]["history_start"])
    return daily_mean(stage_readings(start, today, timezone), timezone)


def baseline(run_date: date, spec: dict) -> dict:
    """The NWS river forecast the run is compared with: the first issuance after the cutoff,
    which the agents cannot see, as the daily mean of its six-hourly stages. Empty until it
    is issued; IEM keeps every issuance, so a later call gives the same numbers."""
    timezone = spec["schedule"]["timezone"]
    cutoff = _at(run_date, spec["schedule"]["cutoff"], timezone)
    targets = [(run_date + timedelta(days=h)).isoformat() for h in spec["horizons"]]
    forecasts = nws_forecasts(cutoff, cutoff + timedelta(days=2))
    forecasts = forecasts[forecasts["issued"] > cutoff]
    if forecasts.empty:
        source = {"report": f"no NWS river forecast for {GAUGE} issued yet after the cutoff"}
        return {"source": source | {"posted": None, "model": RFC}, "forecasts": []}
    issued = forecasts["issued"].min()
    daily = forecast_daily(forecasts[forecasts["issued"] == issued], timezone)
    return {
        "source": {
            "report": f"NWS river forecast for {GAUGE} (HML), archived by IEM",
            "posted": issued.isoformat(),
            "model": RFC,
        },
        "forecasts": [
            {"target_date": t, "point": round(float(daily[pd.Timestamp(t)]), 2)}
            for t in targets
            if pd.Timestamp(t) in daily.index
        ],
    }
