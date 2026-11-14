"""USDA's national advertised price of fresh conventional boneless skinless chicken breast in
value packs, from report 2756, Weekly Grocery Store Chicken Feature Activity: X_d and
first-published truth, both read from the report's data on My Market News, which needs no
key. There is no public expert forecast of this price, so there is no B.

Each issue's data are dated by the week from a Saturday through the Friday the issue is
posted (report_end_date); that Friday labels the week. Until 2022, issues of holiday weeks
ended on the Thursday, which labels the next day. USDA does not revise the figure in
practice, and truth reads the data every day, so the value recorded for a week is the one
of its issue. The server stalls on requests without an Accept header.
"""

from __future__ import annotations

import json
import math
import time
from datetime import date, datetime, timedelta
from datetime import time as clock
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd

ZONE = ZoneInfo("America/New_York")
REPORT = "https://mymarketnews.ams.usda.gov/viewReport/2756"
ENDPOINT = (
    "https://mymarketnews.ams.usda.gov/public_data/"
    "ajax-search-data-by-report-section/2756/Report%20Details"
)
FILTER = "commodity=CHICKEN;section=Parts;condition=Fresh;environment=Conventional;region=NATIONAL"
# The item's name since September 2024, and before it, when the pack was part of the name.
ITEMS = {("Breast, Boneless/Skinless", "Value"), ("BREAST, BONELESS/SKINLESS, VALUE PACK", None)}
COLUMNS = ["report_date", "published_date", "report_begin_date", "report_end_date", "type",
           "package_size", "price_unit", "price_min", "price_max", "price_avg", "store_count"]
USER_AGENT = "agentic-forecasting/1.0"


def _now():
    return datetime.now(ZONE)


def _guard(run_date, spec):
    if spec["schedule"]["timezone"] != ZONE.key:
        raise ValueError("USDA's posting times and the run schedule are in America/New_York.")
    now = _now()
    cutoff = datetime.combine(run_date, clock.fromisoformat(spec["schedule"]["cutoff"]), ZONE)
    if run_date.weekday() != 4 or now.date() != run_date or now >= cutoff:
        raise RuntimeError("Fresh snapshots require the current Friday before the cutoff.")
    return now


def _get(start, end):
    """Rows of the national fresh conventional parts table whose week begins from start
    through end. The server returns at most 1,500 rows and says so only in its counts, so a
    range it truncates is split in two."""
    query = f"{FILTER};report_begin_date={start:%m/%d/%Y}:{end:%m/%d/%Y}"
    url = ENDPOINT + "?" + urlencode({"q": query})
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    for attempt in range(4):
        try:
            with urlopen(Request(url, headers=headers), timeout=90) as response:
                payload = json.load(response)
            break
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise
        except (URLError, TimeoutError, ConnectionError):
            if attempt == 3:
                raise
        time.sleep(2**attempt)
    stats, rows = payload["stats"], payload["results"]
    if stats["returnedRows"] != len(rows):
        raise ValueError("My Market News returned a different number of rows than it reports.")
    if stats["totalRows"] > len(rows):
        if start >= end:
            raise ValueError("My Market News truncated a single day's rows.")
        middle = start + (end - start) // 2
        return _get(start, middle) + _get(middle + timedelta(days=1), end)
    return rows


def _weekly(start, today):
    """Every Friday from start through the latest one published by today (NaN for a week
    without an issue), and the rows it is read from."""
    if start.weekday() != 4:
        raise ValueError("history_start must be a Friday.")
    rows = pd.DataFrame(_get(start - timedelta(days=7), today), columns=COLUMNS)
    item = [(t, None if pd.isna(p) else p) in ITEMS for t, p in zip(rows["type"], rows["package_size"])]
    rows = rows[item].copy()
    if rows.empty:
        raise ValueError("No national price in the requested range.")
    if (rows["price_unit"] != "Per lb").any():
        raise ValueError("The price is no longer per pound.")
    end = pd.to_datetime(rows["report_end_date"], format="%m/%d/%Y")
    if not end.dt.weekday.isin([3, 4]).all():
        raise ValueError("Expected weeks that end on a Friday, or a Thursday before a holiday.")
    rows["week"] = end + pd.to_timedelta(4 - end.dt.weekday, unit="D")
    rows = rows[(rows["week"] >= pd.Timestamp(start)) & (rows["week"] <= pd.Timestamp(today))]
    # The data repeat some rows; were a week ever posted twice, its first issue settles it.
    posted = pd.to_datetime(rows["published_date"], format="%m/%d/%Y %H:%M:%S")
    rows = rows.assign(posted=posted).sort_values(["week", "posted"]).drop_duplicates("week")
    # a week without a positive price is left empty, as one without an issue
    prices = pd.to_numeric(rows["price_avg"], errors="coerce")
    prices = prices.where(prices.map(lambda p: math.isfinite(p) and p > 0))
    if prices.isna().all():
        raise ValueError("No valid national price in the requested range.")
    values = pd.Series(prices.to_numpy(), index=pd.DatetimeIndex(rows["week"]), name="target")
    index = pd.date_range(start, values.index.max(), freq="W-FRI", name="timestamp")
    raw = rows.drop(columns="posted").assign(week=rows["week"].dt.strftime("%Y-%m-%d"))
    return values.reindex(index), raw


def snapshot(run_date: date, spec: dict) -> dict:
    as_of = _guard(run_date, spec)
    start = date.fromisoformat(spec["x"]["history_start"])
    history, raw = _weekly(start, run_date)
    last = history.last_valid_index().date()
    # The issue is posted on the Friday morning, so a run normally has the run date's week.
    # A late issue leaves that week empty; a run day is refused only when two are missing.
    if last < run_date - timedelta(days=7):
        raise RuntimeError(f"USDA's latest published week is {last}; two issues are missing.")
    _guard(run_date, spec)
    targets = [run_date + timedelta(days=h) for h in spec["horizons"]]
    if any(d.weekday() != 4 for d in targets):
        raise ValueError("Target dates must be Fridays.")
    index = pd.date_range(start, max(targets), freq="W-FRI", name="timestamp")
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
        "raw": {"usda_2756_national_value_pack_breast.csv": raw},
        "sources": {
            "price": {"name": "USDA AMS report 2756, Weekly Grocery Store Chicken Feature "
                      "Activity", "url": REPORT, "query": FILTER},
            "as_of": as_of.isoformat(),
        },
        "covariate_notes": {
            "iso_week": "ISO week number of the Friday; may be 53.",
            "month": "Month of the Friday.",
        },
    }


def truth(today: date, spec: dict) -> pd.Series:
    if today > _now().date():
        raise RuntimeError("Cannot settle future dates.")
    # update_truth keeps the first value recorded for each Friday; a week without an issue
    # is left out.
    return _weekly(date.fromisoformat(spec["x"]["history_start"]), today)[0].dropna()


def baseline(run_date: date, spec: dict) -> dict:
    return {"source": {"name": "none: no public expert forecast of this series"}, "forecasts": []}
