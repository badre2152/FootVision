# dashboard/pages/4_final_squad.py
# Football Atlas — Final Squad page
#
# The 26 selected players, grouped by position, with a short data-driven
# justification per player built from their strongest sub-scores.

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dashboard.data_loader import load_latest_top26, available_component_columns
from dashboard.sidebar import render_sidebar, apply_sidebar_filters

st.set_page_config(page_title="Football Atlas — Final Squad", page_icon="🏆", layout="wide")
render_sidebar()
st.title("🏆 Final Squad — 26 Players")

top26_full = load_latest_top26()
if top26_full is None:
    st.error("No Gold output found. Run `run_gold.py` first.")
    st.stop()

top26 = apply_sidebar_filters(top26_full)
if top26.empty:
    st.info("No players match the current sidebar filters — adjust them and try again.")
    st.stop()

component_cols = available_component_columns(top26)


def _justification(row) -> str:
    """Picks the player's single strongest sub-score as a one-line justification."""
    if not component_cols:
        return f"DSSScore {row['DSSScore']:.3f}"
    best_col = max(component_cols, key=lambda c: row[c])
    label_map = {
        "PerformanceScore": "on-pitch output", "FormScore": "recent form",
        "MentalScore": "mental profile", "AvailabilityScore": "fitness/availability",
        "InternationalScore": "international experience", "ChemistryScore": "squad chemistry",
        "PressureIndex": "big-match performance", "FlexibilityScore": "positional versatility",
    }
    return f"Strongest in **{label_map.get(best_col, best_col)}** ({row[best_col]:.2f})"


POSITION_ORDER = ["GK", "DEF", "MID", "FWD"]
POSITION_LABELS = {"GK": "🧤 Goalkeepers", "DEF": "🛡️ Defenders", "MID": "⚙️ Midfielders", "FWD": "⚔️ Forwards"}

for pos in POSITION_ORDER:
    group = top26[top26["position"] == pos].sort_values("DSSScore", ascending=False)
    if group.empty:
        continue
    st.subheader(f"{POSITION_LABELS[pos]} ({len(group)})")
    cols = st.columns(min(len(group), 4))
    for i, (_, row) in enumerate(group.iterrows()):
        with cols[i % len(cols)]:
            st.markdown(f"**#{int(row['Rank'])} {row['player_name']}**")
            st.caption(row.get("club", ""))
            st.progress(min(max(row["DSSScore"], 0.0), 1.0), text=f"DSS {row['DSSScore']:.3f}")
            st.caption(_justification(row))
    st.divider()

expected_caps = {"GK": 3, "DEF": 9, "MID": 9, "FWD": 5}
actual_counts = top26_full["position"].value_counts().to_dict()
mismatches = [f"{p}: {actual_counts.get(p,0)} (expected {c})" for p, c in expected_caps.items() if actual_counts.get(p,0) != c]
if mismatches:
    st.warning("Position composition doesn't match the standard 3/9/9/5 cap: " + "; ".join(mismatches))
else:
    st.success("Squad composition matches the standard cap: 3 GK / 9 DEF / 9 MID / 5 FWD.")