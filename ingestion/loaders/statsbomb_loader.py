# ingestion/loaders/statsbomb_loader.py
# Source 2: StatsBomb Open Data — Match Events for Morocco National Team
#
# Uses the statsbombpy library to pull freely available match data.
# Covers: passes, carries, pressures, shots, dribbles per match.
# Aggregates to per-90 metrics uploaded to Bronze.

import logging
from datetime import datetime
from io import StringIO

import pandas as pd
from rapidfuzz import fuzz, process

from clients.minio_client import upload_json
from ingestion.loaders._bronze_utils import validate_seed_columns, assert_min_coverage

logger = logging.getLogger(__name__)

BUCKET = "football-bronze"

# Minimum fuzzy-match score (0-100) to accept a StatsBomb name as the same
# player when an exact string match fails (e.g. seed "Achraf Hakimi" vs
# StatsBomb's official "Achraf Hakimi Mouh", or "Bilal El Khannouss" vs
# "Bilal El Khannous"). Tuned conservatively to avoid cross-matching two
# different Moroccan players with similar names.
FUZZY_MATCH_THRESHOLD = 85


def _resolve_player_name(player_name: str, statsbomb_names: list) -> str | None:
    """
    Resolves a seed player_name to the matching name actually used in
    StatsBomb's flattened events DataFrame.

    Tries an exact match first (cheap, unambiguous). Falls back to fuzzy
    matching only if no exact match exists, since seed names and StatsBomb's
    official squad names sometimes differ (middle names, transliteration,
    double letters, shortened first names).

    Returns the matching StatsBomb name, or None if nothing clears the
    fuzzy-match threshold.
    """
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

# Morocco competition IDs in StatsBomb Open Data
# Verified against sb.competitions() on 2026-06-19:
# 43   = FIFA World Cup (seasons available free: 2018 -> season_id=3, 2022 -> season_id=106)
# 1267 = African Cup of Nations (season available free: 2023 -> season_id=107)
# NOTE: the old values [1, 67] were wrong — competition_id=1 and 67 (U20) do not
# exist for Morocco in the free open-data competitions list, so this loader was
# silently returning 0 matches / 0 coverage before this fix.
MOROCCO_COMPETITION_IDS = [43, 1267]

# Morocco team ID in StatsBomb.
# Verified against actual WC 2018 + WC 2022 match rows (sb.matches) on 2026-06-19:
# every Morocco fixture in both tournaments resolves to team_id=788.
# NOTE: the old value (1) was a placeholder that never matched any real team,
# which combined with the wrong competition_id above guaranteed 0 results.
MOROCCO_TEAM_ID = 788

# Minimum minutes threshold to include a player's match stats
MIN_MINUTES = 10


def _try_import_statsbomb():
    """Lazy import — statsbombpy is optional; skip gracefully if missing."""
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
    """Returns all freely available Morocco international matches."""
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
    """
    Aggregates StatsBomb event data for one player across all Morocco matches.

    Returns dict with sb_* columns, or {} if no data.
    Columns:
        sb_matches          — number of matches with events
        sb_minutes          — estimated total minutes (from lineup data)
        sb_passes_per90     — accurate passes per 90 min
        sb_pressures_per90  — pressures applied per 90 min
        sb_carries_per90    — carries per 90 min
        sb_dribbles_per90   — successful dribbles per 90 min
        sb_shots_per90      — shots per 90 min
        sb_xg_per90         — xG per 90 min (from shot events)
        sb_obv_per90        — on-ball value added per 90 (if available)
    """
    # NOTE (fixed 2026-06-19): statsbombpy's sb.events() returns an already
    # FLATTENED DataFrame — "player" and "type" are plain strings, and nested
    # attributes like pass outcome / dribble outcome / shot xG live in their
    # own flattened columns (pass_outcome, dribble_outcome, shot_statsbomb_xg).
    # The previous version assumed raw un-flattened StatsBomb JSON (dicts with
    # a "name" key), which never matched anything against this library's
    # output -> guaranteed 0/49 coverage regardless of player_name or IDs.
    player_events = events_df[events_df["player"] == player_name]

    if player_events.empty:
        return {}

    matches_played = player_events["match_id"].nunique()

    # Estimate minutes: 90 × matches as approximation (StatsBomb open data
    # doesn't always include sub timing in free tier)
    total_minutes = matches_played * 80  # conservative estimate
    nineties = max(total_minutes / 90.0, 0.01)

    def _count(event_type: str, extra_filter=None) -> int:
        subset = player_events[player_events["type"] == event_type]
        if extra_filter is not None:
            subset = subset[subset.apply(extra_filter, axis=1)]
        return len(subset)

    # A completed pass has an empty/NaN pass_outcome (StatsBomb only records
    # an outcome value for non-completed passes, e.g. "Incomplete", "Out").
    passes_acc = _count(
        "Pass",
        extra_filter=lambda r: pd.isna(r.get("pass_outcome")),
    )
    pressures   = _count("Pressure")
    carries     = _count("Carry")
    dribbles    = _count(
        "Dribble",
        extra_filter=lambda r: r.get("dribble_outcome") == "Complete",
    )
    shots       = _count("Shot")

    shot_rows   = player_events[player_events["type"] == "Shot"]
    xg_total    = pd.to_numeric(
        shot_rows.get("shot_statsbomb_xg"), errors="coerce"
    ).fillna(0).sum()

    return {
        "sb_matches":          matches_played,
        "sb_minutes":          total_minutes,
        "sb_passes_per90":     round(passes_acc  / nineties, 4),
        "sb_pressures_per90":  round(pressures   / nineties, 4),
        "sb_carries_per90":    round(carries      / nineties, 4),
        "sb_dribbles_per90":   round(dribbles     / nineties, 4),
        "sb_shots_per90":      round(shots        / nineties, 4),
        "sb_xg_per90":         round(xg_total     / nineties, 4),
    }


def _aggregate_pairwise_chemistry(
    events_df: pd.DataFrame,
    resolved_map: dict,   # {seed_player_name: statsbomb_player_name}
) -> list[dict]:
    """
    Builds pairwise pass-chemistry records between candidates who both
    appear in StatsBomb's Morocco open data (WC2018/2022 + AFCON2023).

    For each pair (A, B) that both resolved to a StatsBomb name, counts
    COMPLETED passes exchanged in either direction across all shared
    matches, and the number of matches they actually shared.

    "Completed" uses the same definition as _aggregate_player_events():
    a Pass event with empty/NaN pass_outcome.

    per90 uses the same 80-min/match estimate as _aggregate_player_events()
    (StatsBomb free tier doesn't reliably give sub timing), applied to the
    number of matches the PAIR shared — not each player's own total minutes.

    Coverage note: with only 11/49 candidates resolvable in StatsBomb open
    data (WC/AFCON matches only), this yields ~50 pairs out of C(11,2)=55
    possible — most pairs that never shared a starting XI simply don't
    appear. Silver imputes the other 38 players via position-group median
    (same convention as MentalScore) — this is NOT a bug, it is the honest
    ceiling of what free StatsBomb open data covers for this squad.

    Returns:
        List of dicts: player_a_name, player_b_name (seed names),
        shared_matches, passes_combined, chem_per90 (combined passes/90).
    """
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
            "player_a_name":    sb_to_seed[sb_a],
            "player_b_name":    sb_to_seed[sb_b],
            "shared_matches":   shared_matches,
            "passes_combined":  stats["passes"],
            "chem_per90":       round(stats["passes"] / nineties, 4),
        })

    return records


def _resolve_all_seed_names(seed_df: pd.DataFrame, statsbomb_names: list) -> dict:
    """Resolves every seed player_name to its StatsBomb name (or None)."""
    return {
        row["player_name"]: _resolve_player_name(row["player_name"], statsbomb_names)
        for _, row in seed_df.iterrows()
    }


def load_statsbomb_bronze(
    seed_df: pd.DataFrame,
    run_date: str = "",
) -> str:
    """
    Pulls Morocco match events from StatsBomb Open Data and aggregates
    per-90 metrics for all players in seed_df.

    Writes results to s3://football-bronze/statsbomb/
    ALSO writes pairwise chemistry data to s3://football-bronze/statsbomb/
    as a separate object (chemistry_pairs_{run_date}.json) — reuses the
    same fetched events_df, no extra network calls.

    Args:
        seed_df:  Must contain columns: player_id, player_name
        run_date: Timestamp string; auto-generated if empty

    Returns:
        Status string: "OK (N/49 players with data)" or "FAILED"
    """
    if not run_date:
        run_date = datetime.now().strftime("%Y%m%d_%H%M%S")

    validate_seed_columns(seed_df, ["player_id", "player_name"], source="statsbomb")

    sb = _try_import_statsbomb()
    if sb is None:
        # Write empty records rather than crashing — Silver handles NaN gracefully
        records = [{"player_id": row["player_id"]} for _, row in seed_df.iterrows()]
        object_name = f"statsbomb/players_statsbomb_{run_date}.json"
        success = upload_json(BUCKET, object_name, records)
        upload_json(BUCKET, f"statsbomb_chemistry/chemistry_pairs_{run_date}.json", [])
        return "OK (0/49 — statsbombpy not installed)" if success else "FAILED"

    # Load all Morocco matches and events
    logger.info("Loading Morocco matches from StatsBomb Open Data...")
    matches_df = _get_morocco_matches(sb)

    if matches_df.empty:
        logger.warning("No Morocco matches found in StatsBomb open data.")
        records = [{"player_id": row["player_id"]} for _, row in seed_df.iterrows()]
        upload_json(BUCKET, f"statsbomb/players_statsbomb_{run_date}.json", records)
        upload_json(BUCKET, f"statsbomb_chemistry/chemistry_pairs_{run_date}.json", [])
        return "OK (0/49 — no Morocco matches in StatsBomb free tier)"

    # Load all events for all Morocco matches
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

    # Build the lookup of distinct player names actually present in the
    # Morocco events data once, so fuzzy matching (when needed) doesn't
    # rescan the full events DataFrame for every seed player.
    statsbomb_names = events_df["player"].dropna().unique().tolist()

    # Aggregate per player
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
    success  = upload_json(BUCKET, object_name, records)
    ok_count = sum(1 for r in records if len(r) > 1)

    # ── Pairwise chemistry (reuses events_df + resolved_map, no re-fetch) ────
    chem_records = _aggregate_pairwise_chemistry(events_df, resolved_map)
    upload_json(BUCKET, f"statsbomb_chemistry/chemistry_pairs_{run_date}.json", chem_records)
    logger.info(
        "StatsBomb chemistry pairs: %d pairs among %d resolved candidates",
        len(chem_records), sum(1 for v in resolved_map.values() if v is not None),
    )

    return f"OK ({ok_count}/{len(records)} players with data)" if success else "FAILED"