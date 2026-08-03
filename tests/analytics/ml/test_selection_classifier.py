# tests/test_selection_classifier.py
#
# Test suite for analytics/ml/selection_classifier.py — added 2026-07-29
# alongside the module itself, the same day real ground truth (the
# 2026-05-26 official World Cup squad announcement) became available.

import sys
import os

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from analytics.ml.selection_classifier import (
    match_real_squad,
    random_baseline_pvalue,
    audit_vs_dss_ranking,
    fit_loocv_classifier,
    CLASSIFIER_FEATURES,
)


# ─────────────────────────────────────────────────────────────────────────
# random_baseline_pvalue — check against known hypergeometric values
# ─────────────────────────────────────────────────────────────────────────

class TestRandomBaselinePvalue:
    def test_matches_scipy_hypergeom_directly(self):
        from scipy.stats import hypergeom
        rv = hypergeom(49, 26, 26)
        expected = float(1 - rv.cdf(14))
        assert random_baseline_pvalue(49, 26, 26, 15) == pytest.approx(expected)

    def test_perfect_overlap_has_tiny_pvalue(self):
        # overlap == n_predicted == n_real should be extremely unlikely by chance
        p = random_baseline_pvalue(49, 26, 26, 26)
        assert p < 0.001

    def test_zero_overlap_has_pvalue_near_one(self):
        p = random_baseline_pvalue(49, 26, 26, 0)
        assert p > 0.99

    def test_monotonic_in_observed_overlap(self):
        pvals = [random_baseline_pvalue(49, 26, 26, k) for k in range(0, 27)]
        assert all(pvals[i] >= pvals[i + 1] for i in range(len(pvals) - 1))


# ─────────────────────────────────────────────────────────────────────────
# match_real_squad
# ─────────────────────────────────────────────────────────────────────────

class TestMatchRealSquad:
    def test_exact_names_all_match(self):
        real_squad = ["Achraf Hakimi", "Yassine Bounou"]
        df = pd.DataFrame({"player_name": ["Achraf Hakimi", "Yassine Bounou", "Someone Else"]})
        result = match_real_squad(df, real_squad=real_squad)
        assert result.matched_count == 2
        assert result.unmatched_real_squad == []
        assert result.df.loc[result.df["player_name"] == "Achraf Hakimi", "real_squad_selected"].iloc[0] == 1
        assert result.df.loc[result.df["player_name"] == "Someone Else", "real_squad_selected"].iloc[0] == 0

    def test_close_spelling_variants_match_above_threshold(self):
        real_squad = ["Anass Salah Eddine"]
        df = pd.DataFrame({"player_name": ["Anass Salah-Eddine", "Unrelated Player"]})
        result = match_real_squad(df, real_squad=real_squad, threshold=85)
        assert result.matched_count == 1

    def test_genuinely_absent_player_reported_unmatched_not_silently_dropped(self):
        real_squad = ["Someone Completely Different"]
        df = pd.DataFrame({"player_name": ["Achraf Hakimi", "Yassine Bounou"]})
        result = match_real_squad(df, real_squad=real_squad, threshold=85)
        assert result.matched_count == 0
        assert result.unmatched_real_squad == ["Someone Completely Different"]

    def test_missing_player_name_column_raises(self):
        df = pd.DataFrame({"name": ["x"]})
        with pytest.raises(ValueError, match="player_name"):
            match_real_squad(df, real_squad=["x"])


# ─────────────────────────────────────────────────────────────────────────
# audit_vs_dss_ranking
# ─────────────────────────────────────────────────────────────────────────

class TestAuditVsDssRanking:
    @pytest.fixture
    def toy_df(self):
        # 10 players, Rank 1-10 by DSSScore. Real squad (top_n=4) = players
        # ranked 1,2,9,10 -> overlap should be exactly {1,2} = 2.
        return pd.DataFrame({
            "player_name": [f"P{i}" for i in range(1, 11)],
            "position": ["MID"] * 10,
            "Rank": list(range(1, 11)),
            "DSSScore": np.linspace(0.9, 0.1, 10),
            "real_squad_selected": [1, 1, 0, 0, 0, 0, 0, 0, 1, 1],
        })

    def test_overlap_computed_correctly(self, toy_df):
        result = audit_vs_dss_ranking(toy_df, top_n=4)
        assert result["overlap_count"] == 2
        assert result["overlap_pct"] == pytest.approx(0.5)

    def test_false_positive_and_negative_lists_correct(self, toy_df):
        result = audit_vs_dss_ranking(toy_df, top_n=4)
        fp_names = {r["player_name"] for r in result["dss_favored_not_picked"]}
        fn_names = {r["player_name"] for r in result["picked_not_dss_favored"]}
        assert fp_names == {"P3", "P4"}      # top-4 by rank, not in real squad
        assert fn_names == {"P9", "P10"}     # in real squad, ranked outside top-4

    def test_requires_real_squad_selected_column(self):
        df = pd.DataFrame({"player_name": ["a"], "Rank": [1], "DSSScore": [0.5]})
        with pytest.raises(ValueError, match="match_real_squad"):
            audit_vs_dss_ranking(df, top_n=1)


# ─────────────────────────────────────────────────────────────────────────
# fit_loocv_classifier
# ─────────────────────────────────────────────────────────────────────────

class TestLoocvClassifier:
    @pytest.fixture
    def separable_labeled_df(self):
        # Strongly separable synthetic data — a sanity check that the LOOCV
        # loop is wired correctly (should score very well), not a claim
        # about real football data (which does NOT look like this).
        rng = np.random.default_rng(0)
        n = 40
        y = np.array([1] * 20 + [0] * 20)
        data = {f: np.where(y == 1, rng.normal(0.8, 0.05, n), rng.normal(0.2, 0.05, n))
                for f in CLASSIFIER_FEATURES}
        data["real_squad_selected"] = y
        return pd.DataFrame(data)

    def test_high_accuracy_on_strongly_separable_synthetic_data(self, separable_labeled_df):
        result = fit_loocv_classifier(separable_labeled_df)
        assert result.accuracy > 0.85
        assert result.n_samples == 40

    def test_missing_features_raise(self):
        df = pd.DataFrame({"real_squad_selected": [0, 1]})
        with pytest.raises(ValueError, match="Missing classifier features"):
            fit_loocv_classifier(df)

    def test_single_class_label_raises(self):
        df = pd.DataFrame({f: [0.5] * 10 for f in CLASSIFIER_FEATURES})
        df["real_squad_selected"] = 1  # everyone selected — no negative class
        with pytest.raises(ValueError, match="single-class"):
            fit_loocv_classifier(df)

    def test_coefficients_indexed_by_feature_name(self, separable_labeled_df):
        result = fit_loocv_classifier(separable_labeled_df)
        assert set(result.coefficients.index) == set(CLASSIFIER_FEATURES)

    def test_metrics_in_valid_range(self, separable_labeled_df):
        result = fit_loocv_classifier(separable_labeled_df)
        for v in [result.accuracy, result.precision, result.recall, result.f1]:
            assert 0.0 <= v <= 1.0
