import sys
import os
from datetime import date, timedelta
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ingestion.loaders.clubelo_loader import (
    compute_difficulty_factor,
    _match_club,
    build_league_quality_from_elo,
    load_clubelo_bronze,
    patch_league_quality,
)


class TestComputeDifficultyFactor:

    def test_baseline_elo_gives_factor_of_one(self):
        assert compute_difficulty_factor(1850) == pytest.approx(1.0, abs=0.01)

    def test_lower_elo_gives_lower_factor(self):
        assert compute_difficulty_factor(1200) < compute_difficulty_factor(1600)
        assert compute_difficulty_factor(1600) < compute_difficulty_factor(1850)

    def test_factor_is_always_positive(self):
        for elo in [500, 1000, 1500, 1850, 2200]:
            assert compute_difficulty_factor(elo) > 0


class TestMatchClub:

    ELO_ROWS = [
        {"Club": "Real Madrid", "Elo": "2000"},
        {"Club": "Real Betis", "Elo": "1750"},
        {"Club": "Raja Casablanca", "Elo": "1300"},
        {"Club": "Man United", "Elo": "1800"},
    ]

    def test_exact_match(self):
        result = _match_club("Real Betis", self.ELO_ROWS)
        assert result["Club"] == "Real Betis"

    def test_substring_match(self):
        result = _match_club("Raja Casablanca", self.ELO_ROWS)
        assert result["Club"] == "Raja Casablanca"

    def test_no_match_returns_none(self):
        result = _match_club("Completely Unknown FC", self.ELO_ROWS)
        assert result is None

    def test_keyword_fallback_matches_but_is_risky(self):
        result = _match_club("Manchester United", self.ELO_ROWS)
        assert result is not None
        assert result["Club"] == "Man United"

    def test_ambiguous_keyword_could_collide(self):
        ambiguous_rows = [
            {"Club": "Real Madrid", "Elo": "2000"},
            {"Club": "Real Betis", "Elo": "1750"},
        ]
        result = _match_club("Real Sociedad", ambiguous_rows)
        assert result is not None
        assert result["Club"] == "Real Madrid"


class TestBuildLeagueQualityFromElo:

    def test_higher_elo_league_gets_higher_factor(self):
        seed = pd.DataFrame({
            "club":   ["Real Madrid", "Raja Casablanca"],
            "league": ["La Liga", "Botola Pro"],
        })
        elo_rows = [
            {"Club": "Real Madrid", "Elo": "2000"},
            {"Club": "Raja Casablanca", "Elo": "1300"},
        ]
        result = build_league_quality_from_elo(seed, elo_rows)
        assert result["La Liga"] > result["Botola Pro"]

    def test_league_with_zero_matched_clubs_is_absent(self):
        seed = pd.DataFrame({
            "club":   ["Some Botola Club"],
            "league": ["Botola Pro"],
        })
        elo_rows = [{"Club": "Real Madrid", "Elo": "2000"}]
        result = build_league_quality_from_elo(seed, elo_rows)
        assert "Botola Pro" not in result


class TestDateRolloverFix:

    def test_first_of_month_does_not_crash(self):
        call_log = []

        def fake_fetch(run_date):
            call_log.append(run_date)
            return []

        with patch("ingestion.loaders.clubelo_loader._fetch_clubelo_csv", side_effect=fake_fetch), \
             patch("ingestion.loaders.clubelo_loader.date") as mock_date:
            mock_date.today.return_value = date(2026, 8, 1)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

            result = load_clubelo_bronze(seed_df=None, run_date="2026-08-01")

        assert result == {}
        assert len(call_log) == 2


class TestPatchLeagueQualityCoverageGap:

    def test_warns_about_leagues_not_covered_by_clubelo(self, caplog):
        seed = pd.DataFrame({
            "club":   ["A", "B", "C", "D"],
            "league": ["Ligue 1", "Botola Pro", "Saudi Pro League", "La Liga"],
        })
        clubelo_result = {"difficulty_by_league": {"Ligue 1": 0.901, "La Liga": 0.935}}

        with caplog.at_level("WARNING"):
            patch_league_quality(clubelo_result, seed_df=seed)

        warning_messages = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("Botola Pro" in m and "Saudi Pro League" in m for m in warning_messages), (
            "Expected a WARNING listing the uncovered leagues, got: " + str(warning_messages)
        )

    def test_no_warning_when_seed_df_not_provided(self, caplog):
        clubelo_result = {"difficulty_by_league": {"Ligue 1": 0.901}}
        with caplog.at_level("WARNING"):
            patch_league_quality(clubelo_result)

    def test_no_warning_when_all_leagues_covered(self, caplog):
        seed = pd.DataFrame({"club": ["A"], "league": ["Ligue 1"]})
        clubelo_result = {"difficulty_by_league": {"Ligue 1": 0.901}}
        with caplog.at_level("WARNING"):
            patch_league_quality(clubelo_result, seed_df=seed)
        warning_messages = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert not any("did NOT cover" in m for m in warning_messages)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))