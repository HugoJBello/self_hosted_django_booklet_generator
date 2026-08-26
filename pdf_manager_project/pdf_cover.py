from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date

import fitz  # PyMuPDF


@dataclass(frozen=True)
class CoverEntry:
    filename: str
    title: str
    author: str
    page_count: int


def _clean_meta(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def _fallback_title(filename: str) -> str:
    base = os.path.basename(filename)
    title, _ = os.path.splitext(base)
    return title or base


def collect_cover_entries(input_paths: list[str], display_names: list[str] | None = None) -> list[CoverEntry]:
    entries: list[CoverEntry] = []

    for idx, path in enumerate(input_paths):
        display_name = display_names[idx] if display_names and idx < len(display_names) else os.path.basename(path)
        with fitz.open(path) as doc:
            metadata = doc.metadata or {}
            title = _clean_meta(metadata.get("title")) or _fallback_title(display_name)
            author = _clean_meta(metadata.get("author"))
            entries.append(
                CoverEntry(
                    filename=os.path.basename(display_name),
                    title=title,
                    author=author,
                    page_count=doc.page_count,
                )
            )

    return entries


def _truncate(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "..."


def _add_corner_marks(page: fitz.Page) -> None:
    text = "*"
    font_size = 12
    margin = 18
    text_width = fitz.get_text_length(text, fontname="helv", fontsize=font_size)
    positions = [
        (margin, margin + font_size),
        (page.rect.width - text_width - margin, margin + font_size),
        (margin, page.rect.height - margin),
        (page.rect.width - text_width - margin, page.rect.height - margin),
    ]
    for x, y in positions:
        page.insert_text((x, y), text, fontsize=font_size, fontname="helv", color=(0, 0, 0))


def draw_compact_cover_index(
    page: fitz.Page,
    rect: fitz.Rect,
    entries: list[CoverEntry],
    heading: str = "Booklet index",
) -> None:
    margin = 18
    x0 = rect.x0 + margin
    x1 = rect.x1 - margin
    y = rect.y0 + 28

    page.insert_text((x0, y), heading, fontsize=13, fontname="helv", color=(0.08, 0.08, 0.08))
    subtitle = f"{len(entries)} document(s)"
    subtitle_x = x1 - fitz.get_text_length(subtitle, fontname="helv", fontsize=7)
    page.insert_text(
        (subtitle_x, y),
        subtitle,
        fontsize=7,
        fontname="helv",
        color=(0.38, 0.38, 0.38),
    )
    y += 12
    page.draw_line((x0, y), (x1, y), color=(0.72, 0.72, 0.72), width=0.6)
    y += 12

    if not entries:
        page.insert_text((x0, y), "No documents.", fontsize=8, fontname="helv")
        return

    available_height = max(rect.y1 - y - 16, 1)
    max_columns = 4
    columns = 1
    row_height = available_height / len(entries)
    for candidate_columns in range(1, max_columns + 1):
        rows = (len(entries) + candidate_columns - 1) // candidate_columns
        candidate_row_height = available_height / rows
        columns = candidate_columns
        row_height = candidate_row_height
        if candidate_row_height >= 18:
            break

    rows_per_column = (len(entries) + columns - 1) // columns
    column_gap = 10
    column_width = (x1 - x0 - (column_gap * (columns - 1))) / columns
    title_font_size = max(4.8, min(8.2, row_height * 0.42))
    detail_font_size = max(4.2, min(6.4, row_height * 0.32))
    show_details = row_height >= 11

    for idx, entry in enumerate(entries):
        column = idx // rows_per_column
        row = idx % rows_per_column
        cell_x0 = x0 + column * (column_width + column_gap)
        cell_y0 = y + row * row_height
        number = f"{idx + 1}."
        number_width = fitz.get_text_length(number, fontname="helv", fontsize=title_font_size)
        text_x0 = cell_x0 + number_width + 4
        title_chars = max(10, int(column_width / max(title_font_size * 0.48, 1)) - 6)

        page.insert_text(
            (cell_x0, cell_y0 + title_font_size),
            number,
            fontsize=title_font_size,
            fontname="helv",
            color=(0.12, 0.12, 0.12),
        )
        page.insert_textbox(
            fitz.Rect(text_x0, cell_y0, cell_x0 + column_width, cell_y0 + row_height),
            _truncate(entry.title, title_chars),
            fontsize=title_font_size,
            fontname="helv",
            color=(0.08, 0.08, 0.08),
        )

        if show_details:
            details = [entry.filename]
            if entry.author:
                details.append(f"Author: {entry.author}")
            details.append(f"{entry.page_count}p")
            detail_chars = max(10, int(column_width / max(detail_font_size * 0.48, 1)) - 3)
            page.insert_textbox(
                fitz.Rect(text_x0, cell_y0 + title_font_size + 2, cell_x0 + column_width, cell_y0 + row_height),
                _truncate(" - ".join(details), detail_chars),
                fontsize=detail_font_size,
                fontname="helv",
                color=(0.38, 0.38, 0.38),
            )


def create_cover_pdf(
    output_path: str,
    entries: list[CoverEntry],
    generated_on: date,
    heading: str = "Document index",
) -> None:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    margin = 54
    width = page.rect.width
    y = 64

    page.insert_text((margin, y), heading, fontsize=22, fontname="helv", color=(0.08, 0.08, 0.08))
    y += 28
    subtitle = f"Generated on {generated_on.strftime('%Y-%m-%d')} - {len(entries)} document(s)"
    page.insert_text((margin, y), subtitle, fontsize=10.5, fontname="helv", color=(0.34, 0.34, 0.34))
    y += 34

    page.draw_line((margin, y), (width - margin, y), color=(0.72, 0.72, 0.72), width=0.8)
    y += 22

    if not entries:
        page.insert_text((margin, y), "No documents.", fontsize=11, fontname="helv")
    else:
        row_height = 54
        if len(entries) > 11:
            row_height = max(30, min(48, int((page.rect.height - y - 56) / len(entries))))

        for idx, entry in enumerate(entries, start=1):
            if y + row_height > page.rect.height - 46:
                remaining = len(entries) - idx + 1
                page.insert_text(
                    (margin, y + 10),
                    f"... and {remaining} more document(s)",
                    fontsize=9,
                    fontname="helv",
                    color=(0.34, 0.34, 0.34),
                )
                break

            number = f"{idx}."
            page.insert_text((margin, y + 13), number, fontsize=10, fontname="helv", color=(0.12, 0.12, 0.12))

            title = _truncate(entry.title, 70 if row_height >= 44 else 56)
            page.insert_text(
                (margin + 28, y + 12),
                title,
                fontsize=10.5 if row_height >= 40 else 8.5,
                fontname="helv",
                color=(0.08, 0.08, 0.08),
            )

            details = [entry.filename]
            if entry.author:
                details.append(f"Author: {_truncate(entry.author, 34)}")
            details.append(f"{entry.page_count} page(s)")
            details_text = " - ".join(details)
            if row_height >= 38:
                page.insert_text(
                    (margin + 28, y + 29),
                    _truncate(details_text, 92),
                    fontsize=8.5,
                    fontname="helv",
                    color=(0.38, 0.38, 0.38),
                )

            y += row_height
            if idx < len(entries):
                page.draw_line((margin + 28, y - 8), (width - margin, y - 8), color=(0.9, 0.9, 0.9), width=0.5)

    _add_corner_marks(page)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    doc.save(output_path)
    doc.close()
