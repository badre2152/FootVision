from __future__ import annotations

import io
from datetime import datetime

import pandas as pd
import streamlit as st

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
)
from reportlab.graphics.shapes import Drawing, Rect

from dashboard.i18n import t


def csv_download_button(
    df: pd.DataFrame,
    *,
    filename_prefix: str,
    key: str,
    label: str | None = None,
) -> None:
    if df is None or df.empty:
        return
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    filename = f"{filename_prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    st.download_button(
        label=label or t("export.csv_button"),
        data=csv_bytes,
        file_name=filename,
        mime="text/csv",
        key=key,
    )


LABEL_MAP = {
    "PerformanceScore": "on-pitch output", "FormScore": "recent form",
    "MentalScore": "mental profile", "AvailabilityScore": "fitness/availability",
    "InternationalScore": "international experience", "ChemistryScore": "squad chemistry",
    "PressureIndex": "big-match performance", "FlexibilityScore": "positional versatility",
    "SentimentScore": "public sentiment",
}

POSITION_ORDER = ["GK", "DEF", "MID", "FWD"]
POSITION_LABELS = {"GK": "Goalkeepers", "DEF": "Defenders", "MID": "Midfielders", "FWD": "Forwards"}


def justification_line(row, component_cols: list[str]) -> str:
    if not component_cols:
        return f"DSSScore {row['DSSScore']:.3f}"
    best_col = max(component_cols, key=lambda c: row[c])
    return f"Strongest in {LABEL_MAP.get(best_col, best_col)} ({row[best_col]:.2f})"


def build_final_squad_pdf(
    top26: pd.DataFrame,
    component_cols: list[str],
    *,
    source_file: str | None = None,
    filters_applied: bool = False,
) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=18 * mm, bottomMargin=16 * mm,
        leftMargin=16 * mm, rightMargin=16 * mm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("ReportTitle", parent=styles["Title"], fontSize=18, spaceAfter=4)
    subtitle_style = ParagraphStyle("ReportSubtitle", parent=styles["Normal"], textColor=colors.grey, spaceAfter=14)
    section_style = ParagraphStyle("SectionHeader", parent=styles["Heading2"], spaceBefore=10, spaceAfter=6)
    disclaimer_style = ParagraphStyle("Disclaimer", parent=styles["Normal"], fontSize=8, textColor=colors.grey)

    story = []
    story.append(Paragraph("Football Atlas — Final Squad Report", title_style))
    scope_note = "Filtered view (sidebar filters applied)" if filters_applied else "Full unfiltered squad"
    meta_bits = [f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}", scope_note]
    if source_file:
        meta_bits.append(f"Source: {source_file}")
    story.append(Paragraph("  ·  ".join(meta_bits), subtitle_style))

    for pos in POSITION_ORDER:
        group = top26[top26["position"] == pos].sort_values("DSSScore", ascending=False)
        if group.empty:
            continue
        story.append(Paragraph(f"{POSITION_LABELS[pos]} ({len(group)})", section_style))

        table_data = [["#", "Player", "Club", "DSSScore", "Justification"]]
        for _, row in group.iterrows():
            table_data.append([
                str(int(row["Rank"])) if "Rank" in row.index else "",
                str(row["player_name"]),
                str(row.get("club", "")),
                f"{row['DSSScore']:.3f}",
                justification_line(row, component_cols),
            ])

        tbl = Table(table_data, colWidths=[10 * mm, 42 * mm, 32 * mm, 20 * mm, 74 * mm], repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f4f6")]),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d1d5db")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(tbl)
        story.append(Spacer(1, 6))

    story.append(Spacer(1, 10))
    story.append(Paragraph(
        "Decision-support output only — not a replacement for coaching or "
        "medical judgement. \"Justification\" shows each player's single "
        "strongest scored sub-component, not a full explanation of the "
        "ranking. See README.md's DECISION_DATE note for this project's "
        "known temporal-consistency limitation.",
        disclaimer_style,
    ))

    doc.build(story)
    return buf.getvalue()


def build_player_profile_pdf(row, component_cols: list[str], *, note_text: str | None = None) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=18 * mm, bottomMargin=16 * mm,
        leftMargin=16 * mm, rightMargin=16 * mm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("ReportTitle", parent=styles["Title"], fontSize=18, spaceAfter=4)
    subtitle_style = ParagraphStyle("ReportSubtitle", parent=styles["Normal"], textColor=colors.grey, spaceAfter=14)
    section_style = ParagraphStyle("SectionHeader", parent=styles["Heading2"], spaceBefore=10, spaceAfter=6)
    disclaimer_style = ParagraphStyle("Disclaimer", parent=styles["Normal"], fontSize=8, textColor=colors.grey)

    story = []
    story.append(Paragraph(f"Player Profile — {row['player_name']}", title_style))
    meta_bits = [
        str(row.get("club", "—")), str(row.get("position", "—")),
        f"DSSScore {row['DSSScore']:.4f}",
    ]
    if "Rank" in row.index:
        meta_bits.append(f"Rank #{int(row['Rank'])}")
    story.append(Paragraph("  ·  ".join(meta_bits), subtitle_style))

    if "PlayerDNA" in row.index and row["PlayerDNA"] not in (None, "N/A"):
        story.append(Paragraph(f"Player DNA: {row['PlayerDNA']} (descriptive tag, not a score input)", styles["Normal"]))
        story.append(Spacer(1, 8))

    if note_text:
        story.append(Paragraph("Coach/Staff Note", section_style))
        story.append(Paragraph(str(note_text), styles["Normal"]))
        story.append(Spacer(1, 8))

    story.append(Paragraph("Sub-score breakdown", section_style))
    bar_max_width = 80 * mm
    bar_height = 4 * mm

    table_data = [["Component", "Score", "Bar"]]
    for c in component_cols:
        val = max(0.0, min(1.0, float(row[c])))
        drawing = Drawing(bar_max_width, bar_height)
        drawing.add(Rect(0, 0, bar_max_width, bar_height, fillColor=colors.HexColor("#e5e7eb"), strokeColor=None))
        drawing.add(Rect(0, 0, bar_max_width * val, bar_height, fillColor=colors.HexColor("#2563eb"), strokeColor=None))
        table_data.append([LABEL_MAP.get(c, c), f"{row[c]:.3f}", drawing])

    tbl = Table(table_data, colWidths=[45 * mm, 18 * mm, bar_max_width + 4 * mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d1d5db")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(tbl)

    story.append(Spacer(1, 12))
    story.append(Paragraph(
        "Decision-support output only — not a replacement for coaching or "
        "medical judgement.",
        disclaimer_style,
    ))

    doc.build(story)
    return buf.getvalue()