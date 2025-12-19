# booklets/views.py
from __future__ import annotations

import os
from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import render, redirect
from django.urls import reverse

from .forms import BookletForm
from .services import build_booklets_pipeline


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def booklets_view(request):
    """
    GET: muestra el formulario
    POST: procesa upload+generación en un paso (simple)
    """
    if request.method == "POST":
        form = BookletForm(request.POST, request.FILES)
        if form.is_valid():
            f = form.cleaned_data["input_pdf"]

            max_pages_per_split = form.cleaned_data["max_pages_per_split"]
            same_page_parity = (form.cleaned_data["same_page_parity"] == "true")
            margin = float(form.cleaned_data["margin"])
            add_watermark = bool(form.cleaned_data["add_watermark"])

            uploads_dir = os.path.join(settings.MEDIA_ROOT, "uploads")
            _ensure_dir(uploads_dir)

            upload_path = os.path.join(uploads_dir, f.name)
            # evitar overwrite simple
            if os.path.exists(upload_path):
                base, ext = os.path.splitext(f.name)
                i = 1
                while True:
                    candidate = os.path.join(uploads_dir, f"{base}_{i}{ext}")
                    if not os.path.exists(candidate):
                        upload_path = candidate
                        break
                    i += 1

            with open(upload_path, "wb") as out:
                for chunk in f.chunks():
                    out.write(chunk)

            outputs_dir = os.path.join(settings.MEDIA_ROOT, "booklets_outputs")
            _ensure_dir(outputs_dir)

            try:
                result = build_booklets_pipeline(
                    input_pdf_path=upload_path,
                    max_pages_per_split=max_pages_per_split,
                    same_page_parity=same_page_parity,
                    margin_cm=margin,
                    add_watermark=add_watermark,
                    final_output_dir=outputs_dir,
                )
            except Exception as e:
                messages.error(request, f"Error generando booklets: {e}")
                return render(request, "booklets/booklets_form.html", {"form": form})

            messages.success(request, "Booklets generados correctamente.")
            download_url = reverse("booklets:download", kwargs={"job_id": result.job_id})
            return render(
                request,
                "booklets/booklets_form.html",
                {"form": BookletForm(initial=form.cleaned_data), "download_url": download_url},
            )
    else:
        form = BookletForm()

    return render(request, "booklets/booklets_form.html", {"form": form})


def download_booklets(request, job_id: str):
    outputs_dir = os.path.join(settings.MEDIA_ROOT, "booklets_outputs")
    pdf_path = os.path.join(outputs_dir, f"{job_id}_booklets_for_printing.pdf")

    if not os.path.isfile(pdf_path):
        raise Http404("Archivo no encontrado")

    return FileResponse(
        open(pdf_path, "rb"),
        as_attachment=True,
        filename=os.path.basename(pdf_path),
        content_type="application/pdf",
    )

