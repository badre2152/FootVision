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
#
# Localized (issue #15) — fully translated, including the Plotly chart's
# title/axis label, per the spec's requirement that chart labels not stay
# English-only in a bilingual build. All static text goes through
# dashboard/i18n.py's t() helper so it follows the sidebar language
# switcher (EN/FR/AR).

import sys
from pathlib import Path

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dashboard.data_loader import load_latest_selection_audit, load_latest_injury_predictor
from dashboard.sidebar import render_sidebar
from dashboard.auth import require_auth
from dashboard.i18n import t
from dashboard.report_utils import csv_download_button
from dashboard.components import post_hoc_banner

st.set_page_config(page_title="Football Atlas — Selection Audit", page_icon="🔍", layout="wide")
require_auth()  # spec #6 — must run before render_sidebar()/any data load below
render_sidebar()
st.title(t("nav.selection_audit_title"))

audit_data = load_latest_selection_audit()
if audit_data is None:
    st.warning(t("audit.no_data_warning"))
    st.stop()

# This page audits DSSScore's top-26 against the REAL announced World Cup
# squad (2026-05-26) — information that was not knowable as of
# config.DECISION_DATE. Same treatment as the real-opponent-result section
# of dashboard/pages/8_competition.py (post_hoc_banner() itself,
# spec "05_dashboard_decision_vs_audit_labeling.md").
post_hoc_banner()

st.caption(t(
    "audit.source_caption",
    real_source=audit_data['real_squad_source'],
    source_file=audit_data['source_file'],
    generated_at=audit_data['generated_at'],
))

if audit_data.get("unmatched_real_squad"):
    st.error(t(
        "audit.unmatched_error",
        n=len(audit_data['unmatched_real_squad']),
        names=", ".join(audit_data['unmatched_real_squad']),
    ))

audit = audit_data["audit"]

# ── Headline result ─────────────────────────────────────────────────────
st.subheader(t("audit.headline_header"))
col1, col2, col3 = st.columns(3)
col1.metric(t("audit.overlap_metric"), f"{audit['overlap_count']}/{audit['top_n']}", f"{audit['overlap_pct']:.0%}")
col2.metric(t("audit.pvalue_metric"), f"{audit['random_baseline_pvalue']:.3f}")
col3.metric(
    t("audit.verdict_metric"),
    t("audit.verdict_beats") if audit["beats_random_at_p05"] else t("audit.verdict_random"),
    delta_color="normal" if audit["beats_random_at_p05"] else "inverse",
)

if not audit["beats_random_at_p05"]:
    st.error(t("audit.verdict_error", pvalue=f"{audit['random_baseline_pvalue']:.3f}"))
else:
    st.success(t("audit.verdict_success", pvalue=f"{audit['random_baseline_pvalue']:.3f}"))

# ── Disagreement tables ──────────────────────────────────────────────────
st.subheader(t("audit.disagreement_header"))
left, right = st.columns(2)

with left:
    st.markdown(f"**{t('audit.dss_favored_header', n=audit['top_n'])}**")
    fp_df = pd.DataFrame(audit["dss_favored_not_picked"])
    if len(fp_df):
        st.dataframe(fp_df, width='stretch', hide_index=True)
        csv_download_button(fp_df, filename_prefix="audit_dss_favored_not_picked", key="csv_audit_fp")
    else:
        st.caption(t("audit.dss_favored_empty"))

with right:
    st.markdown(f"**{t('audit.coach_picked_header', n=audit['top_n'])}**")
    fn_df = pd.DataFrame(audit["picked_not_dss_favored"])
    if len(fn_df):
        st.dataframe(fn_df, width='stretch', hide_index=True)
        csv_download_button(fn_df, filename_prefix="audit_picked_not_dss_favored", key="csv_audit_fn")
    else:
        st.caption(t("audit.coach_picked_empty"))

# ── Exploratory LOOCV classifier ─────────────────────────────────────────
if "loocv_classifier" in audit_data:
    st.divider()
    st.subheader(t("audit.loocv_header"))
    st.caption(t("audit.loocv_caption"))
    loocv = audit_data["loocv_classifier"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(t("audit.metric_accuracy"), f"{loocv['accuracy']:.3f}")
    c2.metric(t("audit.metric_precision"), f"{loocv['precision']:.3f}")
    c3.metric(t("audit.metric_recall"), f"{loocv['recall']:.3f}")
    c4.metric(t("audit.metric_f1"), f"{loocv['f1']:.3f}")

    verdict = t("audit.loocv_verdict_beats") if loocv["beats_random_baseline"] else t("audit.loocv_verdict_not")
    st.markdown(t("audit.loocv_verdict_text", verdict=verdict, pvalue=f"{loocv['random_baseline_pvalue']:.3f}", n=loocv['n_samples']))

    coefs = pd.Series(loocv["standardized_coefficients"]).sort_values()
    fig = go.Figure(go.Bar(
        x=coefs.values, y=coefs.index, orientation="h",
        marker_color=["#d62728" if v < 0 else "#2ca02c" for v in coefs.values],
    ))
    fig.update_layout(
        title=t("audit.chart_title"),
        xaxis_title=t("audit.chart_xaxis"), height=400, margin=dict(t=40, b=20),
    )
    st.plotly_chart(fig, width='stretch')
    st.caption(t("audit.loocv_footer_caption"))

# ── Injury Risk Index (transparent, rule-based — NOT a trained model) ────
injury_data = load_latest_injury_predictor()
if injury_data is not None:
    st.divider()
    st.subheader(t("audit.injury_header"))
    st.caption(t("audit.injury_caption"))
    c1, c2, c3 = st.columns(3)
    counts = injury_data["level_counts"]
    c1.metric(t("audit.risk_low"), counts.get("Low", 0))
    c2.metric(t("audit.risk_medium"), counts.get("Medium", 0))
    c3.metric(t("audit.risk_high"), counts.get("High", 0))

    n_low_conf = injury_data["n_low_confidence"]
    n_total = injury_data["n_players"]
    if n_low_conf:
        st.warning(t(
            "audit.low_confidence_warning",
            n_low=n_low_conf, n_total=n_total, pct=f"{n_low_conf / n_total:.0%}",
        ))
        with st.expander(t("audit.low_confidence_expander")):
            st.write(", ".join(injury_data["low_confidence_players"]))

    st.markdown(f"**{t('audit.highest_risk_header')}**")
    highest_risk_df = pd.DataFrame(injury_data["highest_risk"])
    st.dataframe(highest_risk_df, width='stretch', hide_index=True)
    csv_download_button(highest_risk_df, filename_prefix="injury_highest_risk", key="csv_injury_highest_risk")
else:
    st.caption(t("audit.injury_footer_caption"))