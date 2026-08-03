#!/usr/bin/env python
# run_player_dna.py — Football DSS ML Layer — Player DNA Clustering
#
# Reads the latest gold/players_scored_*.csv (produced by run_gold.py),
# runs K-Means play-style clustering on outfield players (see
# analytics/ml/player_clustering.py for the full design rationale and why
# Selection Classifier / Injury Predictor are NOT included here), and
# exports:
#   1. gold/player_dna_YYYYMMDD_HHMMSS.csv   — full table + PlayerDNA column
#   2. gold/player_dna_summary_YYYYMMDD_HHMMSS.json — archetype counts,
#      chosen k, silhouette diagnostics, centroid table, feature coverage
#
# Run AFTER run_gold.py:
#   python run_player_dna.py
#   python run_player_dna.py --k 4          # force k instead of auto-selecting
#   python run_player_dna.py --input gold/players_scored_20260727_112534.csv

import argparse
import glob
import json
import logging
import os
from datetime import datetime

import pandas as pd

from analytics.ml.player_clustering import cluster_players

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Football DSS — Player DNA Clustering (ML Layer)")
    p.add_argument(
        "--input", default=None,
        help="Path to a players_scored_*.csv. Defaults to the most recent file "
             "in gold/ by filename sort (same convention RUN_INSTRUCTIONS.md uses).",
    )
    p.add_argument(
        "--k", type=int, default=None,
        help="Force this many clusters instead of auto-selecting via silhouette "
             "score over k=3..6.",
    )
    p.add_argument("--k-min", type=int, default=3)
    p.add_argument("--k-max", type=int, default=6)
    p.add_argument("--random-state", type=int, default=42)
    p.add_argument("--outdir", default="gold")
    return p.parse_args()


def _latest_scored_csv() -> str:
    candidates = sorted(glob.glob("gold/players_scored_*.csv"))
    if not candidates:
        raise FileNotFoundError(
            "No gold/players_scored_*.csv found — run run_gold.py first."
        )
    return candidates[-1]


def main() -> None:
    args = parse_args()
    input_path = args.input or _latest_scored_csv()
    logger.info("Reading %s", input_path)
    df = pd.read_csv(input_path)
    logger.info("Loaded %d players, %d columns", len(df), df.shape[1])

    result = cluster_players(
        df,
        k=args.k,
        k_range=(args.k_min, args.k_max),
        random_state=args.random_state,
    )

    if result.silhouette_table:
        table_str = ", ".join(f"k={k}: {v:.4f}" for k, v in sorted(result.silhouette_table.items()))
        logger.info("Silhouette scores tried: %s", table_str)
        best = result.silhouette_table[result.k]
        if best < 0.30:
            logger.warning(
                "Best silhouette score is %.3f (k=%d) — below the ~0.3-0.5 range "
                "usually considered 'real' cluster structure. Treat PlayerDNA as a "
                "descriptive/exploratory tag for the dashboard, not a confident "
                "hard categorization. This is expected with continuous football "
                "per-90 stats and n=%d — it is not a bug in this module.",
                best, result.k, result.n_outfield,
            )
    logger.info("Chosen k=%d — archetype map: %s", result.k, result.archetype_map)
    logger.info("Archetype counts:\n%s", result.df["PlayerDNA"].value_counts().to_string())

    os.makedirs(args.outdir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    out_csv = os.path.join(args.outdir, f"player_dna_{ts}.csv")
    keep_cols = [c for c in [
        "player_id", "player_name", "position", "club", "league",
        "PlayerDNA", "player_dna_cluster", "dna_imputed_features_count", "DSSScore", "Rank",
    ] if c in result.df.columns]
    result.df[keep_cols].to_csv(out_csv, index=False)
    logger.info("Wrote %s", out_csv)

    summary = {
        "generated_at": datetime.now().isoformat(),
        "source_file": input_path,
        "k_chosen": result.k,
        "silhouette_table": result.silhouette_table,
        "n_outfield": result.n_outfield,
        "n_gk": result.n_gk,
        "archetype_map": result.archetype_map,
        "archetype_counts": result.df["PlayerDNA"].value_counts().to_dict(),
        "feature_coverage_outfield": result.feature_coverage,
        "centroids": result.centroids.round(4).to_dict(orient="index"),
        "players_with_imputed_features": result.df[
            result.df["dna_imputed_features_count"] > 0
        ][["player_name", "dna_imputed_features_count"]].to_dict(orient="records"),
    }
    out_json = os.path.join(args.outdir, f"player_dna_summary_{ts}.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    logger.info("Wrote %s", out_json)


if __name__ == "__main__":
    main()
