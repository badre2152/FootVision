# tests/test_competition_context.py
#
# Test suite for analytics/competition_context.py — Best XI Generator
# (formation-based, no opponent data — see module docstring for scope).

import sys
import os

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics.competition_context import (
    build_best_xi,
    compare_formations,
    tactical_fit_vs_opponent,
    FORMATIONS,
)


def _make_pool(counts: dict[str, int], score_start: float = 0.90) -> pd.DataFrame:
    """Builds a synthetic candidate pool with `counts[position]` players at
    each position, DSSScore descending within each position so rank order
    is deterministic and easy to assert on."""
    rows = []
    for position, n in counts.items():
        for i in range(n):
            rows.append({
                "player_id": f"{position}{i:02d}",
                "player_name": f"{position}_Player_{i}",
                "position": position,
                "DSSScore": score_start - i * 0.01,
            })
    return pd.DataFrame(rows)


FULL_POOL = _make_pool({"GK": 3, "DEF": 9, "MID": 9, "FWD": 5})  # mirrors real top26 composition


class TestFormationValidation:
    def test_unknown_formation_raises(self):
        with pytest.raises(ValueError, match="Unknown formation"):
            build_best_xi(FULL_POOL, formation="7-2-1")

    def test_missing_required_columns_raises(self):
        df = FULL_POOL.drop(columns=["DSSScore"])
        with pytest.raises(ValueError, match="missing required columns"):
            build_best_xi(df, formation="4-3-3")

    def test_all_formations_are_known_and_have_11_outfield_slots_check(self):
        for name, slots in FORMATIONS.items():
            assert sum(slots.values()) == 10, f"{name} outfield slots should sum to 10 (+1 GK = 11)"


class TestBestXI:
    def test_starting_xi_has_11_players_when_pool_is_deep_enough(self):
        result = build_best_xi(FULL_POOL, formation="4-3-3")
        assert len(result.starting_xi) == 11
        assert not result.shortfalls

    @pytest.mark.parametrize("formation", list(FORMATIONS))
    def test_every_formation_fills_from_real_top26_shaped_pool(self, formation):
        result = build_best_xi(FULL_POOL, formation=formation)
        assert len(result.starting_xi) == 11
        assert not result.shortfalls

    def test_picks_highest_dssscore_players_per_position(self):
        result = build_best_xi(FULL_POOL, formation="4-3-3")
        def_in_xi = result.starting_xi[result.starting_xi["position"] == "DEF"]
        assert len(def_in_xi) == 4
        # top 4 DEF by score are DEF00..DEF03 (score_start down by 0.01 each)
        assert set(def_in_xi["player_id"]) == {"DEF00", "DEF01", "DEF02", "DEF03"}

    def test_bench_excludes_starters_and_is_sorted(self):
        result = build_best_xi(FULL_POOL, formation="4-3-3")
        assert set(result.bench["player_id"]).isdisjoint(set(result.starting_xi["player_id"]))
        assert len(result.bench) == len(FULL_POOL) - 11

    def test_shortfall_reported_when_pool_too_shallow_and_no_fallback(self):
        thin_pool = _make_pool({"GK": 1, "DEF": 2, "MID": 9, "FWD": 5})  # only 2 DEF, formation needs 4
        result = build_best_xi(thin_pool, formation="4-3-3")
        assert result.shortfalls.get("DEF") == 2
        assert len(result.starting_xi) == 9  # 11 - 2 short

    def test_fallback_pool_fills_shortfall_and_is_flagged(self):
        thin_pool = _make_pool({"GK": 1, "DEF": 2, "MID": 9, "FWD": 5})
        wide_pool = _make_pool({"GK": 1, "DEF": 9, "MID": 9, "FWD": 5}, score_start=0.5)  # lower scores, deeper DEF
        result = build_best_xi(thin_pool, formation="4-3-3", fallback_pool=wide_pool)
        assert not result.shortfalls
        assert len(result.starting_xi) == 11
        assert result.used_fallback_pool is True

    def test_no_shortfall_means_fallback_flag_false(self):
        result = build_best_xi(FULL_POOL, formation="4-3-3", fallback_pool=FULL_POOL)
        assert result.used_fallback_pool is False


class TestCompareFormations:
    def test_returns_one_row_per_formation_sorted_by_total_score(self):
        table = compare_formations(FULL_POOL)
        assert len(table) == len(FORMATIONS)
        assert list(table["total_DSSScore"]) == sorted(table["total_DSSScore"], reverse=True)

    def test_subset_of_formations_respected(self):
        table = compare_formations(FULL_POOL, formations=["4-3-3", "3-5-2"])
        assert set(table["formation"]) == {"4-3-3", "3-5-2"}


class TestOpponentStubHonesty:
    def test_tactical_fit_vs_opponent_raises_not_implemented_with_explanation(self):
        with pytest.raises(NotImplementedError, match="no opponent-team data source"):
            tactical_fit_vs_opponent()
