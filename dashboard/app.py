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
from dashboard.auth import require_auth
from dashboard.i18n import t
from dashboard.report_utils import csv_download_button

st.set_page_config(
    page_title="FootVision — Executive Dashboard",
    page_icon="⚽",
    layout="wide",
)
require_auth()

render_sidebar()

st.title(t("nav.app_title"))
st.caption(t("app.caption"))

top26   = load_latest_top26()
players = load_latest_players_scored()
summary = load_latest_squad_summary()

if top26 is None or players is None or summary is None:
    st.error(t("app.no_gold_error"))
    st.stop()

st.caption(t(
    "app.source_caption",
    source=summary.get('_source_file', 'unknown'),
    generated_at=summary.get('generated_at', 'unknown'),
))

col1, col2, col3, col4 = st.columns(4)
col1.metric(t("app.kpi_candidates"), summary.get("total_candidates", len(players)))
col2.metric(t("app.kpi_squad_size"), summary.get("squad_size", len(top26)))
top_player = summary.get("top_player", {})
col3.metric(t("app.kpi_top_player"), top_player.get("name", "—"), f"DSS={top_player.get('score', 0):.4f}")

score_range = summary.get("score_range")
if score_range:
    col4.metric(t("app.kpi_dss_range"), f"{score_range['min']:.3f} – {score_range['max']:.3f}")
else:
    col4.metric(t("app.kpi_dss_range"), f"{players['DSSScore'].min():.3f} – {players['DSSScore'].max():.3f}")

st.divider()

left, right = st.columns([1, 2])

with left:
    st.subheader(t("app.position_breakdown"))
    position_counts = summary.get("position_counts")
    if position_counts is None:
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
        st.warning(t("app.position_mismatch_warning", mismatches="; ".join(mismatches)))

with right:
    st.subheader(t("app.flags_header"))
    flags = summary.get("flags", {})
    if flags.get("high_injury_risk"):
        st.markdown(f"**{t('app.flag_injury', n=len(flags['high_injury_risk']))}** " + ", ".join(flags["high_injury_risk"]))
    if flags.get("limited_data"):
        st.markdown(f"**{t('app.flag_limited_data', n=len(flags['limited_data']))}** " + ", ".join(flags["limited_data"]))
    if flags.get("intl_caps_possibly_missing"):
        st.markdown(f"**{t('app.flag_intl_caps', n=len(flags['intl_caps_possibly_missing']))}** " + ", ".join(flags["intl_caps_possibly_missing"]))
    if not any(flags.values()):
        st.markdown(t("app.no_flags"))

    pct_summary = summary.get("percentile_summary")
    if pct_summary:
        st.subheader(t("app.score_spread_header"))
        st.markdown(t(
            "app.score_spread_text",
            gap_raw=f"{pct_summary['elite_fringe_gap_original']:.3f}",
            gap_pct=f"{pct_summary['elite_fringe_gap_percentile']:.3f}",
            factor=f"{pct_summary['expansion_factor']:.2f}",
        ))

st.divider()

st.subheader(t("app.squad_table_header", n=len(top26)))
top26_filtered = apply_sidebar_filters(top26)
display_cols = [c for c in [
    "Rank", "player_name", "position", "club", "league",
    "DSSScore", "DSSScore_pct", "PerformanceScore", "FormScore",
    "MentalScore", "AvailabilityScore", "InternationalScore",
    "ChemistryScore", "PressureIndex", "FlexibilityScore", "SentimentScore",
] if c in top26_filtered.columns]

if top26_filtered.empty:
    st.info(t("app.no_match_filters"))
else:
    st.dataframe(
        top26_filtered[display_cols].style.background_gradient(subset=["DSSScore"], cmap="RdYlGn"),
        width='stretch',
        hide_index=True,
    )
    csv_download_button(top26_filtered[display_cols], filename_prefix="squad_overview", key="csv_app_overview")

missing_new_cols = [c for c in ["ChemistryScore", "PressureIndex", "FlexibilityScore", "SentimentScore", "DSSScore_pct"] if c not in top26.columns]
if missing_new_cols:
    st.caption(t("app.missing_cols_caption", cols=", ".join(missing_new_cols)))

st.caption(t("app.footer_hint"))