"""Toronto daytime AQHI: where the numbers for this target come from.

The agentic-forecasting pipeline imports this file on the host, outside every episode; the
sandbox's setup stage never runs it, and setup/run.sh makes it unreadable to the agent.
It needs only the standard library and pandas. The pipeline passes `spec`, the target
definition from task.yaml (`extras.agentic-forecasting` plus `params`), and turns the
results into archived files.

    snapshot(run_date, spec)  X_d: AQHI history and known covariates as published now
    truth(today, spec)        every completed day's daytime maximum as published now
    baseline(run_date, spec)  ECCC's AQHI forecast, its first issue after the cutoff

No credentials: Environment and Climate Change Canada's MSC Datamart and Open-Meteo are
keyless. MSC asks that its directory listings not be polled, so only known file names are
requested, at least a second apart.
"""

from __future__ import annotations

import io
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

DATAMART = "https://dd.weather.gc.ca"
COMMUNITY = "FEUZB"  # ECCC's code for the AQHI community
COMMUNITY_NAME = "Toronto"
ZONE = "ont"  # the community's region: its folder on the Datamart
REGION = "ON"  # and its code in observation file names
LAT, LON = 43.7417, -79.3733  # where the weather covariates are taken
WEATHER_MODEL = "ecmwf_ifs025"
FIRST_STAMP, LAST_STAMP = 7, 18  # hours stamped 07:00 to 18:00 local: ECCC's 06:00-18:00 day
MIN_HOURS = 9  # of those 12, for a day to count
SETTLE_DAYS = 1  # a day is recorded as truth from the realtime file of the day after
TEN_PLUS = 11.0  # the value counted for ECCC's "10+"
REQUEST_PAUSE = 1.0  # seconds between requests
RATE_LIMIT_PAUSE = 61  # seconds; Open-Meteo answers 429 until its minute's quota refills
DAILY = (
    "temperature_2m_max,precipitation_sum,wind_speed_10m_max,wind_direction_10m_dominant,"
    "shortwave_radiation_sum"
)
WEATHER_COLUMNS = ["tmax", "precip", "wind_u", "wind_v", "radiation"]
OFFSET = {1: 0, 3: 1, 5: 2}  # daytime forecast period -> days after the issue's base day
MORNING, EVENING = "06:00", "17:00"  # ECCC's scheduled forecast issues, local time

_last_request = 0.0  # time.monotonic() of the last request


def _get(url: str, retries: int = 5) -> bytes:
    global _last_request
    for attempt in range(retries):
        time.sleep(max(0.0, _last_request + REQUEST_PAUSE - time.monotonic()))
        _last_request = time.monotonic()
        try:
            with urllib.request.urlopen(url, timeout=180) as response:
                return response.read()
        except OSError as exc:
            code = getattr(exc, "code", None)
            if attempt == retries - 1 or code == 404:
                raise
            time.sleep(RATE_LIMIT_PAUSE if code == 429 else 2**attempt)
    raise AssertionError("unreachable")


def _get_optional(url: str) -> bytes | None:
    """The file at url, or None if the server has none."""
    try:
        return _get(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _now(timezone: str) -> datetime:
    return datetime.now(ZoneInfo(timezone))


def _at(day: date, clock: str, timezone: str) -> datetime:
    """`clock` ("HH:MM", local) on `day`, in UTC."""
    hour, minute = map(int, clock.split(":"))
    local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZoneInfo(timezone))
    return local.astimezone(UTC)


def _realtime_url(hour: datetime) -> str:
    """ECCC's observation file for a UTC hour. Files sit in the folder of their UTC day; the
    Datamart's today/ folder holds the current UTC day's only."""
    return (
        f"{DATAMART}/{hour:%Y%m%d}/WXO-DD/air_quality/aqhi/{ZONE}/observation/realtime/csv/"
        f"{hour:%Y%m%d%H}_AQHI_{REGION}_SiteObs.csv"
    )


def parse_obs_csv(raw: bytes, code: str) -> pd.Series:
    """One community's hourly AQHI from an observation file (Date, Hour (UTC), a column per
    community), by UTC time: "10+" counts as TEN_PLUS, a blank as missing."""
    frame = pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False)
    stamps = pd.to_datetime(frame["Date"], format="%Y-%m-%d") + pd.to_timedelta(
        frame["Hour (UTC)"].astype(int), unit="h"
    )
    values = pd.to_numeric(frame[code].replace("10+", str(TEN_PLUS)), errors="coerce")
    return pd.Series(values.to_numpy(), index=pd.DatetimeIndex(stamps).tz_localize("UTC"))


def hourly_history(first_day: date, today: date) -> pd.Series:
    """The community's hourly AQHI from first_day's month to now, by UTC time. Each source
    fills in what the later ones lack: the 23Z files ECCC keeps by date for 30 days (28, 21,
    14 and 7 days ago, a week each), the latest realtime file (the last week), and the monthly
    files up to last month, each month's backfilled version where ECCC has posted it."""
    files = []
    for back in (28, 21, 14, 7):
        day = today - timedelta(days=back)
        files.append(_get_optional(_realtime_url(datetime(day.year, day.month, day.day, 23))))
    hour = _now("UTC").replace(minute=0, second=0, microsecond=0)
    for back in range(4):  # the newest file, posted about 40 minutes after its hour
        latest = _get_optional(_realtime_url(hour - timedelta(hours=back)))
        if latest is not None:
            files.append(latest)
            break
    monthly = f"{DATAMART}/today/air_quality/aqhi/{ZONE}/observation/monthly/csv"
    for month in pd.period_range(first_day, today, freq="M")[:-1]:
        name = f"{monthly}/{month.strftime('%Y%m')}_MONTHLY_AQHI_{REGION}_SiteObs"
        # the backfilled version follows the plain one a few days into the next month
        files.append(_get_optional(f"{name}_BACKFILLED.csv") or _get_optional(f"{name}.csv"))
    history = pd.Series(dtype=float, index=pd.DatetimeIndex([], tz="UTC"))
    for raw in files:
        if raw is not None:
            history = parse_obs_csv(raw, COMMUNITY).combine_first(history)
    return history


def daytime_max(hourly: pd.Series, timezone: str) -> pd.Series:
    """Maximum of each local day's hours stamped FIRST_STAMP to LAST_STAMP, for days with at
    least MIN_HOURS of them observed, to two decimals; indexed by the local date."""
    hourly = hourly.dropna()
    local = hourly.index.tz_convert(timezone)
    window = (local.hour >= FIRST_STAMP) & (local.hour <= LAST_STAMP)
    days = hourly[window].groupby(local[window].tz_localize(None).normalize()).agg(["max", "count"])
    return days.loc[days["count"] >= MIN_HOURS, "max"].round(2)


def wind_components(speed: pd.Series, direction: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Eastward and northward components of a wind of `speed` blowing from `direction`
    (degrees clockwise from north)."""
    angle = direction * (math.pi / 180)
    return -speed * angle.map(math.sin), -speed * angle.map(math.cos)


def target_history(aqhi: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """Daily values on `index`, empty after the last observed day. A day missing between two
    observed days is filled linearly (truth leaves it empty): the pipeline's seasonal naive
    reads the last observed week of X_d and cannot score against a missing value."""
    return aqhi.reindex(index).interpolate(limit_area="inside")


def weather_frame(daily: dict) -> pd.DataFrame:
    """Open-Meteo's daily block as WEATHER_COLUMNS, the wind as components."""
    index = pd.to_datetime(daily["time"])
    value = {name: pd.Series(daily[name], index=index, dtype=float) for name in DAILY.split(",")}
    u, v = wind_components(value["wind_speed_10m_max"], value["wind_direction_10m_dominant"])
    frame = pd.DataFrame(
        {
            "tmax": value["temperature_2m_max"],
            "precip": value["precipitation_sum"],
            "wind_u": u,
            "wind_v": v,
            "radiation": value["shortwave_radiation_sum"],
        }
    )
    return frame[WEATHER_COLUMNS]


def weather_past(start: date, end: date, timezone: str) -> pd.DataFrame:
    """Daily weather at the community from ERA5 reanalysis (Open-Meteo archive)."""
    params = {"latitude": LAT, "longitude": LON, "start_date": start, "end_date": end}
    query = urllib.parse.urlencode(params | {"daily": DAILY, "timezone": timezone})
    payload = json.loads(_get(f"https://archive-api.open-meteo.com/v1/archive?{query}"))
    return weather_frame(payload["daily"])


def weather_forecast(horizon: int, timezone: str) -> tuple[pd.DataFrame, dict[str, object]]:
    """Latest published daily weather forecast at the community."""
    params: dict[str, object] = {
        "latitude": LAT,
        "longitude": LON,
        "daily": DAILY,
        "models": WEATHER_MODEL,
        "timezone": timezone,
        "forecast_days": horizon + 3,
        "past_days": 7,  # covers the last days if the reanalysis archive has not caught up
    }
    base = "https://api.open-meteo.com/v1/forecast"
    payload = json.loads(_get(f"{base}?{urllib.parse.urlencode(params)}"))
    return weather_frame(payload["daily"]), {"url": base, **params}


def snapshot(run_date: date, spec: dict) -> dict:
    """X_d as published right now: one row per day from history_start to the last target
    date, `target` empty after the last complete day, covariates known for the window."""
    start = date.fromisoformat(spec["x"]["history_start"])
    timezone = spec["schedule"]["timezone"]
    horizon = max(spec["horizons"])
    hourly = hourly_history(start, run_date)
    # the run date counts once ECCC has posted its last daytime hour, even if that hour is blank
    last_hour = pd.Timestamp(f"{run_date} {LAST_STAMP:02d}:00", tz=timezone).tz_convert(UTC)
    last_day = run_date if last_hour in hourly.index else run_date - timedelta(days=1)
    aqhi = daytime_max(hourly, timezone).loc[: pd.Timestamp(last_day)]
    last_observed = aqhi.index.max().date()
    forecast_daily, forecast_source = weather_forecast(horizon, timezone)

    index = pd.date_range(start, run_date + timedelta(days=horizon), freq="D", name="timestamp")
    forecast = forecast_daily.reindex(index)
    # Reanalysis up to yesterday (forecast fills days the archive lacks), forecast from today.
    weather = weather_past(start, run_date - timedelta(days=1), timezone).reindex(index)
    weather = weather.combine_first(forecast)
    future = index >= pd.Timestamp(run_date)
    weather.loc[future] = forecast.loc[future]
    target = target_history(aqhi, index).rename("target")
    forecasts = forecast_history(start, run_date, timezone)
    eccc = eccc_as_seen(forecasts, index, run_date, spec).rename("eccc")
    dow = pd.Series(index.dayofweek, index, name="dow")
    series = pd.concat([target, eccc, weather, dow], axis=1)
    local_day = hourly.index.tz_convert(timezone).tz_localize(None).normalize()
    observed = hourly[local_day <= pd.Timestamp(last_observed)]
    return {
        "series": series.rename_axis("timestamp"),
        "last_observed": last_observed,
        "raw": {
            "aqhi_hourly.csv": observed.rename("aqhi").rename_axis("utc_time").reset_index(),
            "eccc_forecasts.csv": forecasts,
            "weather_forecast_daily.csv": forecast_daily.loc[pd.Timestamp(run_date) :]
            .rename_axis("date")
            .reset_index(),
        },
        "sources": {
            "aqhi": {
                "url": f"{DATAMART}/today/air_quality/aqhi/{ZONE}/observation/",
                "community": COMMUNITY,
            },
            "eccc_forecast": {
                "url": f"{DATAMART}/today/air_quality/aqhi/{ZONE}/forecast/",
                "community": COMMUNITY,
            },
            "weather_past": {"url": "https://archive-api.open-meteo.com/v1/archive"},
            "weather_forecast": forecast_source,
        },
        "covariate_notes": {
            "target": "daily maximum of the hourly AQHI stamped "
            f"{FIRST_STAMP:02d}:00 to {LAST_STAMP:02d}:00 local time, the run date's once its "
            f"{LAST_STAMP:02d}:00 hour is posted; a day with fewer than {MIN_HOURS} observed "
            "hours is filled linearly from its neighbours",
            "eccc": "ECCC's AQHI forecast of the day's daytime maximum, a whole number: from "
            "the latest issue before that day's snapshot up to run_date, normally the evening "
            "issue of the day before, and from the latest before this snapshot after it",
            "tmax": "daily maximum 2 m temperature (deg C)",
            "precip": "daily precipitation (mm)",
            "wind_u, wind_v": "eastward and northward components (km/h) of the day's maximum "
            "10 m wind speed, along its dominant direction",
            "radiation": "daily shortwave radiation (MJ/m2)",
            "weather": f"at {LAT}, {LON}; dates before run_date are ERA5 reanalysis, run_date "
            f"onward is the {WEATHER_MODEL} forecast issued before the snapshot",
            "dow": "day of week, Monday = 0",
        },
    }


def truth(today: date, spec: dict) -> pd.Series:
    """The daytime maximum of every day up to SETTLE_DAYS before today, as published now."""
    timezone = spec["schedule"]["timezone"]
    start = date.fromisoformat(spec["x"]["history_start"])
    last = pd.Timestamp(today - timedelta(days=SETTLE_DAYS))
    return daytime_max(hourly_history(start, today), timezone).loc[:last]


def parse_forecast_xml(raw: bytes) -> tuple[datetime, str, dict[int, float]]:
    """An ECCC AQHI forecast file: when it was issued (UTC), its status ("issued", or that of
    an amendment) and the AQHI of each forecast period, "10+" counted as TEN_PLUS."""
    root = ET.fromstring(raw)
    region = (root.findtext("region") or "").strip()
    if region != COMMUNITY:
        raise ValueError(f"forecast file for {region!r}, not {COMMUNITY}")
    stamp = root.findtext("dateStamp[@name='aqForecastIssued']/UTCStamp") or ""
    issued = datetime.strptime(stamp.strip(), "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    periods = {}
    for forecast in root.iterfind("forecastGroup/forecast"):
        value = (forecast.findtext("airQualityHealthIndex") or "").strip()
        if value:
            periods[int(forecast.get("periodID"))] = TEN_PLUS if value == "10+" else float(value)
    return issued, root.get("status", ""), periods


def forecast_url(issued: datetime) -> str:
    """ECCC's file for the community's forecast issued at `issued` (UTC). It stays 30 days in
    the folder of its UTC day."""
    return (
        f"{DATAMART}/{issued:%Y%m%d}/WXO-DD/air_quality/aqhi/{ZONE}/forecast/realtime/xml/"
        f"AQ_FCST_{COMMUNITY}_{issued:%Y%m%d%H%M}.xml"
    )


def forecast_days(issued: datetime, periods: dict[int, float], timezone: str) -> dict:
    """An issue's daytime periods by the day they cover. Issues come at 06:00 and 17:00; an
    amendment before 06:00 amends the day before's."""
    local = issued.astimezone(ZoneInfo(timezone))
    base = local.date() - timedelta(days=1 if local.hour < 6 else 0)
    return {base + timedelta(days=OFFSET[p]): v for p, v in periods.items() if p in OFFSET}


def forecast_history(first_day: date, today: date, timezone: str) -> pd.DataFrame:
    """Every ECCC forecast for the community from the month before first_day to yesterday
    evening's issue, one row (issued, day, value) per daytime period. ECCC's monthly files
    hold every issue up to last month; the evening issues of the last 30 days fill in the
    months they lack."""
    rows: list[tuple[pd.Timestamp, pd.Timestamp, float]] = []
    months = pd.period_range(first_day - timedelta(days=1), today, freq="M")  # the evening before
    missing = {months[-1]}
    monthly = f"{DATAMART}/today/air_quality/aqhi/{ZONE}/forecast/monthly/csv"
    for month in months[:-1]:
        name = f"{month.strftime('%Y%m')}_MONTHLY_AQHI_{COMMUNITY}_SiteFcst.csv"
        raw = _get_optional(f"{monthly}/{name}")
        if raw is None:
            missing.add(month)
            continue
        frame = pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False)
        frame.columns = ["code", "name", "date", "time", "period", "value", "amended"]
        issued = pd.to_datetime(frame["date"] + " " + frame["time"], utc=True)
        values = pd.to_numeric(frame["value"].replace("10+", str(TEN_PLUS)), errors="coerce")
        for when, period, value in zip(issued, frame["period"].astype(int), values, strict=True):
            for day, v in forecast_days(when, {period: value}, timezone).items():
                rows.append((when, pd.Timestamp(day), v))
    for back in range(1, 31):
        day = today - timedelta(days=back)
        if pd.Period(day, freq="M") in missing:
            raw = _get_optional(forecast_url(_at(day, EVENING, timezone)))
            if raw is not None:
                issued, _, periods = parse_forecast_xml(raw)
                days = forecast_days(issued, periods, timezone)
                rows += [(pd.Timestamp(issued), pd.Timestamp(d), v) for d, v in days.items()]
    return pd.DataFrame(rows, columns=["issued", "day", "value"]).dropna()


def eccc_as_seen(
    history: pd.DataFrame, index: pd.DatetimeIndex, run_date: date, spec: dict
) -> pd.Series:
    """ECCC's forecast of each day on `index` as a run could see it: from the latest issue
    before that day's snapshot, or the run date's for the days after it, normally the evening
    issue of the day before. A past day no issue covered is filled in linearly."""
    snapshot, timezone = spec["schedule"]["snapshot"], spec["schedule"]["timezone"]
    as_of = pd.Series(
        [_at(min(day.date(), run_date), snapshot, timezone) for day in index],
        index=index,
        name="as_of",
    )
    rows = history.merge(as_of, left_on="day", right_index=True)
    rows = rows[rows["issued"] <= rows["as_of"]].sort_values("issued")
    seen = rows.groupby("day")["value"].last().reindex(index)
    past = index < pd.Timestamp(run_date)
    seen[past] = seen[past].interpolate(limit_area="inside")
    return seen


def baseline(run_date: date, spec: dict) -> dict:
    """ECCC's forecast the run is compared with: the run date's 06:00 issue, the first after
    the cutoff, which the agents cannot see. Its "Today" and "Tomorrow" periods give the run
    date and the day after. Empty until it is posted; ECCC keeps it for 30 days."""
    timezone = spec["schedule"]["timezone"]
    raw = _get_optional(forecast_url(_at(run_date, MORNING, timezone)))
    if raw is None:
        source = {"report": f"no 06:00 ECCC AQHI forecast for {COMMUNITY_NAME} ({COMMUNITY})"}
        return {"source": source | {"posted": None, "model": "ECCC"}, "forecasts": []}
    issued, status, periods = parse_forecast_xml(raw)
    days = forecast_days(issued, periods, timezone)
    targets = [run_date + timedelta(days=h) for h in spec["horizons"]]
    return {
        "source": {
            "report": f"ECCC AQHI forecast for {COMMUNITY_NAME} ({COMMUNITY}), {status}",
            "posted": issued.isoformat(),
            "model": "ECCC",
        },
        "forecasts": [
            {"target_date": t.isoformat(), "point": days[t]} for t in targets if t in days
        ],
    }
