from __future__ import annotations

import os
import tempfile
import uuid
from dataclasses import dataclass

import fitz  # PyMuPDF


@dataclass(frozen=True)
class SourcePdfSpec:
    input_pdf_path: str
    same_page_parity: bool
    margin_cm: float
    add_watermark: bool


@dataclass(frozen=True)
class PreparedPage:
    source_pdf_path: str | None
    source_page_number: int | None
    width: float
    height: float
    margin_cm: float
    add_watermark: bool = False

    @property
    def is_blank(self) -> bool:
        return self.source_pdf_path is None or self.source_page_number is None


@dataclass(frozen=True)
class BookletJobResult:
    job_id: str
    output_pdf_path: str


def detect_content_bbox(page: fitz.Page, margin_pts: float) -> fitz.Rect:
    content_rects = []

    for block in page.get_text("blocks"):
        content_rects.append(fitz.Rect(block[:4]))

    raw_dict = page.get_text("rawdict")
    for block in raw_dict.get("blocks", []):
        if block.get("type") == 1 and "bbox" in block:
            content_rects.append(fitz.Rect(block["bbox"]))

    for item in page.get_drawings():
        rect = item.get("rect")
        if rect and rect.is_valid:
            content_rects.append(rect)

    if not content_rects:
        return page.rect

    x0 = min(r.x0 for r in content_rects)
    y0 = min(r.y0 for r in content_rects)
    x1 = max(r.x1 for r in content_rects)
    y1 = max(r.y1 for r in content_rects)
    bbox = fitz.Rect(x0, y0, x1, y1)

    clip_rect = fitz.Rect(
        max(bbox.x0 - margin_pts, 0),
        max(bbox.y0 - margin_pts, 0),
        min(bbox.x1 + margin_pts, page.rect.width),
        min(bbox.y1 + margin_pts, page.rect.height),
    )

    return clip_rect if clip_rect.is_valid else page.rect


def add_watermark_to_page(page: fitz.Page) -> None:
    text = "*"
    font_size = 20
    margin = 20
    text_width = fitz.get_text_length(text, fontname="helv", fontsize=font_size)
    x = page.rect.width - text_width - margin
    y = margin + font_size
    page.insert_text(
        (x, y),
        text,
        fontsize=font_size,
        fontname="helv",
        color=(0, 0, 0),
    )


def merge_pdfs(input_paths: list[str], output_path: str) -> None:
    merged = fitz.open()
    for path in input_paths:
        with fitz.open(path) as doc:
            merged.insert_pdf(doc)
    merged.save(output_path)
    merged.close()


def compute_split_ranges(total_pages: int, max_pages_per_split: int) -> list[tuple[int, int]]:
    split_ranges: list[tuple[int, int]] = []
    split_count = 0
    current_index = 0

    while current_index < total_pages:
        if split_count > 0 and current_index % 2 == 1:
            current_index += 1
            if current_index >= total_pages:
                break

        to_page = min(current_index + max_pages_per_split - 1, total_pages - 1)

        next_start = to_page + 1
        if next_start < total_pages and next_start % 2 == 1:
            to_page -= 1

        if to_page < current_index:
            to_page = current_index

        split_ranges.append((current_index, to_page))
        split_count += 1
        current_index = to_page + 1

    return split_ranges


def prepare_pages_for_specs(
    specs: list[SourcePdfSpec],
    preserve_file_parity: bool,
) -> list[PreparedPage]:
    prepared_pages: list[PreparedPage] = []

    for spec in specs:
        with fitz.open(spec.input_pdf_path) as doc:
            if len(doc) == 0:
                raise ValueError(f"PDF vacío: {os.path.basename(spec.input_pdf_path)}")

            first_page = doc[0]
            desired_is_odd = spec.same_page_parity

            if preserve_file_parity:
                next_page_number = len(prepared_pages) + 1
                starts_on_odd = (next_page_number % 2) == 1
                if starts_on_odd != desired_is_odd:
                    prepared_pages.append(
                        PreparedPage(
                            source_pdf_path=None,
                            source_page_number=None,
                            width=first_page.rect.width,
                            height=first_page.rect.height,
                            margin_cm=spec.margin_cm,
                            add_watermark=False,
                        )
                    )

            for page_number in range(len(doc)):
                page = doc[page_number]
                prepared_pages.append(
                    PreparedPage(
                        source_pdf_path=spec.input_pdf_path,
                        source_page_number=page_number,
                        width=page.rect.width,
                        height=page.rect.height,
                        margin_cm=spec.margin_cm,
                        add_watermark=spec.add_watermark and page_number == 0,
                    )
                )

    return prepared_pages


def create_booklet(
    prepared_pages: list[PreparedPage],
    output_pdf_path: str,
) -> None:
    source_docs: dict[str, fitz.Document] = {}
    doc_out = fitz.open()

    try:
        out_width = 842
        out_height = 595

        page_plan = list(prepared_pages)
        while len(page_plan) % 4 != 0:
            template_page = page_plan[-1] if page_plan else PreparedPage(None, None, 595, 842, 1.0, False)
            page_plan.append(
                PreparedPage(
                    source_pdf_path=None,
                    source_page_number=None,
                    width=template_page.width,
                    height=template_page.height,
                    margin_cm=template_page.margin_cm,
                    add_watermark=False,
                )
            )

        left_pages = list(range(len(page_plan) - 1, len(page_plan) // 2 - 1, -1))
        right_pages = list(range(0, len(page_plan) // 2))

        for sheet_number, (left_idx, right_idx) in enumerate(zip(left_pages, right_pages), start=1):
            page_out = doc_out.new_page(width=out_width, height=out_height)

            left_page = page_plan[left_idx]
            right_page = page_plan[right_idx]

            def place_prepared_page(
                prepared_page: PreparedPage,
                cell_x0: float,
                cell_y0: float,
                cell_x1: float,
                cell_y1: float,
                rotation: int,
            ) -> None:
                if prepared_page.is_blank:
                    return

                assert prepared_page.source_pdf_path is not None
                assert prepared_page.source_page_number is not None

                doc_in = source_docs.get(prepared_page.source_pdf_path)
                if doc_in is None:
                    doc_in = fitz.open(prepared_page.source_pdf_path)
                    source_docs[prepared_page.source_pdf_path] = doc_in

                page_in = doc_in[prepared_page.source_page_number]
                margin_pts = prepared_page.margin_cm * 72 / 2.54
                bbox = detect_content_bbox(page_in, margin_pts)
                col_width = max(cell_x1 - cell_x0 - (2 * margin_pts), 1)
                col_height = max(cell_y1 - cell_y0 - (2 * margin_pts), 1)
                scale = min(col_width / bbox.width, col_height / bbox.height)
                w_scaled = bbox.width * scale
                h_scaled = bbox.height * scale
                x_draw = cell_x0 + margin_pts + (col_width - w_scaled) / 2
                y_draw = cell_y0 + margin_pts + (col_height - h_scaled) / 2

                try:
                    page_out.show_pdf_page(
                        fitz.Rect(x_draw, y_draw, x_draw + w_scaled, y_draw + h_scaled),
                        doc_in,
                        page_in.number,
                        clip=bbox if bbox != page_in.rect else None,
                        rotate=rotation,
                    )
                except ValueError:
                    try:
                        page_out.show_pdf_page(
                            fitz.Rect(
                                cell_x0 + margin_pts,
                                cell_y0 + margin_pts,
                                cell_x1 - margin_pts,
                                cell_y1 - margin_pts,
                            ),
                            doc_in,
                            page_in.number,
                            rotate=rotation,
                        )
                    except ValueError:
                        page_out.draw_rect(
                            fitz.Rect(cell_x0, cell_y0, cell_x1, cell_y1),
                            color=(1, 1, 1),
                            fill=(1, 1, 1),
                        )

            def get_rotation(prepared_page: PreparedPage) -> int:
                if prepared_page.is_blank:
                    return 0

                assert prepared_page.source_pdf_path is not None
                assert prepared_page.source_page_number is not None

                doc_in = source_docs.get(prepared_page.source_pdf_path)
                if doc_in is None:
                    doc_in = fitz.open(prepared_page.source_pdf_path)
                    source_docs[prepared_page.source_pdf_path] = doc_in

                page_in = doc_in[prepared_page.source_page_number]
                return (page_in.rotation + 180) % 360 if sheet_number % 2 == 1 else page_in.rotation

            place_prepared_page(
                right_page,
                0,
                0,
                out_width / 2,
                out_height,
                get_rotation(right_page),
            )
            place_prepared_page(
                left_page,
                out_width / 2,
                0,
                out_width,
                out_height,
                get_rotation(left_page),
            )

            if left_page.add_watermark or right_page.add_watermark:
                add_watermark_to_page(page_out)

        doc_out.save(output_pdf_path)
    finally:
        doc_out.close()
        for doc in source_docs.values():
            doc.close()


def build_booklets_pipeline(
    specs: list[SourcePdfSpec],
    max_pages_per_split: int,
    final_output_dir: str,
    preserve_file_parity: bool = True,
) -> BookletJobResult:
    if not specs:
        raise ValueError("No hay PDFs para procesar.")

    job_id = uuid.uuid4().hex
    os.makedirs(final_output_dir, exist_ok=True)
    final_pdf = os.path.join(final_output_dir, f"{job_id}_booklets_for_printing.pdf")

    prepared_pages = prepare_pages_for_specs(specs, preserve_file_parity=preserve_file_parity)
    split_ranges = compute_split_ranges(len(prepared_pages), max_pages_per_split)

    with tempfile.TemporaryDirectory(prefix=f"pdf_manager_{job_id}_") as tmp:
        split_outputs: list[str] = []

        for split_idx, (start_idx, end_idx) in enumerate(split_ranges, start=1):
            output_path = os.path.join(tmp, f"split{split_idx:02}_booklet.pdf")
            create_booklet(prepared_pages[start_idx:end_idx + 1], output_path)
            split_outputs.append(output_path)

        merge_pdfs(split_outputs, final_pdf)

    return BookletJobResult(job_id=job_id, output_pdf_path=final_pdf)
