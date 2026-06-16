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
