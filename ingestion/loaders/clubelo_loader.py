# ingestion/loaders/clubelo_loader.py
#
# Source: ClubElo API — http://api.clubelo.com
# يُوفر Elo rating موضوعي لكل نادٍ بناءً على نتائج تاريخية حقيقية
#
# الاستخدام في المشروع:
#   يُستبدل LEAGUE_DIFFICULTY dict اليدوي في league_quality.py
#   بأرقام Elo حقيقية: club_elo / big5_avg_elo → difficulty factor
#
# API: GET http://api.clubelo.com/YYYY-MM-DD → CSV كامل
# مجاني، بدون مفتاح API، بدون rate limit صارم
#
# خطوة التشغيل:
#   python run_bronze.py --only-clubelo
#   أو مباشرة: python -c "from ingestion.loaders.clubelo_loader import load_clubelo_bronze; load_clubelo_bronze()"

import csv
import io
import logging
import time
import urllib.request
from datetime import date, timedelta

import pandas as pd

logger = logging.getLogger(__name__)

CLUBELO_URL = "http://api.clubelo.com/{date}"
BUCKET      = "football-bronze"
OBJECT_PATH = "clubelo/clubelo_{date}.json"

# أندية لاعبينا الـ 49 — يُستخدم للـ lookup بعد جلب الـ CSV الكامل
OUR_CLUBS = [
    "Raja Casablanca", "Al-Hilal", "Paris Saint-Germain", "PSV Eindhoven",
    "Al-Ittihad", "Al Ain", "FAR Rabat", "Olympiacos", "KRC Genk",
    "Watford", "VfB Stuttgart", "Real Betis", "Feyenoord", "Girona",
    "Manchester United", "AS Roma", "Wydad", "Real Sociedad",
    "RSC Anderlecht", "Al Ahly", "Pyramids", "Eintracht Frankfurt",
    "AS Monaco", "Olympique de Marseille", "Al-Fateh", "RCD Espanyol",
    "Bournemouth", "Villarreal", "LOSC Lille", "Stade Rennais",
    "Angers", "RC Strasbourg", "ESTAC Troyes", "Ajax",
    "Jupiler Pro League", "Botola", "Saudi",
]

# Big-5 avg Elo — baseline لحساب الـ difficulty factor
# محسوب من متوسط أفضل 20 نادٍ في Big-5 (تاريخياً ~1800-1900)
BIG5_BASELINE_ELO = 1850.0


def _fetch_clubelo_csv(target_date: str) -> list[dict]:
    """
    يجلب CSV الكامل من ClubElo API ليوم معين.
    target_date: 'YYYY-MM-DD'
    """
    url = CLUBELO_URL.format(date=target_date)
    req = urllib.request.Request(url, headers={
        "User-Agent": "FootballAtlas/1.0 (research project)",
        "Accept": "text/csv,*/*",
    })

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                content = r.read().decode("utf-8")
            reader = csv.DictReader(io.StringIO(content))
            rows = list(reader)
            logger.info("ClubElo: fetched %d clubs for %s", len(rows), target_date)
            return rows
        except Exception as e:
            logger.warning("ClubElo attempt %d failed: %s", attempt + 1, e)
            if attempt < 2:
                time.sleep(3 * (attempt + 1))

    logger.error("ClubElo: all attempts failed for %s", target_date)
    return []


def _match_club(club_name: str, elo_rows: list[dict]) -> dict | None:
    """
    يُطابق اسم النادي مع صفوف ClubElo بـ fuzzy matching.
    """
    name_lower = club_name.lower()

    # محاولة 1: exact match
    for row in elo_rows:
        if row.get("Club", "").lower() == name_lower:
            return row

    # محاولة 2: partial match
    for row in elo_rows:
        club = row.get("Club", "").lower()
        if name_lower in club or club in name_lower:
            return row

    # محاولة 3: كلمة أساسية
    # ⚠️ RISK (flagged 2026-07-21, not fixable without live ClubElo data to
    # verify against): clubs sharing a >3-char keyword can collide here —
    # e.g. "Real Betis" and "Real Sociedad" (both in OUR_CLUBS) both
    # contain "real". This only fires when attempts 1+2 (exact/substring)
    # both fail, but if it does fire for an ambiguous name, it silently
    # returns the FIRST matching row, which may be the wrong club. Logged
    # as a WARNING (not just debug) specifically so this is auditable —
    # check logs for "keyword fallback" matches before trusting the
    # resulting league difficulty factors.
    keywords = [w for w in name_lower.split() if len(w) > 3]
    for kw in keywords:
        for row in elo_rows:
            if kw in row.get("Club", "").lower():
                logger.warning(
                    "ClubElo: '%s' matched via risky keyword fallback ('%s' -> '%s') "
                    "— verify this is the correct club, ambiguous names can collide here.",
                    club_name, kw, row.get("Club"),
                )
                return row

    return None


def compute_difficulty_factor(elo: float, baseline: float = BIG5_BASELINE_ELO) -> float:
    """
    يحوّل Elo rating إلى difficulty factor بين 0.5 و 1.0.
    الصيغة: factor = elo / baseline (مع clipping)
    Big-5 avg (1850) → 1.00
    Botola (≈1200)   → ~0.65
    Saudi (≈1600)    → ~0.86
    """
    factor = elo / baseline
    return round(max(0.50, min(1.10, factor)), 3)


def build_league_quality_from_elo(
    seed_df: pd.DataFrame,
    elo_rows: list[dict],
) -> dict[str, float]:
    """
    يبني league_quality dict جديد بناءً على Elo حقيقي.
    يُعيد: {league_name: difficulty_factor}
    """
    league_elos: dict[str, list[float]] = {}

    for _, row in seed_df.iterrows():
        club  = str(row.get("club", ""))
        league = str(row.get("league", ""))
        if not club or not league:
            continue

        match = _match_club(club, elo_rows)
        if match:
            try:
                elo = float(match.get("Elo", 0))
                if elo > 0:
                    league_elos.setdefault(league, []).append(elo)
                    logger.debug("Matched %s → Elo=%.0f (league=%s)", club, elo, league)
            except (ValueError, TypeError):
                pass
        else:
            logger.warning("ClubElo: no match for club '%s'", club)

    # حساب متوسط Elo لكل دوري
    result = {}
    for league, elos in league_elos.items():
        avg_elo = sum(elos) / len(elos)
        factor  = compute_difficulty_factor(avg_elo)
        result[league] = factor
        logger.info(
            "League: %-35s avg_elo=%.0f → factor=%.3f",
            league, avg_elo, factor,
        )

    return result


def load_clubelo_bronze(
    seed_df: pd.DataFrame | None = None,
    run_date: str | None = None,
) -> dict:
    """
    Main entry point.
    يجلب ClubElo CSV، يحسب difficulty factors للدوريات،
    يرفع النتيجة إلى MinIO.

    Returns:
        dict مع: elo_by_club, difficulty_by_league, run_date
    """
    from clients.minio_client import upload_json

    if run_date is None:
        run_date = date.today().isoformat()

    logger.info("ClubElo loader — date=%s", run_date)

    elo_rows = _fetch_clubelo_csv(run_date)
    if not elo_rows:
        # FIX (found 2026-07-21, verifiable without network access — pure
        # date-arithmetic bug): the old code was
        #   date.today().replace(day=date.today().day - 1)
        # which raises ValueError("day is out of range for month") whenever
        # today is the 1st of any month (day=0 is invalid). timedelta
        # handles month/year boundaries correctly in all cases.
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        logger.warning("Retrying with yesterday: %s", yesterday)
        elo_rows = _fetch_clubelo_csv(yesterday)

    if not elo_rows:
        logger.error("ClubElo: no data retrieved — returning empty")
        return {}

    # بناء lookup: club_name → elo
    elo_by_club: dict[str, float] = {}
    for row in elo_rows:
        try:
            elo_by_club[row["Club"]] = float(row.get("Elo", 0))
        except (ValueError, TypeError, KeyError):
            pass

    # إذا عندنا seed_df — نحسب difficulty factors
    difficulty_by_league: dict[str, float] = {}
    if seed_df is not None:
        difficulty_by_league = build_league_quality_from_elo(seed_df, elo_rows)

    result = {
        "run_date":            run_date,
        "total_clubs":         len(elo_by_club),
        "elo_by_club":         elo_by_club,
        "difficulty_by_league": difficulty_by_league,
    }

    object_name = OBJECT_PATH.format(date=run_date.replace("-", ""))
    upload_json(BUCKET, object_name, result)
    logger.info(
        "ClubElo uploaded → %s | %d clubs | %d leagues mapped",
        object_name, len(elo_by_club), len(difficulty_by_league),
    )

    return result


def patch_league_quality(clubelo_result: dict, seed_df: "pd.DataFrame | None" = None) -> None:
    """
    يُحدّث LEAGUE_DIFFICULTY في league_quality.py بالأرقام الجديدة.
    يُستدعى بعد load_clubelo_bronze() مباشرة.

    الاستخدام:
        result = load_clubelo_bronze(seed_df)
        patch_league_quality(result, seed_df)

    CONFIRMED VIA LIVE DIAGNOSTIC (2026-07-22, diagnose_clubelo_coverage.py):
    ClubElo's daily feed has ZERO coverage of Botola Pro, Saudi Pro League,
    UAE Pro League, Egyptian Premier League, and Super League Greece —
    not a naming/matching issue, these leagues simply aren't in ClubElo's
    tracked competitions at all. This means the ORIGINAL motivating problem
    (AUDIT_AND_FIXES.md Fix 1 — Botola Pro GK bias) is NOT solved by
    --use-clubelo: Botola Pro silently keeps its old hardcoded 0.72
    estimate every time, because ClubElo has nothing to replace it with.

    `seed_df` (optional) lets this function tell you EXACTLY which leagues
    in YOUR candidate pool were left on the manual estimate, instead of
    you having to notice a suspiciously-unchanged DSSScore and go
    detective-work it out (as happened on 2026-07-22, before this fix).
    """
    from analytics.scoring.league_quality import LEAGUE_DIFFICULTY

    difficulty = clubelo_result.get("difficulty_by_league", {})
    updated = 0
    for league, factor in difficulty.items():
        old = LEAGUE_DIFFICULTY.get(league)
        LEAGUE_DIFFICULTY[league] = factor
        updated += 1
        logger.info(
            "league_quality updated: %-35s %.3f → %.3f (Elo-based)",
            league, old or 0.0, factor,
        )

    logger.info(
        "patch_league_quality: %d leagues updated from ClubElo",
        updated,
    )

    if seed_df is not None and "league" in seed_df.columns:
        candidate_leagues = set(seed_df["league"].dropna().unique())
        not_updated = candidate_leagues - set(difficulty.keys())
        if not_updated:
            logger.warning(
                "ClubElo did NOT cover %d league(s) present in your 49-candidate "
                "pool — these are STILL using the manual hardcoded estimate in "
                "league_quality.py, not real Elo data: %s. This is expected for "
                "non-European leagues (ClubElo's daily feed doesn't track them) "
                "— see patch_league_quality()'s docstring.",
                len(not_updated), sorted(not_updated),
            )