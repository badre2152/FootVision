import re
import json
from pathlib import Path

SEASON_LABEL_RE = re.compile(r"^\d{2,4}(/\d{2,4})?$")


def parse_career_table(body_text: str) -> list[dict]:
    lines = [l.strip() for l in body_text.split("\n") if l.strip()]

    try:
        year_idx = lines.index("Year")
        team_idx = lines.index("Team", year_idx)
    except ValueError:
        raise ValueError("Could not find 'Year'/'Team' headers in the text — make sure this is #tab:career")

    if team_idx != year_idx + 1:
        raise ValueError(f"'Team' is not directly after 'Year' (found at {team_idx}, expected {year_idx+1})")

    try:
        mp_idx = lines.index("MP", team_idx)
    except ValueError:
        raise ValueError("Could not find 'MP' header in the text — make sure this is #tab:career")

    row_labels = lines[team_idx + 1: mp_idx]
    if not row_labels:
        raise ValueError("No rowLabels (seasons/teams) found between 'Team' and 'MP'")

    cursor = mp_idx + 1
    if cursor >= len(lines) or lines[cursor] != "MIN":
        raise ValueError(
            f"Expected 'MIN' at position {cursor}, found "
            f"{lines[cursor] if cursor < len(lines) else 'EOF'!r}"
        )
    cursor += 1

    KNOWN_STOP = {"ASR"}
    FIXED_TRAILING = {"AST"}

    middle_cols = []
    trailing_cols = []
    while cursor < len(lines):
        col = lines[cursor]
        if re.fullmatch(r"-?\d+", col):
            break
        if col in KNOWN_STOP:
            cursor += 1
            break
        if col in FIXED_TRAILING:
            trailing_cols.append(col)
            cursor += 1
            if cursor < len(lines) and lines[cursor] in KNOWN_STOP:
                cursor += 1
            break
        middle_cols.append(col)
        cursor += 1

    if not middle_cols:
        raise ValueError(
            f"No middle column found between 'MIN' and the start of the data — found "
            f"{lines[cursor] if cursor < len(lines) else 'EOF'!r} at position {cursor}"
        )

    columns = ["MP", "MIN"] + middle_cols + trailing_cols

    numbers_start = cursor

    numbers = []
    i = numbers_start
    while i < len(lines) and re.fullmatch(r"-?\d+", lines[i]):
        numbers.append(int(lines[i]))
        i += 1

    n_rows = len(row_labels)
    expected_numbers = n_rows * len(columns)
    if len(numbers) != expected_numbers:
        raise ValueError(
            f"Number count ({len(numbers)}) doesn't match expected "
            f"({n_rows} rows x {len(columns)} columns = {expected_numbers}). "
            f"The structure may have changed — check body_text manually."
        )

    rows = []
    for idx, label in enumerate(row_labels):
        start = idx * len(columns)
        values = numbers[start: start + len(columns)]
        row = {
            "label": label,
            "is_summary": bool(SEASON_LABEL_RE.match(label)),
            "middle_cols": middle_cols,
        }
        row.update(dict(zip(columns, values)))
        rows.append(row)

    return rows


def pretty_print(rows: list[dict]) -> None:
    if not rows:
        print("(no rows)")
        return
    middle_cols = rows[0].get("middle_cols", ["GLS/CLS"])
    has_ast = "AST" in rows[0]
    mid_header = "/".join(middle_cols)
    ast_header = " AST" if has_ast else ""
    print(f"{'Label':<35} {'Summary?':<10} {'MP':>5} {'MIN':>6} {mid_header:>12}{ast_header}")
    print("-" * 80)
    for r in rows:
        marker = "*" if r["is_summary"] else "  ->"
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
    print(f"\nSaved structured JSON -> {out_path}")

    summary_idx = next(i for i, r in enumerate(rows) if r["is_summary"])
    sub_rows = []
    for r in rows[summary_idx + 1:]:
        if r["is_summary"]:
            break
        sub_rows.append(r)

    total_mp = rows[summary_idx]["MP"]
    sum_sub_mp = sum(r["MP"] for r in sub_rows)
    ok = total_mp == sum_sub_mp
    print(f"\nCheck: total MP ({total_mp}) == sum of sub-rows ({sum_sub_mp}) "
          f"{'OK' if ok else 'FAIL'}")