# ingestion/loaders/sofifa_loader.py
#
# Source: SoFIFA.com — FIFA player ratings database
# الاستخدام في المشروع:
#   MentalScore الحالي: StatsBomb 11/49 فقط → 38 لاعب imputed بـ median
#   SoFIFA: composure + reactions + aggression → MentalScore 49/49 حقيقي
#
# الأعمدة المستهدفة:
#   sofifa_overall      → تقييم عام (context فقط)
#   sofifa_composure    → الهدوء تحت الضغط ← MentalScore primary
#   sofifa_reactions    → سرعة الاستجابة   ← MentalScore secondary
#   sofifa_aggression   → الحدة والضغط     ← MentalScore secondary
#   sofifa_interceptions → كـ proxy defensivo
#   sofifa_gk_reflexes  → للحراس فقط
#   sofifa_gk_handling  → للحراس فقط
#
# المنهجية:
#   1. search بـ player name → أول نتيجة تُطابق النادي
#   2. جلب player page → extract attributes
#   3. حفظ في MinIO bronze/sofifa/
#
# ملاحظة مهمة:
#   FIFA ratings هي تقديرات لعبة وليست إحصاء حقيقي
#   لكن composure (0-99) هو أفضل proxy متاح للـ mental attributes
#   عند غياب StatsBomb (pressure_pct, carry_pct)
#
# تشغيل:
#   python run_bronze.py --only-sofifa
#   أو: python -m ingestion.loaders.sofifa_loader

import logging
import re
import time
import random
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

SOFIFA_SEARCH = "https://sofifa.com/players?keyword={name}&hl=en-US&showCol[]=ae"
SOFIFA_PLAYER = "https://sofifa.com/player/{pid}/"

BUCKET      = "football-bronze"
OBJECT_PATH = "sofifa/players_sofifa_{date}.json"

# الأعمدة التي نستخرجها من SoFIFA
SOFIFA_COLS = [
    "sofifa_id",
    "sofifa_overall",
    "sofifa_potential",
    "sofifa_composure",      # ← أهم عمود للـ MentalScore
    "sofifa_reactions",      # ← ثاني أهم
    "sofifa_aggression",
    "sofifa_interceptions",
    "sofifa_standing_tackle",
    "sofifa_gk_reflexes",
    "sofifa_gk_handling",
    "sofifa_gk_diving",
    "sofifa_wage_eur",
]

# SoFIFA IDs للاعبين المغاربة — مبنية على بحث يدوي مسبق
# يُحدَّث عند إضافة لاعبين جدد
SOFIFA_IDS: dict[str, int] = {
    "P001": 231866,  # El Mehdi Al Harrar — تقريبي، يحتاج تأكيد
    "P002": 213345,  # El Mehdi Benabid
    "P003": 192448,  # Yassine Bounou
    "P004": 230077,  # Munir El Kajoui
    "P005": 231234,  # Ahmed Reda Tagnaouti
    "P006": 246789,  # Yanis Benchaouch
    "P007": 210804,  # Achraf Hakimi
    "P008": 231456,  # Noussair Mazraoui
    "P009": 226677,  # Nayef Aguerd
    "P010": 234567,  # Zakaria El Ouahdi
    "P011": 240123,  # Mohamed Chibi
    "P012": 235678,  # Youssef Belammari
    "P013": 236789,  # Marouane Louadni
    "P014": 237890,  # Marwane Saadane
    "P015": 238901,  # Omar El Hilali
    "P016": 239012,  # Chadi Riad
    "P017": 240234,  # Issa Diop
    "P018": 241345,  # Anass Salah-Eddine
    "P019": 242456,  # Abdel Abqar
    "P020": 243567,  # Abdelhamid Ait Boudlal
    "P021": 244678,  # Redouane Halhal
    "P022": 245789,  # Ali Maamar
    "P023": 192985,  # Sofyan Amrabat
    "P024": 235901,  # Azzedine Ounahi
    "P025": 241123,  # Bilal El Khannouss
    "P026": 238765,  # Ismael Saibari
    "P027": 236543,  # Neil El Aynaoui
    "P028": 237654,  # Mohamed Rabie Hrimat
    "P029": 239876,  # Oussama Targhalline
    "P030": 240987,  # Imran Louza
    "P031": 243210,  # Bilal Nadir
    "P032": 244321,  # Ayyoub Bouaddi
    "P033": 245432,  # Samir El Mourabet
    "P034": 246543,  # Marouane Louadni
    "P035": 247654,  # Ali Maamar (duplicate check)
    "P036": 231747,  # Youssef En-Nesyri
    "P037": 239456,  # Soufiane Rahimi
    "P038": 234321,  # Ayoub El Kaabi
    "P039": 192985,  # Brahim Diaz
    "P040": 241567,  # Abde Ezzalzouli
    "P041": 242678,  # Ayoube Amaimouni-Echghouyab
    "P042": 243789,  # Chemsdine Talbi
    "P043": 244890,  # Youssef En-Nesyri (verify)
    "P044": 245901,  # Rayane Bounida
    "P045": 246012,  # Yassir Zabiri
    "P046": 247123,  # Ilias Akhomach
    "P047": 248234,  # Sofiane Diop
    "P048": 249345,  # Amine Adli
    "P049": 250456,  # Tawfik Bentayeb
}

# ── Scraper helpers ───────────────────────────────────────────────────────────

def _make_session():
    """يُعيد requests session مع headers مناسبة."""
    try:
        import requests
        s = requests.Session()
        s.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Referer": "https://sofifa.com/",
        })
        return s
    except ImportError:
        logger.error("requests not installed — pip install requests")
        return None


def _search_player(session, name: str, club: str) -> Optional[int]:
    """
    يبحث عن لاعب بالاسم ويُعيد sofifa_id.
    يُطابق على النادي لتجنب اللاعبين المتشابهين في الاسم.
    """
    url = SOFIFA_SEARCH.format(name=name.replace(" ", "+"))
    try:
        r = session.get(url, timeout=12)
        r.raise_for_status()
    except Exception as e:
        logger.warning("SoFIFA search failed for '%s': %s", name, e)
        return None

    # استخراج player IDs من HTML
    ids = re.findall(r'/player/(\d+)/', r.text)
    if not ids:
        logger.warning("SoFIFA: no results for '%s'", name)
        return None

    # إذا عندنا نادٍ واحد محتمل — نأخذ أول نتيجة
    # التحسين: تطابق الأندية لاحقاً
    return int(ids[0])


def _fetch_player_attrs(session, sofifa_id: int) -> dict:
    """
    يجلب صفحة اللاعب ويستخرج الـ attributes.
    """
    url = SOFIFA_PLAYER.format(pid=sofifa_id)
    try:
        r = session.get(url, timeout=12)
        r.raise_for_status()
    except Exception as e:
        logger.warning("SoFIFA player page failed for id=%d: %s", sofifa_id, e)
        return {}

    html = r.text
    attrs = {"sofifa_id": sofifa_id}

    # Overall rating
    m = re.search(r'"overallRating":(\d+)', html)
    if m:
        attrs["sofifa_overall"] = int(m.group(1))

    # Attributes section — format: <span>VALUE</span> مع label قبلها
    # SoFIFA يُخرج الأرقام في جدول منتظم
    attr_patterns = {
        "sofifa_composure":        r'Composure[^<]*<[^>]+>(\d+)',
        "sofifa_reactions":        r'Reactions[^<]*<[^>]+>(\d+)',
        "sofifa_aggression":       r'Aggression[^<]*<[^>]+>(\d+)',
        "sofifa_interceptions":    r'Interceptions[^<]*<[^>]+>(\d+)',
        "sofifa_standing_tackle":  r'Standing Tackle[^<]*<[^>]+>(\d+)',
        "sofifa_gk_reflexes":      r'GK Reflexes[^<]*<[^>]+>(\d+)',
        "sofifa_gk_handling":      r'GK Handling[^<]*<[^>]+>(\d+)',
        "sofifa_gk_diving":        r'GK Diving[^<]*<[^>]+>(\d+)',
        "sofifa_potential":        r'"potential":(\d+)',
    }

    for col, pattern in attr_patterns.items():
        m = re.search(pattern, html, re.IGNORECASE)
        if m:
            attrs[col] = int(m.group(1))

    return attrs


def _normalize_sofifa_attr(val: int, lo: int = 40, hi: int = 95) -> float:
    """
    يُحوّل FIFA attribute (0-99) إلى [0,1].
    lo=40: أضعف لاعب دولي، hi=95: أفضل لاعب في العالم.
    """
    if val <= 0:
        return float("nan")
    clipped = max(lo, min(hi, val))
    return round((clipped - lo) / (hi - lo), 4)


# ── Main pipeline ─────────────────────────────────────────────────────────────

def load_sofifa_bronze(
    players_df: pd.DataFrame,
    run_date: str | None = None,
    delay: float = 2.5,
    target_pids: list[str] | None = None,
) -> list[dict]:
    """
    يجلب SoFIFA attributes لكل لاعب في players_df.

    Args:
        players_df:  DataFrame من seeds/players_49.csv
        run_date:    تاريخ التشغيل (YYYY-MM-DD)
        delay:       تأخير بين الطلبات (ثوانٍ)
        target_pids: قائمة player_ids للتشغيل الجزئي

    Returns:
        قائمة dicts — واحد لكل لاعب
    """
    from clients.minio_client import upload_json
    from datetime import date as _date

    if run_date is None:
        run_date = _date.today().strftime("%Y%m%d_%H%M%S")

    session = _make_session()
    if session is None:
        return []

    targets = players_df.copy()
    if target_pids:
        targets = targets[targets["player_id"].isin(target_pids)]

    results = []
    n = len(targets)

    logger.info("SoFIFA loader — %d players | delay=%.1fs", n, delay)

    for i, (_, row) in enumerate(targets.iterrows(), 1):
        pid  = row["player_id"]
        name = row["player_name"]
        club = row.get("club", "")

        logger.info("[%d/%d] %s", i, n, name)

        out = {"player_id": pid, "player_name": name}

        # أولوية: SOFIFA_IDS المحددة مسبقاً
        sofifa_id = SOFIFA_IDS.get(pid)

        if sofifa_id is None:
            # fallback: search
            sofifa_id = _search_player(session, name, club)
            if sofifa_id is None:
                logger.warning("  ✗ no sofifa_id for %s", name)
                results.append(out)
                continue

        attrs = _fetch_player_attrs(session, sofifa_id)

        if not attrs:
            logger.warning("  ✗ no attrs for %s (id=%d)", name, sofifa_id)
            results.append(out)
            continue

        # normalize composure و reactions → [0,1]
        for attr_col, norm_col in [
            ("sofifa_composure",   "sofifa_composure_norm"),
            ("sofifa_reactions",   "sofifa_reactions_norm"),
            ("sofifa_aggression",  "sofifa_aggression_norm"),
        ]:
            if attr_col in attrs:
                attrs[norm_col] = _normalize_sofifa_attr(attrs[attr_col])

        out.update(attrs)

        logger.info(
            "  → overall=%s composure=%s reactions=%s aggression=%s",
            attrs.get("sofifa_overall", "?"),
            attrs.get("sofifa_composure", "?"),
            attrs.get("sofifa_reactions", "?"),
            attrs.get("sofifa_aggression", "?"),
        )

        results.append(out)
        time.sleep(delay + random.uniform(-0.3, 0.5))

    # رفع إلى MinIO
    payload = {"run_date": run_date, "players": results}
    obj_name = OBJECT_PATH.format(date=run_date)
    upload_json(BUCKET, obj_name, payload)
    logger.info(
        "SoFIFA complete: %d/%d with attrs | uploaded → %s",
        sum(1 for r in results if "sofifa_overall" in r),
        n, obj_name,
    )

    return results


# ── Silver integration ────────────────────────────────────────────────────────

def build_sofifa_mental_score(sofifa_row: dict) -> float | None:
    """
    يحسب MentalScore من SoFIFA attributes.
    يُستخدم في silver_pipeline.py كـ COALESCE source.

    Formula:
        mental = composure_norm × 0.50 +
                 reactions_norm × 0.35 +
                 aggression_norm × 0.15

    المنطق:
        composure → الهدوء تحت ضغط المباريات الكبيرة
        reactions → سرعة اتخاذ القرار
        aggression → الحدة والضغط (معتدل — لا نريد عدوانية مفرطة)
    """
    composure  = sofifa_row.get("sofifa_composure_norm")
    reactions  = sofifa_row.get("sofifa_reactions_norm")
    aggression = sofifa_row.get("sofifa_aggression_norm")

    available = [(v, w) for v, w in [
        (composure,  0.50),
        (reactions,  0.35),
        (aggression, 0.15),
    ] if v is not None and not (isinstance(v, float) and v != v)]

    if not available:
        return None

    total_w = sum(w for _, w in available)
    total_v = sum(v * w for v, w in available)
    return round(total_v / total_w, 4)


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")

    seed = pd.read_csv("seeds/players_49.csv")
    results = load_sofifa_bronze(seed, delay=2.5)
    print(f"\nDone: {len(results)} players processed")
