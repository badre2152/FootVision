# config.py — Football DSS v18 (MASTER_SPEC v1.0)
# Centralised weights, normalization bounds, and scoring configuration.
# All Silver transforms and scoring functions read from here — never hardcode values.
#
# CONFLICTS RESOLVED (ref: MASTER_SPEC.md § 4):
#   CONFLICT-01 — FORM_WEIGHTS now use form_final + rating_final (49/49 COALESCE)
#   CONFLICT-02 — PERFORMANCE_SUB_WEIGHTS moved here from performance_score.py
#   CONFLICT-03 — MENTAL_WEIGHTS added; compute_mental_score() now real (StatsBomb)
#   CONFLICT-04 — DEFENSIVE_WEIGHTS cleaned (Tkl/Int/Clr removed — no Bronze source)
#   CONFLICT-05 — OFFENSIVE_WEIGHTS cleaned (xAG_per90 removed — no Bronze source)

from datetime import date

# ══════════════════════════════════════════════════════════════════════════════
# § 0 — DECISION_DATE (temporal cutoff — spec 01_temporal_cutoff.md)
# ══════════════════════════════════════════════════════════════════════════════
# MERGE NOTE (2026-08-01): a prior delivered archive set this to
# date(2026, 6, 1), justified only as "matches tests/test_injury_predictor.py's
# REF_DATE convention" with an explicit "CONFIRM/ADJUST before relying on it"
# caveat. That value is WRONG, not just unverified: this same file,
# analytics/ml/selection_classifier.py (REAL_SQUAD_2026, line ~5), documents
# — sourced against FIFA/worldcupstats.football — that Morocco's official
# 26-man WC2026 squad was announced 2026-05-26. A DECISION_DATE of 2026-06-01
# is AFTER that announcement, which defeats the entire purpose of a decision
# cutoff (it would let post-announcement data leak straight back in).
# Corrected to 2026-05-25 — the day BEFORE the public announcement — so
# nothing dated on/after announcement day can leak into a tool meant to
# support that decision. tests/test_injury_predictor.py's REF_DATE=2026-06-01
# fixture is a separate, self-contained what-if scenario for that test file
# only and does not need to match this constant.
#
# Single project-wide "as-of" date. Every date-relative computation that
# feeds a live recommendation (injury lookback windows, ClubElo snapshot
# date, age calculations) MUST resolve relative to this date, never to real
# wall-clock time (datetime.now()/date.today()).
#
# SCOPE NOTE: analytics/opponent_analyzer.py, analytics/rotation_strategy.py
# and analytics/ml/selection_classifier.py intentionally use REAL post-
# tournament data for retrospective auditing (run_selection_audit.py,
# run_rotation_strategy.py, dashboard pages 5/6/8) — verified via grep that
# none of them are imported by run_silver.py / run_gold.py. They are left
# untouched on purpose: an audit tool must see the real outcome to audit
# against it. Only the LIVE path (Bronze → Silver → Gold → DSSScore) is in
# scope for this constant.
DECISION_DATE: date = date(2026, 5, 25)

# ══════════════════════════════════════════════════════════════════════════════
# § 1 — Offensive Weights (PerformanceScore subscore)
# ══════════════════════════════════════════════════════════════════════════════
# CONFLICT-05 FIX: xAG_per90 removed — no Bronze source produces this column.
# understat_xa_per90 is the sole xA signal (25/49 players).
# compute_weighted_subscore redistributes weight automatically for NaN players.

OFFENSIVE_WEIGHTS: dict[str, float] = {
    "Gls_per90": 0.35,   # Goals per 90 (fbref → csv → sofascore_detail)
    "Ast_per90": 0.25,   # Assists per 90 (fbref → csv → sofascore_detail)
    "xg_per90":  0.25,   # npxG/xG per 90 (understat → sofascore_detail)
    "xa_per90":  0.15,   # xA per 90 (understat → sofascore_detail)
}  # sum = 1.0 ✓
# GAP 2 RESOLVED: xg/xa now COALESCE(understat, sofascore_detail)

DEFENSIVE_WEIGHTS: dict[str, float] = {
    "Tkl_per90": 0.35,   # Tackles per game (sofascore_detail) — outfield
    "Int_per90": 0.30,   # Interceptions (sofascore_detail) — outfield
    "Clr_per90": 0.10,   # Clearances (sofascore_detail) — outfield
    "GA_per90":  0.25,   # Goals against per 90 — GKs only (inverse)
}  # sum = 1.0 ✓
# GAP 1 RESOLVED: Tkl/Int/Clr now sourced from sofascore_detail_extracted (36/49)

# ══════════════════════════════════════════════════════════════════════════════
# § 2 — FormScore Weights (ADR-007, closed by ADR-013, fixed CONFLICT-01)
# ══════════════════════════════════════════════════════════════════════════════
# CONFLICT-01 FIX: sofascore_* columns replaced by COALESCE columns.
# Silver produces form_final and rating_final (49/49) via:
#   form_final   = COALESCE(sofascore_form_5,     flashscore_form_5)
#   rating_final = COALESCE(sofascore_rating_avg, flashscore_rating_avg)
# Using sofascore_* directly left 20/49 players with NaN FormScore.

FORM_WEIGHTS: dict[str, float] = {
    "form_final":   0.60,   # COALESCE(sofascore_form_5, flashscore_form_5) — 49/49
    "rating_final": 0.40,   # COALESCE(sofascore_rating_avg, flashscore_rating_avg) — 49/49
}  # sum = 1.0 ✓

# ══════════════════════════════════════════════════════════════════════════════
# § 2b — Performance Sub-Weights (CONFLICT-02 fix — moved from performance_score.py)
# ══════════════════════════════════════════════════════════════════════════════
# CONFLICT-02 FIX: these were hardcoded inside compute_performance_score().
# Now centralised here so they can be tuned without touching scoring logic.

PERFORMANCE_SUB_WEIGHTS: dict[str, float] = {
    "offensive_score": 0.50,
    "defensive_score": 0.35,
    "form_score":      0.15,
}  # sum = 1.0 ✓

# ══════════════════════════════════════════════════════════════════════════════
# § 2c — Mental Weights (CONFLICT-03 fix — StatsBomb proxy)
# ══════════════════════════════════════════════════════════════════════════════
# CONFLICT-03 FIX: MentalScore was a ghost (0.10 weight, no computation).
# Now computed from StatsBomb open data (11/49 real, 38/49 imputed position median).
#   sb_pressures_per90 → pressing intensity (work rate proxy)
#   sb_passes_per90    → composure & distribution
#   sb_carries_per90   → ball-carrying / decision-making

MENTAL_WEIGHTS: dict[str, float] = {
    "sb_pressures_per90": 0.50,
    "sb_passes_per90":    0.30,
    "sb_carries_per90":   0.20,
}  # sum = 1.0 ✓

# ══════════════════════════════════════════════════════════════════════════════
# § 3 — Normalization Bounds
# ══════════════════════════════════════════════════════════════════════════════
# Each tuple: (min_value, max_value)
# Values outside bounds are clipped, then normalised to [0, 1].
# Inverse columns (below) are flipped: 0 = worst, 1 = best after inversion.

NORMALIZATION_BOUNDS: dict[str, tuple[float, float]] = {
    # ── Club performance ──────────────────────────────────────────────────────
    "Gls_per90":             (0.0, 1.5),
    "Ast_per90":             (0.0, 1.0),
    "GA_per90":              (0.0, 3.0),   # Inverse — GKs only
    "Err_per90":             (0.0, 1.0),   # Inverse
    # REMOVED: xAG_per90 (no Bronze source), Tkl/Int/Clr_per90 (no Bronze source)

    # ── Understat (ADR-012) ───────────────────────────────────────────────────
    "understat_npxg_per90":  (0.0, 1.2),
    "understat_xa_per90":    (0.0, 0.8),
    "understat_shots_per90": (0.0, 8.0),
    "understat_conversion":  (0.0, 40.0),
    # COALESCE xG/xA columns (understat → sofascore_detail)
    "xg_per90":              (0.0, 1.2),
    "xa_per90":              (0.0, 0.8),
    # Defensive stats from sofascore_detail (GAP 1 resolved)
    "Tkl_per90":             (0.0, 6.0),
    "Int_per90":             (0.0, 5.0),
    "Clr_per90":             (0.0, 8.0),

    # ── SofaScore raw (passthrough — for reference only, not used in scoring)
    "sofascore_rating_avg":    (5.0, 8.5),
    "sofascore_form_5":        (4.0, 9.0),
    "sofascore_matches_rated": (0.0, 38.0),

    # ── FlashScore raw (passthrough — for reference only, not used in scoring)
    "flashscore_rating_avg":    (5.0, 8.5),
    "flashscore_form_5":        (4.0, 9.0),
    "flashscore_matches_rated": (0.0, 38.0),

    # ── COALESCE columns — CONFLICT-01 FIX — used by FormScore ───────────────
    # form_final   = COALESCE(sofascore_form_5,     flashscore_form_5)     49/49
    # rating_final = COALESCE(sofascore_rating_avg, flashscore_rating_avg) 49/49
    "form_final":    (4.0, 9.0),   # same scale as sofascore_form_5
    "rating_final":  (5.0, 8.5),   # same scale as sofascore_rating_avg

    # ── StatsBomb — MentalScore (CONFLICT-03 FIX) ─────────────────────────────
    # 11/49 real data, 38/49 imputed with position-group median in Silver
    "sb_pressures_per90": (0.0, 30.0),   # Kante-level ~25, avg outfield ~12
    "sb_passes_per90":    (0.0, 80.0),   # DM/CB heavy, FWD lower
    "sb_carries_per90":   (0.0, 60.0),
    "sb_xg_per90":        (0.0, 1.0),    # used by PressureIndex

    # ── ChemistryScore — pairwise StatsBomb pass network ──────────────────────
    # 11/49 real (WC2018/2022 + AFCON2023 candidates who shared a match),
    # 38/49 imputed with position-group median in Silver (see silver_pipeline.py
    # join_chemistry_pairs()). Observed real range on 2026-07-14 pull: 1.6-6.5;
    # bounds set with headroom above that ceiling.
    "chem_avg_per90":     (0.0, 8.0),

    # ── PressureIndex — international (StatsBomb) minus club (xg_per90) xG rate.
    # Positive = generates MORE quality chances in big internationals than for
    # their club; negative = drops off under pressure. This is a derived column
    # (not a raw Bronze/Silver field) computed inside pressure_index.py, so its
    # bounds live here for normalize_column()'s reuse but the column itself is
    # only added to the DataFrame at Gold time.
    "pressure_delta":     (-0.5, 0.5),

    # ── FlexibilityScore — Transfermarkt secondary positions (UNTESTED — ────
    # see analytics/scoring/flexibility_score.py docstring) ─────────────────
    # 1 = single position (neutral default), 4 = very versatile (rare).
    "tm_num_positions":   (1.0, 4.0),

    # ── SentimentScore — Reddit + YouTube + NewsAPI, NLP-scored ──────────────
    # NOT normalized via normalize_column() — sentiment_score.py's aggregation
    # (analytics/nlp/sentiment_analyzer.py) already produces a [0,1] value
    # directly (0=negative, 0.5=neutral, 1=positive), so no raw-value bounds
    # are needed here. Listed for documentation completeness only.
    # "sentiment_raw": already in [0, 1] by construction — no bounds needed.

    # ── Injury / Availability (ADR-014) ───────────────────────────────────────
    "inj_count_2y":       (0.0, 10.0),
    "inj_days_missed_2y": (0.0, 365.0),  # FIX B-11: was 180.0 — ACL cases clipped

    # ── International (ADR-015) ───────────────────────────────────────────────
    "intl_caps":          (0.0, 100.0),
    "intl_goals_per90":   (0.0, 1.0),
    "intl_ast_per90":     (0.0, 0.7),
    "intl_minutes_total": (0.0, 5000.0),

    # ── Market / Wikidata ─────────────────────────────────────────────────────
    "market_value_eur":   (0.0, 200_000_000.0),
}

# ══════════════════════════════════════════════════════════════════════════════
# § 4 — Inverse Columns
# ══════════════════════════════════════════════════════════════════════════════
# For these columns, LOWER raw value = BETTER score.
# normalize() flips them: normalised_score = 1 - standard_normalised_value.

INVERSE_COLUMNS: set[str] = {
    "GA_per90",
    "Err_per90",
    "inj_count_2y",       # More injuries = worse
    "inj_days_missed_2y", # More days missed = worse
}

# ══════════════════════════════════════════════════════════════════════════════
# § 5 — Injury Availability Thresholds
# ══════════════════════════════════════════════════════════════════════════════

INJURY_AVAILABILITY_THRESHOLDS: dict[str, int] = {
    "low_risk":    30,    # < 30 days missed in 2 years → stably available
    "medium_risk": 90,    # 30–90 days → monitor closely
    "high_risk":   9999,  # > 90 days → high availability risk (flag to coach)
}

# ══════════════════════════════════════════════════════════════════════════════
# § 6 — Composite Score Weights (Silver → Gold)
# ══════════════════════════════════════════════════════════════════════════════
# These are the top-level DSS score weights fed into the final player ranking.

COMPOSITE_WEIGHTS: dict[str, float] = {
    # REBALANCED 2026-07-22 (third and FINAL pass) to add SentimentScore —
    # completes all 9 components from PROJECT.md's original vision minus
    # nothing (FlexibilityScore was the last gap before this).
    # SentimentScore weight (0.05) matches PROJECT.md's original 8-part
    # formula exactly (the smallest weight in the whole formula — see
    # PROJECT.md's own "priorities if delayed" table, which explicitly
    # marks Sentiment as "🟡 bonus appreciated", not core). The other 8
    # weights are the previous 8-component set (summed to 1.00) scaled
    # down by 0.95 (=1.00-0.05) to make room, again preserving relative
    # ranking rather than re-deriving from data.
    #
    # ⚠️ SentimentScore's entire pipeline (3 new data collectors + 4 NLP
    # models) is UNTESTED end-to-end against live services (see
    # analytics/scoring/sentiment_score.py and each collector's module
    # docstring). Expect this to be the sparsest, least-proven component
    # in the formula until real runs establish actual coverage.
    "PerformanceScore":  0.2562,  # was 0.2697
    "TacticalFitScore":  0.1855,  # was 0.1953
    "FormScore":         0.1502,  # was 0.1581
    "MentalScore":       0.0707,  # was 0.0744
    "AvailabilityScore": 0.0795,  # was 0.0837
    "ChemistryScore":    0.0707,  # was 0.0744
    "PressureIndex":     0.0707,  # was 0.0744
    "FlexibilityScore":  0.0665,  # was 0.07
    "SentimentScore":    0.05,    # NEW
    # Sum = 1.00. This completes PROJECT.md's original 9-part vision
    # (8 weighted sub-scores + FormScore, all present and accounted for).
}  # sum = 1.0 ✓