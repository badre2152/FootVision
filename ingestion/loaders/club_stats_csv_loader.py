import logging
import unicodedata
import re
from datetime import datetime

import pandas as pd
from rapidfuzz import fuzz, process

from clients.minio_client import upload_json
from ingestion.loaders._bronze_utils import validate_seed_columns, assert_min_coverage

logger = logging.getLogger(__name__)

BUCKET   = "football-bronze"
CSV_PATH = "data/raw/players_data-2025_2026.csv"

_COUNTING_COLS = ["MP", "Starts", "Min", "Gls", "Ast", "Sh", "SoT", "CrdY", "CrdR"]

_FUZZY_MIN_SCORE = 92


def _normalize_name(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z ]", " ", s).lower()
    return " ".join(sorted(s.split()))


def _load_csv_indexed() -> tuple[pd.DataFrame, dict[str, list[int]]]:
    df = pd.read_csv(CSV_PATH)
    df["_norm"] = df["Player"].apply(_normalize_name)

    index: dict[str, list[int]] = {}
    for i, norm in df["_norm"].items():
        index.setdefault(norm, []).append(i)

    return df, index


def _aggregate_player_rows(df: pd.DataFrame, row_indices: list[int]) -> dict:
    rows = df.loc[row_indices]

    out: dict = {}
    for col in _COUNTING_COLS:
        if col in rows.columns:
            total = pd.to_numeric(rows[col], errors="coerce").fillna(0).sum()
            out[f"csv_{col.lower()}"] = round(float(total), 2) if total % 1 else int(total)

    total_min = out.get("csv_min", 0)
    out["csv_90s"] = round(total_min / 90.0, 2) if total_min else 0.0

    if "Min" in rows.columns and len(rows) > 0:
        rows_sorted = rows.assign(_min_num=pd.to_numeric(rows["Min"], errors="coerce").fillna(0))
        lead_row = rows_sorted.sort_values("_min_num", ascending=False).iloc[0]
        out["csv_squad"] = lead_row.get("Squad")
        out["csv_comp"]  = lead_row.get("Comp")

    nineties = max(out["csv_90s"], 0.01)
    out["csv_gls_per90"] = round(out.get("csv_gls", 0) / nineties, 3)
    out["csv_ast_per90"] = round(out.get("csv_ast", 0) / nineties, 3)

    return out


def load_club_stats_csv_bronze(
    seed_df: pd.DataFrame,
    run_date: str = "",
) -> str:
    """
    seed_df must contain columns: 'player_id', 'player_name'.
    Returns status string: "OK (N/49 players with data)" or "FAILED".
    """
    if not run_date:
        run_date = datetime.now().strftime("%Y%m%d_%H%M%S")

    validate_seed_columns(seed_df, ["player_id", "player_name"], source="club_stats_csv")

    try:
        df, exact_index = _load_csv_indexed()
    except FileNotFoundError:
        logger.error("[club_stats_csv] CSV not found at %s", CSV_PATH)
        return "FAILED"

    csv_names = df["Player"].tolist()

    records = []
    for _, row in seed_df.iterrows():
        pid  = row["player_id"]
        name = row["player_name"]
        norm = _normalize_name(name)

        row_indices = exact_index.get(norm)
        match_method = "exact_normalized"

        if not row_indices:
            best = process.extractOne(name, csv_names, scorer=fuzz.token_sort_ratio)
            if best and best[1] >= _FUZZY_MIN_SCORE:
                matched_norm = _normalize_name(best[0])
                row_indices  = exact_index.get(matched_norm)
                match_method = f"fuzzy({best[1]:.0f})"

        if not row_indices:
            records.append({"player_id": pid})
            logger.info("No CSV match for %s (%s) - likely outside Big-5 leagues.", pid, name)
            continue

        stats = _aggregate_player_rows(df, row_indices)
        stats["csv_match_method"] = match_method
        records.append({"player_id": pid, **stats})
        logger.info(
            "Matched %s (%s) -> %s [%s] via %s",
            pid, name, stats.get("csv_squad"), stats.get("csv_comp"), match_method,
        )

    assert_min_coverage(records, min_pct=0.30, source="club_stats_csv")

    object_name = f"club_stats_csv/players_club_stats_csv_{run_date}.json"
    success  = upload_json(BUCKET, object_name, records)
    ok_count = sum(1 for r in records if len(r) > 1)
    return f"OK ({ok_count}/{len(records)} players with data)" if success else "FAILED"