# booklets/views.py
from __future__ import annotations

import os
import uuid

from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.urls import reverse

from .forms import BookletForm
from .services import SourcePdfSpec, build_booklets_pipeline

SESSION_KEY = "booklets_items"


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


def _get_items(request) -> list[dict]:
    items = request.session.get(SESSION_KEY)
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and item.get("path")]


def _save_items(request, items: list[dict]) -> None:
    request.session[SESSION_KEY] = items
    request.session.modified = True


def _save_uploaded_file(uploaded_file) -> str:
    uploads_dir = os.path.join(settings.MEDIA_ROOT, "uploads")
    _ensure_dir(uploads_dir)
    upload_path = _unique_path(uploads_dir, uploaded_file.name)
    with open(upload_path, "wb") as out:
        for chunk in uploaded_file.chunks():
            out.write(chunk)
    return upload_path


def _parse_margin(value: str | None, filename: str) -> float:
    try:
        margin_cm = float(value or "1.0")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid margin for '{filename}'.") from exc

    if margin_cm < 0:
        raise ValueError(f"Margin cannot be negative for '{filename}'.")

    return margin_cm


def _legacy_items_from_uploads(files, request) -> list[dict]:
    items: list[dict] = []
    for idx, uploaded_file in enumerate(files):
        items.append(
            {
                "id": uuid.uuid4().hex,
                "name": uploaded_file.name,
                "path": _save_uploaded_file(uploaded_file),
                "size": uploaded_file.size,
                "same_page_parity": _parse_bool(request.POST.get(f"file_same_page_parity_{idx}"), default=True),
                "margin_cm": _parse_margin(request.POST.get(f"file_margin_{idx}", "1.0"), uploaded_file.name),
                "add_watermark": _parse_bool(request.POST.get(f"file_add_watermark_{idx}"), default=False),
            }
        )
    return items


def _items_from_request(request, files) -> list[dict]:
    posted_count = request.POST.get("file_count")
    if posted_count is None:
        return _legacy_items_from_uploads(files, request)

    try:
        count = int(posted_count)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid file list.") from exc

    current_items = {item.get("id"): item for item in _get_items(request)}
    new_files = list(files)
    items: list[dict] = []

    for idx in range(count):
        item_id = request.POST.get(f"file_item_id_{idx}", "")
        new_index_raw = request.POST.get(f"file_new_index_{idx}", "")

        if item_id:
            source = current_items.get(item_id)
            if not source:
                raise ValueError("A previously uploaded file is no longer available.")
            item = dict(source)
        else:
            try:
                new_index = int(new_index_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError("Invalid uploaded file reference.") from exc
            if new_index < 0 or new_index >= len(new_files):
                raise ValueError("Uploaded file reference is out of range.")
            uploaded_file = new_files[new_index]
            item = {
                "id": uuid.uuid4().hex,
                "name": uploaded_file.name,
                "path": _save_uploaded_file(uploaded_file),
                "size": uploaded_file.size,
            }

        item["same_page_parity"] = _parse_bool(request.POST.get(f"file_same_page_parity_{idx}"), default=True)
        item["margin_cm"] = _parse_margin(request.POST.get(f"file_margin_{idx}", "1.0"), item.get("name", "PDF"))
        item["add_watermark"] = _parse_bool(request.POST.get(f"file_add_watermark_{idx}"), default=False)
        items.append(item)

    return items


def _specs_from_items(items: list[dict]) -> list[SourcePdfSpec]:
    return [
        SourcePdfSpec(
            input_pdf_path=item["path"],
            same_page_parity=bool(item.get("same_page_parity", True)),
            margin_cm=float(item.get("margin_cm", 1.0)),
            add_watermark=bool(item.get("add_watermark", False)),
        )
        for item in items
    ]


def _items_for_template(items: list[dict]) -> list[dict]:
    return [
        {
            "id": item.get("id", ""),
            "name": item.get("name", os.path.basename(item.get("path", ""))),
            "size": item.get("size", 0),
            "parity": "true" if item.get("same_page_parity", True) else "false",
            "margin": str(item.get("margin_cm", 1.0)),
            "watermark": bool(item.get("add_watermark", False)),
        }
        for item in items
    ]

    return specs


def _build_initial_form(form: BookletForm) -> BookletForm:
    return BookletForm(
        initial={
            "processing_mode": form.cleaned_data.get("processing_mode", "separate"),
            "max_pages_per_split": form.cleaned_data.get("max_pages_per_split", 40),
            "preserve_file_parity": form.cleaned_data.get("preserve_file_parity", True),
            "generate_cover": form.cleaned_data.get("generate_cover", False),
        }
    )


def booklets_view(request):
    results = []
    items = _get_items(request)

    if request.method == "POST":
        form = BookletForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(
                request,
                "booklets/booklets_form.html",
                {"form": form, "results": [], "booklet_items": _items_for_template(items)},
            )

        files = form.cleaned_data.get("input_pdf") or request.FILES.getlist("input_pdf")
        processing_mode = form.cleaned_data["processing_mode"]
        max_pages_per_split = form.cleaned_data["max_pages_per_split"]
        preserve_file_parity = bool(form.cleaned_data["preserve_file_parity"])
        generate_cover = bool(form.cleaned_data["generate_cover"])
        outputs_dir = os.path.join(settings.MEDIA_ROOT, "booklets_outputs")
        _ensure_dir(outputs_dir)

        try:
            items = _items_from_request(request, files)
            _save_items(request, items)
            if not items:
                messages.error(request, "No file was received.")
                return render(
                    request,
                    "booklets/booklets_form.html",
                    {"form": form, "results": [], "booklet_items": _items_for_template(items)},
                )
            specs = _specs_from_items(items)
        except ValueError as exc:
            messages.error(request, str(exc))
            return render(
                request,
                "booklets/booklets_form.html",
                {"form": form, "results": [], "booklet_items": _items_for_template(items)},
            )

        try:
            if processing_mode == "combined":
                result = build_booklets_pipeline(
                    specs=specs,
                    max_pages_per_split=max_pages_per_split,
                    final_output_dir=outputs_dir,
                    preserve_file_parity=preserve_file_parity,
                    generate_cover=generate_cover,
                )
                results.append(
                    {
                        "original_name": "Combined print file",
                        "download_url": reverse("booklets:download", kwargs={"job_id": result.job_id}),
                    }
                )
                messages.success(request, "Combined booklet generated successfully.")
            else:
                for item, spec in zip(items, specs):
                    result = build_booklets_pipeline(
                        specs=[spec],
                        max_pages_per_split=max_pages_per_split,
                        final_output_dir=outputs_dir,
                        preserve_file_parity=True,
                        generate_cover=False,
                    )
                    results.append(
                        {
                            "original_name": item.get("name", os.path.basename(spec.input_pdf_path)),
                            "download_url": reverse("booklets:download", kwargs={"job_id": result.job_id}),
                        }
                    )
                messages.success(request, f"Generated booklets for {len(results)} file(s).")
        except Exception as exc:
            messages.error(request, f"Error generating booklets: {exc}")

        return render(
            request,
            "booklets/booklets_form.html",
            {
                "form": _build_initial_form(form),
                "results": results,
                "booklet_items": _items_for_template(items),
            },
        )

    return render(
        request,
        "booklets/booklets_form.html",
        {"form": BookletForm(), "results": results, "booklet_items": _items_for_template(items)},
    )


def clear_booklets(request):
    _save_items(request, [])
    messages.success(request, "File list cleared.")
    return redirect("booklets:form")


def download_booklets(request, job_id: str):
    outputs_dir = os.path.join(settings.MEDIA_ROOT, "booklets_outputs")
    pdf_path = os.path.join(outputs_dir, f"{job_id}_booklets_for_printing.pdf")

    if not os.path.isfile(pdf_path):
        raise Http404("File not found")

    return FileResponse(
        open(pdf_path, "rb"),
        as_attachment=True,
        filename=os.path.basename(pdf_path),
        content_type="application/pdf",
    )
