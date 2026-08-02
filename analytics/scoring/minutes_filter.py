# analytics/scoring/minutes_filter.py
#
# CRITICAL FIX: لاعب مُصاب أو غائب في موسم 2025/26 يحصل على DSS مرتفع
# بناءً على بيانات تاريخية (مثال: Imran Louza Rank#4 رغم ACL injury).
#
# الحل: منطق متعدد المستويات يُطبَّق على AvailabilityScore قبل حساب DSSScore.
#
# مستويات الـ flag (من الأسوأ للأفضل):
#   SEED_INACTIVE        → seed active_2526='no'       → ×0.20 (override قاطع)
#   INACTIVE             → fbref/fm < 270 دقيقة        → ×0.35
#   LIMITED              → fbref/fm 270–540 دقيقة      → ×0.70
#   SEED_UNKNOWN         → seed active_2526='unknown'   → ×0.75
#   UNVERIFIED           → لا بيانات موثوقة            → ×0.85
#   ACTIVE               → تأكيد من مصدر موثوق         → ×1.00

import logging
import pandas as pd

logger = logging.getLogger(__name__)

MIN_ACTIVE  = 270   # 3 مباريات كاملة
MIN_REGULAR = 540   # 6 مباريات

PENALTIES: dict[str, float] = {
    "SEED_INACTIVE": 0.20,   # override من seed — قاطع
    "INACTIVE":      0.35,   # < 270 دقيقة موثوقة
    "LIMITED":       0.70,   # 270–540 دقيقة
    "SEED_UNKNOWN":  0.75,   # seed يقول unknown
    "UNVERIFIED":    0.85,   # لا مصادر موثوقة
    "ACTIVE":        1.00,   # نشط مؤكد
}


def _get_reliable_minutes(row: pd.Series) -> tuple[float | None, str | None]:
    """
    يُعيد أول قيمة موثوقة لدقائق موسم 2025/26.
    المصادر الموثوقة: fm_min, fbref_minutes, csv_min فقط.
    sd_min_2526 مُستبعد لأنه تاريخي مجمّع لبعض اللاعبين.
    """
    for col in ("fm_min", "fbref_minutes", "csv_min"):
        val = row.get(col)
        if val is not None and not pd.isna(val) and float(val) > 0:
            return float(val), col
    return None, None


def _classify(row: pd.Series) -> str:
    """
    يُصنّف نشاط اللاعب في موسم 2025/26.
    أولوية: seed override > بيانات موثوقة > unverified.
    """
    # ── Level 1: Seed override (قاطع) ─────────────────────────────────
    seed_flag = str(row.get("active_2526", "unknown")).strip().lower()
    if seed_flag == "no":
        return "SEED_INACTIVE"
    if seed_flag == "unknown":
        return "SEED_UNKNOWN"

    # ── Level 2: مصادر موثوقة (fm / fbref / csv) ──────────────────────
    mins, _ = _get_reliable_minutes(row)
    if mins is not None:
        if mins < MIN_ACTIVE:
            return "INACTIVE"
        if mins < MIN_REGULAR:
            return "LIMITED"
        return "ACTIVE"

    # ── Level 3: seed='yes' لكن لا بيانات موثوقة ────────────────────
    # (مثال: حراس خارج fm_targets لكن seed يؤكد نشاطهم)
    if seed_flag == "yes":
        return "ACTIVE"

    # ── Level 4: لا شيء ──────────────────────────────────────────────
    return "UNVERIFIED"


def apply_minutes_filter(df: pd.DataFrame) -> pd.DataFrame:
    """
    يُصنّف نشاط كل لاعب (موسم 2025/26) ويُطبّق penalty على AvailabilityScore
    فقط. لا يحسب DSSScore هنا — شوف apply_dss_gate() أسفله لهاد الخطوة.

    يتوقع أعمدة في df:
        - active_2526 (من seed) — إذا لم يوجد يُفترض 'unknown'
        - fm_min, fbref_minutes, csv_min — دقائق موثوقة
        - AvailabilityScore

    Returns:
        df مع أعمدة جداد: minutes_flag, AvailabilityScore (مُعدَّل).
        DSSScore ماشي من مسؤولية هاد الدالة — خاص compute_final_score()
        يتقرا من بعدها، ومن بعد apply_dss_gate() (شوف BUG-01 أسفله).
    """
    if "active_2526" not in df.columns:
        df["active_2526"] = "unknown"
        logger.warning("active_2526 column missing — treating all as 'unknown'")

    flags = df.apply(_classify, axis=1)
    df["minutes_flag"] = flags

    # تطبيق الـ penalty على AvailabilityScore فقط (للشفافية الداخلية/التفكيك)
    df["AvailabilityScore"] = df.apply(
        lambda r: round(r["AvailabilityScore"] * PENALTIES[r["minutes_flag"]], 4),
        axis=1,
    )

    # ── تقارير ──────────────────────────────────────────────────────────
    for flag_name in ("SEED_INACTIVE", "INACTIVE", "LIMITED",
                      "SEED_UNKNOWN",  "UNVERIFIED"):
        affected = df[df["minutes_flag"] == flag_name]
        if not affected.empty:
            names = ", ".join(affected["player_name"].tolist())
            penalty = PENALTIES[flag_name]
            logger.warning(
                "minutes_filter [%s] (Avail×%.2f): %s",
                flag_name, penalty, names,
            )

    active_count = (df["minutes_flag"] == "ACTIVE").sum()
    logger.info(
        "minutes_filter classification done — ACTIVE:%d | flagged:%d "
        "(DSSScore gate applied separately by apply_dss_gate(), AFTER "
        "compute_final_score() — see run_gold.py call order, BUG-01)",
        active_count, len(df) - active_count,
    )

    return df


def apply_dss_gate(df: pd.DataFrame) -> pd.DataFrame:
    """
    يُطبّق penalty مضاعف مباشرة على DSSScore الكامل، فوق تخفيض
    AvailabilityScore اللي دارتو apply_minutes_filter() أعلاه.

    ── BUG-01 (2026-07-29 / 03_regression_protection.md) ─────────────────
    المشكلة المكتشفة أصلاً: العقوبة كانت تُطبَّق فقط على AvailabilityScore
    (وزنه ~7.95% فقط فـ الصيغة). حتى أقصى عقوبة (SEED_INACTIVE ×0.20) ما
    تقدر تخفّض DSSScore بأكثر من ~0.064 نقطة — أقل بكثير من الفارق بين
    لاعب ممتاز وآخر متوسط فـ باقي الـ8 مكونات. النتيجة: Imran Louza
    (SEED_INACTIVE, ACL) تصدّر الترتيب رغم العلم الصريح بعدم نشاطه.

    الحل الأول (فـ نفس اليوم) دمج هاد الحساب داخل apply_minutes_filter()
    نفسها — لكن هذا خلق BUG-01: run_gold.py كيندا apply_minutes_filter()
    (لي كانت كتحسب DSSScore مُبوَّب) ومن بعد كيندا compute_final_score()
    اللي كتعيد حساب DSSScore من الصفر بلا أي بوابة — فكتمحى العقوبة
    بصمت، بلا أي خطأ. تأكد هذا فعليا فـ gold/players_scored_20260801_*:
    Imran Louza (SEED_INACTIVE, Avail=0.2) كان لا يزال #15/49، ماشي مُعاقَب
    بـ ×0.20 على DSSScore الكامل كيما كان المفروض.

    الحل النهائي: فصل الحساب لدالتين — apply_minutes_filter() (تصنيف +
    عقوبة AvailabilityScore فقط) ثم compute_final_score() (DSSScore خام)
    ثم هاد apply_dss_gate() (البوابة النهائية على DSSScore الحقيقي). خاص
    تتنادى بهاد الترتيب بالضبط فـ run_gold.py — أي ترتيب آخر كيرجع لنفس
    BUG-01 بصمت (بلا exception).

    يتوقع أعمدة في df: DSSScore (من compute_final_score()), minutes_flag
    (من apply_minutes_filter()).

    Returns:
        df مع DSSScore_pre_gate (القيمة الخام قبل البوابة، للشفافية) و
        DSSScore/Rank مُحدَّثين (بعد البوابة).
    """
    if "minutes_flag" not in df.columns:
        raise ValueError(
            "apply_dss_gate() called without 'minutes_flag' — call "
            "apply_minutes_filter() first."
        )
    if "DSSScore" not in df.columns:
        raise ValueError(
            "apply_dss_gate() called without 'DSSScore' — call "
            "compute_final_score() first (BUG-01: the gate must apply to "
            "the REAL DSSScore, not a stale/pre-computed one)."
        )

    df["DSSScore_pre_gate"] = df["DSSScore"]
    df["DSSScore"] = df.apply(
        lambda r: round(r["DSSScore_pre_gate"] * PENALTIES[r["minutes_flag"]], 4),
        axis=1,
    )

    # إعادة الترتيب على القيمة المُبوَّبة (الحقيقية)
    df["Rank"] = df["DSSScore"].rank(ascending=False, method="min").astype(int)

    # تحذير خاص: أي لاعب SEED_INACTIVE/INACTIVE لازال ضمن أعلى 26 رتبة بعد
    # البوابة — هذا يعني تفوقه فـ باقي المكونات كبير جداً، ويستاهل تحقق يدوي
    # (مثلاً: هل فعلاً غير نشط، ولا الـseed قديم/خاطئ؟).
    risky_flags = {"SEED_INACTIVE", "INACTIVE"}
    still_top = df[df["minutes_flag"].isin(risky_flags) & (df["Rank"] <= 26)]
    if not still_top.empty:
        for _, r in still_top.iterrows():
            logger.warning(
                "⚠️ minutes_filter GATE ALERT: %s is [%s] but still Rank #%d "
                "(DSSScore %.4f → %.4f after gate) — VERIFY MANUALLY before "
                "including in the final squad recommendation.",
                r["player_name"], r["minutes_flag"], r["Rank"],
                r["DSSScore_pre_gate"], r["DSSScore"],
            )

    logger.info("apply_dss_gate done — DSSScore gate applied AFTER compute_final_score().")

    return df