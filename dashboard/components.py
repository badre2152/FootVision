# dashboard/components.py
# Football Atlas — Shared Dashboard Components
#
# Added 2026-08-01 — spec "05_dashboard_decision_vs_audit_labeling.md" (#5).
#
# WHY THIS FILE EXISTS
# ─────────────────────────────────────────────────────────────────────────
# dashboard/pages/5_selection_audit.py's title already makes clear it's a
# retrospective comparison ("... vs. the Real 2026 World Cup Squad"), but
# dashboard/pages/8_competition.py's "Morocco's Real WC2026 Run" tab showed
# real match results (post-tournament, post-DECISION_DATE information) with
# NO warning that this is retrospective audit content, not a forward-looking
# pre-decision recommendation — an inconsistency within the same project,
# not a uniform gap (page 5 proves the pattern was already known).
#
# post_hoc_banner() centralizes the warning text/styling in ONE place so it
# can't drift out of sync between pages the way the two pages above already
# had (same "defined in one place, forgotten in another" risk flagged
# elsewhere in this project — see spec #3 / AUDIT_AND_FIXES.md's Fix pattern).
#
# Deliberately has ZERO business logic — just a rendering helper — so it
# stays trivial to reuse from any page without import cycles.

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# Pages that import this already do their own sys.path.insert(0, <project
# root>) before importing anything under dashboard/ or analytics/ — but
# guard here too so `import config` also works if this module is ever
# imported first/standalone (e.g. from a test).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import config

# Exact wording reused by the AppTest smoke test (tests/test_dashboard_post_hoc_banner.py)
# — keep this string stable; the test asserts on a substring of it.
POST_HOC_BANNER_TITLE = "⏱️ POST-HOC AUDIT"


def post_hoc_banner(extra: str | None = None) -> None:
    """
    Renders an unmissable warning banner marking the section/page below it
    as retrospective, post-tournament analysis — NOT a forward-looking,
    pre-decision recommendation.

    Call this immediately above ANY content that reflects information which
    was not knowable as of config.DECISION_DATE (real match results, the
    real announced squad, post-tournament audits, a future rotation-plan
    "validate_vs_real_xi()" page, etc.) — see spec
    "05_dashboard_decision_vs_audit_labeling.md" Functional Requirement 1.

    Uses st.warning() (not an expander, not a caption) so it renders
    inline and open by default — an expander can be collapsed/skipped past,
    which defeats the point of an "unmissable" banner.

    Args:
        extra: optional page-specific context appended below the standard
            wording (e.g. naming exactly which section/tab is affected).
    """
    text = (
        f"{POST_HOC_BANNER_TITLE} — this section uses information from "
        "after the tournament, for validating model accuracy only. "
        "It is NOT what would have been shown before "
        f"**{config.DECISION_DATE.isoformat()}**."
    )
    if extra:
        text += f"\n\n{extra}"
    st.warning(text, icon="⏱️")


# ══════════════════════════════════════════════════════════════════════════
# #18 — Historical Trend View Across Gold Snapshots
# ══════════════════════════════════════════════════════════════════════════
# Distinct from post_hoc_banner() above: that one flags content built from
# information from AFTER the tournament (real results). This one flags
# content built from OLDER Gold snapshots that were produced before spec #1
# (Bronze/Silver temporal-cutoff enforcement) existed — see the long note
# above dashboard/data_loader.py's load_all_players_scored_history() for
# why, as of this change, that currently means EVERY snapshot on disk.
PRE_CUTOFF_FIX_BANNER_TITLE = "🧪 PRE-CUTOFF-FIX SNAPSHOT"


def pre_cutoff_fix_banner(extra: str | None = None) -> None:
    """
    Renders a warning marking historical Gold snapshot(s) as predating
    spec #1's DECISION_DATE-cutoff enforcement, and therefore possibly
    containing temporally-leaked data (see
    18_historical_trend_view.md's Technical Requirements).

    Call this immediately above any trend/history view built from
    dashboard/data_loader.py's load_all_players_scored_history().

    Args:
        extra: optional page-specific context appended below the standard
            wording.
    """
    text = (
        f"{PRE_CUTOFF_FIX_BANNER_TITLE} — this snapshot was generated before "
        "spec #1's DECISION_DATE-cutoff enforcement existed in the code (see "
        "config.py § 0), so it may contain temporally-leaked data (e.g. "
        "injury/form data from after the actual decision date). Treat it as "
        "directional only, not as a validated decision-support signal. "
        "Re-run the pipeline (`run_bronze.py --as-of ... → run_silver.py → "
        "run_gold.py`) to produce a genuinely cutoff-enforced snapshot."
    )
    if extra:
        text += f"\n\n{extra}"
    st.warning(text, icon="🧪")