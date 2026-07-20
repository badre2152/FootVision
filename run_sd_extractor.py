#!/usr/bin/env python
# run_sd_extractor.py — SofaScore Detail Extractor
#
# Reads sofascore_detail/ from MinIO Bronze,
# extracts sd_* columns (Gls/Ast/xG/xA/Tkl/Int/Clr per90),
# uploads to football-bronze/sofascore_detail_extracted/
#
# Run AFTER run_bronze.py, BEFORE run_silver.py:
#   python run_sd_extractor.py              # all 49 players
#   python run_sd_extractor.py --limit 5   # first 5 players (test)
#   python run_sd_extractor.py --limit 1   # single player (quick check)

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
logger = logging.getLogger("run_sd_extractor")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SofaScore Detail Extractor")
    p.add_argument(
        "--limit", type=int, default=None,
        help="Process only first N players (default: all 49). Useful for testing.",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Extract but do NOT upload to MinIO.",
    )
    return p.parse_args()


def main():
    args     = parse_args()
    run_date = datetime.now().strftime("%Y%m%d_%H%M%S")

    logger.info("=" * 60)
    logger.info("SofaScore Detail Extractor — run_date: %s", run_date)
    logger.info("limit    : %s", args.limit if args.limit else "all 49")
    logger.info("dry-run  : %s", args.dry_run)
    logger.info("=" * 60)

    from clients.minio_client import read_latest_json, upload_json
    from ingestion.loaders.sofascore_detail_extractor import extract_player

    # Read latest sofascore_detail from MinIO
    raw = read_latest_json(bucket="football-bronze", prefix="sofascore_detail/")
    if not raw:
        logger.error("No sofascore_detail data found in MinIO — run run_bronze.py first.")
        sys.exit(1)

    logger.info("Loaded %d records from sofascore_detail/", len(raw))

    # Apply limit BEFORE extraction
    if args.limit:
        raw = raw[:args.limit]
        logger.info("Limiting to first %d players", args.limit)

    # Extract — one player at a time
    results = []
    ok = skipped = 0

    for i, record in enumerate(raw, 1):
        pid = record.get("player_id", "?")

        if len(record) <= 1:
            logger.info("[%d/%d] %s — no detail data, skipping", i, len(raw), pid)
            results.append({"player_id": pid})
            skipped += 1
            continue

        extracted = extract_player(record)
        results.append(extracted)

        has_stats = (
            extracted.get("sd_gls_per90") is not None
            or extracted.get("sd_tkl_per90") is not None
            or extracted.get("sd_ga_per90") is not None
        )
        if has_stats:
            ok += 1

        logger.info(
            "[%d/%d] %s — gls/90=%s tkl/90=%s int/90=%s ga/90=%s",
            i, len(raw), pid,
            extracted.get("sd_gls_per90", "—"),
            extracted.get("sd_tkl_per90", "—"),
            extracted.get("sd_int_per90", "—"),
            extracted.get("sd_ga_per90",  "—"),
        )

    logger.info("-" * 60)
    logger.info(
        "Extracted: %d/%d with stats | %d skipped (no detail data)",
        ok, len(results), skipped,
    )

    # Coverage summary
    logger.info("COVERAGE:")
    cols = [
        ("sd_gls_per90",   "Gls/90"),
        ("sd_ast_per90",   "Ast/90"),
        ("sd_xg_per90",    "xG/90"),
        ("sd_xa_per90",    "xA/90"),
        ("sd_tkl_per90",   "Tkl/90 ← GAP 1"),
        ("sd_int_per90",   "Int/90 ← GAP 1"),
        ("sd_clr_per90",   "Clr/90 ← GAP 1"),
        ("sd_ga_per90",    "GA/90  (GK)"),
        ("sd_saves_per90", "Saves/90 (GK)"),
    ]
    for col, label in cols:
        n = sum(1 for r in results if r.get(col) is not None)
        bar = "█" * n + "░" * (len(results) - n)
        logger.info("  %-22s %s %d/%d  [%s]", col, bar[:15], n, len(results), label)

    # Upload to MinIO
    if args.dry_run:
        logger.info("DRY RUN — skipping MinIO upload.")
        logger.info("Sample output (first player):")
        logger.info(json.dumps(results[0], indent=2, ensure_ascii=False))
        return

    # FIX: upload_json takes object_name (full path), not prefix + filename separately
    object_name = f"sofascore_detail_extracted/players_sd_extracted_{run_date}.json"
    upload_json(
        bucket="football-bronze",
        object_name=object_name,
        data=results,
    )
    logger.info("Uploaded → s3://football-bronze/%s", object_name)
    logger.info("=" * 60)
    logger.info("Done — next: python run_fotmob.py  OR  python run_silver.py")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()