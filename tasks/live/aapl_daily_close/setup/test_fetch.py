"""Offline contract/regression checks: python -m unittest discover -s setup -v."""

import importlib.util
import os
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import yaml

HERE = Path(__file__).resolve().parent
loader = importlib.util.spec_from_file_location("aapl_fetch", HERE / "fetch.py")
fetch = importlib.util.module_from_spec(loader)
loader.loader.exec_module(fetch)
manifest = yaml.safe_load((HERE.parent / "task.yaml").read_text())
SPEC = manifest["extras"]["agentic-forecasting"] | {
    "horizons": manifest["params"]["horizons"],
    "schedule": manifest["extras"]["agentic-forecasting"]["schedule"]
    | {"cutoff": manifest["params"]["cutoff"]},
}
DAY = date(2026, 10, 26)
NOW = datetime(2026, 10, 26, 7, 45, tzinfo=fetch.ZONE)


def chart(start, through):
    dates = fetch._sessions(start - timedelta(days=14), through)
    n = len(dates)
    stamps = [
        int(
            datetime.combine(d.date(), datetime.min.time(), fetch.ZONE)
            .replace(hour=9, minute=30)
            .timestamp()
        )
        for d in dates
    ]
    return {
        "chart": {
            "error": None,
            "result": [
                {
                    "meta": {"symbol": "AAPL"},
                    "timestamp": stamps,
                    "indicators": {
                        "quote": [
                            {
                                key: [200.0] * n
                                for key in ["open", "high", "low", "close", "volume"]
                            }
                        ],
                        "adjclose": [{"adjclose": [190.0] * n}],
                    },
                }
            ],
        }
    }


class FetchTests(unittest.TestCase):
    def setUp(self):
        fetch._prices.cache_clear()

    def test_exchange_holidays_and_special_closure(self):
        for day in [
            "2024-03-29",
            "2024-07-04",
            "2025-01-09",
            "2026-06-19",
            "2026-12-25",
        ]:
            self.assertEqual(len(fetch._sessions(day, day)), 0, day)
        self.assertEqual(len(fetch._sessions("2027-12-31", "2027-12-31")), 1)

    def test_daily_forward_fill_and_no_adjusted_close(self):
        start = date(2026, 10, 19)
        with patch.object(
            fetch, "_get", return_value=chart(start, DAY - timedelta(days=1))
        ):
            daily = fetch._daily(start, DAY - timedelta(days=1))
        self.assertEqual(len(daily), 7)
        self.assertTrue((daily == 200).all())
        self.assertEqual(daily.index.name, "timestamp")

    def test_missing_session_is_not_silently_filled(self):
        start = date(2026, 10, 19)
        payload = chart(start, date(2026, 10, 23))
        item = payload["chart"]["result"][0]
        item["timestamp"].pop()
        for values in item["indicators"]["quote"][0].values():
            values.pop()
        item["indicators"]["adjclose"][0]["adjclose"].pop()
        with patch.object(fetch, "_get", return_value=payload):
            with self.assertRaisesRegex(RuntimeError, "missing expected sessions"):
                fetch._prices(start.isoformat(), "2026-10-23")

    def test_duplicate_and_invalid_prices_rejected(self):
        for defect in ["duplicate", "nan", "negative"]:
            fetch._prices.cache_clear()
            payload = chart(date(2026, 10, 19), date(2026, 10, 23))
            item = payload["chart"]["result"][0]
            if defect == "duplicate":
                item["timestamp"][-1] = item["timestamp"][-2]
            else:
                item["indicators"]["quote"][0]["close"][-1] = (
                    float("nan") if defect == "nan" else -1
                )
            with patch.object(fetch, "_get", return_value=payload):
                with self.assertRaises(RuntimeError):
                    fetch._prices("2026-10-19", "2026-10-23")

    def test_guard_rejects_historical_and_late_snapshots(self):
        for now in [NOW + timedelta(days=1), NOW.replace(hour=9, minute=25)]:
            with patch.object(fetch, "_now", return_value=now):
                with self.assertRaises(RuntimeError):
                    fetch._guard(DAY, SPEC)

    def test_news_filters_future_and_old_and_deduplicates(self):
        def article(i, dt):
            return {
                "id": i,
                "datetime": int(dt.timestamp()),
                "headline": "Headline",
                "summary": "Summary",
                "source": "Publisher",
                "url": f"https://example.com/{i}",
            }

        items = [
            article(1, NOW - timedelta(hours=1)),
            article(1, NOW - timedelta(hours=1)),
            article(2, NOW + timedelta(seconds=1)),
            article(3, NOW - timedelta(days=8)),
        ]
        with (
            patch.dict(os.environ, {"FINNHUB_API_KEY": "test-only"}),
            patch.object(fetch, "_get", return_value=items),
            patch.object(fetch.time, "sleep"),
        ):
            news = fetch._news(DAY, NOW, 7)
        self.assertEqual(list(news["id"]), ["1"])
        self.assertEqual(list(news.columns), fetch.NEWS_COLUMNS)

    def test_missing_key_and_error_response_are_explicit(self):
        with patch.dict(os.environ, {"FINNHUB_API_KEY": ""}):
            with self.assertRaisesRegex(RuntimeError, "FINNHUB_API_KEY"):
                fetch._news(DAY, NOW, 7)
        with (
            patch.dict(os.environ, {"FINNHUB_API_KEY": "test-only"}),
            patch.object(fetch, "_get", return_value={"error": "plan"}),
        ):
            with self.assertRaisesRegex(RuntimeError, "invalid company-news"):
                fetch._news(DAY, NOW, 7)

    def test_snapshot_contract_and_shared_baseline_prices(self):
        start = date.fromisoformat(SPEC["x"]["history_start"])
        prices = chart(start, DAY - timedelta(days=1))
        with (
            patch.object(fetch, "_now", return_value=NOW),
            patch.dict(os.environ, {"FINNHUB_API_KEY": "test-only"}),
            patch.object(fetch, "_get", return_value=prices) as get,
            patch.object(
                fetch, "_news", return_value=pd.DataFrame(columns=fetch.NEWS_COLUMNS)
            ),
        ):
            result = fetch.snapshot(DAY, SPEC)
            baseline = fetch.baseline(DAY, SPEC)
        get.assert_called_once()
        series = result["series"]
        self.assertEqual(result["last_observed"], date(2026, 10, 25))
        self.assertTrue(series.loc["2026-10-26":, "target"].isna().all())
        self.assertFalse(series[["dow", "month"]].isna().any().any())
        self.assertEqual(len(baseline["forecasts"]), 7)
        self.assertTrue(all(f["point"] == 200 for f in baseline["forecasts"]))
        self.assertEqual(set(result["raw"]), {"prices.csv", "news.csv"})


if __name__ == "__main__":
    unittest.main()
