# tests/test_pitch_renderer.py
#
# Test suite for dashboard/pitch_renderer.py — added 2026-07-30.
# No Streamlit runtime needed (module has zero Streamlit imports, same
# convention as dashboard/data_loader.py).

import sys
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import matplotlib
matplotlib.use("Agg")

from dashboard.pitch_renderer import (
    find_player_photo,
    _circular_rgba,
    assign_xi_to_formation,
    render_pitch,
    FORMATIONS,
    PLAYER_PHOTOS_DIR,
)


def _real_2026_xi_df():
    """11 players matching 'Real 2026 (1-4-5-1)' slot counts exactly."""
    rows = (
        [{"player_id": "P_GK", "player_name": "Test GK", "position": "GK"}]
        + [{"player_id": f"P_DEF{i}", "player_name": f"Def {i}", "position": "DEF"} for i in range(4)]
        + [{"player_id": f"P_MID{i}", "player_name": f"Mid {i}", "position": "MID"} for i in range(5)]
        + [{"player_id": "P_FWD", "player_name": "Test FWD", "position": "FWD"}]
    )
    return pd.DataFrame(rows)


class TestFindPlayerPhoto:
    def test_none_id_returns_none(self):
        assert find_player_photo(None) is None

    def test_empty_string_returns_none(self):
        assert find_player_photo("") is None

    def test_nonexistent_player_returns_none(self):
        assert find_player_photo("P_definitely_not_a_real_file_xyz") is None

    def test_finds_existing_jpg(self, tmp_path, monkeypatch):
        monkeypatch.setattr("dashboard.pitch_renderer.PLAYER_PHOTOS_DIR", tmp_path)
        (tmp_path / "P001.jpg").write_bytes(b"not a real jpg but that's fine for this test")
        assert find_player_photo("P001") == tmp_path / "P001.jpg"

    def test_finds_png_when_jpg_absent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("dashboard.pitch_renderer.PLAYER_PHOTOS_DIR", tmp_path)
        (tmp_path / "P002.png").write_bytes(b"fake png bytes")
        assert find_player_photo("P002") == tmp_path / "P002.png"


class TestCircularRgba:
    def test_valid_image_returns_rgba_array_with_alpha_mask(self, tmp_path):
        from PIL import Image
        img_path = tmp_path / "test_photo.jpg"
        Image.new("RGB", (300, 200), color=(120, 60, 200)).save(img_path)

        result = _circular_rgba(img_path, size=100)
        assert result is not None
        assert result.shape == (100, 100, 4)
        # corners should be transparent (outside the circle), center opaque
        assert result[0, 0, 3] == 0
        assert result[50, 50, 3] == 255

    def test_corrupt_file_returns_none_not_raises(self, tmp_path):
        bad_path = tmp_path / "corrupt.jpg"
        bad_path.write_bytes(b"this is not an image at all")
        assert _circular_rgba(bad_path) is None

    def test_missing_file_returns_none_not_raises(self, tmp_path):
        assert _circular_rgba(tmp_path / "does_not_exist.jpg") is None


class TestAssignXiToFormation:
    def test_correct_slot_count_assigns_all_11(self):
        df = _real_2026_xi_df()
        assigned = assign_xi_to_formation(df, formation_name="Real 2026 (1-4-5-1)")
        assert len(assigned) == 11
        assert all("x" in p and "y" in p for p in assigned)

    def test_unknown_formation_raises(self):
        df = _real_2026_xi_df()
        with pytest.raises(ValueError, match="Unknown formation"):
            assign_xi_to_formation(df, formation_name="not-a-real-formation")

    def test_missing_required_columns_raises(self):
        df = pd.DataFrame({"player_name": ["a"]})
        with pytest.raises(ValueError, match="position"):
            assign_xi_to_formation(df)

    def test_mismatched_position_counts_raises_loudly(self):
        # 5 DEF instead of the 4 "Real 2026" formation expects
        df = pd.DataFrame(
            [{"player_id": f"P{i}", "player_name": f"P {i}", "position": "DEF"} for i in range(5)]
            + [{"player_id": "GK", "player_name": "GK", "position": "GK"}]
        )
        with pytest.raises(ValueError, match="don't match formation"):
            assign_xi_to_formation(df, formation_name="Real 2026 (1-4-5-1)")

    def test_all_formations_are_internally_valid_11_man_shapes(self):
        # sanity-check the FORMATIONS constant itself, not just the function
        for name, slots in FORMATIONS.items():
            assert len(slots) == 11, f"{name} has {len(slots)} slots, expected 11"
            counts = {}
            for s in slots:
                counts[s["position"]] = counts.get(s["position"], 0) + 1
            assert counts.get("GK", 0) == 1, f"{name} must have exactly 1 GK"

    def test_player_ids_carried_through_for_photo_lookup(self):
        df = _real_2026_xi_df()
        assigned = assign_xi_to_formation(df)
        assert {p["player_id"] for p in assigned} == set(df["player_id"])


class TestRenderPitch:
    def test_returns_a_figure_without_photos(self):
        df = _real_2026_xi_df()
        assigned = assign_xi_to_formation(df)
        fig = render_pitch(assigned, title="Test XI", show_photos=False)
        assert fig is not None
        import matplotlib.figure
        assert isinstance(fig, matplotlib.figure.Figure)

    def test_missing_player_id_falls_back_without_crashing(self):
        assigned = [{"player_name": "No Id Player", "position": "FWD", "x": 85, "y": 40}]
        fig = render_pitch(assigned, show_photos=True)  # no player_id key at all
        assert fig is not None

    def test_show_photos_false_ignores_existing_photo_file(self, tmp_path, monkeypatch):
        # even if a photo exists, show_photos=False must skip photo lookup
        # entirely and never touch the filesystem for it
        monkeypatch.setattr("dashboard.pitch_renderer.PLAYER_PHOTOS_DIR", tmp_path)
        (tmp_path / "P001.jpg").write_bytes(b"irrelevant")
        assigned = [{"player_id": "P001", "player_name": "Has Photo", "position": "GK", "x": 5, "y": 40}]
        fig = render_pitch(assigned, show_photos=False)
        assert fig is not None

    def test_real_photo_renders_without_error(self, tmp_path, monkeypatch):
        from PIL import Image
        monkeypatch.setattr("dashboard.pitch_renderer.PLAYER_PHOTOS_DIR", tmp_path)
        Image.new("RGB", (150, 150), color=(10, 200, 10)).save(tmp_path / "P099.jpg")
        assigned = [{"player_id": "P099", "player_name": "Photo Player", "position": "FWD", "x": 85, "y": 40}]
        fig = render_pitch(assigned, show_photos=True)
        assert fig is not None

    def test_empty_xi_does_not_crash(self):
        fig = render_pitch([], title="Empty")
        assert fig is not None
