# booklets/views.py
from __future__ import annotations

import os

from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import render
from django.urls import reverse

from .forms import BookletForm
from .services import SourcePdfSpec, build_booklets_pipeline


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


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "on", "yes"}


def _build_specs(files, request) -> list[SourcePdfSpec]:
    specs: list[SourcePdfSpec] = []
    uploads_dir = os.path.join(settings.MEDIA_ROOT, "uploads")
    _ensure_dir(uploads_dir)

    for idx, uploaded_file in enumerate(files):
        same_page_parity = _parse_bool(request.POST.get(f"file_same_page_parity_{idx}"), default=True)
        margin_raw = request.POST.get(f"file_margin_{idx}", "1.0")
        add_watermark = _parse_bool(request.POST.get(f"file_add_watermark_{idx}"), default=False)

        try:
            margin_cm = float(margin_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Margen inválido para '{uploaded_file.name}'.") from exc

        if margin_cm < 0:
            raise ValueError(f"El margen no puede ser negativo en '{uploaded_file.name}'.")

        upload_path = _unique_path(uploads_dir, uploaded_file.name)
        with open(upload_path, "wb") as out:
            for chunk in uploaded_file.chunks():
                out.write(chunk)

        specs.append(
            SourcePdfSpec(
                input_pdf_path=upload_path,
                same_page_parity=same_page_parity,
                margin_cm=margin_cm,
                add_watermark=add_watermark,
            )
        )

    return specs


def _build_initial_form(form: BookletForm) -> BookletForm:
    return BookletForm(
        initial={
            "processing_mode": form.cleaned_data.get("processing_mode", "separate"),
            "max_pages_per_split": form.cleaned_data.get("max_pages_per_split", 40),
            "preserve_file_parity": form.cleaned_data.get("preserve_file_parity", True),
        }
    )


def booklets_view(request):
    results = []

    if request.method == "POST":
        form = BookletForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(request, "booklets/booklets_form.html", {"form": form, "results": []})

        files = form.cleaned_data.get("input_pdf") or request.FILES.getlist("input_pdf")
        if not files:
            messages.error(request, "No se recibió ningún fichero.")
            return render(request, "booklets/booklets_form.html", {"form": form, "results": []})

        processing_mode = form.cleaned_data["processing_mode"]
        max_pages_per_split = form.cleaned_data["max_pages_per_split"]
        preserve_file_parity = bool(form.cleaned_data["preserve_file_parity"])
        outputs_dir = os.path.join(settings.MEDIA_ROOT, "booklets_outputs")
        _ensure_dir(outputs_dir)

        try:
            specs = _build_specs(files, request)
        except ValueError as exc:
            messages.error(request, str(exc))
            return render(request, "booklets/booklets_form.html", {"form": form, "results": []})

        try:
            if processing_mode == "combined":
                result = build_booklets_pipeline(
                    specs=specs,
                    max_pages_per_split=max_pages_per_split,
                    final_output_dir=outputs_dir,
                    preserve_file_parity=preserve_file_parity,
                )
                results.append(
                    {
                        "original_name": "Impresión unificada",
                        "download_url": reverse("booklets:download", kwargs={"job_id": result.job_id}),
                    }
                )
                messages.success(request, "Booklet unificado generado correctamente.")
            else:
                for uploaded_file, spec in zip(files, specs):
                    result = build_booklets_pipeline(
                        specs=[spec],
                        max_pages_per_split=max_pages_per_split,
                        final_output_dir=outputs_dir,
                        preserve_file_parity=True,
                    )
                    results.append(
                        {
                            "original_name": uploaded_file.name,
                            "download_url": reverse("booklets:download", kwargs={"job_id": result.job_id}),
                        }
                    )
                messages.success(request, f"Booklets generados para {len(results)} fichero(s).")
        except Exception as exc:
            messages.error(request, f"Error generando booklets: {exc}")

        return render(
            request,
            "booklets/booklets_form.html",
            {
                "form": _build_initial_form(form),
                "results": results,
            },
        )

    return render(request, "booklets/booklets_form.html", {"form": BookletForm(), "results": results})


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
