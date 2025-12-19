from __future__ import annotations

import os
import uuid
import tempfile
from dataclasses import dataclass

import fitz  # PyMuPDF


# ============================================================
# Utils
# ============================================================

def detect_content_bbox(page: fitz.Page, margin_pts: float) -> fitz.Rect:
    content_rects = []

    # --- 1. Bloques de texto
    for block in page.get_text("blocks"):
        content_rects.append(fitz.Rect(block[:4]))

    # --- 2. Imágenes
    raw_dict = page.get_text("rawdict")
    for block in raw_dict.get("blocks", []):
        if block.get("type") == 1 and "bbox" in block:
            content_rects.append(fitz.Rect(block["bbox"]))

    # --- 3. Dibujos
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


def add_watermark_to_first_page(doc: fitz.Document) -> None:
    if len(doc) == 0:
        return
    page = doc[0]
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


# ============================================================
# Split PDF (idéntico a split_pdf.py)
# ============================================================

def _create_blank_page_like(page: fitz.Page) -> fitz.Document:
    blank_doc = fitz.open()
    blank_doc.new_page(width=page.rect.width, height=page.rect.height)
    return blank_doc


def split_pdf_to_dir(
    input_pdf_path: str,
    output_dir: str,
    max_pages_per_split: int,
    same_page_parity: bool,
) -> list[str]:

    os.makedirs(output_dir, exist_ok=True)

    doc_path_to_use = input_pdf_path
    temp_path = None

    if not same_page_parity:
        doc_orig = fitz.open(input_pdf_path)
        if len(doc_orig) == 0:
            raise ValueError("PDF vacío")

        temp_doc = fitz.open()
        blank_doc = _create_blank_page_like(doc_orig[0])
        temp_doc.insert_pdf(blank_doc)
        blank_doc.close()
        temp_doc.insert_pdf(doc_orig)
        doc_orig.close()

        temp_path = os.path.join(output_dir, "_with_blank_first_page.pdf")
        temp_doc.save(temp_path)
        temp_doc.close()
        doc_path_to_use = temp_path

    doc = fitz.open(doc_path_to_use)

    total_pages = len(doc)
    split_count = 0
    current_index = 0
    split_paths: list[str] = []

    while current_index < total_pages:
        split_doc = fitz.open()

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

        split_doc.insert_pdf(doc, from_page=current_index, to_page=to_page)

        split_count += 1
        out_path = os.path.join(output_dir, f"split{split_count:02}.pdf")
        split_doc.save(out_path)
        split_doc.close()

        split_paths.append(out_path)
        current_index = to_page + 1

    doc.close()

    if temp_path and os.path.exists(temp_path):
        try:
            os.remove(temp_path)
        except Exception:
            pass

    return split_paths


# ============================================================
# Create booklet (idéntico a booklets.py)
# ============================================================

def create_booklet(
    input_pdf_path: str,
    output_pdf_path: str,
    margin_cm: float,
    add_watermark: bool,
) -> None:

    margin_pts = margin_cm * 72 / 2.54
    doc_in = fitz.open(input_pdf_path)
    doc_out = fitz.open()

    out_width = 842   # A4 landscape
    out_height = 595
    margin_out = margin_pts

    total_pages = doc_in.page_count
    while total_pages % 4 != 0:
        doc_in.insert_page(-1)
        total_pages += 1

    left_pages = list(range(total_pages - 1, total_pages // 2 - 1, -1))
    right_pages = list(range(0, total_pages // 2))

    for i, (left_idx, right_idx) in enumerate(zip(left_pages, right_pages), start=1):
        page_out = doc_out.new_page(width=out_width, height=out_height)

        page_left = doc_in[left_idx]
        page_right = doc_in[right_idx]

        bbox_left = detect_content_bbox(page_left, margin_pts)
        bbox_right = detect_content_bbox(page_right, margin_pts)

        col_width = (out_width - 3 * margin_out) / 2
        col_height = out_height - 2 * margin_out

        rot_left = (page_left.rotation + 180) % 360 if i % 2 == 1 else page_left.rotation
        rot_right = (page_right.rotation + 180) % 360 if i % 2 == 1 else page_right.rotation

        def place_page(page_in, bbox, x_pos, y_pos, rotation):
            scale = min(col_width / bbox.width, col_height / bbox.height)
            w_scaled = bbox.width * scale
            h_scaled = bbox.height * scale
            x_draw = x_pos + (col_width - w_scaled) / 2
            y_draw = y_pos + (col_height - h_scaled) / 2

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
                        fitz.Rect(x_pos, y_pos, x_pos + col_width, y_pos + col_height),
                        doc_in,
                        page_in.number,
                        rotate=rotation,
                    )
                except ValueError:
                    page_out.draw_rect(
                        fitz.Rect(x_pos, y_pos, x_pos + col_width, y_pos + col_height),
                        color=(1, 1, 1),
                        fill=(1, 1, 1),
                    )

        # IMPORTANTE: mismo intercambio que en tu script
        place_page(page_right, bbox_right, margin_out, margin_out, rot_right)
        place_page(page_left, bbox_left, margin_out * 2 + col_width, margin_out, rot_left)

        # ⚠️ MISMO comportamiento que tu booklets.py original
        if add_watermark:
            add_watermark_to_first_page(doc_out)

    doc_out.save(output_pdf_path)
    doc_out.close()
    doc_in.close()


# ============================================================
# Merge + pipeline (equivalente a main.py)
# ============================================================

def merge_pdfs(input_paths: list[str], output_path: str) -> None:
    merged = fitz.open()
    for p in input_paths:
        with fitz.open(p) as d:
            merged.insert_pdf(d)
    merged.save(output_path)
    merged.close()


@dataclass(frozen=True)
class BookletJobResult:
    job_id: str
    output_pdf_path: str


def build_booklets_pipeline(
    input_pdf_path: str,
    max_pages_per_split: int,
    same_page_parity: bool,
    margin_cm: float,
    add_watermark: bool,
    final_output_dir: str,
) -> BookletJobResult:

    job_id = uuid.uuid4().hex
    os.makedirs(final_output_dir, exist_ok=True)
    final_pdf = os.path.join(
        final_output_dir, f"{job_id}_booklets_for_printing.pdf"
    )

    with tempfile.TemporaryDirectory(prefix=f"pdf_manager_{job_id}_") as tmp:
        splits_dir = os.path.join(tmp, "splits")
        booklets_dir = os.path.join(tmp, "booklets")

        split_paths = split_pdf_to_dir(
            input_pdf_path=input_pdf_path,
            output_dir=splits_dir,
            max_pages_per_split=max_pages_per_split,
            same_page_parity=same_page_parity,
        )

        os.makedirs(booklets_dir, exist_ok=True)
        booklet_paths = []

        for sp in split_paths:
            out_book = os.path.join(booklets_dir, os.path.basename(sp))
            create_booklet(
                sp,
                out_book,
                margin_cm=margin_cm,
                add_watermark=add_watermark,
            )
            booklet_paths.append(out_book)

        merge_pdfs(booklet_paths, final_pdf)

    return BookletJobResult(job_id=job_id, output_pdf_path=final_pdf)

