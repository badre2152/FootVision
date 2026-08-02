# dashboard/pages/6_tactical_board.py
# Football Atlas — Tactical Board
#
# Redesigned 2026-08-02 (premium HTML/CSS board, see
# dashboard/pitch_renderer_html.py). XI selection, formation slot
# assignment, and data loading are UNCHANGED from the original
# 2026-07-30 version — only how the result is drawn changed, from a
# static matplotlib image (dashboard/pitch_renderer.py::render_pitch(),
# still intact and still covered by tests/test_pitch_renderer.py) to a
# self-contained HTML/CSS component with real hover states and an
# entrance animation, which a static image cannot do at all.
#
# Two sources for the XI:
#   - "Best XI by DSSScore" — top-N per position from the current top26,
#     for the formation's slot counts.
#   - A specific rotation_strategy.py match recommendation (if
#     gold/rotation_strategy_*.json exists) — see run_rotation_strategy.py.
#
# Player photos are optional — see dashboard/pitch_renderer.py's header for
# the exact convention (dashboard/assets/players/{player_id}.jpg). No
# photos yet? Every marker falls back to a stylish gradient+initials badge
# instead of a plain colored circle, the page still works fully.

import sys
import glob
import json
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dashboard.data_loader import load_latest_top26
from dashboard.sidebar import render_sidebar
from dashboard.auth import require_auth
from dashboard.report_utils import csv_download_button
from dashboard.pitch_renderer import FORMATIONS, assign_xi_to_formation, PLAYER_PHOTOS_DIR
from dashboard.pitch_renderer_html import render_pitch_html, render_setup_panel_css

st.set_page_config(page_title="Football Atlas — Tactical Board", page_icon="🧭", layout="wide")
require_auth()  # spec #6 — must run before render_sidebar()/any data load below
render_sidebar()
st.markdown(render_setup_panel_css(), unsafe_allow_html=True)  # page-scoped skin — see pitch_renderer_html.py docstring
st.title("🧭 Tactical Board")

top26 = load_latest_top26()
if top26 is None:
    st.warning("No `gold/top26_*.csv` found. Run `python run_gold.py --top-n 26` first.")
    st.stop()

n_photos = len(list(PLAYER_PHOTOS_DIR.glob("*.*"))) if PLAYER_PHOTOS_DIR.exists() else 0
st.caption(
    f"🖼️ {n_photos} player photo(s) found in `dashboard/assets/players/` — "
    f"players without one show a stylish initials badge instead."
    if n_photos else
    "🖼️ No player photos found in `dashboard/assets/players/` yet — showing "
    "initials badges for everyone. Drop `{player_id}.jpg` files there to add photos."
)

col_left, col_right = st.columns([1, 2])

with col_left:
    with st.container(border=True):
        st.markdown('<p class="tb-eyebrow">Setup</p>', unsafe_allow_html=True)
        source = st.radio("XI source", ["Best XI by DSSScore", "Rotation Strategy match"], index=0)
        formation_name = st.selectbox("Formation", list(FORMATIONS.keys()), index=0)
        show_photos = st.checkbox("Show player photos", value=True)

    slot_counts: dict[str, int] = {}
    for s in FORMATIONS[formation_name]:
        slot_counts[s["position"]] = slot_counts.get(s["position"], 0) + 1

    xi_df = None
    match_label = formation_name

    if source == "Best XI by DSSScore":
        picks = []
        for pos, n in slot_counts.items():
            pool = top26[top26["position"] == pos].sort_values("DSSScore", ascending=False)
            if len(pool) < n:
                st.error(f"Only {len(pool)} {pos} in top26, need {n} for {formation_name} — pick a different formation.")
                st.stop()
            picks.append(pool.head(n))
        xi_df = pd.concat(picks, ignore_index=True)
        match_label = f"Best XI by DSSScore — {formation_name}"

    else:
        rotation_files = sorted(glob.glob("gold/rotation_strategy_*.json"))
        if not rotation_files:
            st.warning(
                "No `gold/rotation_strategy_*.json` found. Run:\n\n"
                "```\npython run_rotation_strategy.py\n```"
            )
            st.stop()
        with open(rotation_files[-1], encoding="utf-8") as f:
            plan = json.load(f)
        match_options = {f"Match {m['match_number']} vs {m['opponent']}": m for m in plan["matches"]}
        chosen_label = st.selectbox("Match", list(match_options.keys()))
        chosen_match = match_options[chosen_label]
        names = chosen_match["recommended_xi"]
        xi_df = top26[top26["player_name"].isin(names)].copy()
        # preserve the recommended order isn't required for slot assignment
        # (assign_xi_to_formation groups by position), but formation slot
        # counts MUST match this match's actual formation — the plan was
        # built with REAL_FORMATION, so force that here regardless of the
        # formation picker above, and say so.
        if formation_name != "Real 2026 (1-4-5-1)":
            st.info("Rotation Strategy plans use the real 1-4-5-1 shape — formation picker ignored for this source.")
            formation_name = "Real 2026 (1-4-5-1)"
        match_label = f"{chosen_label} ({chosen_match['difficulty']})"

        if len(xi_df) != 11:
            st.error(
                f"Only matched {len(xi_df)}/11 recommended players against the current "
                f"top26 (squad composition may have changed since the plan was generated) "
                f"— re-run run_rotation_strategy.py against the latest top26."
            )
            st.stop()

with col_right:
    try:
        assigned = assign_xi_to_formation(xi_df, formation_name=formation_name)

        # Display-only enrichment (DSSScore + club for the hover card) —
        # purely cosmetic, joined AFTER assign_xi_to_formation() has
        # already done the real slot-assignment work, so the formation
        # logic itself never sees or needs these extra columns.
        extra_by_id = {}
        if "player_id" in xi_df.columns:
            for _, row in xi_df.iterrows():
                extra_by_id[row.get("player_id")] = {
                    "dss_score": row.get("DSSScore"),
                    "club": row.get("club"),
                }
        for p in assigned:
            extra = extra_by_id.get(p.get("player_id"), {})
            p["dss_score"] = extra.get("dss_score")
            p["club"] = extra.get("club")

        board_html, board_height_fallback = render_pitch_html(
            assigned, title=match_label, subtitle=formation_name, show_photos=show_photos,
        )
        # st.components.v1.html is deprecated (Streamlit removed it after
        # 2026-06-01) — st.iframe is the current replacement and, for raw
        # HTML strings, auto-sizes height="content" instead of needing a
        # guessed pixel value. Falls back to components.html only for an
        # older Streamlit install that predates st.iframe.
        if hasattr(st, "iframe"):
            st.iframe(board_html, height="content", width="stretch")
        else:
            components.html(board_html, height=board_height_fallback, scrolling=False)
    except ValueError as e:
        st.error(str(e))

st.divider()
st.subheader("XI details")
show_cols = [c for c in ["player_name", "position", "DSSScore", "AvailabilityScore", "PlayerDNA"] if c in xi_df.columns]
xi_display = xi_df[show_cols].sort_values("position")
st.dataframe(xi_display, width='stretch', hide_index=True)
csv_download_button(xi_display, filename_prefix="tactical_board_xi", key="csv_tactical_board")