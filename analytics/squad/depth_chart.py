# analytics/depth_chart.py
# Football Atlas — Depth Chart / Injury Replacement Candidates
#
# Implements 10_depth_chart.md: "If [selected player] gets injured, who
# from outside the 26 is the best replacement at their position?" — the
# single most practically important pre-tournament coaching question this
# tool could not previously answer at all.
#
# Deliberately has ZERO Streamlit imports (same rule as dashboard/data_loader.py
# — see that file's header) and does ZERO file/network I/O of its own, so it
# can be unit-tested end-to-end with synthetic DataFrames. Callers (e.g. the
# dashboard page) are responsible for loading the actual Gold snapshot via
# dashboard/data_loader.py::load_latest_players_scored() /
# load_latest_top26() and passing the resulting DataFrames in.
#
# ── On "must respect DECISION_DATE (spec #1)" ───────────────────────────────
# 10_depth_chart.md's Functional Requirement #3 says replacement candidates'
# underlying stats must be pre-cutoff, per spec #1. As of this session,
# DECISION_DATE does not exist anywhere in this codebase (config.py, or any
# run_*.py) — spec #1 was referenced but was never actually implemented.
# Resolution (flagged for the person, not silently assumed away): this
# module does zero I/O and zero "live" lookups of its own — it is a pure
# function over whatever `all_players_df` / `selected_ids` snapshot the
# caller already loaded from a specific Gold run. Since Gold snapshots are
# already frozen point-in-time files (players_scored_{run_date}.csv), any
# cutoff enforcement belongs at ingestion time (spec #1's actual scope), not
# here — this module can only ever be as fresh/stale as the snapshot it's
# handed, by construction. If/when spec #1 is implemented, nothing in this
# module needs to change.
#
# ── On matching "compatible secondary position" ──────────────────────────────
# tm_secondary_positions (see ingestion/loaders/transfermarkt_loader.py) is a
# free-text, comma-separated list of GRANULAR Transfermarkt labels (e.g.
# "Right Midfield, Left-Back") — NOT the same GK/DEF/MID/FWD taxonomy used by
# the `position` column. No mapping between the two existed anywhere in this
# codebase before this module. POSITION_BUCKET_MAP below is that mapping,
# built from every distinct label observed in real Bronze/Silver output
# (see tests for the exhaustive list). Matched case-insensitively; any label
# NOT in the map is logged and skipped rather than guessed at — same
# conservative philosophy as analytics/ml/injury_predictor.py's keyword
# classification (see that module's header).

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

logger = logging.getLogger(__name__)

POSITIONS = ["GK", "DEF", "MID", "FWD"]

# Every distinct tm_secondary_positions label observed in real Bronze data
# for this project (players_scored_*.csv, 2026-06 through 2026-07 runs),
# mapped to the GK/DEF/MID/FWD bucket taxonomy used everywhere else.
# Keys are matched case-insensitively with hyphens normalized to spaces
# (see _normalize_label()).
POSITION_BUCKET_MAP: dict[str, str] = {
    "goalkeeper": "GK",

    "centre back": "DEF",
    "center back": "DEF",
    "right back": "DEF",
    "left back": "DEF",
    "sweeper": "DEF",

    "defensive midfield": "MID",
    "central midfield": "MID",
    "right midfield": "MID",
    "left midfield": "MID",
    "attacking midfield": "MID",

    "right winger": "FWD",
    "left winger": "FWD",
    "centre forward": "FWD",
    "center forward": "FWD",
    "second striker": "FWD",
}


def _normalize_label(label: str) -> str:
    return label.strip().lower().replace("-", " ")


def parse_secondary_position_buckets(raw: str | float | None) -> list[str]:
    """
    Parses a raw tm_secondary_positions string (e.g. "Right Midfield,
    Left-Back") into a deduplicated list of GK/DEF/MID/FWD buckets.

    Returns [] for NaN/None/empty input, and for any individual label not
    found in POSITION_BUCKET_MAP (logged, not guessed at — see module
    header). Never raises on malformed input.
    """
    if raw is None or (isinstance(raw, float) and pd.isna(raw)) or not str(raw).strip():
        return []

    buckets: list[str] = []
    for part in str(raw).split(","):
        label = _normalize_label(part)
        if not label:
            continue
        bucket = POSITION_BUCKET_MAP.get(label)
        if bucket is None:
            logger.info(
                "parse_secondary_position_buckets: unrecognized TM position "
                "label %r — skipped, not guessed at. Add it to "
                "POSITION_BUCKET_MAP in analytics/depth_chart.py if this is "
                "a genuine (mis-mapped) position.", part.strip(),
            )
            continue
        if bucket not in buckets:
            buckets.append(bucket)
    return buckets


@dataclass
class ReplacementCandidate:
    player_id: str
    player_name: str
    position: str                # candidate's own primary position (GK/DEF/MID/FWD)
    DSSScore: float
    gap_to_incumbent: float      # incumbent_DSSScore - candidate_DSSScore
    via_secondary_position: bool  # True if matched only via tm_secondary_positions, not primary `position`
    stronger_than_incumbent: bool  # gap_to_incumbent < 0 — a real "should this player already be in?" signal

    def as_dict(self) -> dict:
        return {
            "player_id": self.player_id,
            "player_name": self.player_name,
            "position": self.position,
            "DSSScore": self.DSSScore,
            "gap_to_incumbent": self.gap_to_incumbent,
            "via_secondary_position": self.via_secondary_position,
            "stronger_than_incumbent": self.stronger_than_incumbent,
        }


@dataclass
class DepthChartResult:
    target_player_id: str | None
    target_player_name: str | None
    target_position: str
    incumbent_DSSScore: float | None
    requested_top_n: int
    candidates: list[ReplacementCandidate] = field(default_factory=list)
    message: str | None = None   # explanatory note — see module docstring / Edge Cases

    @property
    def has_full_depth(self) -> bool:
        """False if fewer candidates were found than requested_top_n."""
        return len(self.candidates) >= self.requested_top_n

    def as_dict(self) -> dict:
        return {
            "target_player_id": self.target_player_id,
            "target_player_name": self.target_player_name,
            "target_position": self.target_position,
            "incumbent_DSSScore": self.incumbent_DSSScore,
            "requested_top_n": self.requested_top_n,
            "candidates": [c.as_dict() for c in self.candidates],
            "message": self.message,
            "has_full_depth": self.has_full_depth,
        }


def _candidate_pool(
    all_players_df: pd.DataFrame,
    position: str,
    selected_ids: set,
    exclude_player_id: str | None = None,
) -> pd.DataFrame:
    """
    Returns the rows of all_players_df eligible as replacements for
    `position`: not currently selected (not in selected_ids), not the
    target player themself, and either primary `position` match or
    `position` present among their parsed tm_secondary_positions buckets.

    Adds a `_via_secondary` boolean column marking rows matched only via
    the secondary-position path (primary-position matches take priority
    when both would qualify — a row is never double-counted).
    """
    pool = all_players_df[~all_players_df["player_id"].isin(selected_ids)].copy()
    if exclude_player_id is not None:
        pool = pool[pool["player_id"] != exclude_player_id]

    primary_mask = pool["position"] == position

    if "tm_secondary_positions" in pool.columns:
        secondary_mask = pool["tm_secondary_positions"].apply(
            lambda raw: position in parse_secondary_position_buckets(raw)
        )
    else:
        secondary_mask = pd.Series(False, index=pool.index)

    pool = pool[primary_mask | secondary_mask].copy()
    pool["_via_secondary"] = (~primary_mask) & secondary_mask
    return pool


def get_replacement_candidates(
    all_players_df: pd.DataFrame,
    selected_ids,
    player_id: str,
    top_n: int = 3,
) -> DepthChartResult:
    """
    Answers: "If `player_id` (a currently-selected player) gets injured,
    who from outside the 26 is the best replacement at their position?"

    Args:
        all_players_df: full candidate pool (all 49), e.g.
            dashboard/data_loader.py::load_latest_players_scored(). Must
            have at least player_id, player_name, position, DSSScore
            columns; tm_secondary_positions is optional (secondary-
            position matching is simply skipped if absent).
        selected_ids: player_ids of the ACTUAL selected squad (position-
            capped — e.g. top26_df["player_id"], NOT all_players_df's raw
            Rank<=26, which is a documented-elsewhere-as-wrong shortcut:
            see AUDIT_AND_FIXES.md Fix #2 / squad_summary's position-cap
            bug — the same mistake must not be reintroduced here).
        player_id: the incumbent (must be a member of selected_ids).
        top_n: how many replacement candidates to return.

    Returns:
        DepthChartResult. `.candidates` is sorted by DSSScore descending
        (primary-position matches are NOT prioritized over secondary-
        position matches in the ranking — a strong versatile candidate can
        rank above a weak primary-position one; `via_secondary_position`
        on each candidate tells the caller which kind of match it was).
        `.message` is set (and `.candidates` may be shorter than `top_n`,
        or empty) when the position group is genuinely thin/exhausted —
        this is stated explicitly, never silently truncated with no
        explanation, per the spec's Edge Cases.
    """
    selected_ids = set(selected_ids)

    target_rows = all_players_df[all_players_df["player_id"] == player_id]
    if target_rows.empty:
        return DepthChartResult(
            target_player_id=player_id,
            target_player_name=None,
            target_position="?",
            incumbent_DSSScore=None,
            requested_top_n=top_n,
            candidates=[],
            message=f"player_id={player_id!r} not found in all_players_df.",
        )

    target = target_rows.iloc[0]
    position = target["position"]
    incumbent_score = float(target["DSSScore"])

    pool = _candidate_pool(all_players_df, position, selected_ids, exclude_player_id=player_id)

    result = DepthChartResult(
        target_player_id=player_id,
        target_player_name=target["player_name"],
        target_position=position,
        incumbent_DSSScore=incumbent_score,
        requested_top_n=top_n,
    )

    if pool.empty:
        result.message = (
            f"No viable {position} replacement candidates found outside the "
            f"selected 26 (primary or secondary position) — this position "
            f"group's depth is genuinely exhausted in the current 49-player "
            f"candidate pool."
        )
        return result

    pool = pool.sort_values("DSSScore", ascending=False)
    top_rows = pool.head(top_n)

    for _, row in top_rows.iterrows():
        candidate_score = float(row["DSSScore"])
        gap = incumbent_score - candidate_score
        result.candidates.append(ReplacementCandidate(
            player_id=row["player_id"],
            player_name=row["player_name"],
            position=row["position"],
            DSSScore=candidate_score,
            gap_to_incumbent=round(gap, 4),
            via_secondary_position=bool(row["_via_secondary"]),
            stronger_than_incumbent=gap < 0,
        ))

    if len(result.candidates) < top_n:
        result.message = (
            f"Only {len(result.candidates)}/{top_n} requested {position} "
            f"replacement candidates exist in the current 49-player pool — "
            f"depth is thin at this position, not a data error."
        )

    stronger = [c for c in result.candidates if c.stronger_than_incumbent]
    if stronger:
        names = ", ".join(f"{c.player_name} ({c.DSSScore:.3f})" for c in stronger)
        note = (
            f"⚠️ {len(stronger)} replacement candidate(s) actually outscore "
            f"the incumbent ({target['player_name']}, {incumbent_score:.3f}): "
            f"{names}. Worth a manual look, not hidden."
        )
        result.message = f"{result.message} {note}" if result.message else note

    return result


def get_replacement_candidates_for_position(
    all_players_df: pd.DataFrame,
    selected_ids,
    position: str,
    top_n: int = 3,
) -> DepthChartResult:
    """
    Same as get_replacement_candidates(), but for a bare position string
    with no specific incumbent — e.g. "show me the best available DEF
    depth" rather than "who replaces Hakimi specifically". gap_to_incumbent
    on each candidate is computed relative to the TOP-ranked candidate in
    the returned list (i.e. gap-to-#1), not to any real incumbent, since
    there isn't one — `incumbent_DSSScore` on the result is set to that
    same top-candidate score for consistency, and `target_player_id` /
    `target_player_name` are left None.
    """
    if position not in POSITIONS:
        return DepthChartResult(
            target_player_id=None,
            target_player_name=None,
            target_position=position,
            incumbent_DSSScore=None,
            requested_top_n=top_n,
            candidates=[],
            message=f"Unknown position {position!r} — expected one of {POSITIONS}.",
        )

    selected_ids = set(selected_ids)
    pool = _candidate_pool(all_players_df, position, selected_ids)

    result = DepthChartResult(
        target_player_id=None,
        target_player_name=None,
        target_position=position,
        incumbent_DSSScore=None,
        requested_top_n=top_n,
    )

    if pool.empty:
        result.message = (
            f"No viable {position} candidates found outside the selected "
            f"26 (primary or secondary position)."
        )
        return result

    pool = pool.sort_values("DSSScore", ascending=False)
    top_rows = pool.head(top_n)
    top_score = float(top_rows.iloc[0]["DSSScore"])
    result.incumbent_DSSScore = top_score

    for _, row in top_rows.iterrows():
        candidate_score = float(row["DSSScore"])
        result.candidates.append(ReplacementCandidate(
            player_id=row["player_id"],
            player_name=row["player_name"],
            position=row["position"],
            DSSScore=candidate_score,
            gap_to_incumbent=round(top_score - candidate_score, 4),
            via_secondary_position=bool(row["_via_secondary"]),
            stronger_than_incumbent=False,  # meaningless without a real incumbent
        ))

    if len(result.candidates) < top_n:
        result.message = (
            f"Only {len(result.candidates)}/{top_n} requested {position} "
            f"candidates exist in the current 49-player pool."
        )

    return result
