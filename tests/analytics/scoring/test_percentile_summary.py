"""Regression checks for percentile score summaries."""

import unittest

import pandas as pd

from analytics.scoring.percentile_score import apply_percentile_scoring, score_summary


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

    def test_missing_scores_are_preserved_and_do_not_crash(self):
        frame = pd.DataFrame({"DSSScore": [70.0, None, 90.0, 80.0]})
        result = apply_percentile_scoring(frame)
        self.assertTrue(pd.isna(result.loc[1, "DSSScore_pct"]))
        self.assertTrue(result.loc[2, "DSSScore_pct"] > result.loc[3, "DSSScore_pct"])
        self.assertTrue(result.loc[3, "DSSScore_pct"] > result.loc[0, "DSSScore_pct"])

    def test_all_missing_scores_do_not_crash(self):
        frame = pd.DataFrame({"DSSScore": [None, None]})
        result = apply_percentile_scoring(frame)
        self.assertTrue(result["DSSScore_pct"].isna().all())

    def test_empty_squad_returns_unknown_metrics(self):
        frame = pd.DataFrame(columns=["Rank", "DSSScore", "DSSScore_pct"])
        summary = score_summary(frame)
        self.assertTrue(summary)
        self.assertTrue(all(value is None for value in summary.values()))


if __name__ == "__main__":
    unittest.main()
