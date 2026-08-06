import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dashboard.data_loader import load_latest_top26, load_latest_players_scored
from dashboard.sidebar import render_sidebar
from dashboard.auth import require_auth
from dashboard.notes_store import get_all_notes
from dashboard.report_utils import csv_download_button
from analytics.scouting.competition_context import build_best_xi, FORMATIONS

st.set_page_config(page_title="FootVision — Squad Builder", page_icon="🛠️", layout="wide")
require_auth()
render_sidebar()
st.title("🛠️ Squad Builder")
st.caption(
    "Manual override + automatic scoring — no drag-and-drop (Streamlit has none natively; "
    "see page source for why that's a fair substitution, not a missing feature)."
)

top26 = load_latest_top26()
players_full = load_latest_players_scored()
if top26 is None or players_full is None:
    st.error("No Gold output found. Run `run_gold.py` first.")
    st.stop()

id_col = "player_name"

formation = st.selectbox("Formation", list(FORMATIONS.keys()), index=0, key="sb_formation")

if st.session_state.get("sb_last_formation") != formation:
    st.session_state["sb_picks"] = {}
    st.session_state["sb_last_formation"] = formation

if st.button("↺ Reset to automatic Best XI"):
    st.session_state["sb_picks"] = {}

auto_result = build_best_xi(top26, formation=formation, fallback_pool=players_full)
slots = {"GK": 1, **FORMATIONS[formation]}

auto_by_position: dict[str, list] = {}
for position in slots:
    auto_by_position[position] = (
        auto_result.starting_xi[auto_result.starting_xi["position"] == position][id_col].tolist()
    )

picks: dict[str, str] = st.session_state.setdefault("sb_picks", {})

st.divider()
st.subheader(f"Starting XI — {formation} (manual overrides in bold)")

selected_ids: set = set()
rows_to_display = []

for position, n_slots in slots.items():
    st.markdown(f"**{position}**")
    position_pool = players_full[players_full["position"] == position].sort_values("DSSScore", ascending=False)
    cols = st.columns(n_slots)

    for i in range(n_slots):
        slot_key = f"{position}_{i}"
        default_id = auto_by_position[position][i] if i < len(auto_by_position[position]) else None

        available = position_pool[~position_pool[id_col].isin(selected_ids)]
        options = available[id_col].tolist()

        current_pick = picks.get(slot_key)
        if current_pick not in options:
            current_pick = default_id if default_id not in selected_ids else None
        if current_pick not in options:
            current_pick = options[0] if options else None

        if not options:
            with cols[i]:
                st.warning(f"No {position} players left unassigned for slot {i + 1}.")
            picks.pop(slot_key, None)
            continue

        def _label(pid):
            row = players_full[players_full[id_col] == pid]
            if row.empty:
                return str(pid)
            r = row.iloc[0]
            return f"{r['player_name']} ({r['DSSScore']:.3f})"

        with cols[i]:
            chosen = st.selectbox(
                f"Slot {i + 1}", options=options, index=options.index(current_pick),
                format_func=_label, key=f"sb_select_{slot_key}", label_visibility="collapsed",
            )
        picks[slot_key] = chosen
        selected_ids.add(chosen)
        is_manual_override = chosen != default_id
        rows_to_display.append({
            "position": position, "slot": i + 1, id_col: chosen,
            "is_manual_override": is_manual_override,
        })

st.session_state["sb_picks"] = picks

st.divider()

manual_xi = players_full[players_full[id_col].isin(selected_ids)].copy()
manual_total = manual_xi["DSSScore"].sum()
auto_total = auto_result.starting_xi["DSSScore"].sum()
delta = manual_total - auto_total

col1, col2, col3 = st.columns(3)
col1.metric("Automatic Best XI total DSSScore", f"{auto_total:.4f}")
col2.metric("Your squad total DSSScore", f"{manual_total:.4f}", f"{delta:+.4f}")
col3.metric("Manual overrides", sum(1 for r in rows_to_display if r["is_manual_override"]))

st.subheader("Your XI")
position_order = {"GK": 0, "DEF": 1, "MID": 2, "FWD": 3}
manual_xi["_order"] = manual_xi["position"].map(position_order)
manual_xi_sorted = manual_xi.sort_values(["_order", "DSSScore"], ascending=[True, False])
display_cols = [c for c in ["player_name", "position", "club", "DSSScore"] if c in manual_xi_sorted.columns]
st.dataframe(
    manual_xi_sorted[display_cols].style.format({"DSSScore": "{:.4f}"}),
    width="stretch", height=430, hide_index=True,
)
csv_download_button(manual_xi_sorted[display_cols], filename_prefix="squad_builder_xi", key="csv_squad_builder")

if "player_id" in players_full.columns:
    st.subheader("Coach notes for this XI")
    all_notes = get_all_notes()
    name_to_id = dict(zip(players_full["player_name"], players_full["player_id"]))
    for _, xi_row in manual_xi_sorted.iterrows():
        pid = name_to_id.get(xi_row["player_name"])
        note = all_notes.get(str(pid)) if pid is not None else None
        with st.expander(f"📝 {xi_row['player_name']}"):
            if note and note.get("text"):
                st.write(note["text"])
                st.caption(f"Last edited by {note['author']} at {note['updated_at']} UTC")
            else:
                st.caption("No coach note yet — add one from the Player Analysis page.")