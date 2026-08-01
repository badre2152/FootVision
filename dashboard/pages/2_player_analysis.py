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
from dashboard.notes_store import get_note, save_note

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

# ── #20 — Coach/Staff Notes ─────────────────────────────────────────────
# Free-text qualitative judgment (training-ground impressions, character,
# tactical fit) that no scraped data source captures — persisted in
# data/coach_notes.json via dashboard/notes_store.py, deliberately
# outside the Bronze/Silver/Gold pipeline so a `run_gold.py` re-run never
# touches it (see that module's docstring for why).
st.divider()
st.subheader("📝 Coach/Staff Notes")
st.caption(
    "Qualitative observations no data source captures — e.g. \"final ball has dropped "
    "off in training this week.\" Stored separately from the scraped pipeline; survives "
    "a full Bronze → Silver → Gold re-run untouched."
)

existing_note = get_note(row["player_id"])

note_area_col, author_col = st.columns([3, 1])
with author_col:
    author_input = st.text_input(
        "Your name (optional)",
        key="coach_note_author",
        placeholder="e.g. Coach Regragui",
        help="Single-user for now — becomes a real identity once spec #6's authentication lands.",
    )
with note_area_col:
    note_text = st.text_area(
        f"Note for {row['player_name']}",
        value=(existing_note["text"] if existing_note else ""),
        key=f"coach_note_text_{selected_id}",
        height=120,
        placeholder="e.g. Final ball has dropped off in training this week; still a strong dressing-room voice.",
    )

if st.button("💾 Save note", key=f"coach_note_save_{selected_id}"):
    existing_note = save_note(row["player_id"], note_text, author=author_input or None)
    st.success("Note saved.")

if existing_note and existing_note.get("text"):
    st.caption(f"🕒 Last edited by **{existing_note['author']}** at {existing_note['updated_at']} UTC")
elif existing_note is None:
    st.caption("No note yet for this player.")