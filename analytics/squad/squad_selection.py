import logging
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from analytics.scoring.flexibility_score import secondary_position_groups

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PositionCaps:
    GK: int = 3
    DEF: int = 9
    MID: int = 9
    FWD: int = 5

    def as_dict(self) -> dict:
        return {"GK": self.GK, "DEF": self.DEF, "MID": self.MID, "FWD": self.FWD}

    @property
    def total(self) -> int:
        return self.GK + self.DEF + self.MID + self.FWD


DEFAULT_POSITION_CAPS = PositionCaps()


def select_squad_with_position_cap(
    df: pd.DataFrame,
    top_n: int = 26,
    position_caps: dict | None = None,
    use_flexibility_tiebreak: bool = True,
    flex_tiebreak_threshold: float = 0.01,
    depth_chart_surplus_threshold: int = 3,
) -> pd.DataFrame:
    caps = position_caps or DEFAULT_POSITION_CAPS.as_dict()

    understaffed_positions = set()
    if use_flexibility_tiebreak:
        candidate_counts = df["position"].value_counts().to_dict()
        for pos, cap in caps.items():
            surplus = candidate_counts.get(pos, 0) - cap
            if surplus <= depth_chart_surplus_threshold:
                understaffed_positions.add(pos)
        if understaffed_positions:
            logger.info(
                "Flexibility tiebreak ENABLED — position groups with thin "
                "depth-chart surplus (<=%d spare candidates above cap): %s",
                depth_chart_surplus_threshold, sorted(understaffed_positions),
            )

    selected_parts = []
    remaining_parts = []
    selection_reasons: dict = {}

    for pos, cap in caps.items():
        pos_df = df[df["position"] == pos].sort_values("DSSScore", ascending=False)
        pos_selected = pos_df.iloc[:cap].copy()
        pos_overflow = pos_df.iloc[cap:].copy()

        if use_flexibility_tiebreak and len(pos_selected) == cap and cap > 0 and not pos_overflow.empty:
            incumbent_row = pos_selected.iloc[[-1]]
            incumbent = incumbent_row.iloc[0]

            tie_band = pos_overflow[
                (incumbent["DSSScore"] - pos_overflow["DSSScore"]) <= flex_tiebreak_threshold
            ]

            incumbent_flex = secondary_position_groups(
                incumbent.get("tm_secondary_positions")
            ) & understaffed_positions - {pos}

            flexible_challengers = []
            for idx, cand in tie_band.iterrows():
                cand_flex = secondary_position_groups(
                    cand.get("tm_secondary_positions")
                ) & understaffed_positions - {pos}
                if cand_flex:
                    flexible_challengers.append((idx, cand, cand_flex))

            if flexible_challengers and not incumbent_flex:
                best_idx, challenger, challenger_flex = max(
                    flexible_challengers, key=lambda t: t[1]["DSSScore"]
                )
                score_gap = incumbent["DSSScore"] - challenger["DSSScore"]
                reason = (
                    f"Selected over {incumbent['player_name']} for the last "
                    f"{pos} slot (DSSScore gap {score_gap:.4f} <= "
                    f"{flex_tiebreak_threshold}) — covers "
                    f"{'/'.join(sorted(challenger_flex))} as secondary "
                    f"position(s), which {'is' if len(challenger_flex) == 1 else 'are'} "
                    f"thin on depth-chart surplus (<= {depth_chart_surplus_threshold} "
                    f"spare candidates above cap)."
                )
                logger.info("Flexibility tiebreak: %s", reason)
                if "player_id" in challenger:
                    selection_reasons[challenger["player_id"]] = reason

                challenger_row = pos_overflow.loc[[best_idx]]
                pos_selected = pd.concat([pos_selected.iloc[:-1], challenger_row])
                pos_overflow = pd.concat(
                    [incumbent_row, pos_overflow.drop(index=best_idx)]
                )

        selected_parts.append(pos_selected)
        remaining_parts.append(pos_overflow)

    selected_df = (
        pd.concat(selected_parts, ignore_index=False) if selected_parts else df.iloc[0:0]
    )
    remaining_df = (
        pd.concat(remaining_parts, ignore_index=False) if remaining_parts else df.iloc[0:0]
    )

    slots_left = top_n - len(selected_df)
    if slots_left > 0 and not remaining_df.empty:
        remaining_df = remaining_df.sort_values("DSSScore", ascending=False)
        selected_df = pd.concat([selected_df, remaining_df.iloc[:slots_left]])

    squad_df = selected_df.sort_values("DSSScore", ascending=False).reset_index(drop=True)
    squad_df.insert(0, "SquadRank", range(1, len(squad_df) + 1))

    if "player_id" in squad_df.columns:
        squad_df["selection_reason"] = squad_df["player_id"].map(selection_reasons).fillna("")
    else:
        squad_df["selection_reason"] = ""

    return squad_df


def build_squad_summary(squad_df: pd.DataFrame, all_df: pd.DataFrame, top_n: int) -> dict:
    from config import DECISION_DATE

    position_counts = squad_df.groupby("position")["player_name"].apply(list).to_dict()
    position_size = {pos: len(names) for pos, names in position_counts.items()}

    high_risk = squad_df[
        squad_df.get("inj_days_missed_2y", pd.Series(dtype=float)).fillna(0) > 90
    ]["player_name"].tolist() if "inj_days_missed_2y" in squad_df.columns else []

    no_fbref = squad_df[
        squad_df.get("fbref_id", pd.Series(dtype=str)).isna()
    ]["player_name"].tolist() if "fbref_id" in squad_df.columns else []

    caps_missing = []
    if "intl_caps_data_missing" in squad_df.columns:
        caps_missing = squad_df[
            squad_df["intl_caps_data_missing"].fillna(False)
        ]["player_name"].tolist()

    top_row = squad_df.loc[squad_df["DSSScore"].idxmax()]

    flexibility_tiebreaks = []
    if "selection_reason" in squad_df.columns:
        flagged = squad_df[squad_df["selection_reason"].fillna("") != ""]
        flexibility_tiebreaks = [
            {"player_name": row["player_name"], "position": row.get("position", ""),
             "reason": row["selection_reason"]}
            for _, row in flagged.iterrows()
        ]

    return {
        "generated_at":       datetime.now().isoformat(),
        "decision_date_enforced": True,
        "decision_date":       DECISION_DATE.isoformat(),
        "total_candidates":   len(all_df),
        "squad_size":         len(squad_df),
        "squad_size_target":  top_n,
        "position_breakdown": position_counts,
        "position_counts":    position_size,
        "flexibility_tiebreaks": flexibility_tiebreaks,
        "flags": {
            "high_injury_risk":           high_risk,
            "limited_data":               no_fbref,
            "intl_caps_possibly_missing":  caps_missing,
        },
        "score_range": {
            "min":  float(all_df["DSSScore"].min()),
            "max":  float(all_df["DSSScore"].max()),
            "mean": float(all_df["DSSScore"].mean()),
        },
        "top_player": {
            "name":  top_row.get("player_name", "unknown"),
            "score": float(top_row["DSSScore"]),
            "club":  top_row.get("club", "unknown"),
        },
    }