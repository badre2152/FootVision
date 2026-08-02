# dashboard/pages/8_competition.py
# Football Atlas — Competition Context page
#
# Added 2026-07-30. Two independent, honestly-scoped pieces:
#   1. Best XI generator (analytics/competition_context.py) — formation-
#      based lineup from the squad you've already selected/scored. No
#      opponent data involved (see that module's docstring).
#   2. Morocco's REAL 2026 World Cup fixture/result record
#      (analytics/opponent_analyzer.py) — static historical fact, not
#      tactical analysis.
#
# NEITHER piece does "Tactical Fit vs a specific opponent" — that stays
# unimplemented (see both modules' tactical_*() stubs) because no
# opponent squad/formation/style data source exists anywhere in this
# project's Bronze/Silver layers. This page does not pretend otherwise.

import sys
from pathlib import Path

import streamlit as st
import plotly.express as px

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dashboard.data_loader import load_latest_top26, load_latest_players_scored
from dashboard.sidebar import render_sidebar, apply_sidebar_filters
from dashboard.auth import require_auth
from dashboard.components import post_hoc_banner
from dashboard.report_utils import csv_download_button
from analytics.competition_context import build_best_xi, compare_formations, FORMATIONS
from analytics.opponent_analyzer import get_fixtures, tournament_run_summary

st.set_page_config(page_title="Football Atlas — Competition Context", page_icon="🌍", layout="wide")
require_auth()  # spec #6 — must run before render_sidebar()/any data load below
render_sidebar()
st.title("🌍 Competition Context")

top26 = load_latest_top26()
if top26 is None:
    st.error("No Gold output found. Run `run_gold.py` first.")
    st.stop()

players_full = load_latest_players_scored()  # used as fallback_pool if top26 is short a position

tab_best_xi, tab_history = st.tabs(["⚽ Best XI by Formation", "🏆 Morocco's Real WC2026 Run"])

# ─────────────────────────────────────────────────────────────────────────
# TAB 1 — Best XI Generator
# ─────────────────────────────────────────────────────────────────────────
with tab_best_xi:
    st.caption(
        "Formation-based lineup from your selected top-26, ranked purely by DSSScore "
        "within each position — no opponent-specific tactical fit (see "
        "analytics/competition_context.py docstring for why)."
    )

    formation = st.selectbox("Formation", list(FORMATIONS.keys()), index=0)

    result = build_best_xi(top26, formation=formation, fallback_pool=players_full)

    if result.shortfalls:
        st.warning(
            f"⚠️ Squad depth shortfall for this formation: {result.shortfalls} — "
            f"some slots may be unfilled below."
        )
    if result.used_fallback_pool:
        st.info(
            "ℹ️ At least one slot was filled from outside the top-26 (the wider "
            "49-candidate pool) because the top-26 alone didn't have enough depth "
            "at that position for this formation."
        )

    left, right = st.columns([3, 2])

    with left:
        st.subheader(f"Starting XI — {formation}")
        position_order = {"GK": 0, "DEF": 1, "MID": 2, "FWD": 3}
        xi_display = result.starting_xi.copy()
        xi_display["_order"] = xi_display["position"].map(position_order)
        xi_display = xi_display.sort_values(["_order", "DSSScore"], ascending=[True, False])
        cols_to_show = [c for c in ["player_name", "position", "club", "DSSScore"] if c in xi_display.columns]
        st.dataframe(
            xi_display[cols_to_show].style.format({"DSSScore": "{:.4f}"}),
            width="stretch", height=430, hide_index=True,
        )
        csv_download_button(xi_display[cols_to_show], filename_prefix="competition_starting_xi", key="csv_competition_xi")

    with right:
        st.subheader("Bench")
        bench_cols = [c for c in ["player_name", "position", "DSSScore"] if c in result.bench.columns]
        st.dataframe(
            result.bench[bench_cols].style.format({"DSSScore": "{:.4f}"}),
            width="stretch", height=430, hide_index=True,
        )
        csv_download_button(result.bench[bench_cols], filename_prefix="competition_bench", key="csv_competition_bench")

    st.divider()
    st.subheader("Compare all formations")
    st.caption("Which shape best uses the squad depth you actually have — not a tactical recommendation.")
    comparison = compare_formations(top26, formations=list(FORMATIONS.keys()))
    fig = px.bar(
        comparison, x="formation", y="total_DSSScore",
        text=comparison["total_DSSScore"].round(3),
        color="formation",
    )
    fig.update_layout(showlegend=False, height=380, margin=dict(t=20, b=20))
    st.plotly_chart(fig, width="stretch")

# ─────────────────────────────────────────────────────────────────────────
# TAB 2 — Real World Cup Run
# ─────────────────────────────────────────────────────────────────────────
with tab_history:
    post_hoc_banner(
        "Covers everything in this tab: the summary metrics below, the "
        "match-by-match results, and the goals-for/against chart."
    )
    st.caption(
        "Morocco's real, final 2026 FIFA World Cup match record — static historical "
        "data (the tournament has concluded), not opponent tactical analysis. "
        "See analytics/opponent_analyzer.py for sourcing."
    )

    summary = tournament_run_summary()
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Final round reached", summary.final_round_reached)
    col2.metric("Eliminated by", summary.eliminated_by or "—")
    col3.metric("Record (W-D-L)", f"{summary.record['wins']}-{summary.record['draws']}-{summary.record['losses']}")
    col4.metric("Goal difference", f"{summary.goal_difference:+d}", f"{summary.goals_for} for / {summary.goals_against} against")

    st.divider()

    fixtures = get_fixtures()
    result_color = {"Win": "#22c55e", "Loss": "#ef4444"}
    fixtures_display = fixtures.copy()
    fixtures_display["result_short"] = fixtures_display["result"].apply(
        lambda r: "Win" if r == "Win" else ("Loss" if r == "Loss" else "Draw")
    )

    st.subheader("Match-by-match")
    for _, row in fixtures_display.iterrows():
        icon = {"Win": "✅", "Loss": "❌", "Draw": "🟡"}[row["result_short"]]
        pens = f" — {row['penalties']}" if row["penalties"] else ""
        st.markdown(
            f"{icon} **{row['round']}** ({row['date']}) — Morocco {row['morocco_goals']}-"
            f"{row['opponent_goals']} {row['opponent']}{pens}"
        )

    st.divider()
    st.subheader("Goals for/against by match")
    fig2 = px.bar(
        fixtures, x="opponent", y=["morocco_goals", "opponent_goals"],
        barmode="group", labels={"value": "Goals", "variable": "Team"},
    )
    fig2.update_layout(height=380, margin=dict(t=20, b=20))
    st.plotly_chart(fig2, width="stretch")