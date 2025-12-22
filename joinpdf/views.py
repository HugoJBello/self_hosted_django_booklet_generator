# joinpdf/views.py
from __future__ import annotations

import os
from typing import Any

from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.urls import reverse

from .forms import JoinUploadForm, JoinRunForm
from .services import build_join_pipeline


SESSION_KEY = "joinpdf_items"


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


def _get_items(request) -> list[dict[str, Any]]:
    items = request.session.get(SESSION_KEY)
    if not isinstance(items, list):
        items = []
    return items


def _save_items(request, items: list[dict[str, Any]]) -> None:
    request.session[SESSION_KEY] = items
    request.session.modified = True


def join_view(request):
    uploads_dir = os.path.join(settings.MEDIA_ROOT, "join_uploads")
    outputs_dir = os.path.join(settings.MEDIA_ROOT, "join_outputs")
    _ensure_dir(uploads_dir)
    _ensure_dir(outputs_dir)

    items = _get_items(request)
    upload_form = JoinUploadForm()
    run_form = JoinRunForm()

    if request.method == "POST":
        action = request.POST.get("action", "").strip()

        # -------------------------
        # ACTION: UPLOAD (acumular)
        # -------------------------
        if action == "upload":
            upload_form = JoinUploadForm(request.POST, request.FILES)
            run_form = JoinRunForm()  # vacío

            if upload_form.is_valid():
                files = upload_form.cleaned_data.get("input_pdf") or []
                if not files:
                    # fallback defensivo
                    files = request.FILES.getlist("input_pdf")

                if not files:
                    messages.error(request, "No se recibió ningún fichero.")
                    return render(
                        request,
                        "joinpdf/join_form.html",
                        {"upload_form": upload_form, "run_form": run_form, "items": items},
                    )

                added = 0
                for f in files:
                    upload_path = _unique_path(uploads_dir, f.name)
                    with open(upload_path, "wb") as out:
                        for chunk in f.chunks():
                            out.write(chunk)

                    items.append({"name": f.name, "path": upload_path})
                    added += 1

                _save_items(request, items)
                messages.success(request, f"Añadido(s) {added} PDF(s) a la lista.")
                return redirect("joinpdf:form")

            return render(
                request,
                "joinpdf/join_form.html",
                {"upload_form": upload_form, "run_form": run_form, "items": items},
            )

        # -------------------------
        # ACTION: JOIN (generar)
        # -------------------------
        if action == "join":
            run_form = JoinRunForm(request.POST)
            upload_form = JoinUploadForm()  # vacío

            if not items:
                messages.error(request, "La lista está vacía. Sube algún PDF primero.")
                return redirect("joinpdf:form")

            if run_form.is_valid():
                preserve_parity = bool(run_form.cleaned_data.get("preserve_parity"))

                input_paths = [it.get("path") for it in items if it.get("path")]
                try:
                    result = build_join_pipeline(
                        input_paths=input_paths,
                        final_output_dir=outputs_dir,
                        preserve_parity=preserve_parity,
                    )
                except Exception as e:
                    messages.error(request, f"Error uniendo PDFs: {e}")
                    return redirect("joinpdf:form")

                # dejamos la lista intacta (por si el usuario quiere re-join con otra opción)
                messages.success(request, "PDF unido generado correctamente.")
                download_url = reverse("joinpdf:download", kwargs={"job_id": result.job_id})

                return render(
                    request,
                    "joinpdf/join_form.html",
                    {
                        "upload_form": upload_form,
                        "run_form": JoinRunForm(initial={"preserve_parity": preserve_parity}),
                        "items": items,
                        "result_download_url": download_url,
                    },
                )

            return render(
                request,
                "joinpdf/join_form.html",
                {"upload_form": upload_form, "run_form": run_form, "items": items},
            )

        messages.error(request, "Acción no reconocida.")
        return redirect("joinpdf:form")

    return render(
        request,
        "joinpdf/join_form.html",
        {"upload_form": upload_form, "run_form": run_form, "items": items},
    )


def join_remove(request, idx: int):
    items = _get_items(request)
    if 0 <= idx < len(items):
        removed = items.pop(idx)
        _save_items(request, items)
        messages.success(request, f"Eliminado: {removed.get('name','(sin nombre)')}")
    else:
        messages.error(request, "Índice inválido.")
    return redirect("joinpdf:form")


def join_clear(request):
    _save_items(request, [])
    messages.success(request, "Lista limpiada.")
    return redirect("joinpdf:form")


def join_download(request, job_id: str):
    outputs_dir = os.path.join(settings.MEDIA_ROOT, "join_outputs")
    pdf_path = os.path.join(outputs_dir, f"{job_id}_joined.pdf")

    if not os.path.isfile(pdf_path):
        raise Http404("Archivo no encontrado")

    return FileResponse(
        open(pdf_path, "rb"),
        as_attachment=True,
        filename=os.path.basename(pdf_path),
        content_type="application/pdf",
    )

