# ocrpdf/tasks.py
from __future__ import annotations

import os
from django.conf import settings

from .models import OcrJob
from .services import build_ocr_pipeline


def run_ocr_job(job_id: str) -> None:
    job = OcrJob.objects.get(job_id=job_id)
    job.status = "running"
    job.error_message = ""
    job.save(update_fields=["status", "error_message", "updated_at"])

    outputs_dir = os.path.join(settings.MEDIA_ROOT, "ocr_outputs")
    os.makedirs(outputs_dir, exist_ok=True)

    try:
        result = build_ocr_pipeline(
            input_pdf_path=job.input_path,
            final_output_dir=outputs_dir,
            language=job.language,
            deskew=job.deskew,
            rotate_pages=job.rotate_pages,
            force_ocr=job.force_ocr,
            optimize=job.optimize,
        )
        job.output_path = result.output_pdf_path
        job.status = "done"
        job.save(update_fields=["output_path", "status", "updated_at"])
    except Exception as e:
        job.status = "error"
        job.error_message = str(e)
        job.save(update_fields=["status", "error_message", "updated_at"])

