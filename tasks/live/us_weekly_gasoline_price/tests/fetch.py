"""EIA's weekly US regular gasoline retail price, series EMM_EPMR_PTE_NUS_DPG: X_d and
first-published truth, both read from the series' history page on eia.gov, which needs no
key. There is no public expert forecast of the weekly price, so there is no B.

The page lists every Monday since 1990 as EIA publishes it now. EIA does not revise the
series in practice (27 archived copies of the page from 2018 to 2026 all match today's
values), and truth reads the page every day, so the value recorded for a Monday is the one
of its release day.
"""

from __future__ import annotations

import calendar
import math
import re
import time
from datetime import date, datetime, timedelta
from datetime import time as clock
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd

ZONE = ZoneInfo("America/New_York")
SERIES = "EMM_EPMR_PTE_NUS_DPG"
URL = f"https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?n=PET&s={SERIES}&f=W"
LABEL = "Weekly U.S. Regular All Formulations Retail Gasoline Prices"
USER_AGENT = "agentic-forecasting/1.0"
MONTHS = {name: number for number, name in enumerate(calendar.month_abbr) if name}


class _Table(HTMLParser):
    """The text of every table row, one string per cell."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.row, self.cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []

    def handle_data(self, text):
        if self.cell is not None:
            self.cell.append(text)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = self.cell = None


def _now():
    return datetime.now(ZONE)


def _guard(run_date, spec):
    if spec["schedule"]["timezone"] != ZONE.key:
        raise ValueError("EIA's survey and release times are in America/New_York.")
    now = _now()
    cutoff = datetime.combine(run_date, clock.fromisoformat(spec["schedule"]["cutoff"]), ZONE)
    if run_date.weekday() != 3 or now.date() != run_date or now >= cutoff:
        raise RuntimeError("Fresh snapshots require the current Thursday before the cutoff.")
    return now


def _get():
    for attempt in range(4):
        try:
            with urlopen(Request(URL, headers={"User-Agent": USER_AGENT}), timeout=60) as response:
                return response.read().decode("utf-8-sig")
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


def _prices(start, today):
    """Every Monday from start through today as the page lists it now (NaN where EIA shows
    no value), and the date of the release the page reflects."""
    if start.weekday() != 0:
        raise ValueError("history_start must be a Monday.")
    text = _get()
    plain = " ".join(re.sub("<[^>]+>", " ", text).split())
    if LABEL not in plain:
        raise ValueError("EIA's page no longer shows the expected series.")
    release = re.search(r"(?<!Next )Release Date: (\d{1,2}/\d{1,2}/\d{4})", plain)
    table = _Table()
    table.feed(text)
    values = {}
    # one row per month, "2026-Nov", then up to five pairs of "MM/DD" and price
    for row in table.rows:
        if not row or not re.fullmatch(r"\d{4}-[A-Z][a-z]{2}", row[0]):
            continue
        year, name = row[0].split("-")
        if name not in MONTHS or len(row) != 11:
            raise ValueError("EIA's monthly table layout changed.")
        for stamp, price in zip(row[1::2], row[2::2], strict=True):
            if not stamp:
                continue
            month, day = map(int, stamp.split("/"))
            d = date(int(year), month, day)
            if month != MONTHS[name] or d.weekday() != 0:
                raise ValueError(f"Unexpected survey date {d}: EIA's dates are Mondays.")
            if not start <= d <= today:
                continue
            if d in values:
                raise ValueError(f"EIA lists {d} twice.")
            # "--" and anything that is not a positive price count as not published
            number = float(price) if re.fullmatch(r"\d+(\.\d+)?", price) else math.nan
            values[d] = number if number > 0 else math.nan
    if not values or all(math.isnan(v) for v in values.values()):
        raise ValueError("EIA's page lists no prices in the requested range.")
    index = pd.date_range(start, max(values), freq="W-MON", name="timestamp")
    history = pd.Series([values.get(d.date(), math.nan) for d in index], index=index,
                        name="target", dtype=float)
    return history, release.group(1) if release else None


def snapshot(run_date: date, spec: dict) -> dict:
    as_of = _guard(run_date, spec)
    start = date.fromisoformat(spec["x"]["history_start"])
    history, release = _prices(start, run_date)
    last = history.last_valid_index().date()
    # EIA publishes a Monday's price on the Tuesday, or the Wednesday after a Monday
    # holiday, so a Thursday run normally has the Monday three days before it. A late
    # release leaves that week empty; a run day is refused only when two weeks are missing.
    if last < run_date - timedelta(days=10):
        raise RuntimeError(f"EIA's latest published price is for {last}; two releases are missing.")
    _guard(run_date, spec)
    targets = [run_date + timedelta(days=h) for h in spec["horizons"]]
    if any(d.weekday() != 0 for d in targets):
        raise ValueError("Target dates must be Mondays.")
    index = pd.date_range(start, max(targets), freq="W-MON", name="timestamp")
    series = pd.DataFrame(
        {
            "target": history.reindex(index),
            "iso_week": index.isocalendar().week.to_numpy(dtype=int),
            "month": index.month,
        },
        index=index,
    )
    raw = pd.DataFrame({"date": history.index.strftime("%Y-%m-%d"),
                        "usd_per_gallon": history.to_numpy()})
    return {
        "series": series,
        "last_observed": last,
        "raw": {"eia_weekly_regular_gasoline.csv": raw},
        "sources": {
            "gasoline": {"name": "US Energy Information Administration", "series": SERIES,
                         "url": URL, "release_date": release},
            "as_of": as_of.isoformat(),
        },
        "covariate_notes": {
            "iso_week": "ISO week number of the Monday; may be 53.",
            "month": "Month of the Monday.",
        },
    }


def truth(today: date, spec: dict) -> pd.Series:
    if today > _now().date():
        raise RuntimeError("Cannot settle future dates.")
    # update_truth keeps the first value recorded for each Monday; a Monday EIA shows no
    # value for is left out, so its later release can still settle it.
    return _prices(date.fromisoformat(spec["x"]["history_start"]), today)[0].dropna()


def baseline(run_date: date, spec: dict) -> dict:
    return {"source": {"name": "none: no public expert forecast of this series"}, "forecasts": []}
