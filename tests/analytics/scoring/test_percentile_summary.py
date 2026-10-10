"""Regression checks for percentile score summaries."""

import unittest

import pandas as pd

from analytics.scoring.percentile_score import score_summary


class PercentileSummaryTests(unittest.TestCase):
    def test_short_squad_uses_available_rows(self):
        frame = pd.DataFrame({
            "Rank": [1, 2, 3],
            "DSSScore": [90.0, 80.0, 70.0],
            "DSSScore_pct": [95.0, 80.0, 60.0],
        })
        summary = score_summary(frame, top_n=26)
        self.assertEqual(summary["elite_fringe_gap_original"], 10.0)
        self.assertEqual(summary["elite_fringe_gap_percentile"], 15.0)

    def test_empty_squad_returns_unknown_metrics(self):
        frame = pd.DataFrame(columns=["Rank", "DSSScore", "DSSScore_pct"])
        summary = score_summary(frame)
        self.assertTrue(summary)
        self.assertTrue(all(value is None for value in summary.values()))


if __name__ == "__main__":
    unittest.main()
