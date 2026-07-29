# dashboard/pages/2_player_analysis.py
# Football Atlas — Player Analysis page
#
# Radar chart of a selected player's composite sub-scores, using whichever
# columns actually exist in the loaded players_scored_*.csv (older runs
# won't have Chemistry/Pressure/Flexibility — see data_loader.py).

import sys
from pathlib import Path

import streamlit as st
import plotly.graph_objects as go

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dashboard.data_loader import load_latest_players_scored, available_component_columns
from dashboard.sidebar import render_sidebar, apply_sidebar_filters

st.set_page_config(page_title="Football Atlas — Player Analysis", page_icon="📊", layout="wide")
render_sidebar()
st.title("📊 Player Analysis")

players = load_latest_players_scored()
if players is None:
    st.error("No Gold output found. Run `run_gold.py` first.")
    st.stop()

players = apply_sidebar_filters(players)
if players.empty:
    st.info("No players match the current sidebar filters — adjust them and try again.")
    st.stop()

component_cols = available_component_columns(players)
if not component_cols:
    st.error("No composite sub-score columns found in this file — was it produced by run_gold.py?")
    st.stop()

# ── Player selector ──────────────────────────────────────────────────────
players_sorted = players.sort_values("DSSScore", ascending=False)
player_labels = [
    f"#{int(row['Rank'])} {row['player_name']} ({row['position']})"
    for _, row in players_sorted.iterrows()
]
label_to_id = dict(zip(player_labels, players_sorted["player_id"]))

selected_label = st.selectbox("Select a player", player_labels)
selected_id = label_to_id[selected_label]
row = players[players["player_id"] == selected_id].iloc[0]

# ── Header metrics ───────────────────────────────────────────────────────
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Club", row.get("club", "—"))
col2.metric("Position", row.get("position", "—"))
col3.metric("DSSScore", f"{row['DSSScore']:.4f}", f"Rank #{int(row['Rank'])}")
if "minutes_flag" in row.index:
    col4.metric("Minutes flag", row["minutes_flag"])
if "PlayerDNA" in row.index and row["PlayerDNA"] not in (None, "N/A"):
    col5.metric("Player DNA", row["PlayerDNA"])  # descriptive tag, not a score input

st.divider()

# ── Radar chart ───────────────────────────────────────────────────────────
left, right = st.columns([2, 1])

with left:
    values = [row[c] for c in component_cols]
    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(
        r=values + [values[0]],
        theta=component_cols + [component_cols[0]],
        fill="toself",
        name=row["player_name"],
    ))
    fig.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 1])),
        showlegend=False,
        height=450,
        margin=dict(t=30, b=30),
    )
    st.plotly_chart(fig, width='stretch')

with right:
    st.subheader("Raw sub-scores")
    for c in component_cols:
        st.progress(min(max(float(row[c]), 0.0), 1.0), text=f"{c}: {row[c]:.3f}")

    missing = [c for c in ["ChemistryScore", "PressureIndex", "FlexibilityScore"] if c not in players.columns]
    if missing:
        st.caption(f"ℹ️ Not available in this run: {', '.join(missing)} (re-run `run_gold.py` with the 2026-07-14 update)")

st.divider()

# ── Comparison scatter: this player vs the rest of their position group ──
st.subheader(f"How does {row['player_name']} compare to other {row['position']}s?")
same_position = players[players["position"] == row["position"]].sort_values("DSSScore", ascending=False)
st.bar_chart(same_position.set_index("player_name")["DSSScore"])