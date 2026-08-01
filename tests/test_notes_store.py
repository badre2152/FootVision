# tests/test_notes_store.py
#
# Regression tests for 20_coach_notes.md.
#
# All tests monkeypatch dashboard.notes_store.DATA_DIR/NOTES_PATH to a
# pytest tmp_path — NEVER touches the real data/coach_notes.json on disk.

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard import notes_store


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """Points notes_store at a throwaway directory for this test only."""
    data_dir = tmp_path / "data"
    notes_path = data_dir / "coach_notes.json"
    monkeypatch.setattr(notes_store, "DATA_DIR", data_dir)
    monkeypatch.setattr(notes_store, "NOTES_PATH", notes_path)
    return notes_path


class TestGetNote:

    def test_returns_none_when_no_note_exists(self, isolated_store):
        assert notes_store.get_note("player_123") is None

    def test_returns_none_when_file_does_not_exist_yet(self, isolated_store):
        assert not isolated_store.exists()
        assert notes_store.get_note("anyone") is None


class TestSaveNote:

    def test_save_then_get_roundtrip(self, isolated_store):
        """Acceptance criterion: a note persists after 'closing and
        reopening the dashboard' — modeled here as a fresh get_note() call
        with no shared in-memory state, i.e. a true round-trip through disk."""
        notes_store.save_note("player_1", "Great engine, drops off after 70'.", author="Coach Regragui")

        note = notes_store.get_note("player_1")
        assert note is not None
        assert note["text"] == "Great engine, drops off after 70'."
        assert note["author"] == "Coach Regragui"
        assert "updated_at" in note

    def test_default_author_used_when_none_given(self, isolated_store):
        notes_store.save_note("player_2", "Leadership in the dressing room.")
        note = notes_store.get_note("player_2")
        assert note["author"] == notes_store.DEFAULT_AUTHOR

    def test_player_id_coerced_to_string_for_lookup(self, isolated_store):
        """player_id may arrive as an int from a DataFrame row — must still
        round-trip correctly since JSON object keys are always strings."""
        notes_store.save_note(42, "Test note")
        assert notes_store.get_note(42) is not None
        assert notes_store.get_note("42") is not None

    def test_last_write_wins_on_same_player(self, isolated_store):
        """Documented concurrency behavior (spec Edge Cases): the second
        save simply overwrites the first, no merge, no error."""
        notes_store.save_note("player_3", "First observation.", author="A")
        notes_store.save_note("player_3", "Second, more recent observation.", author="B")

        note = notes_store.get_note("player_3")
        assert note["text"] == "Second, more recent observation."
        assert note["author"] == "B"

    def test_saving_one_player_does_not_touch_another(self, isolated_store):
        notes_store.save_note("player_a", "Note A")
        notes_store.save_note("player_b", "Note B")

        assert notes_store.get_note("player_a")["text"] == "Note A"
        assert notes_store.get_note("player_b")["text"] == "Note B"

    def test_empty_text_is_saved_explicitly_not_dropped(self, isolated_store):
        """An explicitly-cleared note is still a real record (with a
        timestamp/author showing who cleared it), not silently deleted."""
        notes_store.save_note("player_4", "", author="Coach")
        note = notes_store.get_note("player_4")
        assert note is not None
        assert note["text"] == ""


class TestGetAllNotes:

    def test_returns_all_saved_notes(self, isolated_store):
        notes_store.save_note("p1", "note 1")
        notes_store.save_note("p2", "note 2")

        all_notes = notes_store.get_all_notes()
        assert set(all_notes.keys()) == {"p1", "p2"}

    def test_returns_empty_dict_when_no_notes_saved(self, isolated_store):
        assert notes_store.get_all_notes() == {}


class TestCorruptOrMalformedFile:

    def test_corrupt_json_does_not_crash_get_note(self, isolated_store):
        isolated_store.parent.mkdir(parents=True, exist_ok=True)
        isolated_store.write_text("{not valid json", encoding="utf-8")
        assert notes_store.get_note("anyone") is None

    def test_non_dict_json_is_ignored(self, isolated_store):
        isolated_store.parent.mkdir(parents=True, exist_ok=True)
        isolated_store.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
        assert notes_store.get_all_notes() == {}


class TestArchitecturalSeparationFromPipeline:

    def test_notes_dir_is_not_bronze_silver_or_gold(self):
        """The acceptance criterion 'notes survive a full pipeline re-run
        untouched' depends entirely on data/ never overlapping with the
        directories run_bronze.py/run_silver.py/run_gold.py write to."""
        from dashboard.data_loader import GOLD_DIR, BRONZE_DIR

        assert notes_store.DATA_DIR != GOLD_DIR
        assert notes_store.DATA_DIR != BRONZE_DIR
        assert notes_store.DATA_DIR.name == "data"
        assert not str(notes_store.DATA_DIR).startswith(str(GOLD_DIR))
        assert not str(notes_store.DATA_DIR).startswith(str(BRONZE_DIR))

    def test_notes_survive_deletion_and_rebuild_of_gold_dir(self, isolated_store, tmp_path):
        """Simulates a pipeline re-run (gold/ wiped and rewritten) and
        confirms it has zero effect on an already-saved note."""
        fake_gold_dir = tmp_path / "gold"
        fake_gold_dir.mkdir()
        (fake_gold_dir / "players_scored_20260101_000000.csv").write_text("player_id,DSSScore\n1,0.9\n")

        notes_store.save_note("player_9", "Survives pipeline re-runs.")

        # Simulate run_gold.py wiping and rewriting gold/ entirely.
        for f in fake_gold_dir.iterdir():
            f.unlink()
        (fake_gold_dir / "players_scored_20260102_000000.csv").write_text("player_id,DSSScore\n1,0.95\n")

        note = notes_store.get_note("player_9")
        assert note is not None
        assert note["text"] == "Survives pipeline re-runs."
