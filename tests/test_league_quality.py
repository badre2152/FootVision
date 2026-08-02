# tests/test_league_quality.py
#
# Offline unit tests for analytics/scoring/league_quality.py's #14 update:
# the new 3-tier COALESCE-style priority chain in get_league_factor() —
#   1. compute_elo_based_coefficient() (real ClubElo Bronze snapshot)
#   2. LEAGUE_DIFFICULTY (manual table)
#   3. LEAGUE_DIFFICULTY["_default"]
#
# No network / MinIO calls: read_latest_json is monkeypatched to return an
# in-memory fake Bronze snapshot, same shape clubelo_loader.load_clubelo_bronze()
# actually uploads ({"difficulty_by_league": {...}, ...}).

import sys
import os
from datetime import date
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analytics.scoring.league_quality as lq


@pytest.fixture(autouse=True)
def _clear_elo_cache():
    """Every test gets a clean per-process Elo snapshot cache."""
    lq._ELO_SNAPSHOT_CACHE.clear()
    yield
    lq._ELO_SNAPSHOT_CACHE.clear()


FAKE_SNAPSHOT = {
    "run_date": "2026-05-25",
    "difficulty_by_league": {
        # A Big-5 league should compute close to 1.0 — acceptance criterion.
        "La Liga": 0.985,
        "Ligue 1": 0.912,
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# compute_elo_based_coefficient — tier 1
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeEloBasedCoefficient:

    def test_covered_big5_league_close_to_one(self):
        """
        Acceptance criterion from 14_clubelo_league_quality_link.md:
        for a league ClubElo DOES cover (Big-5), the coefficient should
        compute close to 1.0, traceable to a real Elo-derived number —
        not the hardcoded manual guess.
        """
        with patch(
            "clients.minio_client.read_latest_json",
            return_value=FAKE_SNAPSHOT,
        ):
            result = lq.compute_elo_based_coefficient("La Liga", as_of_date=date(2026, 5, 25))
        assert result == pytest.approx(0.985, abs=0.01)
        assert result == pytest.approx(1.0, abs=0.05)

    def test_uncovered_league_returns_none(self):
        """
        Botola Pro (and Saudi/UAE/Egyptian/Greek leagues) are NOT in
        ClubElo's tracked competitions — compute_elo_based_coefficient()
        must return None (not a guess), so the caller can fall through
        to the manual table.
        """
        with patch("clients.minio_client.read_latest_json", return_value=FAKE_SNAPSHOT):
            result = lq.compute_elo_based_coefficient("Botola Pro", as_of_date=date(2026, 5, 25))
        assert result is None

    def test_no_snapshot_available_returns_none(self):
        """No Bronze artifact for this date at all -> None, not a crash."""
        with patch("clients.minio_client.read_latest_json", return_value=None):
            result = lq.compute_elo_based_coefficient("La Liga", as_of_date=date(2026, 5, 25))
        assert result is None

    def test_defaults_to_decision_date_not_today(self):
        """
        Respects DECISION_DATE (spec #1): omitting as_of_date must resolve
        to config.DECISION_DATE, never date.today().
        """
        from config import DECISION_DATE

        captured_prefixes = []

        def fake_read(bucket, prefix):
            captured_prefixes.append(prefix)
            return FAKE_SNAPSHOT

        with patch("clients.minio_client.read_latest_json", side_effect=fake_read):
            lq.compute_elo_based_coefficient("La Liga")  # no as_of_date passed

        expected_prefix = f"clubelo/clubelo_{DECISION_DATE.strftime('%Y%m%d')}"
        assert captured_prefixes == [expected_prefix]

    def test_none_league_name_returns_none(self):
        assert lq.compute_elo_based_coefficient(None) is None
        assert lq.compute_elo_based_coefficient("") is None


# ─────────────────────────────────────────────────────────────────────────────
# get_league_factor — full 3-tier priority chain
# ─────────────────────────────────────────────────────────────────────────────

class TestGetLeagueFactorPriorityChain:

    def test_tier1_elo_wins_over_manual_table(self):
        """
        La Liga is hardcoded 1.00 in LEAGUE_DIFFICULTY, but if a covered
        Elo snapshot gives a different real number, tier 1 must win.
        """
        with patch("clients.minio_client.read_latest_json", return_value=FAKE_SNAPSHOT):
            factor = lq.get_league_factor("La Liga", as_of_date=date(2026, 5, 25))
        assert factor == pytest.approx(0.985, abs=0.001)

    def test_tier2_manual_table_when_not_elo_covered(self):
        """
        Botola Pro isn't in the fake Elo snapshot -> falls through to the
        manual LEAGUE_DIFFICULTY table (0.72), exactly as documented.
        """
        with patch("clients.minio_client.read_latest_json", return_value=FAKE_SNAPSHOT):
            factor = lq.get_league_factor("Botola Pro", as_of_date=date(2026, 5, 25))
        assert factor == pytest.approx(0.72, abs=0.001)

    def test_tier3_default_when_totally_unknown(self):
        with patch("clients.minio_client.read_latest_json", return_value=None):
            factor = lq.get_league_factor("Some Random League Nobody Tracks")
        assert factor == lq.LEAGUE_DIFFICULTY["_default"]

    def test_no_bronze_snapshot_falls_back_to_manual_table(self):
        """No ClubElo Bronze artifact at all -> tier 2 (manual table) still works."""
        with patch("clients.minio_client.read_latest_json", return_value=None):
            factor = lq.get_league_factor("Premier League")
        assert factor == 1.00

    def test_none_or_empty_league_returns_default(self):
        assert lq.get_league_factor(None) == lq.LEAGUE_DIFFICULTY["_default"]
        assert lq.get_league_factor("") == lq.LEAGUE_DIFFICULTY["_default"]


# ─────────────────────────────────────────────────────────────────────────────
# Backward compatibility — adjust_stat_for_league / adjust_gk_ga_per90
# ─────────────────────────────────────────────────────────────────────────────

class TestBackwardCompatibility:

    def test_adjust_stat_for_league_still_works_without_bronze_data(self):
        """
        Existing call sites (performance_score.py) call
        adjust_stat_for_league(value, league) with no as_of_date — must
        keep working exactly as before when no Elo snapshot is available.
        """
        with patch("clients.minio_client.read_latest_json", return_value=None):
            adjusted = lq.adjust_stat_for_league(1.0, "Premier League")
        assert adjusted == pytest.approx(1.0)

    def test_adjust_gk_ga_per90_still_works(self):
        with patch("clients.minio_client.read_latest_json", return_value=None):
            adjusted = lq.adjust_gk_ga_per90(0.38, "Botola Pro")
        # factor=0.72 -> 0.38 / 0.72
        assert adjusted == pytest.approx(0.38 / 0.72, abs=0.001)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
