# analytics/scoring/league_quality.py
# League Quality Coefficients — Cross-Position Bias Correction
#
# Problem (original, GK-only): GA/90 is not league-independent. A Botola
# Pro GK facing 4 shots/game naturally concedes fewer goals than a Saudi
# Pro League GK facing 8+ shots/game. Without adjustment, Botola GKs
# appear artificially elite.
#
# EXTENDED (2026-07-29): the exact same bias applies to EVERY position,
# not just GKs. A Botola/UAE/Greece forward scoring goals or a defender
# making tackles faces weaker opposition too — their raw Gls_per90,
# Ast_per90, xg_per90, xa_per90, Tkl_per90, Int_per90, Clr_per90 are all
# inflated the same way GK GA_per90 was deflated. This was flagged
# repeatedly across multiple review sessions but never fixed until now:
# adjust_gk_ga_per90() only ever touched the GK defensive_score branch of
# compute_performance_score(), leaving 100% of outfield offensive/
# defensive scoring completely unadjusted for league quality.
#
# Fix: adjust_stat_for_league() (below) is now the general-purpose entry
# point. adjust_gk_ga_per90() is kept as a thin backward-compatible
# wrapper around it. compute_performance_score() now calls the general
# function for outfield offensive_score AND defensive_score too — see
# performance_score.py's _outfield_offensive()/_outfield_defensive().
#
# Fix: multiply raw GA/90 by a league difficulty factor before normalization.
# adjusted_GA_per90 = raw_GA_per90 / league_factor  (inverse stat — inflate)
# adjusted_offensive/defensive_stat = raw * league_factor  (deflate)
# Lower factor = weaker league = push the raw number toward a league-neutral
# baseline (worse for "lower is better" stats, lower for "higher is better").
#
# Coefficients are derived from:
#   - UEFA/CAF coefficient rankings (2024-25 season)
#   - Average shots on target per game per league (Opta/StatsBomb public data)
#   - Subject to manual validation by coaching staff
#
# Methodology: Big-5 avg (Premier League / La Liga / Bundesliga) = 1.00 baseline.
# All others indexed relative to that.
#
# IMPORTANT: This correction applies transiently, inside
# compute_performance_score(). It does NOT change the raw Silver columns
# (preserve source data) — adjustment + normalization happen together on
# a per-row basis, bypassing the globally-precomputed *_norm columns from
# normalize_all() (which run before league adjustment can be applied and
# therefore cannot reflect it).
#
# ── SOURCE-PRIORITY UPDATE (#14 — 2026-08-02) ──────────────────────────────
# get_league_factor() (and therefore adjust_stat_for_league() /
# adjust_gk_ga_per90(), which both call it) now resolves the coefficient
# via a 3-tier COALESCE-style priority chain, same convention as
# ingestion/silver_pipeline.py's COALESCE_CHAINS:
#
#   1. compute_elo_based_coefficient() — REAL ClubElo ratings, read from the
#      Bronze artifact ingestion/loaders/clubelo_loader.py already produces
#      (s3://football-bronze/clubelo/clubelo_{DECISION_DATE}.json), i.e. the
#      SAME snapshot date the rest of the live pipeline is pinned to
#      (config.DECISION_DATE) — never a live/"today" ClubElo pull.
#   2. LEAGUE_DIFFICULTY (below) — the original hand-estimated table.
#   3. LEAGUE_DIFFICULTY["_default"] — last resort.
#
# HONEST COVERAGE NOTE: ClubElo's tracked competitions do NOT include
# Botola Pro, Saudi Pro League, Egyptian Premier League, UAE Pro League, or
# Super League Greece (confirmed live via diagnose_clubelo_coverage.py,
# 2026-07-22 — zero matches, not a naming/matching bug). Those five leagues
# will ALWAYS fall through to tier 2 (the manual table) or tier 3, no matter
# how current the ClubElo Bronze snapshot is. This module does not — and
# cannot — claim full data-driven coverage; only Big-5 / European leagues
# ClubElo actually tracks are traceable to real Elo numbers.

import logging
from datetime import date

logger = logging.getLogger(__name__)

# Must match ingestion/loaders/clubelo_loader.py's BUCKET / OBJECT_PATH —
# kept as separate literals here (rather than importing clubelo_loader) to
# avoid a heavier import chain (minio client, requests, etc.) just to read
# league_quality.py's coefficients.
_CLUBELO_BUCKET = "football-bronze"
_CLUBELO_PREFIX_TEMPLATE = "clubelo/clubelo_{date}"

# Per-process cache: {date_str: snapshot_dict_or_None}. A full Gold run
# calls get_league_factor() once per player per stat (hundreds of calls),
# all for the same DECISION_DATE snapshot — no reason to hit MinIO/disk
# every time.
_ELO_SNAPSHOT_CACHE: dict[str, dict | None] = {}

# ── League difficulty coefficients ────────────────────────────────────────────
# 1.00 = Big-5 level (Premier League, La Liga, Bundesliga, Serie A, Ligue 1)
# < 1.00 = weaker league → inflate GA/90 to compensate
# Values are conservative: erring toward smaller corrections to avoid over-engineering.
LEAGUE_DIFFICULTY: dict[str, float] = {
    # Tier 1 — Big 5 European (baseline = 1.00)
    "Premier League":          1.00,
    "La Liga":                 1.00,
    "Bundesliga":              1.00,
    "Serie A":                 1.00,
    "Ligue 1":                 1.00,

    # Tier 2 — Strong mid-table European / Top Gulf
    "Saudi Pro League":        0.90,   # High investment, good attacking players
    "Eredivisie":              0.90,
    "Jupiler Pro League":      0.88,
    "Championship":            0.88,   # Physically intense, less technical pressure
    "Primeira Liga":           0.87,
    "Süper Lig":               0.85,

    # Tier 3 — Mid-level European / Top African
    "Super League Greece":     0.82,
    "Egyptian Premier League": 0.80,
    "UAE Pro League":          0.78,
    "Al Ittihad Club":         0.78,   # Qatar / Saudi non-SPL

    # Tier 4 — Moroccan / regional
    "Botola Pro":              0.72,   # Key correction: Botola ≈ 72% of Big-5 difficulty

    # Fallback for unrecognised leagues
    "_default":                0.85,
}


def _resolve_as_of_date(as_of_date: "date | str | None") -> date:
    """
    يحوّل as_of_date لـ date object، ويرجع config.DECISION_DATE كـ default —
    نفس مبدأ التوقيت المستعمل فـ clubelo_loader.load_clubelo_bronze()
    (01_temporal_cutoff.md): القطعة الزمنية لازم تكون DECISION_DATE، ماشي
    date.today()، حتى ما تتسربش بيانات بعد تاريخ القرار.
    """
    if as_of_date is None:
        from config import DECISION_DATE
        return DECISION_DATE
    if isinstance(as_of_date, str):
        return date.fromisoformat(as_of_date)
    return as_of_date


def _load_elo_snapshot(as_of_date: date) -> dict | None:
    """
    يقرأ artifact الـ ClubElo (Bronze) اللي كتبو clubelo_loader.py لتاريخ
    محدد بالضبط — ماشي "آخر واحد موجود" (read_latest_json عادةً كترجع
    آخر object بالاسم، وهذا ممكن يكون بعد DECISION_DATE إذا تعاود تشغيل
    الـ loader فتواريخ لاحقة). كنبنيو prefix يطابق فقط ملف هاد التاريخ.

    Cached بـ date_str باش ما نكرّروش القراءة لكل لاعب/لكل statistic.
    """
    date_str = as_of_date.strftime("%Y%m%d")
    if date_str in _ELO_SNAPSHOT_CACHE:
        return _ELO_SNAPSHOT_CACHE[date_str]

    from clients.minio_client import read_latest_json

    prefix = _CLUBELO_PREFIX_TEMPLATE.format(date=date_str)
    snapshot = read_latest_json(bucket=_CLUBELO_BUCKET, prefix=prefix)
    if not snapshot:
        logger.debug(
            "ClubElo: no Bronze snapshot found for %s (prefix='%s') — "
            "Elo-based coefficients unavailable for this run, falling back "
            "to the manual LEAGUE_DIFFICULTY table.",
            date_str, prefix,
        )
    _ELO_SNAPSHOT_CACHE[date_str] = snapshot
    return snapshot


def compute_elo_based_coefficient(
    league_name: str | None,
    as_of_date: "date | str | None" = None,
) -> float | None:
    """
    الطبقة الأولى (الأولوية القصوى) فـ سلسلة get_league_factor(): تحاول
    ترجع difficulty coefficient حقيقي مبني على ClubElo، مقروء من الـ Bronze
    artifact اللي كيكتبو ingestion/loaders/clubelo_loader.py (عبر
    load_clubelo_bronze() → build_league_quality_from_elo()).

    يحترم DECISION_DATE (spec #1): as_of_date كيتحدد افتراضياً بـ
    config.DECISION_DATE، ماشي بتاريخ اليوم الحقيقي — نفس الـ snapshot
    اللي باقي pipeline مبني عليه.

    Args:
        league_name: اسم الدوري (عمود Silver 'league').
        as_of_date:  date أو ISO string أو None (→ config.DECISION_DATE).

    Returns:
        float coefficient إذا الدوري موجود فـ الـ Elo snapshot المطابق
        لهاد التاريخ، وإلا None (الدوري ماشي مغطى بـ ClubElo — Botola
        Pro، Saudi Pro League، Egyptian Premier League، UAE Pro League،
        و Super League Greece كلهم بلا تغطية ClubElo إطلاقاً، انظر
        docstring الملف فوق).
    """
    if not league_name or not isinstance(league_name, str):
        return None

    resolved_date = _resolve_as_of_date(as_of_date)
    snapshot = _load_elo_snapshot(resolved_date)
    if not snapshot:
        return None

    difficulty = snapshot.get("difficulty_by_league", {})
    if not difficulty:
        return None

    # Exact match first
    if league_name in difficulty:
        return float(difficulty[league_name])

    # Case-insensitive exact match (ClubElo snapshot keys come from the
    # same seed_df 'league' column as LEAGUE_DIFFICULTY, so an exact match
    # is the expected common case — no fuzzy substring matching here on
    # purpose, to avoid silently attributing the wrong league's real Elo
    # number the way a substring match could).
    league_lower = league_name.lower()
    for key, val in difficulty.items():
        if key.lower() == league_lower:
            return float(val)

    return None


def get_league_factor(league: str | None, as_of_date: "date | str | None" = None) -> float:
    """
    Returns the difficulty coefficient for a given league name, via a
    3-tier COALESCE-style priority chain (see module header, #14):

        1. compute_elo_based_coefficient() — real ClubElo data, if the
           league is covered by ClubElo's tracked competitions AND a
           Bronze snapshot exists for `as_of_date` (defaults to
           config.DECISION_DATE).
        2. LEAGUE_DIFFICULTY table (hand-estimated, exact then partial
           match) — used whenever tier 1 returns nothing.
        3. LEAGUE_DIFFICULTY["_default"] — last resort.

    Args:
        league:    League name string (as stored in Silver 'league' column).
        as_of_date: Optional date/ISO-string for the ClubElo snapshot to
                    read (tier 1 only). Defaults to config.DECISION_DATE —
                    pass this explicitly only for testing/backtesting.

    Returns:
        Float coefficient in (0, 1]. 1.0 = Big-5 baseline.
    """
    if not league or not isinstance(league, str):
        return LEAGUE_DIFFICULTY["_default"]

    # ── Tier 1: real ClubElo-derived coefficient ────────────────────────
    elo_factor = compute_elo_based_coefficient(league, as_of_date)
    if elo_factor is not None:
        logger.debug(
            "League factor (Elo-based): '%s' -> %.3f (ClubElo snapshot)",
            league, elo_factor,
        )
        return elo_factor

    # ── Tier 2: manual hand-estimated table — exact match ───────────────
    if league in LEAGUE_DIFFICULTY:
        return LEAGUE_DIFFICULTY[league]

    # ── Tier 2b: manual table — partial match (case-insensitive) ────────
    league_lower = league.lower()
    for key, val in LEAGUE_DIFFICULTY.items():
        if key.startswith("_"):
            continue
        if key.lower() in league_lower or league_lower in key.lower():
            logger.debug("League partial match: '%s' -> '%s' (factor=%.2f)", league, key, val)
            return val

    # ── Tier 3: documented default ───────────────────────────────────────
    logger.warning(
        "Unknown league '%s' — using default factor %.2f. "
        "Not covered by ClubElo and not in LEAGUE_DIFFICULTY in league_quality.py.",
        league,
        LEAGUE_DIFFICULTY["_default"],
    )
    return LEAGUE_DIFFICULTY["_default"]


def adjust_stat_for_league(value: float, league: str | None, inverse: bool = False) -> float:
    """
    تعديل عام لأي إحصائية بناءً على جودة الدوري — يُستعمل لكل المراكز،
    ماشي غير الحراس (adjust_gk_ga_per90 قبل هذا الإصلاح 2026-07-29 كانت
    الوحيدة، وهذا كان عيب منهجي خطير: لاعب هجومي/دفاعي فـ Botola كيسجّل
    أهداف/يدير تدخلات أسهل من نظيره فـ Big-5، بنفس منطق GA/90 بالضبط،
    لكن ما كان عندو أي تعديل).

    inverse=False (الافتراضي — للإحصائيات "الأعلى أحسن": أهداف، تمريرات
        حاسمة، xG، xA، تدخلات، اعتراضات...):
        دوري أضعف → الرقم الخام مُضخَّم artificially → نُنقّصه (نضرب × factor)
        adjusted = raw * factor   (factor<1 يُنقّص الرقم لمستوى Big-5 عادل)

    inverse=True (للإحصائيات "الأقل أحسن": GA/90 للحراس):
        دوري أضعف → الرقم الخام مُنخفَض artificially (أفضل مما يستحق) → نُضخّمه
        adjusted = raw / factor   (factor<1 يُضخّم الرقم)

    Args:
        value:   القيمة الخام.
        league:  اسم الدوري (من عمود Silver 'league').
        inverse: True لإحصائيات "الأقل أحسن" (GA/90)، False لكل الباقي.

    Returns:
        القيمة المُعدَّلة — قابلة للمقارنة عبر الدوريات المختلفة.
    """
    factor = get_league_factor(league)
    if factor <= 0:
        return value
    return value / factor if inverse else value * factor


def adjust_gk_ga_per90(ga_per90: float, league: str | None) -> float:
    """
    تعديل GA/90 للحراس بناءً على جودة الدوري — الآن wrapper رقيق فوق
    adjust_stat_for_league() (نفس السلوك بالضبط، أبقيناها لتوافق الكود
    القديم اللي كيستدعيها مباشرة).

    adjusted = raw * (1 / factor)
    — حارس Botola بـGA/90=0.38 و factor=0.72 كيتعدَّل لـ0.53، مما يخليه
      أقرب للمقارنة مع حارس Big-5 كيدّي 0.53/الماتش.

    Args:
        ga_per90: GA/90 خام.
        league:   اسم الدوري من عمود Silver 'league'.

    Returns:
        GA/90 مُعدَّل — أعلى من الخام للدوريات الأضعف.
    """
    adjusted = adjust_stat_for_league(ga_per90, league, inverse=True)
    if abs(adjusted - ga_per90) > 0.05:
        logger.debug(
            "GA/90 adjusted: raw=%.3f factor=%.2f adjusted=%.3f (league='%s')",
            ga_per90, get_league_factor(league), adjusted, league,
        )
    return adjusted