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

import logging

logger = logging.getLogger(__name__)

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


def get_league_factor(league: str | None) -> float:
    """
    Returns the difficulty coefficient for a given league name.
    Falls back to _default if the league is not in the table.

    Args:
        league: League name string (as stored in Silver 'league' column).

    Returns:
        Float coefficient in (0, 1]. 1.0 = Big-5 baseline.
    """
    if not league or not isinstance(league, str):
        return LEAGUE_DIFFICULTY["_default"]

    # Exact match first
    if league in LEAGUE_DIFFICULTY:
        return LEAGUE_DIFFICULTY[league]

    # Partial match (case-insensitive) for slightly different names
    league_lower = league.lower()
    for key, val in LEAGUE_DIFFICULTY.items():
        if key.startswith("_"):
            continue
        if key.lower() in league_lower or league_lower in key.lower():
            logger.debug("League partial match: '%s' -> '%s' (factor=%.2f)", league, key, val)
            return val

    logger.warning(
        "Unknown league '%s' — using default factor %.2f. "
        "Add to LEAGUE_DIFFICULTY in league_quality.py.",
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