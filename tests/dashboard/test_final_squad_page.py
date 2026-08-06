import sys
import os

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

streamlit_testing = pytest.importorskip("streamlit.testing.v1")
AppTest = streamlit_testing.AppTest

PAGE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "dashboard", "pages", "03_final_squad.py",
)


def _write_gold_snapshot(gold_dir, run_date="20260801_000000", include_player_id=True):
    selected = [
        {"player_id": "P001", "player_name": "Selected GK", "position": "GK",
         "club": "Club A", "league": "Botola Pro", "Rank": 1, "SquadRank": 1,
         "DSSScore": 0.80, "DSSScore_pct": 80.0},
        {"player_id": "P002", "player_name": "Selected DEF", "position": "DEF",
         "club": "Club B", "league": "Botola Pro", "Rank": 2, "SquadRank": 2,
         "DSSScore": 0.75, "DSSScore_pct": 75.0},
        {"player_id": "P003", "player_name": "Selected MID", "position": "MID",
         "club": "Club C", "league": "Botola Pro", "Rank": 3, "SquadRank": 3,
         "DSSScore": 0.70, "DSSScore_pct": 70.0},
        {"player_id": "P004", "player_name": "Selected FWD", "position": "FWD",
         "club": "Club D", "league": "Botola Pro", "Rank": 4, "SquadRank": 4,
         "DSSScore": 0.65, "DSSScore_pct": 65.0},
    ]
    bench = [
        {"player_id": "P005", "player_name": "Bench GK", "position": "GK", "DSSScore": 0.40},
        {"player_id": "P006", "player_name": "Bench DEF", "position": "DEF", "DSSScore": 0.35},
        {"player_id": "P007", "player_name": "Versatile Bench",
         "position": "DEF", "DSSScore": 0.55,
         "tm_secondary_positions": "Central Midfield"},
    ]

    top26_df = pd.DataFrame(selected)
    if not include_player_id:
        top26_df = top26_df.drop(columns=["player_id"])
    top26_df.to_csv(gold_dir / f"top26_{run_date}.csv", index=False)

    full_df = pd.DataFrame(selected + bench)
    full_df.to_csv(gold_dir / f"players_scored_{run_date}.csv", index=False)

    return top26_df, full_df


@pytest.fixture
def gold_snapshot(tmp_path, monkeypatch):
    gold_dir = tmp_path / "gold"
    gold_dir.mkdir()
    import dashboard.data_loader as dl
    monkeypatch.setattr(dl, "GOLD_DIR", gold_dir)
    return gold_dir


class TestFinalSquadPageSmokeTest:

    def test_page_runs_without_exception(self, gold_snapshot):
        _write_gold_snapshot(gold_snapshot)
        at = AppTest.from_file(PAGE_PATH)
        at.session_state["_auth_ok"] = True
        at.run()
        assert not at.exception, f"Page raised: {[str(e) for e in at.exception]}"

    def test_replacement_expander_is_rendered_per_selected_player(self, gold_snapshot):
        _write_gold_snapshot(gold_snapshot)
        at = AppTest.from_file(PAGE_PATH)
        at.session_state["_auth_ok"] = True
        at.run()
        assert not at.exception

        expander_labels = [exp.label for exp in at.expander]
        assert expander_labels, "No expanders rendered at all."
        assert all(label == "🔄 Replacement options" for label in expander_labels)
        assert len(expander_labels) == 4

    def test_page_degrades_gracefully_when_top26_lacks_player_id(self, gold_snapshot):
        _write_gold_snapshot(gold_snapshot, include_player_id=False)
        at = AppTest.from_file(PAGE_PATH)
        at.session_state["_auth_ok"] = True
        at.run()

        assert not at.exception
        assert len(at.expander) == 0
        info_texts = " ".join(i.value for i in at.info)
        assert "player_id" in info_texts

    def test_no_gold_output_shows_error_and_stops_cleanly(self, gold_snapshot):
        at = AppTest.from_file(PAGE_PATH)
        at.session_state["_auth_ok"] = True
        at.run()
        assert not at.exception
        assert any("No Gold output found" in e.value for e in at.error)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))