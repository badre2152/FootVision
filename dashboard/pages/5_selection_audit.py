# dashboard/pages/5_selection_audit.py
# Football Atlas — Selection Audit page (NEW 2026-07-29)
#
# Visualizes the output of run_selection_audit.py: DSSScore's top-26 vs the
# REAL 2026 World Cup squad (announced 2026-05-26 — see
# analytics/ml/selection_classifier.py's module docstring for the full
# story, including why this is a standalone script, not part of
# run_gold.py).
#
# This page leads with the headline finding plainly, including when it's
# bad news for the scoring formula (overlap statistically indistinguishable
# from random) — that is the whole point of running a real-world audit, and
# hiding or softening it here would defeat the purpose.

import sys
from pathlib import Path

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dashboard.data_loader import load_latest_selection_audit, load_latest_injury_predictor
from dashboard.sidebar import render_sidebar

st.set_page_config(page_title="Football Atlas — Selection Audit", page_icon="🔍", layout="wide")
render_sidebar()
st.title("🔍 Selection Audit — DSSScore vs. the Real 2026 World Cup Squad")

audit_data = load_latest_selection_audit()
if audit_data is None:
    st.warning(
        "No `gold/selection_audit_*.json` found. Run:\n\n"
        "```\npython run_selection_audit.py\n```\n\n"
        "This is a standalone analysis script, deliberately not part of "
        "`run_gold.py` — see `run_selection_audit.py`'s header comment for why."
    )
    st.stop()

st.caption(
    f"Ground truth: {audit_data['real_squad_source']}  ·  "
    f"Source Gold run: `{audit_data['source_file']}`  ·  "
    f"Report generated: {audit_data['generated_at']}"
)

if audit_data.get("unmatched_real_squad"):
    st.error(
        f"⚠️ {len(audit_data['unmatched_real_squad'])} real-squad player(s) had NO "
        f"confident name match in the candidate pool — this audit is comparing "
        f"against an INCOMPLETE ground truth: {', '.join(audit_data['unmatched_real_squad'])}"
    )

audit = audit_data["audit"]

# ── Headline result ─────────────────────────────────────────────────────
st.subheader("Headline result")
col1, col2, col3 = st.columns(3)
col1.metric("Overlap", f"{audit['overlap_count']}/{audit['top_n']}", f"{audit['overlap_pct']:.0%}")
col2.metric("P(this much overlap | random selection)", f"{audit['random_baseline_pvalue']:.3f}")
col3.metric(
    "Verdict",
    "Beats random" if audit["beats_random_at_p05"] else "≈ Random",
    delta_color="normal" if audit["beats_random_at_p05"] else "inverse",
)

if not audit["beats_random_at_p05"]:
    st.error(
        "🔴 **The DSSScore-based top-26 selection is NOT statistically "
        "distinguishable from a random draw of 26 out of 49** (p="
        f"{audit['random_baseline_pvalue']:.3f}, needs < 0.05 to claim real skill). "
        "This is a single real-world validation point (n=1 coach decision), heavily "
        "confounded by a mid-cycle coaching change (Regragui → Ouahbi, 2026-03-05) "
        "the scoring formula was never tuned against. It does not *prove* the formula "
        "is wrong, but it is the only real evidence this project has, and it is "
        "not supportive."
    )
else:
    st.success(f"🟢 Beats random selection at p<0.05 (p={audit['random_baseline_pvalue']:.3f}).")

# ── Disagreement tables ──────────────────────────────────────────────────
st.subheader("Where the formula and reality disagree")
left, right = st.columns(2)

with left:
    st.markdown(f"**DSSScore favored (top-{audit['top_n']}), coach did NOT pick**")
    fp_df = pd.DataFrame(audit["dss_favored_not_picked"])
    if len(fp_df):
        st.dataframe(fp_df, width='stretch', hide_index=True)
    else:
        st.caption("None — every DSSScore top pick was actually selected.")

with right:
    st.markdown(f"**Coach picked, DSSScore ranked outside top-{audit['top_n']}**")
    fn_df = pd.DataFrame(audit["picked_not_dss_favored"])
    if len(fn_df):
        st.dataframe(fn_df, width='stretch', hide_index=True)
    else:
        st.caption("None — every real pick was already in the DSSScore top selection.")

# ── Exploratory LOOCV classifier ─────────────────────────────────────────
if "loocv_classifier" in audit_data:
    st.divider()
    st.subheader("Exploratory: does a data-driven reweighting of the same 9 sub-scores do better?")
    st.caption(
        "⚠️ LOOCV Logistic Regression on the 9 Gold sub-scores (not DSSScore itself, "
        "to avoid circularity). This is exploratory/interpretability output, NOT a "
        "predictor to trust for a future squad — see "
        "`analytics/ml/selection_classifier.py` module docstring."
    )
    loocv = audit_data["loocv_classifier"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Accuracy", f"{loocv['accuracy']:.3f}")
    c2.metric("Precision", f"{loocv['precision']:.3f}")
    c3.metric("Recall", f"{loocv['recall']:.3f}")
    c4.metric("F1", f"{loocv['f1']:.3f}")

    verdict = "Beats random baseline (p<0.05)" if loocv["beats_random_baseline"] else "Does NOT beat random baseline"
    st.markdown(f"**{verdict}** — p={loocv['random_baseline_pvalue']:.3f} (n={loocv['n_samples']})")

    coefs = pd.Series(loocv["standardized_coefficients"]).sort_values()
    fig = go.Figure(go.Bar(
        x=coefs.values, y=coefs.index, orientation="h",
        marker_color=["#d62728" if v < 0 else "#2ca02c" for v in coefs.values],
    ))
    fig.update_layout(
        title="Standardized logistic regression coefficients (interpretation only)",
        xaxis_title="Coefficient (standardized)", height=400, margin=dict(t=40, b=20),
    )
    st.plotly_chart(fig, width='stretch')
    st.caption(
        "Do not over-read individual signs/magnitudes — n=49 with correlated "
        "features means coefficients can be unstable. Green = pushes toward "
        "selection, red = pushes away, in this one fitted model."
    )

# ── Injury Risk Index (transparent, rule-based — NOT a trained model) ────
injury_data = load_latest_injury_predictor()
if injury_data is not None:
    st.divider()
    st.subheader("Related: Injury Risk Index (transparent, rule-based)")
    st.caption(
        "⚠️ NOT a trained model, NOT combined into DSSScore (AvailabilityScore "
        "already accounts for injury history there — see "
        "`analytics/ml/injury_predictor.py` module docstring for why this stays "
        "separate). A fixed-weight composite of age, injury history, and injury "
        "type, for medical/coaching staff review. Run `python run_injury_predictor.py` "
        "to refresh."
    )
    c1, c2, c3 = st.columns(3)
    counts = injury_data["level_counts"]
    c1.metric("🟢 Low risk", counts.get("Low", 0))
    c2.metric("🟡 Medium risk", counts.get("Medium", 0))
    c3.metric("🔴 High risk", counts.get("High", 0))

    n_low_conf = injury_data["n_low_confidence"]
    n_total = injury_data["n_players"]
    if n_low_conf:
        st.warning(
            f"⚠️ {n_low_conf}/{n_total} players ({n_low_conf / n_total:.0%}) flagged "
            f"**low confidence** (missing/unclear birth date or injury-type data) — "
            f"verify manually with medical staff before acting on their level."
        )
        with st.expander("Show low-confidence players"):
            st.write(", ".join(injury_data["low_confidence_players"]))

    st.markdown("**Highest risk (top 10)**")
    st.dataframe(pd.DataFrame(injury_data["highest_risk"]), width='stretch', hide_index=True)
else:
    st.caption("ℹ️ Run `python run_injury_predictor.py` to also show the injury risk index here.")

