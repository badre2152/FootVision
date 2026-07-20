#!/usr/bin/env python
# run_bronze.py - Football DSS Bronze Layer Orchestrator v18
#
# Runs all Bronze loaders in sequence, writes results to MinIO.
# Silver reads from MinIO only - this script is the ONLY place that calls loaders.
#
# FIX B-06: silver_pipeline.py in v15 imported Bronze loaders directly.
#            That violates the Medallion boundary. Bronze is orchestrated HERE only.
# FIX C-04 (v17): --fail-fast flag raises RuntimeError on any FAILED upload.
# v18: Added FlashScore loader (Source 15 - rating fallback #2, ADR-018 chain).
#      SofaScore import is guarded (loader file currently exports flashscore fn).
#
# Usage:
#   python run_bronze.py                              # Full ingestion - all 9 sources
#   python run_bronze.py --limit 5                   # Quick test on 5 players
#   python run_bronze.py --only-flashscore           # FlashScore only (fastest test)
#   python run_bronze.py --only-flashscore --limit 3 # FlashScore, 3 players
#   python run_bronze.py --skip-understat --skip-sofascore   # Skip slow scrapers
#   python run_bronze.py --fail-fast                 # Abort on first MinIO failure (CI/CD)

import argparse
import logging
import sys
from datetime import datetime

import pandas as pd

# Existing loaders (pre-v16)
from ingestion.loaders.wikidata_loader             import load_wikidata_bronze
from ingestion.loaders.statsbomb_loader            import load_statsbomb_bronze
from ingestion.loaders.fbref_loader                import load_fbref_bronze
from ingestion.loaders.transfermarkt_loader        import load_transfermarkt_bronze

# New loaders (v16+)
from ingestion.loaders.understat_loader            import load_understat_bronze
from ingestion.loaders.transfermarkt_injury_loader import load_injury_bronze
from ingestion.loaders.fbref_intl_loader           import load_intl_bronze

# v21 addition: local CSV fallback for FBref club stats (Cloudflare bypass
# workaround). Covers Big-5 leagues only (~24/49) - see module docstring in
# club_stats_csv_loader.py for exact column coverage and limitations.
from ingestion.loaders.club_stats_csv_loader        import load_club_stats_csv_bronze

# SofaScore - guarded import (sofascore_loader.py currently contains flashscore
# implementation due to a file naming issue during dev; load_sofascore_bronze
# may not exist in that file)
try:
    from ingestion.loaders.sofascore_loader import load_sofascore_bronze
except ImportError:
    load_sofascore_bronze = None

# SofaScore detail (career + season tabs, DOM scrape) - complements
# sofascore_loader.py's __NEXT_DATA__ extraction with season/tournament
# breakdowns not present there. Migrated from sofatest/sofascore_loader.py.
try:
    from ingestion.loaders.sofascore_detail_loader import load_sofascore_detail_bronze
except ImportError:
    load_sofascore_detail_bronze = None

# v18 - FlashScore rating fallback (Source 15, ADR-018 chain)
# flashscore_loader.py is the canonical file; sofascore_loader.py is the fallback
# in case the file hasn't been copied yet
try:
    from ingestion.loaders.flashscore_loader import load_flashscore_bronze
except ImportError:
    from ingestion.loaders.sofascore_loader  import load_flashscore_bronze

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"logs/bronze_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("run_bronze")

SEED_PATH = "seeds/players_49.csv"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Football DSS - Bronze Layer Ingestion v18",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_bronze.py                              Full ingestion (all 9 sources, 49 players)
  python run_bronze.py --limit 3                   Test mode - 3 players only
  python run_bronze.py --only-flashscore           FlashScore only (rating scraper test)
  python run_bronze.py --only-flashscore --limit 3 FlashScore, 3 players (quickest test)
  python run_bronze.py --fail-fast                 Abort immediately on any MinIO write failure
  python run_bronze.py --skip-sofascore            Skip SofaScore if rate-limited
  python run_bronze.py --skip-sofascore-detail     Skip SofaScore career+season detail (slow DOM scrape)
        """,
    )
    # ── Source skips ──────────────────────────────────────────────────────────
    p.add_argument("--skip-wikidata",       action="store_true", help="Skip Wikidata loader")
    p.add_argument("--skip-statsbomb",      action="store_true", help="Skip StatsBomb loader")
    p.add_argument("--skip-fbref",          action="store_true", help="Skip FBref club stats loader")
    p.add_argument("--skip-transfermarkt",  action="store_true", help="Skip Transfermarkt market value loader")
    p.add_argument("--skip-understat",      action="store_true", help="Skip Understat npxG/xA loader")
    p.add_argument("--skip-sofascore",      action="store_true", help="Skip SofaScore ratings loader")
    p.add_argument("--skip-sofascore-detail", action="store_true", help="Skip SofaScore career+season detail loader (DOM scrape)")
    p.add_argument("--skip-injury",         action="store_true", help="Skip Transfermarkt injury loader")
    p.add_argument("--skip-intl",           action="store_true", help="Skip FBref international stats loader")
    p.add_argument("--skip-flashscore",     action="store_true", help="Skip FlashScore ratings loader")
    p.add_argument("--skip-club-stats-csv", action="store_true", help="Skip local CSV club stats loader (Big-5 leagues, Cloudflare-bypass workaround)")
    # ── Shortcut: run FlashScore only ─────────────────────────────────────────
    p.add_argument(
        "--only-flashscore",
        action="store_true",
        help="Run FlashScore loader only - skips all other sources. Useful for isolated testing.",
    )
    # ── Run options ───────────────────────────────────────────────────────────
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit ingestion to first N players (testing mode)",
    )
    p.add_argument(
        "--fail-fast",
        action="store_true",
        help=(
            "FIX C-04: Abort immediately if any Bronze source upload returns FAILED. "
            "Use in CI/CD to catch MinIO connectivity issues early."
        ),
    )
    return p.parse_args()


def main() -> None:
    args     = parse_args()
    run_date = datetime.now().strftime("%Y%m%d_%H%M%S")

    # --only-flashscore sets all other skips automatically
    if args.only_flashscore:
        args.skip_wikidata      = True
        args.skip_statsbomb     = True
        args.skip_fbref         = True
        args.skip_transfermarkt = True
        args.skip_understat     = True
        args.skip_sofascore     = True
        args.skip_sofascore_detail = True
        args.skip_injury        = True
        args.skip_intl          = True
        args.skip_flashscore    = False
        args.skip_club_stats_csv = True

    logger.info("=" * 60)
    logger.info("Football DSS - Bronze ingestion v18 started")
    logger.info("run_date   : %s", run_date)
    logger.info("fail-fast  : %s", args.fail_fast)
    logger.info("limit      : %s", args.limit or "all 49 players")
    if args.only_flashscore:
        logger.info("mode       : FlashScore only (--only-flashscore)")
    logger.info("=" * 60)

    # ── Load seed CSV ─────────────────────────────────────────────────────────
    try:
        seed_df = pd.read_csv(SEED_PATH)
    except FileNotFoundError:
        logger.error("Seed file not found: %s", SEED_PATH)
        sys.exit(1)

    if len(seed_df) != 49:
        logger.error("Expected 49 players in seed, got %d. Check players_49.csv.", len(seed_df))
        sys.exit(1)

    if args.limit:
        seed_df = seed_df.head(args.limit)
        logger.info("TEST MODE: limited to first %d players", args.limit)

    results: dict[str, str] = {}

    # ── FIX C-04: _run() wrapper raises on FAILED when --fail-fast is set ────
    def _run(name: str, fn, *fn_args) -> None:
        logger.info(">> Starting %s ...", name)
        try:
            status = fn(*fn_args)
        except Exception as e:
            status = "FAILED"
            logger.error("[%s] Loader raised exception: %s", name, e, exc_info=True)

        results[name] = status
        symbol = "[OK]" if status.startswith("OK") else "[XX]"
        logger.info("%s %s -> %s", symbol, name, status)

        if args.fail_fast and status == "FAILED":
            raise RuntimeError(
                f"Bronze source '{name}' upload FAILED and --fail-fast is set. "
                "Check MinIO connectivity and loader logs above."
            )

    # ── Run loaders ───────────────────────────────────────────────────────────
    if not args.skip_wikidata:
        _run("wikidata",      load_wikidata_bronze,      seed_df, run_date)
    if not args.skip_statsbomb:
        _run("statsbomb",     load_statsbomb_bronze,     seed_df, run_date)
    if not args.skip_fbref:
        _run("fbref",         load_fbref_bronze,         seed_df, run_date)
    if not args.skip_transfermarkt:
        _run("transfermarkt", load_transfermarkt_bronze, seed_df, run_date)
    if not args.skip_understat:
        _run("understat",     load_understat_bronze,     seed_df, run_date)
    if not args.skip_sofascore:
        if load_sofascore_bronze:
            _run("sofascore", load_sofascore_bronze, seed_df, run_date)
        else:
            logger.warning("SofaScore loader not available - skipping (sofascore_loader.py has no load_sofascore_bronze)")
    if not args.skip_sofascore_detail:
        if load_sofascore_detail_bronze:
            _run("sofascore_detail", load_sofascore_detail_bronze, seed_df, run_date)
        else:
            logger.warning("SofaScore detail loader not available - skipping (sofascore_detail_loader.py import failed)")
    if not args.skip_injury:
        _run("injury",        load_injury_bronze,        seed_df, run_date)
    if not args.skip_intl:
        _run("intl",          load_intl_bronze,          seed_df, run_date)
    if not args.skip_flashscore:
        _run("flashscore",    load_flashscore_bronze,    seed_df, run_date)
    if not args.skip_club_stats_csv:
        _run("club_stats_csv", load_club_stats_csv_bronze, seed_df, run_date)

    # ── Summary ───────────────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("Bronze ingestion complete - %d sources ran", len(results))
    logger.info("-" * 60)
    for source, status in results.items():
        symbol = "[OK]" if status.startswith("OK") else "[XX]"
        logger.info("  %s  %-25s %s", symbol, source, status)

    failed = [s for s, st in results.items() if st == "FAILED"]
    if failed:
        logger.warning("FAILED sources: %s", failed)
        logger.warning("Silver will merge with missing columns for these sources.")
        sys.exit(1)
    else:
        logger.info("All sources completed successfully.")
        logger.info("=" * 60)


if __name__ == "__main__":
    main()