# ingestion/silver_pipeline.py — v17 (MASTER_SPEC v1.0)
#
# Silver Layer — 3 responsibilities only:
#   1. JOIN    — read all Bronze sources from MinIO, LEFT JOIN on player_id
#   2. RENAME  — map Bronze column names → Gold-expected names (data contract)
#   3. COALESCE + IMPUTE — fill NaN via source priority, then position-group median
#
# Silver does NOT normalize. Silver does NOT score. Those are Gold's job.
#
# CONFLICTS FIXED:
#   CONFLICT-01 — form_final + rating_final COALESCE (49/49 guaranteed)
#   CONFLICT-07 — sofascore_detail/ added to sources; sofascore_matches_rated rename
#   CONFLICT-08 — sofascore_rated_matches → sofascore_matches_rated

import logging

import numpy as np
import pandas as pd

from clients.minio_client import read_latest_json

logger = logging.getLogger(__name__)

# ── RENAME MAP: Bronze column → Silver (Gold-expected) column ─────────────────
# Only columns that need renaming. Passthrough columns are not listed here.
# Source priority is handled in _coalesce_performance() below.
BRONZE_TO_SILVER_RENAME: dict[str, str] = {
    # Transfermarkt
    "tm_market_value_eur":      "market_value_eur",
    # SofaScore column name inconsistency (CONFLICT-08)
    "sofascore_rated_matches":  "sofascore_matches_rated",
}

# ── COALESCE PRIORITY CHAINS ──────────────────────────────────────────────────
# Each entry: (silver_output_col, [bronze_col_priority_1, bronze_col_priority_2, ...])
COALESCE_CHAINS: list[tuple[str, list[str]]] = [
    # Goals per 90 — FBref → club_stats_csv → sofascore_detail → Fotmob
    ("Gls_per90",   ["fbref_goals_per90",    "csv_gls_per90",   "sd_gls_per90",  "fm_gls_per90"]),
    # Assists per 90
    ("Ast_per90",   ["fbref_ast_per90",      "csv_ast_per90",   "sd_ast_per90",  "fm_ast_per90"]),
    # xG per 90 — understat → sofascore_detail → Fotmob
    ("xg_per90",    ["understat_npxg_per90", "sd_xg_per90",     "fm_xg_per90"]),
    # xA per 90
    ("xa_per90",    ["understat_xa_per90",   "sd_xa_per90",     "fm_xa_per90"]),
    # Defensive — sofascore_detail → Fotmob (fills GAP C fully)
    ("Tkl_per90",   ["sd_tkl_per90",  "fm_tkl_per90"]),
    ("Int_per90",   ["sd_int_per90",  "fm_int_per90"]),
    ("Clr_per90",   ["sd_clr_per90",  "fm_clr_per90"]),
    # International — fbref_intl preferred → Fotmob (fills GAP intl)
    ("intl_caps",         ["intl_caps",         "fm_intl_caps"]),
    ("intl_goals_per90",  ["intl_goals_per90",  "fm_intl_goals_per90"]),
    ("intl_ast_per90",    ["intl_ast_per90",     "fm_intl_assists_per90"]),
    # GK Goals Against per 90 — FBref preferred → sofascore_detail
    ("GA_per90",    ["fbref_gk_ga_per90", "sd_ga_per90"]),  # FIX BUG-3: fbref أولاً (أكثر موثوقية)
    # GK Save % — FBref preferred → sofascore_detail
    ("GK_save_pct", ["fbref_gk_save_pct",  "sd_save_pct"]),
    # GK Saves per 90 — new from sofascore_detail
    ("GK_saves_per90", ["sd_saves_per90"]),
    # Ratings — SofaScore preferred, FlashScore fallback (CONFLICT-01)
    ("rating_final", ["sofascore_rating_avg", "flashscore_rating_avg"]),
    ("form_final",   ["sofascore_form_5",     "flashscore_form_5"]),
]

# ── IMPUTATION CONFIG ─────────────────────────────────────────────────────────
# Columns imputed per position-group median after COALESCE.
# GK columns only imputed for GK players; outfield columns only for DEF/MID/FWD.
IMPUTE_OUTFIELD_COLS: list[str] = [
    "Gls_per90", "Ast_per90",
    "understat_npxg_per90", "understat_xa_per90",
    "understat_shots_per90", "understat_conversion",
    "sb_pressures_per90", "sb_passes_per90", "sb_carries_per90",
    "rating_final", "form_final",
    "chem_avg_per90",   # NEW — ChemistryScore input, 11/49 real
]
IMPUTE_GK_COLS: list[str] = [
    "GA_per90", "GK_save_pct",
    "sb_pressures_per90", "sb_passes_per90", "sb_carries_per90",
    "rating_final", "form_final",
    "chem_avg_per90",   # NEW — ChemistryScore input, 11/49 real
]

# Columns imputed with ZERO (not median) — conservative approach.
# intl_* missing = player has no fbref_intl data = likely young/backup with few caps.
# High-cap players (Bounou=90, Amrabat=75) already have real values from fbref_intl.
IMPUTE_ZERO_COLS: list[str] = [
    "intl_caps", "intl_goals_per90", "intl_ast_per90", "intl_minutes_total",
]

# ── MANDATORY COLUMNS (Silver QA) ─────────────────────────────────────────────
# MANDATORY_ALL:      non-null for ALL 49 players after Silver.
# MANDATORY_OUTFIELD: non-null for DEF/MID/FWD only (GKs have no Gls/Ast).
# MANDATORY_GK:       non-null for GK players only.
MANDATORY_ALL: list[str] = [
    "rating_final", "form_final",
    "inj_count_2y", "inj_days_missed_2y",
]
MANDATORY_OUTFIELD: list[str] = [
    "Gls_per90", "Ast_per90",
]
MANDATORY_GK: list[str] = [
    "GA_per90",
]


def join_all_bronze_sources(
    run_date: str,
    seed_path: str = "seeds/players_49.csv",
) -> pd.DataFrame:
    """
    Step 1 — JOIN: reads all Bronze source files from MinIO, merges on player_id.
    All joins are LEFT JOIN — 49 players always preserved.
    Returns raw merged DataFrame before renaming or imputation.

    Args:
        run_date:  Run date string (for logging context only).
        seed_path: Path to seed CSV (local).

    Returns:
        Merged DataFrame, 49 rows, raw Bronze column names.
    """
    base = pd.read_csv(seed_path)
    assert len(base) == 49, f"Expected 49 players in seed, got {len(base)}"

    logger.info("Silver JOIN started — run_date=%s, seed=%d players", run_date, len(base))

    def _read(prefix: str) -> pd.DataFrame | None:
        try:
            records = read_latest_json(bucket="football-bronze", prefix=prefix)
            if records is None:
                return None
            df = pd.DataFrame(records)
            if "player_id" not in df.columns:
                logger.warning("Source '%s' has no player_id — cannot merge.", prefix)
                return None
            return df
        except Exception as e:
            logger.warning("Could not read Bronze source '%s': %s", prefix, e)
            return None

    # CONFLICT-07 FIX: added sofascore_detail/ to sources dict
    sources = {
        "wikidata":             "wikidata/",
        "statsbomb":            "statsbomb/",
        "fbref":                "fbref/",
        "fbref_intl":           "fbref_intl/",
        "transfermarkt":        "transfermarkt/",
        "transfermarkt_injury": "transfermarkt_injury/",
        "understat":            "understat/",
        "sofascore":            "sofascore/",
        "sofascore_detail":             "sofascore_detail/",
        "sofascore_detail_extracted":   "sofascore_detail_extracted/",
        "fotmob":                       "fotmob/",
        "flashscore":                   "flashscore/",
        "club_stats_csv":       "club_stats_csv/",
        # NEW: SoFIFA — composure/reactions/aggression → MentalScore 49/49
        # يُغطي كل لاعب في FIFA بغض النظر عن الدوري
        # sofifa_composure_norm × 0.50 + sofifa_reactions_norm × 0.35 + sofifa_aggression_norm × 0.15
        "sofifa":               "sofifa/",
    }

    for source_name, prefix in sources.items():
        df = _read(prefix)
        if df is not None:
            cols_before = len(base.columns)
            base = base.merge(df, on="player_id", how="left")
            cols_added = len(base.columns) - cols_before
            logger.info(
                "Merged: %-26s → +%d columns (%d total)",
                source_name, cols_added, len(base.columns),
            )
        else:
            logger.warning("Skipped merge for '%s' — no data available.", source_name)

    logger.info("JOIN complete: %d rows × %d columns", *base.shape)
    return base


def join_chemistry_pairs(df: pd.DataFrame) -> pd.DataFrame:
    """
    Reads the pairwise StatsBomb chemistry data (statsbomb/chemistry_pairs_*.json)
    and reduces it from a pair-level table to a per-player scalar that fits
    Silver's one-row-per-player convention:

        chem_avg_per90    — average combined-passes/90 across all this
                             player's known partners (their overall
                             "combines well with others" signal)
        chem_num_partners — how many candidates they have chemistry data
                             with (context — not itself scored)

    Only the 11/49 candidates covered by StatsBomb open data (WC2018/2022 +
    AFCON2023) get real values here. The rest are NaN and get imputed by
    impute_by_position() same as sb_pressures_per90/sb_passes_per90.

    This is a genuine data-availability ceiling, not a bug: pairwise
    chemistry can only be measured between two players who started
    together in a covered StatsBomb match.
    """
    try:
        records = read_latest_json(bucket="football-bronze", prefix="statsbomb_chemistry/")
    except Exception as e:
        logger.warning("Could not read chemistry_pairs Bronze source: %s", e)
        records = None

    if not records:
        logger.warning("chemistry_pairs Bronze source empty/missing — chem_avg_per90 will be all-NaN before imputation.")
        df["chem_avg_per90"]    = np.nan
        df["chem_num_partners"] = 0
        return df

    pairs_df = pd.DataFrame(records)
    # Reshape pair-level rows (a<->b) into one row per side, so each
    # player's involvement in every pair they're part of is counted once.
    long_df = pd.concat([
        pairs_df.rename(columns={"player_a_name": "player_name", "player_b_name": "partner_name"}),
        pairs_df.rename(columns={"player_b_name": "player_name", "player_a_name": "partner_name"}),
    ], ignore_index=True)

    per_player = long_df.groupby("player_name")["chem_per90"].agg(
        chem_avg_per90="mean",
        chem_num_partners="count",
    ).reset_index()

    df = df.merge(per_player, on="player_name", how="left")
    df["chem_num_partners"] = df["chem_num_partners"].fillna(0).astype(int)
    logger.info(
        "Chemistry pairs joined: %d/%d players have real chem_avg_per90 (from %d pairs)",
        df["chem_avg_per90"].notna().sum(), len(df), len(pairs_df),
    )
    return df


def join_sentiment_texts(df: pd.DataFrame) -> pd.DataFrame:
    """
    Reads the three raw-text Bronze sources for SentimentScore (Reddit,
    YouTube, NewsAPI — see ingestion/loaders/reddit_collector.py,
    youtube_collector.py, newsapi_collector.py) and combines them into a
    single "sentiment_texts" list-column per player.

    Deliberately does NOT run any NLP scoring here — Silver's job is raw
    data assembly, consistent with join_chemistry_pairs() (raw pairwise
    data, no scoring) and the rest of this file. Actual sentiment
    scoring (loading transformer models, language routing) happens in
    Gold — see analytics/scoring/sentiment_score.py — since it's compute-
    heavy and belongs with the other compute_*_score() functions.

    Coverage note: all three sources are new (added 2026-07-22) and
    UNTESTED against live APIs (see each collector's module docstring
    for what's verified vs not). Expect this to be the SPARSEST column
    in the entire Silver table until real runs establish actual coverage
    — do not assume high coverage the way ChemistryScore's 11/49 could be
    predicted in advance (StatsBomb's match list was known upfront;
    Reddit/YouTube/NewsAPI discussion volume per player is not knowable
    without actually running the collectors).
    """
    text_columns = {
        "reddit/":  "reddit_texts",
        "youtube/": "youtube_texts",
        "newsapi/": "newsapi_texts",
    }

    combined: dict[str, list[str]] = {}

    for prefix, field_name in text_columns.items():
        try:
            records = read_latest_json(bucket="football-bronze", prefix=prefix)
        except Exception as e:
            logger.warning("Could not read %s Bronze source: %s", prefix, e)
            records = None

        if not records:
            logger.info("No data found for sentiment source '%s' — skipping.", prefix)
            continue

        for record in records:
            player_id = record.get("player_id")
            texts = record.get(field_name)
            if player_id is None or not texts:
                continue
            combined.setdefault(player_id, []).extend(texts)

    if not combined:
        logger.warning(
            "sentiment_texts: NO data from any of Reddit/YouTube/NewsAPI — "
            "SentimentScore will be entirely unscored (None) this run. "
            "Check that the collectors ran and credentials are configured."
        )
        df["sentiment_texts"] = [[] for _ in range(len(df))]
        df["sentiment_text_count"] = 0
        return df

    df["sentiment_texts"] = df["player_id"].map(combined).apply(lambda x: x if isinstance(x, list) else [])
    df["sentiment_text_count"] = df["sentiment_texts"].apply(len)

    covered = (df["sentiment_text_count"] > 0).sum()
    logger.info(
        "Sentiment texts joined: %d/%d players have at least 1 text "
        "(total %d texts across Reddit+YouTube+NewsAPI combined)",
        covered, len(df), sum(len(t) for t in combined.values()),
    )
    return df


def rename_bronze_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Step 2 — RENAME: maps Bronze column names to Gold-expected Silver column names.
    Only renames columns that exist in the DataFrame — no KeyError on missing sources.

    Returns:
        DataFrame with renamed columns.
    """
    rename_map = {k: v for k, v in BRONZE_TO_SILVER_RENAME.items() if k in df.columns}
    df = df.rename(columns=rename_map)
    if rename_map:
        logger.info("Renamed %d columns: %s", len(rename_map), list(rename_map.values()))
    return df


def apply_coalesce(df: pd.DataFrame) -> pd.DataFrame:
    """
    Step 3a — COALESCE: fills Silver output columns using source priority chains.
    For each chain, uses the first non-null value across the listed Bronze columns.

    Example:
        Gls_per90 = COALESCE(fbref_goals_per90, csv_gls_per90)
        form_final = COALESCE(sofascore_form_5, flashscore_form_5)  ← 49/49

    Returns:
        DataFrame with new Silver columns added.
    """
    for silver_col, bronze_cols in COALESCE_CHAINS:
        available = [c for c in bronze_cols if c in df.columns]
        if not available:
            logger.warning("COALESCE '%s': no source columns found — will be all NaN.", silver_col)
            df[silver_col] = np.nan
            continue

        # Start with first available, then combine_first in priority order
        result = df[available[0]].copy()
        for col in available[1:]:
            result = result.combine_first(df[col])

        df[silver_col] = result
        non_null = result.notna().sum()
        logger.info("COALESCE %-20s → %d/49 non-null", silver_col, non_null)

    return df


def impute_by_position(df: pd.DataFrame) -> pd.DataFrame:
    """
    Step 3b — IMPUTE: fills remaining NaN with position-group median.

    Rules:
    - GK columns (GA_per90, GK_save_pct) only imputed for GK players.
    - Outfield columns never imputed for GK players.
    - If entire position group is NaN → fall back to global median.
    - Uses median (not mean) for outlier robustness.

    Returns:
        DataFrame with NaN values filled where possible.
    """
    position_col = "position"
    if position_col not in df.columns:
        logger.error("No 'position' column found — cannot impute by position group.")
        return df

    outfield_mask = df[position_col].isin(["DEF", "MID", "FWD"])
    gk_mask = df[position_col] == "GK"

    def _impute(mask: pd.Series, cols: list[str], group_label: str) -> None:
        positions = df.loc[mask, position_col].unique()
        for col in cols:
            if col not in df.columns:
                continue
            null_before = df.loc[mask, col].isna().sum()
            if null_before == 0:
                continue

            for pos in positions:
                pos_mask = mask & (df[position_col] == pos)
                group_median = df.loc[pos_mask, col].median()

                if pd.isna(group_median):
                    # Fallback: global median for this group type
                    group_median = df.loc[mask, col].median()

                if pd.isna(group_median):
                    logger.warning("No median available for '%s' [%s] — leaving NaN.", col, pos)
                    continue

                fill_mask = pos_mask & df[col].isna()
                df.loc[fill_mask, col] = group_median

            null_after = df.loc[mask, col].isna().sum()
            if null_before > null_after:
                logger.info(
                    "Imputed %-22s [%s]: %d → %d NaN (%d filled)",
                    col, group_label, null_before, null_after, null_before - null_after,
                )

    # ── FIX (2026-07-29): علم data provenance لـ MentalScore ──────────────────
    # المشكلة المكتشفة: compute_mental_score() فـ Gold كانت تصنّف mental_source
    # بناءً على "هل العمود non-null؟" — لكن بعد impute_by_position()، كل الأعمدة
    # non-null (سواء كانت حقيقية أو مُعوَّضة بـmedian المركز). النتيجة: لوغ حقيقي
    # (27 يوليوز) أظهر "sources: {'statsbomb': 49}" رغم أن التغطية الحقيقية
    # لـStatsBomb كانت تاريخياً 11/49 فقط — كذب توثيقي كامل، وجعل منطق SoFIFA
    # fallback (اللي بُني خصيصاً لهذا) كود ميت فعلياً لأنه ما يتفعّلش أبداً.
    #
    # الحل: نسجّل sb_mental_data_real=True/False *قبل* الimputation، بنفس نمط
    # ga_imputed_flag الموجود تحت لهذا الملف. Gold تقدر تستعملها لتصنيف
    # mental_source بصدق (statsbomb الحقيقي vs sofifa vs imputed).
    _MENTAL_COLS = ["sb_pressures_per90", "sb_passes_per90", "sb_carries_per90"]
    _present_mental_cols = [c for c in _MENTAL_COLS if c in df.columns]
    if _present_mental_cols:
        # حقيقي = عنده على الأقل قيمة واحدة غير-فارغة قبل أي imputation
        df["sb_mental_data_real"] = df[_present_mental_cols].notna().any(axis=1)
        real_count = int(df["sb_mental_data_real"].sum())
        logger.info(
            "sb_mental_data_real flag set: %d/%d players have genuine StatsBomb "
            "mental data BEFORE imputation (rest will be position-median filled "
            "and must NOT be reported as 'statsbomb' source downstream).",
            real_count, len(df),
        )
    else:
        df["sb_mental_data_real"] = False

    _impute(outfield_mask, IMPUTE_OUTFIELD_COLS, "outfield")

    # BUG-02 FIX: قبل imputation الـ GK — سجّل أي حارس GA_per90 فيه NaN
    # حتى يعرف performance_score أن القيمة مُعطاة وليست حقيقية
    # Yanis Benchaouch: sd_ga=NaN + fbref_gk=NaN → GA=0 imputed → defensive_score=1.0 (خطأ)
    if "GA_per90" in df.columns:
        df["ga_imputed_flag"] = df["GA_per90"].isna() & gk_mask
        ga_imputed_count = df["ga_imputed_flag"].sum()
        if ga_imputed_count > 0:
            imputed_names = df[df["ga_imputed_flag"]]["player_name"].tolist()
            logger.warning(
                "ga_imputed_flag: %d GKs have no GA data — will use median in scoring: %s",
                ga_imputed_count, imputed_names,
            )
    else:
        df["ga_imputed_flag"] = False

    _impute(gk_mask, IMPUTE_GK_COLS, "GK")

    # Zero-impute intl_* columns (conservative: missing = few/no intl experience)
    for col in IMPUTE_ZERO_COLS:
        if col not in df.columns:
            continue
        null_before = df[col].isna().sum()
        if null_before > 0:
            df[col] = df[col].fillna(0.0)
            logger.info(
                "Zero-imputed %-22s: %d players set to 0 (no intl data = 0 caps assumed)",
                col, null_before,
            )

    return df


def validate_silver(df: pd.DataFrame) -> None:
    """
    Silver data quality assertions.
    Raises AssertionError if mandatory columns have NaN.
    Logs warnings for expected partial coverage.
    """
    assert len(df) == 49, f"Silver must have 49 rows, got {len(df)}"
    assert df["player_id"].nunique() == 49, "Duplicate player_id detected in Silver"

    outfield_mask = df["position"].isin(["DEF", "MID", "FWD"])
    gk_mask = df["position"] == "GK"

    errors = []

    # All players
    for col in MANDATORY_ALL:
        if col not in df.columns:
            errors.append(f"MANDATORY column '{col}' missing entirely from Silver")
        elif df[col].isna().any():
            null_players = df.loc[df[col].isna(), "player_id"].tolist()
            errors.append(f"MANDATORY '{col}' has NaN for: {null_players}")

    # Outfield only (DEF/MID/FWD)
    for col in MANDATORY_OUTFIELD:
        if col not in df.columns:
            errors.append(f"MANDATORY_OUTFIELD column '{col}' missing entirely")
        elif df.loc[outfield_mask, col].isna().any():
            null_players = df.loc[outfield_mask & df[col].isna(), "player_id"].tolist()
            errors.append(f"MANDATORY_OUTFIELD '{col}' NaN for outfield: {null_players}")

    # GK only
    for col in MANDATORY_GK:
        if col in df.columns and df.loc[gk_mask, col].isna().any():
            null_gks = df.loc[gk_mask & df[col].isna(), "player_id"].tolist()
            logger.warning("MANDATORY_GK '%s' NaN for GKs: %s (limited FBref GK coverage)", col, null_gks)

    if errors:
        for e in errors:
            logger.error("Silver QA FAIL: %s", e)
        raise AssertionError(f"Silver QA failed with {len(errors)} error(s). Check logs.")

    # Warnings for expected partial coverage
    warn_cols = {
        "sofascore_rating_avg": 25,
        "understat_npxg_per90": 20,
        "intl_caps": 30,
        "sb_pressures_per90": 10,
    }
    for col, threshold in warn_cols.items():
        if col in df.columns:
            non_null = df[col].notna().sum()
            if non_null < threshold:
                logger.warning(
                    "Silver QA WARN: '%s' has only %d/49 non-null (threshold: %d)",
                    col, non_null, threshold,
                )

    logger.info("Silver QA passed — %d rows × %d columns", *df.shape)


def build_silver(
    run_date: str,
    seed_path: str = "seeds/players_49.csv",
) -> pd.DataFrame:
    """
    Full Silver pipeline: JOIN → RENAME → COALESCE → IMPUTE → VALIDATE.

    This is the main entrypoint called by run_silver.py.
    run_gold.py should call this instead of join_all_bronze_sources() directly.

    Returns:
        Clean Silver DataFrame, 49 rows, Gold-ready column names.
    """
    logger.info("=" * 60)
    logger.info("Silver pipeline started — run_date=%s", run_date)
    logger.info("=" * 60)

    # Step 1: JOIN
    df = join_all_bronze_sources(run_date=run_date, seed_path=seed_path)

    # Step 1b: JOIN pairwise StatsBomb chemistry (pair-level -> per-player scalar)
    df = join_chemistry_pairs(df)

    # Step 1c: JOIN raw sentiment text (Reddit + YouTube + NewsAPI, no scoring yet)
    df = join_sentiment_texts(df)

    # Step 2: RENAME
    df = rename_bronze_columns(df)

    # Step 3a: COALESCE
    df = apply_coalesce(df)

    # Step 3b: IMPUTE
    df = impute_by_position(df)

    # Step 3c: FlexibilityScore raw input default fill — 2026-07-14
    # tm_num_positions is 1 (not NaN) for anyone whose TM fetch found a
    # main position but no secondary one (set inside the loader itself).
    # NaN only happens if the ENTIRE TM fetch failed for that player — a
    # global default of 1 ("assume single position") is the neutral,
    # non-penalizing choice here, not a position-group median like the
    # per-90 metrics use (versatility isn't position-dependent the way
    # e.g. passes/90 is).
    if "tm_num_positions" not in df.columns:
        logger.warning(
            "tm_num_positions column entirely missing — Transfermarkt loader "
            "may not have run, or the secondary-position selectors never "
            "matched (UNTESTED against live TM — verify selectors). "
            "Defaulting all 49 players to 1 position."
        )
        df["tm_num_positions"] = 1
    else:
        missing = df["tm_num_positions"].isna().sum()
        if missing:
            logger.info("tm_num_positions: %d/%d players defaulted to 1 (TM fetch failed)", missing, len(df))
        df["tm_num_positions"] = df["tm_num_positions"].fillna(1).astype(int)

    # Step 4: VALIDATE
    validate_silver(df)

    logger.info("Silver pipeline complete: %d rows × %d columns", *df.shape)
    return df