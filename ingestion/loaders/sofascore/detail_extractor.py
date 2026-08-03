# ingestion/loaders/sofascore_detail_extractor.py
#
# Extracts structured Silver-ready columns from sofascore_detail Bronze JSON.
# Reads from MinIO: football-bronze/sofascore_detail/
# Writes to MinIO:  football-bronze/sofascore_detail_extracted/
#
# Data strategy:
#   career["25/26"] → MP, MIN, GLS, AST → compute Gls_per90, Ast_per90
#   season_detail   → Tackles/game, Int/game, Clr/game, xG, xA (per-game proxy)
#                     NOTE: season_detail = most recent competition snapshot,
#                     used as proxy for full-season rates (best available)
#   GK-specific     → GA_per90, GK_save_pct, GK_saves_per90 from season_detail
#
# Output columns per player:
#   sd_mp_2526, sd_min_2526
#   sd_gls_per90, sd_ast_per90          (from career 25/26 totals ÷ minutes)
#   sd_xg_per90, sd_xa_per90            (from season_detail, per-game)
#   sd_shots_per90                      (from season_detail)
#   sd_tkl_per90, sd_int_per90          (from season_detail Defending)
#   sd_clr_per90                        (from season_detail Defending)
#   sd_key_passes_per90                 (from season_detail Passing)
#   sd_pass_acc_pct                     (from season_detail Passing)
#   sd_dribbles_per90                   (from season_detail Other)
#   sd_duels_won_pct                    (from season_detail Other)
#   -- GK only --
#   sd_ga_per90, sd_save_pct            (from season_detail Matches)
#   sd_saves_per90                      (from season_detail Matches)

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

TARGET_SEASON = "25/26"
MIN_MINUTES   = 90  # minimum minutes to compute per90 (avoid 1-match noise)


def _get(season: dict, category: str, stat: str, field: str = "value"):
    """Safe getter from sofascore_detail_season nested dict."""
    try:
        return season[category][stat][field]
    except (KeyError, TypeError):
        return None


def _per90(total, minutes):
    """Compute per-90 rate. Returns None if minutes < MIN_MINUTES."""
    if total is None or minutes is None or minutes < MIN_MINUTES:
        return None
    return round(float(total) / (float(minutes) / 90), 4)


def extract_player(record: dict) -> dict:
    """
    Extracts Silver-ready stats from one sofascore_detail Bronze record.

    Args:
        record: One player's sofascore_detail JSON record.

    Returns:
        Dict with sd_* columns. player_id always present.
        Missing stats = None (Silver imputes later).
    """
    pid     = record["player_id"]
    career  = record.get("sofascore_detail_career", [])
    season  = record.get("sofascore_detail_season", {})

    out = {"player_id": pid}

    # ── Step 1: career 25/26 totals ──────────────────────────────────────────
    entry_2526 = next(
        (e for e in career if e.get("label") == TARGET_SEASON and e.get("is_summary")),
        None,
    )

    if entry_2526:
        mp  = entry_2526.get("MP")
        mins = entry_2526.get("MIN")
        gls = entry_2526.get("GLS")
        ast = entry_2526.get("AST")

        out["sd_mp_2526"]  = mp
        out["sd_min_2526"] = mins

        # Goals per 90 — from season totals (most accurate)
        out["sd_gls_per90"] = _per90(gls, mins)
        out["sd_ast_per90"] = _per90(ast, mins)
    else:
        out["sd_mp_2526"]   = None
        out["sd_min_2526"]  = None
        out["sd_gls_per90"] = None
        out["sd_ast_per90"] = None
        logger.debug("No 25/26 career entry for %s", pid)

    # ── Step 2: season_detail per-game rates ─────────────────────────────────
    # NOTE: these are per-game averages from most recent competition.
    # Used as proxy for full-season rates — best available from this source.

    # Attacking
    xg_val = _get(season, "Attacking", "Expected goals (xG)")
    out["sd_xg_per90"]    = round(float(xg_val), 4) if xg_val is not None else None
    xa_val = _get(season, "Passing", "Expected assists (xA)")
    out["sd_xa_per90"]    = round(float(xa_val), 4) if xa_val is not None else None
    shots  = _get(season, "Attacking", "Total shots")
    out["sd_shots_per90"] = round(float(shots), 4) if shots is not None else None

    # Defending — the key GAP columns
    tkl = _get(season, "Defending", "Tackles per game")
    out["sd_tkl_per90"] = round(float(tkl), 4) if tkl is not None else None
    intc = _get(season, "Defending", "Interceptions")
    out["sd_int_per90"] = round(float(intc), 4) if intc is not None else None
    clr  = _get(season, "Defending", "Clearances per game")
    out["sd_clr_per90"] = round(float(clr), 4) if clr is not None else None

    # Passing
    kp = _get(season, "Passing", "Key passes")
    out["sd_key_passes_per90"] = round(float(kp), 4) if kp is not None else None
    pass_pct = _get(season, "Passing", "Accurate passes", "percentage")
    out["sd_pass_acc_pct"] = round(float(pass_pct), 1) if pass_pct is not None else None

    # Other
    drib = _get(season, "Other (per game)", "Succ. dribbles")
    out["sd_dribbles_per90"] = round(float(drib), 4) if drib is not None else None
    duels_pct = _get(season, "Other (per game)", "Total duels won", "percentage")
    out["sd_duels_won_pct"] = round(float(duels_pct), 1) if duels_pct is not None else None

    # ── Step 3: GK-specific ──────────────────────────────────────────────────
    # First try season_detail (rich stats)
    ga_pg = _get(season, "Matches", "Goals conceded per game")
    out["sd_ga_per90"] = round(float(ga_pg), 4) if ga_pg is not None else None

    saves_pg = _get(season, "Matches", "Saves per game")
    out["sd_saves_per90"] = round(float(saves_pg), 4) if saves_pg is not None else None

    save_pct = _get(season, "Matches", "Save percentage")
    if save_pct is None:
        save_pct = _get(season, "Matches", "Saves per game", "percentage")
    out["sd_save_pct"] = round(float(save_pct), 1) if save_pct is not None else None

    # Fallback: compute GA_per90 from career 25/26 totals (GC column)
    # IMPORTANT: use CLUB entry only — exclude national team entries
    # National team stats have "is_summary" sometimes false or label like "2026"/"2025"
    # Club entries have label "25/26" AND team name that is NOT "Morocco"
    if out["sd_ga_per90"] is None and entry_2526 is not None:
        # Verify this is a club entry (not national team)
        # National team entries typically have short year labels like "2025", "2026"
        # Club entries have "25/26" format
        label = entry_2526.get("label", "")
        is_club_entry = "/" in label  # "25/26" has slash, "2025" or "2026" does not
        gc   = entry_2526.get("GC")
        mins = entry_2526.get("MIN")
        if is_club_entry and gc is not None and mins and mins >= 90:
            out["sd_ga_per90"] = round(float(gc) / (float(mins) / 90), 4)
            logger.debug("%s — GA_per90 from CLUB career totals: GC=%s MIN=%s → %.4f",
                         pid, gc, mins, out["sd_ga_per90"])

    # Clean sheets from career (club only)
    if entry_2526 is not None and "/" in entry_2526.get("label", ""):
        cls = entry_2526.get("CLS")
        out["sd_clean_sheets"] = int(cls) if cls is not None else None

    return out


def run_sofascore_detail_extractor(
    input_json_path: str,
    output_prefix:   str = "sofascore_detail_extracted/",
    run_date:        str = "",
) -> list[dict]:
    """
    Main entry point: reads sofascore_detail JSON, extracts all players,
    uploads result to MinIO.

    Args:
        input_json_path: Path to local sofascore_detail JSON (or MinIO record list).
        output_prefix:   MinIO prefix to write extracted results.
        run_date:        Run date string for filename.

    Returns:
        List of extracted player dicts.
    """
    import json

    with open(input_json_path) as f:
        raw = json.load(f)

    results = []
    ok = 0
    for record in raw:
        if len(record) <= 1:
            # No detail data — return skeleton
            results.append({"player_id": record["player_id"]})
            continue
        extracted = extract_player(record)
        results.append(extracted)
        if extracted.get("sd_gls_per90") is not None or extracted.get("sd_tkl_per90") is not None:
            ok += 1

    logger.info(
        "sofascore_detail_extractor: %d/%d players with extracted stats",
        ok, len(results),
    )

    # Upload to MinIO
    try:
        from clients.minio_client import upload_json
        filename = f"players_sd_extracted_{run_date}.json"
        upload_json(
            bucket="football-bronze",
            prefix=output_prefix,
            filename=filename,
            data=results,
        )
        logger.info("Uploaded extracted data → s3://football-bronze/%s%s", output_prefix, filename)
    except Exception as e:
        logger.warning("MinIO upload failed: %s — results returned in memory only.", e)

    return results