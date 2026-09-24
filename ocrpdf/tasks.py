# ocrpdf/tasks.py
from __future__ import annotations

import os
from django.conf import settings

from activity.models import Artifact

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
        if job.activity_id:
            Artifact.objects.create(activity=job.activity, kind="output", name=os.path.basename(job.output_path), path=job.output_path, size=os.path.getsize(job.output_path))
            if not job.activity.ocr_jobs.exclude(status="done").exists():
                job.activity.status = "done"
                job.activity.save(update_fields=["status"])
    except Exception as e:
        job.status = "error"
        job.error_message = str(e)
        job.save(update_fields=["status", "error_message", "updated_at"])
        if job.activity_id:
            job.activity.status = "error"
            job.activity.save(update_fields=["status"])
