# analytics/scoring/sentiment_score.py
# Gold Layer — SentimentScore Computation
#
# Added 2026-07-22 (Sentiment session). Was the last of PROJECT.md's
# original 8 components to be built — explicitly deferred earlier this
# session (2026-07-14) because zero infrastructure existed. Now built on
# top of ingestion/loaders/reddit_collector.py, youtube_collector.py,
# newsapi_collector.py (raw text), and analytics/nlp/sentiment_analyzer.py
# (language routing + scoring), combined at Silver by
# silver_pipeline.py::join_sentiment_texts().
#
# ⚠️ TESTING STATUS ⚠️
# This module's own logic (imputation, column handling) is unit-tested
# with a mocked sentiment_analyzer. The FULL chain — real Reddit/YouTube/
# NewsAPI data flowing through real transformer models — has never run
# end-to-end, since none of those pieces could be tested against live
# services/models from the sandbox this was built in. See each
# component's own module docstring for exactly what is/isn't verified.
#
# EXPECTED COVERAGE: likely the SPARSEST component in the whole formula.
# Unlike ChemistryScore (StatsBomb's match list was known in advance,
# so 11/49 coverage was predictable before running anything), social/news
# discussion volume for a 49-player pool — many playing in Botola Pro,
# Saudi Pro League, etc. — is NOT knowable in advance. Do not be
# surprised if real coverage is well under 50%; that's realistic, not a
# bug. The imputation strategy below (position-group median, same
# convention as MentalScore/ChemistryScore) handles this gracefully
# either way.

import logging
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _impute_sentiment(df: pd.DataFrame) -> pd.Series:
    """
    Position-group median imputation for sentiment_raw, with a global
    median fallback if an entire position group has zero real data
    (mirrors pressure_index.py's _impute_pressure_delta exactly — same
    fallback structure, different column).
    """
    sentiment = df["sentiment_raw"].copy()
    if "position" not in df.columns:
        logger.error("No 'position' column — cannot group-impute sentiment_raw.")
        return sentiment

    global_median = sentiment.median()

    for pos in df["position"].unique():
        pos_mask = df["position"] == pos
        null_before = sentiment[pos_mask].isna().sum()
        if null_before == 0:
            continue

        group_median = sentiment[pos_mask].median()
        if pd.isna(group_median):
            if pd.isna(global_median):
                logger.warning(
                    "sentiment_raw: NO real data anywhere in the entire 49-player "
                    "pool — SentimentScore will be all-neutral (0.5) this run. "
                    "This means Reddit+YouTube+NewsAPI collectively returned "
                    "zero usable text — check each collector's status output."
                )
                sentiment.loc[pos_mask] = 0.5  # neutral, not left NaN
                continue
            logger.warning(
                "sentiment_raw: position '%s' has zero real data — using "
                "GLOBAL median (%.4f) instead of position median.",
                pos, global_median,
            )
            group_median = global_median

        fill_mask = pos_mask & sentiment.isna()
        sentiment.loc[fill_mask] = group_median
        logger.info(
            "Imputed sentiment_raw [%s]: %d players -> %.4f (group/global median)",
            pos, fill_mask.sum(), group_median,
        )

    return sentiment


def compute_sentiment_score(df: pd.DataFrame, use_nlp: bool = True) -> pd.DataFrame:
    """
    Computes SentimentScore from the combined Reddit/YouTube/NewsAPI text
    (df["sentiment_texts"], a list-per-player column from Silver's
    join_sentiment_texts()).

    Args:
        df: Silver DataFrame with a "sentiment_texts" column.
        use_nlp: If False, skips actual model inference entirely and
            treats every player as unscored (falls straight to
            imputation -> neutral 0.5 baseline). Useful for fast
            iteration on the REST of the Gold pipeline without waiting
            on 4 transformer models to load — NOT a substitute for
            actually running the real scoring before trusting results.

    Returns:
        DataFrame with added "sentiment_raw" (pre-imputation, kept for
        transparency) and "SentimentScore" (final, imputed, in [0,1]) columns.
    """
    if "sentiment_texts" not in df.columns:
        logger.error(
            "sentiment_texts column missing entirely — SentimentScore will "
            "default to neutral (0.5) for everyone. Check that "
            "join_sentiment_texts() ran in silver_pipeline.py."
        )
        df["sentiment_raw"] = np.nan
        df["SentimentScore"] = 0.5
        return df

    if not use_nlp:
        logger.warning(
            "compute_sentiment_score(use_nlp=False) — skipping real NLP scoring "
            "entirely. Every player will be imputed to neutral. Do not use this "
            "mode for a real squad-selection run."
        )
        df["sentiment_raw"] = np.nan
        df["SentimentScore"] = _impute_sentiment(df).round(4)
        return df

    from analytics.nlp.sentiment_analyzer import aggregate_player_sentiment

    raw_scores = []
    real_data_count = 0
    for texts in df["sentiment_texts"]:
        result = aggregate_player_sentiment(texts if isinstance(texts, list) else [])
        raw_scores.append(result["sentiment_raw"])
        if result["sentiment_raw"] is not None:
            real_data_count += 1

    df["sentiment_raw"] = raw_scores
    df["SentimentScore"] = _impute_sentiment(df).round(4)

    logger.info(
        "SentimentScore computed for %d/%d players (%d with real scored text, "
        "%d position-median/neutral imputed).",
        df["SentimentScore"].notna().sum(), len(df),
        real_data_count, len(df) - real_data_count,
    )
    return df
