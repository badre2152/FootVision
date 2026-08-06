from __future__ import annotations

import logging
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
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
BG_COLOR = "#0e1117"

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
    if not player_id:
        return None
    for ext in PHOTO_EXTENSIONS:
        candidate = PLAYER_PHOTOS_DIR / f"{player_id}{ext}"
        if candidate.exists():
            return candidate
    return None


def _circular_rgba(path: Path, size: int = 200) -> np.ndarray | None:
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
            im = OffsetImage(photo_rgba, zoom=0.22)
            ab = AnnotationBbox(im, (p["x"], p["y"]), frameon=False, pad=0, zorder=3)
            ax.add_artist(ab)
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
        name_text.set_path_effects([
            path_effects.Stroke(linewidth=2.5, foreground="black"),
            path_effects.Normal(),
        ])

    ax.set_title(title, color="white", fontsize=14, pad=14)
    return fig


def assign_xi_to_formation(xi_df, formation_name: str = "Real 2026 (1-4-5-1)") -> list[dict]:
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