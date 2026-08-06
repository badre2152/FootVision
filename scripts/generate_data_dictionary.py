import re
import sys
import glob
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402

OUTPUT_PATH = PROJECT_ROOT / "docs" / "data_dictionary.md"

PREFIX_SOURCE_MAP: list[tuple[str, str]] = [
    ("fbref_gk_",       "FBref (goalkeeper club stats) — ingestion/loaders/fbref_loader.py"),
    ("fbref_",          "FBref (club stats) — ingestion/loaders/fbref_loader.py / fbref_intl_loader.py"),
    ("sb_",             "StatsBomb Open Data — ingestion/loaders/statsbomb_loader.py"),
    ("tm_",             "Transfermarkt — ingestion/loaders/transfermarkt_loader.py"),
    ("understat_",      "Understat — ingestion/loaders/understat_loader.py"),
    ("sofascore_",      "SofaScore — ingestion/loaders/sofascore/client.py"),
    ("flashscore_",     "FlashScore — ingestion/loaders/flashscore_loader.py"),
    ("sd_",             "SofaScore (per-90 detail extract) — run_sd_extractor.py / ingestion/loaders/sofascore/detail_extractor.py"),
    ("fm_",             "Fotmob — ingestion/loaders/fotmob_loader.py / run_fotmob.py"),
    ("csv_",            "Local FBref-format CSV import (Big-5 leagues fallback) — ingestion/loaders/club_stats_csv_loader.py"),
    ("wiki_",           "Wikidata — ingestion/loaders/wikidata_loader.py"),
    ("inj_",            "Transfermarkt injury history — ingestion/loaders/transfermarkt_injury_loader.py"),
    ("chem_",           "StatsBomb pairwise pass-network chemistry — ingestion/silver_pipeline.py::join_chemistry_pairs()"),
    ("sentiment_",      "Reddit + YouTube + NewsAPI, NLP-scored — analytics/nlp/sentiment_analyzer.py"),
    ("manual_intl_",    "Manual override seed (used when FBref/Fotmob international data is unavailable) — seeds/"),
    ("clubelo_",        "ClubElo (optional, --use-clubelo) — ingestion/loaders/clubelo_loader.py"),
]

EXACT_SOURCE_MAP: dict[str, str] = {
    "player_id":            "Silver pipeline merge key (generated)",
    "player_name":          "Seed roster (seeds/) — canonical 49-candidate list",
    "position":             "Seed roster (seeds/)",
    "league":                "Seed roster (seeds/) / Transfermarkt",
    "club":                  "Seed roster (seeds/) / Transfermarkt",
    "active_2526":           "Seed roster (seeds/) — manual flag",
    "market_value_eur":      "Transfermarkt (renamed from tm_market_value_eur in Silver) — ingestion/silver_pipeline.py",
    "intl_caps":             "COALESCE(FBref international, Fotmob) with manual_intl_caps fallback — ingestion/loaders/fbref_intl_loader.py",
    "intl_goals_total":      "COALESCE(FBref international, Fotmob) — ingestion/loaders/fbref_intl_loader.py",
    "intl_ast_total":        "COALESCE(FBref international, Fotmob) — ingestion/loaders/fbref_intl_loader.py",
    "intl_minutes_total":    "FBref international — ingestion/loaders/fbref_intl_loader.py",
    "intl_goals_per90":      "Derived from intl_goals_total / intl_minutes_total — ingestion/silver_pipeline.py",
    "intl_ast_per90":        "Derived from intl_ast_total / intl_minutes_total — ingestion/silver_pipeline.py",
    "xg_per90":              "COALESCE(understat_npxg_per90, sofascore_detail) — ingestion/silver_pipeline.py",
    "xa_per90":              "COALESCE(understat_xa_per90, sofascore_detail) — ingestion/silver_pipeline.py",
    "form_final":            "COALESCE(sofascore_form_5, flashscore_form_5) — ingestion/silver_pipeline.py",
    "rating_final":          "COALESCE(sofascore_rating_avg, flashscore_rating_avg) — ingestion/silver_pipeline.py",
    "Gls_per90":             "COALESCE(fbref_goals_per90, csv_gls_per90, sofascore_detail) — ingestion/silver_pipeline.py",
    "Ast_per90":             "COALESCE(fbref_ast_per90, csv_ast_per90, sofascore_detail) — ingestion/silver_pipeline.py",
    "Tkl_per90":             "SofaScore detail extract (sd_tkl_per90) — ingestion/silver_pipeline.py",
    "Int_per90":             "SofaScore detail extract (sd_int_per90) — ingestion/silver_pipeline.py",
    "Clr_per90":             "SofaScore detail extract (sd_clr_per90) — ingestion/silver_pipeline.py",
    "GA_per90":              "COALESCE(fbref_gk_ga_per90, sd_ga_per90) — ingestion/silver_pipeline.py",
    "GK_save_pct":           "COALESCE(fbref_gk_save_pct, sd_save_pct) — ingestion/silver_pipeline.py",
    "GK_saves_per90":        "COALESCE(fbref_gk_saves_per90, sd_saves_per90) — ingestion/silver_pipeline.py",
    "ga_imputed_flag":       "Silver pipeline flag — ingestion/silver_pipeline.py",
    "mental_source":         "Silver pipeline flag (real StatsBomb vs. position-median imputed) — ingestion/silver_pipeline.py",
    "sb_mental_data_real":   "StatsBomb — flags whether MentalScore's 3 inputs are real vs. imputed",
}

GOLD_DERIVED_COLUMNS: set[str] = {
    "PerformanceScore", "FormScore", "MentalScore", "AvailabilityScore",
    "InternationalScore", "ChemistryScore", "PressureIndex", "FlexibilityScore",
    "SentimentScore", "DSSScore", "DSSScore_pre_gate", "DSSScore_pct", "Rank",
    "offensive_score", "defensive_score", "form_score",
    "pressure_delta", "minutes_flag", "PlayerDNA", "player_dna_cluster",
    "dna_imputed_features_count", "sentiment_raw",
}

CURATED_DESCRIPTIONS: dict[str, str] = {
    "player_id": "Internal unique player identifier, generated by the Silver pipeline merge.",
    "player_name": "Player's display name.",
    "position": "Primary position bucket: GK / DEF / MID / FWD.",
    "league": "Current club's league.",
    "club": "Current club.",
    "active_2526": "Whether the player is confirmed active for the 2025-26 season.",
    "manual_intl_caps": "Manually-sourced fallback for international caps when FBref/Fotmob international data is missing.",
    "manual_intl_goals": "Manually-sourced fallback for international goals.",
    "manual_intl_ast": "Manually-sourced fallback for international assists.",
    "wiki_qid": "Wikidata entity ID (Q-number).",
    "wiki_label": "Player's canonical Wikidata label.",
    "wiki_dob": "Date of birth, used for all age-relative computations (see config.DECISION_DATE).",
    "wiki_height_cm": "Height in centimeters.",
    "market_value_eur": "Current market value in EUR.",
    "tm_foot": "Preferred foot.",
    "tm_secondary_positions": "Secondary positions the player can cover, per Transfermarkt — feeds FlexibilityScore.",
    "tm_num_positions": "Count of distinct positions (primary + secondary) — normalized input to FlexibilityScore.",
    "Gls_per90": "Club goals per 90 minutes. Input to OFFENSIVE_WEIGHTS (config.py).",
    "Ast_per90": "Club assists per 90 minutes. Input to OFFENSIVE_WEIGHTS.",
    "xg_per90": "Club non-penalty xG per 90 minutes. Input to OFFENSIVE_WEIGHTS and PressureIndex.",
    "xa_per90": "Club xA per 90 minutes. Input to OFFENSIVE_WEIGHTS.",
    "Tkl_per90": "Club tackles per 90 minutes (outfield). Input to DEFENSIVE_WEIGHTS.",
    "Int_per90": "Club interceptions per 90 minutes (outfield). Input to DEFENSIVE_WEIGHTS.",
    "Clr_per90": "Club clearances per 90 minutes (outfield). Input to DEFENSIVE_WEIGHTS.",
    "GA_per90": "Goals against per 90 minutes (goalkeepers only, inverse — lower is better). Input to DEFENSIVE_WEIGHTS.",
    "GK_save_pct": "Goalkeeper save percentage.",
    "GK_saves_per90": "Goalkeeper saves per 90 minutes.",
    "form_final": "COALESCE(sofascore_form_5, flashscore_form_5). Input to FORM_WEIGHTS.",
    "rating_final": "COALESCE(sofascore_rating_avg, flashscore_rating_avg). Input to FORM_WEIGHTS.",
    "offensive_score": "Weighted composite of OFFENSIVE_WEIGHTS sub-columns — feeds PerformanceScore via PERFORMANCE_SUB_WEIGHTS.",
    "defensive_score": "Weighted composite of DEFENSIVE_WEIGHTS sub-columns — feeds PerformanceScore.",
    "form_score": "Weighted composite of FORM_WEIGHTS sub-columns — feeds PerformanceScore.",
    "sb_matches": "StatsBomb open-data matches available for this player (coverage indicator).",
    "sb_minutes": "StatsBomb open-data minutes played.",
    "sb_passes_per90": "StatsBomb passes per 90 — input to MENTAL_WEIGHTS (composure/distribution proxy).",
    "sb_pressures_per90": "StatsBomb pressures per 90 — input to MENTAL_WEIGHTS (work-rate proxy).",
    "sb_carries_per90": "StatsBomb carries per 90 — input to MENTAL_WEIGHTS (ball-carrying/decision-making proxy).",
    "sb_dribbles_per90": "StatsBomb dribbles per 90 (reference only, not currently weighted).",
    "sb_shots_per90": "StatsBomb shots per 90 (reference only).",
    "sb_xg_per90": "StatsBomb (international) xG per 90 — used by PressureIndex (international minus club xG rate).",
    "sb_mental_data_real": "True if this player's MentalScore inputs are real StatsBomb data rather than position-group-median imputed.",
    "chem_avg_per90": "Average StatsBomb pairwise pass-network value with current squad-mates — input to ChemistryScore.",
    "chem_num_partners": "Number of squad-mates this player has a real (non-imputed) pairwise chemistry value with.",
    "pressure_delta": "International (StatsBomb) minus club (xg_per90) xG rate. Positive = performs better in big internationals.",
    "understat_npxg_per90": "Understat non-penalty xG per 90.",
    "understat_xa_per90": "Understat xA per 90.",
    "understat_shots_per90": "Understat shots per 90.",
    "understat_conversion": "Understat shot conversion rate (%).",
    "sofascore_rating_avg": "SofaScore average match rating (raw, reference only — not used directly in scoring; see rating_final).",
    "sofascore_form_5": "SofaScore rolling 5-match form rating (raw, reference only; see form_final).",
    "sofascore_matches_rated": "Number of SofaScore-rated matches this season (coverage indicator).",
    "flashscore_rating_avg": "FlashScore average match rating (raw, reference only; see rating_final).",
    "flashscore_form_5": "FlashScore rolling 5-match form rating (raw, reference only; see form_final).",
    "flashscore_matches_rated": "Number of FlashScore-rated matches this season (coverage indicator).",
    "inj_count_2y": "Number of distinct injuries in the last 2 years (Transfermarkt). Input to AvailabilityScore (inverse).",
    "inj_days_missed_2y": "Total days missed to injury in the last 2 years. Input to AvailabilityScore (inverse).",
    "inj_last_type": "Most recent injury type.",
    "inj_last_date": "Date of the most recent injury.",
    "intl_caps": "International caps. Input to InternationalScore.",
    "intl_minutes_total": "Total international minutes played.",
    "intl_goals_total": "Total international goals.",
    "intl_ast_total": "Total international assists.",
    "intl_goals_per90": "International goals per 90. Input to InternationalScore.",
    "intl_ast_per90": "International assists per 90. Input to InternationalScore.",
    "sentiment_texts": "Raw collected text snippets (Reddit/YouTube/NewsAPI) used for sentiment scoring.",
    "sentiment_text_count": "Number of text snippets collected for this player (coverage indicator).",
    "sentiment_raw": "Aggregated NLP sentiment score in [0, 1] before becoming SentimentScore.",
    "PerformanceScore": "Composite: offensive_score, defensive_score, form_score (PERFORMANCE_SUB_WEIGHTS). Top-level DSSScore component.",
    "FormScore": "Composite of form_final/rating_final (FORM_WEIGHTS). Top-level DSSScore component.",
    "MentalScore": "Composite of StatsBomb mental proxies (MENTAL_WEIGHTS), 11/49 real + 38/49 position-median imputed. Top-level DSSScore component.",
    "mental_source": "'real' or 'imputed' flag for MentalScore's underlying StatsBomb data.",
    "AvailabilityScore": "Composite of injury history (inverse) — how reliably available the player has been. Top-level DSSScore component.",
    "InternationalScore": "Composite of international caps/goals/assists. Top-level DSSScore component.",
    "ChemistryScore": "Composite of chem_avg_per90 (real 11/49, imputed 38/49 — see chem_is_real in Silver). Top-level DSSScore component.",
    "PressureIndex": "Composite derived from pressure_delta — big-match/international performance vs. club baseline. Top-level DSSScore component.",
    "FlexibilityScore": "Composite of tm_num_positions — positional versatility. Top-level DSSScore component, and (spec #13) a tiebreak input in squad selection.",
    "SentimentScore": "Composite NLP sentiment score, [0,1]. Top-level DSSScore component (smallest weight, 0.05).",
    "DSSScore_pre_gate": "DSSScore before the minutes-played gate is applied.",
    "minutes_flag": "Flag indicating whether this player was affected by the minutes-played gate.",
    "DSSScore": "Final weighted composite score (COMPOSITE_WEIGHTS) used to rank/select the squad.",
    "Rank": "Global 1-49 rank by DSSScore, descending.",
    "DSSScore_pct": "DSSScore rescaled to a wider display percentile range (visual spread only — ranking is identical to DSSScore).",
    "player_dna_cluster": "K-Means cluster ID from player-style clustering (descriptive only, not a DSSScore factor).",
    "PlayerDNA": "Human-readable play-style label derived from player_dna_cluster (descriptive only, not a DSSScore factor).",
    "dna_imputed_features_count": "Number of clustering input features that were imputed (missing-data indicator) for this player's PlayerDNA assignment.",
    "ga_imputed_flag": "Flag indicating GA_per90 (goals-against) was imputed rather than a real value (goalkeepers only).",
}


def load_latest_players_scored() -> tuple[pd.DataFrame, str]:
    files = sorted(glob.glob(str(PROJECT_ROOT / "gold" / "players_scored_*.csv")))
    if not files:
        raise FileNotFoundError(
            "No gold/players_scored_*.csv found — run run_gold.py first, "
            "then re-run this script."
        )
    path = files[-1]
    return pd.read_csv(path), Path(path).name


def classify_source(col: str) -> tuple[str, bool]:
    if col in EXACT_SOURCE_MAP:
        return EXACT_SOURCE_MAP[col], True
    for prefix, source in PREFIX_SOURCE_MAP:
        if col.startswith(prefix):
            return source, True
    if col in GOLD_DERIVED_COLUMNS or col.endswith("_norm"):
        return "Gold pipeline (derived — see raw_or_derived column)", True
    return "⚠️ unclear — needs investigation", False


def classify_raw_or_derived(col: str) -> str:
    if col.endswith("_norm"):
        return "derived (normalized, see config.NORMALIZATION_BOUNDS)"
    if col in GOLD_DERIVED_COLUMNS:
        return "derived (Gold-time composite/flag)"
    if col in config.COMPOSITE_WEIGHTS:
        return "derived (top-level DSSScore component)"
    return "raw"


def humanize_column_name(col: str) -> str:
    words = re.sub(r"([a-z])([A-Z])", r"\1 \2", col).replace("_", " ")
    return words.strip().capitalize()


def build_used_in_index(root: Path) -> dict[str, str]:
    index: dict[str, str] = {}
    exclude_dirs = {"__pycache__", ".git", "tests", "docs", "logs", "bronze", "silver", "gold", "data", "seeds"}
    for path in root.rglob("*.py"):
        if any(part in exclude_dirs for part in path.parts):
            continue
        if path.name == "generate_data_dictionary.py":
            continue
        try:
            index[str(path.relative_to(root))] = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
    return index


def find_used_in(col: str, file_index: dict[str, str], max_files: int = 6) -> str:
    pattern = re.compile(r"\b" + re.escape(col) + r"\b")
    matches = [f for f, text in file_index.items() if pattern.search(text)]
    if not matches:
        return "*(not referenced by name in any .py file outside tests/ — possibly dead)*"
    matches.sort()
    shown = matches[:max_files]
    suffix = f", +{len(matches) - max_files} more" if len(matches) > max_files else ""
    return ", ".join(f"`{m}`" for m in shown) + suffix


def main():
    df, source_file = load_latest_players_scored()
    n_rows = len(df)
    file_index = build_used_in_index(PROJECT_ROOT)

    rows = []
    n_auto_confident = 0
    for col in df.columns:
        coverage = df[col].notna().sum()
        coverage_pct = round(100 * coverage / n_rows, 1) if n_rows else 0.0

        source, confident = classify_source(col)
        if confident:
            n_auto_confident += 1

        raw_or_derived = classify_raw_or_derived(col)

        description = CURATED_DESCRIPTIONS.get(col)
        if description is None:
            description = f"*(auto-guess: {humanize_column_name(col)} — ⚠️ needs manual review)*"

        used_in = find_used_in(col, file_index)

        rows.append({
            "column": col,
            "source": source,
            "description": description,
            "coverage": f"{coverage}/{n_rows} ({coverage_pct}%)",
            "raw_or_derived": raw_or_derived,
            "used_in": used_in,
        })

    n_curated = sum(1 for c in df.columns if c in CURATED_DESCRIPTIONS)
    n_needs_review = len(df.columns) - n_curated

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = []
    lines.append("# Gold Column Data Dictionary")
    lines.append("")
    lines.append(
        "**Auto-generated by `scripts/generate_data_dictionary.py` — DO NOT hand-edit.** "
        "Re-run the script after any Bronze/Silver/Gold column change to refresh this file; "
        "hand edits will be overwritten on the next run."
    )
    lines.append("")
    lines.append(f"- Source file: `gold/{source_file}` ({n_rows} candidates)")
    lines.append(f"- Generated: {generated_at}")
    lines.append(f"- Columns: {len(df.columns)} total — "
                  f"{n_auto_confident} source-classified automatically, "
                  f"{n_curated} with a curated hand-written description, "
                  f"**{n_needs_review} flagged below as needing manual review** "
                  "(auto-guessed description and/or unclear source).")
    lines.append("")
    lines.append(
        "**Coverage** is the ACTUAL non-null percentage measured against the "
        "specific Gold run named above — not a guessed/expected figure. "
        "(A prior spec document guessed `wiki_dob` coverage at 44/49; the "
        "real, measured figure turned out to be 49/49 — see issue #16's "
        "notes. This file always measures, never assumes.)"
    )
    lines.append("")
    lines.append(
        "**used_in** is a whole-word source-text search across the project's "
        "`.py` files (excluding `tests/`) — it can under-count columns only "
        "referenced dynamically (e.g. via a loop over `df.columns`), and a "
        "column showing no hits is a candidate for the 31 \"dead\" "
        "`NORMALIZATION_BOUNDS` keys this spec's Acceptance Criteria calls "
        "out, not automatic proof of dead code."
    )
    lines.append("")
    lines.append("| Column | Source | Description | Coverage | Raw / Derived | Used in |")
    lines.append("|---|---|---|---|---|---|")
    for r in rows:
        lines.append(
            f"| `{r['column']}` | {r['source']} | {r['description']} | "
            f"{r['coverage']} | {r['raw_or_derived']} | {r['used_in']} |"
        )
    lines.append("")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text("\n".join(lines), encoding="utf-8")

    print(f"Wrote {OUTPUT_PATH.relative_to(PROJECT_ROOT)}")
    print(f"  {len(df.columns)} columns, {n_curated} curated, {n_needs_review} flagged for manual review")


if __name__ == "__main__":
    main()