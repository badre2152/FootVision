#!/usr/bin/env python
# run_rotation_strategy.py — Football DSS — Rotation Strategy (group stage)
#
# Standalone (not wired into run_gold.py, same reasoning as
# run_selection_audit.py / run_injury_predictor.py — this is a
# squad-management tool, not a scoring input).
#
# Requires gold/top26_*.csv (from run_gold.py). Optionally merges in
# gold/injury_predictor_*.csv (from run_injury_predictor.py) for a real
# injury_risk_penalty component — falls back to neutral if absent, with a
# warning, same pattern as recommend_xi() itself.
#
# Run AFTER run_gold.py (and ideally run_injury_predictor.py):
#   python run_rotation_strategy.py
#   python run_rotation_strategy.py --no-validate   # skip the real-XI comparison

import argparse
import glob
import json
import logging
from datetime import datetime

import pandas as pd

from analytics.squad.rotation_strategy import (
    build_rotation_plan,
    validate_vs_real_xi,
    REAL_GROUP_STAGE_2026,
    REAL_XI_ALL_THREE_MATCHES,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Football DSS — Rotation Strategy (group stage)")
    p.add_argument("--top26", default=None, help="Defaults to most recent gold/top26_*.csv")
    p.add_argument("--injury", default=None, help="Defaults to most recent gold/injury_predictor_*.csv, if any")
    p.add_argument("--no-validate", action="store_true", help="Skip comparison against the real reported XI")
    p.add_argument("--outdir", default="gold")
    return p.parse_args()


def _latest(pattern: str) -> str | None:
    candidates = sorted(glob.glob(pattern))
    return candidates[-1] if candidates else None


def main() -> None:
    args = parse_args()

    top26_path = args.top26 or _latest("gold/top26_*.csv")
    if top26_path is None:
        raise FileNotFoundError("No gold/top26_*.csv found — run run_gold.py first.")
    logger.info("Reading squad: %s", top26_path)
    squad_df = pd.read_csv(top26_path)

    injury_path = args.injury or _latest("gold/injury_predictor_*.csv")
    if injury_path:
        logger.info("Merging injury risk: %s", injury_path)
        injury_df = pd.read_csv(injury_path)[["player_name", "InjuryRiskScore"]]
        squad_df = squad_df.merge(injury_df, on="player_name", how="left")
        if squad_df["InjuryRiskScore"].isna().any():
            logger.warning(
                "%d squad player(s) had no match in the injury file — "
                "InjuryRiskScore will be NaN for them, treated as neutral 0.5 "
                "by recommend_xi().",
                squad_df["InjuryRiskScore"].isna().sum(),
            )
            squad_df["InjuryRiskScore"] = squad_df["InjuryRiskScore"].fillna(0.5)
    else:
        logger.warning(
            "No gold/injury_predictor_*.csv found — run run_injury_predictor.py first "
            "for a real injury_risk_penalty component. Proceeding with neutral (0.5) "
            "for everyone."
        )

    plan = build_rotation_plan(squad_df)

    print("\n" + "=" * 78)
    print("ROTATION STRATEGY — Group C (real 2026 fixtures)")
    print("=" * 78)

    output = {"generated_at": datetime.now().isoformat(), "matches": []}
    for rec in plan.matches:
        print(f"\n-- Match {rec.match_number} vs {rec.opponent} (difficulty={rec.difficulty}) --")
        print("Recommended XI:", ", ".join(rec.recommended_xi))
        if rec.rotation_candidates:
            print("Rotation candidates brought in (fresh + weak-opponent bonus):",
                  ", ".join(rec.rotation_candidates))

        match_out = {
            "match_number": rec.match_number, "opponent": rec.opponent,
            "difficulty": rec.difficulty, "recommended_xi": rec.recommended_xi,
            "rotation_candidates": rec.rotation_candidates,
        }

        if not args.no_validate:
            validation = validate_vs_real_xi(rec)
            print(f"vs REAL reported XI: {validation['overlap_count']}/11 overlap "
                  f"({validation['overlap_pct']:.0%})")
            if validation["real_not_recommended"]:
                print("  Real starters this tool did NOT recommend:", ", ".join(validation["real_not_recommended"]))
            if validation["recommended_not_real"]:
                print("  Tool recommended, NOT real starters:", ", ".join(validation["recommended_not_real"]))
            match_out["validation_vs_real_xi"] = validation

        output["matches"].append(match_out)

    print("\n-- Accumulated fatigue after the 3 matches (top 5) --")
    fatigue_sorted = sorted(plan.fatigue_after.items(), key=lambda kv: -kv[1])
    for name, units in fatigue_sorted[:5]:
        print(f"  {name}: {units:.2f}")

    if not args.no_validate:
        print(f"\nReal XI source note: reportedly UNCHANGED across all 3 matches — see "
              f"analytics/rotation_strategy.py's REAL_XI_ALL_THREE_MATCHES docstring/citation.")

    output["fatigue_after"] = plan.fatigue_after
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = f"{args.outdir}/rotation_strategy_{ts}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    logger.info("Wrote %s", out_path)


if __name__ == "__main__":
    main()
