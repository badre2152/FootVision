from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
REFERENCE_PLAYERS_PATH = PROJECT_ROOT / "seeds" / "rival_reference_players.csv"

RADAR_CATEGORIES_BY_POSITION: dict[str, list[str]] = {
    "GK":  ["GA_per90", "GK_saves_per90"],
    "DEF": ["Tkl_per90", "Int_per90", "Clr_per90"],
    "MID": ["Tkl_per90", "Int_per90", "Ast_per90", "xa_per90"],
    "FWD": ["Gls_per90", "Ast_per90", "xg_per90", "xa_per90"],
}

INVERTED_CATEGORIES = {"GA_per90"}


def load_reference_players() -> pd.DataFrame:
    if not REFERENCE_PLAYERS_PATH.exists():
        logger.warning("Reference player seed file not found at %s.", REFERENCE_PLAYERS_PATH)
        return pd.DataFrame()
    return pd.read_csv(REFERENCE_PLAYERS_PATH)


def get_references_for_position(position: str, references_df: pd.DataFrame | None = None) -> pd.DataFrame:
    if references_df is None:
        references_df = load_reference_players()
    if references_df.empty or "position" not in references_df.columns:
        return references_df
    return references_df[references_df["position"] == position].reset_index(drop=True)


def _minmax_scale(value: float, lo: float, hi: float, invert: bool = False) -> float:
    if pd.isna(value):
        return None
    if hi == lo:
        return 0.5
    scaled = (value - lo) / (hi - lo)
    if invert:
        scaled = 1.0 - scaled
    return float(min(max(scaled, 0.0), 1.0))


def compare_to_reference(
    player_row: pd.Series,
    position: str,
    references_df: pd.DataFrame | None = None,
) -> dict:
    categories = RADAR_CATEGORIES_BY_POSITION.get(position, [])
    result = {
        "categories": categories,
        "player_name": player_row.get("player_name", "Unknown"),
        "player_values_raw": {},
        "player_values_scaled": {},
        "references": [],
        "warning": None,
    }

    if not categories:
        result["warning"] = f"No benchmark stat categories defined for position '{position}'."
        return result

    refs = get_references_for_position(position, references_df)
    if refs.empty:
        result["warning"] = (
            f"No curated international reference players for position '{position}' yet — "
            "see seeds/rival_reference_players.csv to add one."
        )

    player_raw = {c: (float(player_row[c]) if c in player_row.index and pd.notna(player_row[c]) else None) for c in categories}
    ref_raws = []
    for _, ref in refs.iterrows():
        ref_raws.append({
            "name": ref.get("ref_player_name", "Unknown"),
            "nation": ref.get("nation", ""),
            "club": ref.get("club", ""),
            "season": ref.get("season", ""),
            "benchmark_reason": ref.get("benchmark_reason", ""),
            "stats_verified": bool(ref.get("stats_verified", False)),
            "values_raw": {c: (float(ref[c]) if c in ref.index and pd.notna(ref[c]) else None) for c in categories},
        })

    for cat in categories:
        values_for_cat = [player_raw[cat]] + [r["values_raw"][cat] for r in ref_raws]
        values_for_cat = [v for v in values_for_cat if v is not None]
        if not values_for_cat:
            lo, hi = 0.0, 1.0
        else:
            lo, hi = min(values_for_cat), max(values_for_cat)
        invert = cat in INVERTED_CATEGORIES

        result["player_values_scaled"][cat] = _minmax_scale(player_raw[cat], lo, hi, invert) if player_raw[cat] is not None else None
        for r in ref_raws:
            r.setdefault("values_scaled", {})[cat] = (
                _minmax_scale(r["values_raw"][cat], lo, hi, invert) if r["values_raw"][cat] is not None else None
            )

    result["player_values_raw"] = player_raw
    result["references"] = ref_raws
    return result