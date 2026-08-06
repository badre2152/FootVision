import sys
from pathlib import Path

import streamlit as st
import plotly.express as px

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dashboard.data_loader import (
    load_all_players_scored_history,
    compute_biggest_movers,
    available_component_columns,
)
from dashboard.sidebar import render_sidebar, apply_sidebar_filters
from dashboard.auth import require_auth
from dashboard.components import pre_cutoff_fix_banner
from dashboard.report_utils import csv_download_button

st.set_page_config(page_title="Football Atlas — Historical Trend View", page_icon="📈", layout="wide")
require_auth()
render_sidebar()
st.title("📈 Historical Trend View")
st.caption(
    "DSSScore/Rank trajectory across every available Gold snapshot from the prep window — "
    "not just the latest run."
)

history = load_all_players_scored_history()
if history.empty:
    st.error("No `gold/players_scored_*.csv` snapshots found. Run `run_gold.py` at least once.")
    st.stop()

if history["is_pre_cutoff_fix"].any():
    n_pre_fix = history.loc[history["is_pre_cutoff_fix"], "source_file"].nunique()
    n_total = history["source_file"].nunique()
    pre_cutoff_fix_banner(
        extra=f"{n_pre_fix} of {n_total} snapshot(s) shown below predate the temporal-cutoff fix."
    )

n_snapshots = history["run_date"].nunique()
if n_snapshots < 2:
    st.info(
        f"Only {n_snapshots} snapshot found — run `run_gold.py` again on a later date to start "
        "seeing a trend. Showing what's available below."
    )

history = apply_sidebar_filters(history)
if history.empty:
    st.info("No players match the current sidebar filters — adjust them and try again.")
    st.stop()

st.divider()

st.subheader("Player trend")

latest_run = history["run_date"].max()
latest_snapshot = history[history["run_date"] == latest_run].sort_values("DSSScore", ascending=False)

player_labels = [
    f"#{int(row['Rank'])} {row['player_name']} ({row['position']})"
    for _, row in latest_snapshot.iterrows()
]
label_to_id = dict(zip(player_labels, latest_snapshot["player_id"]))

if not player_labels:
    st.info("No players available to plot after filtering.")
    st.stop()

selected_label = st.selectbox("Select a player", player_labels)
selected_id = label_to_id[selected_label]

player_history = history[history["player_id"] == selected_id].sort_values("run_date")
player_name = player_history.iloc[-1]["player_name"]

metric_options = ["DSSScore", "Rank"] + [
    c for c in available_component_columns(player_history) if c in player_history.columns
]
selected_metric = st.radio("Metric", metric_options, horizontal=True)

fig = px.line(
    player_history,
    x="run_date",
    y=selected_metric,
    markers=True,
    title=f"{player_name} — {selected_metric} over time ({n_snapshots} snapshot{'s' if n_snapshots != 1 else ''})",
)
if selected_metric == "Rank":
    fig.update_yaxes(autorange="reversed")
fig.update_layout(height=420, margin=dict(t=50, b=30))
st.plotly_chart(fig, width='stretch')

with st.expander("Raw snapshot values for this player"):
    display_cols = ["run_date", "source_file", "DSSScore", "Rank"] + [
        c for c in available_component_columns(player_history) if c not in ("DSSScore", "Rank")
    ]
    display_cols = [c for c in display_cols if c in player_history.columns]
    st.dataframe(player_history[display_cols], width='stretch', hide_index=True)
    csv_download_button(player_history[display_cols], filename_prefix="player_trend_history", key="csv_trend_player")

st.divider()

st.subheader("Biggest movers")
st.caption("Largest Rank changes between the two most recent snapshots (up or down).")

movers = compute_biggest_movers(history)
if movers.empty:
    st.info("Need at least two snapshots (after sidebar filters) to compute movers.")
else:
    previous_date = movers.iloc[0]["previous_run_date"]
    current_date = movers.iloc[0]["current_run_date"]
    st.caption(f"Comparing {previous_date} → {current_date}")

    display = movers.copy()
    display["Movement"] = display["rank_change"].apply(
        lambda x: f"⬆️ +{int(x)}" if x > 0 else (f"⬇️ {int(x)}" if x < 0 else "— 0")
    )
    display = display.rename(columns={
        "player_name": "Player",
        "position": "Position",
        "previous_rank": "Previous Rank",
        "current_rank": "Current Rank",
    })
    st.dataframe(
        display[["Player", "Position", "Previous Rank", "Current Rank", "Movement"]],
        width='stretch',
        hide_index=True,
    )
    csv_download_button(
        display[["Player", "Position", "Previous Rank", "Current Rank", "Movement"]],
        filename_prefix="biggest_movers", key="csv_trend_movers",
    )