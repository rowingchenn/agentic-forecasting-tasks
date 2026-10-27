"""Norway structural liquidity: where the numbers for this target come from.

The agentic-forecasting pipeline imports this file on the host, outside every episode; the
sandbox's setup stage never runs it, and setup/run.sh makes it unreadable to the agent.
It needs only the standard library and pandas. The pipeline passes `spec`, the target
definition from task.yaml (`extras.agentic-forecasting` plus `params`), and turns the
results into archived files.

    snapshot(run_date, spec)  X_d: structural liquidity history and the payment calendar
    truth(today, spec)        every day's structural liquidity published so far
    baseline(run_date, spec)  Norges Bank's own forecast, its latest release by the run date

No credentials: Norges Bank's SDMX API is keyless, and Norges Bank allows its data to be
reused with the source stated. Each release of the liquidity forecast is a version
(FORECAST_DATE) of one calendar year's set (FORECAST_YEAR). It holds the actual values up
to the day before the release (STAT_S) and the forecast for the rest of that year
(FCAST_S); a year's set starts in mid-December of the year before, with forecasts only.
Every version stays available.
"""

from __future__ import annotations

import io
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

API = "https://data.norges-bank.no/api/data/LIQUIDITY_FORECAST"
ACTUAL = "STAT_S"  # structural liquidity, NOK million
FORECAST = "FCAST_S"  # Norges Bank's forecast of it
FORMAT = "format=csv"  # plain numbers: locale=en would print 214286 as 214,286
RELEASE_HOUR = 12  # a release comes out at noon, Oslo time, on its date
ABOUT = (
    "https://www.norges-bank.no/en/topics/liquidity-and-markets/bank-liquidity/"
    "about-liquidity-forecast/"
)
CALENDAR_COLUMNS = [
    "dow",
    "business_day",
    "tax_withholding",
    "tax_corporate",
    "tax_vat",
    "tax_petroleum",
    "pensions",
    "month_start",
]
# The government's regular payments, as Norges Bank's "About the liquidity forecast" lists
# them: (month, day) dates, and the way a date off a business day moves, +1 to the next
# business day (payments to the government) or -1 to the previous one (payments by it).
PAYMENTS = {
    "tax_withholding": ([(m, 15) for m in (1, 3, 5, 7, 9, 11)], 1),  # and employer contributions
    "tax_corporate": ([(2, 15), (4, 15)], 1),  # companies' advance tax: seen in the data
    "tax_vat": ([(2, 10), (4, 10), (6, 10), (8, 31), (10, 10), (12, 10)], 1),
    "tax_petroleum": ([(m, 1) for m in (2, 4, 6, 8, 10, 12)], 1),
    "pensions": ([(m, 19) for m in range(1, 13)], -1),  # NAV pensions and benefits
}
# Days Norwegian banks are closed: fixed dates, then days counted from Easter Sunday (Maundy
# Thursday, Good Friday, Easter Monday, Ascension Day, Whit Monday).
CLOSED_FIXED = [(1, 1), (5, 1), (5, 17), (12, 24), (12, 25), (12, 26), (12, 31)]
CLOSED_EASTER = [-3, -2, 1, 39, 50]


def _get(url: str, retries: int = 5) -> bytes:
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=180) as response:
                return response.read()
        except OSError as exc:
            if attempt == retries - 1 or getattr(exc, "code", None) == 404:
                raise
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def _get_optional(url: str) -> bytes | None:
    """The reply at url, or None if the API has no data for it."""
    try:
        return _get(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _csv(raw: bytes) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(raw), sep=";", dtype=str)


def versions(year: int) -> dict[str, list[date]]:
    """Release dates of one year's set per series, oldest first; {} before its first release."""
    raw = _get_optional(f"{API}/D.{ACTUAL}+{FORECAST}.{year}.?detail=serieskeysonly&{FORMAT}")
    if raw is None:
        return {}
    keys = _csv(raw)
    return {
        str(series): sorted({date.fromisoformat(d) for d in group["FORECAST_DATE"]})
        for series, group in keys.groupby("FORECAST_TYPE")
    }


def version(year: int, published: date) -> pd.DataFrame:
    """Every value of one release of one year's set: series, date and value (NOK million). A
    day without a value counts as not published."""
    rows = _csv(_get(f"{API}/D.{ACTUAL}+{FORECAST}.{year}.{published}?{FORMAT}"))
    rows = rows.dropna(subset=["OBS_VALUE"])
    return pd.DataFrame(
        {
            "series": rows["FORECAST_TYPE"],
            "date": pd.to_datetime(rows["TIME_PERIOD"]),
            "value": pd.to_numeric(rows["OBS_VALUE"], errors="raise").astype(float),
        }
    )


def latest(dates: list[date], day: date) -> date | None:
    """The last of `dates` on or before `day`."""
    return max((d for d in dates if d <= day), default=None)


def actuals(start: date, day: date, strict: bool = False) -> pd.DataFrame:
    """Structural liquidity from `start` on, each year's from its latest release dated on or
    before `day`; a year with no such release (yet) is left out. A year's set exists from
    mid-December of the year before, so with `strict` a year the API has no set for at all
    is an error rather than a gap."""
    columns = ["forecast_year", "version", "date", "value"]
    frames = []
    for year in range(start.year, day.year + 1):
        found = versions(year)
        if strict and not found:
            raise LookupError(f"Norges Bank's API lists no {year} liquidity forecast")
        published = latest(found.get(ACTUAL, []), day)
        if published is None:
            continue
        rows = version(year, published)
        rows = rows[(rows["series"] == ACTUAL) & (rows["date"] >= pd.Timestamp(start))]
        frames.append(rows.assign(forecast_year=year, version=published.isoformat())[columns])
    if not frames:
        return pd.DataFrame(columns=columns)
    return pd.concat(frames).sort_values("date").reset_index(drop=True)


def closed_days(year: int) -> set[date]:
    """Norwegian public holidays, Christmas Eve and New Year's Eve."""
    easter = (pd.Timestamp(year, 1, 1) + pd.offsets.Easter()).date()
    fixed = {date(year, month, day) for month, day in CLOSED_FIXED}
    return fixed | {easter + timedelta(days=offset) for offset in CLOSED_EASTER}


def is_business_day(day: date) -> bool:
    return day.weekday() < 5 and day not in closed_days(day.year)


def _move(day: date, step: int) -> date:
    """`day` if it is a business day, else the nearest one in the direction of `step`."""
    while not is_business_day(day):
        day += timedelta(days=step)
    return day


def calendar(index: pd.DatetimeIndex) -> pd.DataFrame:
    """Day of week (Monday = 0), business days and the payment days on `index`, as integers."""
    years = range(index.year.min(), index.year.max() + 1)
    events = {
        column: {_move(date(year, m, d), step) for year in years for m, d in dates}
        for column, (dates, step) in PAYMENTS.items()
    }
    events["month_start"] = {_move(date(year, m, 1), 1) for year in years for m in range(1, 13)}
    days = [day.date() for day in index]
    frame = pd.DataFrame({"dow": index.dayofweek}, index=index)
    frame["business_day"] = [is_business_day(day) for day in days]
    for column in CALENDAR_COLUMNS[2:]:
        frame[column] = [day in events[column] for day in days]
    return frame.astype("int64")


def snapshot(run_date: date, spec: dict) -> dict:
    """X_d as published right now: one row per day from history_start to the last target
    date, `target` empty after the last published day, the calendar known for the window."""
    start = date.fromisoformat(spec["x"]["history_start"])
    rows = actuals(start, run_date, strict=True)
    published = rows.set_index("date")["value"]
    end = run_date + timedelta(days=max(spec["horizons"]))
    index = pd.date_range(start, end, freq="D", name="timestamp")
    # Norges Bank never publishes the last days of a year: filled in linearly here, while
    # truth leaves them out
    target = published.reindex(index).interpolate(limit_area="inside").rename("target")
    used = rows.groupby("forecast_year")["version"].first()
    return {
        "series": pd.concat([target, calendar(index)], axis=1).rename_axis("timestamp"),
        "last_observed": published.index.max().date(),
        "raw": {"structural_liquidity.csv": rows},
        "sources": {
            "structural_liquidity": {
                "url": API,
                "series": ACTUAL,
                "versions": {str(year): v for year, v in used.items()},
            },
            "calendar": {
                "url": ABOUT,
                "note": "computed from the payment dates and holiday rules Norges Bank lists",
            },
        },
        "covariate_notes": {
            "target": "structural liquidity (NOK million) as in each year's latest release "
            "by the run date; the last days of a year, never published, are filled linearly",
            "dow": "day of week, Monday = 0",
            "business_day": "1 on Norwegian business days: not a weekend, a public holiday, "
            "24 or 31 December",
            "tax_withholding": "1 on the deadline for withholding tax and employer "
            "contributions (the 15th of odd months)",
            "tax_corporate": "1 on the deadlines for companies' advance tax (15 February and "
            "15 April)",
            "tax_vat": "1 on the VAT deadlines (the 10th of even months except August, and "
            "31 August)",
            "tax_petroleum": "1 on the petroleum tax deadlines (the 1st of even months)",
            "pensions": "1 on NAV's pension and benefit payment day (the 19th)",
            "month_start": "1 on the first business day of each month (payments to health "
            "trusts and local governments)",
            "moved": "a tax deadline off a business day moves to the next business day, a "
            "payment by the government to the previous one",
        },
    }


def truth(today: date, spec: dict) -> pd.Series:
    """Structural liquidity of every day published so far."""
    rows = actuals(date.fromisoformat(spec["x"]["history_start"]), today)
    return pd.Series(rows["value"].to_numpy(float), index=pd.DatetimeIndex(rows["date"]))


def baseline(run_date: date, spec: dict) -> dict:
    """Norges Bank's own forecast: for each target date's year, FCAST_S of that year's latest
    release dated on or before the run date, normally the one published at noon that day.
    Every release stays on the API, so a later call gives the same numbers."""
    timezone = ZoneInfo(spec["schedule"]["timezone"])
    targets = [run_date + timedelta(days=h) for h in spec["horizons"]]
    used: dict[str, str] = {}
    points: dict[date, float] = {}
    for year in sorted({target.year for target in targets}):
        published = latest(versions(year).get(FORECAST, []), run_date)
        if published is None:  # the next year's forecast first appears in mid-December
            continue
        rows = version(year, published)
        forecast = rows[rows["series"] == FORECAST].set_index("date")["value"]
        used[str(year)] = published.isoformat()
        for target in targets:
            if target.year == year and pd.Timestamp(target) in forecast.index:
                points[target] = float(forecast[pd.Timestamp(target)])
    newest = max(used.values(), default=None)
    posted = None
    if newest is not None:
        day = date.fromisoformat(newest)
        posted = datetime(day.year, day.month, day.day, RELEASE_HOUR, tzinfo=timezone).isoformat()
    return {
        "source": {
            "report": f"Norges Bank structural liquidity forecast ({FORECAST})",
            "posted": posted,
            "model": "Norges Bank",
            "versions": used,
        },
        "forecasts": [
            {"target_date": target.isoformat(), "point": round(points[target], 2)}
            for target in targets
            if target in points
        ],
    }
