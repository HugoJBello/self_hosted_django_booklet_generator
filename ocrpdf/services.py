# ocrpdf/services.py
from __future__ import annotations

import os
import uuid
import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class OcrJobResult:
    job_id: str
    output_pdf_path: str


def _require_ocrmypdf() -> str:
    exe = shutil.which("ocrmypdf")
    if not exe:
        raise RuntimeError(
            "No se encuentra 'ocrmypdf' en el sistema. "
            "Instálalo (p.ej. 'apt install ocrmypdf' o via pip/venv) y asegúrate de que está en PATH."
        )
    return exe


def run_ocrmypdf(
    input_pdf_path: str,
    output_pdf_path: str,
    language: str = "spa",
    deskew: bool = True,
    rotate_pages: bool = True,
    force_ocr: bool = False,
    optimize: int = 2,
) -> None:
    exe = _require_ocrmypdf()

    # Construimos comando seguro
    cmd = [
        exe,
        "--output-type", "pdf",
        "--optimize", str(int(optimize)),
    ]

    # Idioma (si viene vacío, no lo pasamos)
    lang = (language or "").strip()
    if lang:
        cmd += ["-l", lang]

    if deskew:
        cmd.append("--deskew")

    if rotate_pages:
        cmd.append("--rotate-pages")

    if force_ocr:
        cmd.append("--force-ocr")
    else:
        # Por defecto, ocrmypdf suele saltarse páginas con texto, lo cual es deseable
        # (no añadimos nada)
        pass

    # input / output
    cmd += [input_pdf_path, output_pdf_path]

    # Ejecutar
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        # devolvemos el error útil
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"OCR falló (code={proc.returncode}). Detalle: {err[:2000]}")


def build_ocr_pipeline(
    input_pdf_path: str,
    final_output_dir: str,
    language: str = "spa",
    deskew: bool = True,
    rotate_pages: bool = True,
    force_ocr: bool = False,
    optimize: int = 2,
) -> OcrJobResult:
    job_id = uuid.uuid4().hex
    os.makedirs(final_output_dir, exist_ok=True)

    final_pdf = os.path.join(final_output_dir, f"{job_id}_ocr.pdf")

    run_ocrmypdf(
        input_pdf_path=input_pdf_path,
        output_pdf_path=final_pdf,
        language=language,
        deskew=deskew,
        rotate_pages=rotate_pages,
        force_ocr=force_ocr,
        optimize=optimize,
    )

    return OcrJobResult(job_id=job_id, output_pdf_path=final_pdf)

