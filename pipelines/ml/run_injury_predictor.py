import argparse
import glob
import json
import logging
import os
from datetime import date, datetime

import pandas as pd

import config
from analytics.ml.injury_predictor import compute_injury_risk

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Football DSS — Injury Risk Index (transparent, rule-based)")
    p.add_argument("--input", default=None, help="Defaults to most recent gold/players_scored_*.csv")
    p.add_argument(
        "--reference-date", default=None,
        help=(
            "YYYY-MM-DD to compute age as-of (e.g. a squad-announcement date). "
            f"Defaults to config.DECISION_DATE ({config.DECISION_DATE.isoformat()}), "
            "NOT today's date (spec #4 fix — see analytics/ml/injury_predictor.py)."
        ),
    )
    p.add_argument("--outdir", default="gold")
    return p.parse_args()


def _latest_scored_csv() -> str:
    candidates = sorted(glob.glob("gold/players_scored_*.csv"))
    if not candidates:
        raise FileNotFoundError("No gold/players_scored_*.csv found — run run_gold.py first.")
    return candidates[-1]


def main() -> None:
    args = parse_args()
    input_path = args.input or _latest_scored_csv()
    ref_date = date.fromisoformat(args.reference_date) if args.reference_date else config.DECISION_DATE

    logger.info("Reading %s", input_path)
    logger.info("Using reference_date=%s", ref_date.isoformat())
    df = pd.read_csv(input_path)

    result = compute_injury_risk(df, reference_date=ref_date)

    print("\n" + "=" * 78)
    print("INJURY RISK INDEX — transparent, rule-based, NOT a trained model")
    print("(never combined into DSSScore — see module docstring)")
    print("=" * 78)
    print(f"n={result.n_players}  |  low-confidence flags: {result.n_low_confidence} "
          f"({result.n_low_confidence / result.n_players:.0%})")
    print(f"Level counts: {result.level_counts}")

    cols = ["player_name", "position", "InjuryRiskScore", "InjuryRiskLevel", "injury_risk_confidence"]
    print("\n-- Highest risk (top 10) --")
    print(result.df.sort_values("InjuryRiskScore", ascending=False)[cols].head(10).to_string(index=False))

    low_conf = result.df[result.df["injury_risk_confidence"] == "Low"]
    if len(low_conf):
        print(f"\n⚠️ {len(low_conf)} player(s) flagged LOW CONFIDENCE (missing/unclear dob or "
              f"injury-type data) — verify manually with medical staff before acting on their level:")
        print(", ".join(low_conf["player_name"].tolist()))

    os.makedirs(args.outdir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    keep_cols = [c for c in [
        "player_id", "player_name", "position", "club",
        "InjuryRiskScore", "InjuryRiskLevel", "injury_risk_confidence",
        "injury_risk_age_component", "injury_risk_history_days_component",
        "injury_risk_history_count_component", "injury_risk_recurrence_type_component",
    ] if c in result.df.columns]
    out_csv = f"{args.outdir}/injury_predictor_{ts}.csv"
    result.df[keep_cols].to_csv(out_csv, index=False)
    logger.info("Wrote %s", out_csv)

    summary = {
        "generated_at": datetime.now().isoformat(),
        "source_file": input_path,
        "reference_date": ref_date.isoformat(),
        "n_players": result.n_players,
        "n_low_confidence": result.n_low_confidence,
        "level_counts": result.level_counts,
        "low_confidence_players": low_conf["player_name"].tolist() if "player_name" in result.df.columns else [],
        "highest_risk": result.df.sort_values("InjuryRiskScore", ascending=False)[cols].head(10).to_dict(orient="records"),
    }
    out_json = f"{args.outdir}/injury_predictor_summary_{ts}.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    logger.info("Wrote %s", out_json)


if __name__ == "__main__":
    main()