"""Host-only InfoDengue Rio reported cases: X_d and delayed-settlement truth. There is no
public official or expert forecast of this series, so there is no B.

No credentials. casos_est is InfoDengue's nowcast of past weeks, never truth; X_d keeps it
in raw/ as published at the snapshot. Source documentation: https://info.dengue.mat.br/services/api
"""
from __future__ import annotations
import http.client
import json
import math
import sys
import time
from datetime import UTC, date, datetime, time as clock, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
import pandas as pd

API = "https://info.dengue.mat.br/api/alertcity"
DOCS = "https://info.dengue.mat.br/services/api"
ZONE = ZoneInfo("America/Sao_Paulo")
GEOCODE = 3304557
SETTLEMENT_LAG_DAYS = 28
MAX_LAG_DAYS = 21
FAILURES = (OSError, http.client.HTTPException, ValueError)


def _now():
    return datetime.now(ZONE)


def _cutoff(run_date, spec):
    if spec["schedule"]["timezone"] != "America/Sao_Paulo":
        raise ValueError("Rio runs require America/Sao_Paulo.")
    return datetime.combine(run_date, clock.fromisoformat(spec["schedule"]["cutoff"]), ZONE)


def _guard(run_date, spec):
    now = _now()
    if now.date() != run_date or now >= _cutoff(run_date, spec):
        raise RuntimeError("Fresh snapshots require the current local day before cutoff.")
    return now


def _get(url):
    for attempt in range(4):
        try:
            request = Request(url, headers={"Accept": "application/json",
                "User-Agent": "agentic-forecasting-dengue/1.0 (https://github.com/rowingchenn/agentic-forecasting-tasks)"})
            with urlopen(request, timeout=90) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise
            retry = exc.headers.get("Retry-After", "")
            delay = min(120, int(retry)) if retry.isdigit() else 2 ** attempt
        except (URLError, TimeoutError):
            if attempt == 3:
                raise
            delay = 2 ** attempt
        time.sleep(delay)
    raise AssertionError("unreachable")


def _weekly(today, spec):
    start = date.fromisoformat(spec["x"]["history_start"])
    if start < date(2024, 1, 7) or start.weekday() != 6:
        raise ValueError("History starts on a Sunday from 2024-01-07 onward.")
    records = {}
    urls = []
    # Use publisher year queries but key by its Sunday dates, not reconstructed week IDs.
    for year in range(start.year, today.year + 1):
        params = {"geocode": GEOCODE, "disease": "dengue", "format": "json",
                  "ew_start": 1, "ew_end": 53, "ey_start": year, "ey_end": year}
        url = f"{API}?{urlencode(params)}"
        rows = _get(url)
        urls.append(url)
        if not isinstance(rows, list):
            raise ValueError("Unexpected InfoDengue response.")
        for row in rows:
            if (not isinstance(row, dict) or row.get("municipio_nome") != "Rio de Janeiro"
                    or str(row.get("Localidade_id")) != "0"):
                raise ValueError("Wrong municipality/locality or malformed row.")
            stamp = row.get("data_iniSE")
            if isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or not math.isfinite(stamp):
                raise ValueError("Invalid source Sunday date.")
            # Epoch milliseconds encode the date at UTC midnight. Converting the instant
            # to Sao Paulo would wrongly turn Sunday into Saturday; retain the date label.
            moment = datetime.fromtimestamp(stamp / 1000, UTC)
            week = moment.date()
            if moment.time() != clock.min or week.weekday() != 6:
                raise ValueError("Source week label is not Sunday at encoded midnight.")
            if week < start or week + timedelta(days=6) >= today:
                continue  # Never use an unfinished week's notifications as a full-week input.
            value = row.get("casos")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("Missing or non-numeric reported casos; do not use a nowcast.")
            if not math.isfinite(value) or value < 0 or value != int(value):
                raise ValueError("Reported casos must be non-negative integers.")
            if week in records:
                raise ValueError("Duplicate source Sunday; review query/year boundaries.")
            records[week] = {"week_start": str(week), "SE": row.get("SE"), "casos": int(value),
                             "casos_est": row.get("casos_est"), "casos_est_min": row.get("casos_est_min"),
                             "casos_est_max": row.get("casos_est_max"),
                             "versao_modelo": row.get("versao_modelo")}
    if not records:
        raise ValueError("No completed reported-case history returned.")
    index = pd.date_range(start, max(records), freq="W-SUN", name="timestamp")
    if set(records) != set(index.date):
        raise ValueError("Missing reported weeks; do not interpolate or invent zeros.")
    history = pd.Series([records[d.date()]["casos"] for d in index], index=index, name="target", dtype=float)
    raw = pd.DataFrame([records[d.date()] for d in index])
    return history, raw, {"name": "InfoDengue / AlertaDengue", "documentation": DOCS,
                          "urls": urls, "geocode": GEOCODE, "disease": "dengue",
                          "field": "casos", "note": "Reported notifications (SINAN, via InfoDengue; Codeco et al. 2018); nowcasts are not truth."}


def snapshot(run_date: date, spec: dict) -> dict:
    as_of = _guard(run_date, spec)
    history, raw, source = _weekly(run_date, spec)
    last = history.index[-1].date()
    if last < run_date - timedelta(days=MAX_LAG_DAYS):
        raise RuntimeError("Latest published completed week is over 21 days old.")
    _guard(run_date, spec)
    targets = [run_date + timedelta(days=h) for h in spec["horizons"]]
    if any(d.weekday() != 6 for d in targets):
        raise ValueError("Targets must label Sunday week starts.")
    index = pd.date_range(spec["x"]["history_start"], max(targets), freq="W-SUN", name="timestamp")
    return {"series": pd.DataFrame({"target": history.reindex(index),
                                   "iso_week": index.isocalendar().week.to_numpy(dtype=int),
                                   "month": index.month}, index=index),
            "last_observed": last, "raw": {"infodengue_reported_weeks.csv": raw},
            "sources": {"reported_cases": source, "as_of": as_of.isoformat()},
            "covariate_notes": {"iso_week": "ISO week of source Sunday, not its SE identifier.",
                                "month": "Month of the Sunday week-start label."}}


def truth(today: date, spec: dict) -> pd.Series:
    """casos of every week that a release dated (versao_modelo) at least 28 days after its
    Saturday has rewritten; a week without a version date is not yet settled. The pipeline
    keeps the first value it sees, that of the first such release. If InfoDengue cannot be
    read, nothing is recorded today and a warning is printed, so that the day's run goes on."""
    if today > _now().date():
        raise RuntimeError("Cannot settle future dates.")
    try:
        history, raw, _ = _weekly(today, spec)
    except FAILURES as exc:
        print(f"warning: InfoDengue not read, no truth recorded today: {exc!r}", file=sys.stderr)
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    release = pd.to_datetime(raw["versao_modelo"], errors="coerce").to_numpy()  # missing: NaT
    return history[release >= history.index + pd.Timedelta(days=6 + SETTLEMENT_LAG_DAYS)]


def baseline(run_date: date, spec: dict) -> dict:
    return {"source": {"name": "none: no official or expert forecast of this series"}, "forecasts": []}
