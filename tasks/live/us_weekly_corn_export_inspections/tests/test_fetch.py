"""Offline regression checks for source completeness, date labels and release lag."""

import unittest
from datetime import date, datetime
from unittest.mock import patch

import fetch
import pandas as pd


def row(day="2024-01-04T00:00:00", **updates):
    return {
        "date": day,
        "target": "100",
        "records": "2",
        "valid_records": "2",
        "min_mt": "40",
    } | updates


def stats(**updates):
    return [
        {"records": "2", "weeks": "1", "valid_records": "2", "min_mt": "40"} | updates
    ]


class SourceChecks(unittest.TestCase):
    def test_schema_and_changed_update_version(self):
        metadata = {
            "name": "Grain Inspections",
            "rowsUpdatedAt": 1,
            "columns": [
                {"fieldName": field, "dataTypeName": kind, "name": label}
                for field, kind, label in [
                    ("date", "calendar_date", "Week Ending Date"),
                    ("grain", "text", "Grain"),
                    ("mt", "number", "MT"),
                ]
            ],
        }
        fetch._schema(metadata)
        with (
            patch.object(
                fetch,
                "_get",
                side_effect=[
                    metadata,
                    [row()],
                    stats(),
                    metadata | {"rowsUpdatedAt": 2},
                ],
            ),
            self.assertRaises(RuntimeError),
        ):
            fetch._history(date(2024, 1, 4))
        metadata["columns"][-1]["name"] = "Pounds"
        with self.assertRaises(ValueError):
            fetch._schema(metadata)

    def test_whole_week_and_metric_tons(self):
        s = fetch._parse([row(target="1092362")], stats(), date(2024, 1, 4))
        self.assertEqual(s.iloc[0], 1092362)
        self.assertEqual(s.index[0], pd.Timestamp("2024-01-04"))
        self.assertEqual(
            fetch._parse(
                [row(target="0", min_mt="0")], stats(min_mt="0"), date(2024, 1, 4)
            ).iloc[0],
            0,
        )

    def test_truncation_and_record_counts(self):
        for total in [stats(weeks="2"), stats(records="3"), stats(valid_records="1")]:
            with self.assertRaises(ValueError):
                fetch._parse([row()], total, date(2024, 1, 4))

    def test_invalid_amounts(self):
        for amount in ["-1", "NaN", "Infinity", "1.5", None, True]:
            with self.assertRaises(ValueError):
                fetch._integer(amount)
        with self.assertRaises(ValueError):
            fetch._parse([row(valid_records="1")], stats(), date(2024, 1, 4))

    def test_dates_and_duplicates(self):
        for stamp in [
            "2024-01-05T00:00:00",
            "2024-01-04T12:00:00",
            "2024-01-11T00:00:00",
        ]:
            with self.assertRaises(ValueError):
                fetch._parse([row(stamp)], stats(), date(2024, 1, 4))
        with self.assertRaises(ValueError):
            fetch._parse(
                [row(), row()],
                stats(records="4", weeks="2", valid_records="4"),
                date(2024, 1, 4),
            )

    def test_missing_weeks_stay_missing(self):
        series = fetch._parse([row()], stats(), date(2024, 1, 11))
        self.assertTrue(pd.isna(series.iloc[-1]))

    def test_truth_waits_until_monday(self):
        for today, expected in [
            (date(2026, 11, 8), date(2026, 10, 29)),
            (date(2026, 11, 9), date(2026, 11, 5)),
        ]:
            with (
                patch.object(
                    fetch,
                    "_now",
                    return_value=datetime(2026, 11, 10, tzinfo=fetch.ZONE),
                ),
                patch.object(
                    fetch, "_history", return_value=(pd.Series(dtype=float), None, None)
                ) as history,
            ):
                fetch.truth(today, {})
                history.assert_called_once_with(expected)

    def test_clock_and_reference(self):
        spec = {
            "horizons": [7],
            "schedule": {"timezone": fetch.ZONE.key, "cutoff": "15:50"},
            "x": {"history_start": "2024-01-04"},
        }
        with patch.object(
            fetch, "_now", return_value=datetime(2026, 11, 5, 14, 0, tzinfo=fetch.ZONE)
        ):
            fetch._guard(date(2026, 11, 5), spec)
        with (
            patch.object(
                fetch,
                "_now",
                return_value=datetime(2026, 11, 5, 15, 50, tzinfo=fetch.ZONE),
            ),
            self.assertRaises(RuntimeError),
        ):
            fetch._guard(date(2026, 11, 5), spec)
        s = pd.Series(
            [100.0, 200.0, 300.0, 400.0, float("nan"), float("nan")],
            index=pd.date_range("2026-10-08", periods=6, freq="W-THU"),
        )
        self.assertEqual(fetch.reference_forecast(s), 250)
        with self.assertRaises(ValueError):
            fetch.reference_forecast(s.drop(s.index[1]))


if __name__ == "__main__":
    unittest.main()
