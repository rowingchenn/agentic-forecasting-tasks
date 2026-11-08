"""Host-only EIA-930 SWPP WND history; no credentials or selected expert forecast.

Use only the raw 'Wind without Integrated Battery Storage' column, never WNB,
other fuel categories, EIA's imputed/adjusted columns, or the legacy unsplit label.
The public six-month CSVs are refreshed on every call because history is revisable.
Missing hours/values make that daily target unknown, not zero or a partial sum.
"""

from __future__ import annotations

import csv
import io
import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from datetime import time as clock
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd

ZONE = ZoneInfo("America/Chicago")
HISTORY_START = date(2024, 7, 1)
WIND = "Net Generation (MW) from Wind without Integrated Battery Storage"
BATTERY_WIND = "Net Generation (MW) from Wind with Integrated Battery Storage"
BASE = "https://www.eia.gov/electricity/gridmonitor/sixMonthFiles"
PAGE = "https://www.eia.gov/electricity/gridmonitor/"
INSTRUCTIONS = "https://www.eia.gov/survey/form/eia_930/instructions.pdf"
ABOUT = "https://www.eia.gov/electricity/930-content/about.html"
USER_AGENT = (
    "agentic-forecasting-swpp-wind/1.0 "
    "(https://github.com/rowingchenn/agentic-forecasting-tasks)"
)
REQUIRED = {"Balancing Authority", "Data Date", "UTC Time at End of Hour", WIND}
MAX_LAG_DAYS = 3


def _now():
    return datetime.now(ZONE)


def _guard(run_date, spec):
    if spec["schedule"]["timezone"] != ZONE.key:
        raise ValueError("SWPP reporting days use America/Chicago.")
    now = _now()
    cutoff = datetime.combine(
        run_date, clock.fromisoformat(spec["schedule"]["cutoff"]), ZONE
    )
    if now.date() != run_date or now >= cutoff:
        raise RuntimeError(
            "Fresh snapshots require the current local day before cutoff."
        )
    return now


def _url(year, half):
    return f"{BASE}/EIA930_BALANCE_{year}_{half}.csv"


def _periods(start, end):
    return [
        (year, half)
        for year in range(start.year, end.year + 1)
        for half, first, last in [
            ("Jan_Jun", date(year, 1, 1), date(year, 6, 30)),
            ("Jul_Dec", date(year, 7, 1), date(year, 12, 31)),
        ]
        if first <= end and last >= start
    ]


def _read_csv(stream, start, end):
    """Read all SWPP rows; do not assume the file is sorted by balancing authority."""
    reader = csv.DictReader(stream)
    if not reader.fieldnames or not REQUIRED.issubset(reader.fieldnames):
        raise ValueError(
            "EIA-930 schema changed; require the explicit non-battery wind column."
        )
    records = []
    for row in reader:
        if row["Balancing Authority"].strip() != "SWPP":
            continue
        day = (
            datetime.strptime(row["Data Date"], "%m/%d/%Y").replace(tzinfo=ZONE).date()
        )
        if not start <= day <= end:
            continue
        hour = datetime.strptime(
            row["UTC Time at End of Hour"], "%m/%d/%Y %I:%M:%S %p"
        ).replace(tzinfo=UTC)
        if hour.minute or hour.second:
            raise ValueError("Expected whole-hour UTC end timestamps.")
        # Midnight hour-ending belongs to the previous local reporting day.
        if (hour.astimezone(ZONE) - timedelta(microseconds=1)).date() != day:
            raise ValueError(
                "EIA Data Date disagrees with the Central-time reporting day."
            )
        text = row[WIND].strip()
        value = float(text) if text else float("nan")
        if text and not math.isfinite(value):
            raise ValueError("Non-finite reported wind value.")
        records.append(
            {
                "date": day.isoformat(),
                "utc_hour_ending": hour.isoformat(),
                "wnd_mw": value,
                "wnb_as_reported": row.get(BATTERY_WIND, ""),
                "balancing_authority": "SWPP",
            }
        )
    return records


def _download(period, start, end):
    url = _url(*period)
    for attempt in range(4):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(request, timeout=120) as response:
                rows = _read_csv(
                    io.TextIOWrapper(response, encoding="utf-8-sig"), start, end
                )
            if not rows:
                raise ValueError(
                    f"No SWPP rows in {url}; do not accept an empty source file."
                )
            return rows, url
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise
            retry = exc.headers.get("Retry-After", "")
            delay = min(120, int(retry)) if retry.isdigit() else 2**attempt
        except (URLError, TimeoutError, ConnectionError):
            if attempt == 3:
                raise
            delay = 2**attempt
        time.sleep(delay)
    raise AssertionError("unreachable")


def _aggregate(rows, start, end):
    """Require every distinct UTC hour of the 23/24/25-hour local day, with a WND value."""
    by_day = {}
    seen = set()
    for row in rows:
        hour = datetime.fromisoformat(row["utc_hour_ending"])
        if hour in seen:
            raise ValueError(
                "Duplicate SWPP UTC hour; do not double count or choose a revision."
            )
        seen.add(hour)
        by_day.setdefault(date.fromisoformat(row["date"]), {})[hour] = row["wnd_mw"]
    values, audit = {}, []
    day = start
    while day <= end:
        first = datetime.combine(day, clock(), ZONE).astimezone(UTC)
        last = datetime.combine(day + timedelta(days=1), clock(), ZONE).astimezone(UTC)
        expected = {
            first + timedelta(hours=k)
            for k in range(1, int((last - first).total_seconds() / 3600) + 1)
        }
        reported = by_day.get(day, {})
        if set(reported) - expected:
            raise ValueError("Hour outside its local day; reject the source snapshot.")
        valid = sum(math.isfinite(v) for v in reported.values())
        complete = set(reported) == expected and valid == len(expected)
        # The EIA values are hourly integrated MW: a one-hour observation contributes
        # numerically the same number of MWh. Real zeros remain zero; blanks remain gaps.
        value = math.fsum(reported.values()) if complete else float("nan")
        if complete and value < 0:
            raise ValueError(
                "Negative daily SWPP WND total; investigate before settling."
            )
        values[pd.Timestamp(day)] = value
        audit.append(
            {
                "date": day.isoformat(),
                "expected_hours": len(expected),
                "reported_hours": len(reported),
                "valid_wnd_hours": valid,
                "complete": complete,
                "wnd_mwh": value,
            }
        )
        day += timedelta(days=1)
    series = pd.Series(values, name="target", dtype=float).rename_axis("timestamp")
    return series, pd.DataFrame(audit)


def _daily(start, end):
    if start < HISTORY_START or start > end:
        raise ValueError(
            "Require nonempty history from 2024-07-01 or later, with explicit WND."
        )
    periods = _periods(start, end)
    with ThreadPoolExecutor(max_workers=3) as pool:
        batches = list(pool.map(lambda p: _download(p, start, end), periods))
    rows = [row for batch, _ in batches for row in batch]
    history, audit = _aggregate(rows, start, end)
    raw = pd.DataFrame(rows).sort_values("utc_hour_ending").reset_index(drop=True)
    return history, raw, audit, [url for _, url in batches]


def snapshot(run_date: date, spec: dict) -> dict:
    as_of = _guard(run_date, spec)
    start = date.fromisoformat(spec["x"]["history_start"])
    history, raw, audit, urls = _daily(start, run_date - timedelta(days=1))
    last = history.last_valid_index()
    if last is None or last.date() < run_date - timedelta(days=MAX_LAG_DAYS):
        raise RuntimeError(
            "No complete SWPP WND day in the past three days; review publication."
        )
    _guard(run_date, spec)
    index = pd.date_range(
        start, run_date + timedelta(days=max(spec["horizons"])), name="timestamp"
    )
    series = pd.DataFrame(
        {
            "target": history.reindex(index),
            "dow": index.dayofweek,
            "month": index.month,
            "day_of_year": index.dayofyear,
        },
        index=index,
    )
    return {
        "series": series,
        "last_observed": last.date(),
        "raw": {"swpp_wnd_hourly.csv": raw, "daily_completeness.csv": audit},
        "sources": {
            "wind": {
                "name": "U.S. Energy Information Administration, EIA-930",
                "url": PAGE,
                "downloads": urls,
                "balancing_authority": "SWPP",
                "column": WIND,
                "instructions": INSTRUCTIONS,
                "about": ABOUT,
                "data_use": "https://www.eia.gov/about/copyrights_reuse.php",
            },
            "as_of": as_of.isoformat(),
        },
        "covariate_notes": {
            "dow": "Central-time weekday, Monday=0.",
            "month": "Central-time calendar month, 1-12.",
            "day_of_year": "Central-time calendar day of year, 1-366.",
        },
    }


def truth(today: date, spec: dict) -> pd.Series:
    if today > _now().date():
        raise RuntimeError("Cannot settle future dates.")
    # update_truth appends the first observed complete daily value; subsequent source
    # revisions cannot overwrite it. Historical bootstrap values are current vintages.
    return _daily(
        date.fromisoformat(spec["x"]["history_start"]), today - timedelta(days=1)
    )[0].dropna()


def baseline(run_date: date, spec: dict) -> dict:
    return {
        "source": {
            "report": "No matching public expert forecast selected for SWPP WND"
        },
        "forecasts": [],
    }
