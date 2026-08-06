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

STOP_MARKERS = {}


def _parse_value(raw: str):
    raw = raw.strip()

    m = re.fullmatch(r"(\d+(?:\.\d+)?)min", raw)
    if m:
        return float(m.group(1))

    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*km(?:/h)?", raw)
    if m:
        return float(m.group(1))

    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*\((\d+)%\)", raw)
    if m:
        return float(m.group(1))

    if re.fullmatch(r"\d+/\d+", raw):
        return None

    m = re.fullmatch(r"(\d+(?:\.\d+)?)%", raw)
    if m:
        return float(m.group(1))

    if re.fullmatch(r"-?\d+(?:\.\d+)?", raw):
        return float(raw)

    return None


def _parse_percentage(raw: str):
    m = re.fullmatch(r"\d+(?:\.\d+)?\s*\((\d+)%\)", raw.strip())
    if m:
        return float(m.group(1))
    return None


def parse_season_table(body_text: str) -> dict:
    lines = [l.strip() for l in body_text.split("\n") if l.strip()]

    try:
        nav_idx = lines.index("Media")
        search_start = nav_idx + 1
    except ValueError:
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
                "Neither 'Media' (tabs nav) nor any known section header "
                "(Matches/Attacking/Passing/...) was found in the text — "
                "make sure this is a real player page and that it fully "
                "finished loading (increase --wait if this keeps happening)"
            )
        search_start = first_header_idx

    section_positions = {}
    for header in SECTION_HEADERS:
        try:
            idx = lines.index(header, search_start)
            section_positions[header] = idx
        except ValueError:
            continue

    if not section_positions:
        raise ValueError("Could not find any known section header — make sure this is #tab:season")

    ordered_sections = sorted(section_positions.items(), key=lambda kv: kv[1])

    result = {}
    for i, (header, start_idx) in enumerate(ordered_sections):
        end_idx = ordered_sections[i + 1][1] if i + 1 < len(ordered_sections) else None
        section_lines = lines[start_idx + 1:end_idx]

        logger.debug(
            "[season_parser] section=%r start_idx=%d end_idx=%s first_8_lines=%r",
            header, start_idx, end_idx, section_lines[:8],
        )

        section_data = {}
        j = 0

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
            pass

        consecutive_skips = 0
        MAX_CONSECUTIVE_SKIPS = 2

        while j < len(section_lines) - 1:
            label = section_lines[j]
            raw_value = section_lines[j + 1]

            if len(label) > 60 or label.count(" ") > 6:
                consecutive_skips += 1
                if consecutive_skips > MAX_CONSECUTIVE_SKIPS:
                    break
                j += 1
                continue

            is_short_stat_format = bool(
                re.fullmatch(
                    r"-?\d+(?:\.\d+)?(?:\s*\((?:\d+%|\d+\.\d+)\))?"
                    r"|\d+/\d+"
                    r"|\d+(?:\.\d+)?\s*km(?:/h)?"
                    r"|\d+(?:\.\d+)?min"
                    r"|\d+(?:\.\d+)?%"
                    r"|-",
                    raw_value.strip(),
                )
            )
            if not is_short_stat_format:
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
    print(f"\nSaved structured JSON -> {out_path}")

    checks = [
        ("Attacking", "Goals", 2.0),
        ("Passing", "Assists", 0.0),
        ("Matches", "Appearances", 2.0),
        ("Cards", "Yellow", 0.0),
    ]
    print("\nChecks:")
    all_ok = True
    for section, label, expected in checks:
        actual = data.get(section, {}).get(label, {}).get("value")
        ok = actual == expected
        all_ok &= ok
        print(f"  {section}.{label}: expected={expected} actual={actual} {'OK' if ok else 'FAIL'}")
    print(f"\n{'All checks passed' if all_ok else 'A check failed, review the parser'}")