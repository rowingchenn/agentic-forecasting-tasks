"""Offline regression checks for source ambiguity, truncation and timing."""

import io
import json
import unittest
from datetime import date, datetime
from unittest.mock import patch

import fetch
import pandas as pd


def row(**updates):
    return {
        "type": "Breast, Boneless/Skinless",
        "package_size": "Value",
        "region": "NATIONAL",
        "commodity": "Chicken",
        "slug_id": 2756,
        "price_unit": "Per lb",
        "price_avg": 2.5,
        "store_count": 100,
        "section": "Parts",
        "condition": "Fresh",
        "environment": "Conventional",
        "report_begin_date": "10/31/2026",
        "report_end_date": "11/06/2026",
    } | updates


class FetchChecks(unittest.TestCase):
    def test_exact_duplicates_only(self):
        record = row()
        self.assertEqual(len(fetch._select([record, record], date(2026, 11, 6))), 1)
        with self.assertRaises(ValueError):
            fetch._select([record, row(price_avg=2.6)], date(2026, 11, 6))

    def test_product_and_units(self):
        for changes in ({"package_size": "Regular"}, {"type": "Tenders"}):
            self.assertFalse(fetch._select([row(**changes)], date(2026, 11, 6)))
        for changes in ({"price_unit": "Per kg"}, {"price_avg": 0}, {"store_count": 0}):
            with self.assertRaises(ValueError):
                fetch._select([row(**changes)], date(2026, 11, 6))
        legacy = row(type="BREAST, BONELESS/SKINLESS, VALUE PACK", package_size=None)
        self.assertEqual(
            fetch._select([legacy], date(2026, 11, 6)),
            fetch._select([row()], date(2026, 11, 6)),
        )

    def test_truncation_is_split(self):
        def payload(total, rows):
            return io.BytesIO(
                json.dumps(
                    {
                        "stats": {"totalRows": total, "returnedRows": len(rows)},
                        "results": rows,
                    }
                ).encode()
            )

        with patch.object(
            fetch,
            "urlopen",
            side_effect=[payload(2, [row()]), payload(1, [row()]), payload(0, [])],
        ) as request:
            records, urls = fetch._get(date(2026, 10, 31), date(2026, 11, 1))
        self.assertEqual(len(records), 1)
        self.assertEqual(len(urls), 2)
        self.assertEqual(request.call_count, 3)
        with (
            patch.object(fetch, "urlopen", return_value=payload(2, [row()])),
            self.assertRaises(ValueError),
        ):
            fetch._get(date(2026, 10, 31), date(2026, 10, 31))

    def test_calendar_and_reference(self):
        self.assertEqual(fetch._label(date(2026, 11, 5)), date(2026, 11, 6))
        self.assertFalse(fetch._select([row()], date(2026, 11, 5)))
        s = pd.Series(
            [1.0, 2.0, 3.0, 4.0],
            index=pd.date_range("2026-10-16", periods=4, freq="W-FRI"),
        )
        self.assertEqual(fetch.reference_forecast(s), 2.5)
        with self.assertRaises(ValueError):
            fetch.reference_forecast(s.drop(s.index[1]))

    def test_time_guard(self):
        spec = {
            "horizons": [7],
            "x": {"history_start": "2021-01-01"},
            "schedule": {"timezone": "America/New_York", "cutoff": "15:50"},
        }
        for hour, minute in [(13, 59), (15, 50)]:
            with (
                patch.object(
                    fetch,
                    "_now",
                    return_value=datetime(2026, 11, 6, hour, minute, tzinfo=fetch.ZONE),
                ),
                self.assertRaises(RuntimeError),
            ):
                fetch._guard(date(2026, 11, 6), spec)
        with patch.object(
            fetch, "_now", return_value=datetime(2026, 11, 6, 14, 0, tzinfo=fetch.ZONE)
        ):
            fetch._guard(date(2026, 11, 6), spec)


if __name__ == "__main__":
    unittest.main()
