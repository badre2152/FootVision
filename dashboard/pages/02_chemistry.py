# dashboard/pages/02_chemistry.py
# Football Atlas — Chemistry Matrix page

import sys
from pathlib import Path

import streamlit as st
import pandas as pd
import plotly.express as px

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dashboard.data_loader import load_latest_chemistry_pairs, load_latest_players_scored
from dashboard.sidebar import render_sidebar
from dashboard.auth import require_auth
from dashboard.report_utils import csv_download_button

st.set_page_config(page_title="Football Atlas — Chemistry Matrix", page_icon="🔗", layout="wide")
require_auth()
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
csv_download_button(top_pairs_display, filename_prefix="chemistry_best_duos", key="csv_chemistry")

# ── ChemistryScore — real vs imputed transparency ───────────────────────
st.divider()
st.subheader("ChemistryScore — real vs imputed (all candidates)")

players = load_latest_players_scored()

if players is None or "ChemistryScore" not in players.columns:
    st.info(
        "No `players_scored_*.csv` with ChemistryScore found. Run `run_gold.py` "
        "to see the real-vs-imputed breakdown for all candidates here."
    )
else:
    if "chem_is_real" in players.columns:
        confidence = players["chem_is_real"].map({True: "Real", False: "Imputed"})
    elif "chem_num_partners" in players.columns:
        confidence = (players["chem_num_partners"] > 0).map({True: "Real", False: "Imputed"})
    else:
        confidence = pd.Series("Unknown", index=players.index)

    chem_view = players.copy()
    chem_view["chem_confidence"] = confidence

    n_real = int((chem_view["chem_confidence"] == "Real").sum())
    n_imputed = int((chem_view["chem_confidence"] == "Imputed").sum())

    col1, col2 = st.columns(2)
    col1.metric(
        "Players with real StatsBomb chemistry data", n_real,
        help="Had at least one real pairwise chem_per90 value before Silver's position-group imputation ran.",
    )
    col2.metric("Position-median imputed (not their own signal)", n_imputed)

    if n_imputed > n_real:
        st.warning(
            f"⚠️ {n_imputed}/{len(chem_view)} players' ChemistryScore is a position-group "
            f"median fallback, not their own real signal — always check 'Data confidence' "
            f"before reading anything into an individual's ChemistryScore below."
        )

    fig3 = px.bar(
        chem_view.sort_values("ChemistryScore"),
        x="ChemistryScore", y="player_name", orientation="h",
        color="chem_confidence",
        color_discrete_map={"Real": "#22c55e", "Imputed": "#6b7280", "Unknown": "#6b7280"},
        height=max(500, 18 * len(chem_view)),
    )
    fig3.update_layout(margin=dict(t=20, b=20), yaxis_title="")
    st.plotly_chart(fig3, width="stretch")

    display_cols = [c for c in ["player_name", "position", "club", "chem_avg_per90", "ChemistryScore", "chem_confidence"] if c in chem_view.columns]
    chem_table = chem_view[display_cols].reset_index(drop=True)
    st.dataframe(
        chem_table.style.format(
            {"chem_avg_per90": "{:.3f}", "ChemistryScore": "{:.4f}"}, na_rep="—"
        ),
        width="stretch", height=450,
    )
    csv_download_button(chem_table, filename_prefix="chemistry_real_vs_imputed", key="csv_chemistry_transparency")