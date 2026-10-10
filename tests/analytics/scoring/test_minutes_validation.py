"""Regression tests for minute source fallback and input validation."""

import unittest

import pandas as pd

from analytics.scoring.minutes_filter import _get_reliable_minutes, _classify


class MinuteValidationTests(unittest.TestCase):
    def test_invalid_primary_source_falls_back(self):
        row = pd.Series({
            "fm_min": "unknown",
            "fbref_minutes": "540",
            "csv_min": 900,
        })
        self.assertEqual(_get_reliable_minutes(row), (540.0, "fbref_minutes"))

    def test_nonfinite_values_are_skipped(self):
        row = pd.Series({
            "fm_min": float("inf"),
            "fbref_minutes": "-5",
            "csv_min": "300",
        })
        self.assertEqual(_get_reliable_minutes(row), (300.0, "csv_min"))

    def test_unusable_minutes_do_not_crash_classification(self):
        row = pd.Series({
            "active_2526": "yes",
            "fm_min": "not available",
            "fbref_minutes": None,
            "csv_min": "nan",
        })
        self.assertEqual(_get_reliable_minutes(row), (None, None))
        self.assertEqual(_classify(row), "ACTIVE")

    def test_valid_primary_source_keeps_precedence(self):
        row = pd.Series({"fm_min": 271, "fbref_minutes": 1000})
        self.assertEqual(_get_reliable_minutes(row), (271.0, "fm_min"))


if __name__ == "__main__":
    unittest.main()
