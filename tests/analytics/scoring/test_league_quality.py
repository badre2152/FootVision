import sys
import os
from datetime import date
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import analytics.scoring.league_quality as lq


@pytest.fixture(autouse=True)
def _clear_elo_cache():
    lq._ELO_SNAPSHOT_CACHE.clear()
    yield
    lq._ELO_SNAPSHOT_CACHE.clear()


FAKE_SNAPSHOT = {
    "run_date": "2026-05-25",
    "difficulty_by_league": {
        "La Liga": 0.985,
        "Ligue 1": 0.912,
    },
}


class TestComputeEloBasedCoefficient:

    def test_covered_big5_league_close_to_one(self):
        with patch(
            "clients.minio_client.read_latest_json",
            return_value=FAKE_SNAPSHOT,
        ):
            result = lq.compute_elo_based_coefficient("La Liga", as_of_date=date(2026, 5, 25))
        assert result == pytest.approx(0.985, abs=0.01)
        assert result == pytest.approx(1.0, abs=0.05)

    def test_uncovered_league_returns_none(self):
        with patch("clients.minio_client.read_latest_json", return_value=FAKE_SNAPSHOT):
            result = lq.compute_elo_based_coefficient("Botola Pro", as_of_date=date(2026, 5, 25))
        assert result is None

    def test_no_snapshot_available_returns_none(self):
        with patch("clients.minio_client.read_latest_json", return_value=None):
            result = lq.compute_elo_based_coefficient("La Liga", as_of_date=date(2026, 5, 25))
        assert result is None

    def test_defaults_to_decision_date_not_today(self):
        from config import DECISION_DATE

        captured_prefixes = []

        def fake_read(bucket, prefix):
            captured_prefixes.append(prefix)
            return FAKE_SNAPSHOT

        with patch("clients.minio_client.read_latest_json", side_effect=fake_read):
            lq.compute_elo_based_coefficient("La Liga")

        expected_prefix = f"clubelo/clubelo_{DECISION_DATE.strftime('%Y%m%d')}"
        assert captured_prefixes == [expected_prefix]

    def test_none_league_name_returns_none(self):
        assert lq.compute_elo_based_coefficient(None) is None
        assert lq.compute_elo_based_coefficient("") is None


class TestGetLeagueFactorPriorityChain:

    def test_tier1_elo_wins_over_manual_table(self):
        with patch("clients.minio_client.read_latest_json", return_value=FAKE_SNAPSHOT):
            factor = lq.get_league_factor("La Liga", as_of_date=date(2026, 5, 25))
        assert factor == pytest.approx(0.985, abs=0.001)

    def test_tier2_manual_table_when_not_elo_covered(self):
        with patch("clients.minio_client.read_latest_json", return_value=FAKE_SNAPSHOT):
            factor = lq.get_league_factor("Botola Pro", as_of_date=date(2026, 5, 25))
        assert factor == pytest.approx(0.72, abs=0.001)

    def test_tier3_default_when_totally_unknown(self):
        with patch("clients.minio_client.read_latest_json", return_value=None):
            factor = lq.get_league_factor("Some Random League Nobody Tracks")
        assert factor == lq.LEAGUE_DIFFICULTY["_default"]

    def test_no_bronze_snapshot_falls_back_to_manual_table(self):
        with patch("clients.minio_client.read_latest_json", return_value=None):
            factor = lq.get_league_factor("Premier League")
        assert factor == 1.00

    def test_none_or_empty_league_returns_default(self):
        assert lq.get_league_factor(None) == lq.LEAGUE_DIFFICULTY["_default"]
        assert lq.get_league_factor("") == lq.LEAGUE_DIFFICULTY["_default"]


class TestBackwardCompatibility:

    def test_adjust_stat_for_league_still_works_without_bronze_data(self):
        with patch("clients.minio_client.read_latest_json", return_value=None):
            adjusted = lq.adjust_stat_for_league(1.0, "Premier League")
        assert adjusted == pytest.approx(1.0)

    def test_adjust_gk_ga_per90_still_works(self):
        with patch("clients.minio_client.read_latest_json", return_value=None):
            adjusted = lq.adjust_gk_ga_per90(0.38, "Botola Pro")
        assert adjusted == pytest.approx(0.38 / 0.72, abs=0.001)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))