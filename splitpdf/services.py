from __future__ import annotations

import os
import re
import shutil
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Literal

import fitz

from booklets.flipped_a4 import build_flipped_a4_booklets_pipeline
from booklets.services import SourcePdfSpec, build_booklets_pipeline


BookletLayout = Literal["side_by_side", "flipped_a4"]
FlippedSplitMode = Literal["vector", "raster"]


@dataclass(frozen=True)
class TocEntry:
    level: int
    title: str
    page: int


@dataclass(frozen=True)
class SplitSection:
    index: int
    level: int
    title: str
    start_page: int
    end_page: int
    filename: str

    @property
    def page_count(self) -> int:
        return self.end_page - self.start_page + 1


@dataclass(frozen=True)
class SplitOutput:
    output_id: str
    title: str
    filename: str
    path: str
    start_page: int
    end_page: int
    page_count: int
    booklet_applied: bool


@dataclass(frozen=True)
class SplitPdfJobOptions:
    apply_booklets: bool = False
    booklet_layout: BookletLayout = "side_by_side"
    max_pages_per_split: int = 40
    margin_cm: float = 1.0
    side_by_side_prepare_for_portrait_printing: bool = True
    flipped_a4_quality: str = "medium"
    flipped_a4_split_mode: FlippedSplitMode = "vector"
    flipped_a4_center_gap_cm: float = 1.0
    flipped_a4_prepare_for_a5_printing: bool = False


def extract_toc(input_pdf_path: str) -> tuple[list[TocEntry], int]:
    with fitz.open(input_pdf_path) as doc:
        total_pages = len(doc)
        raw_toc = doc.get_toc(simple=True)

    entries = [
        TocEntry(level=int(level), title=_clean_title(title), page=_clamp_page(int(page), total_pages))
        for level, title, page in raw_toc
        if total_pages > 0
    ]
    return entries, total_pages


def available_levels(toc_entries: list[TocEntry]) -> list[int]:
    return sorted({entry.level for entry in toc_entries})


def build_sections_for_level(
    toc_entries: list[TocEntry],
    total_pages: int,
    selected_level: int,
) -> list[SplitSection]:
    if total_pages < 1:
        raise ValueError("The PDF has no pages.")
    if not toc_entries:
        raise ValueError("No table of contents was detected in this PDF.")

    selected_entries = [
        (entry_index, entry)
        for entry_index, entry in enumerate(toc_entries)
        if entry.level == selected_level
    ]
    if not selected_entries:
        raise ValueError("The selected table-of-contents level does not contain sections.")

    sections: list[SplitSection] = []
    for idx, (entry_index, entry) in enumerate(selected_entries, start=1):
        next_boundary = _find_next_boundary_page(toc_entries, entry_index, selected_level)
        start_page = _clamp_page(entry.page, total_pages)
        end_page = total_pages if next_boundary is None else _clamp_page(next_boundary - 1, total_pages)
        if end_page < start_page:
            end_page = start_page

        sections.append(
            SplitSection(
                index=idx,
                level=entry.level,
                title=entry.title,
                start_page=start_page,
                end_page=end_page,
                filename=section_filename(idx, entry.title),
            )
        )

    return sections


def split_pdf_by_sections(
    input_pdf_path: str,
    sections: list[SplitSection],
    output_dir: str,
    options: SplitPdfJobOptions,
) -> list[SplitOutput]:
    if not sections:
        raise ValueError("No sections were selected for splitting.")

    os.makedirs(output_dir, exist_ok=True)
    outputs: list[SplitOutput] = []

    for section in sections:
        section_pdf_path = _unique_path(output_dir, section.filename)
        _write_pdf_range(input_pdf_path, section_pdf_path, section.start_page, section.end_page)
        final_path = section_pdf_path
        final_filename = os.path.basename(final_path)
        booklet_applied = False

        if options.apply_booklets:
            final_path = _apply_booklet(section_pdf_path, section, output_dir, options)
            final_filename = os.path.basename(final_path)
            booklet_applied = True

        outputs.append(
            SplitOutput(
                output_id=uuid.uuid4().hex,
                title=section.title,
                filename=final_filename,
                path=final_path,
                start_page=section.start_page,
                end_page=section.end_page,
                page_count=section.page_count,
                booklet_applied=booklet_applied,
            )
        )

    return outputs


def section_filename(index: int, title: str, suffix: str = ".pdf") -> str:
    safe_title = _slug_filename(title)
    return f"{index:02d}_{safe_title}{suffix}"


def _apply_booklet(
    section_pdf_path: str,
    section: SplitSection,
    output_dir: str,
    options: SplitPdfJobOptions,
) -> str:
    spec = SourcePdfSpec(
        input_pdf_path=section_pdf_path,
        same_page_parity=True,
        margin_cm=options.margin_cm,
        add_watermark=False,
    )
    pipeline = build_flipped_a4_booklets_pipeline if options.booklet_layout == "flipped_a4" else build_booklets_pipeline
    pipeline_kwargs = {
        "specs": [spec],
        "max_pages_per_split": options.max_pages_per_split,
        "final_output_dir": output_dir,
        "preserve_file_parity": True,
        "generate_cover": False,
    }

    if options.booklet_layout == "flipped_a4":
        pipeline_kwargs.update(
            {
                "render_quality": options.flipped_a4_quality,
                "split_mode": options.flipped_a4_split_mode,
                "center_gap_cm": options.flipped_a4_center_gap_cm,
                "prepare_for_a5_printing": options.flipped_a4_prepare_for_a5_printing,
            }
        )
    else:
        pipeline_kwargs["prepare_for_portrait_printing"] = options.side_by_side_prepare_for_portrait_printing

    result = pipeline(**pipeline_kwargs)
    target_path = _unique_path(output_dir, section_filename(section.index, f"{section.title}_booklet"))
    shutil.move(result.output_pdf_path, target_path)
    return target_path


def _write_pdf_range(input_pdf_path: str, output_pdf_path: str, start_page: int, end_page: int) -> None:
    with fitz.open(input_pdf_path) as source_doc:
        total_pages = len(source_doc)
        start_index = _clamp_page(start_page, total_pages) - 1
        end_index = _clamp_page(end_page, total_pages) - 1
        output_doc = fitz.open()
        try:
            output_doc.insert_pdf(source_doc, from_page=start_index, to_page=end_index)
            output_doc.save(output_pdf_path, garbage=4, deflate=True)
        finally:
            output_doc.close()


def _find_next_boundary_page(
    toc_entries: list[TocEntry],
    current_index: int,
    selected_level: int,
) -> int | None:
    for entry in toc_entries[current_index + 1 :]:
        if entry.level <= selected_level:
            return entry.page
    return None


def _clean_title(title: str) -> str:
    title = " ".join((title or "").split())
    return title or "Untitled section"


def _slug_filename(value: str, max_length: int = 72) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    safe = re.sub(r"[^A-Za-z0-9._ -]+", "", ascii_value)
    safe = re.sub(r"[\s-]+", "_", safe).strip("._-")
    if not safe:
        safe = "section"
    if len(safe) > max_length:
        safe = safe[:max_length].rstrip("._-")
    return safe or "section"


def _clamp_page(page: int, total_pages: int) -> int:
    return max(1, min(page, total_pages))


def _unique_path(dirpath: str, filename: str) -> str:
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(dirpath, filename)
    if not os.path.exists(candidate):
        return candidate

    i = 1
    while True:
        candidate = os.path.join(dirpath, f"{base}_{i}{ext}")
        if not os.path.exists(candidate):
            return candidate
        i += 1
