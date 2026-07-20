"""
parse_sofascore_season_table.py
==================================
Parser للقسم ديال #tab:season فـ body.text (السلينيوم).

البنية (مؤكدة من عينة حقيقية):
  كل section عندو عنوان ثابت (Matches/Running performance.../Attacking/
  Passing/Defending/Other (per game)/Cards)، وتحتو مباشرة أزواج
  label\nvalue\nlabel\nvalue... حتى نوصلو لعنوان section الجاي.

أمثلة قيم بصيغ خاصة لازم نتعاملو معاهم بحذر (ماشي float() مباشرة):
  "87min"           -> minutes (نفصلو "min")
  "0/0"              -> ratio (نخليوها كنص أو نفصلوها لـ made/attempted)
  "21.5 (88%)"       -> value + percentage (نفصلوهم)
  "33.8 km/h"        -> رقم + وحدة
  "10.2 km"          -> رقم + وحدة

النهج: نسجلو القيمة الخام (raw string) ديما، + نحاولو نستخرجو الرقم
الأساسي (float/int) فحقل منفصل `value_numeric` كل ما كان ممكن، بلا
ما نضيع المعلومة الأصلية (احتياط: 0/0 معناها penalty 0 من 0 مزال ماشي
0.0).

USAGE:
    from parse_sofascore_season_table import parse_season_table
    data = parse_season_table(body_text)
    # data = {
    #   "Matches": {"Appearances": {"raw": "2", "value": 2.0}, ...},
    #   "Attacking": {"Goals": {"raw": "2", "value": 2.0}, ...},
    #   ...
    # }
"""

import re
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


SECTION_HEADERS = [
    "Matches",
    "Running performance (per 90)",
    "Attacking",
    "Passing",
    "Defending",
    "Other (per game)",
    "Cards",
]

# سطور كنتجاهلوها لو طلعت كـ "label" بالغلط (مش جزء من الجدول)
STOP_MARKERS = {
    # أول سطر بعد "Cards" section كيكون اسم اللاعب (نهاية الجدول)
}


def _parse_value(raw: str):
    """
    يحاول يستخرج رقم أساسي (float) من raw string، ويرجع None إلا
    تعذر، بلا ما يفقد raw الأصلية.
    """
    raw = raw.strip()

    # صيغة "87min" -> 87
    m = re.fullmatch(r"(\d+(?:\.\d+)?)min", raw)
    if m:
        return float(m.group(1))

    # صيغة "33.8 km/h" أو "10.2 km" -> الرقم بوحدو
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*km(?:/h)?", raw)
    if m:
        return float(m.group(1))

    # صيغة "21.5 (88%)" -> 21.5 (والنسبة % كنخزنوها فحقل منفصل)
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*\((\d+)%\)", raw)
    if m:
        return float(m.group(1))

    # صيغة "0/0" أو "1/3" (made/attempted) -> منخليوهاش كرقم وحيد،
    # كنرجعو None هنا، الـ caller يقدر يستعمل raw
    if re.fullmatch(r"\d+/\d+", raw):
        return None

    # صيغة "40%" -> 40.0
    m = re.fullmatch(r"(\d+(?:\.\d+)?)%", raw)
    if m:
        return float(m.group(1))

    # رقم عادي int/float
    if re.fullmatch(r"-?\d+(?:\.\d+)?", raw):
        return float(raw)

    return None


def _parse_percentage(raw: str):
    """يستخرج النسبة % من صيغة بحال '21.5 (88%)' إلا كانت كاينة."""
    m = re.fullmatch(r"\d+(?:\.\d+)?\s*\((\d+)%\)", raw.strip())
    if m:
        return float(m.group(1))
    return None


def parse_season_table(body_text: str) -> dict:
    lines = [l.strip() for l in body_text.split("\n") if l.strip()]

    # نلقاو نقطة بداية البحث عن الـ sections. الحالة العادية: نقلبو على
    # "Media" (آخر تبويب فالـ nav bar Matches/Season/Career/Fantasy/Media)
    # ونبداو البحث بعدو، باش ماناخدوش "Matches" مرة كـ section غلط (هي
    # كاينة مرتين فالنص: مرة فالـ nav، ومرة كـ section header حقيقي).
    #
    # FALLBACK: بعض اللاعبين (مثلا احتياطيين بلا دقائق لعب هاد الموسم،
    # أو صفحات بـ layout مختصر) ماعندهمش "Media" فالنص أصلا — نص الـ
    # nav bar كيتبدل. فهاد الحالة، كنقلبو مباشرة على أول section header
    # معروف (من SECTION_HEADERS) ونبداو القراءة منو، بلا ما نعتمدو على
    # "Media" كـ anchor إجباري.
    try:
        nav_idx = lines.index("Media")  # آخر تبويب فالـ nav bar
        search_start = nav_idx + 1
    except ValueError:
        # fallback: أول ظهور لأي section header معروف
        first_header_idx = None
        for header in SECTION_HEADERS:
            try:
                idx = lines.index(header)
                if first_header_idx is None or idx < first_header_idx:
                    first_header_idx = idx
            except ValueError:
                continue
        if first_header_idx is None:
            raise ValueError(
                "ماكايناش 'Media' (tabs nav) ولا أي section header معروف "
                "(Matches/Attacking/Passing/...) فالنص — تأكد بلي راك فصفحة "
                "لاعب حقيقية وبلي الصفحة كملات التحميل (زيد وقت الانتظار "
                "--wait إلا تكرر هاد الخطأ)"
            )
        search_start = first_header_idx

    section_positions = {}
    for header in SECTION_HEADERS:
        try:
            idx = lines.index(header, search_start)
            section_positions[header] = idx
        except ValueError:
            # بعض الأقسام قد ما تكونش موجودة لكل لاعب (مثلا GK ماعندوش Attacking مفصلة بحالها)
            continue

    if not section_positions:
        raise ValueError("ماقدرتش نلقى أي section header معروف — تأكد بلي راك فـ #tab:season")

    # نرتبو الأقسام حسب موقعهم فالنص
    ordered_sections = sorted(section_positions.items(), key=lambda kv: kv[1])

    result = {}
    for i, (header, start_idx) in enumerate(ordered_sections):
        end_idx = ordered_sections[i + 1][1] if i + 1 < len(ordered_sections) else None
        section_lines = lines[start_idx + 1:end_idx]

        # DIAGNOSTIC: log the first few raw lines of every section so we can
        # see exactly what's there when a section unexpectedly ends up empty
        # (e.g. P002 'Matches' section parsing to {} despite no exception).
        logger.debug(
            "[season_parser] section=%r start_idx=%d end_idx=%s first_8_lines=%r",
            header, start_idx, end_idx, section_lines[:8],
        )

        section_data = {}
        j = 0

        # بعض الـ sections (لوحظ مع "Matches" عند لاعبين بزاف مباريات
        # حديثة) فيهم widget إضافية (rating chart / season heatmap) محشورة
        # بين section header والقيم الحقيقية — وهاد الـ widget كيكرر نفس
        # اسم الـ header مرة أخرى (مثلا 'Matches' كتبان زوج: مرة كـ
        # section header اللي لقيناها، ومرة تانية مباشرة قبل القيم
        # الحقيقية). كنقلبو على آخر occurrence ديال نفس header جوا
        # section_lines ديالها، ونبداو القراءة من بعدها — هادشي كيتخطى
        # الـ widget كاملة بضربة وحدة، بدل skip-ahead سطر-بسطر محدود.
        try:
            dup_header_idx = len(section_lines) - 1 - section_lines[::-1].index(header)
            if dup_header_idx > 0:
                logger.debug(
                    "[season_parser] section %r repeats its own header at "
                    "offset %d within its slice (widget noise) — skipping past it",
                    header, dup_header_idx,
                )
                j = dup_header_idx + 1
        except ValueError:
            pass  # header doesn't repeat — normal case, start from j=0

        # عداد محاولات "تجاوز" متتالية فاشلة — كنوقفو نهائي إلا توالت
        # بزاف متتالي (احتياط ضد section خاوية فعلا أو نهاية الجدول
        # الحقيقية)، بلا ما نوقف من أول label/value غير صالح لقيناه.
        consecutive_skips = 0
        MAX_CONSECUTIVE_SKIPS = 2

        while j < len(section_lines) - 1:
            label = section_lines[j]
            raw_value = section_lines[j + 1]

            # توقف آمن 1: label نفسه طويل بزاف (جملة بيوغرافية، ماشي
            # اسم إحصائية قصير) -> غالبا وصلنا لنهاية الجدول، لكن
            # ممكن يكون سطر "noise" وحيد (نفس منطق توقف آمن 2) — كنقفزو
            # سطر واحد ونعاودو نحاولو بدل ما نخسرو الـ section كاملة.
            if len(label) > 60 or label.count(" ") > 6:
                consecutive_skips += 1
                if consecutive_skips > MAX_CONSECUTIVE_SKIPS:
                    break
                j += 1
                continue

            # توقف آمن 2: إلا الـ "value" نفسه كيبان جملة طويلة بلا صيغة
            # رقمية معروفة (بحال bio paragraph) يعني وصلنا لنهاية الجدول
            is_short_stat_format = bool(
                re.fullmatch(
                    r"-?\d+(?:\.\d+)?(?:\s*\((?:\d+%|\d+\.\d+)\))?"   # 21.5 / 21.5 (88%)
                    r"|\d+/\d+"                                       # 0/0
                    r"|\d+(?:\.\d+)?\s*km(?:/h)?"                     # 10.2 km / 33.8 km/h
                    r"|\d+(?:\.\d+)?min"                               # 87min
                    r"|\d+(?:\.\d+)?%"                                 # 40%
                    r"|-",
                    raw_value.strip(),
                )
            )
            if not is_short_stat_format:
                # ماشي بالضرورة نهاية الجدول — ممكن يكون سطر "noise"
                # وحيد جوا section (مثلا header فرعي مخفي، أو قيمة
                # ماكملاتش تتحمل فالـ DOM لحظة قراءة body.text).
                # كنقفزو سطر واحد بغير ونعاودو نحاولو، بدل ما نخسرو
                # باقي الـ section كاملة على غلطة سطر وحيد.
                consecutive_skips += 1
                if consecutive_skips > MAX_CONSECUTIVE_SKIPS:
                    break
                j += 1
                continue

            consecutive_skips = 0
            entry = {"raw": raw_value}
            numeric = _parse_value(raw_value)
            if numeric is not None:
                entry["value"] = numeric
            pct = _parse_percentage(raw_value)
            if pct is not None:
                entry["percentage"] = pct

            section_data[label] = entry
            j += 2

        result[header] = section_data

        if not section_data and section_lines:
            # Section had raw lines but we extracted nothing from them —
            # worth a closer look. Log the full raw lines (not just first 8)
            # so the next real run gives us the exact text to diagnose.
            logger.warning(
                "[season_parser] section %r produced ZERO stats despite having "
                "%d raw lines available — raw lines: %r",
                header, len(section_lines), section_lines[:20],
            )

    return result


def pretty_print(data: dict) -> None:
    for section, fields in data.items():
        print(f"\n=== {section} ===")
        for label, entry in fields.items():
            val_str = entry.get("value", entry["raw"])
            print(f"  {label}: {val_str}  (raw={entry['raw']!r})")


if __name__ == "__main__":
    txt_path = Path("debug_output/season_tab_body_text.txt")
    if not txt_path.exists():
        alt = Path("/mnt/user-data/uploads/season_tab_body_text.txt")
        txt_path = alt if alt.exists() else txt_path

    body_text = txt_path.read_text(encoding="utf-8")
    data = parse_season_table(body_text)

    pretty_print(data)

    out_path = Path("debug_output/season_table_parsed.json")
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n✅ تسجل JSON منظم → {out_path}")

    # تحققات سريعة ضد القيم المعروفة من العينة الحقيقية
    checks = [
        ("Attacking", "Goals", 2.0),
        ("Passing", "Assists", 0.0),
        ("Matches", "Appearances", 2.0),
        ("Cards", "Yellow", 0.0),
    ]
    print("\n🔍 تحققات:")
    all_ok = True
    for section, label, expected in checks:
        actual = data.get(section, {}).get(label, {}).get("value")
        ok = actual == expected
        all_ok &= ok
        print(f"  {section}.{label}: متوقع={expected} فعلي={actual} {'✅' if ok else '❌'}")
    print(f"\n{'✅ كل التحققات نجحت' if all_ok else '⚠️ كاين تحقق فشل، راجع الـ parser'}")