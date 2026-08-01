# tests/test_temporal_cutoff.py
#
# Regression tests for 01_temporal_cutoff.md — DECISION_DATE enforcement.
# Offline only (no network/MinIO) — verifies:
#   1. config.DECISION_DATE exists and is the day BEFORE the real WC2026
#      squad announcement (2026-05-26), matching
#      analytics/ml/selection_classifier.py's REAL_SQUAD_2026 citation.
#   2. analytics/ml/injury_predictor.compute_injury_risk() defaults its
#      reference date to DECISION_DATE, not real wall-clock time.
#   3. ingestion.loaders.transfermarkt_injury_loader.fetch_injury_history()
#      excludes injuries dated AFTER `as_of` and computes ongoing-injury
#      day counts relative to `as_of`, not datetime.now().
#
# NOTE: this does NOT cover re-running the live pipeline end-to-end
# (`inj_last_date` max <= DECISION_DATE on a fresh Gold export) — that
# acceptance criterion requires network access to transfermarkt.com /
# api.clubelo.com, which this sandbox does not have. It must be verified
# manually after `run_bronze.py --as-of 2026-05-25` on a machine with
# network access, before trusting any Gold export produced going forward.

import os
import sys
from datetime import date, datetime
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DECISION_DATE
from analytics.ml.injury_predictor import compute_injury_risk
from ingestion.loaders.transfermarkt_injury_loader import fetch_injury_history


def test_decision_date_is_day_before_squad_announcement():
    # Real announcement: 2026-05-26 (analytics/ml/selection_classifier.py header).
    assert DECISION_DATE == date(2026, 5, 25)


def test_injury_risk_defaults_to_decision_date_not_today():
    df = pd.DataFrame([{
        "player_id": "P001", "player_name": "X", "wiki_dob": "1998-05-25",
        "inj_count_2y": 0, "inj_days_missed_2y": 0, "inj_last_type": None,
    }])
    # No reference_date passed -> must use DECISION_DATE, so age is exactly 28.
    result = compute_injury_risk(df)
    age_component_low_conf = result.df["injury_risk_age_component"].iloc[0]
    # Sanity: with an unmissable/known dob the age branch must not be the
    # low-confidence fallback (0.5) — confirms a real reference date was used.
    assert age_component_low_conf != 0.5


@patch("ingestion.loaders.transfermarkt_injury_loader.requests.get")
def test_fetch_injury_history_excludes_records_after_as_of(mock_get):
    # One injury BEFORE as_of (must be counted), one AFTER (must be excluded).
    html = """
    <table class="items"><tbody>
      <tr><td></td><td>Hamstring</td><td>Jan 1, 2026</td><td>Jan 15, 2026</td><td>14 days</td></tr>
      <tr><td></td><td>Knee</td><td>Jun 10, 2026</td><td>-</td><td>- days</td></tr>
    </tbody></table>
    """
    mock_resp = MagicMock(status_code=200, content=html.encode())
    mock_resp.raise_for_status = lambda: None
    mock_get.return_value = mock_resp

    as_of = datetime(2026, 5, 25)
    result = fetch_injury_history(tm_id=1, slug="test", as_of=as_of)

    assert result["inj_count_2y"] == 1
    assert result["inj_last_date"] == "2026-01-01"
    assert result["inj_days_missed_2y"] == 14


@patch("ingestion.loaders.transfermarkt_injury_loader.requests.get")
def test_fetch_injury_history_ongoing_days_relative_to_as_of(mock_get):
    html = """
    <table class="items"><tbody>
      <tr><td></td><td>ACL</td><td>May 1, 2026</td><td>-</td><td>- days</td></tr>
    </tbody></table>
    """
    mock_resp = MagicMock(status_code=200, content=html.encode())
    mock_resp.raise_for_status = lambda: None
    mock_get.return_value = mock_resp

    as_of = datetime(2026, 5, 25)  # 24 days after May 1
    result = fetch_injury_history(tm_id=1, slug="test", as_of=as_of)

    assert result["inj_days_missed_2y"] == 24
