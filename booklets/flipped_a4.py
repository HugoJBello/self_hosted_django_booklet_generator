from __future__ import annotations

import os
import tempfile
import uuid
from dataclasses import dataclass
from typing import Literal

import fitz
from django.utils import timezone

from pdf_manager_project.pdf_cover import collect_cover_entries, create_cover_pdf

from .services import (
    BookletJobResult,
    PreparedPage,
    SourcePdfSpec,
    add_watermark_to_page,
    compute_split_ranges,
    merge_pdfs,
    prepare_pages_for_specs,
)


FLIPPED_A4_CENTER_GAP_CM = 1.0
FLIPPED_A4_MIN_OUTER_MARGIN_CM = 0.15
FlippedA4Quality = Literal["very_low", "low", "medium", "high", "super_high"]
FlippedA4SplitMode = Literal["raster", "vector"]

FLIPPED_A4_QUALITY_PROFILES: dict[FlippedA4Quality, tuple[float, int]] = {
    "very_low": (2.5, 84),
    "low": (3.5, 88),
    "medium": (4.5, 88),
    "high": (5.0, 86),
    "super_high": (6.0, 88),
}


@dataclass(frozen=True)
class PreparedHalfPage:
    prepared_page: PreparedPage
    half: str

    @property
    def is_blank(self) -> bool:
        return self.prepared_page.is_blank

    @property
    def add_watermark(self) -> bool:
        return self.prepared_page.add_watermark and self.half == "top"


@dataclass(frozen=True)
class ImposedHalfPage:
    half_page: PreparedHalfPage
    rotate_180: bool = False

    @property
    def is_blank(self) -> bool:
        return self.half_page.is_blank

    @property
    def add_watermark(self) -> bool:
        return self.half_page.add_watermark


def _logical_half_pages_for_prepared_pages(prepared_pages: list[PreparedPage]) -> list[PreparedHalfPage]:
    first_page = prepared_pages[0] if prepared_pages else PreparedPage(None, None, 595, 842, 1.0, False)
    half_pages: list[PreparedHalfPage] = [_blank_half_like(PreparedHalfPage(first_page, "top"))]

    for prepared_page in prepared_pages:
        half_pages.append(PreparedHalfPage(prepared_page=prepared_page, half="top"))
        half_pages.append(PreparedHalfPage(prepared_page=prepared_page, half="bottom"))

    half_pages.append(_blank_half_like(PreparedHalfPage(prepared_pages[-1], "bottom") if prepared_pages else None))
    return half_pages


def _blank_half_like(template_half: PreparedHalfPage | None) -> PreparedHalfPage:
    if template_half is None:
        template_page = PreparedPage(None, None, 595, 842, 1.0, False)
        return PreparedHalfPage(prepared_page=template_page, half="top")

    template_page = template_half.prepared_page
    return PreparedHalfPage(
        prepared_page=PreparedPage(
            source_pdf_path=None,
            source_page_number=None,
            width=template_page.width,
            height=template_page.height,
            margin_cm=template_page.margin_cm,
            add_watermark=False,
        ),
        half=template_half.half,
    )


def _clip_half_page(page: fitz.Page, half: str) -> fitz.Rect:
    split_y = _find_half_split_y(page)

    if half == "top":
        return fitz.Rect(page.rect.x0, page.rect.y0, page.rect.x1, split_y)
    if half == "bottom":
        return fitz.Rect(page.rect.x0, split_y, page.rect.x1, page.rect.y1)

    raise ValueError(f"Unsupported half-page value: {half}")


def _content_y_ranges(page: fitz.Page) -> list[tuple[float, float]]:
    return []


def _split_intersects_content(y: float, ranges: list[tuple[float, float]]) -> bool:
    return False


def _find_half_split_y(page: fitz.Page) -> float:
    return page.rect.y0 + page.rect.height / 2


def _padded_half_pages(page_plan: list[PreparedHalfPage]) -> list[PreparedHalfPage]:
    padded = list(page_plan)
    while len(padded) % 4 != 0:
        padded.append(_blank_half_like(padded[-1] if padded else None))
    return padded


def _ensure_odd_prepared_pages(prepared_pages: list[PreparedPage]) -> list[PreparedPage]:
    if len(prepared_pages) % 2 == 1:
        return list(prepared_pages)

    template_page = prepared_pages[-1] if prepared_pages else PreparedPage(None, None, 595, 842, 1.0, False)
    return [
        *prepared_pages,
        PreparedPage(
            source_pdf_path=None,
            source_page_number=None,
            width=template_page.width,
            height=template_page.height,
            margin_cm=template_page.margin_cm,
            add_watermark=False,
        ),
    ]


def _imposed_cell_pairs(prepared_pages: list[PreparedPage]) -> list[tuple[ImposedHalfPage, ImposedHalfPage]]:
    original = _ensure_odd_prepared_pages(prepared_pages)
    if not original:
        blank = ImposedHalfPage(_blank_half_like(None))
        return [(blank, blank)]

    blank_template = PreparedHalfPage(original[0], "top")
    blank = ImposedHalfPage(_blank_half_like(blank_template))
    imposed: list[tuple[ImposedHalfPage, ImposedHalfPage]] = [(blank, blank)]

    for k in range(0, len(original) // 2):
        left_page = original[k]
        right_page = original[len(original) - 1 - k]
        imposed.append(
            (
                ImposedHalfPage(PreparedHalfPage(left_page, "top")),
                ImposedHalfPage(PreparedHalfPage(right_page, "bottom")),
            )
        )
        imposed.append(
            (
                ImposedHalfPage(PreparedHalfPage(left_page, "bottom"), rotate_180=True),
                ImposedHalfPage(PreparedHalfPage(right_page, "top"), rotate_180=True),
            )
        )

    center_page = original[len(original) // 2]
    imposed.append(
        (
            ImposedHalfPage(PreparedHalfPage(center_page, "top")),
            ImposedHalfPage(PreparedHalfPage(center_page, "bottom")),
        )
    )
    return imposed


def _should_rotate_output_page(output_page_number: int) -> bool:
    return output_page_number % 2 == 0


def _cell_draw_rect(
    cell_x0: float,
    cell_y0: float,
    cell_x1: float,
    cell_y1: float,
    outer_margin_pts: float,
    fold_edge: str,
    center_gap_cm: float = FLIPPED_A4_CENTER_GAP_CM,
) -> fitz.Rect:
    base_margin_pts = max(outer_margin_pts, FLIPPED_A4_MIN_OUTER_MARGIN_CM * 72 / 2.54)
    fold_margin_pts = (max(center_gap_cm, 0.0) / 2) * 72 / 2.54
    left_margin = base_margin_pts
    right_margin = base_margin_pts
    top_margin = fold_margin_pts if fold_edge == "top" else base_margin_pts
    bottom_margin = fold_margin_pts if fold_edge == "bottom" else base_margin_pts
    return fitz.Rect(
        cell_x0 + left_margin,
        cell_y0 + top_margin,
        cell_x1 - right_margin,
        cell_y1 - bottom_margin,
    )


def create_flipped_a4_booklet(
    prepared_pages: list[PreparedPage],
    output_pdf_path: str,
    render_quality: FlippedA4Quality = "medium",
    center_gap_cm: float = FLIPPED_A4_CENTER_GAP_CM,
    split_mode: FlippedA4SplitMode = "vector",
) -> None:
    source_docs: dict[str, fitz.Document] = {}
    half_docs: dict[tuple[str, int, str], fitz.Document] = {}
    doc_out = fitz.open()
    render_scale, jpeg_quality = FLIPPED_A4_QUALITY_PROFILES.get(render_quality, FLIPPED_A4_QUALITY_PROFILES["medium"])

    try:
        out_width = 595
        out_height = 842

        imposed_cell_pairs = _imposed_cell_pairs(prepared_pages)

        for top_slot_page, bottom_slot_page in imposed_cell_pairs:
            page_out = doc_out.new_page(width=out_width, height=out_height)

            def place_half_page(
                imposed_half_page: ImposedHalfPage,
                cell_x0: float,
                cell_y0: float,
                cell_x1: float,
                cell_y1: float,
                fold_edge: str,
            ) -> None:
                if imposed_half_page.is_blank:
                    return

                half_page = imposed_half_page.half_page
                prepared_page = half_page.prepared_page
                assert prepared_page.source_pdf_path is not None
                assert prepared_page.source_page_number is not None

                half_doc = get_materialized_half_doc(half_page)
                half_page_in = half_doc[0]
                margin_pts = prepared_page.margin_cm * 72 / 2.54
                draw_area = _cell_draw_rect(
                    cell_x0,
                    cell_y0,
                    cell_x1,
                    cell_y1,
                    margin_pts,
                    fold_edge,
                    center_gap_cm=center_gap_cm,
                )
                rotation = get_rotation(imposed_half_page)

                cell_width = max(draw_area.width, 1)
                cell_height = max(draw_area.height, 1)
                rotated_width = half_page_in.rect.width if rotation % 180 == 0 else half_page_in.rect.height
                rotated_height = half_page_in.rect.height if rotation % 180 == 0 else half_page_in.rect.width
                scale = min(cell_width / rotated_width, cell_height / rotated_height)
                w_scaled = rotated_width * scale
                h_scaled = rotated_height * scale
                x_draw = draw_area.x0 + (cell_width - w_scaled) / 2
                if fold_edge == "bottom":
                    y_draw = draw_area.y1 - h_scaled
                elif fold_edge == "top":
                    y_draw = draw_area.y0
                else:
                    y_draw = draw_area.y0 + (cell_height - h_scaled) / 2

                try:
                    page_out.show_pdf_page(
                        fitz.Rect(x_draw, y_draw, x_draw + w_scaled, y_draw + h_scaled),
                        half_doc,
                        0,
                        rotate=rotation,
                    )
                except ValueError:
                    page_out.show_pdf_page(
                        draw_area,
                        half_doc,
                        0,
                        rotate=rotation,
                    )

            def get_materialized_half_doc(half_page: PreparedHalfPage) -> fitz.Document:
                prepared_page = half_page.prepared_page
                assert prepared_page.source_pdf_path is not None
                assert prepared_page.source_page_number is not None

                cache_key = (prepared_page.source_pdf_path, prepared_page.source_page_number, half_page.half)
                half_doc = half_docs.get(cache_key)
                if half_doc is not None:
                    return half_doc

                doc_in = source_docs.get(prepared_page.source_pdf_path)
                if doc_in is None:
                    doc_in = fitz.open(prepared_page.source_pdf_path)
                    source_docs[prepared_page.source_pdf_path] = doc_in

                page_in = doc_in[prepared_page.source_page_number]
                clip = _clip_half_page(page_in, half_page.half)
                if split_mode == "vector":
                    half_doc = _materialize_vector_half_doc(doc_in, page_in.number, clip)
                else:
                    half_doc = fitz.open()
                    page_half = half_doc.new_page(width=clip.width, height=clip.height)
                    pixmap = page_in.get_pixmap(
                        matrix=fitz.Matrix(render_scale, render_scale),
                        clip=clip,
                        alpha=False,
                    )
                    page_half.insert_image(
                        fitz.Rect(0, 0, clip.width, clip.height),
                        stream=pixmap.tobytes("jpeg", jpg_quality=jpeg_quality),
                    )
                half_docs[cache_key] = half_doc
                return half_doc

            def get_rotation(imposed_half_page: ImposedHalfPage) -> int:
                if imposed_half_page.is_blank:
                    return 0

                half_page = imposed_half_page.half_page
                prepared_page = half_page.prepared_page
                assert prepared_page.source_pdf_path is not None
                assert prepared_page.source_page_number is not None

                doc_in = source_docs.get(prepared_page.source_pdf_path)
                if doc_in is None:
                    doc_in = fitz.open(prepared_page.source_pdf_path)
                    source_docs[prepared_page.source_pdf_path] = doc_in

                page_in = doc_in[prepared_page.source_page_number]
                return (page_in.rotation + (180 if imposed_half_page.rotate_180 else 0)) % 360

            place_half_page(
                top_slot_page,
                0,
                0,
                out_width,
                out_height / 2,
                "bottom",
            )
            place_half_page(
                bottom_slot_page,
                0,
                out_height / 2,
                out_width,
                out_height,
                "top",
            )

            if top_slot_page.add_watermark or bottom_slot_page.add_watermark:
                add_watermark_to_page(page_out)

        doc_out.save(output_pdf_path, garbage=4, deflate=True)
    finally:
        doc_out.close()
        for doc in half_docs.values():
            doc.close()
        for doc in source_docs.values():
            doc.close()


def _materialize_vector_half_doc(source_doc: fitz.Document, source_page_number: int, clip: fitz.Rect) -> fitz.Document:
    half_doc = fitz.open()
    page_half = half_doc.new_page(width=clip.width, height=clip.height)
    page_half.show_pdf_page(
        fitz.Rect(0, 0, clip.width, clip.height),
        source_doc,
        source_page_number,
        clip=clip,
    )
    return half_doc


def build_flipped_a4_booklets_pipeline(
    specs: list[SourcePdfSpec],
    max_pages_per_split: int,
    final_output_dir: str,
    preserve_file_parity: bool = True,
    generate_cover: bool = False,
    render_quality: FlippedA4Quality = "medium",
    center_gap_cm: float = FLIPPED_A4_CENTER_GAP_CM,
    split_mode: FlippedA4SplitMode = "vector",
) -> BookletJobResult:
    if not specs:
        raise ValueError("There are no PDFs to process.")

    job_id = uuid.uuid4().hex
    os.makedirs(final_output_dir, exist_ok=True)
    final_pdf = os.path.join(final_output_dir, f"{job_id}_flipped_a4_booklets_for_printing.pdf")

    with tempfile.TemporaryDirectory(prefix=f"pdf_manager_{job_id}_") as tmp:
        specs_to_process = list(specs)
        if generate_cover:
            cover_path = os.path.join(tmp, "cover.pdf")
            create_cover_pdf(
                output_path=cover_path,
                entries=collect_cover_entries([spec.input_pdf_path for spec in specs]),
                generated_on=timezone.localdate(),
                heading="Booklet index",
            )
            specs_to_process.insert(
                0,
                SourcePdfSpec(
                    input_pdf_path=cover_path,
                    same_page_parity=True,
                    margin_cm=0.0,
                    add_watermark=False,
                ),
            )

        prepared_pages = prepare_pages_for_specs(specs_to_process, preserve_file_parity=preserve_file_parity)
        split_ranges = compute_split_ranges(len(prepared_pages), max_pages_per_split)
        split_outputs: list[str] = []

        for split_idx, (start_idx, end_idx) in enumerate(split_ranges, start=1):
            output_path = os.path.join(tmp, f"split{split_idx:02}_flipped_a4_booklet.pdf")
            create_flipped_a4_booklet(
                prepared_pages[start_idx : end_idx + 1],
                output_path,
                render_quality=render_quality,
                center_gap_cm=center_gap_cm,
                split_mode=split_mode,
            )
            split_outputs.append(output_path)

        merge_pdfs(split_outputs, final_pdf)

    return BookletJobResult(job_id=job_id, output_pdf_path=final_pdf)
