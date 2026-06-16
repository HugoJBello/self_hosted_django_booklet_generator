# joinpdf/services.py
from __future__ import annotations

import os
import tempfile
import uuid
from dataclasses import dataclass

import fitz  # PyMuPDF
from django.utils import timezone
from pdf_manager_project.pdf_cover import collect_cover_entries, create_cover_pdf


@dataclass(frozen=True)
class JoinJobResult:
    job_id: str
    output_pdf_path: str


def _add_blank_page(doc_out: fitz.Document, like_page: fitz.Page) -> None:
    r = like_page.rect
    doc_out.new_page(width=r.width, height=r.height)


def join_pdfs(
    input_paths: list[str],
    output_path: str,
    preserve_parity: bool,
    cover_pdf_path: str | None = None,
) -> None:
    """
    Joins PDFs in the given order.

    preserve_parity=True:
      before inserting each PDF, if the output document has an odd page count,
      a blank page is added so the next PDF starts on an odd page.
    """
    if not input_paths:
        raise ValueError("There are no PDFs to join")

    out = fitz.open()

    if cover_pdf_path:
        with fitz.open(cover_pdf_path) as cover:
            if cover.page_count > 0:
                out.insert_pdf(cover)

    for i, p in enumerate(input_paths):
        if not os.path.isfile(p):
            raise FileNotFoundError(f"File does not exist: {p}")

        with fitz.open(p) as d:
            if d.page_count == 0:
                continue

            # If each section should start on an odd page, add a blank when the
            # current output page count would make the next page even.
            if preserve_parity and out.page_count > 0 and (out.page_count % 2 == 1):
                _add_blank_page(out, d[0])

            out.insert_pdf(d)

    if out.page_count == 0:
        out.close()
        raise ValueError("Empty result (all PDFs were empty)")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    out.save(output_path)
    out.close()


def build_join_pipeline(
    input_paths: list[str],
    final_output_dir: str,
    preserve_parity: bool,
    generate_cover: bool = False,
    display_names: list[str] | None = None,
) -> JoinJobResult:
    job_id = uuid.uuid4().hex
    os.makedirs(final_output_dir, exist_ok=True)
    final_pdf = os.path.join(final_output_dir, f"{job_id}_joined.pdf")

    with tempfile.TemporaryDirectory(prefix=f"pdf_manager_join_{job_id}_") as tmp:
        cover_pdf_path = None
        if generate_cover:
            cover_pdf_path = os.path.join(tmp, "cover.pdf")
            create_cover_pdf(
                output_path=cover_pdf_path,
                entries=collect_cover_entries(input_paths, display_names=display_names),
                generated_on=timezone.localdate(),
                heading="Document index",
            )

        join_pdfs(
            input_paths=input_paths,
            output_path=final_pdf,
            preserve_parity=preserve_parity,
            cover_pdf_path=cover_pdf_path,
        )

    return JoinJobResult(job_id=job_id, output_pdf_path=final_pdf)
