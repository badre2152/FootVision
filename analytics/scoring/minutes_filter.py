import logging
import math
import pandas as pd

logger = logging.getLogger(__name__)

MIN_ACTIVE  = 270
MIN_REGULAR = 540

PENALTIES: dict[str, float] = {
    "SEED_INACTIVE": 0.20,
    "INACTIVE":      0.35,
    "LIMITED":       0.70,
    "SEED_UNKNOWN":  0.75,
    "UNVERIFIED":    0.85,
    "ACTIVE":        1.00,
}


def _get_reliable_minutes(row: pd.Series) -> tuple[float | None, str | None]:
    for col in ("fm_min", "fbref_minutes", "csv_min"):
        val = row.get(col)
        if val is None or pd.isna(val):
            continue
        try:
            minutes = float(val)
        except (TypeError, ValueError):
            continue
        if math.isfinite(minutes) and minutes > 0:
            return minutes, col
    return None, None


def _classify(row: pd.Series) -> str:
    seed_flag = str(row.get("active_2526", "unknown")).strip().lower()
    if seed_flag == "no":
        return "SEED_INACTIVE"
    if seed_flag == "unknown":
        return "SEED_UNKNOWN"

    mins, _ = _get_reliable_minutes(row)
    if mins is not None:
        if mins < MIN_ACTIVE:
            return "INACTIVE"
        if mins < MIN_REGULAR:
            return "LIMITED"
        return "ACTIVE"

    if seed_flag == "yes":
        return "ACTIVE"

    return "UNVERIFIED"


def apply_minutes_filter(df: pd.DataFrame) -> pd.DataFrame:
    if "active_2526" not in df.columns:
        df["active_2526"] = "unknown"
        logger.warning("active_2526 column missing — treating all as 'unknown'")

    flags = df.apply(_classify, axis=1)
    df["minutes_flag"] = flags

    df["AvailabilityScore"] = df.apply(
        lambda r: round(r["AvailabilityScore"] * PENALTIES[r["minutes_flag"]], 4),
        axis=1,
    )

    for flag_name in ("SEED_INACTIVE", "INACTIVE", "LIMITED",
                      "SEED_UNKNOWN",  "UNVERIFIED"):
        affected = df[df["minutes_flag"] == flag_name]
        if not affected.empty:
            names = ", ".join(affected["player_name"].tolist())
            penalty = PENALTIES[flag_name]
            logger.warning(
                "minutes_filter [%s] (Avail×%.2f): %s",
                flag_name, penalty, names,
            )

    active_count = (df["minutes_flag"] == "ACTIVE").sum()
    logger.info(
        "minutes_filter classification done — ACTIVE:%d | flagged:%d "
        "(DSSScore gate applied separately by apply_dss_gate(), AFTER "
        "compute_final_score() — see run_gold.py call order, BUG-01)",
        active_count, len(df) - active_count,
    )

    return df


def apply_dss_gate(df: pd.DataFrame) -> pd.DataFrame:
    if "minutes_flag" not in df.columns:
        raise ValueError(
            "apply_dss_gate() called without 'minutes_flag' — call "
            "apply_minutes_filter() first."
        )
    if "DSSScore" not in df.columns:
        raise ValueError(
            "apply_dss_gate() called without 'DSSScore' — call "
            "compute_final_score() first (BUG-01: the gate must apply to "
            "the REAL DSSScore, not a stale/pre-computed one)."
        )

    df["DSSScore_pre_gate"] = df["DSSScore"]
    df["DSSScore"] = df.apply(
        lambda r: round(r["DSSScore_pre_gate"] * PENALTIES[r["minutes_flag"]], 4),
        axis=1,
    )

    df["Rank"] = df["DSSScore"].rank(ascending=False, method="min").astype(int)

    risky_flags = {"SEED_INACTIVE", "INACTIVE"}
    still_top = df[df["minutes_flag"].isin(risky_flags) & (df["Rank"] <= 26)]
    if not still_top.empty:
        for _, r in still_top.iterrows():
            logger.warning(
                "minutes_filter GATE ALERT: %s is [%s] but still Rank #%d "
                "(DSSScore %.4f → %.4f after gate) — VERIFY MANUALLY before "
                "including in the final squad recommendation.",
                r["player_name"], r["minutes_flag"], r["Rank"],
                r["DSSScore_pre_gate"], r["DSSScore"],
            )

    logger.info("apply_dss_gate done — DSSScore gate applied AFTER compute_final_score().")

    return df