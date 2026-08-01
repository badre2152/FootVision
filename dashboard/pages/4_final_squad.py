# dashboard/pages/4_final_squad.py
# Football Atlas — Final Squad page
#
# The 26 selected players, grouped by position, with a short data-driven
# justification per player built from their strongest sub-scores.
#
# Localized (issue #15) — fully translated, this is the most
# decision-critical page for Coach Ouahbi's staff. All static text goes
# through dashboard/i18n.py's t() helper so it follows the sidebar
# language switcher (EN/FR/AR).

import sys
from pathlib import Path

import streamlit as st
import pandas as pd
import plotly.express as px

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import config
from dashboard.data_loader import load_latest_top26, load_latest_players_scored, available_component_columns
from dashboard.sidebar import render_sidebar, apply_sidebar_filters
from dashboard.i18n import t
from dashboard.notes_store import get_all_notes
from analytics.ml.player_clustering import ARCHETYPE_EMOJI  # NEW 2026-07-29
from analytics.squad_balance import compute_age_distribution, TIER_ORDER, UNKNOWN_TIER  # #16

st.set_page_config(page_title="Football Atlas — Final Squad", page_icon="🏆", layout="wide")
render_sidebar()
st.title(t("nav.final_squad_title"))

top26_full = load_latest_top26()
if top26_full is None:
    st.error(t("final_squad.no_gold_error"))
    st.stop()

top26 = apply_sidebar_filters(top26_full)
if top26.empty:
    st.info(t("final_squad.no_match_filters"))
    st.stop()

component_cols = available_component_columns(top26)

# #20 — Coach/Staff Notes, compact read-only view. Loaded ONCE for all 26
# players (not once per row) since notes_store.get_all_notes() reads the
# whole JSON file — see that module's docstring for why this is decoupled
# from the Bronze/Silver/Gold pipeline entirely (top26/players_scored
# have nothing to do with where notes live).
all_notes = get_all_notes()

if "PlayerDNA" in top26.columns and (top26["PlayerDNA"] != "N/A").any():
    st.caption(t("final_squad.dna_caption"))


def _justification(row) -> str:
    """Picks the player's single strongest sub-score as a one-line justification."""
    if not component_cols:
        return t("final_squad.justification_default", score=f"{row['DSSScore']:.3f}")
    best_col = max(component_cols, key=lambda c: row[c])
    label_map = {
        "PerformanceScore": t("final_squad.label_performance"),
        "FormScore": t("final_squad.label_form"),
        "MentalScore": t("final_squad.label_mental"),
        "AvailabilityScore": t("final_squad.label_availability"),
        "InternationalScore": t("final_squad.label_international"),
        "ChemistryScore": t("final_squad.label_chemistry"),
        "PressureIndex": t("final_squad.label_pressure"),
        "FlexibilityScore": t("final_squad.label_flexibility"),
    }
    return t("final_squad.justification", label=label_map.get(best_col, best_col), score=f"{row[best_col]:.2f}")


POSITION_ORDER = ["GK", "DEF", "MID", "FWD"]
POSITION_LABELS = {
    "GK": t("final_squad.position_gk"),
    "DEF": t("final_squad.position_def"),
    "MID": t("final_squad.position_mid"),
    "FWD": t("final_squad.position_fwd"),
}

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
            dna = row.get("PlayerDNA")
            if dna and dna != "N/A":
                emoji = ARCHETYPE_EMOJI.get(dna, "🏷️")
                st.markdown(f"{emoji} *{dna}*")
            st.progress(min(max(row["DSSScore"], 0.0), 1.0), text=t("final_squad.dss_progress", score=f"{row['DSSScore']:.3f}"))
            st.caption(_justification(row))

            note = all_notes.get(str(row.get("player_id", "")))
            with st.expander(t("final_squad.note_expander")):
                if note and note.get("text"):
                    st.write(note["text"])
                    st.caption(t("final_squad.note_last_edited", author=note["author"], time=note["updated_at"]))
                else:
                    st.caption(t("final_squad.note_empty"))
    st.divider()

expected_caps = {"GK": 3, "DEF": 9, "MID": 9, "FWD": 5}
actual_counts = top26_full["position"].value_counts().to_dict()
mismatches = [f"{p}: {actual_counts.get(p,0)} (expected {c})" for p, c in expected_caps.items() if actual_counts.get(p,0) != c]
if mismatches:
    st.warning(t("final_squad.mismatch_warning", mismatches="; ".join(mismatches)))
else:
    st.success(t("final_squad.mismatch_success"))

# ─────────────────────────────────────────────────────────────────────────
# #16 — Squad Age/Experience Balance
#
# Uses top26_full (the whole 26-player squad), not the sidebar-filtered
# `top26` — same reasoning as the position-cap check just above: this is a
# whole-squad composition metric, not something that should change shape
# because the coach happened to type a name into the search box.
#
# top26_full doesn't carry wiki_dob/intl_caps itself (Gold's top26_*.csv is
# a lean summary — see dashboard/data_loader.py), so those two columns are
# pulled in from the fuller players_scored_*.csv via a player_id merge.
# ─────────────────────────────────────────────────────────────────────────
st.divider()
st.subheader(t("final_squad.age_balance_header"))
st.caption(t("final_squad.age_balance_caption", as_of=config.DECISION_DATE.isoformat()))

players_full = load_latest_players_scored()
if players_full is None or "wiki_dob" not in players_full.columns:
    st.caption(t("final_squad.age_balance_no_source"))
else:
    age_cols = [c for c in ["player_id", "wiki_dob", "intl_caps"] if c in players_full.columns]
    squad_with_age = top26_full.merge(players_full[age_cols], on="player_id", how="left")

    dist = compute_age_distribution(squad_with_age, as_of=config.DECISION_DATE)

    TIER_LABEL_KEYS = {
        "Young (<23)":       "final_squad.tier_young",
        "Prime (23-29)":     "final_squad.tier_prime",
        "Experienced (30+)": "final_squad.tier_experienced",
        UNKNOWN_TIER:        "final_squad.tier_unknown",
    }
    tier_display = {tier: t(TIER_LABEL_KEYS[tier]) for tier in TIER_ORDER}

    if dist["n_unknown_dob"]:
        st.warning(t(
            "final_squad.age_balance_unknown_warning",
            n=dist["n_unknown_dob"],
            names=", ".join(dist["players_with_unknown_dob"]),
        ))

    # Per-position one-line summary, e.g. "GK: 1 young / 2 prime / 0 experienced"
    for pos in POSITION_ORDER:
        counts = dist["by_position"].get(pos)
        if not counts:
            continue
        n_unknown_pos = counts.get(UNKNOWN_TIER, 0)
        unknown_suffix = (
            t("final_squad.age_balance_position_summary_unknown_suffix", n=n_unknown_pos)
            if n_unknown_pos else ""
        )
        st.markdown(t(
            "final_squad.age_balance_position_summary",
            pos=POSITION_LABELS[pos],
            young=counts.get("Young (<23)", 0),
            prime=counts.get("Prime (23-29)", 0),
            experienced=counts.get("Experienced (30+)", 0),
            unknown_suffix=unknown_suffix,
        ))

    # Stacked bar chart — tier distribution by position.
    chart_rows = [
        {"position": pos, "tier": tier_display[tier], "count": count, "_tier_order": TIER_ORDER.index(tier)}
        for pos, counts in dist["by_position"].items()
        for tier, count in counts.items()
        if count > 0
    ]
    if chart_rows:
        chart_df = pd.DataFrame(chart_rows).sort_values("_tier_order")
        fig = px.bar(
            chart_df, x="position", y="count", color="tier",
            category_orders={"position": POSITION_ORDER, "tier": [tier_display[t_] for t_ in TIER_ORDER]},
            color_discrete_map={
                tier_display["Young (<23)"]:       "#8ecae6",
                tier_display["Prime (23-29)"]:     "#2a9d8f",
                tier_display["Experienced (30+)"]: "#e76f51",
                tier_display[UNKNOWN_TIER]:        "#adb5bd",
            },
            title=t("final_squad.age_balance_chart_title"),
            labels={
                "position": t("final_squad.age_balance_chart_xaxis"),
                "count": t("final_squad.age_balance_chart_yaxis"),
                "tier": t("final_squad.age_balance_chart_legend"),
            },
        )
        fig.update_layout(barmode="stack", height=360, margin=dict(t=50, b=20))
        st.plotly_chart(fig, width='stretch')

    # Average international caps per tier — simple experience proxy.
    if dist["overall_avg_caps"] is not None:
        st.markdown(f"**{t('final_squad.age_balance_avg_caps_header')}**")
        caps_rows = [
            {"tier": tier_display[tier], "avg_caps": round(avg, 1)}
            for tier, avg in dist["overall_avg_caps"].items()
            if avg is not None
        ]
        if caps_rows:
            st.dataframe(pd.DataFrame(caps_rows), width='stretch', hide_index=True)