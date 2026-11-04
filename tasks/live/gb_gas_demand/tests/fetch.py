"""GB daily gas demand: where the numbers for this target come from.

The agentic-forecasting pipeline imports this file on the host, outside every episode; the
sandbox's setup stage never runs it, and setup/run.sh makes it unreadable to the agent.
It needs only the standard library and pandas. The pipeline passes `spec`, the target
definition from task.yaml (`extras.agentic-forecasting` plus `params`), and turns the
results into archived files.

    snapshot(run_date, spec)  X_d: demand as first published, National Gas's forecast of the
                              cycle before the run's, city temperatures and the calendar
    truth(today, spec)        every gas day's demand as first published
    baseline(run_date, spec)  National Gas's forecast for the next gas day, its 13:00 version,
                              published during the run

A gas day runs from 05:00 to 05:00 UK local time and is dated by the day it starts on, so
it has 23 or 25 hours when the clocks change. National Gas stamps every version of a value
with UK local clock times (Applicable At, Generated Time), and the schedule's times are
compared with them as UK local clock times too: exact except within the hour the clocks
repeat in October, when nothing this Task uses is published. No credentials are needed:
the National Gas Data Portal, Open-Meteo and GOV.UK answer anonymous requests.
"""

from __future__ import annotations

import hashlib
import http.client
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

TARGET = "gb_gas_demand"
PORTAL = "https://data.nationalgas.com"
API = f"{PORTAL}/api/find-gas-data-download"  # the Data Portal's CSV download
PAGE = f"{PORTAL}/find-gas-data"
# The data items: id -> (name in the portal's CSV, what this file calls it)
ITEMS = {
    "PUBOB637": ("Demand Actual, NTS, D+1", "actual"),
    "PUBOB623": ("Demand Forecast, NTS", "forecast"),  # from 13:00 the day before the gas day
    "PUBOB36": ("Demand Forecast, NTS, D-2", "forecast_d2"),  # two days before it
}
MODEL = "National Gas NTS demand forecast"
UK = ZoneInfo("Europe/London")  # National Gas's clock
GAS_DAY_START = 5  # a gas day starts at 05:00 UK local time
SCREEN = 1.2  # an actual counts if within this factor of the median forecast for its day
SETTLED_DAYS = 14  # versions of a gas day this old no longer change, so they are cached
# The latest demand at the 13:00 snapshot is normally the day before the run date (on 731 of
# 763 run dates from 2024-10-02 to 2026-11-03; two days on 28, three or four on 4); older
# means the portal's answer was incomplete
STALE = timedelta(days=7)
# City: (latitude, longitude) where its temperature is taken
CITIES = {
    "london": (51.5072, -0.1276),
    "birmingham": (52.4862, -1.8904),
    "manchester": (53.4808, -2.2426),
    "glasgow": (55.8642, -4.2518),
}
WEATHER_MODEL = "ecmwf_ifs025"
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
FORECAST = "https://api.open-meteo.com/v1/forecast"
BANK_HOLIDAYS = "https://www.gov.uk/bank-holidays.json"
DIVISION = "england-and-wales"
OGL = "Open Government Licence v3.0"
USER_AGENT = "agentic-forecasting/1.0"
CACHE = (
    Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "agentic-forecasting" / TARGET
)
CACHE_VERSION = "v2"  # in the cache files' names; raise it when a parser changes
VERSION_COLUMNS = ["item", "gas_day", "at", "generated", "value", "quality"]
PORTAL_TIME = "%d/%m/%Y %H:%M:%S"
CACHE_TIME = "%Y-%m-%d %H:%M:%S"
# what a failed request, or an answer that no longer parses, raises
FAILURES = (OSError, http.client.HTTPException, ValueError, KeyError, TypeError)
REQUEST_PAUSE = 0.5  # seconds between requests
RATE_LIMIT_PAUSE = 61  # seconds; Open-Meteo answers 429 until its minute's quota refills

_last_request = 0.0  # time.monotonic() of the last request


def _now() -> datetime:
    return datetime.now(UTC)


def _get(url: str, headers: dict | None = None, retries: int = 3) -> tuple[bytes, dict]:
    """The body at url, and where and when it was retrieved. A 429 is retried after
    RATE_LIMIT_PAUSE seconds, and a server error, timeout or dropped connection after a short
    wait, up to `retries` times; any other HTTP error is raised at once."""
    global _last_request
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    for attempt in range(retries + 1):
        time.sleep(max(0.0, _last_request + REQUEST_PAUSE - time.monotonic()))
        _last_request = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = response.read()
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
        return body, meta
    raise AssertionError("unreachable")


def _uk(day: date, clock: str, timezone: str) -> pd.Timestamp:
    """`clock` ("HH:MM" in `timezone`) on `day`, as a UK local clock time without a zone, the
    way National Gas stamps its data."""
    hour, minute = map(int, clock.split(":"))
    local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZoneInfo(timezone))
    return pd.Timestamp(local.astimezone(UK).replace(tzinfo=None))


def _zoned(stamp: pd.Timestamp) -> str:
    """A UK local clock time in ISO 8601 with its offset (in the repeated hour, the first)."""
    return stamp.to_pydatetime().replace(tzinfo=UK).isoformat()


def _empty_versions() -> pd.DataFrame:
    frame = pd.DataFrame({column: pd.Series(dtype=object) for column in VERSION_COLUMNS})
    return frame.astype(
        {
            "gas_day": "datetime64[ns]",
            "at": "datetime64[ns]",
            "generated": "datetime64[ns]",
            "value": float,
        }
    )


def parse_versions(raw: bytes) -> pd.DataFrame:
    """Every version in a Data Portal CSV answer, one row each, by gas day: the item (see
    ITEMS), the gas day (Applicable For), when the version was stamped (Applicable At) and
    generated (Generated Time), both UK local clock times, its value (mcm) and its quality
    flag. Rows of other items are left out. An answer with no rows is a line break."""
    if not raw.strip():
        return _empty_versions()
    frame = pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False)
    names = {name: item for name, item in ITEMS.values()}
    stamped = pd.to_datetime(frame["Applicable At"], format=PORTAL_TIME, errors="coerce")
    generated = pd.to_datetime(frame["Generated Time"], format=PORTAL_TIME, errors="coerce")
    rows = pd.DataFrame(
        {
            "item": frame["Data Item"].map(names),
            "gas_day": pd.to_datetime(frame["Applicable For"], format="%d/%m/%Y", errors="coerce"),
            "at": stamped,
            "generated": generated.fillna(stamped),
            "value": pd.to_numeric(frame["Value"], errors="coerce"),
            "quality": frame["Quality Indicator"],
        }
    ).dropna(subset=["item", "gas_day", "at", "value"])
    return rows.sort_values(["gas_day", "item", "at", "generated"]).reset_index(drop=True)


def _request(first_day: date, last_day: date) -> tuple[pd.DataFrame, dict]:
    """Every version of the ITEMS for the gas days first_day to last_day, as published now.
    The portal answers any range in one CSV (80,000 rows at once, checked 2026-11-01), but
    holds only the last five years."""
    query = {
        "applicableFor": "Y",
        "dateFrom": first_day.isoformat(),
        "dateTo": last_day.isoformat(),
        "dateType": "GASDAY",
        "latestFlag": "N",  # every version, not only the latest
        "ids": ",".join(ITEMS),
        "type": "CSV",
    }
    body, meta = _get(f"{API}?{urllib.parse.urlencode(query, safe=',')}")
    return parse_versions(body), meta


def _read_versions(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype={"item": str, "quality": str}, keep_default_na=False)
    for column in ("gas_day", "at", "generated"):
        frame[column] = pd.to_datetime(frame[column], format=CACHE_TIME)
    frame["value"] = pd.to_numeric(frame["value"])
    return frame[VERSION_COLUMNS]


def versions(first_day: date, last_day: date, today: date) -> tuple[pd.DataFrame, dict]:
    """Every version of the ITEMS for the gas days first_day to last_day, as published now.
    The versions of a gas day SETTLED_DAYS before `today` no longer change (National Gas
    revises an actual for up to five days), so they are cached on the host and only later
    gas days are requested; the cache also keeps the history the portal drops after five
    years."""
    path = CACHE / f"nationalgas.{CACHE_VERSION}.csv"
    cached = _read_versions(path) if path.is_file() else _empty_versions()
    start = pd.Timestamp(first_day)
    if not cached.empty and cached["gas_day"].min() <= start:
        start = max(start, cached["gas_day"].max() + pd.Timedelta(days=1))
    meta: dict = {"url": API, "cached_before": start.date().isoformat()}
    fresh = _empty_versions()
    if start <= pd.Timestamp(last_day):
        fresh, meta = _request(start.date(), last_day)
        meta["cached_before"] = start.date().isoformat()
    parts = [part for part in (cached[cached["gas_day"] < start], fresh) if not part.empty]
    rows = pd.concat(parts, ignore_index=True) if parts else _empty_versions()
    settled = rows[rows["gas_day"] < pd.Timestamp(today - timedelta(days=SETTLED_DAYS))]
    if len(settled) > len(cached):
        CACHE.mkdir(parents=True, exist_ok=True)
        settled.to_csv(path.with_suffix(".tmp"), index=False, date_format=CACHE_TIME)
        path.with_suffix(".tmp").replace(path)
    within = (rows["gas_day"] >= pd.Timestamp(first_day)) & (
        rows["gas_day"] <= pd.Timestamp(last_day)
    )
    return rows[within].reset_index(drop=True), meta


def _published(rows: pd.DataFrame) -> pd.Series:
    """When each version became public: the later of its two stamps. A few were generated
    long after their Applicable At (corrections, re-generated rows) and count from then."""
    return rows[["at", "generated"]].max(axis=1)


def first_published(rows: pd.DataFrame) -> pd.Series:
    """Each gas day's demand as first published: of the actuals within a factor of SCREEN of
    the median of the forecasts for the day published before them, the one published first.
    That passes over the partial sums the portal posts on the morning after the gas day
    (often 0 or about 20 mcm) and the rare erroneous value, such as 999,150 mcm, which
    National Gas corrects later. An actual with no forecast published before it counts as
    it is."""
    actual = rows[rows["item"] == "actual"].assign(published=_published).reset_index(drop=True)
    forecast = rows[rows["item"] == "forecast"].assign(published=_published)
    pairs = actual.reset_index().merge(
        forecast[["gas_day", "published", "value"]],
        on="gas_day",
        how="left",
        suffixes=("", "_forecast"),
    )
    earlier = pairs[pairs["published_forecast"] <= pairs["published"]]
    reference = earlier.groupby("index")["value_forecast"].median().reindex(actual.index)
    counts = reference.isna() | (
        (actual["value"] >= reference / SCREEN) & (actual["value"] <= reference * SCREEN)
    )
    first = actual[counts].sort_values(["published", "at"]).groupby("gas_day")["value"].first()
    return first.astype(float).rename_axis("gas_day")


def forecast_as_seen(
    rows: pd.DataFrame,
    index: pd.DatetimeIndex,
    spec: dict,
    last_observed: pd.Timestamp,
    target: pd.Series,
) -> pd.Series:
    """National Gas's forecast for each gas day on `index` from the cycle before B's: the
    latest version (by Applicable At) published before the day of the run that targets the
    gas day, of its Demand Forecast, NTS, which starts at 13:00 on that day, and its
    two-day-ahead forecast (D-2), posted two days before the gas day. So it is the D-2
    forecast, for past gas days and the forecast window alike, and never the run date's
    13:00 day-ahead version, which is B. A past day without one is filled in linearly; a
    window day without one carries the day before's, or the last observed demand."""
    lead = timedelta(days=min(spec["horizons"]))
    as_of = pd.Series(index - lead, index=index, name="as_of")  # midnight, UK clock
    forecasts = rows[rows["item"].isin(["forecast", "forecast_d2"])].assign(published=_published)
    dated = forecasts.merge(as_of, left_on="gas_day", right_index=True)
    seen = (
        dated[dated["published"] < dated["as_of"]]
        .sort_values(["at", "generated"])
        .groupby("gas_day")["value"]
        .last()
        .reindex(index)
    )
    past = index <= last_observed
    seen[past] = seen[past].interpolate(limit_area="inside").bfill()
    return seen.ffill().fillna(target[last_observed])


def _openmeteo(base: str, params: dict) -> tuple[list[dict], dict]:
    """One Open-Meteo response per requested location, in request order."""
    body, meta = _get(f"{base}?{urllib.parse.urlencode(params)}")
    payload = json.loads(body)
    return (payload if isinstance(payload, list) else [payload]), meta


def _coordinates() -> dict[str, str]:
    return {
        "latitude": ",".join(str(lat) for lat, _ in CITIES.values()),
        "longitude": ",".join(str(lon) for _, lon in CITIES.values()),
    }


def hourly_temperatures(responses: list[dict]) -> pd.DataFrame:
    """Hourly 2 m air temperature (deg C) per city, a column each, indexed by UTC time."""
    columns = {
        city: pd.Series(
            response["hourly"]["temperature_2m"],
            index=pd.to_datetime(response["hourly"]["time"], format="%Y-%m-%dT%H:%M"),
            dtype=float,
        )
        for city, response in zip(CITIES, responses, strict=True)
    }
    return pd.DataFrame(columns).rename_axis("time")


def _no_temperatures(name: str) -> pd.DataFrame:
    return pd.DataFrame(columns=list(CITIES), index=pd.DatetimeIndex([], name=name), dtype=float)


def gas_day_hours(days: pd.DatetimeIndex) -> pd.Series:
    """The number of hours in each gas day: 24, or 23 and 25 when the clocks change."""
    starts = (days + pd.Timedelta(hours=GAS_DAY_START)).tz_localize(UK)
    ends = (days + pd.Timedelta(days=1, hours=GAS_DAY_START)).tz_localize(UK)
    return pd.Series((ends - starts) / pd.Timedelta(hours=1), index=days).round().astype(int)


def gas_day_means(hourly: pd.DataFrame) -> pd.DataFrame:
    """The mean of each column of an hourly frame indexed by UTC time over each gas day, for
    the gas days whose every hour has a value. A gas day is told by its UK local clock time
    less five hours, so the hours the clocks repeat or skip fall in the right one."""
    wall = hourly.index.tz_localize("UTC").tz_convert(UK).tz_localize(None)
    days = (wall - pd.Timedelta(hours=GAS_DAY_START)).normalize()
    means = hourly.groupby(days).mean()
    counts = hourly.groupby(days).count()
    complete = counts.eq(gas_day_hours(pd.DatetimeIndex(means.index)), axis=0)
    return means.where(complete).rename_axis("gas_day")


def weather_past(start: date, end: date) -> tuple[pd.DataFrame, dict]:
    """Mean temperature per city over each gas day from start to end, from Open-Meteo's
    historical archive (ERA5 reanalysis; ECMWF's analyses for the last few days). Each answer
    is cached, and if Open-Meteo fails the cached one stands in."""
    path = CACHE / f"temperature.{CACHE_VERSION}.csv"
    params = _coordinates() | {
        "start_date": start.isoformat(),
        "end_date": (end + timedelta(days=1)).isoformat(),  # gas day `end` ends the day after
        "hourly": "temperature_2m",
        "timezone": "GMT",
    }
    meta: dict = {"url": ARCHIVE}
    try:
        responses, got = _openmeteo(ARCHIVE, params)
        means = gas_day_means(hourly_temperatures(responses)).loc[
            pd.Timestamp(start) : pd.Timestamp(end)
        ]
    except FAILURES as exc:
        if not path.is_file():
            meta["error"] = f"archive failed and nothing is cached: {exc!r}"
            return _no_temperatures("gas_day"), meta
        meta["error"] = f"archive failed, the cached temperatures stand in: {exc!r}"
        return pd.read_csv(path, index_col="gas_day", parse_dates=["gas_day"]), meta
    CACHE.mkdir(parents=True, exist_ok=True)
    means.to_csv(path.with_suffix(".tmp"), date_format="%Y-%m-%d")
    path.with_suffix(".tmp").replace(path)
    return means, meta | {"retrieved_at": got["retrieved_at"]}


def weather_forecast(horizon: int) -> tuple[pd.DataFrame, dict]:
    """The latest ECMWF IFS hourly temperature forecast per city from Open-Meteo, from a week
    ago to `horizon` + 3 days ahead. Each is cached, and if Open-Meteo fails the last cached
    one stands in: issued a day earlier, it still covers the window."""
    path = CACHE / f"temperature_forecast.{CACHE_VERSION}.csv"
    params: dict[str, object] = _coordinates() | {
        "hourly": "temperature_2m",
        "models": WEATHER_MODEL,
        "timezone": "GMT",
        "forecast_days": horizon + 3,
        "past_days": 7,  # covers the last days if the archive has not caught up
    }
    meta = {"url": FORECAST, **{k: v for k, v in params.items() if k not in _coordinates()}}
    try:
        responses, got = _openmeteo(FORECAST, params)
        hourly = hourly_temperatures(responses)
    except FAILURES as exc:
        if not path.is_file():
            meta["error"] = f"forecast failed and none is cached: {exc!r}"
            return _no_temperatures("time"), meta
        cached = pd.read_csv(path, index_col="time", parse_dates=["time"])
        issued = cached.pop("retrieved_at").iloc[0]
        meta["error"] = f"forecast failed, the one retrieved at {issued} stands in: {exc!r}"
        return cached, meta | {"retrieved_at": issued}
    CACHE.mkdir(parents=True, exist_ok=True)
    hourly.assign(retrieved_at=got["retrieved_at"]).to_csv(path.with_suffix(".tmp"))
    path.with_suffix(".tmp").replace(path)
    return hourly, meta | {"retrieved_at": got["retrieved_at"]}


def temperatures(
    start: date, run_date: date, horizon: int, index: pd.DatetimeIndex
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Mean temperature per city over each gas day on `index`: from the archive for gas days
    before the run date (the forecast's past days fill any the archive lacks), from the
    forecast for the rest. What neither gives is filled in, so that an Open-Meteo outage does
    not cost a run day: a past gap linearly, the window from the day before. Without the
    archive and a cached copy of it, the forecast's past week is all the history there is,
    and the cities are left out rather than filled from it. Also the hourly forecast, and
    where each part came from."""
    past, past_meta = weather_past(start, run_date - timedelta(days=1))
    hourly, forecast_meta = weather_forecast(horizon)
    forecast = gas_day_means(hourly).reindex(index=index, columns=list(CITIES))
    weather = past.reindex(index=index, columns=list(CITIES)).combine_first(forecast)
    future = index >= pd.Timestamp(run_date)
    weather.loc[future] = forecast.loc[future]
    weather = weather.interpolate(limit_area="inside").ffill().bfill(limit=3)
    missing = [city for city in CITIES if weather[city].isna().any()]
    if missing:
        note = f"no past temperatures for {', '.join(missing)}: left out"
        past_meta["error"] = "; ".join(filter(None, [past_meta.get("error"), note]))
    weather = weather.drop(columns=missing).rename(columns=lambda city: f"temp_{city}")
    return weather, hourly, {"weather_past": past_meta, "weather_forecast": forecast_meta}


def standard_bank_holidays(year: int) -> dict[date, str]:
    """England and Wales's regular bank holidays in `year`, a weekend's moved to the next
    weekday. GOV.UK's list also has the one-off holidays (a jubilee, a state funeral) and
    any moved by proclamation."""
    easter = (pd.Timestamp(year, 1, 1) + pd.offsets.Easter()).date()
    may1, may31, august31 = date(year, 5, 1), date(year, 5, 31), date(year, 8, 31)
    days = {
        easter - timedelta(days=2): "Good Friday",
        easter + timedelta(days=1): "Easter Monday",
        may1 + timedelta(days=-may1.weekday() % 7): "Early May bank holiday",
        may31 - timedelta(days=may31.weekday()): "Spring bank holiday",
        august31 - timedelta(days=august31.weekday()): "Summer bank holiday",
    }
    for title, day in (
        ("New Year’s Day", date(year, 1, 1)),
        ("Christmas Day", date(year, 12, 25)),
        ("Boxing Day", date(year, 12, 26)),
    ):
        while day.weekday() >= 5 or day in days:
            day += timedelta(days=1)
        days[day] = title
    return dict(sorted(days.items()))


def bank_holidays(first_year: int, last_year: int) -> tuple[pd.DataFrame, dict]:
    """England and Wales's bank holidays from first_year to last_year, one row (date, title,
    source) each: GOV.UK's list for the years it covers (it starts in 2019 and runs about
    two years ahead), merged with every list read before, which is cached, so years GOV.UK
    later drops keep their one-off holidays; the regular holidays, computed, for years no
    list covers. If GOV.UK fails, the cached lists and the computed holidays stand in."""
    path = CACHE / f"bank_holidays.{CACHE_VERSION}.csv"
    cached = (
        pd.read_csv(path, dtype=str, keep_default_na=False)
        if path.is_file()
        else pd.DataFrame(columns=["date", "title"], dtype=str)
    )
    meta: dict = {"url": BANK_HOLIDAYS, "division": DIVISION, "license": OGL}
    try:
        body, got = _get(BANK_HOLIDAYS)
        events = json.loads(body)[DIVISION]["events"]
        fresh = pd.DataFrame(
            [{"date": e["date"], "title": e["title"]} for e in events], columns=["date", "title"]
        )
        meta["retrieved_at"] = got["retrieved_at"]
    except FAILURES as exc:
        fresh = cached.iloc[0:0]
        meta["error"] = f"GOV.UK not read, the cached list stands in: {exc!r}"
    fresh_years = set(fresh["date"].str[:4])
    listed = pd.concat([fresh, cached[~cached["date"].str[:4].isin(fresh_years)]])
    listed = listed.drop_duplicates("date").sort_values("date").reset_index(drop=True)
    if len(fresh):
        CACHE.mkdir(parents=True, exist_ok=True)
        listed.to_csv(path.with_suffix(".tmp"), index=False)
        path.with_suffix(".tmp").replace(path)
    listed_years = {int(year) for year in listed["date"].str[:4]}
    rows = [
        (day, title, "GOV.UK")
        for day, title in zip(listed["date"], listed["title"], strict=True)
        if first_year <= int(day[:4]) <= last_year
    ]
    computed = [year for year in range(first_year, last_year + 1) if year not in listed_years]
    for year in computed:
        rows += [(d.isoformat(), t, "computed") for d, t in standard_bank_holidays(year).items()]
    meta["computed_years"] = computed
    return pd.DataFrame(rows, columns=["date", "title", "source"]).sort_values("date"), meta


def snapshot(run_date: date, spec: dict) -> dict:
    """X_d as published at the snapshot: one row per gas day from history_start to the last
    target date, `target` empty after the last gas day National Gas has published,
    covariates known for the window. Versions published after the snapshot's time are left
    out even if it is built later, so the version B is taken from never enters it."""
    start = date.fromisoformat(spec["x"]["history_start"])
    timezone = spec["schedule"]["timezone"]
    targets = [run_date + timedelta(days=h) for h in spec["horizons"]]
    as_of = _uk(run_date, spec["schedule"]["snapshot"], timezone)
    rows, rows_meta = versions(start, max(targets), run_date)
    rows = rows[_published(rows) <= as_of]
    firsts = first_published(rows).round(3)  # as truth records them
    firsts = firsts[(firsts.index >= pd.Timestamp(start)) & (firsts.index < pd.Timestamp(run_date))]
    if firsts.empty:
        raise RuntimeError("National Gas has no NTS demand between history_start and the run")
    last_observed = firsts.index.max()
    if last_observed < pd.Timestamp(run_date - STALE):
        raise RuntimeError(
            f"National Gas's latest NTS demand is for gas day {last_observed.date()}, more "
            "than a week before the run date: the portal's answer is incomplete"
        )

    index = pd.date_range(start, max(targets), freq="D", name="timestamp")
    past = index <= last_observed
    target = firsts.reindex(index)
    target[past] = target[past].interpolate(limit_area="inside")
    expert = forecast_as_seen(rows, index, spec, last_observed, target)
    weather, hourly, weather_meta = temperatures(start, run_date, max(spec["horizons"]), index)
    holidays, holidays_meta = bank_holidays(start.year, max(targets).year)
    holiday = index.isin(pd.to_datetime(holidays["date"], format="%Y-%m-%d")).astype(int)
    series = pd.concat(
        [
            target.rename("target"),
            expert.rename("ng_forecast"),
            weather,
            pd.DataFrame({"dow": index.dayofweek, "holiday": holiday}, index=index),
        ],
        axis=1,
    )
    shown = rows.rename(columns={"at": "applicable_at"}).sort_values(["gas_day", "item"])
    return {
        "series": series.rename_axis("timestamp"),
        "last_observed": last_observed.date(),
        "raw": {
            "nationalgas_versions.csv": shown,
            "temperature_forecast_hourly.csv": hourly.loc[pd.Timestamp(run_date) :].reset_index(),
            "bank_holidays.csv": holidays,
        },
        "sources": {
            "national_gas": {
                **rows_meta,
                "page": PAGE,
                "items": {code: name for code, (name, _) in ITEMS.items()},
                "versions_before_snapshot": len(rows),
            },
            **weather_meta,
            "bank_holidays": holidays_meta,
        },
        "covariate_notes": {
            "target": "NTS demand of the gas day starting at 05:00 UK local time on the date "
            "(mcm), National Gas's Demand Actual, NTS, D+1 as first published: of the values "
            f"within a factor of {SCREEN} of the median of the forecasts for the day published "
            "before them, the first; a missing day is filled linearly",
            "ng_forecast": "National Gas's forecast for the gas day (mcm) from the cycle before "
            "the run that targets it: the latest version published before that run's date, "
            "which is its D-2 forecast, issued two days before the gas day; the same for past "
            "gas days and the window, never the run date's 13:00 day-ahead forecast",
            "temp_<city>": "mean 2 m air temperature over the gas day (deg C); gas days before "
            f"run_date are reanalysis, run_date onward the {WEATHER_MODEL} forecast issued "
            "before the snapshot",
            "cities": {city: f"{lat}, {lon}" for city, (lat, lon) in CITIES.items()},
            "dow": "day of week the gas day starts on, Monday = 0",
            "holiday": "bank holiday in England and Wales (1) or not (0)",
        },
    }


def truth(today: date, spec: dict) -> pd.Series:
    """Every gas day's demand as first published (see first_published), from history_start
    to the day before `today`. The pipeline keeps the first value it sees for a gas day,
    which is that one. If the portal cannot be read, nothing is recorded today and a warning
    is printed, so that the day's run goes on."""
    start = date.fromisoformat(spec["x"]["history_start"])
    try:
        rows, _ = versions(start, today, today)
    except FAILURES as exc:
        print(f"warning: National Gas not read, no truth recorded today: {exc!r}", file=sys.stderr)
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    values = first_published(rows)
    within = (values.index >= pd.Timestamp(start)) & (values.index < pd.Timestamp(today))
    return values[within].round(3)


def baseline(run_date: date, spec: dict) -> dict:
    """National Gas's forecast the run is compared with: for each target gas day, its 13:00
    day-ahead forecast, the Demand Forecast, NTS version stamped (Applicable At) first
    between the start and the cutoff on the run date, normally at 13:01 and published at
    about 13:10, during the run, so the agents can see it. The 16:00 version, which repeats
    it, is never B; without a 13:00 version, B stays empty. A version re-issued with the same
    stamp is a correction: the latest re-issue published by the next day's snapshot, when
    the pipeline reads B, counts (on 2025-01-25 the 16:01 version was posted as 1 and
    corrected to 265 an hour later), and later ones do not, so a re-run on any later day
    records the same B. Empty until it is published."""
    timezone = spec["schedule"]["timezone"]
    start = _uk(run_date, spec["schedule"]["snapshot"], timezone)
    cutoff = _uk(run_date, spec["schedule"]["cutoff"], timezone)
    read = _uk(run_date + timedelta(days=1), spec["schedule"]["snapshot"], timezone)
    days = [run_date + timedelta(days=h) for h in spec["horizons"]]
    rows, meta = _request(min(days), max(days))
    forecast = rows[rows["item"] == "forecast"].assign(published=_published)
    forecasts, used = [], []
    for day in days:
        mine = forecast[forecast["gas_day"] == pd.Timestamp(day)]
        issued = mine[(mine["at"] >= start) & (mine["at"] < cutoff) & (mine["published"] <= read)]
        if issued.empty:
            continue
        issues = issued[issued["at"] == issued["at"].min()]
        version = issues.sort_values(["published", "generated"]).iloc[-1]
        forecasts.append(
            {"target_date": day.isoformat(), "point": round(float(version["value"]), 3)}
        )
        used.append(version)
    source = {
        "report": "National Gas Demand Forecast, NTS for gas day "
        + ", ".join(day.isoformat() for day in days),
        "posted": _zoned(used[0]["published"]) if used else None,
        "model": MODEL,
        "data_item": f"PUBOB623, {ITEMS['PUBOB623'][0]}",
        "page": PAGE,
        "url": meta["url"],
        "retrieved_at": meta["retrieved_at"],
        "sha256": meta["sha256"],
    }
    if used:
        source["stamped"] = _zoned(used[0]["at"])  # the version's Applicable At
    else:
        source["note"] = "no version stamped between the start and the cutoff"
    return {"source": source, "forecasts": forecasts}
