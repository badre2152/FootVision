# dashboard/pages/01_player_analysis.py
# FootVision — Player Analysis page

import sys
from datetime import datetime
from pathlib import Path

import streamlit as st
import plotly.graph_objects as go

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dashboard.data_loader import load_latest_players_scored, available_component_columns
from dashboard.sidebar import render_sidebar, apply_sidebar_filters
from dashboard.auth import require_auth
from dashboard.notes_store import get_note, save_note, NoteConflictError
from dashboard.report_utils import csv_download_button, build_player_profile_pdf
from analytics.scouting.rival_benchmark import compare_to_reference

st.set_page_config(page_title="FootVision — Player Analysis", page_icon="📊", layout="wide")
require_auth()
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
    col5.metric("Player DNA", row["PlayerDNA"])

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

# ── vs. International Reference ──────────────────────────────────────────
st.subheader(f"🌍 {row['player_name']} vs. International Reference")
st.caption(
    "Contextual benchmark only — reference player data never feeds DSSScore or any "
    "sub-score. Raw per-90 stats, not the composite scores above (not directly "
    "comparable across pools) — see seeds/rival_reference_players.csv for who's "
    "included and why."
)

benchmark = compare_to_reference(row, row["position"])

if benchmark["warning"]:
    st.info(f"ℹ️ {benchmark['warning']}")
elif not benchmark["references"]:
    st.info("No curated international reference players available for this position yet.")
else:
    bench_left, bench_right = st.columns([2, 1])
    categories = benchmark["categories"]

    with bench_left:
        fig_bench = go.Figure()
        player_values = [benchmark["player_values_scaled"][c] or 0.0 for c in categories]
        fig_bench.add_trace(go.Scatterpolar(
            r=player_values + [player_values[0]],
            theta=categories + [categories[0]],
            fill="toself",
            name=benchmark["player_name"],
        ))
        for ref in benchmark["references"]:
            ref_values = [ref["values_scaled"][c] or 0.0 for c in categories]
            ref_label = f"{ref['name']} ({ref['nation']})" + ("" if ref["stats_verified"] else " ⚠️")
            fig_bench.add_trace(go.Scatterpolar(
                r=ref_values + [ref_values[0]],
                theta=categories + [categories[0]],
                fill="toself",
                name=ref_label,
                opacity=0.6 if ref["stats_verified"] else 0.35,
            ))
        fig_bench.update_layout(
            polar=dict(radialaxis=dict(visible=True, range=[0, 1])),
            showlegend=True,
            height=450,
            margin=dict(t=30, b=30),
        )
        st.plotly_chart(fig_bench, width='stretch')

    with bench_right:
        st.markdown("**Reference players**")
        for ref in benchmark["references"]:
            verified_tag = "" if ref["stats_verified"] else " ⚠️ *stats not yet verified*"
            st.markdown(f"- **{ref['name']}** ({ref['club']}, {ref['nation']}){verified_tag}")
            st.caption(f"{ref['benchmark_reason']} — {ref['season']} season.")
        if any(not ref["stats_verified"] for ref in benchmark["references"]):
            st.warning(
                "⚠️ One or more reference players are curated (identity/position confirmed) "
                "but their per-90 stat VALUES have not yet been fetched/verified from a real "
                "source — shown faded above. See seeds/rival_reference_players.csv."
            )

st.divider()

# ── Comparison scatter: this player vs the rest of their position group ──
st.subheader(f"How does {row['player_name']} compare to other {row['position']}s?")
same_position = players[players["position"] == row["position"]].sort_values("DSSScore", ascending=False)
st.bar_chart(same_position.set_index("player_name")["DSSScore"])

# ── Coach/Staff Notes ─────────────────────────────────────────────────────
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
    loaded_version = existing_note["version"] if existing_note else 0
    try:
        existing_note = save_note(
            row["player_id"], note_text, author=author_input or None,
            expected_version=loaded_version,
        )
        st.success("Note saved.")
    except NoteConflictError as e:
        st.error(
            f"⚠️ Someone else saved a note for {row['player_name']} while you were "
            f"editing — your version was **not** saved, to avoid silently "
            f"overwriting theirs."
        )
        st.info(
            f"Their note (by **{e.current['author']}** at {e.current['updated_at']} UTC):\n\n"
            f"> {e.current['text'] or '*(empty)*'}"
        )
        st.caption("Copy anything you still need from your draft above, then save again to overwrite intentionally.")
        existing_note = e.current

if existing_note and existing_note.get("text"):
    st.caption(f"🕒 Last edited by **{existing_note['author']}** at {existing_note['updated_at']} UTC")
elif existing_note is None:
    st.caption("No note yet for this player.")

st.divider()
pdf_bytes = build_player_profile_pdf(
    row, component_cols,
    note_text=(existing_note.get("text") if existing_note else None),
)
safe_name = "".join(c if c.isalnum() else "_" for c in str(row["player_name"]))
st.download_button(
    "📄 Export Player PDF Profile",
    data=pdf_bytes,
    file_name=f"player_profile_{safe_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
    mime="application/pdf",
    key="pdf_player_profile",
)

csv_download_button(
    players.sort_values("DSSScore", ascending=False),
    filename_prefix="player_analysis_candidates", key="csv_player_analysis",
    label="⬇️ Download current candidate list (CSV)",
)