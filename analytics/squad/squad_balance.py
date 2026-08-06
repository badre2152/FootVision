import logging
from datetime import date

import pandas as pd

logger = logging.getLogger(__name__)

AGE_TIERS: list[tuple[str, float, float | None]] = [
    ("Young (<23)",       0.0, 23.0),
    ("Prime (23-29)",     23.0, 30.0),
    ("Experienced (30+)", 30.0, None),
]

UNKNOWN_TIER = "Unknown (missing DOB)"

TIER_ORDER: list[str] = [label for label, _, _ in AGE_TIERS] + [UNKNOWN_TIER]


def _age_years(dob, as_of: date) -> float | None:
    if dob is None or pd.isna(dob):
        return None
    try:
        birth = pd.to_datetime(dob).date()
    except (ValueError, TypeError):
        logger.warning("Unparseable wiki_dob value %r — treating as unknown age.", dob)
        return None
    return (as_of - birth).days / 365.25


def _age_tier(age_years: float | None) -> str:
    if age_years is None or pd.isna(age_years):
        return UNKNOWN_TIER
    for label, lo, hi in AGE_TIERS:
        if age_years >= lo and (hi is None or age_years < hi):
            return label
    return AGE_TIERS[-1][0]


def compute_age_distribution(df: pd.DataFrame, as_of: date) -> dict:
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