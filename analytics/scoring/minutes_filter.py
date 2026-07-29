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
    يُطبّق penalty على AvailabilityScore بناءً على نشاط موسم 2025/26،
    ثم يُعيد حساب DSSScore والـ Rank.

    يتوقع أعمدة في df:
        - active_2526 (من seed) — إذا لم يوجد يُفترض 'unknown'
        - fm_min, fbref_minutes, csv_min — دقائق موثوقة
        - AvailabilityScore, PerformanceScore, InternationalScore,
          FormScore, MentalScore, DSSScore, Rank
    """
    # BUG FIX: this used to rebuild the composite formula locally with
    # w.get("performance", 0.35) / w.get("international", 0.25) / etc.
    # COMPOSITE_WEIGHTS keys are actually "PerformanceScore", "TacticalFitScore"...
    # — none of those .get() lookups ever matched, so this silently ignored
    # config.py and always used the hardcoded defaults (harmless only by
    # coincidence, since the defaults happened to equal the real weights).
    # It also duplicated the composite math in a second place with different
    # (broken, non-redistributing) NaN handling than compute_final_score().
    #
    # Fix: delegate to the SAME weighted-subscore helper compute_final_score()
    # uses, with the SAME key mapping, so there is exactly one implementation
    # of "how DSSScore is computed" in the whole codebase.
    from config import COMPOSITE_WEIGHTS
    from analytics.scoring.performance_score import compute_weighted_subscore

    composite_weights = {
        "PerformanceScore":   COMPOSITE_WEIGHTS["PerformanceScore"],
        "InternationalScore": COMPOSITE_WEIGHTS["TacticalFitScore"],
        "FormScore":          COMPOSITE_WEIGHTS["FormScore"],
        "MentalScore":        COMPOSITE_WEIGHTS["MentalScore"],
        "AvailabilityScore":  COMPOSITE_WEIGHTS["AvailabilityScore"],
        "ChemistryScore":     COMPOSITE_WEIGHTS["ChemistryScore"],
        "PressureIndex":      COMPOSITE_WEIGHTS["PressureIndex"],
        "FlexibilityScore":   COMPOSITE_WEIGHTS["FlexibilityScore"],
        "SentimentScore":     COMPOSITE_WEIGHTS["SentimentScore"],
    }

    if "active_2526" not in df.columns:
        df["active_2526"] = "unknown"
        logger.warning("active_2526 column missing — treating all as 'unknown'")

    flags      = df.apply(_classify, axis=1)
    df["minutes_flag"] = flags

    # تطبيق الـ penalty
    df["AvailabilityScore"] = df.apply(
        lambda r: round(r["AvailabilityScore"] * PENALTIES[r["minutes_flag"]], 4),
        axis=1,
    )

    # إعادة حساب DSSScore — بنفس الدالة والمفاتيح اللي كيستعملها compute_final_score
    df["DSSScore_pre_gate"] = df.apply(
        lambda r: compute_weighted_subscore(r, composite_weights, suffix=""),
        axis=1,
    ).round(4)

    # ── FIX (2026-07-29): البوابة الحقيقية على DSSScore، ماشي غير AvailabilityScore ──
    # المشكلة المكتشفة: العقوبة كانت تُطبَّق فقط على AvailabilityScore (وزنه 7.95%
    # فقط فـ الصيغة). حتى أقصى عقوبة (SEED_INACTIVE ×0.20) ما تقدر تخفّض DSSScore
    # بأكثر من ~0.064 نقطة — أقل بكثير من الفارق بين لاعب ممتاز وآخر متوسط فـ باقي
    # الـ8 مكونات. النتيجة الفعلية المُلاحَظة: Imran Louza (SEED_INACTIVE) تصدّر
    # الترتيب #1 رغم العلم الصريح بعدم نشاطه — بالضبط السيناريو اللي هذا الملف
    # بُني أصلاً باش يمنعه (شوف التعليق فـ رأس الملف)، لكن التطبيق الفعلي فشل
    # يحقق الهدف المكتوب.
    #
    # الحل: نطبّق نفس معامل العقوبة (PENALTIES) كمضاعف مباشر على DSSScore الكامل،
    # فوق تخفيض AvailabilityScore الأصلي (اللي كيبقى للشفافية الداخلية/التفكيك).
    # هكذا لاعب SEED_INACTIVE ما يقدرش يتصدّر الترتيب إلا لو كان متفوق بشكل
    # استثنائي جداً فـ كل شيء آخر (سيناريو نادر ويستاهل مراجعة يدوية أصلاً).
    df["DSSScore"] = df.apply(
        lambda r: round(r["DSSScore_pre_gate"] * PENALTIES[r["minutes_flag"]], 4),
        axis=1,
    )

    # إعادة الترتيب على القيمة المُبوَّبة (الحقيقية)
    df["Rank"] = df["DSSScore"].rank(ascending=False, method="min").astype(int)

    # ── تقارير ──────────────────────────────────────────────────────────
    for flag_name in ("SEED_INACTIVE", "INACTIVE", "LIMITED",
                      "SEED_UNKNOWN",  "UNVERIFIED"):
        affected = df[df["minutes_flag"] == flag_name]
        if not affected.empty:
            names = ", ".join(affected["player_name"].tolist())
            penalty = PENALTIES[flag_name]
            logger.warning(
                "minutes_filter [%s] (Avail×%.2f, DSSScore×%.2f): %s",
                flag_name, penalty, penalty, names,
            )

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

    active_count = (df["minutes_flag"] == "ACTIVE").sum()
    logger.info(
        "minutes_filter done — ACTIVE:%d | penalties applied:%d",
        active_count, len(df) - active_count,
    )

    return df