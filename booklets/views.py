# booklets/views.py
from __future__ import annotations

import os
from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import render
from django.urls import reverse

from .forms import BookletForm
from .services import build_booklets_pipeline


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _unique_path(dirpath: str, filename: str) -> str:
    """
    Devuelve una ruta dentro de dirpath que no exista, añadiendo _1, _2, ...
    """
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


def booklets_view(request):
    """
    GET: muestra el formulario
    POST: procesa 1 o N PDFs, genera booklet para cada uno, y lista resultados abajo
    """
    results = []

    if request.method == "POST":
        form = BookletForm(request.POST, request.FILES)
        if form.is_valid():
            # Parámetros comunes a todos los PDFs
            max_pages_per_split = form.cleaned_data["max_pages_per_split"]
            same_page_parity = (form.cleaned_data["same_page_parity"] == "true")
            margin = float(form.cleaned_data["margin"])
            add_watermark = bool(form.cleaned_data["add_watermark"])

            uploads_dir = os.path.join(settings.MEDIA_ROOT, "uploads")
            outputs_dir = os.path.join(settings.MEDIA_ROOT, "booklets_outputs")
            _ensure_dir(uploads_dir)
            _ensure_dir(outputs_dir)

            # ✅ ahora viene ya como lista gracias a MultipleFileField
            files = form.cleaned_data.get("input_pdf") or []

            # fallback defensivo por si alguna versión rara no lo rellena
            if not files:
                files = request.FILES.getlist("input_pdf")

            if not files:
                messages.error(request, "No se recibió ningún fichero (revisa enctype multipart).")
                return render(request, "booklets/booklets_form.html", {"form": form, "results": []})

            any_ok = False
            for f in files:
                upload_path = _unique_path(uploads_dir, f.name)
                with open(upload_path, "wb") as out:
                    for chunk in f.chunks():
                        out.write(chunk)

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
                    messages.error(request, f"Error generando booklets para '{f.name}': {e}")
                    continue

                any_ok = True
                results.append(
                    {
                        "original_name": f.name,
                        "download_url": reverse("booklets:download", kwargs={"job_id": result.job_id}),
                    }
                )

            if any_ok:
                messages.success(request, f"Booklets generados para {len(results)} fichero(s).")

            return render(
                request,
                "booklets/booklets_form.html",
                {
                    "form": BookletForm(initial={
                        "max_pages_per_split": max_pages_per_split,
                        "same_page_parity": "true" if same_page_parity else "false",
                        "margin": margin,
                        "add_watermark": add_watermark,
                    }),
                    "results": results,
                },
            )
        else:
            # Si no valida, mostramos errores del form (incluye input_pdf)
            return render(request, "booklets/booklets_form.html", {"form": form, "results": []})

    form = BookletForm()
    return render(request, "booklets/booklets_form.html", {"form": form, "results": results})


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

