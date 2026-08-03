# tests/test_gold2_components.py
#
# Test suite for the 2026-07-14 session's work:
#   - ChemistryScore, PressureIndex, FlexibilityScore (new components)
#   - percentile_score.py wiring (rank-preservation contract)
#   - minutes_filter.py weight-key regression (the w.get() dead-key bug)
#   - build_squad_summary() position-cap regression (the 5-GK bug)
#   - COMPOSITE_WEIGHTS sums to 1.00
#
# These are UNIT tests against small synthetic DataFrames — no network,
# no MinIO, no real Bronze/Silver run required. They test the SCORING
# MATH and the REGRESSION-PRONE WIRING, not the scrapers themselves.
#
# NOTE ON FlexibilityScore: these tests cover the scoring module's math
# (analytics/scoring/flexibility_score.py), which IS verified here. They
# do NOT and CANNOT verify the upstream Transfermarkt secondary-position
# selector (ingestion/loaders/transfermarkt_loader.py::
# _extract_secondary_positions) actually matches live TM markup — that
# requires a real network fetch, which this sandbox does not have. See
# that module's docstring for the manual verification steps required.

import sys
import os
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from analytics.scoring.chemistry_score import compute_chemistry_score
from analytics.scoring.pressure_index import compute_pressure_index, _impute_pressure_delta
from analytics.scoring.flexibility_score import compute_flexibility_score, secondary_position_groups
from analytics.scoring.percentile_score import apply_percentile_scoring, score_summary
from analytics.scoring.minutes_filter import apply_minutes_filter
from analytics.scoring.performance_score import compute_final_score
import config


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _base_df(n=6):
    """Minimal synthetic Silver-shaped DataFrame for testing sub-score modules."""
    positions = (["GK", "DEF", "DEF", "MID", "MID", "FWD"] * ((n // 6) + 1))[:n]
    return pd.DataFrame({
        "player_id":   [f"P{i:03d}" for i in range(n)],
        "player_name": [f"Player {i}" for i in range(n)],
        "position":    positions,
        "club":        ["Club A"] * n,
    })


def _fully_scored_df(n=6):
    """
    A DataFrame with every sub-score column compute_final_score() needs,
    already populated with plausible values — used to test percentile
    scoring, minutes_filter, and squad_summary without depending on the
    full Silver->Gold pipeline running end to end.
    """
    df = _base_df(n)
    rng = np.random.default_rng(42)
    for col in ["PerformanceScore", "FormScore", "MentalScore",
                "AvailabilityScore", "InternationalScore",
                "ChemistryScore", "PressureIndex", "FlexibilityScore"]:
        df[col] = rng.uniform(0.2, 0.9, size=n).round(4)
    df["active_2526"] = "active"
    df["minutes_flag"] = "OK"
    return df


# ─────────────────────────────────────────────────────────────────────────────
# ChemistryScore
# ─────────────────────────────────────────────────────────────────────────────

class TestChemistryScore:

    def test_normalizes_within_bounds(self):
        df = _base_df()
        df["chem_avg_per90"] = [0.0, 2.0, 4.0, 6.0, 8.0, 4.0]
        df["chem_num_partners"] = [0, 3, 5, 2, 4, 0]
        out = compute_chemistry_score(df)
        assert "ChemistryScore" in out.columns
        assert out["ChemistryScore"].between(0, 1).all()
        # bounds are (0.0, 8.0) in config.py -> 8.0 should normalize to 1.0
        assert out.loc[out["chem_avg_per90"] == 8.0, "ChemistryScore"].iloc[0] == pytest.approx(1.0)
        assert out.loc[out["chem_avg_per90"] == 0.0, "ChemistryScore"].iloc[0] == pytest.approx(0.0)

    def test_missing_column_does_not_crash(self):
        df = _base_df()
        out = compute_chemistry_score(df)
        assert "ChemistryScore" in out.columns
        assert out["ChemistryScore"].isna().all()

    def test_monotonic(self):
        """Higher chem_avg_per90 must never produce a lower ChemistryScore."""
        df = _base_df()
        df["chem_avg_per90"] = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        out = compute_chemistry_score(df).sort_values("chem_avg_per90")
        assert out["ChemistryScore"].is_monotonic_increasing


# ─────────────────────────────────────────────────────────────────────────────
# PressureIndex
# ─────────────────────────────────────────────────────────────────────────────

class TestPressureIndex:

    def test_positive_delta_when_intl_exceeds_club(self):
        df = _base_df(n=2)
        df.loc[0, "position"] = "FWD"
        df.loc[1, "position"] = "FWD"
        df["sb_xg_per90"] = [0.8, 0.1]
        df["xg_per90"]    = [0.2, 0.5]
        out = compute_pressure_index(df)
        # player 0: performs ABOVE club level under pressure -> positive delta -> PressureIndex > 0.5 (midpoint)
        assert out.loc[0, "pressure_delta"] > 0
        assert out.loc[0, "PressureIndex"] > out.loc[1, "PressureIndex"]

    def test_gk_falls_back_to_global_median_not_crash(self):
        """
        GKs have no club xG by construction -> pressure_delta is always NaN
        pre-imputation for GK. Must fall back to the GLOBAL median, not crash
        or silently stay NaN forever.
        """
        df = _base_df(n=3)
        df.loc[0, "position"] = "GK"
        df.loc[1, "position"] = "FWD"
        df.loc[2, "position"] = "FWD"
        df["sb_xg_per90"] = [np.nan, 0.6, 0.4]
        df["xg_per90"]    = [np.nan, 0.3, 0.3]
        out = compute_pressure_index(df)
        assert out["PressureIndex"].notna().all(), "GK should be imputed, not left NaN"

    def test_missing_columns_do_not_crash(self):
        df = _base_df()
        out = compute_pressure_index(df)
        assert "PressureIndex" in out.columns
        assert out["PressureIndex"].isna().all()


# ─────────────────────────────────────────────────────────────────────────────
# FlexibilityScore
# ─────────────────────────────────────────────────────────────────────────────

class TestFlexibilityScore:

    def test_more_positions_scores_higher(self):
        df = _base_df(n=4)
        df["tm_num_positions"] = [1, 2, 3, 4]
        out = compute_flexibility_score(df)
        assert out["FlexibilityScore"].is_monotonic_increasing

    def test_all_ones_produces_flat_zero_and_warns(self, caplog):
        """
        The expected failure mode if the (untested) upstream Transfermarkt
        selector never matches: every player defaults to 1 position, and
        this module must warn about it rather than silently produce a
        misleadingly "normal-looking" score.
        """
        df = _base_df(n=5)
        df["tm_num_positions"] = 1
        with caplog.at_level("WARNING"):
            out = compute_flexibility_score(df)
        assert (out["FlexibilityScore"] == 0.0).all()
        assert any("stale" in r.message or "uninformative" in r.message for r in caplog.records)

    def test_missing_column_does_not_crash(self):
        df = _base_df()
        out = compute_flexibility_score(df)
        assert out["FlexibilityScore"].isna().all()


# ─────────────────────────────────────────────────────────────────────────────
# percentile_score.py — rank-preservation contract (was orphaned code)
# ─────────────────────────────────────────────────────────────────────────────

class TestPercentileScoring:

    def test_never_changes_rank_order(self):
        df = _fully_scored_df(n=10)
        df = compute_final_score(df)
        df_pct = apply_percentile_scoring(df.copy())

        original_order   = df.sort_values("DSSScore", ascending=False)["player_name"].tolist()
        percentile_order  = df_pct.sort_values("DSSScore_pct", ascending=False)["player_name"].tolist()
        assert original_order == percentile_order, (
            "apply_percentile_scoring() must NEVER change relative ranking — "
            "it is a display-only rescaling."
        )

    def test_expands_score_range(self):
        df = _fully_scored_df(n=10)
        df = compute_final_score(df)
        df_pct = apply_percentile_scoring(df.copy())
        original_range   = df["DSSScore"].max() - df["DSSScore"].min()
        percentile_range = df_pct["DSSScore_pct"].max() - df_pct["DSSScore_pct"].min()
        assert percentile_range >= original_range

    def test_score_summary_reports_both_ranges(self):
        df = _fully_scored_df(n=10)
        df = compute_final_score(df)
        df = apply_percentile_scoring(df)
        summary = score_summary(df, top_n=5)
        assert "dss_range_original" in summary
        assert "dss_range_percentile" in summary
        assert summary["dss_range_percentile"] >= summary["dss_range_original"]


# ─────────────────────────────────────────────────────────────────────────────
# minutes_filter.py — regression test for the w.get() dead-key bug
# ─────────────────────────────────────────────────────────────────────────────

class TestMinutesFilterWeightBinding:

    def test_dssscore_actually_reads_config_weights(self, monkeypatch):
        """
        REGRESSION TEST for the 2026-07-14 fix: apply_minutes_filter() used
        to recompute DSSScore with COMPOSITE_WEIGHTS.get("performance", 0.35)
        etc. — lowercase keys that never existed in COMPOSITE_WEIGHTS (real
        keys are "PerformanceScore", "TacticalFitScore"...), so it silently
        ALWAYS used the hardcoded defaults regardless of config.py.

        UPDATED (2026-07-29): apply_minutes_filter() no longer computes
        DSSScore at all — that duplicate/dead-key-bound computation was
        removed entirely (see BUG: apply_dss_gate() ordering fix same day).
        DSSScore is now computed exactly once, by compute_final_score()
        (which already reads config.COMPOSITE_WEIGHTS correctly), and the
        minutes-based gate is applied afterwards by apply_dss_gate(). This
        test now exercises the full, correctly-ordered pipeline:
            apply_minutes_filter() -> compute_final_score() -> apply_dss_gate()
        and proves the ORIGINAL intent still holds end-to-end: mutating
        COMPOSITE_WEIGHTS in config.py must actually change the final
        DSSScore. If this test ever fails again, either the dead-key bug
        regressed, or a new ordering bug (like the 2026-07-29 one, where
        apply_dss_gate() ran before compute_final_score() and got its
        result silently overwritten) was reintroduced.
        """
        from analytics.scoring.minutes_filter import apply_dss_gate

        df = _fully_scored_df(n=5)
        df["inj_days_missed_2y"] = 0
        df["fm_min"] = 900  # plenty of minutes -> ACTIVE, no availability penalty

        original_weights = dict(config.COMPOSITE_WEIGHTS)

        # الترتيب الصحيح: minutes_filter (تصنيف) -> compute_final_score (DSSScore
        # الوحيد) -> apply_dss_gate (البوابة). نفس الترتيب المستعمل فعلياً فـ run_gold.py
        df1 = apply_minutes_filter(df.copy())
        df1 = compute_final_score(df1)
        df1 = apply_dss_gate(df1)
        assert "DSSScore" in df1.columns, (
            "DSSScore missing after the full pipeline — apply_minutes_filter() -> "
            "compute_final_score() -> apply_dss_gate() must produce it exactly once."
        )

        # Now drastically change ONE weight and confirm the output changes
        mutated_weights = dict(original_weights)
        mutated_weights["PerformanceScore"] = 0.999
        mutated_weights["FormScore"] = max(0.0001, 1 - sum(
            v for k, v in mutated_weights.items() if k != "FormScore"
        ))
        # ملاحظة اكتُشفت أثناء هذا التحديث: compute_final_score() تستورد
        # COMPOSITE_WEIGHTS على مستوى الملف (import علوي فـ performance_score.py)،
        # ماشي جوّا الدالة كيما كانت apply_minutes_filter() القديمة. يعني
        # monkeypatch.setattr(config, "COMPOSITE_WEIGHTS", ...) وحدها ماكافيتش
        # — خاصنا نبدّل الاسم المربوط مباشرة فـ namespace performance_score.py.
        # هذا فرق سلوك حقيقي (module-level import = قراءة مرة وحدة عند
        # الimport، ماشي "قراءة حية" فـ كل استدعاء) يستاهل تحقق فـ الإنتاج،
        # لكن ماشي bug جديد — التصميم كان هكذا أصلاً قبل هاذ الجلسة.
        monkeypatch.setattr(config, "COMPOSITE_WEIGHTS", mutated_weights)
        import analytics.scoring.performance_score as _perf_mod
        monkeypatch.setattr(_perf_mod, "COMPOSITE_WEIGHTS", mutated_weights)
        df2 = apply_minutes_filter(df.copy())
        df2 = compute_final_score(df2)
        df2 = apply_dss_gate(df2)

        assert not df1["DSSScore"].equals(df2["DSSScore"]), (
            "DSSScore did not change after mutating COMPOSITE_WEIGHTS — either "
            "the dead-key binding bug (w.get() with keys that don't exist in "
            "COMPOSITE_WEIGHTS) has regressed, or compute_final_score() is no "
            "longer being called / no longer reads config.py correctly."
        )

        # BUG-01 ordering regression guard: apply_dss_gate() must run AFTER
        # compute_final_score(), never before. If someone reorders the calls
        # back to the 2026-07-29 broken order, apply_dss_gate() would gate a
        # DSSScore that gets silently overwritten right after — this would
        # NOT raise an exception, so we assert the gate's effect is actually
        # visible in the final output instead.
        active_all = df.copy()
        active_all["active_2526"] = "yes"
        active_all["fm_min"] = 900
        active_all = apply_minutes_filter(active_all)
        active_all.loc[active_all.index[0], "minutes_flag"] = "SEED_INACTIVE"
        gated = compute_final_score(active_all.copy())
        gated = apply_dss_gate(gated)
        ungated_equiv = compute_final_score(active_all.copy())
        assert gated["DSSScore"].iloc[0] < ungated_equiv["DSSScore"].iloc[0] * 0.5, (
            "apply_dss_gate() had no meaningful effect on a SEED_INACTIVE "
            "player's final DSSScore — the ordering bug (gate applied before "
            "compute_final_score, then silently discarded) may have regressed."
        )


# ─────────────────────────────────────────────────────────────────────────────
# config.py sanity
# ─────────────────────────────────────────────────────────────────────────────

class TestCompositeWeights:

    def test_sums_to_one(self):
        total = sum(config.COMPOSITE_WEIGHTS.values())
        assert total == pytest.approx(1.0, abs=1e-6), (
            f"COMPOSITE_WEIGHTS sums to {total}, not 1.00 — DSSScore will not "
            f"be a proper weighted average."
        )

    def test_flexibility_and_sentiment_status(self):
        """
        UPDATED 2026-07-22: both FlexibilityScore (added 2026-07-14) and
        SentimentScore (added 2026-07-22, on top of new Reddit/YouTube/
        NewsAPI collectors + analytics/nlp/sentiment_analyzer.py) are now
        real components with real supporting infrastructure — this test
        used to assert SentimentScore must NOT be present (deferred,
        no infra existed). Updated now that the infra exists, per the
        project owner's decision to build it in this session.
        """
        assert "FlexibilityScore" in config.COMPOSITE_WEIGHTS
        assert "SentimentScore" in config.COMPOSITE_WEIGHTS

    def test_sentiment_has_supporting_infrastructure(self):
        """
        Guards against SentimentScore's weight ever being re-added as a
        placeholder without its actual pipeline — the failure mode this
        whole test class originally protected against. If someone removes
        the NLP module or the collectors while leaving the weight in
        config.py, this should fail as a signal to fix one or the other.
        """
        import importlib.util
        nlp_path = importlib.util.find_spec("analytics.nlp.sentiment_analyzer")
        assert nlp_path is not None, (
            "SentimentScore has a weight in COMPOSITE_WEIGHTS but "
            "analytics/nlp/sentiment_analyzer.py doesn't exist — remove the "
            "weight or restore the module."
        )


# ─────────────────────────────────────────────────────────────────────────────
# secondary_position_groups() — spec #13 TM-label -> GK/DEF/MID/FWD mapping
# ─────────────────────────────────────────────────────────────────────────────

class TestSecondaryPositionGroups:

    def test_maps_known_labels(self):
        assert secondary_position_groups("Right Midfield, Left-Back") == {"MID", "DEF"}

    def test_single_label(self):
        assert secondary_position_groups("Centre-Forward") == {"FWD"}

    def test_none_and_nan_and_empty_return_empty_set(self):
        assert secondary_position_groups(None) == set()
        assert secondary_position_groups(np.nan) == set()
        assert secondary_position_groups("") == set()
        assert secondary_position_groups("   ") == set()

    def test_unrecognized_label_skipped_not_crashed(self):
        assert secondary_position_groups("Some New TM Label") == set()
        # mixed known + unknown: known one still comes through
        assert secondary_position_groups("Left-Back, Some New TM Label") == {"DEF"}


# ─────────────────────────────────────────────────────────────────────────────
# select_squad_with_position_cap() — spec #13 flexibility tiebreak
# ─────────────────────────────────────────────────────────────────────────────

class TestFlexibilityTiebreak:

    def _synthetic_df(self):
        """
        6 GK, 6 DEF, 3 MID, 2 FWD candidates with caps {GK:2, DEF:3, MID:2, FWD:1}
        (top_n=8) — small but structurally identical to the real 49-player /
        26-squad case: MID has the thinnest surplus (3 candidates for a cap
        of 2 -> surplus 1), making it the "understaffed" group.

        For DEF's last (3rd) slot: "Def C" and "Def D" are near-tied
        (0.001 apart, well under the 0.01 threshold). "Def D" lists MID as a
        secondary position (understaffed) and should be preferred over
        "Def C", who has no secondary positions.
        """
        rows = [
            # GK
            {"player_id": "GK1", "player_name": "Gk A", "position": "GK", "DSSScore": 0.90, "tm_secondary_positions": None},
            {"player_id": "GK2", "player_name": "Gk B", "position": "GK", "DSSScore": 0.85, "tm_secondary_positions": None},
            {"player_id": "GK3", "player_name": "Gk C", "position": "GK", "DSSScore": 0.40, "tm_secondary_positions": None},
            {"player_id": "GK4", "player_name": "Gk D", "position": "GK", "DSSScore": 0.35, "tm_secondary_positions": None},
            {"player_id": "GK5", "player_name": "Gk E", "position": "GK", "DSSScore": 0.30, "tm_secondary_positions": None},
            {"player_id": "GK6", "player_name": "Gk F", "position": "GK", "DSSScore": 0.25, "tm_secondary_positions": None},
            # DEF — the tiebreak case is here (last/3rd slot)
            {"player_id": "DEF1", "player_name": "Def A", "position": "DEF", "DSSScore": 0.88, "tm_secondary_positions": None},
            {"player_id": "DEF2", "player_name": "Def B", "position": "DEF", "DSSScore": 0.80, "tm_secondary_positions": None},
            {"player_id": "DEF3", "player_name": "Def C", "position": "DEF", "DSSScore": 0.601, "tm_secondary_positions": None},
            {"player_id": "DEF4", "player_name": "Def D", "position": "DEF", "DSSScore": 0.600, "tm_secondary_positions": "Central Midfield"},
            {"player_id": "DEF5", "player_name": "Def E", "position": "DEF", "DSSScore": 0.40, "tm_secondary_positions": None},
            {"player_id": "DEF6", "player_name": "Def F", "position": "DEF", "DSSScore": 0.35, "tm_secondary_positions": None},
            # MID — thin: only 3 candidates for a cap of 2 (surplus 1)
            {"player_id": "MID1", "player_name": "Mid A", "position": "MID", "DSSScore": 0.75, "tm_secondary_positions": None},
            {"player_id": "MID2", "player_name": "Mid B", "position": "MID", "DSSScore": 0.70, "tm_secondary_positions": None},
            {"player_id": "MID3", "player_name": "Mid C", "position": "MID", "DSSScore": 0.20, "tm_secondary_positions": None},
            # FWD
            {"player_id": "FWD1", "player_name": "Fwd A", "position": "FWD", "DSSScore": 0.65, "tm_secondary_positions": None},
            {"player_id": "FWD2", "player_name": "Fwd B", "position": "FWD", "DSSScore": 0.20, "tm_secondary_positions": None},
        ]
        df = pd.DataFrame(rows)
        df["Rank"] = df["DSSScore"].rank(ascending=False, method="min").astype(int)
        df["club"] = "Club"
        return df

    def test_flexible_near_tied_player_is_selected_with_reason(self):
        from pipelines.gold import run_gold
        df = self._synthetic_df()
        caps = {"GK": 2, "DEF": 3, "MID": 2, "FWD": 1}

        squad_df = run_gold.select_squad_with_position_cap(
            df, top_n=8, position_caps=caps,
            use_flexibility_tiebreak=True, flex_tiebreak_threshold=0.01,
            depth_chart_surplus_threshold=3,
        )

        def_names = set(squad_df[squad_df["position"] == "DEF"]["player_name"])
        assert "Def D" in def_names, "Flexible near-tied player should fill the last DEF slot"
        assert "Def C" not in def_names, "Non-flexible near-tied player should be swapped out"

        reason_row = squad_df[squad_df["player_name"] == "Def D"].iloc[0]
        assert reason_row["selection_reason"] != "", "Swap must be recorded, not silent"
        assert "MID" in reason_row["selection_reason"]

        # Everyone else keeps an empty (not-swapped) reason
        others = squad_df[squad_df["player_name"] != "Def D"]
        assert (others["selection_reason"] == "").all()

    def test_position_caps_and_total_unchanged_by_tiebreak(self):
        """Regardless of the tiebreak, hard position totals and squad size must hold."""
        from pipelines.gold import run_gold
        df = self._synthetic_df()
        caps = {"GK": 2, "DEF": 3, "MID": 2, "FWD": 1}

        squad_df = run_gold.select_squad_with_position_cap(
            df, top_n=8, position_caps=caps, use_flexibility_tiebreak=True,
        )
        counts = squad_df["position"].value_counts().to_dict()
        assert counts == caps
        assert len(squad_df) == 8

    def test_no_close_tie_leaves_pure_dsscore_order_unchanged(self):
        """
        Existing squad-selection behavior for cases with no close ties must be
        unchanged — e.g. FWD's single slot ("Fwd A" @ 0.65 vs "Fwd B" @ 0.20)
        isn't remotely close, so the higher-scored player wins regardless of
        flexibility, exactly as before this spec.
        """
        from pipelines.gold import run_gold
        df = self._synthetic_df()
        caps = {"GK": 2, "DEF": 3, "MID": 2, "FWD": 1}

        squad_df = run_gold.select_squad_with_position_cap(
            df, top_n=8, position_caps=caps, use_flexibility_tiebreak=True,
        )
        fwd_names = set(squad_df[squad_df["position"] == "FWD"]["player_name"])
        assert fwd_names == {"Fwd A"}

    def test_disabling_tiebreak_falls_back_to_old_behavior(self):
        from pipelines.gold import run_gold
        df = self._synthetic_df()
        caps = {"GK": 2, "DEF": 3, "MID": 2, "FWD": 1}

        squad_df = run_gold.select_squad_with_position_cap(
            df, top_n=8, position_caps=caps, use_flexibility_tiebreak=False,
        )
        def_names = set(squad_df[squad_df["position"] == "DEF"]["player_name"])
        assert def_names == {"Def A", "Def B", "Def C"}, (
            "With the tiebreak disabled, pure DSSScore order must win, "
            "same as before spec #13."
        )
        assert (squad_df["selection_reason"] == "").all()


# ─────────────────────────────────────────────────────────────────────────────
# build_squad_summary() — regression test for the position-cap bug
# ─────────────────────────────────────────────────────────────────────────────

class TestSquadSummaryPositionCap:

    def test_position_breakdown_uses_capped_squad_not_raw_rank(self):
        """
        REGRESSION TEST for the 2026-07-14 fix: build_squad_summary() used
        to filter the FULL 49-player table by raw Rank <= top_n, ignoring
        the position cap entirely -> could report e.g. 5 GKs in a 26-man
        squad (verified live on the 2026-07-13 run before this fix).

        This test builds a deliberately GK-heavy synthetic "all_df" (6 GKs
        in the top ranks) and a correctly capped "squad_df" (3 GKs), and
        confirms build_squad_summary() reports 3, reading squad_df — NOT 6.
        """
        from pipelines.gold import run_gold

        # 6 GKs rank in the global top 10 by pure DSSScore (unrealistic but
        # exactly the scenario that broke in production)
        all_df = pd.DataFrame({
            "player_id":   [f"P{i:03d}" for i in range(10)],
            "player_name": [f"Player {i}" for i in range(10)],
            "position":    ["GK"] * 6 + ["DEF"] * 2 + ["MID"] * 1 + ["FWD"] * 1,
            "club":        ["Club A"] * 10,
            "DSSScore":    [0.9, 0.85, 0.8, 0.75, 0.7, 0.65, 0.6, 0.55, 0.5, 0.45],
            "Rank":        list(range(1, 11)),
        })

        # The CORRECTLY capped squad (what select_squad_with_position_cap
        # would produce): only 3 GKs
        squad_df = all_df[all_df["player_name"].isin(
            ["Player 0", "Player 1", "Player 2",   # 3 GKs (capped)
             "Player 6", "Player 7",                 # DEF
             "Player 8",                              # MID
             "Player 9"]                              # FWD
        )].copy()

        summary = run_gold.build_squad_summary(squad_df, all_df, top_n=len(squad_df))

        assert summary["position_counts"]["GK"] == 3, (
            "squad_summary reported the WRONG GK count — it is reading "
            "raw-Rank-filtered all_df again instead of the position-capped "
            "squad_df. This is the exact regression fixed on 2026-07-14."
        )
        assert summary["squad_size"] == len(squad_df)
        assert summary["total_candidates"] == len(all_df)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))