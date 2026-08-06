import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

streamlit_testing = pytest.importorskip("streamlit.testing.v1")
AppTest = streamlit_testing.AppTest

PAGE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "dashboard", "pages", "01_player_analysis.py",
)


def _write_players_scored(gold_dir, run_date="20260801_000000"):
    df = pd.DataFrame([
        {"player_id": "P001", "player_name": "Test GK", "position": "GK", "club": "Club A",
         "DSSScore": 0.80, "Rank": 1,
         "PerformanceScore": 0.7, "FormScore": 0.6, "MentalScore": 0.5,
         "AvailabilityScore": 0.8, "InternationalScore": 0.6,
         "GA_per90": 1.0, "GK_saves_per90": 2.8},
        {"player_id": "P002", "player_name": "Test CB", "position": "DEF", "club": "Club B",
         "DSSScore": 0.70, "Rank": 2,
         "PerformanceScore": 0.6, "FormScore": 0.5, "MentalScore": 0.5,
         "AvailabilityScore": 0.7, "InternationalScore": 0.5,
         "Tkl_per90": 2.0, "Int_per90": 1.5, "Clr_per90": 4.0},
    ])
    df.to_csv(gold_dir / f"players_scored_{run_date}.csv", index=False)
    return df


@pytest.fixture
def gold_snapshot(tmp_path, monkeypatch):
    gold_dir = tmp_path / "gold"
    gold_dir.mkdir()
    import dashboard.data_loader as dl
    monkeypatch.setattr(dl, "GOLD_DIR", gold_dir)
    return gold_dir


class TestPlayerAnalysisPageSmokeTest:

    def test_page_runs_without_exception(self, gold_snapshot):
        _write_players_scored(gold_snapshot)
        at = AppTest.from_file(PAGE_PATH)
        at.session_state["_auth_ok"] = True
        at.run()
        assert not at.exception, f"Page raised: {[str(e) for e in at.exception]}"

    def test_international_reference_section_renders_for_gk(self, gold_snapshot):
        _write_players_scored(gold_snapshot)
        at = AppTest.from_file(PAGE_PATH)
        at.session_state["_auth_ok"] = True
        at.run()
        assert not at.exception

        subheader_texts = " ".join(s.value for s in at.subheader)
        assert "International Reference" in subheader_texts
        markdown_texts = " ".join(m.value for m in at.markdown)
        assert "Alisson Becker" in markdown_texts

    def test_no_gold_output_shows_error_and_stops_cleanly(self, gold_snapshot):
        at = AppTest.from_file(PAGE_PATH)
        at.session_state["_auth_ok"] = True
        at.run()
        assert not at.exception
        assert any("No Gold output found" in e.value for e in at.error)