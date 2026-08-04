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
from analytics.scoring.flexibility_score import (
    compute_flexibility_score,       # NEW 2026-07-14 (UNTESTED upstream — see module docstring)
    secondary_position_groups,       # NEW 2026-08-01 (spec #13) — TM label -> GK/DEF/MID/FWD
)
from analytics.scoring.sentiment_score import compute_sentiment_score      # NEW 2026-07-22 (UNTESTED end-to-end — see module docstring)
from analytics.scoring.percentile_score import apply_percentile_scoring, score_summary  # WIRED IN 2026-07-14 (was orphaned)
from analytics.ml.player_clustering import cluster_players  # NEW 2026-07-29 — ML layer, see module docstring

# C2 reorg note: select_squad_with_position_cap() and build_squad_summary()
# used to be defined right here in this script (~270 lines of squad-selection
# domain logic living inside an entrypoint). They now live in
# analytics/squad/squad_selection.py — this import re-exports them so
# `run_gold.select_squad_with_position_cap` / `run_gold.build_squad_summary`
# still resolve for existing callers (including tests/pipelines/*.py, which
# import this module directly to reach them) without any change on their end.
from analytics.squad.squad_selection import (  # noqa: F401
    select_squad_with_position_cap,
    build_squad_summary,
    DEFAULT_POSITION_CAPS,
)


# ══════════════════════════════════════════════════════════════════════════
# TOP_EXPORT_COLS / build_top_cols() — regression fix (03_regression_protection.md)
# ══════════════════════════════════════════════════════════════════════════
# HISTORY: this used to be a local `top_cols = [...]` list inline in main(),
# built without "SquadRank". That was fixed once (SquadRank added), then the
# fix was silently lost again in a later delivered archive because nothing
# asserted on the column list's actual contents — see
# tests/test_regression_protection.py::TestTopColsIncludesSquadRank, which
# now pins this exact behavior so it can't regress again unnoticed.
#
# Pulled out to a module-level constant + a testable function (rather than
# an inline list inside main()) specifically so a test can import run_gold
# and call build_top_cols() directly, without running the full pipeline.
TOP_EXPORT_COLS = [
    "SquadRank", "Rank", "player_id", "player_name", "position", "club", "league",
    "DSSScore", "DSSScore_pct", "PerformanceScore", "FormScore",
    "MentalScore", "AvailabilityScore", "InternationalScore",
    "ChemistryScore", "PressureIndex", "FlexibilityScore", "SentimentScore",
    "PlayerDNA", "selection_reason",  # NEW 2026-08-01 (spec #13)
]


def build_top_cols(available_columns) -> list[str]:
    """
    Returns TOP_EXPORT_COLS filtered down to whichever of those columns are
    actually present in `available_columns` (preserving TOP_EXPORT_COLS's
    order). Columns not present are silently dropped — this is a
    defensive/optional-column filter, not a validation step, so a squad_df
    missing e.g. "selection_reason" (or, in principle, "SquadRank") must not
    raise here.

    Args:
        available_columns: any iterable of column names — typically
            `squad_df.columns`, but plain lists work too (see
            tests/test_regression_protection.py).

    Returns:
        The subset of TOP_EXPORT_COLS present in available_columns, in
        TOP_EXPORT_COLS's canonical order.
    """
    available = set(available_columns)
    return [c for c in TOP_EXPORT_COLS if c in available]

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
    p.add_argument(
        "--no-flexibility-tiebreak", action="store_true",
        help=(
            "Disable the spec #13 flexibility-aware tiebreak in "
            "select_squad_with_position_cap() — falls back to pure DSSScore "
            "rank order for every slot, including near-ties. Useful for "
            "comparing squads with/without the tiebreak, or if "
            "tm_secondary_positions data is suspect for this run."
        ),
    )
    p.add_argument(
        "--flex-tiebreak-threshold", type=float, default=0.01,
        help=(
            "DSSScore gap (default 0.01) at or below which two candidates "
            "for a position group's last cap slot are considered 'close "
            "enough' for the spec #13 flexibility tiebreak to apply. This is "
            "a product-owner-tunable margin, not a hardcoded constant — see "
            "13_flexibility_in_selection_logic.md's Implementation Plan #1."
        ),
    )
    p.add_argument(
        "--depth-chart-surplus-threshold", type=int, default=3,
        help=(
            "A position group is treated as 'understaffed relative to its "
            "depth chart' (spec #13) when it has this many or fewer spare "
            "candidates above its hard cap in the full candidate pool "
            "(e.g. MID with 11 candidates for a cap of 9 has a surplus of "
            "only 2). Default 3."
        ),
    )
    return p.parse_args()


# NOTE: apply_minutes_filter() used to be defined here as a full duplicate
# of analytics/scoring/minutes_filter.py — dead code, never called (main()
# always imported the real implementation from that module — see below).
# Removed to stop the two copies from silently drifting apart again (this
# is exactly how the w.get() weight-key bug in minutes_filter.py happened:
# someone fixed one copy and not the other). The single source of truth for
# minutes-based availability penalties is analytics/scoring/minutes_filter.py.



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

# ══════════════════════════════════════════════════════════════════════════
# main() decomposition (C4 in the architecture review)
# ══════════════════════════════════════════════════════════════════════════
# main() used to be a single ~220-line function doing orchestration + data
# enrichment + ML fallback handling + file I/O + validation + console
# reporting all in one place — impossible to unit-test any one piece without
# running the whole pipeline. Split into the stage functions below; main()
# itself is now purely sequencing them. Every log message, exception-handling
# path, and computation is copied verbatim — only the function boundaries are
# new, nothing about what runs or in what order has changed.


def _enrich_with_clubelo(df: pd.DataFrame, args: argparse.Namespace) -> None:
    """
    Step 2.5/5 (optional, --use-clubelo only). Patches the module-level
    LEAGUE_DIFFICULTY table in analytics/scoring/league_quality.py in place
    for this run — does not modify or return `df` itself. A fetch/patch
    failure must never block a real squad-selection run, so this always
    falls back to the hardcoded table on any error.
    """
    if not args.use_clubelo:
        return

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


def _compute_all_subscores(df: pd.DataFrame, args: argparse.Namespace) -> pd.DataFrame:
    """Step 3/5 — the full sub-score chain (Performance, Form, Mental, ...)."""
    df = compute_performance_score(df)
    df = compute_form_score(df)          # CONFLICT-01 FIX: uses form_final + rating_final
    df = compute_mental_score(df)        # CONFLICT-03 FIX: StatsBomb proxy
    df = compute_availability_score(df)
    df = compute_international_score(df)
    df = compute_chemistry_score(df)     # NEW 2026-07-14 — pairwise StatsBomb pass network
    df = compute_pressure_index(df)      # NEW 2026-07-14 — StatsBomb intl xG vs club xG delta
    df = compute_flexibility_score(df)   # NEW 2026-07-14 — UNTESTED upstream, see module docstring
    df = compute_sentiment_score(df, use_nlp=not args.no_sentiment_nlp)  # NEW 2026-07-22
    return df


def _apply_final_scoring(df: pd.DataFrame) -> pd.DataFrame:
    """
    Step 4/5 — minutes filter, final DSSScore + rank, the post-scoring DSS
    gate, and percentile scoring.

    BUG-01 FIX (03_regression_protection.md / analytics/scoring/minutes_filter.py):
    apply_dss_gate() MUST run AFTER compute_final_score(), never before.
    apply_minutes_filter() only classifies activity + penalizes
    AvailabilityScore; the actual DSSScore-level gate (the one that stops an
    injured/inactive player like Imran Louza topping the ranking on the
    strength of historical stats alone) is applied to the real freshly-
    computed DSSScore. Reordering these two calls silently reintroduces
    BUG-01 — no exception is raised, the gate's effect just vanishes, so
    tests/pipelines/test_gold2_components.py's TestMinutesFilterWeightBinding
    class asserts on the actual numeric effect, not just that the functions
    were called. Keeping both calls inside this one function (in this fixed
    order) is what used to be enforced only by a comment two functions apart
    inside main() — see the pipeline-ordering guard in C3 for the next step
    beyond even this.
    """
    logger.info("Step 3.5/5 — Applying minutes filter (current season activity)...")
    from analytics.scoring.minutes_filter import apply_minutes_filter as _mf
    df = _mf(df)
    inactive = df[df.get("minutes_flag", pd.Series("ACTIVE", index=df.index)).isin(["INACTIVE", "SEED_INACTIVE"])]
    if not inactive.empty:
        logger.warning(
            "INACTIVE/SEED_INACTIVE players (AvailabilityScore penalised, DSSScore gate applied after scoring): %s",
            ", ".join(inactive["player_name"].tolist())
        )

    logger.info("Step 4/5 — Computing final DSSScore and ranking...")
    df = compute_final_score(df)

    from analytics.scoring.minutes_filter import apply_dss_gate
    df = apply_dss_gate(df)

    # WIRED IN 2026-07-14: was a fully-built, never-called module
    # (analytics/scoring/percentile_score.py — confirmed zero call sites
    # anywhere in the codebase before this). Adds DSSScore_pct for
    # dashboard/report DISPLAY only — never changes Rank or squad
    # selection (the module self-verifies this with a rank-mismatch check
    # logged as an error if it ever occurs).
    df = apply_percentile_scoring(df)
    return df


def _run_player_dna(df: pd.DataFrame, args: argparse.Namespace) -> tuple[pd.DataFrame, dict, int | None]:
    """
    Step 5/6 — Player DNA (K-Means, ML layer). Descriptive/exploratory
    play-style tag, NOT a selection input — see
    analytics/ml/player_clustering.py module docstring for the full
    rationale. Wrapped in try/except: a clustering failure must never block
    a real squad-selection run, same pattern as --use-clubelo.

    Returns (df, dna_archetype_map, dna_k).
    """
    if args.no_player_dna:
        logger.info("Step 5/6 — Player DNA clustering SKIPPED (--no-player-dna).")
        df["PlayerDNA"] = "N/A"
        df["player_dna_cluster"] = pd.NA
        df["dna_imputed_features_count"] = pd.NA
        return df, {}, None

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
        return df, dna_archetype_map, dna_k
    except Exception as e:
        logger.error(
            "Player DNA clustering FAILED (%s) — falling back to 'N/A' for "
            "everyone. Rest of the pipeline (scoring, squad selection) is "
            "unaffected, since PlayerDNA is not a scoring input.", e,
        )
        df["PlayerDNA"] = "N/A"
        df["player_dna_cluster"] = pd.NA
        df["dna_imputed_features_count"] = pd.NA
        return df, {}, None


def _export_full_table(df: pd.DataFrame, output_dir: Path, run_date: str) -> Path:
    """Exports the full scored table (all candidates) and uploads it to Gold."""
    full_path = output_dir / f"players_scored_{run_date}.csv"
    df.to_csv(full_path, index=False)
    logger.info("Full table: %s", full_path)
    upload_gold_to_minio(full_path, full_path.name)
    return full_path


def _select_and_export_squad(
    df: pd.DataFrame, args: argparse.Namespace, output_dir: Path, run_date: str,
) -> tuple[pd.DataFrame, list[str]]:
    """Runs position-capped squad selection and exports top-N.csv. Returns (squad_df, top_cols)."""
    squad_df = select_squad_with_position_cap(
        df,
        top_n=args.top_n,
        use_flexibility_tiebreak=not args.no_flexibility_tiebreak,
        flex_tiebreak_threshold=args.flex_tiebreak_threshold,
        depth_chart_surplus_threshold=args.depth_chart_surplus_threshold,
    )
    top_cols = build_top_cols(squad_df.columns)
    top_path = output_dir / f"top{args.top_n}_{run_date}.csv"
    squad_df[top_cols].to_csv(top_path, index=False)
    logger.info("Top-%d recommendation: %s", args.top_n, top_path)
    upload_gold_to_minio(top_path, top_path.name)
    return squad_df, top_cols


def _build_and_export_summary(
    squad_df: pd.DataFrame, df: pd.DataFrame, args: argparse.Namespace,
    output_dir: Path, run_date: str, dna_archetype_map: dict, dna_k: int | None,
) -> dict:
    """Builds squad_summary.json (position breakdown, flags, Player DNA breakdown) and exports it."""
    summary = build_squad_summary(squad_df, df, args.top_n)
    summary["percentile_summary"] = score_summary(df, top_n=args.top_n)  # WIRED IN 2026-07-14
    # NEW 2026-07-29 — Player DNA breakdown WITHIN the selected squad only.
    # Descriptive only: never used by select_squad_with_position_cap(),
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
    return summary


def _check_guard_rails(summary: dict, args: argparse.Namespace) -> None:
    """
    Post-export sanity checks — catches the exact class of regression
    documented in build_squad_summary()'s docstring (position_breakdown
    silently computed from the wrong DataFrame) automatically, instead of
    relying on someone noticing an impossible squad composition by eye.
    """
    for pos, cap in DEFAULT_POSITION_CAPS.as_dict().items():
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


def _print_top_squad(top_df: pd.DataFrame, top_cols: list[str], args: argparse.Namespace) -> None:
    """Prints the final top-N squad to the console/log."""
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

    # ── Step 2.5 (optional): ClubElo enrichment ──────────────────────────────
    _enrich_with_clubelo(df, args)

    # ── Step 3: Sub-scores ────────────────────────────────────────────────────
    logger.info("Step 3/5 — Computing sub-scores...")
    df = _compute_all_subscores(df, args)

    # ── Step 4: Final composite DSS score ─────────────────────────────────────
    df = _apply_final_scoring(df)

    # ── Step 5: Player DNA ────────────────────────────────────────────────────
    df, dna_archetype_map, dna_k = _run_player_dna(df, args)

    # ── Step 6: Export ────────────────────────────────────────────────────────
    logger.info("Step 6/6 — Exporting results...")
    _export_full_table(df, output_dir, run_date)
    squad_df, top_cols = _select_and_export_squad(df, args, output_dir, run_date)
    summary = _build_and_export_summary(
        squad_df, df, args, output_dir, run_date, dna_archetype_map, dna_k
    )

    _check_guard_rails(summary, args)
    _print_top_squad(squad_df, top_cols, args)


if __name__ == "__main__":
    main()