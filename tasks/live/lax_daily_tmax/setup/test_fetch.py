"""Offline tests; no API keys or network requests. Run unittest discover -s setup -v."""

import copy
import importlib.util
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

HERE = Path(__file__).resolve().parent
loader = importlib.util.spec_from_file_location("lax_fetch", HERE / "fetch.py")
fetch = importlib.util.module_from_spec(loader)
loader.loader.exec_module(fetch)
MANIFEST = yaml.safe_load((HERE.parent / "task.yaml").read_text())
SPEC = MANIFEST["extras"]["agentic-forecasting"] | {
    "horizons": MANIFEST["params"]["horizons"],
    "schedule": MANIFEST["extras"]["agentic-forecasting"]["schedule"]
    | {
        "timezone": MANIFEST["params"]["timezone"],
        "cutoff": MANIFEST["params"]["cutoff"],
    },
}
DAY = date(2026, 10, 29)
NOW = datetime(2026, 10, 29, 7, 30, tzinfo=ZoneInfo("America/Los_Angeles"))
VARIABLE = SPEC["x"]["variable"]


def response(start, end, temperatures=None):
    dates = pd.date_range(start, end)
    return {
        "latitude": 34.0,
        "longitude": -118.5,
        "elevation": 38,
        "timezone": SPEC["schedule"]["timezone"],
        "daily_units": {"time": "iso8601", VARIABLE: "°C"},
        "daily": {
            "time": list(dates.strftime("%Y-%m-%d")),
            VARIABLE: temperatures if temperatures is not None else [20.0] * len(dates),
        },
    }


def fake_get(url):
    params = parse_qs(urlsplit(url).query)
    return response(params["start_date"][0], params["end_date"][0])


class FetchTests(unittest.TestCase):
    def test_snapshot_and_baseline_contract(self):
        with (
            patch.object(fetch, "_now", return_value=NOW),
            patch.object(fetch, "_get", side_effect=fake_get) as get,
        ):
            snapshot = fetch.snapshot(DAY, SPEC)
            baseline = fetch.baseline(DAY, SPEC)
        series = snapshot["series"]
        self.assertEqual(snapshot["last_observed"], DAY - timedelta(days=6))
        self.assertAlmostEqual(series.loc["2026-10-23", "target"], 293.15)
        self.assertTrue(series.loc["2026-10-24":, "target"].isna().all())
        self.assertFalse(series.drop(columns="target").isna().any().any())
        self.assertEqual(series.index.name, "timestamp")
        self.assertEqual(len(baseline["forecasts"]), 7)
        self.assertEqual(baseline["forecasts"][0]["target_date"], "2026-10-30")
        self.assertEqual(baseline["forecasts"][-1]["target_date"], "2026-11-05")
        self.assertTrue(
            all(abs(f["point"] - 293.15) < 1e-9 for f in baseline["forecasts"])
        )
        self.assertEqual(
            set(snapshot["raw"]),
            {"historical_daily.csv", "history_response.csv", "baseline_response.csv"},
        )
        params = [parse_qs(urlsplit(c.args[0]).query) for c in get.call_args_list]
        self.assertEqual({p["models"][0] for p in params}, {"era5", "ecmwf_ifs025"})
        for p in params:
            self.assertEqual(p["elevation"], ["38"])
            self.assertEqual(p["temperature_unit"], ["celsius"])

    def test_negative_celsius_is_valid_kelvin(self):
        parsed = fetch._parse(response(DAY, DAY, [-5.0]), DAY, DAY, SPEC)
        self.assertAlmostEqual(parsed["target"].iloc[0], 268.15)

    def test_trailing_archive_missing_shortens_history(self):
        def delayed(url):
            result = fake_get(url)
            result["daily"][VARIABLE][-2:] = [None, None]
            return result

        with (
            patch.object(fetch, "_now", return_value=NOW),
            patch.object(fetch, "_get", side_effect=delayed),
        ):
            history, raw, source = fetch._history(DAY, SPEC)
        self.assertEqual(history.index[-1].date(), DAY - timedelta(days=8))
        self.assertEqual(raw["target"].isna().sum(), 2)
        self.assertIn("response", source)

    def test_internal_missing_or_all_missing_archive_fails(self):
        for values in [[20.0, None, 21.0], [None, None, None]]:
            with self.assertRaises(RuntimeError):
                fetch._parse(
                    response(DAY, DAY + timedelta(days=2), values),
                    DAY,
                    DAY + timedelta(days=2),
                    SPEC,
                    allow_trailing_missing=True,
                )

    def test_invalid_temperatures_rejected(self):
        for value in [None, True, float("nan"), float("inf"), -273.15, -300.0, "20"]:
            with self.assertRaises(RuntimeError):
                fetch._parse(response(DAY, DAY, [value]), DAY, DAY, SPEC)

    def test_dates_units_timezone_and_lengths_rejected(self):
        original = response(DAY, DAY + timedelta(days=1))
        for defect in ["timezone", "units", "duplicate", "reordered", "length"]:
            payload = copy.deepcopy(original)
            if defect == "timezone":
                payload["timezone"] = "UTC"
            if defect == "units":
                payload["daily_units"][VARIABLE] = "°F"
            if defect == "duplicate":
                payload["daily"]["time"][1] = payload["daily"]["time"][0]
            if defect == "reordered":
                payload["daily"]["time"].reverse()
            if defect == "length":
                payload["daily"][VARIABLE].pop()
            with self.assertRaises(RuntimeError):
                fetch._parse(payload, DAY, DAY + timedelta(days=1), SPEC)

    def test_late_historical_and_future_runs_rejected(self):
        with patch.object(fetch, "_now", return_value=NOW):
            for day in [DAY - timedelta(days=1), DAY + timedelta(days=1)]:
                with self.assertRaises(RuntimeError):
                    fetch._guard(day, SPEC)
            with self.assertRaises(RuntimeError):
                fetch.truth(DAY + timedelta(days=1), SPEC)
        with patch.object(fetch, "_now", return_value=NOW.replace(hour=10)):
            with self.assertRaises(RuntimeError):
                fetch._guard(DAY, SPEC)

    def test_slow_baseline_crossing_cutoff_rejected(self):
        with (
            patch.object(fetch, "_now", side_effect=[NOW, NOW.replace(hour=10)]),
            patch.object(fetch, "_get", side_effect=fake_get),
        ):
            with self.assertRaises(RuntimeError):
                fetch.baseline(DAY, SPEC)

    def test_missing_forecast_day_rejected(self):
        def missing(url):
            payload = fake_get(url)
            payload["daily"][VARIABLE][-1] = None
            return payload

        with (
            patch.object(fetch, "_now", return_value=NOW),
            patch.object(fetch, "_get", side_effect=missing),
        ):
            with self.assertRaises(RuntimeError):
                fetch.baseline(DAY, SPEC)

    def test_calendar_dates_across_dst_end(self):
        with (
            patch.object(fetch, "_now", return_value=NOW),
            patch.object(fetch, "_get", side_effect=fake_get),
        ):
            forecasts = fetch.baseline(DAY, SPEC)["forecasts"]
        self.assertEqual(
            [r["target_date"] for r in forecasts],
            [(DAY + timedelta(days=i)).isoformat() for i in range(1, 8)],
        )


if __name__ == "__main__":
    unittest.main()
