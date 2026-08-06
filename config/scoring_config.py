# config.py — Football DSS v18
# Centralised weights, normalization bounds, and scoring configuration.
# All Silver transforms and scoring functions read from here — never hardcode values.

from datetime import date

# ── Decision date (temporal cutoff) ─────────────────────────────────────────
DECISION_DATE: date = date(2026, 5, 25)

# ── Offensive Weights (PerformanceScore subscore) ───────────────────────────
OFFENSIVE_WEIGHTS: dict[str, float] = {
    "Gls_per90": 0.35,
    "Ast_per90": 0.25,
    "xg_per90":  0.25,
    "xa_per90":  0.15,
}  # sum = 1.0

DEFENSIVE_WEIGHTS: dict[str, float] = {
    "Tkl_per90": 0.35,
    "Int_per90": 0.30,
    "Clr_per90": 0.10,
    "GA_per90":  0.25,   # GKs only (inverse)
}  # sum = 1.0

# ── FormScore Weights ────────────────────────────────────────────────────────
FORM_WEIGHTS: dict[str, float] = {
    "form_final":   0.60,
    "rating_final": 0.40,
}  # sum = 1.0

# ── Performance Sub-Weights ──────────────────────────────────────────────────
PERFORMANCE_SUB_WEIGHTS: dict[str, float] = {
    "offensive_score": 0.50,
    "defensive_score": 0.35,
    "form_score":      0.15,
}  # sum = 1.0

# ── Mental Weights (StatsBomb proxy) ─────────────────────────────────────────
MENTAL_WEIGHTS: dict[str, float] = {
    "sb_pressures_per90": 0.50,
    "sb_passes_per90":    0.30,
    "sb_carries_per90":   0.20,
}  # sum = 1.0

# ── Normalization Bounds ─────────────────────────────────────────────────────
# Each tuple: (min_value, max_value). Values outside bounds are clipped, then
# normalised to [0, 1]. Inverse columns are flipped after normalisation.

NORMALIZATION_BOUNDS: dict[str, tuple[float, float]] = {
    # Club performance
    "Gls_per90":             (0.0, 1.5),
    "Ast_per90":             (0.0, 1.0),
    "GA_per90":              (0.0, 3.0),   # Inverse — GKs only
    "Err_per90":             (0.0, 1.0),   # Inverse

    # Understat
    "understat_npxg_per90":  (0.0, 1.2),
    "understat_xa_per90":    (0.0, 0.8),
    "understat_shots_per90": (0.0, 8.0),
    "understat_conversion":  (0.0, 40.0),
    "xg_per90":              (0.0, 1.2),
    "xa_per90":              (0.0, 0.8),
    "Tkl_per90":             (0.0, 6.0),
    "Int_per90":             (0.0, 5.0),
    "Clr_per90":             (0.0, 8.0),

    # SofaScore raw (reference only, not used in scoring)
    "sofascore_rating_avg":    (5.0, 8.5),
    "sofascore_form_5":        (4.0, 9.0),
    "sofascore_matches_rated": (0.0, 38.0),

    # FlashScore raw (reference only, not used in scoring)
    "flashscore_rating_avg":    (5.0, 8.5),
    "flashscore_form_5":        (4.0, 9.0),
    "flashscore_matches_rated": (0.0, 38.0),

    # COALESCE columns used by FormScore
    "form_final":    (4.0, 9.0),
    "rating_final":  (5.0, 8.5),

    # StatsBomb — MentalScore
    "sb_pressures_per90": (0.0, 30.0),
    "sb_passes_per90":    (0.0, 80.0),
    "sb_carries_per90":   (0.0, 60.0),
    "sb_xg_per90":        (0.0, 1.0),   # used by PressureIndex

    # ChemistryScore — pairwise StatsBomb pass network
    "chem_avg_per90":     (0.0, 8.0),

    # PressureIndex — derived column (added to DataFrame at Gold time)
    "pressure_delta":     (-0.5, 0.5),

    # FlexibilityScore — Transfermarkt secondary positions
    "tm_num_positions":   (1.0, 4.0),

    # SentimentScore — already in [0, 1] by construction, no bounds needed

    # Injury / Availability
    "inj_count_2y":       (0.0, 10.0),
    "inj_days_missed_2y": (0.0, 365.0),

    # International
    "intl_caps":          (0.0, 100.0),
    "intl_goals_per90":   (0.0, 1.0),
    "intl_ast_per90":     (0.0, 0.7),
    "intl_minutes_total": (0.0, 5000.0),

    # Market / Wikidata
    "market_value_eur":   (0.0, 200_000_000.0),
}

# ── Inverse Columns ───────────────────────────────────────────────────────────
# Lower raw value = better score. normalize() flips these.

INVERSE_COLUMNS: set[str] = {
    "GA_per90",
    "Err_per90",
    "inj_count_2y",
    "inj_days_missed_2y",
}

# ── Injury Availability Thresholds ───────────────────────────────────────────

INJURY_AVAILABILITY_THRESHOLDS: dict[str, int] = {
    "low_risk":    30,
    "medium_risk": 90,
    "high_risk":   9999,
}

# ── Composite Score Weights (Silver → Gold) ──────────────────────────────────

COMPOSITE_WEIGHTS: dict[str, float] = {
    "PerformanceScore":  0.2562,
    "TacticalFitScore":  0.1855,
    "FormScore":         0.1502,
    "MentalScore":       0.0707,
    "AvailabilityScore": 0.0795,
    "ChemistryScore":    0.0707,
    "PressureIndex":     0.0707,
    "FlexibilityScore":  0.0665,
    "SentimentScore":    0.05,
}  # sum = 1.0