"""
sofascore_parsers_career.py
=============================
Parser للقسم ديال #tab:career فـ body.text (السلينيوم).

البنية (مؤكدة من عينة حقيقية — Ismael Saibari):
    Year
    Team
    25/26                              <- rowLabel #1 (موسم، الصف الإجمالي)
    VriendenLoterij Eredivisie         <- sub-row (تورنوا داخل 25/26)
    Eurojackpot KNVB Beker             <- sub-row
    UEFA Champions League              <- sub-row
    2025                                <- rowLabel #2 (موسم منفرد)
    24/25
    23/24
    22/23
    21/22
    20/21
    MP
    MIN
    GLS
    AST
    ASR                                 <- ⚠️ العنوان كاين لكن القيم
                                            ديالو ماكايناش فـ body.text
                                            (الـ ASR badge كيترسم بـ
                                            canvas/SVG، ماشي نص HTML)
    36
    2886
    19
    9
    27
    ...

أي: بعد "ASR" كتجي سلسلة أرقام، كل 4 أرقام متتالية = صف واحد
(MP, MIN, GLS, AST) — وعدد الصفوف الرقمية = عدد rowLabels بالضبط
(الموسم الإجمالي + الـ sub-rows ديال التورنوات).

التمييز بين summary row و sub-row:
    rowLabel كيكون "summary" (موسم إجمالي) إلا تطابق مع نمط سنة
    (بحال "25/26" أو "2025")، وكيكون "sub-row" (تورنوا فرعي) إلا
    كان نص حر (اسم بطولة، بحال "VriendenLoterij Eredivisie").

USAGE:
    from ingestion.loaders.sofascore.parsers_career import parse_career_table
    rows = parse_career_table(body_text)
    # rows = [
    #   {"label": "25/26", "is_summary": True, "MP": 36, "MIN": 2886,
    #    "GLS": 19, "AST": 9},
    #   {"label": "VriendenLoterij Eredivisie", "is_summary": False,
    #    "MP": 27, "MIN": 2145, "GLS": 15, "AST": 8},
    #   ...
    # ]

ملاحظة مهمة (حراس المرمى): عمود الإحصائية الثالث (بعد MP/MIN) كيتبدل
حسب الـ position — اللاعبين الميدانيين عندهم "GLS" (Goals)، بينما
الحراس عندهم "CLS" (Clean Sheets) فنفس الموقع. الـ parser كيقرا اسم
هاد العمود ديناميكيا من النص نفسه (ماشي مفترض ثابت)، وكيرجعو فالـ
dict تحت المفتاح الحقيقي ديالو (GLS أو CLS أو أي اسم آخر يطلع)، زائد
alias ثابت "GOAL_OR_CS_COL" باش الكود اللي كيستهلك الداتا يقدر يعرف
بسهولة شنو كان اسم هاد العمود بلا ما يخمن.
"""

import re
import json
from pathlib import Path

# نمط سنة/موسم: "2025" أو "25/26" أو "2025/26" أو "20/21"
SEASON_LABEL_RE = re.compile(r"^\d{2,4}(/\d{2,4})?$")


def parse_career_table(body_text: str) -> list[dict]:
    lines = [l.strip() for l in body_text.split("\n") if l.strip()]

    try:
        year_idx = lines.index("Year")
        team_idx = lines.index("Team", year_idx)
    except ValueError:
        raise ValueError("ماكايناش 'Year'/'Team' headers فالنص — تأكد بلي راك فـ #tab:career")

    if team_idx != year_idx + 1:
        raise ValueError(f"'Team' ماشي مباشرة بعد 'Year' (لقيناه فموقع {team_idx}، متوقع {year_idx+1})")

    # نلقاو "MP" header بعد "Team" — هوما بداية أسماء الأعمدة
    try:
        mp_idx = lines.index("MP", team_idx)
    except ValueError:
        raise ValueError("ماكايناش 'MP' header فالنص — تأكد بلي راك فـ #tab:career")

    # rowLabels = السطور ما بين "Team" و"MP" (الموسم + التورنوات الفرعية)
    row_labels = lines[team_idx + 1: mp_idx]
    if not row_labels:
        raise ValueError("ماكايناش rowLabels (الموسمين/الفرق) بين 'Team' و 'MP'")

    # بعد "MP" كيجي "MIN"، وبعدو عدد متغير من أعمدة الإحصائيات حتى نلقاو
    # "AST" (العمود الأخير الثابت دايما). المنطق:
    #   - ميدانيين/مهاجمين/دفاع: MP, MIN, GLS, AST (عمود وسيط واحد)
    #   - حراس المرمى:           MP, MIN, CLS, GC, AST (عمودين وسطيين)
    # كنقراو loop حتى نلقاو "AST" — ديناميكي بالكامل، ماشي عدد ثابت.
    cursor = mp_idx + 1
    if cursor >= len(lines) or lines[cursor] != "MIN":
        raise ValueError(
            f"خاص 'MIN' فموقع {cursor}، لقينا "
            f"{lines[cursor] if cursor < len(lines) else 'EOF'!r}"
        )
    cursor += 1

    # نقرا كل الأعمدة الوسطى + AST بطريقة ديناميكية كاملة.
    # STOP conditions للـ loop: "ASR" (badge بلا قيم) أو رقم (بداية الداتا)
    # أعمدة مؤكدة حسب الـ position:
    #   ميدانيين/دفاع/مهاجمين: MIN → GLS → AST → ASR (أو بلا ASR)
    #   حراس المرمى:           MIN → CLS → GC  → AST → ASR
    #                           أو:  MIN → CLS → GC  → ASR (بلا AST أحيانا)
    KNOWN_STOP = {"ASR"}  # أعمدة كنتخطاوها (badge بلا قيم فالـ body.text)
    FIXED_TRAILING = {"AST"}  # أعمدة ثابتة فآخر الـ header row (كنضيفوهم لـ columns)

    middle_cols = []
    trailing_cols = []
    while cursor < len(lines):
        col = lines[cursor]
        if re.fullmatch(r"-?\d+", col):
            break  # وصلنا لبداية الداتا الرقمية
        if col in KNOWN_STOP:
            cursor += 1  # نتخطاو ASR بلا ما نضيفوه لـ columns
            break
        if col in FIXED_TRAILING:
            trailing_cols.append(col)
            cursor += 1
            # بعد AST كيجي ASR (نتخطاوه) أو الأرقام مباشرة
            if cursor < len(lines) and lines[cursor] in KNOWN_STOP:
                cursor += 1
            break
        middle_cols.append(col)
        cursor += 1

    if not middle_cols:
        raise ValueError(
            f"ماكاين حتى عمود وسيط بين 'MIN' وبداية الداتا — لقينا "
            f"{lines[cursor] if cursor < len(lines) else 'EOF'!r} فموقع {cursor}"
        )

    # COLUMNS النهائية: MP, MIN, [middle_cols...], [AST إلا كان كاين]
    columns = ["MP", "MIN"] + middle_cols + trailing_cols

    numbers_start = cursor

    # نلقاو نهاية سلسلة الأرقام: أول سطر بعدها ماشي رقم صحيح (Legend عادة)
    numbers = []
    i = numbers_start
    while i < len(lines) and re.fullmatch(r"-?\d+", lines[i]):
        numbers.append(int(lines[i]))
        i += 1

    n_rows = len(row_labels)
    expected_numbers = n_rows * len(columns)
    if len(numbers) != expected_numbers:
        raise ValueError(
            f"عدد الأرقام ({len(numbers)}) ما كيتطابقش مع المتوقع "
            f"({n_rows} صفوف × {len(columns)} أعمدة = {expected_numbers}). "
            f"ربما البنية تبدلات — راجع body_text يدويا."
        )

    rows = []
    for idx, label in enumerate(row_labels):
        start = idx * len(columns)
        values = numbers[start: start + len(columns)]
        row = {
            "label": label,
            "is_summary": bool(SEASON_LABEL_RE.match(label)),
            # لائحة الأعمدة الوسطى (GLS وحده لميدانيين، [CLS, GC] لحراس...)
            # باش الكود المستهلك يعرف عددها وأسماءها بلا ما يخمن
            "middle_cols": middle_cols,
        }
        row.update(dict(zip(columns, values)))
        rows.append(row)

    return rows


def pretty_print(rows: list[dict]) -> None:
    if not rows:
        print("(لا صفوف)")
        return
    middle_cols = rows[0].get("middle_cols", ["GLS/CLS"])
    has_ast = "AST" in rows[0]
    mid_header = "/".join(middle_cols)
    ast_header = " AST" if has_ast else ""
    print(f"{'Label':<35} {'Summary?':<10} {'MP':>5} {'MIN':>6} {mid_header:>12}{ast_header}")
    print("-" * 80)
    for r in rows:
        marker = "📅" if r["is_summary"] else "  ↳"
        mid_vals = "/".join(str(r.get(c, "?")) for c in r["middle_cols"])
        ast_val = f" {r['AST']:>4}" if "AST" in r else ""
        print(f"{marker} {r['label']:<32} {str(r['is_summary']):<10} "
              f"{r['MP']:>5} {r['MIN']:>6} {mid_vals:>12}{ast_val}")


if __name__ == "__main__":
    txt_path = Path("debug_output/career_tab_body_text.txt")
    if not txt_path.exists():
        alt = Path("/mnt/user-data/uploads/career_tab_body_text.txt")
        txt_path = alt if alt.exists() else txt_path

    body_text = txt_path.read_text(encoding="utf-8")
    rows = parse_career_table(body_text)

    pretty_print(rows)

    out_path = Path("debug_output/career_table_parsed.json")
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n✅ تسجل JSON منظم → {out_path}")

    # تحقق: مجموع MP ديال sub-rows ديال أول موسم summary = MP ديال الصف الإجمالي
    summary_idx = next(i for i, r in enumerate(rows) if r["is_summary"])
    sub_rows = []
    for r in rows[summary_idx + 1:]:
        if r["is_summary"]:
            break
        sub_rows.append(r)

    total_mp = rows[summary_idx]["MP"]
    sum_sub_mp = sum(r["MP"] for r in sub_rows)
    ok = total_mp == sum_sub_mp
    print(f"\n🔍 تحقق: MP الإجمالي ({total_mp}) == مجموع sub-rows ({sum_sub_mp}) "
          f"{'✅' if ok else '❌'}")