# analytics/ml/player_clustering.py
# ML Layer — Player DNA Clustering (K-Means)
#
# Added 2026-07-29. This is the FIRST ML component built for this project —
# nothing under analytics/ml/ existed before this file. Player DNA was
# always in the original PROJECT.md vision (K-Means k=4 -> Warrior / Brain /
# Speedster / Rock) but stayed "Phase 4 — not built" through every session
# up to and including the 2026-07-14 -> 2026-07-29 Gold-completion session.
#
# ─────────────────────────────────────────────────────────────────────────
# WHY THIS MODULE EXISTS, AND WHY THE OTHER TWO ML COMPONENTS DON'T (YET)
# ─────────────────────────────────────────────────────────────────────────
# PROJECT.md's Phase 4 lists three ML models: Player DNA (K-Means),
# Selection Classifier (RF/XGBoost), Injury Predictor (XGBoost).
#
# Only Player DNA is implemented here. The other two are NOT started,
# deliberately, because with n=49 total candidates they are methodologically
# unsound as currently scoped:
#   - Selection Classifier needs a real binary label ("was this player
#     actually selected by the coach, yes/no"). Using DSSScore's own top-26
#     cutoff as the label would just be the classifier re-learning a
#     monotonic function of the score that produced its own labels —
#     circular, not a genuine prediction task. It only becomes meaningful
#     once the real announced squad (the "Option B — historical simulation"
#     held-out ground truth MASTER_SPEC.md § 1 describes) is available as
#     y, and even then a 49-row train/test split needs leave-one-out CV,
#     not a normal split, plus heavy regularization to avoid overfitting.
#   - Injury Predictor needs a time-series injury history per player, not
#     one static two-year aggregate (inj_count_2y, inj_days_missed_2y).
#     Training XGBoost on 49 static rows to predict a scalar risk score
#     would produce a number that LOOKS like a probability but has no real
#     predictive validity — worse than no model, because it invites false
#     confidence in a squad decision.
#
# Player DNA has neither problem: it's unsupervised (no label needed) and
# it's explicitly descriptive/exploratory (a play-style tag for the
# dashboard and coaching staff), not a decision the squad selection leans
# on. That is why it goes first.
#
# ─────────────────────────────────────────────────────────────────────────
# DESIGN NOTES
# ─────────────────────────────────────────────────────────────────────────
# - Goalkeepers are EXCLUDED from the K-Means fit entirely. GK metrics
#   (GA_per90, save%) are structurally incomparable to outfield metrics
#   (goals, tackles, passes) — mixing them would just produce a trivial
#   "is this a goalkeeper" cluster instead of a real play-style signal.
#   Only 6 GK candidates exist anyway, too few to sub-cluster meaningfully.
#   GKs get a fixed archetype label ("Gardien") assigned directly.
# - Features are raw per-90 / rate stats (not the Gold *_norm columns),
#   StandardScaler'd inside this module. Gold's [0,1] min-max normalization
#   is tuned for score aggregation, not for Euclidean-distance clustering;
#   StandardScaler is the standard choice for K-Means.
# - Missing feature values (5-6/43 outfield players per column — see
#   coverage check in this module's own test suite) are filled with the
#   POSITION-GROUP median, same convention as silver_pipeline.py's
#   impute_by_position(), and flagged in `dna_imputed_features_count` so
#   downstream consumers can see how much of a given player's profile is
#   real vs. filled.
# - k is NOT hardcoded to 4. select_k() tests k=3..6 (per ROADMAP.md's own
#   "Tester K=3, 5, 6 -> choisir le meilleur silhouette score" instruction)
#   and returns the best-scoring k plus the full silhouette table, so a
#   different k than 4 is a legitimate outcome, not a bug, if the data
#   genuinely doesn't support the storybook 4-way split. When k does not
#   land on 4, the naming heuristic below still runs (it interprets
#   centroids generically) but the output will use generic
#   "Cluster N" labels beyond the four named archetypes.

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────
# Feature set (raw per-90 / rate columns — see coverage notes above)
# ─────────────────────────────────────────────────────────────────────────
CLUSTER_FEATURES: dict[str, str] = {
    # offensive / creativity
    "Gls_per90":            "goals per 90",
    "Ast_per90":             "assists per 90",
    "xg_per90":              "expected goals per 90",
    "xa_per90":              "expected assists per 90",
    "sd_key_passes_per90":   "key passes per 90",
    "sd_dribbles_per90":     "dribbles per 90",
    # physical / work-rate / duels
    "sb_pressures_per90":    "pressing actions per 90",
    "sd_duels_won_pct":      "duels won %",
    # passing / vision
    "sb_passes_per90":       "passes per 90",
    "sd_pass_acc_pct":       "pass accuracy %",
    "sb_carries_per90":      "ball carries per 90",
    # defensive
    "Tkl_per90":             "tackles per 90",
    "Int_per90":             "interceptions per 90",
    "Clr_per90":             "clearances per 90",
}

# Archetype naming heuristic: for each axis, which features push a centroid
# toward that archetype. Values are z-scored centroid coordinates (after
# StandardScaler), so "high" means above the outfield-player average.
ARCHETYPE_AXES: dict[str, list[str]] = {
    "Warrior":   ["sb_pressures_per90", "sd_duels_won_pct", "Tkl_per90"],
    "Brain":     ["sb_passes_per90", "sd_pass_acc_pct", "sd_key_passes_per90"],
    "Speedster": ["sd_dribbles_per90", "sb_carries_per90", "xg_per90", "Ast_per90"],
    "Rock":      ["Int_per90", "Clr_per90"],
}

ARCHETYPE_EMOJI: dict[str, str] = {
    "Warrior": "🦁", "Brain": "🧠", "Speedster": "⚡", "Rock": "🛡️",
}

GK_LABEL = "Gardien"
MIN_K, MAX_K = 3, 6
RANDOM_STATE = 42


@dataclass
class ClusteringResult:
    df: pd.DataFrame                       # input df + PlayerDNA columns added
    k: int                                  # chosen k
    silhouette_table: dict[int, float]      # k -> silhouette score, all k tried
    centroids: pd.DataFrame                 # k rows x features, z-scored space
    archetype_map: dict[int, str]           # cluster_id -> archetype name
    feature_coverage: dict[str, int]        # feature -> non-null count (outfield only)
    n_outfield: int
    n_gk: int


def _position_median_impute(X: pd.DataFrame, positions: pd.Series) -> tuple[pd.DataFrame, pd.Series]:
    """
    Fills NaN in X with the position-group median (falls back to the global
    median if an entire position group is NaN for a column). Returns the
    filled matrix and a per-row count of how many features were imputed,
    so callers can flag low-confidence profiles.
    """
    X_filled = X.copy()
    imputed_count = pd.Series(0, index=X.index)

    for col in X.columns:
        na_mask = X[col].isna()
        if not na_mask.any():
            continue
        imputed_count.loc[na_mask] += 1
        group_medians = X[col].groupby(positions).transform("median")
        fill_values = group_medians.where(group_medians.notna(), X[col].median())
        X_filled.loc[na_mask, col] = fill_values.loc[na_mask]

    return X_filled, imputed_count


def select_k(
    X_scaled: np.ndarray,
    k_range: tuple[int, int] = (MIN_K, MAX_K),
    random_state: int = RANDOM_STATE,
) -> tuple[int, dict[int, float]]:
    """
    Fits K-Means for every k in [k_range[0], k_range[1]] and returns the k
    with the best silhouette score, plus the full score table so the caller
    can see how decisive (or not) the "best" choice actually is.

    n_samples must exceed k for silhouette_score to be defined; k values
    that don't satisfy this are skipped with a warning rather than crashing.
    """
    n = X_scaled.shape[0]
    scores: dict[int, float] = {}

    for k in range(k_range[0], k_range[1] + 1):
        if k >= n:
            logger.warning(
                "Skipping k=%d: only %d samples available (need k < n_samples).",
                k, n,
            )
            continue
        labels = KMeans(n_clusters=k, random_state=random_state, n_init=10).fit_predict(X_scaled)
        scores[k] = float(silhouette_score(X_scaled, labels))

    if not scores:
        raise ValueError(
            f"No valid k in range {k_range} for {n} samples — need more outfield "
            f"candidates or a narrower k_range."
        )

    best_k = max(scores, key=scores.get)
    return best_k, scores


def _name_archetypes(centroids: pd.DataFrame) -> dict[int, str]:
    """
    Assigns a human-readable archetype name to each cluster centroid.

    For each of the 4 canonical archetypes, scores every centroid by the
    mean z-score across that archetype's defining features, then greedily
    assigns the best-matching centroid to each archetype (without reusing a
    centroid for two archetypes). If k != 4, or k < 4, some clusters end up
    unnamed and fall back to "Cluster N"; if k > 4, the remaining clusters
    beyond the 4 archetypes also fall back to "Cluster N". This is reported
    plainly rather than forced, per this module's header note on k.
    """
    archetype_scores = pd.DataFrame(
        {
            name: centroids[[f for f in feats if f in centroids.columns]].mean(axis=1)
            for name, feats in ARCHETYPE_AXES.items()
        }
    )

    archetype_map: dict[int, str] = {}
    assigned_clusters: set[int] = set()
    assigned_archetypes: set[str] = set()

    # Greedy assignment: repeatedly pick the single best remaining
    # (archetype, cluster) pair until either side runs out.
    remaining = archetype_scores.copy()
    while len(assigned_archetypes) < len(ARCHETYPE_AXES) and len(assigned_clusters) < len(centroids):
        # mask out already-assigned rows/cols
        masked = remaining.copy()
        masked.loc[list(assigned_clusters)] = -np.inf
        masked[list(assigned_archetypes)] = -np.inf
        if not np.isfinite(masked.values).any():
            break
        cluster_id, archetype = np.unravel_index(np.argmax(masked.values), masked.shape)
        cluster_id = masked.index[cluster_id]
        archetype = masked.columns[archetype]
        archetype_map[cluster_id] = archetype
        assigned_clusters.add(cluster_id)
        assigned_archetypes.add(archetype)

    for cluster_id in centroids.index:
        if cluster_id not in archetype_map:
            archetype_map[cluster_id] = f"Cluster {cluster_id}"

    return archetype_map


def cluster_players(
    df: pd.DataFrame,
    k: int | None = None,
    k_range: tuple[int, int] = (MIN_K, MAX_K),
    random_state: int = RANDOM_STATE,
) -> ClusteringResult:
    """
    Main entrypoint. Clusters OUTFIELD players only (position != "GK") into
    play-style archetypes via K-Means on standardized per-90 features.

    Args:
        df: Gold/Silver-level DataFrame — must have a "position" column and
            the raw feature columns listed in CLUSTER_FEATURES (missing
            columns are dropped from the feature set with a warning, not
            a crash — the module degrades gracefully with fewer features
            rather than failing outright).
        k: fixed cluster count. If None (default), select_k() picks the
           best k in k_range by silhouette score.
        k_range: range searched by select_k() when k is None.
        random_state: KMeans/reproducibility seed.

    Returns:
        ClusteringResult — see dataclass above. df has two new columns:
          - "player_dna_cluster": int cluster id (NaN for GKs)
          - "PlayerDNA": archetype label ("Gardien" for GKs, else the
            named/generic cluster label)
          - "dna_imputed_features_count": how many of this player's
            clustering features were position-median-imputed (0 for GKs,
            who aren't clustered)
    """
    if "position" not in df.columns:
        raise ValueError("cluster_players() requires a 'position' column.")

    available_features = [c for c in CLUSTER_FEATURES if c in df.columns]
    missing_features = [c for c in CLUSTER_FEATURES if c not in df.columns]
    if missing_features:
        logger.warning(
            "cluster_players(): %d/%d expected features missing from input df, "
            "proceeding with the rest: %s",
            len(missing_features), len(CLUSTER_FEATURES), missing_features,
        )
    if len(available_features) < 4:
        raise ValueError(
            f"Only {len(available_features)} usable features found — too few for "
            f"meaningful clustering. Check that the Gold/Silver columns line up "
            f"with CLUSTER_FEATURES."
        )

    result_df = df.copy()
    result_df["player_dna_cluster"] = np.nan
    result_df["PlayerDNA"] = GK_LABEL
    result_df["dna_imputed_features_count"] = 0

    is_gk = result_df["position"] == "GK"
    outfield_idx = result_df.index[~is_gk]
    n_gk = int(is_gk.sum())
    n_outfield = len(outfield_idx)

    if n_outfield < MIN_K + 1:
        raise ValueError(
            f"Only {n_outfield} outfield players — need at least {MIN_K + 1} for "
            f"k>={MIN_K} clustering to be meaningful."
        )

    X_raw = result_df.loc[outfield_idx, available_features]
    feature_coverage = X_raw.notna().sum().to_dict()

    X_filled, imputed_count = _position_median_impute(
        X_raw, result_df.loc[outfield_idx, "position"]
    )
    result_df.loc[outfield_idx, "dna_imputed_features_count"] = imputed_count.values

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_filled.values)

    if k is None:
        best_k, silhouette_table = select_k(X_scaled, k_range=k_range, random_state=random_state)
    else:
        best_k = k
        silhouette_table = {}

    kmeans = KMeans(n_clusters=best_k, random_state=random_state, n_init=10)
    labels = kmeans.fit_predict(X_scaled)
    result_df.loc[outfield_idx, "player_dna_cluster"] = labels

    centroids = pd.DataFrame(
        kmeans.cluster_centers_, columns=available_features,
        index=pd.RangeIndex(best_k, name="cluster_id"),
    )
    archetype_map = _name_archetypes(centroids)

    result_df.loc[outfield_idx, "PlayerDNA"] = (
        result_df.loc[outfield_idx, "player_dna_cluster"].map(archetype_map)
    )

    logger.info(
        "Player DNA: k=%d clusters over %d outfield players (%d GK excluded). "
        "Archetypes: %s",
        best_k, n_outfield, n_gk, archetype_map,
    )

    return ClusteringResult(
        df=result_df,
        k=best_k,
        silhouette_table=silhouette_table,
        centroids=centroids,
        archetype_map=archetype_map,
        feature_coverage=feature_coverage,
        n_outfield=n_outfield,
        n_gk=n_gk,
    )
