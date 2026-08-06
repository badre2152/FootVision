from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

logger = logging.getLogger(__name__)

POSITIONS = ["GK", "DEF", "MID", "FWD"]

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
    position: str
    DSSScore: float
    gap_to_incumbent: float
    via_secondary_position: bool
    stronger_than_incumbent: bool

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
    message: str | None = None

    @property
    def has_full_depth(self) -> bool:
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
            stronger_than_incumbent=False,
        ))

    if len(result.candidates) < top_n:
        result.message = (
            f"Only {len(result.candidates)}/{top_n} requested {position} "
            f"candidates exist in the current 49-player pool."
        )

    return result