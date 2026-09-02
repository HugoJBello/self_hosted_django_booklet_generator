from __future__ import annotations

import os
import uuid
from typing import Any

from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.urls import reverse

from .forms import SplitPdfForm
from .services import (
    SplitPdfJobOptions,
    TocEntry,
    available_levels,
    build_sections_for_level,
    extract_toc,
    split_pdf_by_sections,
)


SESSION_KEY = "splitpdf_state"


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


def _state(request) -> dict[str, Any]:
    state = request.session.get(SESSION_KEY)
    return state if isinstance(state, dict) else {}


def _save_state(request, state: dict[str, Any]) -> None:
    request.session[SESSION_KEY] = state
    request.session.modified = True


def _save_uploaded_file(uploaded_file) -> str:
    uploads_dir = os.path.join(settings.MEDIA_ROOT, "split_uploads")
    _ensure_dir(uploads_dir)
    upload_path = _unique_path(uploads_dir, uploaded_file.name)
    with open(upload_path, "wb") as out:
        for chunk in uploaded_file.chunks():
            out.write(chunk)
    return upload_path


def _toc_from_state(state: dict[str, Any]) -> list[TocEntry]:
    entries = state.get("toc_entries")
    if not isinstance(entries, list):
        return []
    return [
        TocEntry(level=int(entry["level"]), title=str(entry["title"]), page=int(entry["page"]))
        for entry in entries
        if isinstance(entry, dict) and {"level", "title", "page"} <= set(entry)
    ]


def _level_choices(toc_entries: list[TocEntry]) -> list[tuple[str, str]]:
    choices = []
    for level in available_levels(toc_entries):
        count = sum(1 for entry in toc_entries if entry.level == level)
        choices.append((str(level), f"Level {level} ({count} sections)"))
    return choices


def _initial_from_state(state: dict[str, Any], selected_level: int | None = None) -> dict[str, Any]:
    initial = {
        "selected_level": selected_level or state.get("selected_level"),
        "apply_booklets": state.get("apply_booklets", False),
        "booklet_layout": state.get("booklet_layout", "side_by_side"),
        "max_pages_per_split": state.get("max_pages_per_split", 40),
        "margin_cm": state.get("margin_cm", 1.0),
        "side_by_side_prepare_for_portrait_printing": state.get(
            "side_by_side_prepare_for_portrait_printing",
            True,
        ),
        "flipped_a4_quality": state.get("flipped_a4_quality", "medium"),
        "flipped_a4_split_mode": state.get("flipped_a4_split_mode", "vector"),
        "flipped_a4_center_gap_cm": state.get("flipped_a4_center_gap_cm", 1.0),
        "flipped_a4_prepare_for_a5_printing": state.get("flipped_a4_prepare_for_a5_printing", False),
    }
    return initial


def _context(request, form: SplitPdfForm | None = None, sections=None) -> dict[str, Any]:
    state = _state(request)
    toc_entries = _toc_from_state(state)
    selected_level = state.get("selected_level")
    if sections is None and toc_entries and selected_level:
        try:
            sections = build_sections_for_level(toc_entries, int(state["total_pages"]), int(selected_level))
        except ValueError:
            sections = []

    return {
        "form": form
        or SplitPdfForm(level_choices=_level_choices(toc_entries), initial=_initial_from_state(state)),
        "has_pdf": bool(state.get("pdf_path")),
        "pdf_name": state.get("pdf_name", ""),
        "total_pages": state.get("total_pages"),
        "toc_entries": toc_entries,
        "level_choices": _level_choices(toc_entries),
        "selected_level": int(selected_level) if selected_level else None,
        "sections": sections or [],
        "outputs": state.get("outputs", []),
    }


def split_view(request):
    if request.method == "POST":
        action = request.POST.get("action", "").strip()
        if not action and request.FILES.get("input_pdf"):
            action = "detect"
        state = _state(request)
        toc_entries = _toc_from_state(state)

        if action == "detect":
            form = SplitPdfForm(request.POST, request.FILES, level_choices=_level_choices(toc_entries))
            if not form.is_valid():
                return render(request, "splitpdf/split_form.html", _context(request, form=form))

            uploaded_file = form.cleaned_data.get("input_pdf")
            if not uploaded_file:
                messages.error(request, "No file was received.")
                return render(request, "splitpdf/split_form.html", _context(request, form=form))

            try:
                pdf_path = _save_uploaded_file(uploaded_file)
                toc_entries, total_pages = extract_toc(pdf_path)
                levels = available_levels(toc_entries)
                if not levels:
                    raise ValueError("No table of contents was detected in this PDF.")
                selected_level = levels[0]
                sections = build_sections_for_level(toc_entries, total_pages, selected_level)
            except Exception as exc:
                messages.error(request, f"Error detecting table of contents: {exc}")
                return render(request, "splitpdf/split_form.html", _context(request, form=form))

            state = {
                "pdf_path": pdf_path,
                "pdf_name": uploaded_file.name,
                "total_pages": total_pages,
                "toc_entries": [entry.__dict__ for entry in toc_entries],
                "selected_level": selected_level,
                "outputs": [],
            }
            _save_state(request, state)
            messages.success(request, "Table of contents detected.")
            form = SplitPdfForm(level_choices=_level_choices(toc_entries), initial=_initial_from_state(state))
            return render(request, "splitpdf/split_form.html", _context(request, form=form, sections=sections))

        if action in {"preview", "generate"}:
            if not state.get("pdf_path"):
                messages.error(request, "Upload a PDF before generating sections.")
                return redirect("splitpdf:form")

            form = SplitPdfForm(request.POST, level_choices=_level_choices(toc_entries))
            if not form.is_valid():
                return render(request, "splitpdf/split_form.html", _context(request, form=form))

            selected_level = form.cleaned_data.get("selected_level")
            if not selected_level:
                messages.error(request, "Select a table-of-contents level.")
                return render(request, "splitpdf/split_form.html", _context(request, form=form))

            state.update(
                {
                    "selected_level": selected_level,
                    "apply_booklets": bool(form.cleaned_data["apply_booklets"]),
                    "booklet_layout": form.cleaned_data["booklet_layout"],
                    "max_pages_per_split": form.cleaned_data["max_pages_per_split"],
                    "margin_cm": form.cleaned_data["margin_cm"],
                    "side_by_side_prepare_for_portrait_printing": bool(
                        form.cleaned_data["side_by_side_prepare_for_portrait_printing"]
                    ),
                    "flipped_a4_quality": form.cleaned_data["flipped_a4_quality"],
                    "flipped_a4_split_mode": form.cleaned_data["flipped_a4_split_mode"],
                    "flipped_a4_center_gap_cm": form.cleaned_data["flipped_a4_center_gap_cm"],
                    "flipped_a4_prepare_for_a5_printing": bool(
                        form.cleaned_data["flipped_a4_prepare_for_a5_printing"]
                    ),
                }
            )

            try:
                sections = build_sections_for_level(toc_entries, int(state["total_pages"]), selected_level)
            except Exception as exc:
                messages.error(request, str(exc))
                return render(request, "splitpdf/split_form.html", _context(request, form=form))

            if action == "preview":
                _save_state(request, state)
                return render(request, "splitpdf/split_form.html", _context(request, form=form, sections=sections))

            options = SplitPdfJobOptions(
                apply_booklets=bool(form.cleaned_data["apply_booklets"]),
                booklet_layout=form.cleaned_data["booklet_layout"],
                max_pages_per_split=form.cleaned_data["max_pages_per_split"],
                margin_cm=form.cleaned_data["margin_cm"],
                side_by_side_prepare_for_portrait_printing=bool(
                    form.cleaned_data["side_by_side_prepare_for_portrait_printing"]
                ),
                flipped_a4_quality=form.cleaned_data["flipped_a4_quality"],
                flipped_a4_split_mode=form.cleaned_data["flipped_a4_split_mode"],
                flipped_a4_center_gap_cm=form.cleaned_data["flipped_a4_center_gap_cm"],
                flipped_a4_prepare_for_a5_printing=bool(form.cleaned_data["flipped_a4_prepare_for_a5_printing"]),
            )

            try:
                outputs_dir = os.path.join(settings.MEDIA_ROOT, "split_outputs", uuid.uuid4().hex)
                outputs = split_pdf_by_sections(state["pdf_path"], sections, outputs_dir, options)
            except Exception as exc:
                messages.error(request, f"Error generating split PDFs: {exc}")
                return render(request, "splitpdf/split_form.html", _context(request, form=form, sections=sections))

            state["outputs"] = [
                {
                    "output_id": output.output_id,
                    "title": output.title,
                    "filename": output.filename,
                    "path": output.path,
                    "start_page": output.start_page,
                    "end_page": output.end_page,
                    "page_count": output.page_count,
                    "booklet_applied": output.booklet_applied,
                    "download_url": reverse("splitpdf:download", kwargs={"output_id": output.output_id}),
                }
                for output in outputs
            ]
            _save_state(request, state)
            messages.success(request, f"Generated {len(outputs)} PDF(s).")
            return render(request, "splitpdf/split_form.html", _context(request, form=form, sections=sections))

        messages.error(request, "Unknown action.")
        return redirect("splitpdf:form")

    return render(request, "splitpdf/split_form.html", _context(request))


def clear_split(request):
    _save_state(request, {})
    messages.success(request, "Split PDF state cleared.")
    return redirect("splitpdf:form")


def download_split(request, output_id: str):
    outputs = _state(request).get("outputs", [])
    if not isinstance(outputs, list):
        outputs = []

    for output in outputs:
        if not isinstance(output, dict) or output.get("output_id") != output_id:
            continue
        path = output.get("path")
        if path and os.path.isfile(path):
            return FileResponse(
                open(path, "rb"),
                as_attachment=True,
                filename=output.get("filename") or os.path.basename(path),
                content_type="application/pdf",
            )

    raise Http404("File not found")
