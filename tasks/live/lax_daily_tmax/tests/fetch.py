"""Host-only LAX-area temperature data: stdlib + pandas, no API key.

Open-Meteo ERA5 is the delayed reanalysis target; ECMWF IFS 0.25-degree is
an independently archived forecast baseline. Neither is a station observation.
All target and baseline values are Kelvin; raw Celsius values remain available.
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

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
FORECAST = "https://api.open-meteo.com/v1/forecast"


def _now(spec):
    return datetime.now(ZoneInfo(spec["schedule"]["timezone"]))


def _guard(run_date, spec):
    now = _now(spec)
    cutoff = datetime.combine(
        run_date,
        clock.fromisoformat(spec["schedule"]["cutoff"]),
        ZoneInfo(spec["schedule"]["timezone"]),
    )
    if now.date() != run_date or now >= cutoff:
        raise RuntimeError("Live snapshots/baselines require today, before the cutoff.")
    return now


def _common(spec):
    x = spec["x"]
    return {
        "latitude": x["latitude"],
        "longitude": x["longitude"],
        "elevation": x["elevation"],
        "cell_selection": x["cell_selection"],
        "daily": x["variable"],
        "timezone": spec["schedule"]["timezone"],
        "temperature_unit": "celsius",
    }


def _get(url):
    for attempt in range(3):
        try:
            request = Request(
                url, headers={"User-Agent": "agentic-forecasting-temperature"}
            )
            with urlopen(request, timeout=60) as response:
                result = json.load(response)
            if not isinstance(result, dict) or result.get("error"):
                raise RuntimeError("Open-Meteo returned an invalid or error response.")
            return result
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise RuntimeError(f"Open-Meteo returned HTTP {exc.code}.") from None
            time.sleep(2**attempt)
        except (URLError, TimeoutError):
            if attempt == 2:
                raise RuntimeError("Open-Meteo network/TLS request failed.") from None
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def _parse(payload, start, end, spec, allow_trailing_missing=False):
    variable = spec["x"]["variable"]
    if payload.get("timezone") != spec["schedule"]["timezone"]:
        raise RuntimeError("Provider timezone differs from the target timezone.")
    if payload.get("daily_units", {}).get(variable) != "°C":
        raise RuntimeError("Expected Celsius temperature values from the provider.")
    daily = payload.get("daily", {})
    days, values = daily.get("time"), daily.get(variable)
    if (
        not isinstance(days, list)
        or not isinstance(values, list)
        or len(days) != len(values)
    ):
        raise RuntimeError(
            "Daily temperature dates/values are missing or inconsistent."
        )
    expected = pd.date_range(start, end, name="timestamp")
    if days != list(expected.strftime("%Y-%m-%d")):
        raise RuntimeError(
            "Provider dates are incomplete, duplicated, reordered or outside the request."
        )
    for value in values:
        if value is None and allow_trailing_missing:
            continue
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
            or value <= -273.15
        ):
            raise RuntimeError(
                "Temperature is missing, invalid or below absolute zero."
            )
    frame = pd.DataFrame({"celsius": values}, index=expected)
    frame["target"] = frame["celsius"] + 273.15
    if allow_trailing_missing:
        last = frame["target"].last_valid_index()
        if last is None:
            raise RuntimeError("No finite historical temperature values available.")
        if frame.loc[:last, "target"].isna().any():
            raise RuntimeError(
                "Historical series has an internal gap; refusing to fill truth."
            )
    return frame


def _history(today, spec):
    start = date.fromisoformat(spec["x"]["history_start"])
    end = today - timedelta(days=int(spec["x"]["archive_delay_days"]))
    if end < start:
        raise RuntimeError("History start is later than the delayed archive cutoff.")
    params = _common(spec) | {
        "models": spec["x"]["history_model"],
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    url = ARCHIVE + "?" + urlencode(params)
    payload = _get(url)
    fetched = _now(spec).isoformat(timespec="seconds")
    frame = _parse(payload, start, end, spec, allow_trailing_missing=True)
    last = frame["target"].last_valid_index()
    history = frame.loc[:last, "target"].rename("target")
    return history, frame, {"url": url, "retrieved_at": fetched, "response": payload}


def _baseline(run_date, spec):
    _guard(run_date, spec)
    start = run_date + timedelta(days=min(spec["horizons"]))
    end = run_date + timedelta(days=max(spec["horizons"]))
    params = _common(spec) | {
        "models": spec["x"]["forecast_model"],
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    url = FORECAST + "?" + urlencode(params)
    payload = _get(url)
    retrieved = _guard(
        run_date, spec
    )  # Reject responses completing after the deadline.
    frame = _parse(payload, start, end, spec)
    forecasts = [
        {
            "target_date": (run_date + timedelta(days=h)).isoformat(),
            "point": float(
                frame.loc[pd.Timestamp(run_date + timedelta(days=h)), "target"]
            ),
        }
        for h in spec["horizons"]
    ]
    return {
        "source": {
            "name": spec["baseline"]["source"]["name"],
            "model": spec["x"]["forecast_model"],
            "request_url": url,
            "retrieved_at": retrieved.isoformat(timespec="seconds"),
            "timestamp_note": "Retrieval time, not a model-cycle issue timestamp.",
            "response": payload,
        },
        "forecasts": forecasts,
    }


def snapshot(run_date, spec):
    _guard(run_date, spec)
    history, raw_history, historical_source = _history(run_date, spec)
    reference = _baseline(run_date, spec)
    _guard(run_date, spec)
    end = run_date + timedelta(days=max(spec["horizons"]))
    index = pd.date_range(
        date.fromisoformat(spec["x"]["history_start"]), end, name="timestamp"
    )
    series = pd.DataFrame(
        {
            "target": history.reindex(index),
            "dow": index.dayofweek,
            "doy_sin": [
                math.sin(2 * math.pi * (d.dayofyear - 1) / 365.2425) for d in index
            ],
            "doy_cos": [
                math.cos(2 * math.pi * (d.dayofyear - 1) / 365.2425) for d in index
            ],
        },
        index=index,
    )

    def raw_response(source):
        return pd.DataFrame([{"source_json": json.dumps(source, ensure_ascii=False)}])

    return {
        "series": series,
        "last_observed": history.index[-1].date(),
        "raw": {
            "historical_daily.csv": raw_history.reset_index(),
            "history_response.csv": raw_response(historical_source),
            "baseline_response.csv": raw_response(reference["source"]),
        },
        "sources": {
            "historical": {
                "url": historical_source["url"],
                "retrieved_at": historical_source["retrieved_at"],
                "model": spec["x"]["history_model"],
            },
            "baseline": {
                "url": reference["source"]["request_url"],
                "retrieved_at": reference["source"]["retrieved_at"],
                "model": spec["x"]["forecast_model"],
            },
            "attribution": "Open-Meteo; Copernicus ERA5; ECMWF IFS. Check current source licenses and service terms.",
        },
        "covariate_notes": {
            "dow": "Weekday, Monday=0; known for all dates.",
            "doy_sin": "Annual cycle sine; known for all dates.",
            "doy_cos": "Annual cycle cosine; known for all dates.",
        },
    }


def truth(today, spec):
    if today > _now(spec).date():
        raise RuntimeError("Cannot settle future temperatures.")
    history, _, _ = _history(today, spec)
    return history


def baseline(run_date, spec):
    return _baseline(run_date, spec)
