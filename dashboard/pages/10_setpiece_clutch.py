# dashboard/pages/10_setpiece_clutch.py
# Football Atlas — Set Piece & Clutch page
#
# Added 2026-07-30. Only the "Pressure Index" third of the original
# PROJECT.md spec is here — see analytics/set_piece_analyzer.py's
# docstring for exactly why "90+ minutes performance" and "set-piece
# specialists" aren't (no minute-level events, no set-piece tagging,
# anywhere in this project's data).

import sys
from pathlib import Path

import streamlit as st
import plotly.express as px

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dashboard.data_loader import load_latest_players_scored
from dashboard.sidebar import render_sidebar, apply_sidebar_filters
from dashboard.auth import require_auth
from dashboard.report_utils import csv_download_button
from analytics.performance.set_piece_analyzer import clutch_ranking

st.set_page_config(page_title="Football Atlas — Set Piece & Clutch", page_icon="🎯", layout="wide")
require_auth()  # spec #6 — must run before render_sidebar()/any data load below
render_sidebar()
st.title("🎯 Set Piece & Clutch")
st.caption(
    "Only 'big-match vs club-form' pressure analysis is built here. Set-piece "
    "specialist detection and 90+ minute clutch performance are NOT — this project "
    "has no data for either (see analytics/set_piece_analyzer.py)."
)

players = load_latest_players_scored()
if players is None:
    st.error("No Gold output found. Run `run_gold.py` first.")
    st.stop()

players = apply_sidebar_filters(players)
if players.empty:
    st.info("No players match the current sidebar filters — adjust them and try again.")
    st.stop()

try:
    result = clutch_ranking(players)
except ValueError as e:
    st.error(str(e))
    st.stop()

n_real = int((result["pressure_data_confidence"] == "Real").sum())
n_imputed = int((result["pressure_data_confidence"] == "Imputed").sum())

col1, col2, col3 = st.columns(3)
col1.metric("Players with real StatsBomb pressure data", n_real, help="Only players covered by StatsBomb open data (WC2018/2022, AFCON2023) with a club xG baseline")
col2.metric("Position-median imputed (not their own signal)", n_imputed)
col3.metric("Thrives under pressure", int((result["ClutchLabel"] == "Thrives Under Pressure").sum()))

if n_imputed > n_real:
    st.warning(
        f"⚠️ {n_imputed}/{len(result)} players' ClutchLabel is a position-group median "
        f"fallback, not their own real signal — always check 'Data confidence' before "
        f"reading anything into an individual's label below."
    )

st.divider()

left, right = st.columns([3, 2])

with left:
    st.subheader("PressureIndex — ranked")
    fig = px.bar(
        result.sort_values("PressureIndex"),
        x="PressureIndex", y="player_name", orientation="h",
        color="pressure_data_confidence",
        color_discrete_map={"Real": "#22c55e", "Imputed": "#6b7280", "Unknown": "#6b7280"},
        height=max(500, 18 * len(result)),
    )
    fig.update_layout(margin=dict(t=20, b=20), yaxis_title="")
    st.plotly_chart(fig, width="stretch")

with right:
    st.subheader("Real StatsBomb signal only")
    real_only = result[result["pressure_data_confidence"] == "Real"]
    if real_only.empty:
        st.info("No players with real (non-imputed) pressure data in the current filter.")
    else:
        fig2 = px.bar(
            real_only.sort_values("pressure_delta"),
            x="pressure_delta", y="player_name", orientation="h",
            color="ClutchLabel",
            color_discrete_map={
                "Thrives Under Pressure": "#22c55e",
                "Consistent": "#f59e0b",
                "Drops Off": "#ef4444",
            },
            height=max(400, 24 * len(real_only)),
        )
        fig2.update_layout(margin=dict(t=20, b=20), yaxis_title="")
        st.plotly_chart(fig2, width="stretch")
        st.caption("International big-match xG minus club xG, per 90 — the only players with a real signal here.")

st.divider()
st.subheader("Full breakdown")
display_cols = ["player_name", "position", "club", "PressureIndex", "ClutchLabel", "pressure_data_confidence"]
table = result[display_cols].reset_index(drop=True)
st.dataframe(
    table.style.format({"PressureIndex": "{:.4f}"}),
    width="stretch", height=450,
)
csv_download_button(table, filename_prefix="setpiece_clutch_breakdown", key="csv_setpiece_clutch")