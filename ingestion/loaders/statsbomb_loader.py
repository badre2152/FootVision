import logging
from datetime import datetime
from io import StringIO

import pandas as pd
from rapidfuzz import fuzz, process

from clients.minio_client import upload_json
from ingestion.loaders._bronze_utils import validate_seed_columns, assert_min_coverage

logger = logging.getLogger(__name__)

BUCKET = "football-bronze"
FUZZY_MATCH_THRESHOLD = 85


def _resolve_player_name(player_name: str, statsbomb_names: list) -> str | None:
    if player_name in statsbomb_names:
        return player_name

    match = process.extractOne(
        player_name, statsbomb_names, scorer=fuzz.token_sort_ratio
    )
    if match is not None and match[1] >= FUZZY_MATCH_THRESHOLD:
        logger.info(
            "Fuzzy-matched seed name '%s' -> StatsBomb name '%s' (score=%.0f)",
            player_name, match[0], match[1],
        )
        return match[0]

    return None


MOROCCO_COMPETITION_IDS = [43, 1267]
MOROCCO_TEAM_ID = 788
MIN_MINUTES = 10


def _try_import_statsbomb():
    try:
        from statsbombpy import sb
        return sb
    except ImportError:
        logger.warning(
            "statsbombpy not installed. Run: pip install statsbombpy\n"
            "StatsBomb loader will return empty records."
        )
        return None


def _get_morocco_matches(sb) -> pd.DataFrame:
    all_matches = []
    competitions = sb.competitions()
    morocco_comps = competitions[
        competitions["competition_id"].isin(MOROCCO_COMPETITION_IDS)
    ]

    for _, comp_row in morocco_comps.iterrows():
        try:
            matches = sb.matches(
                competition_id=comp_row["competition_id"],
                season_id=comp_row["season_id"],
            )
            morocco_matches = matches[
                (matches["home_team_id"] == MOROCCO_TEAM_ID) |
                (matches["away_team_id"] == MOROCCO_TEAM_ID)
            ]
            all_matches.append(morocco_matches)
        except Exception as e:
            logger.debug("No matches for comp=%s season=%s: %s",
                         comp_row["competition_id"], comp_row["season_id"], e)

    if not all_matches:
        return pd.DataFrame()
    return pd.concat(all_matches, ignore_index=True)


def _aggregate_player_events(events_df: pd.DataFrame, player_name: str) -> dict:
    player_events = events_df[events_df["player"] == player_name]

    if player_events.empty:
        return {}

    matches_played = player_events["match_id"].nunique()
    total_minutes = matches_played * 80
    nineties = max(total_minutes / 90.0, 0.01)

    def _count(event_type: str, extra_filter=None) -> int:
        subset = player_events[player_events["type"] == event_type]
        if extra_filter is not None:
            subset = subset[subset.apply(extra_filter, axis=1)]
        return len(subset)

    passes_acc = _count(
        "Pass",
        extra_filter=lambda r: pd.isna(r.get("pass_outcome")),
    )
    pressures = _count("Pressure")
    carries = _count("Carry")
    dribbles = _count(
        "Dribble",
        extra_filter=lambda r: r.get("dribble_outcome") == "Complete",
    )
    shots = _count("Shot")

    shot_rows = player_events[player_events["type"] == "Shot"]
    xg_total = pd.to_numeric(
        shot_rows.get("shot_statsbomb_xg"), errors="coerce"
    ).fillna(0).sum()

    return {
        "sb_matches": matches_played,
        "sb_minutes": total_minutes,
        "sb_passes_per90": round(passes_acc / nineties, 4),
        "sb_pressures_per90": round(pressures / nineties, 4),
        "sb_carries_per90": round(carries / nineties, 4),
        "sb_dribbles_per90": round(dribbles / nineties, 4),
        "sb_shots_per90": round(shots / nineties, 4),
        "sb_xg_per90": round(xg_total / nineties, 4),
    }


def _aggregate_pairwise_chemistry(
    events_df: pd.DataFrame,
    resolved_map: dict,
) -> list[dict]:
    sb_to_seed = {v: k for k, v in resolved_map.items() if v is not None}
    candidate_sb_names = set(sb_to_seed.keys())

    if not candidate_sb_names:
        return []

    passes = events_df[
        (events_df["type"] == "Pass")
        & (events_df["player"].isin(candidate_sb_names))
        & (events_df["pass_outcome"].isna())
    ].copy()

    pair_stats: dict[tuple, dict] = {}
    for match_id, grp in passes.groupby("match_id"):
        for _, row in grp.iterrows():
            a, b = row["player"], row.get("pass_recipient")
            if pd.isna(b) or b not in candidate_sb_names:
                continue
            pair = tuple(sorted([a, b]))
            if pair[0] == pair[1]:
                continue
            entry = pair_stats.setdefault(pair, {"passes": 0, "matches": set()})
            entry["passes"] += 1
            entry["matches"].add(match_id)

    records = []
    for (sb_a, sb_b), stats in pair_stats.items():
        shared_matches = len(stats["matches"])
        nineties = max(shared_matches * 80 / 90.0, 0.01)
        records.append({
            "player_a_name": sb_to_seed[sb_a],
            "player_b_name": sb_to_seed[sb_b],
            "shared_matches": shared_matches,
            "passes_combined": stats["passes"],
            "chem_per90": round(stats["passes"] / nineties, 4),
        })

    return records


def _resolve_all_seed_names(seed_df: pd.DataFrame, statsbomb_names: list) -> dict:
    return {
        row["player_name"]: _resolve_player_name(row["player_name"], statsbomb_names)
        for _, row in seed_df.iterrows()
    }


def load_statsbomb_bronze(
    seed_df: pd.DataFrame,
    run_date: str = "",
) -> str:
    if not run_date:
        run_date = datetime.now().strftime("%Y%m%d_%H%M%S")

    validate_seed_columns(seed_df, ["player_id", "player_name"], source="statsbomb")

    sb = _try_import_statsbomb()
    if sb is None:
        records = [{"player_id": row["player_id"]} for _, row in seed_df.iterrows()]
        object_name = f"statsbomb/players_statsbomb_{run_date}.json"
        success = upload_json(BUCKET, object_name, records)
        upload_json(BUCKET, f"statsbomb_chemistry/chemistry_pairs_{run_date}.json", [])
        return "OK (0/49 — statsbombpy not installed)" if success else "FAILED"

    logger.info("Loading Morocco matches from StatsBomb Open Data...")
    matches_df = _get_morocco_matches(sb)

    if matches_df.empty:
        logger.warning("No Morocco matches found in StatsBomb open data.")
        records = [{"player_id": row["player_id"]} for _, row in seed_df.iterrows()]
        upload_json(BUCKET, f"statsbomb/players_statsbomb_{run_date}.json", records)
        upload_json(BUCKET, f"statsbomb_chemistry/chemistry_pairs_{run_date}.json", [])
        return "OK (0/49 — no Morocco matches in StatsBomb free tier)"

    all_events = []
    for match_id in matches_df["match_id"].unique():
        try:
            events = sb.events(match_id=match_id)
            events["match_id"] = match_id
            all_events.append(events)
        except Exception as e:
            logger.debug("Failed to load events for match_id=%s: %s", match_id, e)

    if not all_events:
        logger.warning("No events loaded from StatsBomb.")
        records = [{"player_id": row["player_id"]} for _, row in seed_df.iterrows()]
        upload_json(BUCKET, f"statsbomb/players_statsbomb_{run_date}.json", records)
        upload_json(BUCKET, f"statsbomb_chemistry/chemistry_pairs_{run_date}.json", [])
        return "OK (0/49 — no events loaded)"

    events_df = pd.concat(all_events, ignore_index=True)
    logger.info("Loaded %d events from %d Morocco matches.", len(events_df), len(all_events))

    statsbomb_names = events_df["player"].dropna().unique().tolist()

    resolved_map = _resolve_all_seed_names(seed_df, statsbomb_names)
    records = []
    for _, row in seed_df.iterrows():
        resolved_name = resolved_map.get(row["player_name"])
        stats = (
            _aggregate_player_events(events_df, resolved_name)
            if resolved_name is not None
            else {}
        )
        records.append({"player_id": row["player_id"], **stats})

    assert_min_coverage(records, min_pct=0.20, source="statsbomb")

    object_name = f"statsbomb/players_statsbomb_{run_date}.json"
    success = upload_json(BUCKET, object_name, records)
    ok_count = sum(1 for r in records if len(r) > 1)

    chem_records = _aggregate_pairwise_chemistry(events_df, resolved_map)
    upload_json(BUCKET, f"statsbomb_chemistry/chemistry_pairs_{run_date}.json", chem_records)
    logger.info(
        "StatsBomb chemistry pairs: %d pairs among %d resolved candidates",
        len(chem_records), sum(1 for v in resolved_map.values() if v is not None),
    )

    return f"OK ({ok_count}/{len(records)} players with data)" if success else "FAILED"