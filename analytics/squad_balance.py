# analytics/squad_balance.py
# Football Atlas — Squad Age / Experience Balance Analysis (issue #16)
#
# Buckets a proposed squad's players into age tiers, computed as of a
# fixed DECISION_DATE (config.py — NOT real "today", see that constant's
# docstring for why), and reports tier counts overall and per position,
# plus a simple experience proxy (average intl_caps per tier). This is a
# standard national-team squad-planning consideration — young/inexperienced
# vs peak-age vs veteran balance — that was completely absent from this
# tool before this module.
#
# Age tiers are configurable data (AGE_TIERS below), not hardcoded
# conditionals, following this codebase's existing pattern of centralising
# tunable thresholds (see config.py's INJURY_AVAILABILITY_THRESHOLDS).
#
# Missing wiki_dob is NOT silently dropped from the counts — those players
# land in an explicit "Unknown (missing DOB)" tier instead, per the
# spec's edge-case requirement. As of the 2026-07-21 Gold run, wiki_dob
# coverage is actually 49/49 (the spec's "confirm current coverage" step
# found none missing) — but wikidata_loader.py's data is a small hardcoded
# lookup table, so a future candidate added there without a DOB entry (or
# a real Wikidata-API-backed loader down the line) could easily reintroduce
# gaps, hence still handling it explicitly rather than assuming.

import logging
from datetime import date

import pandas as pd

logger = logging.getLogger(__name__)

# ── Age tiers ────────────────────────────────────────────────────────────
# (label, min_age_inclusive, max_age_exclusive). max=None means "and above".
# Matches the spec's suggested Young(<23) / Prime(23-29) / Experienced(30+)
# split by default — retune here without touching compute_age_distribution().
AGE_TIERS: list[tuple[str, float, float | None]] = [
    ("Young (<23)",       0.0, 23.0),
    ("Prime (23-29)",     23.0, 30.0),
    ("Experienced (30+)", 30.0, None),
]

UNKNOWN_TIER = "Unknown (missing DOB)"

# Display order used by both this module's own outputs and the dashboard —
# "Unknown" always last so it doesn't visually interrupt the age progression.
TIER_ORDER: list[str] = [label for label, _, _ in AGE_TIERS] + [UNKNOWN_TIER]


def _age_years(dob, as_of: date) -> float | None:
    """Returns age in years as of `as_of`, or None if dob is missing/unparseable."""
    # pd.isna() (not an isinstance(..., float) check) so this also catches
    # pandas' newer nullable-string dtype's pd.NA marker, not just classic
    # float NaN — a str-dtype column with a None value can surface either
    # depending on pandas version/backend, and both must count as missing.
    if dob is None or pd.isna(dob):
        return None
    try:
        birth = pd.to_datetime(dob).date()
    except (ValueError, TypeError):
        logger.warning("Unparseable wiki_dob value %r — treating as unknown age.", dob)
        return None
    return (as_of - birth).days / 365.25


def _age_tier(age_years: float | None) -> str:
    """Maps an age in years (or None) to its tier label."""
    # age_years arrives here after a round-trip through a pandas Series
    # (see compute_age_distribution's work["_age_years"].apply(...)) —
    # a column of Python Nones gets silently upcast to float NaN by
    # pandas, so `is None` alone would miss it. pd.isna() catches both.
    if age_years is None or pd.isna(age_years):
        return UNKNOWN_TIER
    for label, lo, hi in AGE_TIERS:
        if age_years >= lo and (hi is None or age_years < hi):
            return label
    # AGE_TIERS covers [0, inf) by construction above, so this shouldn't be
    # reachable — but fall back to the last tier rather than raising on an
    # unexpected value (e.g. a negative age from bad data).
    return AGE_TIERS[-1][0]


def compute_age_distribution(df: pd.DataFrame, as_of: date) -> dict:
    """
    Computes age/experience tier distribution for a squad selection.

    Args:
        df: DataFrame with at least "wiki_dob" and "position" columns —
            one row per player in the squad to analyze (e.g. the top-26,
            or a custom Squad Builder selection). "intl_caps" and
            "player_name" are optional: if present they add average-caps-
            per-tier and named low-confidence players respectively; if
            absent those parts of the output are simply omitted rather
            than raising.
        as_of: reference date age is computed relative to — pass
            config.DECISION_DATE, not date.today(), so results are
            reproducible regardless of when the dashboard happens to be
            opened (see config.py's DECISION_DATE docstring).

    Returns:
        {
          "as_of": "YYYY-MM-DD",
          "tier_order": [tier labels in display order, "Unknown..." last],
          "overall": {tier: count, ...},
          "overall_avg_caps": {tier: float|None, ...} or None if no intl_caps col,
          "by_position": {position: {tier: count, ...}, ...},
          "n_unknown_dob": int,
          "players_with_unknown_dob": [name, ...] (empty if no player_name col),
        }

    Raises:
        ValueError: if `df` is missing "wiki_dob" or "position".
    """
    required = ["wiki_dob", "position"]
    missing_required = [c for c in required if c not in df.columns]
    if missing_required:
        raise ValueError(
            f"compute_age_distribution() requires columns {required}, "
            f"missing: {missing_required}"
        )

    has_caps = "intl_caps" in df.columns
    has_names = "player_name" in df.columns

    work = df.copy()
    work["_age_years"] = work["wiki_dob"].apply(lambda d: _age_years(d, as_of))
    work["_age_tier"] = work["_age_years"].apply(_age_tier)

    overall = {tier: int((work["_age_tier"] == tier).sum()) for tier in TIER_ORDER}

    overall_avg_caps = None
    if has_caps:
        overall_avg_caps = {}
        for tier in TIER_ORDER:
            subset = work.loc[work["_age_tier"] == tier, "intl_caps"]
            overall_avg_caps[tier] = (
                float(subset.mean()) if len(subset) and subset.notna().any() else None
            )

    by_position: dict[str, dict[str, int]] = {}
    for pos, group in work.groupby("position"):
        by_position[pos] = {tier: int((group["_age_tier"] == tier).sum()) for tier in TIER_ORDER}

    unknown_mask = work["_age_tier"] == UNKNOWN_TIER
    n_unknown = int(unknown_mask.sum())
    unknown_names = work.loc[unknown_mask, "player_name"].tolist() if has_names else []

    return {
        "as_of": as_of.isoformat(),
        "tier_order": TIER_ORDER,
        "overall": overall,
        "overall_avg_caps": overall_avg_caps,
        "by_position": by_position,
        "n_unknown_dob": n_unknown,
        "players_with_unknown_dob": unknown_names,
    }
