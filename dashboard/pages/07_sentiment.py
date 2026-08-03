# dashboard/pages/07_sentiment.py
# Football Atlas — Sentiment Dashboard page
#
# Added 2026-07-30. SentimentScore itself was already computed in Gold
# (analytics/scoring/sentiment_score.py, from real Reddit/YouTube text —
# see sentiment_text_count below) but never had its own dashboard page;
# it only showed up folded into the Player Analysis radar chart alongside
# every other sub-score. This page gives it a dedicated view, since public
# sentiment is qualitatively different from the performance-stat
# sub-scores (it's about perception, not measured output) and benefits
# from its own context rather than being one more radar axis.
#
# Per analytics/scoring/sentiment_score.py's own header: the NLP pipeline
# behind this was flagged as "not tested end-to-end" — treat SentimentScore
# here as an exploratory public-perception signal, not a validated
# measurement. This page does not change that caveat, just surfaces it.

import sys
from pathlib import Path

import streamlit as st
import plotly.express as px

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dashboard.data_loader import load_latest_players_scored
from dashboard.sidebar import render_sidebar, apply_sidebar_filters
from dashboard.auth import require_auth
from dashboard.report_utils import csv_download_button

st.set_page_config(page_title="Football Atlas — Sentiment", page_icon="💬", layout="wide")
require_auth()  # spec #6 — must run before render_sidebar()/any data load below
render_sidebar()
st.title("💬 Public Sentiment")
st.caption(
    "Reddit/YouTube-derived sentiment — exploratory signal, not a validated NLP model. "
    "See analytics/scoring/sentiment_score.py."
)

players = load_latest_players_scored()
if players is None:
    st.error("No Gold output found. Run `run_gold.py` first.")
    st.stop()

if "SentimentScore" not in players.columns:
    st.error(
        "This players_scored_*.csv has no SentimentScore column — it was produced "
        "before the 2026-07-22 sentiment update, or the sentiment pipeline didn't run. "
        "Re-run run_gold.py with sentiment_score.py included."
    )
    st.stop()

players = apply_sidebar_filters(players)
if players.empty:
    st.info("No players match the current sidebar filters — adjust them and try again.")
    st.stop()

has_text_count = "sentiment_text_count" in players.columns

# ── Summary ───────────────────────────────────────────────────────────────
col1, col2, col3 = st.columns(3)
col1.metric("Mean SentimentScore", f"{players['SentimentScore'].mean():.3f}")
col2.metric("Highest", players.loc[players["SentimentScore"].idxmax(), "player_name"])
col3.metric("Lowest", players.loc[players["SentimentScore"].idxmin(), "player_name"])

if has_text_count:
    n_low_volume = int((players["sentiment_text_count"] < 5).sum())
    if n_low_volume:
        st.caption(
            f"ℹ️ {n_low_volume} player(s) have fewer than 5 source texts — their "
            f"SentimentScore is based on a small sample, treat with extra caution "
            f"(see 'Source volume' column below)."
        )

st.divider()

# ── Ranked bar chart ──────────────────────────────────────────────────────
left, right = st.columns([3, 2])

with left:
    st.subheader("SentimentScore — all players")
    ranked = players.sort_values("SentimentScore", ascending=True)
    fig = px.bar(
        ranked, x="SentimentScore", y="player_name", orientation="h",
        color="position",
        height=max(500, 18 * len(ranked)),
    )
    fig.update_layout(margin=dict(t=20, b=20), yaxis_title="")
    st.plotly_chart(fig, width="stretch")

with right:
    st.subheader("Sentiment vs on-pitch score")
    fig2 = px.scatter(
        players, x="SentimentScore", y="DSSScore",
        color="position", hover_name="player_name",
        height=max(500, 18 * len(players) // 3),
    )
    fig2.update_layout(margin=dict(t=20, b=20))
    st.plotly_chart(fig2, width="stretch")
    st.caption(
        "Top-left (high public sentiment, lower DSSScore) and bottom-right (low sentiment, "
        "high DSSScore) are the interesting mismatches — public perception and measured "
        "performance don't always agree, and neither is automatically 'right'."
    )

st.divider()

# ── Table ─────────────────────────────────────────────────────────────────
st.subheader("Full breakdown")
display_cols = ["player_name", "position", "club", "SentimentScore", "DSSScore"]
if has_text_count:
    display_cols.insert(3, "sentiment_text_count")

table = players[display_cols].rename(
    columns={"sentiment_text_count": "Source volume"}
).sort_values("SentimentScore", ascending=False).reset_index(drop=True)
st.dataframe(
    table.style.format({"SentimentScore": "{:.3f}", "DSSScore": "{:.4f}"}),
    width="stretch", height=450,
)
csv_download_button(table, filename_prefix="sentiment_breakdown", key="csv_sentiment")