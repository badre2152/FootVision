import logging
import re

logger = logging.getLogger(__name__)

MODEL_IDS = {
    "arabic":       "Ammar-alhaj-ali/arabic-MARBERT-sentiment",
    "darija":       "BenhamdaneNawfal/sentiment-analysis-darija",
    "french":       "cmarkea/distilcamembert-base-sentiment",
    "multilingual": "lxyuan/distilbert-base-multilingual-cased-sentiments-student",
}

_model_cache: dict = {}


def _get_model(model_key: str):
    if model_key not in MODEL_IDS:
        raise ValueError(f"Unknown model_key: {model_key}")

    if model_key not in _model_cache:
        from transformers import pipeline
        logger.info("Loading sentiment model '%s' (%s)...", model_key, MODEL_IDS[model_key])
        _model_cache[model_key] = pipeline("text-classification", model=MODEL_IDS[model_key])

    return _model_cache[model_key]

DARIJA_KEYWORDS = {
    "بزاف", "ديال", "واخا", "زعما", "دابا", "غادي", "كيفاش", "شنو",
    "بغيت", "خاصني", "ماشي", "حيت", "فين", "علاش", "بحال",
}


def detect_language(text: str) -> str:
    text = (text or "").strip()
    if len(text) < 3:
        return "unknown"

    try:
        from langdetect import detect, DetectorFactory
        DetectorFactory.seed = 0
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
    return any(kw in text for kw in DARIJA_KEYWORDS)

_STAR_PATTERN = re.compile(r"(\d)\s*star", re.IGNORECASE)


def normalize_label(label: str, confidence: float) -> float | None:
    label_lower = label.lower().strip()

    star_match = _STAR_PATTERN.match(label_lower)
    if star_match:
        star_num = int(star_match.group(1))
        if 1 <= star_num <= 5:
            return (star_num - 1) / 4.0

    if "positive" in label_lower or "pos" == label_lower:
        return 0.5 + 0.5 * confidence
    if "negative" in label_lower or "neg" == label_lower:
        return 0.5 - 0.5 * confidence
    if "neutral" in label_lower or "neu" == label_lower:
        return 0.5

    if label_lower in ("label_0", "label_1"):
        logger.warning(
            "normalize_label: got raw '%s' — assuming LABEL_0=negative/LABEL_1=positive "
            "(UNVERIFIED assumption for this specific model, check on first real run).",
            label,
        )
        return (0.5 - 0.5 * confidence) if label_lower == "label_0" else (0.5 + 0.5 * confidence)

    logger.warning("normalize_label: unrecognized label '%s' — cannot normalize.", label)
    return None


def score_text(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        return {"normalized_score": None, "language": "unknown", "model_used": None}

    language = detect_language(text)

    if language == "ar":
        model_key = "darija" if is_likely_darija(text) else "arabic"
    elif language == "fr":
        model_key = "french"
    else:
        model_key = "multilingual"

    try:
        model = _get_model(model_key)
        safe_text = text[:2000]
        result = model(safe_text, truncation=True, max_length=512)[0]
    except Exception as e:
        logger.warning("Sentiment scoring failed (model=%s): %s", model_key, e)
        return {"normalized_score": None, "language": language, "model_used": model_key}

    normalized = normalize_label(result["label"], result["score"])
    return {"normalized_score": normalized, "language": language, "model_used": model_key}


def aggregate_player_sentiment(texts: list[str]) -> dict:
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