# dashboard/app.py
# Football Atlas — Streamlit Dashboard, Executive Overview (page 1/4)
#
# Added 2026-07-14. Run with:  streamlit run dashboard/app.py
#
# Reads the latest Gold output from gold/ (see data_loader.py). If you
# haven't run run_gold.py yet, this shows a clear "no data" message
# instead of crashing.

import sys
from pathlib import Path

import streamlit as st
import pandas as pd
import plotly.express as px

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard.data_loader import (
    load_latest_top26,
    load_latest_players_scored,
    load_latest_squad_summary,
)
from dashboard.sidebar import render_sidebar, apply_sidebar_filters

st.set_page_config(
    page_title="Football Atlas — Executive Dashboard",
    page_icon="⚽",
    layout="wide",
)

render_sidebar()

st.title("⚽ Football Atlas — Executive Dashboard")
st.caption("Decision Support System — Morocco World Cup 2026 squad selection")

top26   = load_latest_top26()
players = load_latest_players_scored()
summary = load_latest_squad_summary()

if top26 is None or players is None or summary is None:
    st.error(
        "No Gold output found in `gold/`. Run the pipeline first:\n\n"
        "```bash\npython run_bronze.py\npython run_silver.py\npython run_gold.py --top-n 26\n```"
    )
    st.stop()

st.caption(f"Data source: `{summary.get('_source_file', 'unknown')}` — generated {summary.get('generated_at', 'unknown')}")

# ── KPI row ───────────────────────────────────────────────────────────────
col1, col2, col3, col4 = st.columns(4)
col1.metric("Candidates tracked", summary.get("total_candidates", len(players)))
col2.metric("Squad size", summary.get("squad_size", len(top26)))
top_player = summary.get("top_player", {})
col3.metric("Top player", top_player.get("name", "—"), f"DSS={top_player.get('score', 0):.4f}")

score_range = summary.get("score_range")
if score_range:
    col4.metric("DSS range (all 49)", f"{score_range['min']:.3f} – {score_range['max']:.3f}")
else:
    # Older squad_summary.json (pre-2026-07-14) doesn't have score_range —
    # compute it on the fly rather than hiding the metric entirely.
    col4.metric("DSS range (all 49)", f"{players['DSSScore'].min():.3f} – {players['DSSScore'].max():.3f}")

st.divider()

# ── Position breakdown ──────────────────────────────────────────────────────
left, right = st.columns([1, 2])

with left:
    st.subheader("Position breakdown")
    position_counts = summary.get("position_counts")
    if position_counts is None:
        # Older squad_summary.json — derive it from position_breakdown lists instead
        position_counts = {pos: len(names) for pos, names in summary.get("position_breakdown", {}).items()}
    counts_df = pd.DataFrame(list(position_counts.items()), columns=["Position", "Count"])
    fig = px.pie(counts_df, names="Position", values="Count", hole=0.4,
                 color="Position",
                 color_discrete_map={"GK": "#e63946", "DEF": "#457b9d", "MID": "#2a9d8f", "FWD": "#f4a261"})
    fig.update_layout(margin=dict(t=10, b=10, l=10, r=10), height=280)
    st.plotly_chart(fig, width='stretch')

    expected_caps = {"GK": 3, "DEF": 9, "MID": 9, "FWD": 5}
    mismatches = [
        f"{pos}: {position_counts.get(pos, 0)} (expected {cap})"
        for pos, cap in expected_caps.items()
        if position_counts.get(pos, 0) != cap
    ]
    if mismatches:
        st.warning(
            "Position counts don't match the standard 3/9/9/5 cap — "
            "if this squad_summary.json predates 2026-07-14, it may have "
            "the position-cap bug (see AUDIT_AND_FIXES / MASTER_SPEC "
            "addendum): " + "; ".join(mismatches)
        )

with right:
    st.subheader("⚠️ Flags")
    flags = summary.get("flags", {})
    if flags.get("high_injury_risk"):
        st.markdown(f"**High injury risk ({len(flags['high_injury_risk'])}):** " + ", ".join(flags["high_injury_risk"]))
    if flags.get("limited_data"):
        st.markdown(f"**Limited data coverage ({len(flags['limited_data'])}):** " + ", ".join(flags["limited_data"]))
    if flags.get("intl_caps_possibly_missing"):
        st.markdown(f"**Intl caps possibly missing ({len(flags['intl_caps_possibly_missing'])}):** " + ", ".join(flags["intl_caps_possibly_missing"]))
    if not any(flags.values()):
        st.markdown("No flags raised for this squad.")

    pct_summary = summary.get("percentile_summary")
    if pct_summary:
        st.subheader("Score spread (display scaling)")
        st.markdown(
            f"Elite-to-fringe gap: **{pct_summary['elite_fringe_gap_original']:.3f}** (raw DSS) "
            f"→ **{pct_summary['elite_fringe_gap_percentile']:.3f}** (display-scaled, ×{pct_summary['expansion_factor']:.2f}). "
            "See `DSSScore_pct` in the table below — ranking is identical, only the visual spread is stretched."
        )

st.divider()

# ── Full squad table ─────────────────────────────────────────────────────
st.subheader(f"Squad — top {len(top26)}")
top26_filtered = apply_sidebar_filters(top26)
display_cols = [c for c in [
    "Rank", "player_name", "position", "club", "league",
    "DSSScore", "DSSScore_pct", "PerformanceScore", "FormScore",
    "MentalScore", "AvailabilityScore", "InternationalScore",
    "ChemistryScore", "PressureIndex", "FlexibilityScore", "SentimentScore",
] if c in top26_filtered.columns]

if top26_filtered.empty:
    st.info("No players match the current sidebar filters.")
else:
    st.dataframe(
        top26_filtered[display_cols].style.background_gradient(subset=["DSSScore"], cmap="RdYlGn"),
        width='stretch',
        hide_index=True,
    )

missing_new_cols = [c for c in ["ChemistryScore", "PressureIndex", "FlexibilityScore", "SentimentScore", "DSSScore_pct"] if c not in top26.columns]
if missing_new_cols:
    st.caption(
        f"ℹ️ This run predates the 2026-07-14 session — {', '.join(missing_new_cols)} "
        "not present. Re-run `run_gold.py` with the updated code to see them."
    )

st.caption("👈 Use the sidebar to open **Player Analysis**, **Chemistry Matrix**, and **Final Squad** pages.")