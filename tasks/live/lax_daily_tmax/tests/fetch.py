"""Daily maximum temperature at Los Angeles International Airport: where the numbers for this
target come from.

The agentic-forecasting pipeline imports this file on the host, outside every episode; ALE
uploads tests/ only to verify, after the agent has finished. It needs only the standard
library and pandas. `spec` is [metadata.agentic-forecasting] of task.toml with the AF_*
schedule merged in.

    snapshot(run_date, spec)  X_d: the daily maxima and the NWS forecast as published now
    truth(today, spec)        each day's maximum from its first final climate report
    baseline(run_date, spec)  the NWS forecast high, the first issue after the cutoff

All three read NWS text products of the Los Angeles/Oxnard office from the Iowa Environmental
Mesonet's AFOS archive, which needs no key: the Daily Climate Report CLILAX (y), the Tabular
State Forecast SFTLOX (B and the expert covariate) and the Point Forecast Matrices PFMLOX (B
when SFTLOX is missing). Past products never change, so their parsed rows are kept on the host.
"""

from __future__ import annotations

import math
import os
import re
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

IEM_AFOS = "https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py"
OFFICE = "NWS Los Angeles/Oxnard (LOX)"
SFT_ROW = "Los Angeles Intl Airport"  # its row in SFTLOX
PFM_POINT = "Los Angeles Airport CA"  # its segment in PFMLOX, 33.94N 118.39W
LIMIT = 9999  # products per request; a year of SFTLOX is about 2,500
CACHE = (
    Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    / "agentic-forecasting"
    / "lax_daily_tmax"
)
COLUMNS = ["issued", "day", "high"]  # issue time (UTC), the day forecast or reported, °F
ISSUED = re.compile(
    r"^(\d{1,2})(\d\d) ([AP]M) (P[SD]T) \w{3} (\w{3}) (\d{1,2}) (\d{4})", re.M | re.I
)
SENT = re.compile(r"^[A-Z]{4}\d\d K[A-Z]{3} (\d\d)(\d\d)(\d\d)", re.M)  # "FPUS66 KLOX 021828"


def _get(url: str, retries: int = 5) -> str:
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=180) as response:
                return response.read().decode("latin-1")
        except OSError:
            if attempt == retries - 1:
                raise
            time.sleep(30 * 2**attempt)  # outages last minutes
    raise AssertionError("unreachable")


def _at(day: date, clock: str, timezone: str) -> pd.Timestamp:
    """`clock` ("HH:MM", local) on `day`, in UTC."""
    hour, minute = map(int, clock.split(":"))
    local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZoneInfo(timezone))
    return pd.Timestamp(local.astimezone(UTC))


def products(pil: str, start: date, end: date) -> list[str]:
    """The text of every `pil` product IEM archived on the UTC dates start to end - 1, one
    calendar year per request."""
    texts = []
    while start < end:
        stop = min(end, date(start.year + 1, 1, 1))
        query = {"pil": pil, "sdate": start, "edate": stop, "fmt": "text", "limit": LIMIT}
        found = _get(f"{IEM_AFOS}?{urllib.parse.urlencode(query)}").split("\x01")
        assert len(found) <= LIMIT, f"{pil} {start} to {stop}: more products than one request gives"
        texts += found
        start = stop
    return texts


def issue_time(text: str) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """(UTC time sent, local time written) of a product. The time line ("1028 AM PST Mon Nov 2
    2026") gives the date; the WMO header ("FPUS66 KLOX 021828": day, UTC hour and minute) the
    time it was sent, which for a correction is later than its time line."""
    found, sent = ISSUED.search(text), SENT.search(text)
    if not (found and sent):
        return None
    hour, minute, half, zone, month, day, year = found.groups()
    local = pd.Timestamp(f"{month} {day} {year}") + pd.Timedelta(
        hours=int(hour) % 12 + 12 * (half.upper() == "PM"), minutes=int(minute)
    )
    written = (local + pd.Timedelta(hours=8 if zone.upper() == "PST" else 7)).tz_localize(UTC)
    sent_day, sent_hour, sent_minute = map(int, sent.groups())
    days = (written.normalize() + pd.Timedelta(days=k) for k in range(-1, 32))
    utc = next(d for d in days if d.day == sent_day)
    return utc + pd.Timedelta(hours=sent_hour, minutes=sent_minute), local


def climate_reports(texts: list[str]) -> pd.DataFrame:
    """The maximum of each final Daily Climate Report. Preliminary reports ("VALID TODAY AS OF
    0500 PM") and reports whose maximum is missing ("MM") are left out."""
    rows = []
    for text in texts:
        day = re.search(r"CLIMATE SUMMARY FOR (\w+ \d{1,2} \d{4})", text)
        # the MAXIMUM line comes after the TEMPERATURE (F) line and YESTERDAY (or TODAY)
        high = re.search(r"TEMPERATURE \(F\)[^\n]*\n[^\n]*\n\s+MAXIMUM\s+(\d+)R?\s", text)
        when = issue_time(text)
        if day and high and when and not re.search(r"VALID (TODAY )?AS OF", text):
            rows.append((when[0], pd.Timestamp(day.group(1)), float(high.group(1))))
    return pd.DataFrame(rows, columns=COLUMNS)


def sft_highs(texts: list[str]) -> pd.DataFrame:
    """The daytime highs for Los Angeles Intl Airport in each Tabular State Forecast. Its seven
    columns are consecutive days ("Nov 02 Nov 03 ..."); a value is "low/high", "/high" or "MM"."""
    rows = []
    for text in texts:
        when = issue_time(text)
        dates = re.search(r"^\s+([A-Z][a-z]{2} \d\d)(?:\s+[A-Z][a-z]{2} \d\d){5,}\s*$", text, re.M)
        lines = text.splitlines()
        at = next((i for i, line in enumerate(lines) if line.strip() == SFT_ROW), None)
        if not (when and dates and at is not None and at + 2 < len(lines)):
            continue
        first = pd.Timestamp(f"{dates.group(1)} {when[1].year}")
        if first < when[1].normalize() - pd.Timedelta(days=2):  # issued Dec 31, starts Jan 1
            first += pd.DateOffset(years=1)
        values = lines[at + 2].split()
        if len(values) != len(dates.group(0).split()) // 2:
            continue
        days = pd.date_range(first, periods=len(values))
        highs = [value.split("/")[-1] for value in values]
        rows += [(when[0], d, float(h)) for d, h in zip(days, highs, strict=True) if h.isdigit()]
    return pd.DataFrame(rows, columns=COLUMNS)


def pfm_highs(texts: list[str]) -> pd.DataFrame:
    """The daily Max for Los Angeles Airport in each Point Forecast Matrices product. Each block
    has a date row ("Mon 11/02/26 ...", cut at the block's edges, e.g. "11/05/26" or "Thu") and
    a Max/Min (or Min/Max) row whose values alternate; a Max belongs to the date written nearest
    above it."""
    rows = []
    for text in texts:
        when = issue_time(text)
        if not when or PFM_POINT not in text:
            continue
        segment = text[text.index(PFM_POINT) :].split("$$")[0]
        for block in re.split(r"^(?=Date )", segment, flags=re.M)[1:]:
            lines = block.splitlines()
            labels = [
                (found.start() + 4, datetime.strptime(found.group(), "%m/%d/%y"))
                for found in re.finditer(r"\d\d/\d\d/\d\d", lines[0])
            ]
            row = next((line for line in lines if line.startswith(("Max/Min", "Min/Max"))), "")
            kinds = row[:7].split("/")
            for k, value in enumerate(re.finditer(r"\S+", row[7:])):
                if labels and kinds[k % 2] == "Max" and value.group().isdigit():
                    middle = 7 + (value.start() + value.end()) / 2
                    day = min(labels, key=lambda label: abs(label[0] - middle))[1]
                    rows.append((when[0], pd.Timestamp(day), float(value.group())))
    return pd.DataFrame(rows, columns=COLUMNS)


def archive(pil: str, parse: Callable[[list[str]], pd.DataFrame], first: date) -> pd.DataFrame:
    """parse() of every `pil` product issued from `first` to now. The parsed rows are kept in
    CACHE; each call fetches the products of the last two days again, in case IEM archived one
    late, and the newer ones."""
    path = CACHE / f"{pil}.csv"
    parts = [pd.read_csv(path, parse_dates=["issued", "day"])] if path.is_file() else []
    since = (parts[0]["issued"].max() - pd.Timedelta(days=2)).date() if parts else first
    parts.append(parse(products(pil, since, datetime.now(UTC).date() + timedelta(days=1))))
    rows = pd.concat(parts, ignore_index=True).drop_duplicates(["issued", "day"], keep="last")
    rows = rows.sort_values(["issued", "day"], kind="stable")
    path.parent.mkdir(parents=True, exist_ok=True)
    rows.to_csv(path, index=False)
    return rows.reset_index(drop=True)


def first_final(reports: pd.DataFrame, start: date) -> pd.Series:
    """Each day's maximum from its first final report that gives one."""
    first = reports.sort_values("issued", kind="stable").drop_duplicates("day")
    first = first.set_index("day")["high"]
    return first[first.index >= pd.Timestamp(start)].sort_index().rename_axis("date")


def nws_as_seen(
    forecasts: pd.DataFrame, index: pd.DatetimeIndex, run_date: date, spec: dict
) -> pd.Series:
    """The NWS forecast high of each day on `index` as a run could see it: from the latest issue
    before the snapshot of the day before, or of the run date for the days after it. So the
    history holds the forecasts one day ahead, and the forecast window the run date's latest
    issue. A past day no issue covered is filled in linearly."""
    snapshot, timezone = spec["schedule"]["snapshot"], spec["schedule"]["timezone"]
    as_of = pd.Series(
        [_at(min(day.date() - timedelta(days=1), run_date), snapshot, timezone) for day in index],
        index=index,
        name="as_of",
    )
    rows = forecasts.merge(as_of, left_on="day", right_index=True)
    rows = rows[rows["issued"] <= rows["as_of"]].sort_values("issued")
    seen = rows.groupby("day")["high"].last().reindex(index)
    past = index < pd.Timestamp(run_date)
    seen[past] = seen[past].interpolate(limit_area="inside")
    return seen


def snapshot(run_date: date, spec: dict) -> dict:
    """X_d as published right now: one row per day from history_start to the last target date,
    `target` empty after the last reported day, the NWS forecast and calendar for the window."""
    start = date.fromisoformat(spec["x"]["history_start"])
    horizon = max(spec["horizons"])
    reports = archive("CLILAX", climate_reports, start)
    reports = reports[
        (reports["day"] >= pd.Timestamp(start)) & (reports["day"] < pd.Timestamp(run_date))
    ]
    highs = first_final(reports, start)
    forecasts = archive("SFTLOX", sft_highs, start - timedelta(days=2))
    as_of = _at(run_date, spec["schedule"]["snapshot"], spec["schedule"]["timezone"])
    latest = forecasts[
        forecasts["issued"] == forecasts["issued"][forecasts["issued"] <= as_of].max()
    ]

    index = pd.date_range(start, run_date + timedelta(days=horizon), freq="D", name="timestamp")
    # A day without a report (3 in 2024-2026) is filled linearly; truth leaves it empty. The
    # pipeline's seasonal naive reads the last observed week of X_d.
    target = highs.reindex(index).interpolate(limit_area="inside").rename("target")
    nws = nws_as_seen(forecasts, index, run_date, spec).rename("nws")
    calendar = pd.DataFrame(
        {
            "dow": index.dayofweek,
            "doy_sin": [math.sin(2 * math.pi * (d.dayofyear - 1) / 365.2425) for d in index],
            "doy_cos": [math.cos(2 * math.pi * (d.dayofyear - 1) / 365.2425) for d in index],
        },
        index=index,
    )
    return {
        "series": pd.concat([target, nws, calendar], axis=1),
        "last_observed": highs.index.max().date(),
        "raw": {"climate_reports.csv": reports, "nws_forecast_latest.csv": latest},
        "sources": {
            "target": {"url": IEM_AFOS, "pil": "CLILAX"},
            "nws": {"url": IEM_AFOS, "pil": "SFTLOX", "latest_issued": str(latest["issued"].max())},
        },
        "covariate_notes": {
            "target": "daily maximum (°F) from the first final NWS climate report for LAX; a day "
            "without one is filled linearly from its neighbours",
            "nws": "NWS forecast daytime high (°F, 6 AM to 6 PM) for Los Angeles Intl Airport in "
            "the Tabular State Forecast: from the latest issue before the previous day's snapshot "
            "(one day ahead) up to run_date, from the latest before this snapshot after it",
            "dow": "weekday, Monday = 0",
            "doy_sin": "annual cycle, sine of the day of the year",
            "doy_cos": "annual cycle, cosine of the day of the year",
        },
    }


def truth(today: date, spec: dict) -> pd.Series:
    """Each day's maximum from its first final climate report, for every day reported so far."""
    start = date.fromisoformat(spec["x"]["history_start"])
    return first_final(archive("CLILAX", climate_reports, start), start)


def baseline(run_date: date, spec: dict) -> dict:
    """The NWS forecast the run is compared with: the first SFTLOX issued after the cutoff (the
    PFMLOX if no SFTLOX came out within a day of it), which the agents cannot see. Empty until it
    is issued; IEM keeps every issue, so a later call gives the same numbers."""
    cutoff = _at(run_date, spec["schedule"]["cutoff"], spec["schedule"]["timezone"])
    start = date.fromisoformat(spec["x"]["history_start"]) - timedelta(days=2)

    def after_cutoff(rows: pd.DataFrame) -> pd.DataFrame:
        return rows[(rows["issued"] > cutoff) & (rows["issued"] <= cutoff + pd.Timedelta(days=1))]

    report = "NWS Tabular State Forecast (SFTLOX), archived by IEM"
    rows = after_cutoff(archive("SFTLOX", sft_highs, start))
    if rows.empty:
        report = "NWS Point Forecast Matrices (PFMLOX), archived by IEM"
        rows = after_cutoff(pfm_highs(products("PFMLOX", run_date, run_date + timedelta(days=2))))
    if rows.empty:
        source = {"report": "no NWS forecast for LAX issued yet after the cutoff", "posted": None}
        return {"source": source | {"model": OFFICE}, "forecasts": []}
    posted = rows["issued"].min()
    highs = rows[rows["issued"] == posted].set_index("day")["high"]
    targets = [pd.Timestamp(run_date + timedelta(days=h)) for h in spec["horizons"]]
    return {
        "source": {"report": report, "posted": posted.isoformat(), "model": OFFICE},
        "forecasts": [
            {"target_date": t.date().isoformat(), "point": float(highs[t])}
            for t in targets
            if t in highs.index
        ],
    }
