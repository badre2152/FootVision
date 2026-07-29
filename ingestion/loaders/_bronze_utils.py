# ingestion/loaders/_bronze_utils.py
# Shared helpers for all Bronze loaders.
# Extracted here to avoid copy-paste across understat, sofascore,
# transfermarkt_injury, and fbref_intl loaders (deferred from v17, done in v18).

import logging
import re
import pandas as pd

logger = logging.getLogger(__name__)

# Unicode ranges covering Arabic, Arabic Supplement, and Arabic Extended-A —
# enough to catch Modern Standard Arabic and Darija written in Arabic script.
_ARABIC_CHAR_RE = re.compile(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]')


def validate_seed_columns(seed_df: pd.DataFrame, required: list[str], source: str) -> None:
    """
    FIX C-02 (v17): Guards against silent None returns when seed CSV is missing
    expected columns. Raises ValueError immediately rather than producing empty data.

    Args:
        seed_df:  The seed DataFrame (players_49.csv loaded).
        required: List of column names that must be present.
        source:   Loader name for error messages (e.g. 'understat').

    Raises:
        ValueError: If any required column is missing.
    """
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
    """
    FIX C-08 (v17): Warns when a loader returns data for fewer than min_pct of players.

    An all-empty upload to MinIO is valid JSON but useless - this surfaces the
    problem before Silver silently merges zero new columns.

    A record is considered to "have data" if it contains more than just 'player_id'
    (i.e. len(record) > 1).

    Args:
        records:  List of dicts, one per player.
        min_pct:  Minimum fraction of players expected to have data (default 0.30 = 30%).
        source:   Loader name for log messages.
    """
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
    """
    Checks whether `text` actually discusses the player, not just came
    from a search result that matched somewhere in a larger document.

    Moved here 2026-07-24 to be shared between arctic_shift_collector.py
    (where this fix was first found necessary — a live test showed a
    Reddit megathread post matching "Achraf Hakimi" whose own comments
    were entirely about Messi) and newsapi_collector.py (same class of
    bug found independently: NewsAPI's search matches a player's name
    anywhere in a full article, not necessarily in the title/description
    snippet actually extracted — generic match-preview articles like
    "How to watch France vs Morocco" were showing up in a specific
    player's collected texts).

    Checks for the full name OR just the surname (fans/headlines commonly
    use surname only — "Hakimi was amazing" is common, "Achraf Hakimi was
    amazing" less so in casual text).

    FIX 2026-07-27 (this exact fix was previously written and verified in
    an earlier session, but did not survive into this archive — see
    handoff notes; reapplied 2026-07-29 with one refinement, documented
    below):
    The original version below only ever checked for a Latin-script
    substring match (`player_name` / `surname`). Arabic-script text can
    NEVER contain a Latin string like "Belammari", so this silently
    discarded 100% of Arabic/Darija mentions of every one of the 49
    players — not a coverage gap, a total exclusion. Prior sentiment
    analysis showed Darija text skews more critical/negative than
    English/French coverage, so this bug was making SentimentScore's
    positive bias WORSE, not better, and doing so completely invisibly
    (no error, no warning — `mentions_player` just always returned False
    for Arabic text and the caller silently dropped it as "irrelevant").

    Building a reliable Latin→Arabic transliteration map for 49 players
    (and their common Darija nicknames) is not worth the maintenance
    burden and would itself be a fresh source of silent misses. Instead:
    if the text is *predominantly* Arabic-script, treat it as relevant
    without attempting the Latin-name check — the caller already queried
    a source (Reddit/YouTube/NewsAPI) targeted at this specific player,
    so `mentions_player` for Arabic text here is a coarser but honest
    "don't silently discard on a check we can't actually perform"
    decision rather than a false "confirmed match".

    Refinement over the original fix: relevance is decided by the
    *majority* script of the text (Arabic chars > 50% of alphabetic
    chars), not merely "contains any Arabic character at all" — an
    English-language article that happens to quote one Arabic word/name
    should still go through the normal Latin-name check, not get a free
    pass just because of that one incidental character.
    """
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