# analytics/squad/squad_selection.py
#
# Reorg note (C2 in the architecture review): select_squad_with_position_cap()
# and build_squad_summary() used to live inside pipelines/gold/run_gold.py —
# an orchestration entrypoint script, not a domain module. That meant the
# single most important decision this whole system produces (which 26
# players make the squad) could only be invoked by importing a script whose
# top level also does argparse/logging setup, and couldn't be reused from
# anywhere else (a future API, a notebook, a different pipeline).
#
# Content below is copied verbatim from run_gold.py — same algorithm, same
# docstrings, same log messages. Two deliberate, disclosed differences from
# the original:
#   1. This module uses its own logger (`analytics.squad.squad_selection`)
#      instead of reusing run_gold's "run_gold" logger name — log *content*
#      is identical, only the `%(name)s` field in printed log lines changes,
#      which is more accurate now (the log really does originate here).
#   2. The default position caps ({"GK": 3, "DEF": 9, "MID": 9, "FWD": 5})
#      used to be hardcoded twice in run_gold.py — once as this function's
#      default, once again in a "guard rail" check at the bottom of main()
#      that was supposed to catch drift from this exact default. Two
#      hand-typed copies of the same constant defeats the guard rail's own
#      purpose. There is now exactly one: DEFAULT_POSITION_CAPS below.
#
# run_gold.py still exposes select_squad_with_position_cap and
# build_squad_summary as `run_gold.select_squad_with_position_cap` /
# `run_gold.build_squad_summary` via a plain re-import, so existing tests
# and call sites are unaffected.

import logging
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from analytics.scoring.flexibility_score import secondary_position_groups

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PositionCaps:
    """
    Squad composition rules (FIFA World Cup squad of 26).

    GK  = 3  (minimum: need 3 to cover all scenarios)
    DEF = 9  (maximum: 4-5 starters + 4-5 backups)
    MID = 9  (maximum)
    FWD = 5  (minimum: 3 starters + 2 backups)
    """
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
    """
    Selects top-N players with realistic position caps.

    A pure score-based ranking produces 6 GKs in top-26 (since Morocco has 6 GK
    candidates and the scoring system ranks GKs on a different scale). Real squads
    have position constraints: 3 GK, ~9 DEF, ~9 MID, ~5 FWD.

    Default caps: see PositionCaps / DEFAULT_POSITION_CAPS above.

    Algorithm: greedy, per position group — take the top `caps[pos]` players of
    each group by DSSScore (equivalent to the old single-pass-by-global-Rank
    walk, since Rank is itself a DSSScore-descending rank — see
    analytics/scoring/performance_score.py). After all groups are filled:
    fill remaining slots from the best leftover players regardless of position.

    NEW 2026-08-01 (spec #13, 13_flexibility_in_selection_logic.md):
    -------------------------------------------------------------------------
    Before this, `FlexibilityScore` / `tm_secondary_positions` only nudged
    DSSScore slightly — they had ZERO effect on which players actually filled
    the position-capped squad, which is exactly where real positional
    flexibility matters (covering an injury/suspension without burning a
    full extra roster slot).

    When `use_flexibility_tiebreak=True` (default), for each position group's
    LAST cap slot, if the incumbent (last player who'd normally make the cut)
    and one or more challengers (best players just outside the cap) are within
    `flex_tiebreak_threshold` DSSScore of each other, a challenger is preferred
    over the incumbent IF the challenger — and not the incumbent — has
    documented secondary-position coverage (`tm_secondary_positions`, mapped
    via analytics.scoring.flexibility_score.secondary_position_groups()) for
    another position group that is "understaffed relative to its depth
    chart".

    "Understaffed relative to depth chart" here means: that position group
    has `depth_chart_surplus_threshold` or fewer spare candidates above its
    hard cap in the full candidate pool (e.g. MID with 11 total candidates
    for a cap of 9 has a surplus of only 2 — thin bench cushion vs. FWD's
    16-for-5). This repo does not yet implement spec #10's fuller per-
    position depth chart, so this candidate-pool surplus is the closest
    available proxy; if/when spec #10 lands, this should read its actual
    depth-chart output instead (see that spec).

    This NEVER changes the hard per-position totals (`caps` stay exact) — it
    only changes WHICH player occupies a borderline slot, and only when it
    would actually change the outcome. Every swap is logged via `logger.info`
    and recorded in the returned `selection_reason` column (empty string for
    everyone else) so it's visible in `top26.csv` and `03_final_squad.py`, not
    a silent internal tiebreak.

    Args:
        df:                              Full ranked DataFrame (must have Rank
                                          + position cols; tm_secondary_positions
                                          optional — treated as "no flexibility"
                                          if missing).
        top_n:                           Target squad size (default 26).
        position_caps:                   Override default caps per position.
        use_flexibility_tiebreak:        Set False to fall back to pure
                                          DSSScore order (old behavior).
        flex_tiebreak_threshold:         Max DSSScore gap to count as a "close
                                          enough" tie (default 0.01 — see spec
                                          #13's Implementation Plan #1; this is
                                          a product decision, tune as needed).
        depth_chart_surplus_threshold:   Candidate-pool surplus at/below which
                                          a position group counts as thin
                                          (default 3).

    Returns:
        DataFrame of top_n selected players, sorted by DSSScore, with an
        added `selection_reason` column (non-empty only for flexibility-tiebreak
        swaps).
    """
    caps = position_caps or DEFAULT_POSITION_CAPS.as_dict()

    # ── Determine which position groups are "understaffed relative to depth
    #    chart" — see docstring above for what that means in this codebase. ──
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
    selection_reasons: dict = {}   # player_id -> human-readable reason string

    for pos, cap in caps.items():
        pos_df = df[df["position"] == pos].sort_values("DSSScore", ascending=False)
        pos_selected = pos_df.iloc[:cap].copy()
        pos_overflow = pos_df.iloc[cap:].copy()

        # Tiebreak only makes sense if the group is actually at its cap (i.e.
        # there IS a "last slot" being contested) and there's at least one
        # challenger just outside it.
        if use_flexibility_tiebreak and len(pos_selected) == cap and cap > 0 and not pos_overflow.empty:
            incumbent_row = pos_selected.iloc[[-1]]
            incumbent = incumbent_row.iloc[0]

            # Near-tie band: every overflow candidate within threshold of the
            # incumbent's DSSScore (covers "last slot(s)", plural, per spec).
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

            # Only swap if the incumbent has NO qualifying flexibility of its
            # own — otherwise there's nothing to gain by swapping.
            if flexible_challengers and not incumbent_flex:
                # Prefer the highest-DSSScore flexible challenger among ties.
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

    # Fill remaining slots from best remaining players, regardless of position
    # (unchanged from the old behavior — remaining_df re-sorted by DSSScore
    # since it's now assembled per-position-group rather than in one global pass).
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
    """
    Builds a JSON-serialisable squad summary for the coach.
    Includes position breakdown, injury flags, and coverage stats.

    FIX (regression of AUDIT_AND_FIXES.md Fix 2, June 29 2026):
    This function MUST receive the position-capped squad (`squad_df`, the
    output of select_squad_with_position_cap()) for position_breakdown —
    NOT the full 49-player df filtered by raw Rank <= top_n. Raw Rank
    ignores position caps (e.g. 5-6 GKs legitimately rank inside the top 26
    on pure DSSScore), which previously produced an impossible squad
    (GK=5/6) in this JSON while top26.csv showed the correct capped
    composition (GK=3). Verified live on the 2026-07-13 run before this fix.

    `all_df` (the full 49-candidate table) is only used for total_candidates
    and the global score_range — never for position_breakdown.
    """
    from config import DECISION_DATE

    # Position breakdown — from the CAPPED squad, not raw Rank
    position_counts = squad_df.groupby("position")["player_name"].apply(list).to_dict()
    position_size = {pos: len(names) for pos, names in position_counts.items()}

    # Injury risk flags — within the actual squad
    high_risk = squad_df[
        squad_df.get("inj_days_missed_2y", pd.Series(dtype=float)).fillna(0) > 90
    ]["player_name"].tolist() if "inj_days_missed_2y" in squad_df.columns else []

    # Low fbref coverage — within the actual squad
    no_fbref = squad_df[
        squad_df.get("fbref_id", pd.Series(dtype=str)).isna()
    ]["player_name"].tolist() if "fbref_id" in squad_df.columns else []

    # intl_caps data-quality flag (only if Silver produced it — see AUDIT Fix 3)
    caps_missing = []
    if "intl_caps_data_missing" in squad_df.columns:
        caps_missing = squad_df[
            squad_df["intl_caps_data_missing"].fillna(False)
        ]["player_name"].tolist()

    top_row = squad_df.loc[squad_df["DSSScore"].idxmax()]

    # NEW 2026-08-01 (spec #13) — surface any flexibility-tiebreak swaps
    # explicitly in the summary, so they're visible to the coach, not just a
    # silent internal tiebreak buried in a per-player column.
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
        # #24 / #18 forward-compat stamp (01_temporal_cutoff.md is now
        # implemented — see config/scoring_config.py § 0). Lets
        # dashboard/data_loader.py's load_all_players_scored_history()
        # (spec #18) tell a genuinely cutoff-enforced run apart from an old
        # pre-fix one, instead of conservatively treating every snapshot as
        # pre-fix forever. `decision_date_enforced` is a claim about THIS
        # CODE VERSION, not a per-record guarantee — see
        # config/scoring_config.py's own caveats about live sources (e.g.
        # NewsAPI's free-tier ~30-day window) that can't always honor an
        # arbitrary as_of even when the code asks them to.
        "decision_date_enforced": True,
        "decision_date":       DECISION_DATE.isoformat(),
        "total_candidates":   len(all_df),
        "squad_size":         len(squad_df),          # actual selected count, not just target
        "squad_size_target":  top_n,
        "position_breakdown": position_counts,
        "position_counts":    position_size,          # e.g. {"GK": 3, "DEF": 9, "MID": 9, "FWD": 5}
        "flexibility_tiebreaks": flexibility_tiebreaks,  # NEW 2026-08-01 (spec #13)
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
