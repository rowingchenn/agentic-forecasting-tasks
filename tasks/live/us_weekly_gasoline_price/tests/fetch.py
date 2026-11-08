"""EIA's weekly national regular gasoline survey price; host-only, no credentials.

Use the public history HTML so the host needs only stdlib and pandas (no Excel
engine or EIA API key). Values are Monday observations, not weekly averages.
"""

from __future__ import annotations

import calendar
import math
import re
import time
from datetime import date, datetime, timedelta
from datetime import time as clock
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd

ZONE = ZoneInfo("America/New_York")
HISTORY_START = date(2024, 1, 1)
SERIES = "EMM_EPMR_PTE_NUS_DPG"
HISTORY = f"https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?n=PET&s={SERIES}&f=W"
LABEL = "Weekly U.S. Regular All Formulations Retail Gasoline Prices"
METHOD = "https://www.eia.gov/petroleum/gasdiesel/gas_proc-methods.php"
SCHEDULE = "https://www.eia.gov/petroleum/gasdiesel/schedule.php"
USER_AGENT = (
    "agentic-forecasting-gasoline/1.0 "
    "(https://github.com/rowingchenn/agentic-forecasting-tasks)"
)
MONTHS = {name: number for number, name in enumerate(calendar.month_abbr) if name}


class _HistoryTable(HTMLParser):
    """Collect table cells, including text split over nested formatting tags."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.row = None
        self.cell = None

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
            self.row = None
            self.cell = None


def _now():
    return datetime.now(ZONE)


def _guard(run_date, spec):
    now = _now()
    if spec["schedule"]["timezone"] != ZONE.key or run_date.weekday() != 3:
        raise ValueError("Gasoline runs are Thursdays in America/New_York.")
    if spec["horizons"] != [4]:
        raise ValueError("Thursday runs forecast only the next Monday (horizon 4).")
    cutoff = datetime.combine(
        run_date, clock.fromisoformat(spec["schedule"]["cutoff"]), ZONE
    )
    if now.date() != run_date or now >= cutoff:
        raise RuntimeError(
            "Fresh snapshots require the current local Thursday before cutoff."
        )
    return now


def _get():
    for attempt in range(4):
        try:
            with urlopen(
                Request(HISTORY, headers={"User-Agent": USER_AGENT}), timeout=60
            ) as response:
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


def _parse(text, start, as_of):
    if start < HISTORY_START or start.weekday() != 0 or start > as_of:
        raise ValueError(
            "Require a nonempty Monday-based history from January 2024 or later."
        )
    parser = _HistoryTable()
    parser.feed(text)
    parser.close()
    if LABEL not in " ".join(re.sub("<[^>]+>", " ", text).split()):
        raise ValueError("Unexpected EIA price series or frequency.")
    values = {}
    for row in parser.rows:
        if not row or not re.fullmatch(r"\d{4}-[A-Za-z]{3}", row[0]):
            continue
        year, name = row[0].split("-")
        if name not in MONTHS or len(row) != 11:
            raise ValueError("EIA monthly history table layout changed.")
        year, month = int(year), MONTHS[name]
        for stamp, price in zip(row[1::2], row[2::2], strict=True):
            if not stamp:
                if price:
                    raise ValueError("Price has no observation date.")
                continue
            if not re.fullmatch(r"\d{2}/\d{2}", stamp):
                raise ValueError("Invalid observation date.")
            mm, dd = map(int, stamp.split("/"))
            day = date(year, mm, dd)
            if mm != month or day.weekday() != 0:
                raise ValueError("Expected a Monday observation in the labelled month.")
            if day < start or day > as_of:
                continue
            if day in values:
                raise ValueError(
                    "Duplicate weekly observation; do not choose a revision."
                )
            if price in ("", "--", "NA"):
                values[day] = float("nan")
                continue
            try:
                number = Decimal(price)
            except InvalidOperation as exc:
                raise ValueError("Invalid gasoline price.") from exc
            if (
                not number.is_finite()
                or number <= 0
                or number != number.quantize(Decimal("0.001"))
            ):
                raise ValueError(
                    "Expected a positive price with at most three decimal places."
                )
            values[day] = float(number)
    if not values or not any(math.isfinite(v) for v in values.values()):
        raise ValueError("No published gasoline observations returned.")
    index = pd.date_range(start, max(values), freq="W-MON", name="timestamp")
    history = pd.Series(
        [values.get(d.date(), float("nan")) for d in index],
        index=index,
        name="target",
        dtype=float,
    )
    raw = pd.DataFrame(
        {
            "date": index.strftime("%Y-%m-%d"),
            "series": SERIES,
            "price_usd_per_gallon": history.to_numpy(),
        }
    )
    return history, raw


def _history(start, as_of):
    return _parse(_get(), start, as_of)


def snapshot(run_date: date, spec: dict) -> dict:
    as_of = _guard(run_date, spec)
    start = date.fromisoformat(spec["x"]["history_start"])
    history, raw = _history(start, run_date)
    last = history.last_valid_index().date()
    latest_monday = run_date - timedelta(days=3)
    if last != latest_monday:
        raise RuntimeError(
            "This week's Monday price has not been published; skip a stale run."
        )
    _guard(run_date, spec)
    index = pd.date_range(
        start, run_date + timedelta(days=4), freq="W-MON", name="timestamp"
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
        "raw": {"eia_weekly_gasoline_prices.csv": raw},
        "sources": {
            "gasoline": {
                "name": "U.S. Energy Information Administration",
                "url": HISTORY,
                "series": SERIES,
                "methodology": METHOD,
                "release_schedule": SCHEDULE,
                "data_use": "https://www.eia.gov/about/copyrights_reuse.php",
            },
            "as_of": as_of.isoformat(),
        },
        "covariate_notes": {
            "iso_week": "ISO week number of the Monday observation date.",
            "month": "Calendar month of the Monday observation date.",
        },
    }


def truth(today: date, spec: dict) -> pd.Series:
    if today > _now().date():
        raise RuntimeError("Cannot settle future observations.")
    # The pipeline retains the first recorded value. Never insert a NaN settlement,
    # which update_truth would otherwise prevent a later published value replacing.
    return _history(date.fromisoformat(spec["x"]["history_start"]), today)[0].dropna()


def baseline(run_date: date, spec: dict) -> dict:
    return {
        "source": {"report": "No matching public weekly expert forecast selected"},
        "forecasts": [],
    }
