# dashboard/pages/05_fitness.py
# Football Atlas — Fitness & Rotation page
#
# Added 2026-07-30. Uses analytics/ml/injury_predictor.py (transparent,
# rule-based risk index — NOT a trained model, see that module's
# docstring) + Gold's existing AvailabilityScore.
#
# IMPORTANT: InjuryRiskLevel is advisory only, same rule as everywhere
# else it's used in this project — it must NEVER be treated as an
# automatic exclusion filter for squad selection. This page presents it
# for the medical/coaching staff to review, nothing more. See
# injury_predictor.py's "CRITICAL: DO NOT DOUBLE-COUNT" section for why
# it's also never summed into DSSScore.
#
# "Rotation Strategy sur 3 matchs" (ROADMAP.md § Jour 7) is NOT built —
# that needs an opponent/fixture-by-fixture minutes-management model this
# project has no data or design for yet (a separate, bigger scope than
# this page). This page covers the "Fitness" half only, honestly.

import sys
from pathlib import Path

import streamlit as st
import plotly.express as px

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dashboard.data_loader import load_latest_players_scored
from dashboard.sidebar import render_sidebar, apply_sidebar_filters
from dashboard.auth import require_auth
from dashboard.report_utils import csv_download_button
from analytics.ml.injury_predictor import compute_injury_risk

st.set_page_config(page_title="Football Atlas — Fitness & Rotation", page_icon="🩺", layout="wide")
require_auth()  # spec #6 — must run before render_sidebar()/any data load below
render_sidebar()
st.title("🩺 Fitness & Rotation")
st.caption(
    "Injury risk is a transparent, rule-based advisory index — NOT a trained model, "
    "and NOT part of DSSScore. See analytics/ml/injury_predictor.py for full methodology."
)

players = load_latest_players_scored()
if players is None:
    st.error("No Gold output found. Run `run_gold.py` first.")
    st.stop()

players = apply_sidebar_filters(players)
if players.empty:
    st.info("No players match the current sidebar filters — adjust them and try again.")
    st.stop()

required_cols = ["wiki_dob", "inj_count_2y", "inj_days_missed_2y"]
missing_cols = [c for c in required_cols if c not in players.columns]
if missing_cols:
    st.error(
        f"players_scored_*.csv is missing {missing_cols} — this file must be produced "
        f"with the Silver injury/dob columns joined in. Injury risk cannot be computed "
        f"without them."
    )
    st.stop()

result = compute_injury_risk(players)
risk_df = result.df

# ── Summary row ──────────────────────────────────────────────────────────
col1, col2, col3, col4 = st.columns(4)
col1.metric("Low risk", int((risk_df["InjuryRiskLevel"] == "Low").sum()))
col2.metric("Medium risk", int((risk_df["InjuryRiskLevel"] == "Medium").sum()))
col3.metric("High risk", int((risk_df["InjuryRiskLevel"] == "High").sum()))
col4.metric("Low-confidence data", result.n_low_confidence, help="Missing/unclear age or injury-type data — verify manually")

if result.n_low_confidence:
    st.warning(
        f"⚠️ {result.n_low_confidence} player(s) have missing or ambiguous injury-history "
        f"data (see 'Data confidence' column below) — their risk level is a weaker signal "
        f"than for players with complete data. Do not treat 'Low' risk for a low-confidence "
        f"player as equivalent to a verified clean bill of health."
    )

st.divider()

# ── AvailabilityScore vs InjuryRiskScore scatter — these measure related
# but distinct things (see injury_predictor.py docstring: AvailabilityScore
# is already inside DSSScore; InjuryRiskScore adds age + injury-type
# context on top, purely advisory) ─────────────────────────────────────
left, right = st.columns([3, 2])

with left:
    st.subheader("Availability (in DSSScore) vs Injury Risk (advisory only)")
    fig = px.scatter(
        risk_df, x="AvailabilityScore", y="InjuryRiskScore",
        color="InjuryRiskLevel", hover_name="player_name",
        color_discrete_map={"Low": "#22c55e", "Medium": "#f59e0b", "High": "#ef4444"},
        category_orders={"InjuryRiskLevel": ["Low", "Medium", "High"]},
    )
    fig.update_layout(height=420, margin=dict(t=20, b=20))
    st.plotly_chart(fig, width="stretch")
    st.caption(
        "Bottom-right (high AvailabilityScore, high InjuryRiskScore) is the group worth "
        "a second look — good recent availability doesn't rule out age/history-based risk."
    )

with right:
    st.subheader("Risk level breakdown")
    level_counts = risk_df["InjuryRiskLevel"].value_counts().reindex(["Low", "Medium", "High"]).fillna(0)
    fig2 = px.bar(
        level_counts, orientation="h",
        color=level_counts.index,
        color_discrete_map={"Low": "#22c55e", "Medium": "#f59e0b", "High": "#ef4444"},
    )
    fig2.update_layout(showlegend=False, height=420, margin=dict(t=20, b=20), xaxis_title="Players", yaxis_title="")
    st.plotly_chart(fig2, width="stretch")

st.divider()

# ── Full table, sortable by risk ─────────────────────────────────────────
st.subheader("Full squad — sorted by injury risk")
display_cols = [
    "player_name", "position", "club", "InjuryRiskLevel", "InjuryRiskScore",
    "injury_risk_confidence", "inj_count_2y", "inj_days_missed_2y", "AvailabilityScore",
]
if "inj_last_type" in risk_df.columns:
    display_cols.insert(-1, "inj_last_type")

table = risk_df[display_cols].sort_values("InjuryRiskScore", ascending=False).reset_index(drop=True)
st.dataframe(
    table.style.format({"InjuryRiskScore": "{:.3f}", "AvailabilityScore": "{:.3f}"}),
    width="stretch", height=450,
)
csv_download_button(table, filename_prefix="fitness_injury_risk", key="csv_fitness")