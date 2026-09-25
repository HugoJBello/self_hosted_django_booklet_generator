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
from django.views.decorators.clickjacking import xframe_options_sameorigin

from activity.services import record_activity
from activity.workspaces import prepare_workspace

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
    prepare_workspace(request, "ocrpdf")
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
                messages.error(request, "No file was received. Check that the form uses multipart encoding.")
                return render(request, "ocrpdf/ocr_form.html", {"form": form, "jobs": []})

            q = django_rq.get_queue("default")
            saved_inputs = []
            queued_data = []
            for f in files:
                upload_path = _unique_path(uploads_dir, f.name)
                with open(upload_path, "wb") as out:
                    for chunk in f.chunks():
                        out.write(chunk)

                saved_inputs.append({"name": f.name, "path": upload_path})
                queued_data.append((f.name, upload_path))

            options = {"language": language, "deskew": deskew, "rotate_pages": rotate_pages, "force_ocr": force_ocr, "optimize": optimize}
            activity = record_activity(owner=request.user, tool="ocrpdf", title=f"OCR for {len(saved_inputs)} PDF(s)", options=options, inputs=saved_inputs, restore_state={"form_initial": options}, status="queued")
            for original_name, upload_path in queued_data:
                job_id = uuid.uuid4().hex
                job = OcrJob.objects.create(
                    owner=request.user,
                    activity=activity,
                    job_id=job_id,
                    original_name=original_name,
                    input_path=upload_path,
                    status="queued",
                    language=language,
                    optimize=optimize,
                    deskew=deskew,
                    rotate_pages=rotate_pages,
                    force_ocr=force_ocr,
                )

                q.enqueue(run_ocr_job, job_id)

                created_jobs.append(
                    {
                        "job_id": job.job_id,
                        "original_name": job.original_name,
                        "status_url": reverse("ocrpdf:status", kwargs={"job_id": job.job_id}),
                        "download_url": reverse("ocrpdf:download", kwargs={"job_id": job.job_id}),
                    }
                )

            messages.success(request, f"Queued {len(created_jobs)} OCR job(s). You can leave this page open.")
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

    form = OcrPdfForm(initial=request.session.pop("activity_initial_ocrpdf", None))
    return render(request, "ocrpdf/ocr_form.html", {"form": form, "jobs": []})


def ocr_status(request, job_id: str):
    jobs = OcrJob.objects.filter(job_id=job_id)
    if not request.user.is_staff:
        jobs = jobs.filter(owner=request.user)
    job = jobs.first()
    if job is None:
        return JsonResponse({"status": "not_found"}, status=404)

    payload = {
        "status": job.status,
        "original_name": job.original_name,
        "error_message": job.error_message,
    }
    if job.status == "done":
        artifact = job.activity.artifacts.filter(kind="output", path=job.output_path).first()
        if artifact:
            payload.update({
                "preview_url": reverse("activity:preview", kwargs={"public_id": artifact.public_id}),
                "download_url": reverse("activity:file", kwargs={"public_id": artifact.public_id}),
                "print_url": f'{reverse("printmanager:print")}?artifact={artifact.pk}',
            })
        else:
            payload["status"] = "running"
    return JsonResponse(payload)


@xframe_options_sameorigin
def download_ocr(request, job_id: str):
    jobs = OcrJob.objects.filter(job_id=job_id)
    if not request.user.is_staff:
        jobs = jobs.filter(owner=request.user)
    job = jobs.first()
    if job is None:
        raise Http404("Job not found")

    if job.status != "done" or not job.output_path or not os.path.isfile(job.output_path):
        raise Http404("File is not available yet")

    return FileResponse(
        open(job.output_path, "rb"),
        as_attachment=request.GET.get("preview") != "1",
        filename=os.path.basename(job.output_path),
        content_type="application/pdf",
    )
