# ocrpdf/views.py
from __future__ import annotations

import os
import uuid

import django_rq
from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import render
from django.urls import reverse

from .forms import OcrPdfForm
from .models import OcrJob
from .tasks import run_ocr_job


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


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


def ocr_view(request):
    created_jobs = []

    if request.method == "POST":
        form = OcrPdfForm(request.POST, request.FILES)
        if form.is_valid():
            language = (form.cleaned_data.get("language") or "spa").strip() or "spa"
            deskew = bool(form.cleaned_data.get("deskew"))
            rotate_pages = bool(form.cleaned_data.get("rotate_pages"))
            force_ocr = bool(form.cleaned_data.get("force_ocr"))
            optimize = int(form.cleaned_data.get("optimize") or 2)

            uploads_dir = os.path.join(settings.MEDIA_ROOT, "uploads_ocr")
            _ensure_dir(uploads_dir)

            files = form.cleaned_data.get("input_pdf") or []
            if not files:
                files = request.FILES.getlist("input_pdf")

            if not files:
                messages.error(request, "No se recibió ningún fichero (revisa enctype multipart).")
                return render(request, "ocrpdf/ocr_form.html", {"form": form, "jobs": []})

            q = django_rq.get_queue("default")
            for f in files:
                upload_path = _unique_path(uploads_dir, f.name)
                with open(upload_path, "wb") as out:
                    for chunk in f.chunks():
                        out.write(chunk)

                job_id = uuid.uuid4().hex
                job = OcrJob.objects.create(
                    job_id=job_id,
                    original_name=f.name,
                    input_path=upload_path,
                    status="queued",
                    language=language,
                    optimize=optimize,
                    deskew=deskew,
                    rotate_pages=rotate_pages,
                    force_ocr=force_ocr,
                )

                # Encolar background
                q.enqueue(run_ocr_job, job_id)

                created_jobs.append(
                    {
                        "job_id": job.job_id,
                        "original_name": job.original_name,
                        "status_url": reverse("ocrpdf:status", kwargs={"job_id": job.job_id}),
                        "download_url": reverse("ocrpdf:download", kwargs={"job_id": job.job_id}),
                    }
                )

            messages.success(request, f"Se han encolado {len(created_jobs)} OCR(s). Puedes dejar esta página abierta.")
            return render(
                request,
                "ocrpdf/ocr_form.html",
                {
                    "form": OcrPdfForm(
                        initial={
                            "language": language,
                            "deskew": deskew,
                            "rotate_pages": rotate_pages,
                            "force_ocr": force_ocr,
                            "optimize": str(optimize),
                        }
                    ),
                    "jobs": created_jobs,
                },
            )

        return render(request, "ocrpdf/ocr_form.html", {"form": form, "jobs": []})

    form = OcrPdfForm()
    return render(request, "ocrpdf/ocr_form.html", {"form": form, "jobs": []})


def ocr_status(request, job_id: str):
    job = OcrJob.objects.filter(job_id=job_id).first()
    if job is None:
        return JsonResponse({"status": "not_found"}, status=404)

    payload = {
        "status": job.status,
        "original_name": job.original_name,
        "error_message": job.error_message,
    }
    return JsonResponse(payload)


def download_ocr(request, job_id: str):
    job = OcrJob.objects.filter(job_id=job_id).first()
    if job is None:
        raise Http404("Job no encontrado")

    if job.status != "done" or not job.output_path or not os.path.isfile(job.output_path):
        raise Http404("Archivo no disponible todavía")

    return FileResponse(
        open(job.output_path, "rb"),
        as_attachment=True,
        filename=os.path.basename(job.output_path),
        content_type="application/pdf",
    )

