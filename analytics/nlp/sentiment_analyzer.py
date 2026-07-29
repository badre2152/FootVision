# analytics/nlp/sentiment_analyzer.py
# SentimentScore — NLP pipeline
#
# Added 2026-07-22 (Sentiment session). Routes each collected text
# (Reddit/YouTube/NewsAPI) to a language-appropriate HuggingFace model,
# normalizes each model's own label scheme to a common [0, 1] scale, and
# aggregates per player.
#
# ⚠️ WHAT IS AND ISN'T TESTED ⚠️
# - Language detection (langdetect): tested, runs offline, no models needed.
# - Darija-keyword heuristic: tested (pure string logic).
# - Label normalization (star-rating vs positive/negative/neutral vs
#   binary schemes -> unified [0,1]): tested exhaustively against every
#   known label string from all 4 models.
# - Per-player aggregation: tested.
# - THE ACTUAL MODEL INFERENCE ITSELF: NOT tested. huggingface.co is not
#   reachable from the sandbox this was written in, and no GPU/torch
#   runtime was available to even load a model locally. Every call in
#   this file that touches a real `transformers.pipeline(...)` object has
#   never executed against a real model. The exact repo IDs below WERE
#   verified to exist via web search (2026-07-22) — that is NOT the same
#   as confirming they load correctly or that the label strings documented
#   online exactly match what you'll get back. VERIFY on first real run,
#   see the checklist at the bottom of this docstring.
#
# MODELS USED (decided with the project owner 2026-07-22):
#   Arabic (default, incl. Darija written in Arabic script):
#       Ammar-alhaj-ali/arabic-MARBERT-sentiment
#       Labels (per model card): "positive" / "negative" / "neutral"
#   Darija (optional refinement, only for text matching the Darija
#   keyword heuristic below — community fine-tune, less established than
#   an official release, use with appropriate skepticism):
#       BenhamdaneNawfal/sentiment-analysis-darija
#       Labels: binary, exact strings NOT verified — defensive parsing below.
#   French:
#       cmarkea/distilcamembert-base-sentiment
#       Labels: "1 star".."5 stars" (5-point scale, NOT positive/negative)
#   Fallback (English + anything else, added 2026-07-22 after confirming
#   the original 3-model plan had zero English coverage despite English
#   being common in the collected Reddit/YouTube/NewsAPI text):
#       lxyuan/distilbert-base-multilingual-cased-sentiments-student
#       Labels: "positive" / "negative" / "neutral" (assumed from name —
#       NOT verified against a live call)
#
# BEFORE TRUSTING THIS IN A REAL RUN:
#   1. pip install transformers torch langdetect
#   2. Run score_text() on 5-10 hand-picked sentences PER LANGUAGE with
#      OBVIOUS sentiment ("this player is terrible" / "amazing goal!" /
#      French and Arabic equivalents) and manually confirm the returned
#      normalized_score is on the correct side of 0.5.
#   3. Specifically check BenhamdaneNawfal/sentiment-analysis-darija's
#      actual label strings (print raw model output) — this file's
#      _normalize_label() has a generic fallback for unrecognized labels
#      that logs a WARNING rather than silently mis-scoring, so check
#      logs for that warning on first run.
#   4. Loading 4 transformer models is memory-heavy — if running on a
#      machine without much RAM, consider lazy-loading only the models
#      actually needed (this module already does this — see _get_model()
#      — but 4 loaded simultaneously is still a real footprint).

import logging
import re

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Model registry — lazy-loaded, cached after first use
# ─────────────────────────────────────────────────────────────────────────────

MODEL_IDS = {
    "arabic":       "Ammar-alhaj-ali/arabic-MARBERT-sentiment",
    "darija":       "BenhamdaneNawfal/sentiment-analysis-darija",
    "french":       "cmarkea/distilcamembert-base-sentiment",
    "multilingual": "lxyuan/distilbert-base-multilingual-cased-sentiments-student",
}

_model_cache: dict = {}


def _get_model(model_key: str):
    """
    Lazy-loads and caches a transformers pipeline by key. Only downloads/
    loads a model the FIRST time it's actually needed for some text (e.g.
    if no French text is ever seen, cmarkea/distilcamembert-base-sentiment
    is never loaded) — avoids paying the load cost for unused languages.
    """
    if model_key not in MODEL_IDS:
        raise ValueError(f"Unknown model_key: {model_key}")

    if model_key not in _model_cache:
        from transformers import pipeline  # deferred import — transformers/torch
        # are heavy; only pay the import cost if sentiment scoring is
        # actually invoked, not just for importing this module.
        logger.info("Loading sentiment model '%s' (%s)...", model_key, MODEL_IDS[model_key])
        _model_cache[model_key] = pipeline("text-classification", model=MODEL_IDS[model_key])

    return _model_cache[model_key]


# ─────────────────────────────────────────────────────────────────────────────
# Language detection + Darija heuristic
# ─────────────────────────────────────────────────────────────────────────────

# Words that are near-exclusively Darija (Moroccan dialectal Arabic) and
# rarely/never appear in Modern Standard Arabic news/formal writing. This
# is a HEURISTIC, not a rigorous dialect classifier — false negatives
# (real Darija text that happens not to use these specific words) will
# just fall through to the MARBERT default, which is a safe fallback,
# not a crash.
DARIJA_KEYWORDS = {
    "بزاف", "ديال", "واخا", "زعما", "دابا", "غادي", "كيفاش", "شنو",
    "بغيت", "خاصني", "ماشي", "حيت", "فين", "علاش", "بحال",
}


def detect_language(text: str) -> str:
    """
    Returns a coarse language code: "ar", "fr", "en", or "unknown".

    Uses langdetect (pure statistical, no network/model download needed —
    confirmed working offline). Short or ambiguous text can raise
    LangDetectException — caught and mapped to "unknown" rather than
    propagating, since a single unparseable text shouldn't crash a
    49-player batch job.
    """
    text = (text or "").strip()
    if len(text) < 3:
        return "unknown"

    try:
        from langdetect import detect, DetectorFactory
        DetectorFactory.seed = 0  # deterministic results (langdetect is otherwise randomized)
        lang = detect(text)
    except Exception as e:
        logger.debug("Language detection failed for text (len=%d): %s", len(text), e)
        return "unknown"

    if lang == "ar":
        return "ar"
    if lang == "fr":
        return "fr"
    if lang == "en":
        return "en"
    return "unknown"


def is_likely_darija(text: str) -> bool:
    """
    Heuristic: does this Arabic-script text contain a Darija-specific
    keyword? Only meaningful to call on text already detected as "ar" —
    doesn't itself check language.
    """
    return any(kw in text for kw in DARIJA_KEYWORDS)


# ─────────────────────────────────────────────────────────────────────────────
# Label normalization — every model's own label scheme -> unified [0, 1]
# ─────────────────────────────────────────────────────────────────────────────

_STAR_PATTERN = re.compile(r"(\d)\s*star", re.IGNORECASE)


def normalize_label(label: str, confidence: float) -> float | None:
    """
    Converts a model's raw (label, confidence) output into a single value
    in [0, 1], where 0.0 = strongly negative, 0.5 = neutral, 1.0 =
    strongly positive — regardless of which of the 4 models produced it.

    Returns None (not a default 0.5) for a label string this function
    truly doesn't recognize, so the caller can log/count "unparseable"
    outputs distinctly from genuinely neutral ones — important for
    catching a label-scheme mismatch (e.g. if BenhamdaneNawfal's real
    labels turn out to be "LABEL_0"/"LABEL_1" instead of "positive"/
    "negative", this makes that failure visible instead of silently
    treating every Darija text as neutral).

    Args:
        label:      Raw label string from the model (e.g. "positive", "3 stars").
        confidence: The model's own confidence/probability for that label, 0-1.
    """
    label_lower = label.lower().strip()

    star_match = _STAR_PATTERN.match(label_lower)
    if star_match:
        star_num = int(star_match.group(1))
        if 1 <= star_num <= 5:
            return (star_num - 1) / 4.0  # 1 star->0.0, 3 stars->0.5, 5 stars->1.0

    if "positive" in label_lower or "pos" == label_lower:
        return 0.5 + 0.5 * confidence
    if "negative" in label_lower or "neg" == label_lower:
        return 0.5 - 0.5 * confidence
    if "neutral" in label_lower or "neu" == label_lower:
        return 0.5

    # Binary LABEL_0/LABEL_1 scheme (common default when a model card
    # doesn't set human-readable label names) — LABEL_0 is conventionally
    # negative, LABEL_1 positive, but THIS IS AN ASSUMPTION, not verified
    # for BenhamdaneNawfal's model specifically. Flagged via the None
    # return path's caller-side logging instead of silently guessing here.
    if label_lower in ("label_0", "label_1"):
        logger.warning(
            "normalize_label: got raw '%s' — assuming LABEL_0=negative/LABEL_1=positive "
            "(UNVERIFIED assumption for this specific model, check on first real run).",
            label,
        )
        return (0.5 - 0.5 * confidence) if label_lower == "label_0" else (0.5 + 0.5 * confidence)

    logger.warning("normalize_label: unrecognized label '%s' — cannot normalize.", label)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Per-text scoring
# ─────────────────────────────────────────────────────────────────────────────

def score_text(text: str) -> dict:
    """
    Scores a single piece of text: detects language, routes to the
    appropriate model, normalizes the result.

    Returns:
        {
            "normalized_score": float in [0,1] or None if scoring failed,
            "language": "ar" | "fr" | "en" | "unknown",
            "model_used": one of MODEL_IDS' keys, or None,
        }
    """
    text = (text or "").strip()
    if not text:
        return {"normalized_score": None, "language": "unknown", "model_used": None}

    language = detect_language(text)

    if language == "ar":
        model_key = "darija" if is_likely_darija(text) else "arabic"
    elif language == "fr":
        model_key = "french"
    else:
        # "en" and "unknown" both go to the multilingual fallback — see
        # module docstring for why English needed a 4th model added.
        model_key = "multilingual"

    try:
        model = _get_model(model_key)
        # FIX (found via live run 2026-07-24): truncation=True alone was
        # NOT enough — a real error occurred: "The size of tensor a (733)
        # must match the size of tensor b (512)". Some tokenizers don't
        # reliably auto-detect the underlying model's true max position
        # embeddings from truncation=True alone. Two layers of defense:
        # (1) explicit max_length=512 passed to the pipeline call, and
        # (2) a blunt character-level pre-truncation as a safety net that
        # doesn't depend on any tokenizer's internal behavior at all.
        # Long Reddit/YouTube comments are common enough (a 733-token text
        # is roughly 2900+ characters) that this isn't a rare edge case —
        # expect it to matter on every real run with unfiltered social text.
        safe_text = text[:2000]
        result = model(safe_text, truncation=True, max_length=512)[0]
    except Exception as e:
        logger.warning("Sentiment scoring failed (model=%s): %s", model_key, e)
        return {"normalized_score": None, "language": language, "model_used": model_key}

    normalized = normalize_label(result["label"], result["score"])
    return {"normalized_score": normalized, "language": language, "model_used": model_key}


# ─────────────────────────────────────────────────────────────────────────────
# Per-player aggregation
# ─────────────────────────────────────────────────────────────────────────────

def aggregate_player_sentiment(texts: list[str]) -> dict:
    """
    Scores every text for one player and aggregates to a single value.

    Returns:
        {
            "sentiment_raw": float in [0,1] or None if no text scored successfully,
            "text_count": total texts passed in,
            "scored_count": how many actually produced a normalized score,
            "language_breakdown": {"ar": N, "fr": N, "en": N, "unknown": N},
        }
    """
    if not texts:
        return {"sentiment_raw": None, "text_count": 0, "scored_count": 0, "language_breakdown": {}}

    scores = []
    language_breakdown: dict = {}

    for text in texts:
        result = score_text(text)
        language_breakdown[result["language"]] = language_breakdown.get(result["language"], 0) + 1
        if result["normalized_score"] is not None:
            scores.append(result["normalized_score"])

    sentiment_raw = sum(scores) / len(scores) if scores else None

    return {
        "sentiment_raw": sentiment_raw,
        "text_count": len(texts),
        "scored_count": len(scores),
        "language_breakdown": language_breakdown,
    }