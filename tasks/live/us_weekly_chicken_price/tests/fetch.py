"""Host-only USDA chicken-price acquisition; stdlib + pandas, no API key.

Historical bootstrap is a downloaded vintage, with each row's verification and
PDF URL retained. Live data never fill gaps by interpolation or another product.
"""

from __future__ import annotations

import json
import math
import time
from datetime import date, datetime, timedelta
from datetime import time as clock
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd

ZONE = ZoneInfo("America/New_York")
START = date(2021, 1, 1)
WEB = "https://mymarketnews.ams.usda.gov/viewReport/2756"
ENDPOINT = (
    "https://mymarketnews.ams.usda.gov/public_data/"
    "ajax-search-data-by-report-section/2756/Report%20Details"
)


def _now():
    return datetime.now(ZONE)


def _date(value):
    return datetime.strptime(value, "%m/%d/%Y").replace(tzinfo=ZONE).date()


def _label(day):
    if day.weekday() not in (3, 4):
        raise ValueError("Unexpected report end weekday; review holiday mapping.")
    return day + timedelta(days=4 - day.weekday())


def _get(start, end):
    query = (
        "commodity=CHICKEN;section=Parts;condition=Fresh;environment=Conventional;"
        f"report_begin_date={start:%m/%d/%Y}:{end:%m/%d/%Y}"
    )
    url = ENDPOINT + "?" + urlencode({"q": query})
    for attempt in range(3):
        try:
            with urlopen(
                Request(url, headers={"User-Agent": "agentic-forecasting/1.0"}),
                timeout=60,
            ) as response:
                result = json.load(response)
            break
        except (OSError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(2**attempt)
    stats, rows = result["stats"], result["results"]
    if stats["returnedRows"] != len(rows):
        raise ValueError("USDA response row count mismatch.")
    if stats["totalRows"] != stats["returnedRows"]:
        if start == end:
            raise ValueError("Single-day USDA response is truncated.")
        mid = start + (end - start) // 2
        left, a = _get(start, mid)
        right, b = _get(mid + timedelta(days=1), end)
        return left + right, a + b
    for row in rows:
        if not start <= _date(row["report_begin_date"]) <= end:
            raise ValueError("USDA ignored requested date range.")
        if (row["section"], row["condition"], row["environment"]) != (
            "Parts",
            "Fresh",
            "Conventional",
        ):
            raise ValueError("USDA ignored product filters.")
    return rows, [url]


def _select(rows, as_of):
    values = {}
    # Source has exact duplicate records. Conflicting selected rows fail below.
    unique = {json.dumps(r, sort_keys=True): r for r in rows}
    for row in unique.values():
        legacy = (
            row["type"] == "BREAST, BONELESS/SKINLESS, VALUE PACK"
            and row["package_size"] is None
        )
        current = (
            row["type"] == "Breast, Boneless/Skinless"
            and row["package_size"] == "Value"
        )
        if not (legacy or current) or row["region"].upper() != "NATIONAL":
            continue
        if (
            str(row["commodity"]).upper() != "CHICKEN"
            or row["price_unit"] != "Per lb"
            or int(row["slug_id"]) != 2756
        ):
            raise ValueError("Unexpected commodity, report or price unit.")
        begin, end = _date(row["report_begin_date"]), _date(row["report_end_date"])
        label = _label(end)
        if (end - begin).days != 6:
            raise ValueError("Expected a seven-day reporting period.")
        if label > as_of:
            continue
        price, stores = row["price_avg"], row["store_count"]
        if (
            isinstance(price, bool)
            or not isinstance(price, (int, float))
            or not math.isfinite(price)
            or price <= 0
            or abs(price * 100 - round(price * 100)) > 1e-7
            or isinstance(stores, bool)
            or not isinstance(stores, (int, float))
            or not math.isfinite(stores)
            or stores <= 0
            or int(stores) != stores
        ):
            raise ValueError("Invalid price or advertising store count.")
        if label in values:
            raise ValueError("Conflicting/ambiguous national records for one week.")
        values[label] = {"target": float(price), "advertising_store_count": int(stores)}
    return values


def _history(as_of):
    bootstrap = pd.read_csv(Path(__file__).with_name("historical.csv"))
    labels = pd.to_datetime(bootstrap.week_ending_label)
    if labels.duplicated().any() or not labels.equals(
        pd.Series(pd.date_range(START, "2023-12-29", freq="W-FRI"))
    ):
        raise ValueError("Historical bootstrap date coverage changed.")
    historical = bootstrap.set_index(labels)[
        ["price_usd_per_lb", "advertising_store_count"]
    ]
    historical.columns = ["target", "advertising_store_count"]
    historical = historical.loc[historical.index.date <= as_of]
    urls, rows = [], []
    if as_of >= date(2024, 1, 1):
        # Include December beginnings for the first January reporting week.
        rows, urls = _get(date(2023, 12, 30), as_of)
        selected = _select(rows, as_of)
        live = pd.DataFrame.from_dict(selected, orient="index")
        if not live.empty:
            live.index = pd.to_datetime(live.index)
            historical = pd.concat([historical, live]).sort_index()
    if historical.index.duplicated().any():
        raise ValueError("Duplicate historical/live boundary.")
    historical.index.name = "timestamp"
    expected = pd.date_range(START, as_of, freq="W-FRI", name="timestamp")
    return historical.reindex(expected), bootstrap, rows, urls


def _guard(run_date, spec):
    now = _now()
    if (
        run_date.weekday() != 4
        or spec["horizons"] != [7]
        or spec["schedule"]["timezone"] != ZONE.key
        or spec["schedule"]["cutoff"] != "15:50"
        or spec["x"]["history_start"] != START.isoformat()
    ):
        raise ValueError(
            "Require Friday, +7 days, 15:50 Eastern and January 2021 history."
        )
    start = datetime.combine(run_date, clock(14, 0), ZONE)
    cutoff = start.replace(hour=15, minute=50)
    if now.date() != run_date or not start <= now < cutoff:
        raise RuntimeError(
            "Fresh snapshots require the current Friday 14:00–15:50 Eastern."
        )
    return now


def snapshot(run_date: date, spec: dict) -> dict:
    as_of = _guard(run_date, spec)
    history, bootstrap, rows, urls = _history(run_date)
    if (
        history.empty
        or history.isna().any().any()
        or history.index[-1].date() != run_date
    ):
        raise RuntimeError(
            "Missing current report or intervening week; skip stale/incomplete run."
        )
    _guard(run_date, spec)
    index = pd.date_range(
        START, run_date + timedelta(days=7), freq="W-FRI", name="timestamp"
    )
    series = history[["target"]].reindex(index)
    series["iso_week"] = index.isocalendar().week.to_numpy(dtype=int)
    series["month"] = index.month
    return {
        "series": series,
        "last_observed": run_date,
        "raw": {
            "verified_historical_bootstrap.csv": bootstrap,
            "usda_report_details.csv": pd.DataFrame(rows),
        },
        "sources": {
            "usda": {
                "name": "USDA AMS report 2756",
                "url": WEB,
                "queries": urls,
                "rights": "https://www.usda.gov/policies-and-links",
            },
            "as_of": as_of.isoformat(),
            "bootstrap": "2021-2023 downloaded vintage, independently PDF-verified",
        },
        "covariate_notes": {
            "iso_week": "ISO week of the Friday reporting-week label.",
            "month": "Month of the Friday reporting-week label.",
        },
    }


def truth(today: date, spec: dict) -> pd.Series:
    if today > _now().date():
        raise RuntimeError("Cannot settle a future date.")
    # Pipeline update_truth preserves the first captured nonmissing observation.
    return _history(today)[0]["target"].dropna()


def baseline(run_date: date, spec: dict) -> dict:
    # This interface is reserved for a matching public expert forecast.
    return {
        "source": {"report": "No matching public weekly expert forecast verified"},
        "forecasts": [],
    }


def reference_forecast(series: pd.Series) -> float:
    """Simple reference: arithmetic mean of the latest four consecutive observations.

    Call with snapshot's observed target only. This is not a public expert B issue.
    """
    observed = series.dropna()
    last = observed.tail(4)
    if len(last) != 4 or not last.index.equals(
        pd.date_range(last.index[0], periods=4, freq="W-FRI")
    ):
        raise ValueError("Reference requires four consecutive observed Friday weeks.")
    return float(last.mean())
