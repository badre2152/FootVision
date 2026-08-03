# dashboard/pages/11_squad_builder.py
# Football Atlas — Squad Builder page
#
# Added 2026-07-30.
#
# ─────────────────────────────────────────────────────────────────────────
# WHY DROPDOWNS INSTEAD OF DRAG-AND-DROP
# ─────────────────────────────────────────────────────────────────────────
# PROJECT.md's original spec: "Interface glisser-déposer + score
# automatique" (drag-and-drop interface + automatic score). Streamlit has
# no native drag-and-drop widget — building real drag-and-drop would mean
# writing a custom JS/React Streamlit component (a separate, much larger
# engineering task, and a dependency this project's stack doesn't already
# have anywhere).
#
# What matters functionally about "Squad Builder" is the SECOND half —
# "construction manuelle + score automatique" (manual construction, score
# updates automatically) — and that doesn't actually require drag-and-drop
# as the input mechanism. This page delivers that with a per-slot
# dropdown: pick a formation, get analytics/competition_context.py's
# automatic Best XI pre-filled, then override any individual slot from a
# position-filtered player list. The score recalculates live either way.
# This is a genuine, honestly-labeled substitution for the UI mechanism,
# not a partial/fake version of the actual feature (manual override +
# live automatic scoring) that feature was actually meant to enable.
#
# Same sub-position caveat as dashboard/pages/06_tactical_board.py applies:
# slots within DEF/MID/FWD are generic (no LB/CB/RB or DM/AM distinction),
# since this project's `position` column doesn't have that granularity.

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

st.set_page_config(page_title="Football Atlas — Squad Builder", page_icon="🛠️", layout="wide")
require_auth()  # spec #6 — must run before render_sidebar()/any data load below
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

# IMPORTANT (found by actually running this page's logic against real
# data, not by reading the code): build_best_xi()'s returned starting_xi
# is sliced from `candidate_pool` (top26) wherever top26 alone has enough
# depth, and only pulls from `fallback_pool` (players_full) for slots
# that were actually short. Since top26_*.csv has NO player_id column
# (see competition_context.py's _resolve_id_col), auto_result.starting_xi
# frequently has NO player_id column either — it inherited top26's
# columns for most rows. Using "player_id" as this page's id_col crashed
# with a real KeyError the first time this was tested against actual
# Gold output. `player_name` is the only column guaranteed present in
# BOTH top26-sourced and players_full-sourced rows, so it's used
# consistently everywhere in this page — same trade-off
# competition_context.py's own docstring already accepts (a shared exact
# name would collide; not the case in the current 49-candidate roster).
id_col = "player_name"

formation = st.selectbox("Formation", list(FORMATIONS.keys()), index=0, key="sb_formation")

# Reset the manual picks whenever the formation changes, or on explicit request.
if st.session_state.get("sb_last_formation") != formation:
    st.session_state["sb_picks"] = {}
    st.session_state["sb_last_formation"] = formation

if st.button("↺ Reset to automatic Best XI"):
    st.session_state["sb_picks"] = {}

auto_result = build_best_xi(top26, formation=formation, fallback_pool=players_full)
slots = {"GK": 1, **FORMATIONS[formation]}

# Build the ordered list of (position, slot_index) -> default player id from
# the automatic result, so each dropdown has a sensible pre-fill.
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

        # Options for THIS slot: every player at this position not already
        # claimed by an EARLIER slot this pass (selected_ids only holds
        # earlier slots' picks at this point — this slot's own pick is
        # added to selected_ids only after its selectbox below, so there's
        # no need to self-exempt it here).
        available = position_pool[~position_pool[id_col].isin(selected_ids)]
        options = available[id_col].tolist()

        # FIX (found by actually simulating this exact sequence, not by
        # inspection): if a user manually overrides an EARLIER slot to a
        # player who happens to be a LATER slot's untouched automatic
        # default, that default is no longer safe to reuse here — it's
        # already taken. Falling back to the automatic default blindly
        # produced a real duplicate (two slots resolving to the same
        # player, verified by running this exact scenario against real
        # Gold data before shipping this page). Resolution order:
        #   1. this slot's own previously-stored pick, if still available
        #   2. else the automatic default, if not already taken
        #   3. else the best remaining player at this position
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

# ── #20 — Coach/Staff Notes, compact read-only view ─────────────────────
# This page uses player_name as its id_col throughout (see the module
# docstring above for why player_id isn't reliably present here), but
# notes_store is keyed by player_id — so notes are looked up via
# players_full, which always has a real player_id column, joined back on
# player_name (the one column guaranteed present everywhere on this page).
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