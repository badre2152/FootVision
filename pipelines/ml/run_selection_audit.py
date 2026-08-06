import argparse
import json
import glob
import logging
from datetime import datetime

import pandas as pd

from analytics.ml.selection_classifier import (
    match_real_squad,
    audit_vs_dss_ranking,
    fit_loocv_classifier,
    REAL_SQUAD_2026,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Football DSS — Selection Audit vs real 2026 WC squad")
    p.add_argument("--input", default=None, help="Defaults to most recent gold/players_scored_*.csv")
    p.add_argument("--top-n", type=int, default=26)
    p.add_argument("--no-classifier", action="store_true", help="Skip the LOOCV logistic regression step")
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
    logger.info("Reading %s", input_path)
    df = pd.read_csv(input_path)

    match_result = match_real_squad(df)
    if match_result.unmatched_real_squad:
        logger.warning(
            "%d real-squad player(s) NOT found in the candidate pool: %s — "
            "the audit below is comparing against %d/%d real picks only.",
            len(match_result.unmatched_real_squad), match_result.unmatched_real_squad,
            match_result.matched_count, len(REAL_SQUAD_2026),
        )

    audit = audit_vs_dss_ranking(match_result.df, top_n=args.top_n)

    print("\n" + "=" * 78)
    print(f"SELECTION AUDIT — DSSScore top-{args.top_n} vs. real 2026 WC squad")
    print("=" * 78)
    print(f"Overlap: {audit['overlap_count']}/{args.top_n} ({audit['overlap_pct']:.1%})")
    print(f"P(overlap >= {audit['overlap_count']} | random selection) = {audit['random_baseline_pvalue']:.3f}")
    verdict = "beats random selection (p<0.05)" if audit["beats_random_at_p05"] else "NOT distinguishable from random selection"
    print(f"Verdict: {verdict}")

    print(f"\n-- DSSScore favored, coach did NOT pick ({len(audit['dss_favored_not_picked'])}) --")
    for r in audit["dss_favored_not_picked"]:
        print(f"  #{r['Rank']:<3d} {r['player_name']:<28s} {r['position']:<4s} DSS={r['DSSScore']:.4f}")

    print(f"\n-- Coach picked, DSSScore ranked OUTSIDE top-{args.top_n} ({len(audit['picked_not_dss_favored'])}) --")
    for r in audit["picked_not_dss_favored"]:
        print(f"  #{r['Rank']:<3d} {r['player_name']:<28s} {r['position']:<4s} DSS={r['DSSScore']:.4f}")

    output = {
        "generated_at": datetime.now().isoformat(),
        "source_file": input_path,
        "real_squad_source": "https://worldcupstats.football/teams/morocco/squad/ (announced 2026-05-26)",
        "unmatched_real_squad": match_result.unmatched_real_squad,
        "audit": audit,
    }

    if not args.no_classifier:
        print("\n" + "=" * 78)
        print("EXPLORATORY: LOOCV Logistic Regression on the 9 sub-scores")
        print("(NOT a predictor to trust for a future squad — see")
        print(" analytics/ml/selection_classifier.py module docstring)")
        print("=" * 78)
        loocv = fit_loocv_classifier(match_result.df)
        print(f"Accuracy={loocv.accuracy:.3f}  Precision={loocv.precision:.3f}  "
              f"Recall={loocv.recall:.3f}  F1={loocv.f1:.3f}  (n={loocv.n_samples})")
        verdict = "beats random baseline (p<0.05)" if loocv.beats_random_baseline else "does NOT clear random baseline"
        print(f"P(this many correct positive picks | random) = {loocv.random_baseline_pvalue:.3f} -> {verdict}")
        print("\nStandardized coefficients (interpretation only — do not over-read")
        print("individual signs/magnitudes at n=49 with correlated features):")
        print(loocv.coefficients.round(3).to_string())

        output["loocv_classifier"] = {
            "accuracy": loocv.accuracy, "precision": loocv.precision,
            "recall": loocv.recall, "f1": loocv.f1, "n_samples": loocv.n_samples,
            "random_baseline_pvalue": loocv.random_baseline_pvalue,
            "beats_random_baseline": loocv.beats_random_baseline,
            "standardized_coefficients": loocv.coefficients.round(4).to_dict(),
        }

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = f"{args.outdir}/selection_audit_{ts}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    logger.info("Wrote %s", out_path)


if __name__ == "__main__":
    main()