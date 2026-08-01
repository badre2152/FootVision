# tests/test_depth_chart.py
#
# Test suite for analytics/depth_chart.py (10_depth_chart.md).
# Unit tests against small synthetic DataFrames, no network/MinIO/Gold run
# required — mirrors tests/test_injury_predictor.py's style.
#
# Covers, per 10_depth_chart.md's Acceptance Criteria:
#   - normal case
#   - position with no depth left (thin/exhausted pool)
#   - a flexible player counted correctly for two positions
# plus the Edge Cases section explicitly: honest "thin depth" messaging,
# and a replacement that outscores the incumbent being flagged, not hidden.

import sys
import os

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics.depth_chart import (
    get_replacement_candidates,
    get_replacement_candidates_for_position,
    parse_secondary_position_buckets,
    POSITION_BUCKET_MAP,
    POSITIONS,
)


def _player(player_id, name, position, dss, secondary=None, **extra):
    row = {
        "player_id": player_id,
        "player_name": name,
        "position": position,
        "DSSScore": dss,
    }
    if secondary is not None:
        row["tm_secondary_positions"] = secondary
    row.update(extra)
    return row


def _df(*rows):
    return pd.DataFrame(list(rows))


# ─────────────────────────────────────────────────────────────────────────
# parse_secondary_position_buckets()
# ─────────────────────────────────────────────────────────────────────────

class TestParseSecondaryPositionBuckets:

    def test_empty_and_missing_input(self):
        assert parse_secondary_position_buckets(None) == []
        assert parse_secondary_position_buckets(float("nan")) == []
        assert parse_secondary_position_buckets("") == []
        assert parse_secondary_position_buckets("   ") == []

    def test_single_label(self):
        assert parse_secondary_position_buckets("Right-Back") == ["DEF"]

    def test_multiple_labels_deduplicated(self):
        # "Right Midfield" and "Central Midfield" both bucket to MID —
        # must appear once, not twice.
        result = parse_secondary_position_buckets("Right Midfield, Central Midfield")
        assert result == ["MID"]

    def test_multiple_labels_different_buckets_order_preserved(self):
        result = parse_secondary_position_buckets("Right Midfield, Left-Back")
        assert result == ["MID", "DEF"]

    def test_unrecognized_label_skipped_not_guessed(self):
        # "Deep-Lying Playmaker" isn't a real TM label used in this
        # project's data, but must not crash or be silently invented into
        # a bucket — it should just be dropped.
        result = parse_secondary_position_buckets("Deep-Lying Playmaker, Right-Back")
        assert result == ["DEF"]

    def test_case_and_hyphen_insensitive(self):
        assert parse_secondary_position_buckets("right-back") == ["DEF"]
        assert parse_secondary_position_buckets("RIGHT BACK") == ["DEF"]
        assert parse_secondary_position_buckets("Right Back") == ["DEF"]

    def test_every_map_value_is_a_valid_position(self):
        assert set(POSITION_BUCKET_MAP.values()) <= set(POSITIONS)

    def test_real_project_labels_all_resolve(self):
        # Every distinct label actually observed in this project's real
        # tm_secondary_positions Bronze data (see analytics/depth_chart.py
        # module header) — this test is the regression guard if that
        # observed set ever changes and POSITION_BUCKET_MAP isn't updated
        # alongside it.
        real_labels = [
            "Right Midfield", "Left-Back", "Centre-Back", "Central Midfield",
            "Left Midfield", "Left Winger", "Right-Back", "Sweeper",
            "Defensive Midfield", "Attacking Midfield", "Right Winger",
            "Centre-Forward", "Second Striker", "Goalkeeper",
        ]
        for label in real_labels:
            buckets = parse_secondary_position_buckets(label)
            assert buckets, f"{label!r} did not resolve to any bucket"


# ─────────────────────────────────────────────────────────────────────────
# get_replacement_candidates() — normal case
# ─────────────────────────────────────────────────────────────────────────

class TestNormalCase:

    def test_returns_top_n_ranked_by_dssscore(self):
        df = _df(
            _player("P001", "Incumbent GK", "GK", 0.90),
            _player("P002", "Bench GK 1", "GK", 0.60),
            _player("P003", "Bench GK 2", "GK", 0.80),
            _player("P004", "Bench GK 3", "GK", 0.70),
            _player("P005", "Bench GK 4", "GK", 0.50),
        )
        selected = {"P001"}
        result = get_replacement_candidates(df, selected, "P001", top_n=3)

        assert result.target_player_name == "Incumbent GK"
        assert result.incumbent_DSSScore == 0.90
        assert [c.player_id for c in result.candidates] == ["P003", "P004", "P002"]
        assert result.has_full_depth is True
        assert result.message is None

    def test_gap_to_incumbent_computed_correctly(self):
        df = _df(
            _player("P001", "Incumbent", "DEF", 0.80),
            _player("P002", "Candidate", "DEF", 0.65),
        )
        result = get_replacement_candidates(df, {"P001"}, "P001", top_n=1)
        c = result.candidates[0]
        assert c.gap_to_incumbent == pytest.approx(0.15)
        assert c.stronger_than_incumbent is False

    def test_excludes_currently_selected_players_from_pool(self):
        df = _df(
            _player("P001", "Incumbent", "MID", 0.80),
            _player("P002", "Other selected MID", "MID", 0.95),  # selected — must NOT appear
            _player("P003", "Bench MID", "MID", 0.60),
        )
        selected = {"P001", "P002"}
        result = get_replacement_candidates(df, selected, "P001", top_n=3)
        ids = [c.player_id for c in result.candidates]
        assert "P002" not in ids
        assert ids == ["P003"]

    def test_excludes_target_player_from_their_own_replacement_pool(self):
        df = _df(_player("P001", "Solo", "FWD", 0.80))
        result = get_replacement_candidates(df, {"P001"}, "P001", top_n=3)
        assert result.candidates == []
        assert "exhausted" in result.message.lower() or "genuinely" in result.message.lower()

    def test_unknown_player_id_returns_explanatory_message_not_crash(self):
        df = _df(_player("P001", "A", "GK", 0.5))
        result = get_replacement_candidates(df, {"P001"}, "P999", top_n=3)
        assert result.candidates == []
        assert "not found" in result.message.lower()


# ─────────────────────────────────────────────────────────────────────────
# Edge case — position with no depth left
# ─────────────────────────────────────────────────────────────────────────

class TestThinOrExhaustedDepth:

    def test_position_group_fully_exhausted_returns_honest_message(self):
        # All 3 GKs in the entire candidate pool are already selected —
        # zero remain as replacements. GK edge case explicitly called out
        # in 10_depth_chart.md's Edge Cases (small position group).
        df = _df(
            _player("P001", "GK A", "GK", 0.9),
            _player("P002", "GK B", "GK", 0.8),
            _player("P003", "GK C", "GK", 0.7),
        )
        selected = {"P001", "P002", "P003"}
        result = get_replacement_candidates(df, selected, "P001", top_n=3)

        assert result.candidates == []
        assert result.has_full_depth is False
        assert result.message is not None
        assert "GK" in result.message
        assert "exhausted" in result.message.lower()

    def test_thin_depth_returns_fewer_than_requested_with_explicit_message(self):
        # Only 1 replacement candidate exists, but top_n=3 was requested —
        # must return the 1 it has AND say so explicitly, never silently
        # truncate with no explanation (spec's Edge Cases requirement).
        df = _df(
            _player("P001", "Incumbent", "GK", 0.9),
            _player("P002", "Only bench GK", "GK", 0.5),
        )
        result = get_replacement_candidates(df, {"P001"}, "P001", top_n=3)

        assert len(result.candidates) == 1
        assert result.candidates[0].player_id == "P002"
        assert result.has_full_depth is False
        assert result.message is not None
        assert "1/3" in result.message


# ─────────────────────────────────────────────────────────────────────────
# Edge case — a flexible player counted correctly for two positions
# ─────────────────────────────────────────────────────────────────────────

class TestFlexiblePlayerCountedForTwoPositions:

    def test_secondary_position_candidate_appears_for_the_secondary_position(self):
        # Hakimi-style player: primary DEF, secondary includes a MID label.
        df = _df(
            _player("P001", "MID Incumbent", "MID", 0.85),
            _player(
                "P002", "Versatile DEF/MID", "DEF", 0.70,
                secondary="Right Midfield, Left-Back",
            ),
        )
        result = get_replacement_candidates(df, {"P001"}, "P001", top_n=3)
        assert len(result.candidates) == 1
        c = result.candidates[0]
        assert c.player_id == "P002"
        assert c.position == "DEF"          # candidate's own primary position, unchanged
        assert c.via_secondary_position is True

    def test_flexible_player_appears_as_primary_match_for_own_position_too(self):
        # The SAME versatile player must also show up as a normal
        # (non-secondary) DEF replacement candidate — proving they're
        # correctly counted for BOTH positions, not consumed by one.
        df = _df(
            _player("P001", "DEF Incumbent", "DEF", 0.85),
            _player(
                "P002", "Versatile DEF/MID", "DEF", 0.70,
                secondary="Right Midfield, Left-Back",
            ),
        )
        result = get_replacement_candidates(df, {"P001"}, "P001", top_n=3)
        assert len(result.candidates) == 1
        c = result.candidates[0]
        assert c.player_id == "P002"
        assert c.via_secondary_position is False  # primary-position match here, not secondary

    def test_primary_match_and_secondary_match_can_both_appear_ranked_together(self):
        df = _df(
            _player("P001", "MID Incumbent", "MID", 0.90),
            _player("P002", "Primary MID bench", "MID", 0.60),
            _player(
                "P003", "Versatile DEF/MID (stronger)", "DEF", 0.75,
                secondary="Central Midfield",
            ),
        )
        result = get_replacement_candidates(df, {"P001"}, "P001", top_n=3)
        ids = [c.player_id for c in result.candidates]
        # Ranked purely by DSSScore — the versatile DEF (0.75) outranks
        # the weaker primary-MID bench player (0.60).
        assert ids == ["P003", "P002"]
        by_id = {c.player_id: c for c in result.candidates}
        assert by_id["P003"].via_secondary_position is True
        assert by_id["P002"].via_secondary_position is False

    def test_missing_tm_secondary_positions_column_does_not_crash(self):
        # Older Gold runs may not have this column at all — must degrade
        # gracefully to primary-position-only matching, not raise.
        df = _df(
            _player("P001", "Incumbent", "DEF", 0.85),
            _player("P002", "Bench DEF", "DEF", 0.60),
        )
        assert "tm_secondary_positions" not in df.columns
        result = get_replacement_candidates(df, {"P001"}, "P001", top_n=3)
        assert [c.player_id for c in result.candidates] == ["P002"]


# ─────────────────────────────────────────────────────────────────────────
# Edge case — replacement stronger than incumbent must be flagged, not hidden
# ─────────────────────────────────────────────────────────────────────────

class TestStrongerThanIncumbentFlag:

    def test_stronger_candidate_is_flagged_and_surfaced_in_message(self):
        df = _df(
            _player("P001", "Weak Incumbent", "FWD", 0.50),
            _player("P002", "Actually Stronger Bench Player", "FWD", 0.70),
            _player("P003", "Weaker Bench Player", "FWD", 0.30),
        )
        result = get_replacement_candidates(df, {"P001"}, "P001", top_n=3)

        by_id = {c.player_id: c for c in result.candidates}
        assert by_id["P002"].stronger_than_incumbent is True
        assert by_id["P002"].gap_to_incumbent < 0
        assert by_id["P003"].stronger_than_incumbent is False

        assert result.message is not None
        assert "Actually Stronger Bench Player" in result.message
        assert "⚠️" in result.message

    def test_no_stronger_candidates_message_stays_none_when_depth_is_full(self):
        df = _df(
            _player("P001", "Strong Incumbent", "FWD", 0.90),
            _player("P002", "Weaker bench", "FWD", 0.50),
        )
        result = get_replacement_candidates(df, {"P001"}, "P001", top_n=1)
        assert result.message is None


# ─────────────────────────────────────────────────────────────────────────
# get_replacement_candidates_for_position() — bare-position mode
# ─────────────────────────────────────────────────────────────────────────

class TestGetReplacementCandidatesForPosition:

    def test_ranks_pool_with_no_specific_incumbent(self):
        df = _df(
            _player("P001", "Selected MID", "MID", 0.90),
            _player("P002", "Bench MID 1", "MID", 0.70),
            _player("P003", "Bench MID 2", "MID", 0.80),
        )
        result = get_replacement_candidates_for_position(df, {"P001"}, "MID", top_n=2)
        assert result.target_player_id is None
        assert [c.player_id for c in result.candidates] == ["P003", "P002"]
        assert result.incumbent_DSSScore == pytest.approx(0.80)  # top candidate's own score

    def test_unknown_position_string_returns_explanatory_message(self):
        df = _df(_player("P001", "A", "GK", 0.5))
        result = get_replacement_candidates_for_position(df, set(), "GOALKEEPER", top_n=3)
        assert result.candidates == []
        assert "Unknown position" in result.message


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
