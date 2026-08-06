import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dashboard import notes_store


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    notes_path = data_dir / "coach_notes.db"
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
        notes_store.save_note(42, "Test note")
        assert notes_store.get_note(42) is not None
        assert notes_store.get_note("42") is not None

    def test_last_write_wins_on_same_player(self, isolated_store):
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


class TestCorruptOrMalformedLegacyFile:

    def test_corrupt_legacy_json_does_not_crash_get_note(self, isolated_store):
        legacy_path = isolated_store.parent / "coach_notes.json"
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.write_text("{not valid json", encoding="utf-8")
        assert notes_store.get_note("anyone") is None

    def test_non_dict_legacy_json_is_ignored(self, isolated_store):
        legacy_path = isolated_store.parent / "coach_notes.json"
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
        assert notes_store.get_all_notes() == {}


class TestOptimisticConcurrency:

    def test_new_note_has_version_1(self, isolated_store):
        record = notes_store.save_note("player_5", "First note ever.")
        assert record["version"] == 1

    def test_version_increments_on_each_save(self, isolated_store):
        notes_store.save_note("player_6", "v1", expected_version=None)
        r2 = notes_store.save_note("player_6", "v2", expected_version=1)
        r3 = notes_store.save_note("player_6", "v3", expected_version=2)
        assert r2["version"] == 2
        assert r3["version"] == 3

    def test_save_without_expected_version_still_overwrites_unconditionally(self, isolated_store):
        notes_store.save_note("player_7", "First.", expected_version=None)
        record = notes_store.save_note("player_7", "Second, no version check.", expected_version=None)
        assert record["text"] == "Second, no version check."
        assert notes_store.get_note("player_7")["text"] == "Second, no version check."

    def test_conflicting_save_raises_and_does_not_overwrite(self, isolated_store):
        notes_store.save_note("player_8", "Coach A's original note.", author="A")
        notes_store.save_note("player_8", "Coach A's follow-up edit.", author="A", expected_version=1)

        with pytest.raises(notes_store.NoteConflictError) as exc_info:
            notes_store.save_note(
                "player_8", "Coach B's edit, based on stale version 1.",
                author="B", expected_version=1,
            )

        current = notes_store.get_note("player_8")
        assert current["text"] == "Coach A's follow-up edit."
        assert current["author"] == "A"
        assert current["version"] == 2

        assert exc_info.value.current["text"] == "Coach A's follow-up edit."
        assert exc_info.value.player_id == "player_8"

    def test_expected_version_0_conflicts_if_note_was_created_meanwhile(self, isolated_store):
        notes_store.save_note("player_10", "Someone got there first.", author="A")

        with pytest.raises(notes_store.NoteConflictError):
            notes_store.save_note("player_10", "I thought this was empty.", author="B", expected_version=0)

    def test_expected_version_matching_current_succeeds(self, isolated_store):
        notes_store.save_note("player_11", "Original.", author="A")
        record = notes_store.save_note("player_11", "Correctly-based edit.", author="B", expected_version=1)
        assert record["version"] == 2
        assert record["text"] == "Correctly-based edit."


class TestLegacyJsonMigration:

    def test_existing_legacy_notes_are_migrated_on_first_read(self, isolated_store):
        legacy_path = isolated_store.parent / "coach_notes.json"
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.write_text(json.dumps({
            "P003": {"author": "brahim", "text": "best keeper of alll",
                      "updated_at": "2026-08-01T22:10:00+00:00"},
            "P024": {"author": "hajji", "text": "picks the matches",
                      "updated_at": "2026-08-03T00:12:37+00:00"},
        }), encoding="utf-8")

        note = notes_store.get_note("P003")
        assert note is not None
        assert note["text"] == "best keeper of alll"
        assert note["author"] == "brahim"
        assert note["version"] == 1

        all_notes = notes_store.get_all_notes()
        assert set(all_notes.keys()) == {"P003", "P024"}

    def test_legacy_file_is_left_untouched_after_migration(self, isolated_store):
        legacy_path = isolated_store.parent / "coach_notes.json"
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        original_content = json.dumps({"P001": {"author": "x", "text": "y", "updated_at": "2026-01-01T00:00:00+00:00"}})
        legacy_path.write_text(original_content, encoding="utf-8")

        notes_store.get_all_notes()

        assert legacy_path.exists()
        assert legacy_path.read_text(encoding="utf-8") == original_content

    def test_migration_does_not_reimport_after_new_notes_exist(self, isolated_store):
        legacy_path = isolated_store.parent / "coach_notes.json"
        legacy_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.write_text(json.dumps({
            "P001": {"author": "x", "text": "original", "updated_at": "2026-01-01T00:00:00+00:00"}
        }), encoding="utf-8")

        notes_store.get_note("P001")
        notes_store.save_note("P001", "edited after migration", expected_version=1)

        assert notes_store.get_note("P001")["text"] == "edited after migration"


class TestArchitecturalSeparationFromPipeline:

    def test_notes_dir_is_not_bronze_silver_or_gold(self):
        from dashboard.data_loader import GOLD_DIR, BRONZE_DIR

        assert notes_store.DATA_DIR != GOLD_DIR
        assert notes_store.DATA_DIR != BRONZE_DIR
        assert notes_store.DATA_DIR.name == "data"
        assert not str(notes_store.DATA_DIR).startswith(str(GOLD_DIR))
        assert not str(notes_store.DATA_DIR).startswith(str(BRONZE_DIR))

    def test_notes_survive_deletion_and_rebuild_of_gold_dir(self, isolated_store, tmp_path):
        fake_gold_dir = tmp_path / "gold"
        fake_gold_dir.mkdir()
        (fake_gold_dir / "players_scored_20260101_000000.csv").write_text("player_id,DSSScore\n1,0.9\n")

        notes_store.save_note("player_9", "Survives pipeline re-runs.")

        for f in fake_gold_dir.iterdir():
            f.unlink()
        (fake_gold_dir / "players_scored_20260102_000000.csv").write_text("player_id,DSSScore\n1,0.95\n")

        note = notes_store.get_note("player_9")
        assert note is not None
        assert note["text"] == "Survives pipeline re-runs."