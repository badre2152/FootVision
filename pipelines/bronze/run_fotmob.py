import argparse
import json
import logging
import sys
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("run_fotmob")


def parse_args():
    p = argparse.ArgumentParser(description="Fotmob Bronze Loader")
    p.add_argument("--limit",   type=int,  default=None,  help="Process only first N players")
    p.add_argument("--pid",     nargs="+", default=None,  help="Specific player IDs")
    p.add_argument("--seed",    default="seeds/players_49.csv")
    p.add_argument("--dry-run", action="store_true", help="Skip MinIO upload")
    p.add_argument(
        "--as-of", default=None,
        help=(
            "YYYY-MM-DD decision-date cutoff (config.DECISION_DATE by default). "
            "NOTE: Fotmob loader reads a fixed TARGET_SEASON string "
            "('2025/2026', see fotmob_loader.py), not a datetime.now()-relative "
            "window, so it is not currently affected by real-time leakage. This "
            "flag is accepted for CLI consistency (01_temporal_cutoff.md req. #3) "
            "and logged only."
        ),
    )
    return p.parse_args()


def main():
    args     = parse_args()
    from config import DECISION_DATE
    as_of    = args.as_of or DECISION_DATE.isoformat()
    run_date = datetime.now().strftime("%Y%m%d_%H%M%S")

    from ingestion.loaders.fotmob_loader import run_fotmob_loader, FOTMOB_IDS
    from clients.minio_client import upload_json

    target_pids = args.pid if args.pid else list(FOTMOB_IDS.keys())
    if args.limit:
        target_pids = target_pids[:args.limit]

    logger.info("=" * 60)
    logger.info("Fotmob Bronze Loader — run_date: %s | as-of: %s", run_date, as_of)
    logger.info("targets  : %d players", len(target_pids))
    logger.info("dry-run  : %s", args.dry_run)
    logger.info("=" * 60)

    results = run_fotmob_loader(seed_path=args.seed, target_pids=target_pids)

    logger.info("=" * 60)
    logger.info("COVERAGE REPORT:")
    cols = [
        ("fm_gls_per90",  "Gls/90"),
        ("fm_ast_per90",  "Ast/90"),
        ("fm_xg_per90",   "xG/90"),
        ("fm_tkl_per90",  "Tkl/90 ← GAP C"),
        ("fm_int_per90",  "Int/90 ← GAP C"),
        ("fm_clr_per90",  "Clr/90 ← GAP C"),
        ("fm_ga_per90",   "GA/90 (GK)"),
    ]
    total_with_stats = 0
    for col, label in cols:
        n = sum(1 for r in results if r.get(col) is not None)
        bar = "█" * n + "░" * (len(results) - n)
        logger.info("  %-22s %s %d/%d  [%s]", col, bar[:15], n, len(results), label)
        if col == "fm_gls_per90":
            total_with_stats = n

    if total_with_stats == 0:
        logger.warning("0 players with stats — check if IDs are correct or API changed")
        if not args.dry_run:
            logger.warning("Skipping upload — no data to write")
            return

    if args.dry_run:
        logger.info("DRY RUN — skipping upload")
        logger.info("Sample: %s", json.dumps(results[0], indent=2))
        return

    object_name = f"fotmob/players_fotmob_{run_date}.json"
    upload_json(bucket="football-bronze", object_name=object_name, data=results)
    logger.info("Uploaded → s3://football-bronze/%s", object_name)
    logger.info("Next: python run_silver.py")


if __name__ == "__main__":
    main()