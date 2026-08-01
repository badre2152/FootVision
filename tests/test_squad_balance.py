# tests/test_squad_balance.py
#
# Test suite for analytics/squad_balance.py — squad age/experience balance
# analysis (issue #16). Unit tests against small synthetic DataFrames, no
# network/MinIO/Gold run required. Mirrors tests/test_injury_predictor.py's
# style (fixed reference date, small synthetic rows) for consistency.

import sys
import os
from datetime import date

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics.squad_balance import (
    compute_age_distribution,
    _age_years,
    _age_tier,
    AGE_TIERS,
    TIER_ORDER,
    UNKNOWN_TIER,
)

AS_OF = date(2026, 6, 1)  # matches config.DECISION_DATE


def _row(player_name, dob, position, intl_caps=None):
    row = {"player_name": player_name, "wiki_dob": dob, "position": position}
    if intl_caps is not None:
        row["intl_caps"] = intl_caps
    return row


def _df(*rows):
    return pd.DataFrame(list(rows))


# ─────────────────────────────────────────────────────────────────────────
# _age_years / _age_tier — low-level helpers
# ─────────────────────────────────────────────────────────────────────────

class TestAgeHelpers:
    def test_age_years_exact(self):
        # Born exactly 23 years before AS_OF
        assert _age_years("2003-06-01", AS_OF) == pytest.approx(23.0, abs=0.01)

    def test_age_years_missing_dob_is_none(self):
        assert _age_years(None, AS_OF) is None
        assert _age_years(float("nan"), AS_OF) is None

    def test_age_years_unparseable_dob_is_none(self):
        assert _age_years("not-a-date", AS_OF) is None

    def test_tier_boundaries(self):
        # Tiers: Young <23, Prime 23-29, Experienced 30+
        assert _age_tier(22.99) == "Young (<23)"
        assert _age_tier(23.0) == "Prime (23-29)"
        assert _age_tier(29.99) == "Prime (23-29)"
        assert _age_tier(30.0) == "Experienced (30+)"
        assert _age_tier(38.0) == "Experienced (30+)"

    def test_tier_none_is_unknown(self):
        assert _age_tier(None) == UNKNOWN_TIER

    def test_tiers_sum_to_full_coverage(self):
        # AGE_TIERS must cover [0, inf) with no gaps — sanity check the
        # config itself, not just individual boundary points.
        assert AGE_TIERS[0][1] == 0.0
        for (_, _, hi), (_, lo_next, _) in zip(AGE_TIERS, AGE_TIERS[1:]):
            assert hi == lo_next
        assert AGE_TIERS[-1][2] is None


# ─────────────────────────────────────────────────────────────────────────
# compute_age_distribution — required columns
# ─────────────────────────────────────────────────────────────────────────

class TestRequiredColumns:
    def test_missing_wiki_dob_raises(self):
        df = pd.DataFrame({"position": ["GK"]})
        with pytest.raises(ValueError, match="wiki_dob"):
            compute_age_distribution(df, as_of=AS_OF)

    def test_missing_position_raises(self):
        df = pd.DataFrame({"wiki_dob": ["2000-01-01"]})
        with pytest.raises(ValueError, match="position"):
            compute_age_distribution(df, as_of=AS_OF)


# ─────────────────────────────────────────────────────────────────────────
# compute_age_distribution — full synthetic squad covering all tiers
# ─────────────────────────────────────────────────────────────────────────

class TestComputeAgeDistribution:
    @pytest.fixture
    def squad(self):
        return _df(
            _row("Young GK",        "2004-01-01", "GK",  intl_caps=5),   # ~22 -> Young
            _row("Prime GK",        "1999-01-01", "GK",  intl_caps=60),  # ~27 -> Prime
            _row("Unknown DOB GK",  None,          "GK",  intl_caps=10), # -> Unknown
            _row("Young DEF",       "2005-06-01", "DEF", intl_caps=2),   # ~21 -> Young
            _row("Prime DEF",       "1998-06-01", "DEF", intl_caps=70),  # ~28 -> Prime
            _row("Experienced DEF", "1993-01-01", "DEF", intl_caps=95),  # ~33 -> Experienced
            _row("Experienced FWD", "1990-01-01", "FWD", intl_caps=100), # ~36 -> Experienced
        )

    def test_overall_counts(self, squad):
        result = compute_age_distribution(squad, as_of=AS_OF)
        assert result["overall"]["Young (<23)"] == 2
        assert result["overall"]["Prime (23-29)"] == 2
        assert result["overall"]["Experienced (30+)"] == 2
        assert result["overall"][UNKNOWN_TIER] == 1
        assert sum(result["overall"].values()) == len(squad)

    def test_tier_order_has_unknown_last(self, squad):
        result = compute_age_distribution(squad, as_of=AS_OF)
        assert result["tier_order"] == TIER_ORDER
        assert result["tier_order"][-1] == UNKNOWN_TIER

    def test_by_position_breakdown(self, squad):
        result = compute_age_distribution(squad, as_of=AS_OF)
        gk = result["by_position"]["GK"]
        assert gk["Young (<23)"] == 1
        assert gk["Prime (23-29)"] == 1
        assert gk[UNKNOWN_TIER] == 1
        assert gk["Experienced (30+)"] == 0

        deF = result["by_position"]["DEF"]
        assert deF["Young (<23)"] == 1
        assert deF["Prime (23-29)"] == 1
        assert deF["Experienced (30+)"] == 1

        fwd = result["by_position"]["FWD"]
        assert fwd["Experienced (30+)"] == 1
        assert fwd["Young (<23)"] == 0

    def test_unknown_dob_not_silently_dropped(self, squad):
        result = compute_age_distribution(squad, as_of=AS_OF)
        assert result["n_unknown_dob"] == 1
        assert result["players_with_unknown_dob"] == ["Unknown DOB GK"]
        # Total across all positions must still equal the full squad size —
        # i.e. the unknown-DOB player is counted somewhere, not dropped.
        total_by_position = sum(
            sum(counts.values()) for counts in result["by_position"].values()
        )
        assert total_by_position == len(squad)

    def test_avg_caps_by_tier(self, squad):
        result = compute_age_distribution(squad, as_of=AS_OF)
        avg_caps = result["overall_avg_caps"]
        assert avg_caps is not None
        # Young tier: caps 5 and 2 -> avg 3.5
        assert avg_caps["Young (<23)"] == pytest.approx(3.5)
        # Experienced tier: caps 95 and 100 -> avg 97.5
        assert avg_caps["Experienced (30+)"] == pytest.approx(97.5)

    def test_missing_intl_caps_column_omits_avg_caps_only(self):
        df = _df(
            _row("A", "2000-01-01", "GK"),
            _row("B", "1995-01-01", "DEF"),
        )
        result = compute_age_distribution(df, as_of=AS_OF)
        assert result["overall_avg_caps"] is None
        # Counts still computed fine without intl_caps.
        assert sum(result["overall"].values()) == 2

    def test_missing_player_name_column_gives_empty_unknown_list(self):
        df = pd.DataFrame({
            "wiki_dob": [None],
            "position": ["GK"],
        })
        result = compute_age_distribution(df, as_of=AS_OF)
        assert result["n_unknown_dob"] == 1
        assert result["players_with_unknown_dob"] == []

    def test_as_of_is_isoformat_string(self, squad):
        result = compute_age_distribution(squad, as_of=AS_OF)
        assert result["as_of"] == "2026-06-01"

    def test_all_unknown_dob_squad(self):
        df = _df(
            _row("A", None, "GK", intl_caps=1),
            _row("B", "bad-date", "DEF", intl_caps=2),
        )
        result = compute_age_distribution(df, as_of=AS_OF)
        assert result["n_unknown_dob"] == 2
        assert result["overall"][UNKNOWN_TIER] == 2
        assert all(result["overall"][t] == 0 for t in TIER_ORDER if t != UNKNOWN_TIER)
