# dashboard/pitch_renderer.py
# Football Atlas Dashboard — Tactical Board pitch rendering (mplsoccer)
#
# Added 2026-07-30. Zero Streamlit imports (same convention as
# dashboard/data_loader.py) so this is independently unit-testable without
# a running Streamlit server — see tests/test_pitch_renderer.py.
#
# mplsoccer/matplotlib chosen over a hand-built Plotly pitch after showing
# both side by side (see chat history 2026-07-30) — the user's call,
# knowingly trading away native interactivity (hover/zoom) for mplsoccer's
# purpose-built pitch rendering and, per this file, player-photo support.
#
# ─────────────────────────────────────────────────────────────────────────
# PLAYER PHOTOS
# ─────────────────────────────────────────────────────────────────────────
# Drop photos at:  dashboard/assets/players/{player_id}.jpg  (or .jpeg/.png)
# player_id is the same P0xx id used throughout Silver/Gold (seeds/players_49.csv).
# No photo for a given player_id? find_player_photo() returns None and
# render_pitch() falls back to a solid position-colored circle with the
# player's first initial — the page must never crash or show a broken
# image icon just because a photo is missing; partial photo coverage is
# the expected normal case, not an error state.
#
# This project does NOT scrape or bundle any real player photos itself —
# see MASTER_SPEC.md's own "no scraping per Cahier des Charges" constraint,
# and more simply: press/club photography is normally copyrighted. Supply
# your own licensed photos (club media access, your federation's official
# kit, generic silhouettes, etc.) — this module only handles displaying
# whatever you provide, at the path above.

from __future__ import annotations

import logging
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless — this module is imported by a Streamlit page, not run as a GUI app
import matplotlib.patheffects as path_effects
import numpy as np
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
from mplsoccer import Pitch

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PLAYER_PHOTOS_DIR = PROJECT_ROOT / "dashboard" / "assets" / "players"
PHOTO_EXTENSIONS = [".jpg", ".jpeg", ".png"]

POSITION_COLORS: dict[str, str] = {"GK": "#f1c40f", "DEF": "#3498db", "MID": "#2ecc71", "FWD": "#e74c3c"}
PITCH_COLOR = "#1e5631"
BG_COLOR = "#0e1117"  # matches Streamlit's default dark theme background

# Formation coordinate presets — slot -> (x, y) on a 0-100 (length) x 0-80
# (width) opta-style pitch, own goal at x=0.
# "real_2026" matches REAL_FORMATION from analytics/rotation_strategy.py
# (GK1/DEF4/MID5/FWD1) — Morocco's actual reported group-stage shape.
FORMATIONS: dict[str, list[dict]] = {
    "Real 2026 (1-4-5-1)": [
        {"position": "GK", "x": 5, "y": 40},
        {"position": "DEF", "x": 25, "y": 68}, {"position": "DEF", "x": 20, "y": 48},
        {"position": "DEF", "x": 20, "y": 32}, {"position": "DEF", "x": 25, "y": 12},
        {"position": "MID", "x": 42, "y": 40}, {"position": "MID", "x": 55, "y": 55},
        {"position": "MID", "x": 60, "y": 65}, {"position": "MID", "x": 60, "y": 15},
        {"position": "MID", "x": 65, "y": 40},
        {"position": "FWD", "x": 85, "y": 40},
    ],
    "4-3-3": [
        {"position": "GK", "x": 5, "y": 40},
        {"position": "DEF", "x": 20, "y": 68}, {"position": "DEF", "x": 18, "y": 48},
        {"position": "DEF", "x": 18, "y": 32}, {"position": "DEF", "x": 20, "y": 12},
        {"position": "MID", "x": 42, "y": 55}, {"position": "MID", "x": 38, "y": 40},
        {"position": "MID", "x": 42, "y": 25},
        {"position": "FWD", "x": 80, "y": 62}, {"position": "FWD", "x": 88, "y": 40}, {"position": "FWD", "x": 80, "y": 18},
    ],
    "4-2-3-1": [
        {"position": "GK", "x": 5, "y": 40},
        {"position": "DEF", "x": 20, "y": 68}, {"position": "DEF", "x": 18, "y": 48},
        {"position": "DEF", "x": 18, "y": 32}, {"position": "DEF", "x": 20, "y": 12},
        {"position": "MID", "x": 38, "y": 52}, {"position": "MID", "x": 38, "y": 28},
        {"position": "MID", "x": 62, "y": 62}, {"position": "MID", "x": 62, "y": 40}, {"position": "MID", "x": 62, "y": 18},
        {"position": "FWD", "x": 85, "y": 40},
    ],
}


def find_player_photo(player_id: str | None) -> Path | None:
    """Looks for dashboard/assets/players/{player_id}.(jpg|jpeg|png). Returns
    None (never raises) if player_id is falsy or no matching file exists —
    render_pitch() treats None as "use the fallback marker", not an error."""
    if not player_id:
        return None
    for ext in PHOTO_EXTENSIONS:
        candidate = PLAYER_PHOTOS_DIR / f"{player_id}{ext}"
        if candidate.exists():
            return candidate
    return None


def _circular_rgba(path: Path, size: int = 200) -> np.ndarray | None:
    """Loads an image, center-crops to square, and applies a circular alpha
    mask — returns an RGBA numpy array for OffsetImage. Returns None (never
    raises) on any load/decode failure, so a corrupt/unsupported photo file
    degrades to the fallback marker instead of crashing the page."""
    try:
        from PIL import Image
    except ImportError:
        logger.warning("Pillow not installed — player photos will fall back to plain markers.")
        return None

    try:
        img = Image.open(path).convert("RGBA")
    except Exception as e:
        logger.warning("Could not open player photo %s (%s) — falling back to marker.", path, e)
        return None

    w, h = img.size
    side = min(w, h)
    left, top = (w - side) // 2, (h - side) // 2
    img = img.crop((left, top, left + side, top + side)).resize((size, size))

    mask = Image.new("L", (size, size), 0)
    ys, xs = np.ogrid[:size, :size]
    center = size / 2
    circle_mask = ((xs - center) ** 2 + (ys - center) ** 2) <= (center - 2) ** 2
    mask_arr = np.zeros((size, size), dtype=np.uint8)
    mask_arr[circle_mask] = 255
    mask = Image.fromarray(mask_arr, mode="L")

    rgba = np.array(img)
    rgba[..., 3] = np.array(mask)
    return rgba


def render_pitch(
    assigned_players: list[dict],
    title: str = "Starting XI",
    figsize: tuple[float, float] = (7.6, 5.6),
    show_photos: bool = True,
):
    """
    Draws a full pitch with 11 player markers.

    assigned_players: list of dicts with player_name, position, x, y, and
        optionally player_id (used to look up a photo — omit or set None
        for the plain fallback marker regardless of show_photos).
    show_photos: master on/off switch — False always uses the plain
        position-colored circle markers, even if photos exist on disk
        (useful for a fast/consistent look without touching any files).

    Returns a matplotlib Figure — callers pass this to st.pyplot(fig).
    """
    pitch = Pitch(pitch_type="opta", pitch_color=PITCH_COLOR, line_color="white", linewidth=2)
    fig, ax = pitch.draw(figsize=figsize)
    fig.patch.set_facecolor(BG_COLOR)

    for p in assigned_players:
        color = POSITION_COLORS.get(p["position"], "#95a5a6")
        photo_rgba = None
        if show_photos:
            photo_path = find_player_photo(p.get("player_id"))
            if photo_path is not None:
                photo_rgba = _circular_rgba(photo_path)

        if photo_rgba is not None:
            im = OffsetImage(photo_rgba, zoom=0.22)  # was 0.34 — too big, overlapped the name label below
            ab = AnnotationBbox(im, (p["x"], p["y"]), frameon=False, pad=0, zorder=3)
            ax.add_artist(ab)
            # thin colored ring around the photo so position is still readable at a glance
            ax.scatter([p["x"]], [p["y"]], s=950, facecolors="none", edgecolors=color, linewidths=2.5, zorder=2)
        else:
            ax.scatter([p["x"]], [p["y"]], s=900, color=color, edgecolors="white", linewidths=2, zorder=3)
            initial = (p["player_name"].split()[-1][0] if p.get("player_name") else "?").upper()
            ax.text(p["x"], p["y"], initial, color="white", ha="center", va="center",
                    fontsize=14, fontweight="bold", zorder=4)

        name_text = ax.text(
            p["x"], p["y"] - 9.5, p.get("player_name", "").split()[-1] if p.get("player_name") else "",
            color="white", ha="center", va="top", fontsize=10, fontweight="bold", zorder=6,
        )
        # black outline around the text so it stays readable regardless of
        # what's behind it (green pitch, another player, a photo edge) —
        # this was the other half of the "name should be under the photo"
        # fix: position alone doesn't help if the label is also hard to read.
        name_text.set_path_effects([
            path_effects.Stroke(linewidth=2.5, foreground="black"),
            path_effects.Normal(),
        ])

    ax.set_title(title, color="white", fontsize=14, pad=14)
    return fig


def assign_xi_to_formation(xi_df, formation_name: str = "Real 2026 (1-4-5-1)") -> list[dict]:
    """
    Maps an 11-row DataFrame (needs player_name, position, and ideally
    player_id for photo lookup) onto formation slot coordinates. Assigns
    within each position group in the DataFrame's given row order — sort
    xi_df by priority/DSSScore first if slot placement (e.g. which DEF
    plays right-back vs center-back) should reflect that; this function
    does not re-sort for you.

    Raises ValueError (loudly, not a silent misplacement) if xi_df's
    position counts don't match the chosen formation's slot counts.
    """
    if formation_name not in FORMATIONS:
        raise ValueError(f"Unknown formation {formation_name!r} — choose from {list(FORMATIONS)}")
    if "position" not in xi_df.columns or "player_name" not in xi_df.columns:
        raise ValueError("xi_df requires at least 'player_name' and 'position' columns.")

    slots = FORMATIONS[formation_name]
    slot_counts: dict[str, int] = {}
    for s in slots:
        slot_counts[s["position"]] = slot_counts.get(s["position"], 0) + 1
    xi_counts = xi_df["position"].value_counts().to_dict()
    if slot_counts != xi_counts:
        raise ValueError(
            f"xi_df position counts {xi_counts} don't match formation "
            f"{formation_name!r} slot counts {slot_counts} — pick a matching "
            f"formation or adjust xi_df to exactly 11 players in those proportions."
        )

    remaining_slots = {pos: [s for s in slots if s["position"] == pos] for pos in slot_counts}
    assigned = []
    for _, row in xi_df.iterrows():
        slot = remaining_slots[row["position"]].pop(0)
        assigned.append({
            "player_id": row.get("player_id"),
            "player_name": row["player_name"],
            "position": row["position"],
            "x": slot["x"], "y": slot["y"],
        })
    return assigned