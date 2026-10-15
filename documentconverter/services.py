import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import fitz
from django.conf import settings

from printmanager.image_documents import is_supported_image, convert_uploaded_image


class ConversionError(ValueError):
    pass


def _safe_stem(name):
    return re.sub(r"[^A-Za-z0-9._-]+", "-", Path(name).stem).strip(".-") or "document"


def _save_upload(uploaded, directory):
    source_name = os.path.basename(uploaded.name) or "document"
    source_path = Path(directory) / source_name
    uploaded.seek(0)
    with source_path.open("wb") as destination:
        for chunk in uploaded.chunks():
            destination.write(chunk)
    uploaded.seek(0)
    return source_path, source_name


def convert_office_to_pdf(uploaded):
    directory = Path(settings.MEDIA_ROOT) / "document_conversions" / uuid.uuid4().hex
    directory.mkdir(parents=True, exist_ok=True)
    source_path, source_name = _save_upload(uploaded, directory)
    expected = directory / f"{source_path.stem}.pdf"
    try:
        with tempfile.TemporaryDirectory(prefix="libreoffice-profile-") as profile:
            command = [
                "libreoffice", "--headless", "--nologo", "--nodefault", "--nolockcheck",
                f"-env:UserInstallation={Path(profile).as_uri()}",
                "--convert-to", "pdf:writer_pdf_Export", "--outdir", str(directory), str(source_path),
            ]
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=settings.OFFICE_CONVERSION_TIMEOUT,
                check=False,
            )
        if result.returncode or not expected.is_file() or expected.stat().st_size == 0:
            detail = (result.stderr or result.stdout or "LibreOffice did not create a PDF.").strip()
            raise ConversionError(detail)
        with fitz.open(expected) as document:
            if not document.page_count:
                raise ConversionError("LibreOffice created a PDF without pages.")
    except (FileNotFoundError, subprocess.TimeoutExpired, fitz.FileDataError, ConversionError) as exc:
        shutil.rmtree(directory, ignore_errors=True)
        if isinstance(exc, FileNotFoundError):
            raise ConversionError("LibreOffice is not installed in the application container.") from exc
        if isinstance(exc, subprocess.TimeoutExpired):
            raise ConversionError("The document took too long to convert.") from exc
        if isinstance(exc, fitz.FileDataError):
            raise ConversionError("LibreOffice created an invalid PDF.") from exc
        raise
    return {
        "source_path": str(source_path), "source_name": source_name,
        "output_path": str(expected), "output_name": f"{_safe_stem(source_name)}.pdf",
        "kind": "office", "content_type": getattr(uploaded, "content_type", "application/octet-stream"),
    }


def convert_to_pdf(uploaded, image_options=None):
    if is_supported_image(uploaded.name):
        output_path, output_name, options, source_path, source_name = convert_uploaded_image(uploaded, image_options)
        return {
            "source_path": source_path, "source_name": source_name,
            "output_path": output_path, "output_name": output_name,
            "kind": "image", "image_options": options,
            "content_type": getattr(uploaded, "content_type", "application/octet-stream"),
        }
    return convert_office_to_pdf(uploaded)
