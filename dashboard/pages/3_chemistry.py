# dashboard/pages/3_chemistry.py
# Football Atlas — Chemistry Matrix page
#
# Heatmap of REAL pairwise pass-network data from StatsBomb Open Data
# (see analytics/scoring/chemistry_score.py + ingestion/loaders/
# statsbomb_loader.py::_aggregate_pairwise_chemistry). Only covers the
# 11/49 candidates who appeared in WC2018/2022 or AFCON2023 matches — this
# page says so explicitly rather than implying full 49x49 coverage.

import sys
from pathlib import Path

import streamlit as st
import pandas as pd
import plotly.express as px

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dashboard.data_loader import load_latest_chemistry_pairs, load_latest_players_scored
from dashboard.sidebar import render_sidebar

st.set_page_config(page_title="Football Atlas — Chemistry Matrix", page_icon="🔗", layout="wide")
render_sidebar()
st.title("🔗 Chemistry Matrix")
st.caption(
    "Pairwise pass-network chemistry from StatsBomb Open Data "
    "(WC2018/2022 + AFCON2023 matches only)."
)

pairs = load_latest_chemistry_pairs()

if not pairs:
    st.warning(
        "No `chemistry_pairs_*.json` found under `bronze/statsbomb_chemistry/`. "
        "This is built by `statsbomb_loader.py::_aggregate_pairwise_chemistry()` "
        "(added 2026-07-14) — run `run_bronze.py` (or just the StatsBomb loader) "
        "to generate it."
    )
    st.stop()

pairs_df = pd.DataFrame(pairs)
all_players = sorted(set(pairs_df["player_a_name"]) | set(pairs_df["player_b_name"]))

st.info(
    f"**{len(all_players)}/49** candidates have real pairwise data here "
    f"(**{len(pairs_df)} pairs**) — only players who shared a StatsBomb-covered "
    "international match. The other 38/49 get a position-group median "
    "ChemistryScore in the main scoring pipeline (not shown here, since "
    "there's no real *pairwise* signal for them)."
)

# ── Build symmetric matrix for the heatmap ──────────────────────────────
matrix = pd.DataFrame(0.0, index=all_players, columns=all_players)
for _, r in pairs_df.iterrows():
    matrix.loc[r["player_a_name"], r["player_b_name"]] = r["chem_per90"]
    matrix.loc[r["player_b_name"], r["player_a_name"]] = r["chem_per90"]

fig = px.imshow(
    matrix,
    color_continuous_scale="Viridis",
    labels=dict(color="Combined passes/90"),
    aspect="auto",
)
fig.update_layout(height=600, margin=dict(t=30, b=30))
st.plotly_chart(fig, width='stretch')

st.divider()

st.subheader("Best duos (highest combined passes/90)")
top_pairs = pairs_df.sort_values("chem_per90", ascending=False).head(10)
top_pairs_display = top_pairs[["player_a_name", "player_b_name", "chem_per90", "shared_matches", "passes_combined"]].rename(
    columns={
        "player_a_name": "Player A", "player_b_name": "Player B",
        "chem_per90": "Combined passes/90", "shared_matches": "Shared matches",
        "passes_combined": "Total passes",
    }
)
st.dataframe(top_pairs_display, width='stretch', hide_index=True)