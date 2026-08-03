# ingestion/loaders/club_stats_csv_loader.py
# Source: Local FBref-style club stats CSV (data/raw/players_data-2025_2026.csv)
#
# WHY THIS LOADER EXISTS:
#   fbref_loader.py and fbref_advanced_loader.py are both blocked by FBref's
#   Cloudflare Turnstile challenge (confirmed: checkbox-solve does not persist
#   cf_clearance across page navigations even with patchright + visible Chrome).
#   This CSV is a one-time manual export of FBref-format player stats (same
#   column shape: Gls, Ast, Sh, SoT, Min, 90s, ...) covering the Big-5 European
#   leagues (Premier League, La Liga, Bundesliga, Serie A, Ligue 1) for the
#   2025-26 season. It lets us populate club_performance columns for the
#   subset of the 49-player seed who play in those 5 leagues, with zero
#   network requests and zero blocker risk.
#
# KNOWN LIMITATION (confirmed by manual inspection of the CSV):
#   The CSV does NOT contain xG, npxG, xA, press_per90, or prog_passes -
#   those columns are FBref "advanced" tables (shooting/passing/possession
#   types) that were never exported into this file. Only the "standard"
#   FBref columns are present: Gls, Ast, Min, 90s, Sh, SoT, CrdY, CrdR, etc.
#   This loader writes ONLY the columns it can honestly fill. It does NOT
#   approximate xG from Sh/SoT (shot location/quality/type all factor into
#   real xG - any proxy would be statistically unreliable for a player
#   selection DSS). xG/npxG/press_per90/prog_passes remain NULL here and
#   stay the responsibility of fbref_loader.py / fbref_advanced_loader.py
#   once a working Cloudflare bypass exists.
#
# COVERAGE: ~24/49 players (Big-5 leagues only). The other ~25 players
#   (Botola Pro, Saudi Pro League, Jupiler Pro League, Eredivisie, Egyptian
#   Premier League, UAE Pro League, Super League Greece, Ligue 2,
#   Championship) are outside this CSV's scope and will show as
#   {"player_id": ...} only (no stats) - same "skip, don't fabricate"
#   pattern used by every other loader in this pipeline.
#
# MID-SEASON TRANSFERS: some players have two rows (e.g. moved clubs in the
#   January window - Aguerd: Marseille -> West Ham; Akhomach: Villarreal ->
#   Rayo Vallecano). Counting stats (Gls, Ast, Min, Sh, SoT, CrdY, CrdR) are
#   SUMMED across all rows for a player; the most recent/highest-minutes
#   club is reported as csv_squad / csv_comp for context.

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

# Columns this loader can honestly fill from the CSV (FBref "standard" table
# only - no xG/npxG/xA/press/prog, see module docstring).
_COUNTING_COLS = ["MP", "Starts", "Min", "Gls", "Ast", "Sh", "SoT", "CrdY", "CrdR"]

_FUZZY_MIN_SCORE = 92  # token_sort_ratio threshold for accepting a name match


def _normalize_name(name: str) -> str:
    """
    Strips accents/diacritics and non-letter characters, lowercases, and
    sorts tokens - so 'Brahim Díaz' / 'Brahim Diaz' and
    'Ayoube Amaimouni-Echghouyab' / 'Ayoube Amaimouni Echghouyab' match
    on an exact pass before falling back to fuzzy matching.
    """
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z ]", " ", s).lower()
    return " ".join(sorted(s.split()))


def _load_csv_indexed() -> tuple[pd.DataFrame, dict[str, list[int]]]:
    """
    Loads the CSV once and builds a normalized-name -> [row indices] index
    for fast exact lookup, with the raw 'Player' column kept for fuzzy
    fallback.
    """
    df = pd.read_csv(CSV_PATH)
    df["_norm"] = df["Player"].apply(_normalize_name)

    index: dict[str, list[int]] = {}
    for i, norm in df["_norm"].items():
        index.setdefault(norm, []).append(i)

    return df, index


def _aggregate_player_rows(df: pd.DataFrame, row_indices: list[int]) -> dict:
    """
    Sums counting stats across all matched rows (handles mid-season
    transfers where a player has 2+ rows). Reports the club/competition
    from the row with the most minutes played as the representative club.
    """
    rows = df.loc[row_indices]

    out: dict = {}
    for col in _COUNTING_COLS:
        if col in rows.columns:
            total = pd.to_numeric(rows[col], errors="coerce").fillna(0).sum()
            out[f"csv_{col.lower()}"] = round(float(total), 2) if total % 1 else int(total)

    # 90s is a derived float (Min / 90) - recompute from summed minutes
    # rather than summing the per-row 90s (avoids rounding drift).
    total_min = out.get("csv_min", 0)
    out["csv_90s"] = round(total_min / 90.0, 2) if total_min else 0.0

    # Representative club = row with most minutes (handles mid-season moves)
    if "Min" in rows.columns and len(rows) > 0:
        rows_sorted = rows.assign(_min_num=pd.to_numeric(rows["Min"], errors="coerce").fillna(0))
        lead_row = rows_sorted.sort_values("_min_num", ascending=False).iloc[0]
        out["csv_squad"] = lead_row.get("Squad")
        out["csv_comp"]  = lead_row.get("Comp")

    # Per-90 rates for the counting stats that matter most to the DSS
    nineties = max(out["csv_90s"], 0.01)
    out["csv_gls_per90"] = round(out.get("csv_gls", 0) / nineties, 3)
    out["csv_ast_per90"] = round(out.get("csv_ast", 0) / nineties, 3)

    return out


def load_club_stats_csv_bronze(
    seed_df: pd.DataFrame,
    run_date: str = "",
) -> str:
    """
    Fills club-performance counting stats (Gls, Ast, Min, Sh, SoT, CrdY,
    CrdR + per-90 rates) for players found in data/raw/players_data-2025_2026.csv
    (Big-5 European leagues, 2025-26 season).

    Does NOT fill xG, npxG, xA, press_per90, or prog_passes - those columns
    are not present in the CSV and are NOT approximated. See module
    docstring for rationale.

    seed_df must contain columns: 'player_id', 'player_name'.
    Players not found in the CSV (non-Big-5 leagues) get
    {"player_id": ...} only - no fabricated data.

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
            # Fallback: fuzzy match on raw name (catches edge cases the
            # normalizer doesn't, e.g. nickname/short-form differences)
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
