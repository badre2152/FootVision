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
    assert DECISION_DATE == date(2026, 5, 25)


def test_injury_risk_defaults_to_decision_date_not_today():
    df = pd.DataFrame([{
        "player_id": "P001", "player_name": "X", "wiki_dob": "1998-05-25",
        "inj_count_2y": 0, "inj_days_missed_2y": 0, "inj_last_type": None,
    }])
    result = compute_injury_risk(df)
    age_component_low_conf = result.df["injury_risk_age_component"].iloc[0]
    assert age_component_low_conf != 0.5


@patch("ingestion.loaders.transfermarkt_injury_loader.requests.get")
def test_fetch_injury_history_excludes_records_after_as_of(mock_get):
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

    as_of = datetime(2026, 5, 25)
    result = fetch_injury_history(tm_id=1, slug="test", as_of=as_of)

    assert result["inj_days_missed_2y"] == 24