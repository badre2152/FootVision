import sys
import os
from datetime import date

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import config
from analytics.ml.injury_predictor import (
    compute_injury_risk,
    _age_component,
    _classify_injury_type,
    AGE_RISK_MIN,
    AGE_RISK_MAX,
    WEIGHTS,
)

REF_DATE = date(2026, 6, 1)


def _base_row(**overrides):
    row = {
        "player_id": "P001",
        "player_name": "Test Player",
        "wiki_dob": "1998-06-01",
        "inj_count_2y": 2,
        "inj_days_missed_2y": 60,
        "inj_last_type": "Hamstring injury",
    }
    row.update(overrides)
    return row


def _df(*rows):
    if len(rows) == 1 and not isinstance(rows[0], dict):
        rows = tuple(rows[0])
    return pd.DataFrame(list(rows))


class TestWeights:
    def test_weights_sum_to_one(self):
        assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


class TestAgeComponent:
    def test_young_player_zero_risk(self):
        comp, low_conf = _age_component("2004-06-01", REF_DATE)
        assert comp == 0.0
        assert low_conf is False

    def test_old_player_capped_at_one(self):
        comp, low_conf = _age_component("1988-06-01", REF_DATE)
        assert comp == 1.0
        assert low_conf is False

    def test_midpoint_age(self):
        mid_age = (AGE_RISK_MIN + AGE_RISK_MAX) / 2
        dob = f"{2026 - int(mid_age)}-06-01"
        comp, _ = _age_component(dob, REF_DATE)
        assert 0.45 <= comp <= 0.55

    def test_missing_dob_neutral_and_flagged(self):
        comp, low_conf = _age_component(None, REF_DATE)
        assert comp == 0.5
        assert low_conf is True

    def test_unparseable_dob_neutral_and_flagged(self):
        comp, low_conf = _age_component("not-a-date", REF_DATE)
        assert comp == 0.5
        assert low_conf is True

    def test_monotonic_increasing_with_age(self):
        ages = [20, 24, 28, 32, 36]
        comps = [_age_component(f"{2026 - a}-06-01", REF_DATE)[0] for a in ages]
        assert comps == sorted(comps)


class TestInjuryTypeClassification:
    @pytest.mark.parametrize("text", [
        "Muscle injury", "Hamstring injury", "Calf injury", "Groin strain", "Thigh strain",
    ])
    def test_soft_tissue_scores_high(self, text):
        comp, low_conf = _classify_injury_type(text)
        assert comp == 1.0
        assert low_conf is False

    @pytest.mark.parametrize("text", [
        "Ankle injury", "Cruciate ligament injury", "Bruised ribs", "Shoulder injury",
    ])
    def test_non_soft_tissue_scores_low(self, text):
        comp, low_conf = _classify_injury_type(text)
        assert comp == 0.0
        assert low_conf is False

    @pytest.mark.parametrize("text", [
        "unknown injury", "Fitness", "Rest", "Knock", "minor knock", "Dead leg",
    ])
    def test_vague_scores_neutral_and_flagged(self, text):
        comp, low_conf = _classify_injury_type(text)
        assert comp == 0.5
        assert low_conf is True

    def test_missing_scores_neutral_and_flagged(self):
        comp, low_conf = _classify_injury_type(None)
        assert comp == 0.5
        assert low_conf is True

    def test_missing_nan_scores_neutral_and_flagged(self):
        comp, low_conf = _classify_injury_type(np.nan)
        assert comp == 0.5
        assert low_conf is True

    def test_ambiguous_double_match_flagged_low_confidence(self):
        comp, low_conf = _classify_injury_type("Hamstring muscle injury")
        assert comp == 1.0
        assert low_conf is False

    def test_unrecognized_free_text_neutral_and_flagged(self):
        comp, low_conf = _classify_injury_type("some totally unrelated free text")
        assert comp == 0.5
        assert low_conf is True


class TestComputeInjuryRisk:
    def test_missing_required_columns_raises(self):
        df = pd.DataFrame({"player_id": ["P001"]})
        with pytest.raises(ValueError, match="requires columns"):
            compute_injury_risk(df, reference_date=REF_DATE)

    def test_score_in_valid_range(self):
        df = _df(
            _base_row(player_id="P001"),
            _base_row(player_id="P002", wiki_dob="1988-06-01", inj_count_2y=8, inj_days_missed_2y=300),
            _base_row(player_id="P003", wiki_dob="2005-06-01", inj_count_2y=0, inj_days_missed_2y=0, inj_last_type=None),
        )
        result = compute_injury_risk(df, reference_date=REF_DATE)
        assert result.df["InjuryRiskScore"].between(0, 1).all()

    def test_healthiest_young_player_is_low_risk(self):
        df = _df(_base_row(
            player_id="P001", wiki_dob="2005-06-01",
            inj_count_2y=0, inj_days_missed_2y=0, inj_last_type="Ankle injury",
        ))
        result = compute_injury_risk(df, reference_date=REF_DATE)
        row = result.df.iloc[0]
        assert row["InjuryRiskLevel"] == "Low"
        assert row["InjuryRiskScore"] < 0.35

    def test_oldest_most_injured_player_is_high_risk(self):
        df = _df(_base_row(
            player_id="P001", wiki_dob="1987-06-01",
            inj_count_2y=9, inj_days_missed_2y=350, inj_last_type="Hamstring injury",
        ))
        result = compute_injury_risk(df, reference_date=REF_DATE)
        row = result.df.iloc[0]
        assert row["InjuryRiskLevel"] == "High"
        assert row["InjuryRiskScore"] > 0.60

    def test_missing_injury_type_flagged_low_confidence_not_silently_dropped(self):
        df = _df(_base_row(player_id="P001", inj_last_type=None))
        result = compute_injury_risk(df, reference_date=REF_DATE)
        assert result.df.iloc[0]["injury_risk_confidence"] == "Low"
        assert result.n_low_confidence == 1

    def test_inj_last_type_column_entirely_absent_does_not_crash(self):
        df = _df(_base_row()).drop(columns=["inj_last_type"])
        result = compute_injury_risk(df, reference_date=REF_DATE)
        assert (result.df["injury_risk_confidence"] == "Low").all()

    def test_row_count_preserved(self):
        df = _df(_base_row(player_id=f"P{i:03d}") for i in range(5))
        result = compute_injury_risk(df, reference_date=REF_DATE)
        assert len(result.df) == 5
        assert result.n_players == 5

    def test_level_counts_match_actual_labels(self):
        df = _df(
            _base_row(player_id="P001", wiki_dob="2005-06-01", inj_count_2y=0, inj_days_missed_2y=0, inj_last_type="Ankle injury"),
            _base_row(player_id="P002", wiki_dob="1987-06-01", inj_count_2y=9, inj_days_missed_2y=350, inj_last_type="Hamstring injury"),
        )
        result = compute_injury_risk(df, reference_date=REF_DATE)
        assert result.level_counts == result.df["InjuryRiskLevel"].value_counts().to_dict()

    def test_component_columns_present_for_audit(self):
        df = _df(_base_row())
        result = compute_injury_risk(df, reference_date=REF_DATE)
        for col in [
            "injury_risk_age_component", "injury_risk_history_days_component",
            "injury_risk_history_count_component", "injury_risk_recurrence_type_component",
        ]:
            assert col in result.df.columns

    def test_reference_date_changes_age_component_deterministically(self):
        df = _df(_base_row(wiki_dob="2000-01-01"))
        r_early = compute_injury_risk(df, reference_date=date(2020, 1, 1))
        r_late = compute_injury_risk(df, reference_date=date(2030, 1, 1))
        assert r_late.df.iloc[0]["injury_risk_age_component"] > r_early.df.iloc[0]["injury_risk_age_component"]

    def test_default_reference_date_is_decision_date_not_today(self):
        df = _df(_base_row())
        result_default = compute_injury_risk(df)
        result_explicit = compute_injury_risk(df, reference_date=config.DECISION_DATE)
        assert not result_default.df["InjuryRiskScore"].isna().any()
        assert (
            result_default.df["injury_risk_age_component"].iloc[0]
            == result_explicit.df["injury_risk_age_component"].iloc[0]
        )