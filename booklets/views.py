# booklets/views.py
from __future__ import annotations

import os
import uuid

import fitz
from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.urls import reverse

from activity.services import record_activity
from activity.models import Artifact
from activity.workspaces import continue_workspace, prepare_workspace
from activity.watermark import numbered_sections

from .forms import BookletForm, BookletUploadForm
from .flipped_a4 import build_flipped_a4_booklets_pipeline
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
        return _legacy_items_from_uploads(files, request) if files else _get_items(request)

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


def _specs_from_items(items: list[dict], margin_cm: float) -> list[SourcePdfSpec]:
    return [
        SourcePdfSpec(
            input_pdf_path=item["path"],
            same_page_parity=bool(item.get("same_page_parity", True)),
            margin_cm=margin_cm,
            add_watermark=bool(item.get("add_watermark", False)),
        )
        for item in items
    ]


def _items_for_template(items: list[dict]) -> list[dict]:
    template_items = []
    for index, item in enumerate(items):
        template_items.append({
            "id": item.get("id", ""),
            "name": item.get("name", os.path.basename(item.get("path", ""))),
            "size": item.get("size", 0),
            "page_count": item.get("page_count"),
            "parity": "true" if item.get("same_page_parity", True) else "false",
            "margin": str(item.get("margin_cm", 1.0)),
            "watermark": bool(item.get("add_watermark", False)),
            "preview_url": reverse("activity:workspace_preview", kwargs={"tool": "booklets", "file_id": item.get("id") or str(index)}),
        })
    return template_items

def _build_initial_form(form: BookletForm) -> BookletForm:
    return BookletForm(
        initial={
            "processing_mode": form.cleaned_data.get("processing_mode", "separate"),
            "booklet_layout": form.cleaned_data.get("booklet_layout", "side_by_side"),
            "max_pages_per_split": form.cleaned_data.get("max_pages_per_split"),
            "margin_cm": form.cleaned_data.get("margin_cm", 1.0),
            "preserve_file_parity": form.cleaned_data.get("preserve_file_parity", True),
            "generate_cover": form.cleaned_data.get("generate_cover", False),
            "watermark": form.cleaned_data.get("watermark", False),
            "watermark_filename": form.cleaned_data.get("watermark_filename", False),
            "watermark_section_index": form.cleaned_data.get("watermark_section_index", True),
            "side_by_side_prepare_for_portrait_printing": form.cleaned_data.get(
                "side_by_side_prepare_for_portrait_printing",
                True,
            ),
            "flipped_a4": form.cleaned_data.get("booklet_layout") == "flipped_a4",
            "flipped_a4_quality": form.cleaned_data.get("flipped_a4_quality", "medium"),
            "flipped_a4_split_mode": form.cleaned_data.get("flipped_a4_split_mode", "vector"),
            "flipped_a4_center_gap_cm": form.cleaned_data.get("flipped_a4_center_gap_cm", 1.0),
            "flipped_a4_prepare_for_a5_printing": form.cleaned_data.get("flipped_a4_prepare_for_a5_printing", False),
        }
    )


def _render_booklets(request, *, form, items, results=(), upload_form=None):
    return render(request, "booklets/booklets_form.html", {
        "form": form,
        "upload_form": upload_form or BookletUploadForm(),
        "results": results,
        "booklet_items": _items_for_template(items),
    })


def booklets_view(request):
    prepare_workspace(request, "booklets", SESSION_KEY)
    results = []
    items = _get_items(request)

    if request.method == "POST":
        if request.POST.get("action") == "upload":
            upload_form = BookletUploadForm(request.POST, request.FILES)
            if upload_form.is_valid():
                new_items = []
                saved_paths = []
                try:
                    retained_items = _items_from_request(request, [])
                    for uploaded in upload_form.cleaned_data["input_pdf"]:
                        path = _save_uploaded_file(uploaded)
                        saved_paths.append(path)
                        with fitz.open(path) as document:
                            if not document.is_pdf or not document.page_count:
                                raise ValueError(f"'{uploaded.name}' is not a readable PDF with pages.")
                            page_count = document.page_count
                        new_items.append({
                            "id": uuid.uuid4().hex, "name": uploaded.name, "path": path,
                            "size": uploaded.size, "page_count": page_count,
                            "same_page_parity": True, "margin_cm": 1.0, "add_watermark": True,
                        })
                except (fitz.FileDataError, OSError, ValueError) as exc:
                    for path in saved_paths:
                        if os.path.exists(path):
                            os.unlink(path)
                    upload_form.add_error("input_pdf", f"A PDF could not be loaded: {exc}")
                else:
                    _save_items(request, retained_items + new_items)
                    if request.POST.get("processing_mode"):
                        settings_form = BookletForm(request.POST)
                        if settings_form.is_valid():
                            request.session["booklets_pending_options"] = {
                                key: value for key, value in settings_form.cleaned_data.items() if key != "input_pdf"
                            }
                    messages.success(request, f"{len(new_items)} PDF(s) uploaded. Review the files and generate your booklets.")
                    continue_workspace(request, "booklets")
                    return redirect("booklets:form")
            return _render_booklets(request, form=BookletForm(), items=items, upload_form=upload_form)

        form = BookletForm(request.POST, request.FILES)
        if not form.is_valid():
            return _render_booklets(request, form=form, items=items)

        files = form.cleaned_data.get("input_pdf") or request.FILES.getlist("input_pdf")
        processing_mode = form.cleaned_data["processing_mode"]
        booklet_layout = form.cleaned_data["booklet_layout"]
        max_pages_per_split = form.cleaned_data["max_pages_per_split"]
        margin_cm = form.cleaned_data["margin_cm"]
        preserve_file_parity = bool(form.cleaned_data["preserve_file_parity"])
        generate_cover = bool(form.cleaned_data["generate_cover"])
        flipped_a4 = booklet_layout == "flipped_a4"
        side_by_side_prepare_for_portrait_printing = bool(
            form.cleaned_data["side_by_side_prepare_for_portrait_printing"]
        )
        flipped_a4_quality = form.cleaned_data["flipped_a4_quality"]
        flipped_a4_split_mode = form.cleaned_data["flipped_a4_split_mode"]
        flipped_a4_center_gap_cm = form.cleaned_data["flipped_a4_center_gap_cm"]
        flipped_a4_prepare_for_a5_printing = bool(form.cleaned_data["flipped_a4_prepare_for_a5_printing"])
        outputs_dir = os.path.join(settings.MEDIA_ROOT, "booklets_outputs")
        _ensure_dir(outputs_dir)

        generated_outputs = []
        try:
            items = _items_from_request(request, files)
            _save_items(request, items)
            if not items:
                messages.error(request, "Upload at least one PDF before generating booklets.")
                return _render_booklets(request, form=form, items=items)
            specs = _specs_from_items(items, margin_cm=margin_cm)
        except ValueError as exc:
            messages.error(request, str(exc))
            return _render_booklets(request, form=form, items=items)

        try:
            pipeline = build_flipped_a4_booklets_pipeline if flipped_a4 else build_booklets_pipeline
            if processing_mode == "combined":
                pipeline_kwargs = {
                    "specs": specs,
                    "max_pages_per_split": max_pages_per_split,
                    "final_output_dir": outputs_dir,
                    "preserve_file_parity": preserve_file_parity,
                    "generate_cover": generate_cover,
                }
                if flipped_a4:
                    pipeline_kwargs["render_quality"] = flipped_a4_quality
                    pipeline_kwargs["split_mode"] = flipped_a4_split_mode
                    pipeline_kwargs["center_gap_cm"] = flipped_a4_center_gap_cm
                    pipeline_kwargs["prepare_for_a5_printing"] = flipped_a4_prepare_for_a5_printing
                else:
                    pipeline_kwargs["prepare_for_portrait_printing"] = side_by_side_prepare_for_portrait_printing
                result = pipeline(**pipeline_kwargs)
                results.append(
                    {
                        "original_name": "Combined print file",
                        "download_url": reverse("booklets:download", kwargs={"job_id": result.job_id}),
                    }
                )
                generated_outputs.append({
                    "name": os.path.basename(result.output_pdf_path), "path": result.output_pdf_path,
                    "section_marks": numbered_sections(result.section_ranges),
                })
                messages.success(request, "Combined booklet generated successfully.")
            else:
                for output_index, (item, spec) in enumerate(zip(items, specs), start=1):
                    pipeline_kwargs = {
                        "specs": [spec],
                        "max_pages_per_split": max_pages_per_split,
                        "final_output_dir": outputs_dir,
                        "preserve_file_parity": True,
                        "generate_cover": generate_cover if flipped_a4 else False,
                    }
                    if flipped_a4:
                        pipeline_kwargs["render_quality"] = flipped_a4_quality
                        pipeline_kwargs["split_mode"] = flipped_a4_split_mode
                        pipeline_kwargs["center_gap_cm"] = flipped_a4_center_gap_cm
                        pipeline_kwargs["prepare_for_a5_printing"] = flipped_a4_prepare_for_a5_printing
                    else:
                        pipeline_kwargs["prepare_for_portrait_printing"] = side_by_side_prepare_for_portrait_printing
                    result = pipeline(**pipeline_kwargs)
                    results.append(
                        {
                            "original_name": item.get("name", os.path.basename(spec.input_pdf_path)),
                            "download_url": reverse("booklets:download", kwargs={"job_id": result.job_id}),
                        }
                    )
                    generated_outputs.append({
                        "name": os.path.basename(result.output_pdf_path), "path": result.output_pdf_path,
                        "section_marks": numbered_sections(result.section_ranges, parent=output_index),
                    })
                messages.success(request, f"Generated booklets for {len(results)} file(s).")
        except Exception as exc:
            messages.error(request, f"Error generating booklets: {exc}")

        if generated_outputs:
            options = {key: value for key, value in form.cleaned_data.items() if key != "input_pdf"}
            activity = record_activity(
                owner=request.user, tool="booklets", title=f"{len(items)} source PDF(s)", options=options,
                inputs=[{"name": item.get("name"), "path": item["path"]} for item in items], outputs=generated_outputs,
                restore_state={"session_key": SESSION_KEY, "session_value": items, "form_initial": options},
                generated_names=True,
            )
            output_artifacts = list(activity.artifacts.filter(kind="output"))
            for result_item, artifact in zip(results, output_artifacts):
                result_item["filename"] = artifact.name
                result_item["size"] = artifact.size
                result_item["download_url"] = reverse("activity:file", kwargs={"public_id": artifact.public_id})
                result_item["preview_url"] = reverse("activity:preview", kwargs={"public_id": artifact.public_id})
                result_item["artifact_id"] = artifact.pk

        return _render_booklets(request, form=_build_initial_form(form), items=items, results=results)

    initial = request.session.pop("booklets_pending_options", None)
    if initial is None:
        initial = request.session.pop("activity_initial_booklets", None)
    return _render_booklets(request, form=BookletForm(initial=initial), items=items)


def clear_booklets(request):
    _save_items(request, [])
    request.session.pop("booklets_pending_options", None)
    messages.success(request, "File list cleared.")
    return redirect("booklets:form")


def download_booklets(request, job_id: str):
    outputs_dir = os.path.join(settings.MEDIA_ROOT, "booklets_outputs")
    pdf_path = os.path.join(outputs_dir, f"{job_id}_booklets_for_printing.pdf")
    if not os.path.isfile(pdf_path):
        pdf_path = os.path.join(outputs_dir, f"{job_id}_flipped_a4_booklets_for_printing.pdf")

    if not os.path.isfile(pdf_path):
        raise Http404("File not found")

    allowed = Artifact.objects.filter(kind="output", path=pdf_path)
    if not request.user.is_staff:
        allowed = allowed.filter(activity__owner=request.user)
    artifact = allowed.first()
    if not artifact:
        raise Http404("File not found")

    return FileResponse(
        open(pdf_path, "rb"),
        as_attachment=True,
        filename=artifact.name,
        content_type="application/pdf",
    )
