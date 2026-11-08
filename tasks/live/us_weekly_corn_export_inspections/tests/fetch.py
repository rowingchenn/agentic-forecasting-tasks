"""Host-only USDA weekly corn-inspection acquisition; stdlib + pandas, no key.

Use the source's week-ending field, not certification-year files. API aggregates
are reconciled to independent count queries and a stable dataset update version.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from datetime import time as clock
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd

ZONE = ZoneInfo("America/New_York")
START = date(2024, 1, 4)
WEB = "https://agtransport.usda.gov/Exports/Grain-Inspections/sruw-w49i"
API = "https://agtransport.usda.gov/resource/sruw-w49i.json"
META = "https://agtransport.usda.gov/api/views/sruw-w49i.json"
LIMIT = 5000


def _now():
    return datetime.now(ZONE)


def _get(url):
    for attempt in range(3):
        try:
            with urlopen(
                Request(url, headers={"User-Agent": "agentic-forecasting/1.0"}),
                timeout=60,
            ) as response:
                return json.load(response)
        except HTTPError as exc:
            if attempt == 2 or exc.code not in (429, 500, 502, 503, 504):
                raise
            retry = exc.headers.get("Retry-After", "")
            delay = min(60, int(retry)) if retry.isdigit() else 2**attempt
        except (OSError, TimeoutError):
            if attempt == 2:
                raise
            delay = 2**attempt
        time.sleep(delay)
    raise AssertionError("unreachable")


def _integer(value):
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Invalid metric-ton amount or record count.") from exc
    if not number.is_finite() or number < 0 or number != number.to_integral_value():
        raise ValueError("Expected a finite nonnegative whole metric-ton amount/count.")
    return int(number)


def _schema(metadata):
    columns = {c.get("fieldName"): c for c in metadata["columns"]}
    for field, kind, name in [
        ("date", "calendar_date", "Week Ending Date"),
        ("grain", "text", "Grain"),
        ("mt", "number", "MT"),
    ]:
        if (
            columns.get(field, {}).get("dataTypeName") != kind
            or columns.get(field, {}).get("name") != name
        ):
            raise ValueError("USDA field names, units or types changed; review source.")
    if metadata.get("name") != "Grain Inspections" or not metadata.get("rowsUpdatedAt"):
        raise ValueError("Unexpected USDA dataset or absent update timestamp.")


def _parse(rows, stats, end):
    if len(stats) != 1 or not isinstance(rows, list):
        raise ValueError("Unexpected USDA aggregate/count response.")
    total = stats[0]
    count, weeks = _integer(total["records"]), _integer(total["weeks"])
    if weeks != len(rows) or len(rows) >= LIMIT:
        raise ValueError("Weekly response missing rows or reached the request limit.")
    if _integer(total["valid_records"]) != count or (
        count and _integer(total["min_mt"]) < 0
    ):
        raise ValueError("Missing or invalid source metric-ton values.")
    observations, records = {}, 0
    for row in rows:
        stamp = pd.Timestamp(row["date"])
        if stamp.tzinfo is not None or stamp != stamp.normalize():
            raise ValueError("Expected an unzoned midnight week-ending date.")
        day = stamp.date()
        if day.weekday() != 3 or not START <= day <= end or stamp in observations:
            raise ValueError("Unexpected, duplicate or out-of-range Thursday label.")
        n = _integer(row["records"])
        if n == 0 or _integer(row["valid_records"]) != n:
            raise ValueError("Missing inspection amounts within a week.")
        _integer(row["min_mt"])
        observations[stamp] = float(_integer(row["target"]))
        records += n
    if records != count:
        raise ValueError("Weekly row counts do not reconcile to source total.")
    index = pd.date_range(START, end, freq="W-THU", name="timestamp")
    return pd.Series(observations, name="target", dtype=float).reindex(index)


def _history(end):
    if end < START:
        return pd.Series(dtype=float, name="target"), pd.DataFrame(), []
    before = _get(META)
    _schema(before)
    where = f"grain='CORN' AND date >= '{START.isoformat()}T00:00:00' AND date <= '{end.isoformat()}T00:00:00'"
    queries = [
        {
            "$select": "date,sum(mt) as target,count(*) as records,count(mt) as valid_records,min(mt) as min_mt",
            "$where": where,
            "$group": "date",
            "$order": "date",
            "$limit": LIMIT,
        },
        {
            "$select": "count(*) as records,count(distinct date) as weeks,count(mt) as valid_records,min(mt) as min_mt",
            "$where": where,
        },
    ]
    urls = [API + "?" + urlencode(q) for q in queries]
    rows, stats = [_get(url) for url in urls]
    after = _get(META)
    _schema(after)
    if before["rowsUpdatedAt"] != after["rowsUpdatedAt"]:
        raise RuntimeError(
            "USDA dataset changed during acquisition; retry a fresh snapshot."
        )
    history = _parse(rows, stats, end)
    raw = pd.DataFrame(rows)
    raw["dataset_rows_updated_at"] = before["rowsUpdatedAt"]
    raw["source_total_records"] = stats[0]["records"]
    return history, raw, [META, *urls]


def _guard(run_date, spec):
    now = _now()
    if (
        run_date.weekday() != 3
        or spec["horizons"] != [7]
        or spec["schedule"]["timezone"] != ZONE.key
        or spec["schedule"]["cutoff"] != "15:50"
        or spec["x"]["history_start"] != START.isoformat()
    ):
        raise ValueError(
            "Require Thursday, +7 days, 15:50 Eastern and January 2024 history."
        )
    start = datetime.combine(run_date, clock(14, 0), ZONE)
    if now.date() != run_date or not start <= now < start.replace(hour=15, minute=50):
        raise RuntimeError(
            "Fresh snapshots require the current Thursday 14:00–15:50 Eastern."
        )
    return now


def snapshot(run_date: date, spec: dict) -> dict:
    as_of = _guard(run_date, spec)
    last = run_date - timedelta(days=7)
    history, raw, urls = _history(last)
    if history.empty or history.isna().any() or history.index[-1].date() != last:
        raise RuntimeError(
            "Missing expected latest report or intervening week; skip stale run."
        )
    _guard(run_date, spec)
    index = pd.date_range(
        START, run_date + timedelta(days=7), freq="W-THU", name="timestamp"
    )
    series = pd.DataFrame(
        {
            "target": history.reindex(index),
            "iso_week": index.isocalendar().week.to_numpy(dtype=int),
            "month": index.month,
        },
        index=index,
    )
    return {
        "series": series,
        "last_observed": last,
        "raw": {"usda_weekly_corn_aggregates.csv": raw},
        "sources": {
            "corn": {
                "name": "USDA FGIS / AMS Grain Inspections",
                "url": WEB,
                "queries": urls,
                "grain": "CORN",
                "unit": "metric tons",
                "rights": "https://www.usda.gov/policies-and-links",
            },
            "as_of": as_of.isoformat(),
            "history_vintage": "As available at acquisition; may include revisions",
        },
        "covariate_notes": {
            "iso_week": "ISO week of the Thursday reporting-week label.",
            "month": "Month of the Thursday reporting-week label.",
        },
    }


def truth(today: date, spec: dict) -> pd.Series:
    if today > _now().date():
        raise RuntimeError("Cannot settle future dates.")
    # A target Thursday becomes eligible on the following Monday, never mid-period.
    end = today - timedelta(days=4)
    end -= timedelta(days=(end.weekday() - 3) % 7)
    return _history(end)[0].dropna()


def baseline(run_date: date, spec: dict) -> dict:
    return {
        "source": {"report": "No matching public expert weekly forecast verified"},
        "forecasts": [],
    }


def reference_forecast(series: pd.Series) -> float:
    """Four latest consecutive observed weekly totals; a simple reference, not expert B.

    Supply snapshot's target column. With this schedule the latest observation is
    two weekly labels before the target; do not accidentally use the unpublished week.
    """
    last = series.dropna().tail(4)
    if len(last) != 4 or not last.index.equals(
        pd.date_range(last.index[0], periods=4, freq="W-THU")
    ):
        raise ValueError("Reference requires four consecutive observed Thursday weeks.")
    return float(last.mean())
