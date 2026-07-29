#!/usr/bin/env python
# run_gold.py — Football DSS Gold Layer Pipeline
#
# Reads Silver output (MinIO Bronze join), normalizes all columns,
# computes all scores, and exports:
#   1. gold/players_scored_YYYYMMDD.csv  — full scored table
#   2. gold/top26_YYYYMMDD.csv           — top 26 recommended players
#   3. gold/squad_summary_YYYYMMDD.json  — position breakdown + flagged risks
#
#
# NEW 2026-07-29: also runs Player DNA (K-Means) clustering and adds a
# "PlayerDNA" column to all three outputs above — see
# analytics/ml/player_clustering.py for what it is and, importantly, what
# it deliberately is NOT (a squad-selection input).
#
# Run AFTER run_bronze.py:
#   python run_gold.py
#   python run_gold.py --top-n 26
#   python run_gold.py --no-player-dna       # skip clustering (fast iteration)
#   python run_gold.py --player-dna-k 4      # force k=4 instead of auto-select

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from ingestion.silver_pipeline import build_silver
from analytics.scoring.normalize import normalize_all
from analytics.scoring.performance_score import (
    compute_performance_score,
    compute_form_score,
    compute_mental_score,
    compute_availability_score,
    compute_international_score,
    compute_final_score,
)
from analytics.scoring.chemistry_score import compute_chemistry_score      # NEW 2026-07-14
from analytics.scoring.pressure_index import compute_pressure_index        # NEW 2026-07-14
from analytics.scoring.flexibility_score import compute_flexibility_score  # NEW 2026-07-14 (UNTESTED upstream — see module docstring)
from analytics.scoring.sentiment_score import compute_sentiment_score      # NEW 2026-07-22 (UNTESTED end-to-end — see module docstring)
from analytics.scoring.percentile_score import apply_percentile_scoring, score_summary  # WIRED IN 2026-07-14 (was orphaned)
from analytics.ml.player_clustering import cluster_players  # NEW 2026-07-29 — ML layer, see module docstring

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("run_gold")

GOLD_OUTPUT_DIR = Path("gold")
SEED_PATH       = "seeds/players_49.csv"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Football DSS — Gold Layer Pipeline")
    p.add_argument(
        "--top-n", type=int, default=26,
        help="Number of top players to include in squad recommendation (default: 26)",
    )
    p.add_argument(
        "--output-dir", type=str, default="gold",
        help="Directory for output files (default: gold/)",
    )
    p.add_argument(
        "--use-clubelo", action="store_true",
        help=(
            "Replace the hardcoded LEAGUE_DIFFICULTY table (league_quality.py, "
            "from AUDIT_AND_FIXES.md Fix 1) with real ClubElo ratings via "
            "ingestion/loaders/clubelo_loader.py. OFF by default — this touches "
            "GK ranking (previously audited), and the live ClubElo fetch has "
            "not been tested from a network-restricted environment. The "
            "parsing/matching/scoring LOGIC is unit-tested; the live API call "
            "is not. Verify GK rankings after your first --use-clubelo run "
            "before trusting it for a real selection decision."
        ),
    )
    p.add_argument(
        "--no-sentiment-nlp", action="store_true",
        help=(
            "Skip real NLP sentiment scoring (loading 4 transformer models is "
            "slow/memory-heavy) — SentimentScore falls back to neutral (0.5) "
            "for everyone. Useful for fast iteration on the rest of the "
            "pipeline. NOT for a real squad-selection run."
        ),
    )
    p.add_argument(
        "--no-player-dna", action="store_true",
        help=(
            "Skip Player DNA (K-Means) clustering — PlayerDNA falls back to "
            "'N/A' for everyone. Clustering itself is cheap (no network, no "
            "heavy models), this flag exists mainly so a clustering failure "
            "never blocks the rest of a real squad-selection run — see "
            "analytics/ml/player_clustering.py docstring for why this "
            "component is descriptive/exploratory, not a selection input."
        ),
    )
    p.add_argument(
        "--player-dna-k", type=int, default=None,
        help="Force this many Player DNA clusters instead of auto-selecting "
             "via silhouette score over k=3..6 (see run_player_dna.py --k).",
    )
    return p.parse_args()


# NOTE: apply_minutes_filter() used to be defined here as a full duplicate
# of analytics/scoring/minutes_filter.py — dead code, never called (main()
# always imported the real implementation from that module — see below).
# Removed to stop the two copies from silently drifting apart again (this
# is exactly how the w.get() weight-key bug in minutes_filter.py happened:
# someone fixed one copy and not the other). The single source of truth for
# minutes-based availability penalties is analytics/scoring/minutes_filter.py.



def select_squad_with_position_cap(
    df: pd.DataFrame,
    top_n: int = 26,
    position_caps: dict | None = None,
) -> pd.DataFrame:
    """
    Selects top-N players with realistic position caps.

    A pure score-based ranking produces 6 GKs in top-26 (since Morocco has 6 GK
    candidates and the scoring system ranks GKs on a different scale). Real squads
    have position constraints: 3 GK, ~9 DEF, ~9 MID, ~5 FWD.

    Default caps (FIFA World Cup squad of 26):
        GK  = 3  (minimum: need 3 to cover all scenarios)
        DEF = 9  (maximum: 4-5 starters + 4-5 backups)
        MID = 9  (maximum)
        FWD = 5  (minimum: 3 starters + 2 backups)

    Algorithm: greedy — iterate by DSSScore rank, add player if position cap not hit.
    After filling all caps: fill remaining slots from best remaining regardless of position.

    Args:
        df:             Full 49-player ranked DataFrame (must have Rank + position cols).
        top_n:          Target squad size (default 26).
        position_caps:  Override default caps per position.

    Returns:
        DataFrame of top_n selected players, sorted by DSSScore.
    """
    caps = position_caps or {"GK": 3, "DEF": 9, "MID": 9, "FWD": 5}
    counts = {pos: 0 for pos in caps}

    selected = []
    remaining = []

    for _, row in df.sort_values("Rank").iterrows():
        pos = row["position"]
        if pos in caps and counts.get(pos, 0) < caps[pos]:
            selected.append(row)
            counts[pos] = counts.get(pos, 0) + 1
        else:
            remaining.append(row)

    # Fill remaining slots from best remaining players
    slots_left = top_n - len(selected)
    if slots_left > 0 and remaining:
        selected.extend(remaining[:slots_left])

    squad_df = pd.DataFrame(selected).sort_values("DSSScore", ascending=False).reset_index(drop=True)
    squad_df.insert(0, "SquadRank", range(1, len(squad_df) + 1))
    return squad_df


def build_squad_summary(squad_df: pd.DataFrame, all_df: pd.DataFrame, top_n: int) -> dict:
    """
    Builds a JSON-serialisable squad summary for the coach.
    Includes position breakdown, injury flags, and coverage stats.

    FIX (regression of AUDIT_AND_FIXES.md Fix 2, June 29 2026):
    This function MUST receive the position-capped squad (`squad_df`, the
    output of select_squad_with_position_cap()) for position_breakdown —
    NOT the full 49-player df filtered by raw Rank <= top_n. Raw Rank
    ignores position caps (e.g. 5-6 GKs legitimately rank inside the top 26
    on pure DSSScore), which previously produced an impossible squad
    (GK=5/6) in this JSON while top26.csv showed the correct capped
    composition (GK=3). Verified live on the 2026-07-13 run before this fix.

    `all_df` (the full 49-candidate table) is only used for total_candidates
    and the global score_range — never for position_breakdown.
    """
    # Position breakdown — from the CAPPED squad, not raw Rank
    position_counts = squad_df.groupby("position")["player_name"].apply(list).to_dict()
    position_size = {pos: len(names) for pos, names in position_counts.items()}

    # Injury risk flags — within the actual squad
    high_risk = squad_df[
        squad_df.get("inj_days_missed_2y", pd.Series(dtype=float)).fillna(0) > 90
    ]["player_name"].tolist() if "inj_days_missed_2y" in squad_df.columns else []

    # Low fbref coverage — within the actual squad
    no_fbref = squad_df[
        squad_df.get("fbref_id", pd.Series(dtype=str)).isna()
    ]["player_name"].tolist() if "fbref_id" in squad_df.columns else []

    # intl_caps data-quality flag (only if Silver produced it — see AUDIT Fix 3)
    caps_missing = []
    if "intl_caps_data_missing" in squad_df.columns:
        caps_missing = squad_df[
            squad_df["intl_caps_data_missing"].fillna(False)
        ]["player_name"].tolist()

    top_row = squad_df.loc[squad_df["DSSScore"].idxmax()]

    return {
        "generated_at":       datetime.now().isoformat(),
        "total_candidates":   len(all_df),
        "squad_size":         len(squad_df),          # actual selected count, not just target
        "squad_size_target":  top_n,
        "position_breakdown": position_counts,
        "position_counts":    position_size,          # e.g. {"GK": 3, "DEF": 9, "MID": 9, "FWD": 5}
        "flags": {
            "high_injury_risk":           high_risk,
            "limited_data":               no_fbref,
            "intl_caps_possibly_missing":  caps_missing,
        },
        "score_range": {
            "min":  float(all_df["DSSScore"].min()),
            "max":  float(all_df["DSSScore"].max()),
            "mean": float(all_df["DSSScore"].mean()),
        },
        "top_player": {
            "name":  top_row.get("player_name", "unknown"),
            "score": float(top_row["DSSScore"]),
            "club":  top_row.get("club", "unknown"),
        },
    }



def upload_gold_to_minio(local_path: Path, object_name: str) -> None:
    """Uploads a single Gold output file to the football-gold MinIO bucket."""
    if os.getenv("MINIO_MOCK", "0") == "1":
        logging.getLogger("run_gold").info("MINIO_MOCK=1 — skipping upload of %s", object_name)
        return
    try:
        from clients.minio_client import get_minio_client
        client = get_minio_client()
        client.fput_object("football-gold", object_name, str(local_path))
        logging.getLogger("run_gold").info("Uploaded to MinIO: football-gold/%s", object_name)
    except Exception as e:
        logging.getLogger("run_gold").error("MinIO upload FAILED for %s: %s", object_name, e)


def ensure_minio_buckets() -> None:
    """Auto-create football-silver and football-gold buckets if not present."""
    if os.getenv("MINIO_MOCK", "0") == "1":
        return
    try:
        from clients.minio_client import get_minio_client, _ensure_bucket
        client = get_minio_client()
        for bucket in ["football-bronze", "football-silver", "football-gold"]:
            _ensure_bucket(client, bucket)
        logging.getLogger("run_gold").info("MinIO buckets verified/created.")
    except Exception as e:
        logging.getLogger("run_gold").warning("Could not verify MinIO buckets: %s", e)

def main() -> None:
    args     = parse_args()
    run_date = datetime.now().strftime("%Y%m%d_%H%M%S")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(exist_ok=True)

    logger.info("=" * 60)
    logger.info("Football DSS — Gold Pipeline started")
    ensure_minio_buckets()
    logger.info("run_date: %s  |  top_n: %d", run_date, args.top_n)
    logger.info("=" * 60)

    # ── Step 1: Silver (JOIN + RENAME + COALESCE + IMPUTE + VALIDATE) ───────
    logger.info("Step 1/5 — Building Silver layer from MinIO Bronze sources...")
    df = build_silver(run_date=run_date, seed_path=SEED_PATH)
    logger.info("Silver complete: %d rows × %d columns", *df.shape)

    # ── Step 2: Normalize ─────────────────────────────────────────────────────
    logger.info("Step 2/5 — Normalizing columns to [0, 1]...")
    df = normalize_all(df)

    # ── Step 3: Compute sub-scores ────────────────────────────────────────────
    if args.use_clubelo:
        logger.info("Step 2.5/5 — Fetching real ClubElo ratings (--use-clubelo)...")
        try:
            from ingestion.loaders.clubelo_loader import load_clubelo_bronze, patch_league_quality
            clubelo_result = load_clubelo_bronze(seed_df=df)
            if clubelo_result.get("difficulty_by_league"):
                patch_league_quality(clubelo_result, seed_df=df)
                logger.info(
                    "ClubElo: patched %d leagues with real Elo-based difficulty factors "
                    "(replacing the hardcoded table for this run only — league_quality.py "
                    "itself is unchanged on disk).",
                    len(clubelo_result["difficulty_by_league"]),
                )
            else:
                logger.warning(
                    "ClubElo: fetch returned no usable data — falling back to the "
                    "hardcoded LEAGUE_DIFFICULTY table (league_quality.py). This is "
                    "the expected safe behavior on network failure, not a crash."
                )
        except Exception as e:
            logger.warning(
                "ClubElo fetch/patch failed (%s) — falling back to the hardcoded "
                "LEAGUE_DIFFICULTY table. This --use-clubelo path has not been "
                "tested end-to-end against the live API from every environment; "
                "if this keeps failing, run "
                "`python -c \"from ingestion.loaders.clubelo_loader import load_clubelo_bronze; "
                "print(load_clubelo_bronze())\"` directly to see the raw error.",
                e,
            )

    logger.info("Step 3/5 — Computing sub-scores...")
    df = compute_performance_score(df)
    df = compute_form_score(df)          # CONFLICT-01 FIX: uses form_final + rating_final
    df = compute_mental_score(df)        # CONFLICT-03 FIX: StatsBomb proxy
    df = compute_availability_score(df)
    df = compute_international_score(df)
    df = compute_chemistry_score(df)     # NEW 2026-07-14 — pairwise StatsBomb pass network
    df = compute_pressure_index(df)      # NEW 2026-07-14 — StatsBomb intl xG vs club xG delta
    df = compute_flexibility_score(df)   # NEW 2026-07-14 — UNTESTED upstream, see module docstring
    df = compute_sentiment_score(df, use_nlp=not args.no_sentiment_nlp)  # NEW 2026-07-22

    # ── Step 4: Final composite DSS score ─────────────────────────────────────
    logger.info("Step 3.5/5 — Applying minutes filter (current season activity)...")
    from analytics.scoring.minutes_filter import apply_minutes_filter as _mf
    df = _mf(df)
    inactive = df[df.get("minutes_flag", pd.Series("active", index=df.index)) == "inactive"]
    if not inactive.empty:
        logger.warning(
            "INACTIVE players (penalised -65%% AvailabilityScore): %s",
            ", ".join(inactive["player_name"].tolist())
        )

    logger.info("Step 4/5 — Computing final DSSScore and ranking...")
    df = compute_final_score(df)

    # WIRED IN 2026-07-14: was a fully-built, never-called module
    # (analytics/scoring/percentile_score.py — confirmed zero call sites
    # anywhere in the codebase before this). Adds DSSScore_pct for
    # dashboard/report DISPLAY only — never changes Rank or squad
    # selection (the module self-verifies this with a rank-mismatch check
    # logged as an error if it ever occurs).
    df = apply_percentile_scoring(df)

    # ── Step 5: Player DNA (K-Means, ML layer — NEW 2026-07-29) ───────────────
    # Descriptive/exploratory play-style tag, NOT a selection input — see
    # analytics/ml/player_clustering.py module docstring for the full
    # rationale (why this ships and Selection Classifier / Injury Predictor
    # don't, yet). Wrapped in try/except: a clustering failure must never
    # block a real squad-selection run, same pattern as --use-clubelo above.
    if args.no_player_dna:
        logger.info("Step 5/6 — Player DNA clustering SKIPPED (--no-player-dna).")
        df["PlayerDNA"] = "N/A"
        df["player_dna_cluster"] = pd.NA
        df["dna_imputed_features_count"] = pd.NA
        dna_archetype_map: dict = {}
        dna_k = None
    else:
        logger.info("Step 5/6 — Player DNA clustering (K-Means)...")
        try:
            dna_result = cluster_players(df, k=args.player_dna_k)
            df = dna_result.df
            dna_archetype_map = dna_result.archetype_map
            dna_k = dna_result.k
            if dna_result.silhouette_table:
                best_sil = dna_result.silhouette_table[dna_result.k]
                sil_str = ", ".join(f"k={k}:{v:.3f}" for k, v in sorted(dna_result.silhouette_table.items()))
                logger.info("Player DNA: k=%d chosen (silhouette=%.3f). All k tried: %s",
                             dna_result.k, best_sil, sil_str)
                if best_sil < 0.30:
                    logger.warning(
                        "Player DNA silhouette=%.3f (k=%d) is below the ~0.3-0.5 "
                        "range usually considered real cluster structure — expected "
                        "with continuous per-90 stats and %d outfield players. "
                        "Treat PlayerDNA as a descriptive dashboard tag, NOT a "
                        "selection input. See analytics/ml/player_clustering.py.",
                        best_sil, dna_result.k, dna_result.n_outfield,
                    )
            logger.info("Player DNA archetype counts:\n%s",
                         df["PlayerDNA"].value_counts().to_string())
        except Exception as e:
            logger.error(
                "Player DNA clustering FAILED (%s) — falling back to 'N/A' for "
                "everyone. Rest of the pipeline (scoring, squad selection) is "
                "unaffected, since PlayerDNA is not a scoring input.", e,
            )
            df["PlayerDNA"] = "N/A"
            df["player_dna_cluster"] = pd.NA
            df["dna_imputed_features_count"] = pd.NA
            dna_archetype_map = {}
            dna_k = None

    # ── Step 6: Export ────────────────────────────────────────────────────────
    logger.info("Step 6/6 — Exporting results...")

    # Full scored table
    full_path = output_dir / f"players_scored_{run_date}.csv"
    df.to_csv(full_path, index=False)
    logger.info("Full table: %s", full_path)
    upload_gold_to_minio(full_path, full_path.name)

    # Top-N squad recommendation — position-capped
    squad_df = select_squad_with_position_cap(df, top_n=args.top_n)
    top_df   = squad_df
    top_cols = [
        "Rank", "player_name", "position", "club", "league",
        "DSSScore", "DSSScore_pct", "PerformanceScore", "FormScore",
        "MentalScore", "AvailabilityScore", "InternationalScore",
        "ChemistryScore", "PressureIndex", "FlexibilityScore", "SentimentScore",
        "PlayerDNA",
    ]
    top_cols = [c for c in top_cols if c in top_df.columns]
    top_path = output_dir / f"top{args.top_n}_{run_date}.csv"
    top_df[top_cols].to_csv(top_path, index=False)
    logger.info("Top-%d recommendation: %s", args.top_n, top_path)
    upload_gold_to_minio(top_path, top_path.name)

    # Squad summary JSON — MUST use squad_df (position-capped), not the raw df
    summary = build_squad_summary(squad_df, df, args.top_n)
    summary["percentile_summary"] = score_summary(df, top_n=args.top_n)  # WIRED IN 2026-07-14
    # NEW 2026-07-29 — Player DNA breakdown WITHIN the selected squad only.
    # Descriptive only: never used by select_squad_with_position_cap() above,
    # squad selection already happened by the time this runs.
    summary["player_dna"] = {
        "k_chosen": dna_k,
        "archetype_map": dna_archetype_map,
        "squad_archetype_counts": (
            squad_df["PlayerDNA"].value_counts().to_dict()
            if "PlayerDNA" in squad_df.columns else {}
        ),
    }
    summary_path = output_dir / f"squad_summary_{run_date}.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    logger.info("Squad summary: %s", summary_path)
    upload_gold_to_minio(summary_path, summary_path.name)

    # ── Guard rail: catch this regression automatically next time ───────────
    default_caps = {"GK": 3, "DEF": 9, "MID": 9, "FWD": 5}
    for pos, cap in default_caps.items():
        actual = summary["position_counts"].get(pos, 0)
        if actual != cap:
            logger.error(
                "POSITION CAP VIOLATION in squad_summary: %s = %d (expected %d). "
                "build_squad_summary() may be reading the wrong DataFrame again.",
                pos, actual, cap,
            )
    assert summary["squad_size"] == args.top_n, (
        f"squad_summary squad_size={summary['squad_size']} != top_n={args.top_n} "
        "— position cap + fill-remaining logic produced the wrong squad size."
    )

    # ── Print top 26 to console ───────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("TOP %d RECOMMENDED PLAYERS:", args.top_n)
    logger.info("-" * 60)
    for _, row in top_df[top_cols].iterrows():
        logger.info(
            "#%-3d %-28s %-8s %-25s DSS=%.4f  DNA=%s",
            row["Rank"], row["player_name"], row["position"],
            row.get("club", "")[:24], row["DSSScore"], row.get("PlayerDNA", "N/A"),
        )
    logger.info("=" * 60)


if __name__ == "__main__":
    main()