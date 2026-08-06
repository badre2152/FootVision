import logging
import re
import pandas as pd

logger = logging.getLogger(__name__)

_ARABIC_CHAR_RE = re.compile(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]')


def validate_seed_columns(seed_df: pd.DataFrame, required: list[str], source: str) -> None:
    missing = [c for c in required if c not in seed_df.columns]
    if missing:
        raise ValueError(
            f"[{source}] seed_df is missing required columns: {missing}. "
            "Check players_49.csv schema."
        )


def assert_min_coverage(
    records: list[dict],
    min_pct: float = 0.30,
    source: str = "",
) -> None:
    if not records:
        logger.warning("[%s] No records at all - upload will be empty.", source)
        return

    ok  = sum(1 for r in records if len(r) > 1)
    pct = ok / len(records)

    if pct < min_pct:
        logger.warning(
            "[%s] Low coverage: only %d/%d players (%.0f%%) have data. "
            "Expected >= %.0f%%. Check IDs or source availability.",
            source, ok, len(records), pct * 100, min_pct * 100,
        )
    else:
        logger.info(
            "[%s] Coverage OK: %d/%d players (%.0f%%).",
            source, ok, len(records), pct * 100,
        )


def mentions_player(text: str, player_name: str) -> bool:
    if not text:
        return False

    arabic_chars = len(_ARABIC_CHAR_RE.findall(text))
    alphabetic_chars = sum(1 for ch in text if ch.isalpha())
    if alphabetic_chars > 0 and (arabic_chars / alphabetic_chars) > 0.5:
        return True

    text_lower = text.lower()
    name_parts = player_name.lower().split()
    surname = name_parts[-1] if name_parts else player_name.lower()
    return player_name.lower() in text_lower or surname in text_lower