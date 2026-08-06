import logging
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _impute_sentiment(df: pd.DataFrame) -> pd.Series:
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
                sentiment.loc[pos_mask] = 0.5
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