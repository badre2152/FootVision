# tests/test_player_clustering.py
#
# Test suite for analytics/ml/player_clustering.py — the first ML component
# added to this project (2026-07-29). Unit tests against small synthetic
# DataFrames — no network, no MinIO, no real Gold run required.
#
# Coverage:
#   - GKs are always excluded from K-Means and get the fixed "Gardien" label
#   - select_k() picks a k that actually improves silhouette on separable data
#   - cluster_players() is reproducible given a fixed random_state
#   - missing-feature imputation uses position-group median, not global
#   - graceful degradation: missing columns / too few players raise clear
#     errors instead of silently producing garbage
#   - archetype naming assigns 4 distinct names on well-separated synthetic
#     archetype-shaped data (this is the one heuristic-y part of the module,
#     so it gets the most scrutiny)

import sys
import os

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics.ml.player_clustering import (
    cluster_players,
    select_k,
    _position_median_impute,
    _name_archetypes,
    GK_LABEL,
    CLUSTER_FEATURES,
)


# ─────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────

FEATURES = list(CLUSTER_FEATURES.keys())


def _make_archetype_block(n, position, seed, **overrides):
    """Builds n rows with mostly-flat feature values, nudged by `overrides`
    to sit in a specific corner of feature space (i.e. simulate a real
    archetype), plus small random noise so K-Means has something to do."""
    rng = np.random.default_rng(seed)
    data = {f: rng.normal(loc=overrides.get(f, 1.0), scale=0.05, size=n) for f in FEATURES}
    data["position"] = position
    data["player_name"] = [f"{position}_{seed}_{i}" for i in range(n)]
    return pd.DataFrame(data)


@pytest.fixture
def separable_df():
    """6 GKs + 4 well-separated outfield archetype blocks (8 players each =
    32 outfield), so k=4 should clearly win the silhouette search."""
    gk = _make_archetype_block(6, "GK", seed=1)

    warrior = _make_archetype_block(
        8, "MID", seed=2,
        sb_pressures_per90=9.0, sd_duels_won_pct=9.0, Tkl_per90=9.0,
        sb_passes_per90=1.0, sd_pass_acc_pct=1.0, sd_key_passes_per90=1.0,
        sd_dribbles_per90=1.0, sb_carries_per90=1.0, xg_per90=1.0, Ast_per90=1.0,
        Int_per90=1.0, Clr_per90=1.0,
    )
    brain = _make_archetype_block(
        8, "MID", seed=3,
        sb_passes_per90=9.0, sd_pass_acc_pct=9.0, sd_key_passes_per90=9.0,
        sb_pressures_per90=1.0, sd_duels_won_pct=1.0, Tkl_per90=1.0,
        sd_dribbles_per90=1.0, sb_carries_per90=1.0, xg_per90=1.0, Ast_per90=1.0,
        Int_per90=1.0, Clr_per90=1.0,
    )
    speedster = _make_archetype_block(
        8, "FWD", seed=4,
        sd_dribbles_per90=9.0, sb_carries_per90=9.0, xg_per90=9.0, Ast_per90=9.0,
        sb_pressures_per90=1.0, sd_duels_won_pct=1.0, Tkl_per90=1.0,
        sb_passes_per90=1.0, sd_pass_acc_pct=1.0, sd_key_passes_per90=1.0,
        Int_per90=1.0, Clr_per90=1.0,
    )
    rock = _make_archetype_block(
        8, "DEF", seed=5,
        Int_per90=9.0, Clr_per90=9.0,
        sb_pressures_per90=1.0, sd_duels_won_pct=1.0, Tkl_per90=1.0,
        sb_passes_per90=1.0, sd_pass_acc_pct=1.0, sd_key_passes_per90=1.0,
        sd_dribbles_per90=1.0, sb_carries_per90=1.0, xg_per90=1.0, Ast_per90=1.0,
    )

    df = pd.concat([gk, warrior, brain, speedster, rock], ignore_index=True)
    df["player_id"] = [f"P{i:03d}" for i in range(len(df))]
    return df


@pytest.fixture
def small_realistic_df():
    """49-ish rows, some missing feature values — mirrors the real project's
    scale and NaN pattern, without needing the real Gold CSV."""
    rng = np.random.default_rng(7)
    n_gk, n_out = 6, 43
    gk = _make_archetype_block(n_gk, "GK", seed=10)
    outfield_positions = ["DEF"] * 16 + ["MID"] * 11 + ["FWD"] * 16
    out = pd.concat(
        [_make_archetype_block(1, pos, seed=100 + i) for i, pos in enumerate(outfield_positions)],
        ignore_index=True,
    )
    df = pd.concat([gk, out], ignore_index=True)
    df["player_id"] = [f"P{i:03d}" for i in range(len(df))]

    # Knock out ~12% of a couple of feature columns with NaN, outfield only
    for col in ["xg_per90", "sd_dribbles_per90"]:
        na_idx = df[df["position"] != "GK"].sample(frac=0.12, random_state=1).index
        df.loc[na_idx, col] = np.nan
    return df


# ─────────────────────────────────────────────────────────────────────────
# GK exclusion
# ─────────────────────────────────────────────────────────────────────────

class TestGoalkeeperExclusion:
    def test_gk_get_fixed_label_not_clustered(self, separable_df):
        result = cluster_players(separable_df, k=4)
        gk_rows = result.df[result.df["position"] == "GK"]
        assert (gk_rows["PlayerDNA"] == GK_LABEL).all()
        assert gk_rows["player_dna_cluster"].isna().all()

    def test_gk_excluded_from_centroid_count(self, separable_df):
        result = cluster_players(separable_df, k=4)
        assert result.n_gk == 6
        assert result.n_outfield == len(separable_df) - 6
        assert len(result.centroids) == 4


# ─────────────────────────────────────────────────────────────────────────
# select_k()
# ─────────────────────────────────────────────────────────────────────────

class TestSelectK:
    def test_picks_k4_on_four_separable_blocks(self, separable_df):
        from sklearn.preprocessing import StandardScaler

        outfield = separable_df[separable_df["position"] != "GK"]
        X = StandardScaler().fit_transform(outfield[FEATURES].values)
        best_k, table = select_k(X, k_range=(3, 6))
        assert best_k == 4
        assert set(table.keys()) == {3, 4, 5, 6}
        assert table[4] == max(table.values())

    def test_skips_k_too_large_for_n(self):
        X = np.random.default_rng(0).normal(size=(5, 3))
        best_k, table = select_k(X, k_range=(3, 6))
        # only k=3, k=4 are < n=5
        assert set(table.keys()) <= {3, 4}
        assert best_k in table

    def test_raises_if_no_valid_k(self):
        X = np.random.default_rng(0).normal(size=(2, 3))
        with pytest.raises(ValueError):
            select_k(X, k_range=(3, 6))


# ─────────────────────────────────────────────────────────────────────────
# Reproducibility
# ─────────────────────────────────────────────────────────────────────────

class TestReproducibility:
    def test_same_seed_same_labels(self, small_realistic_df):
        r1 = cluster_players(small_realistic_df, k=4, random_state=42)
        r2 = cluster_players(small_realistic_df, k=4, random_state=42)
        pd.testing.assert_series_equal(
            r1.df["player_dna_cluster"], r2.df["player_dna_cluster"]
        )

    def test_row_count_preserved(self, small_realistic_df):
        result = cluster_players(small_realistic_df, k=4)
        assert len(result.df) == len(small_realistic_df)


# ─────────────────────────────────────────────────────────────────────────
# Imputation
# ─────────────────────────────────────────────────────────────────────────

class TestImputation:
    def test_position_median_used_not_global(self):
        X = pd.DataFrame({"f": [10.0, 12.0, np.nan, 100.0, 102.0]})
        positions = pd.Series(["DEF", "DEF", "DEF", "FWD", "FWD"])
        filled, counts = _position_median_impute(X, positions)
        # DEF median of [10, 12] = 11, NOT the global median (~12) or FWD's 101
        assert filled.loc[2, "f"] == 11.0
        assert counts.tolist() == [0, 0, 1, 0, 0]

    def test_imputed_feature_count_tracked_on_output(self, small_realistic_df):
        result = cluster_players(small_realistic_df, k=4)
        outfield = result.df[result.df["position"] != "GK"]
        assert (outfield["dna_imputed_features_count"] >= 0).all()
        assert outfield["dna_imputed_features_count"].sum() > 0  # NaNs were injected
        gk = result.df[result.df["position"] == "GK"]
        assert (gk["dna_imputed_features_count"] == 0).all()


# ─────────────────────────────────────────────────────────────────────────
# Graceful degradation / input validation
# ─────────────────────────────────────────────────────────────────────────

class TestInputValidation:
    def test_missing_position_column_raises(self):
        df = pd.DataFrame({f: [1.0] * 10 for f in FEATURES})
        with pytest.raises(ValueError, match="position"):
            cluster_players(df)

    def test_too_few_outfield_players_raises(self):
        df = pd.DataFrame({f: [1.0] * 5 for f in FEATURES})
        df["position"] = ["GK"] * 2 + ["DEF"] * 3  # only 3 outfield, need >= MIN_K+1=4
        with pytest.raises(ValueError, match="outfield"):
            cluster_players(df)

    def test_missing_feature_columns_degrade_not_crash(self, separable_df):
        df = separable_df.drop(columns=["sd_dribbles_per90", "sd_pass_acc_pct"])
        result = cluster_players(df, k=4)  # should not raise
        assert "PlayerDNA" in result.df.columns


# ─────────────────────────────────────────────────────────────────────────
# Archetype naming
# ─────────────────────────────────────────────────────────────────────────

class TestArchetypeNaming:
    def test_four_distinct_archetypes_on_separable_data(self, separable_df):
        result = cluster_players(separable_df, k=4)
        names = set(result.archetype_map.values())
        assert names == {"Warrior", "Brain", "Speedster", "Rock"}

    def test_no_archetype_reused_across_clusters(self, separable_df):
        result = cluster_players(separable_df, k=4)
        assert len(result.archetype_map) == len(set(result.archetype_map.values()))

    def test_correct_block_maps_to_correct_archetype(self, separable_df):
        result = cluster_players(separable_df, k=4)
        df = result.df
        for expected_name, position, seed_prefix in [
            ("Warrior", "MID", "MID_2_"),
            ("Brain", "MID", "MID_3_"),
            ("Speedster", "FWD", "FWD_4_"),
            ("Rock", "DEF", "DEF_5_"),
        ]:
            block = df[df["player_name"].str.startswith(seed_prefix)]
            assert (block["PlayerDNA"] == expected_name).all(), (
                f"expected block {seed_prefix} to be labeled {expected_name}, "
                f"got {block['PlayerDNA'].unique()}"
            )

    def test_unnamed_clusters_fall_back_generically_when_k_gt_4(self, separable_df):
        result = cluster_players(separable_df, k=5)
        labels = set(result.archetype_map.values())
        named = labels & {"Warrior", "Brain", "Speedster", "Rock"}
        generic = labels - named
        assert len(named) <= 4
        assert all(name.startswith("Cluster ") for name in generic)
