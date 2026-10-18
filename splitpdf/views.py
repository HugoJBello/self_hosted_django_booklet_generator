from __future__ import annotations

from io import BytesIO
import os
import uuid
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from activity.services import json_safe, record_activity
from activity.workspaces import continue_workspace, prepare_workspace

from .forms import SplitPdfForm
from .services import (
    SplitPdfJobOptions,
    SplitSection,
    SplitSectionPart,
    TocEntry,
    available_levels,
    build_sections_for_page_ranges,
    build_sections_for_level,
    count_pdf_pages,
    extract_toc,
    merge_adjacent_sections,
    merge_sections,
    renumber_sections,
    render_page_thumbnail_data_uri,
    section_can_split,
    split_section_for_preview,
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


def _sections_to_state(sections: list[SplitSection]) -> list[dict[str, Any]]:
    return [
        {
            "section_id": section.section_id,
            "index": section.index,
            "level": section.level,
            "title": section.title,
            "start_page": section.start_page,
            "end_page": section.end_page,
            "filename": section.filename,
            "toc_indexes": list(section.toc_indexes),
            "parts": [
                {
                    "level": part.level,
                    "title": part.title,
                    "start_page": part.start_page,
                    "end_page": part.end_page,
                    "toc_index": part.toc_index,
                }
                for part in section.included_parts
            ],
        }
        for section in sections
    ]


def _sections_from_state(state: dict[str, Any]) -> list[SplitSection]:
    raw_sections = state.get("preview_sections")
    if not isinstance(raw_sections, list):
        return []

    sections = []
    for raw in raw_sections:
        if not isinstance(raw, dict):
            continue
        try:
            parts = tuple(
                SplitSectionPart(
                    level=int(part["level"]),
                    title=str(part["title"]),
                    start_page=int(part["start_page"]),
                    end_page=int(part["end_page"]),
                    toc_index=int(part["toc_index"]) if part.get("toc_index") is not None else None,
                )
                for part in raw.get("parts", [])
                if isinstance(part, dict) and {"level", "title", "start_page", "end_page"} <= set(part)
            )
            sections.append(
                SplitSection(
                    section_id=str(raw["section_id"]),
                    index=int(raw["index"]),
                    level=int(raw["level"]),
                    title=str(raw["title"]),
                    start_page=int(raw["start_page"]),
                    end_page=int(raw["end_page"]),
                    filename=str(raw["filename"]),
                    toc_indexes=tuple(int(idx) for idx in raw.get("toc_indexes", [])),
                    parts=parts,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return renumber_sections(sections)


def _level_choices(toc_entries: list[TocEntry]) -> list[tuple[str, str]]:
    choices = [("", "Choose a table-of-contents level")]
    for level in available_levels(toc_entries):
        count = sum(1 for entry in toc_entries if entry.level == level)
        choices.append((str(level), f"Level {level} ({count} sections)"))
    return choices


def _initial_from_state(state: dict[str, Any], selected_level: int | None = None) -> dict[str, Any]:
    initial = {
        "selected_level": selected_level or state.get("selected_level"),
        "split_mode": state.get("split_mode", ""),
        "page_ranges": state.get("page_ranges", ""),
        "apply_booklets": state.get("apply_booklets", False),
        "booklet_layout": state.get("booklet_layout", "side_by_side"),
        "max_pages_per_split": state.get("max_pages_per_split", 40),
        "margin_cm": state.get("margin_cm", 1.0),
        "preserve_file_parity": state.get("preserve_file_parity", True),
        "same_page_parity": "true" if state.get("same_page_parity", True) else "false",
        "side_by_side_prepare_for_portrait_printing": state.get(
            "side_by_side_prepare_for_portrait_printing",
            True,
        ),
        "flipped_a4_quality": state.get("flipped_a4_quality", "medium"),
        "flipped_a4_split_mode": state.get("flipped_a4_split_mode", "vector"),
        "flipped_a4_center_gap_cm": state.get("flipped_a4_center_gap_cm", 1.0),
        "flipped_a4_prepare_for_a5_printing": state.get("flipped_a4_prepare_for_a5_printing", False),
        "watermark": state.get("watermark", False),
        "watermark_filename": state.get("watermark_filename", False),
    }
    return initial


def _context(request, form: SplitPdfForm | None = None, sections=None) -> dict[str, Any]:
    state = _state(request)
    toc_entries = _toc_from_state(state)
    selected_level = state.get("selected_level")
    split_mode = state.get("split_mode", "")
    if sections is None:
        sections = _sections_from_state(state)
    if not sections and state.get("pdf_path") and not state.get("preview_initialized"):
        try:
            if split_mode == "ranges":
                sections = build_sections_for_page_ranges(state.get("page_ranges", ""), int(state["total_pages"]))
            elif toc_entries and selected_level:
                sections = build_sections_for_level(toc_entries, int(state["total_pages"]), int(selected_level))
        except ValueError:
            sections = []

    sections = renumber_sections(sections or [])

    return {
        "form": form
        or SplitPdfForm(level_choices=_level_choices(toc_entries), initial=_initial_from_state(state)),
        "has_pdf": bool(state.get("pdf_path")),
        "pdf_name": state.get("pdf_name", ""),
        "total_pages": state.get("total_pages"),
        "toc_entries": toc_entries,
        "level_choices": _level_choices(toc_entries),
        "selected_level": int(selected_level) if selected_level else None,
        "split_mode": split_mode,
        "page_ranges": state.get("page_ranges", ""),
        "has_toc": bool(toc_entries),
        "sections": _sections_for_template(
            sections,
            toc_entries,
            int(state["total_pages"]) if state.get("total_pages") else 0,
            split_mode=split_mode,
            input_pdf_path=state.get("pdf_path", ""),
        ),
        "section_count": len(sections),
        "final_page_count": sum(section.page_count for section in sections),
        "outputs": state.get("outputs", []),
    }


def split_view(request):
    prepare_workspace(request, "splitpdf", SESSION_KEY)
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
            except Exception as exc:
                try:
                    total_pages = count_pdf_pages(pdf_path) if "pdf_path" in locals() else 0
                except Exception:
                    messages.error(request, f"Error reading PDF: {exc}")
                    return render(request, "splitpdf/split_form.html", _context(request, form=form))
                toc_entries = []
                levels = []

            selected_level = None
            split_mode = ""
            page_ranges = ""
            sections = []

            state = {
                "pdf_path": pdf_path,
                "pdf_name": uploaded_file.name,
                "total_pages": total_pages,
                "toc_entries": [entry.__dict__ for entry in toc_entries],
                "selected_level": selected_level,
                "split_mode": split_mode,
                "page_ranges": page_ranges,
                "preview_sections": _sections_to_state(sections),
                "preview_initialized": False,
                "outputs": [],
            }
            _save_state(request, state)
            if not levels:
                messages.warning(
                    request,
                    "PDF loaded. No table of contents was detected, so choose page ranges to continue.",
                )
            else:
                messages.success(request, "PDF loaded. Now choose how you want to split it.")
            form = SplitPdfForm(level_choices=_level_choices(toc_entries), initial=_initial_from_state(state))
            return render(request, "splitpdf/split_form.html", _context(request, form=form, sections=sections))

        if action in {
            "preview", "generate", "split_section", "merge_next", "delete_section",
            "merge_selected", "delete_selected", "reorder_sections",
        }:
            if not state.get("pdf_path"):
                messages.error(request, "Upload a PDF before generating sections.")
                return redirect("splitpdf:form")

            form = SplitPdfForm(request.POST, level_choices=_level_choices(toc_entries))
            if not form.is_valid():
                return render(request, "splitpdf/split_form.html", _context(request, form=form))

            split_mode = form.cleaned_data.get("split_mode", "")
            selected_level = form.cleaned_data.get("selected_level")
            if not split_mode:
                messages.error(request, "Choose a split method before starting the preview.")
                return render(request, "splitpdf/split_form.html", _context(request, form=form))
            if split_mode == "toc" and not selected_level:
                messages.error(request, "Select a table-of-contents level.")
                return render(request, "splitpdf/split_form.html", _context(request, form=form))

            previous_level = state.get("selected_level")
            previous_mode = state.get("split_mode", "")
            state.update(
                {
                    "selected_level": selected_level,
                    "split_mode": split_mode,
                    "page_ranges": form.cleaned_data.get("page_ranges", ""),
                    "apply_booklets": bool(form.cleaned_data["apply_booklets"]),
                    "watermark": bool(form.cleaned_data["watermark"]),
                    "watermark_filename": bool(form.cleaned_data["watermark_filename"]),
                    "booklet_layout": form.cleaned_data["booklet_layout"],
                    "max_pages_per_split": form.cleaned_data["max_pages_per_split"],
                    "margin_cm": form.cleaned_data["margin_cm"],
                    "preserve_file_parity": bool(form.cleaned_data["preserve_file_parity"]),
                    "same_page_parity": bool(form.cleaned_data["same_page_parity"]),
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
                if split_mode == "ranges":
                    sections = build_sections_for_page_ranges(state.get("page_ranges", ""), int(state["total_pages"]))
                elif (
                    action == "preview"
                    or previous_mode != split_mode
                    or int(selected_level) != int(previous_level or selected_level)
                ):
                    sections = build_sections_for_level(toc_entries, int(state["total_pages"]), selected_level)
                else:
                    sections = _sections_from_state(state)
                    if not sections and not state.get("preview_initialized"):
                        sections = build_sections_for_level(toc_entries, int(state["total_pages"]), selected_level)
            except Exception as exc:
                messages.error(request, str(exc))
                return render(request, "splitpdf/split_form.html", _context(request, form=form))

            if action == "preview":
                state["preview_sections"] = _sections_to_state(sections)
                state["preview_initialized"] = True
                state["outputs"] = []
                _save_state(request, state)
                return render(request, "splitpdf/split_form.html", _context(request, form=form, sections=sections))

            if action == "split_section":
                if split_mode != "toc":
                    messages.warning(request, "Interactive split is only available in table-of-contents mode.")
                    return render(request, "splitpdf/split_form.html", _context(request, form=form, sections=sections))
                section_id = request.POST.get("section_id", "")
                split_strategy = request.POST.get("split_strategy", "automatic")
                split_after_page = None
                if split_strategy == "page":
                    try:
                        split_after_page = int(request.POST.get("split_page", ""))
                    except (TypeError, ValueError):
                        messages.error(request, "Choose a valid page after which to split the section.")
                        return render(request, "splitpdf/split_form.html", _context(request, form=form, sections=sections))
                try:
                    updated_sections = _split_preview_section(
                        sections,
                        section_id,
                        split_after_page=split_after_page,
                    )
                except ValueError as exc:
                    messages.error(request, str(exc))
                    return render(request, "splitpdf/split_form.html", _context(request, form=form, sections=sections))
                if _section_ids(updated_sections) == _section_ids(sections):
                    messages.warning(request, "That section cannot be split any further.")
                else:
                    sections = updated_sections
                    messages.success(request, "Section split updated.")
                state["preview_sections"] = _sections_to_state(sections)
                state["preview_initialized"] = True
                state["outputs"] = []
                _save_state(request, state)
                return render(request, "splitpdf/split_form.html", _context(request, form=form, sections=sections))

            if action == "delete_section":
                if split_mode != "toc":
                    messages.warning(request, "Interactive deletion is only available in table-of-contents mode.")
                    return render(request, "splitpdf/split_form.html", _context(request, form=form, sections=sections))
                section_id = request.POST.get("section_id", "")
                updated_sections = _delete_preview_section(sections, section_id)
                if len(updated_sections) == len(sections):
                    messages.warning(request, "That section is no longer in the preview.")
                else:
                    sections = updated_sections
                    messages.success(request, "Section removed from the output preview.")
                state["preview_sections"] = _sections_to_state(sections)
                state["preview_initialized"] = True
                state["outputs"] = []
                _save_state(request, state)
                return render(request, "splitpdf/split_form.html", _context(request, form=form, sections=sections))

            if action == "delete_selected":
                selected_ids = _posted_section_ids(request.POST.get("selected_sections", ""))
                updated_sections = [section for section in sections if section.section_id not in selected_ids]
                if not selected_ids or len(updated_sections) == len(sections):
                    messages.warning(request, "Select at least one section to delete.")
                else:
                    sections = renumber_sections(updated_sections)
                    messages.success(request, f"Removed {len(selected_ids)} selected section(s) from the output.")
                state["preview_sections"] = _sections_to_state(sections)
                state["preview_initialized"] = True
                state["outputs"] = []
                _save_state(request, state)
                return render(request, "splitpdf/split_form.html", _context(request, form=form, sections=sections))

            if action == "merge_selected":
                selected_ids = _posted_section_ids(request.POST.get("selected_sections", ""))
                try:
                    sections = _merge_selected_sections(sections, selected_ids)
                except ValueError as exc:
                    messages.error(request, str(exc))
                    return render(request, "splitpdf/split_form.html", _context(request, form=form, sections=sections))
                messages.success(request, f"Merged {len(selected_ids)} sections into one output.")
                state["preview_sections"] = _sections_to_state(sections)
                state["preview_initialized"] = True
                state["outputs"] = []
                _save_state(request, state)
                return render(request, "splitpdf/split_form.html", _context(request, form=form, sections=sections))

            if action == "reorder_sections":
                ordered_ids = _posted_section_ids(request.POST.get("ordered_sections", ""))
                try:
                    sections = _reorder_preview_sections(sections, ordered_ids)
                except ValueError as exc:
                    messages.error(request, str(exc))
                    return render(request, "splitpdf/split_form.html", _context(request, form=form, sections=sections))
                messages.success(request, "Output order updated.")
                state["preview_sections"] = _sections_to_state(sections)
                state["preview_initialized"] = True
                state["outputs"] = []
                _save_state(request, state)
                return render(request, "splitpdf/split_form.html", _context(request, form=form, sections=sections))

            if action == "merge_next":
                if split_mode != "toc":
                    messages.warning(request, "Interactive merge is only available in table-of-contents mode.")
                    return render(request, "splitpdf/split_form.html", _context(request, form=form, sections=sections))
                section_id = request.POST.get("section_id", "")
                updated_sections = _merge_preview_section(sections, section_id)
                if _section_ids(updated_sections) == _section_ids(sections):
                    messages.warning(request, "That section cannot be merged because there is no next output.")
                else:
                    sections = updated_sections
                    messages.success(request, "Sections merged.")
                state["preview_sections"] = _sections_to_state(sections)
                state["preview_initialized"] = True
                state["outputs"] = []
                _save_state(request, state)
                return render(request, "splitpdf/split_form.html", _context(request, form=form, sections=sections))

            options = SplitPdfJobOptions(
                apply_booklets=bool(form.cleaned_data["apply_booklets"]),
                watermark=bool(form.cleaned_data["watermark"]),
                watermark_filename=bool(form.cleaned_data["watermark_filename"]),
                booklet_layout=form.cleaned_data["booklet_layout"],
                max_pages_per_split=form.cleaned_data["max_pages_per_split"],
                margin_cm=form.cleaned_data["margin_cm"],
                preserve_file_parity=bool(form.cleaned_data["preserve_file_parity"]),
                same_page_parity=bool(form.cleaned_data["same_page_parity"]),
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
                final_sections = renumber_sections(sections)
                outputs = split_pdf_by_sections(state["pdf_path"], final_sections, outputs_dir, options)
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
                    "source_ranges": ", ".join(
                        f"{part.start_page}–{part.end_page}"
                        for part in section.included_parts
                    ),
                    "booklet_applied": output.booklet_applied,
                    "download_url": reverse("splitpdf:download", kwargs={"output_id": output.output_id}),
                }
                for output, section in zip(outputs, final_sections)
            ]
            options_data = {key: value for key, value in form.cleaned_data.items() if key != "input_pdf"}
            activity = record_activity(
                owner=request.user, tool="splitpdf", title=f"Split {state.get('pdf_name', 'PDF')} into {len(outputs)} file(s)",
                options=options_data, inputs=[{"name": state.get("pdf_name"), "path": state["pdf_path"]}],
                outputs=[{"name": output.filename, "path": output.path} for output in outputs],
                restore_state={"session_key": SESSION_KEY, "session_value": state, "form_initial": options_data},
                generated_names=True,
            )
            for output_state, artifact in zip(state["outputs"], activity.artifacts.filter(kind="output")):
                output_state["filename"] = artifact.name
                output_state["download_url"] = reverse("activity:file", kwargs={"public_id": artifact.public_id})
                output_state["preview_url"] = reverse("activity:preview", kwargs={"public_id": artifact.public_id})
                output_state["artifact_id"] = artifact.pk
            activity.restore_state = json_safe({"session_key": SESSION_KEY, "session_value": state, "form_initial": options_data})
            activity.save(update_fields=["restore_state"])
            _save_state(request, state)
            messages.success(request, f"Generated {len(outputs)} PDF(s).")
            return render(request, "splitpdf/split_form.html", _context(request, form=form, sections=final_sections))

        messages.error(request, "Unknown action.")
        return redirect("splitpdf:form")

    return render(request, "splitpdf/split_form.html", _context(request))


def clear_split(request):
    _save_state(request, {})
    messages.success(request, "Split PDF state cleared.")
    return redirect("splitpdf:form")


def split_thumbnail(request, page_number: int):
    state = _state(request)
    path = state.get("pdf_path")
    total_pages = int(state.get("total_pages") or 0)
    if not path or not os.path.isfile(path) or page_number < 1 or page_number > total_pages:
        raise Http404("Preview page not found")
    data_uri = render_page_thumbnail_data_uri(path, page_number, target_width=360)
    try:
        import base64
        payload = base64.b64decode(data_uri.split(",", 1)[1])
    except (IndexError, ValueError):
        raise Http404("Preview could not be generated")
    response = HttpResponse(payload, content_type="image/png")
    response["Cache-Control"] = "private, max-age=3600"
    return response


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


def download_all_split(request):
    outputs = _state(request).get("outputs", [])
    if not isinstance(outputs, list):
        outputs = []

    files = _downloadable_outputs(outputs)
    if not files:
        raise Http404("No generated PDFs are available for download")

    archive = BytesIO()
    used_names: set[str] = set()
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as zip_file:
        for output in files:
            filename = _unique_archive_name(str(output.get("filename") or os.path.basename(output["path"])), used_names)
            zip_file.write(output["path"], arcname=filename)

    archive.seek(0)
    pdf_name = str(_state(request).get("pdf_name") or "split_pdfs")
    return FileResponse(
        archive,
        as_attachment=True,
        filename=f"{_download_archive_stem(pdf_name)}.zip",
        content_type="application/zip",
    )


def _sections_for_template(
    sections: list[SplitSection],
    toc_entries: list[TocEntry],
    total_pages: int,
    split_mode: str = "toc",
    input_pdf_path: str = "",
) -> list[dict[str, Any]]:
    def thumbnail(page_number: int) -> str:
        if not input_pdf_path:
            return ""
        return reverse("splitpdf:thumbnail", kwargs={"page_number": page_number})

    return [
        {
            "section_id": section.section_id,
            "index": section.index,
            "level": section.level,
            "title": section.title,
            "start_page": section.start_page,
            "end_page": section.end_page,
            "page_count": section.page_count,
            "filename": section.filename,
            "can_split": section_can_split(section),
            "can_merge": idx < len(sections) - 1,
            "part_count": len(section.included_parts),
            "included_parts": section.included_parts,
            "first_page_thumbnail": thumbnail(section.start_page),
            "last_page_thumbnail": thumbnail(section.end_page),
        }
        for idx, section in enumerate(sections)
    ]


def _split_preview_section(
    sections: list[SplitSection],
    section_id: str,
    split_after_page: int | None = None,
) -> list[SplitSection]:
    for idx, section in enumerate(sections):
        if section.section_id != section_id:
            continue
        child_sections = split_section_for_preview(section, split_after_page=split_after_page)
        if not child_sections:
            return sections
        return renumber_sections([*sections[:idx], *child_sections, *sections[idx + 1 :]])
    return sections


def _merge_preview_section(sections: list[SplitSection], section_id: str) -> list[SplitSection]:
    for idx, section in enumerate(sections):
        if section.section_id == section_id and idx < len(sections) - 1:
            merged = merge_adjacent_sections(section, sections[idx + 1])
            return renumber_sections([*sections[:idx], merged, *sections[idx + 2 :]])
    return sections


def _delete_preview_section(sections: list[SplitSection], section_id: str) -> list[SplitSection]:
    return renumber_sections([section for section in sections if section.section_id != section_id])


def _posted_section_ids(raw_ids: str) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in raw_ids.split(",") if value.strip()))


def _merge_selected_sections(sections: list[SplitSection], selected_ids: list[str]) -> list[SplitSection]:
    selected = set(selected_ids)
    chosen = [section for section in sections if section.section_id in selected]
    if len(chosen) < 2:
        raise ValueError("Select at least two sections to merge.")
    if len(chosen) != len(selected):
        raise ValueError("One of the selected sections is no longer available. Refresh and try again.")
    merged = merge_sections(chosen)
    first_position = min(index for index, section in enumerate(sections) if section.section_id in selected)
    remaining = [section for section in sections if section.section_id not in selected]
    remaining.insert(first_position, merged)
    return renumber_sections(remaining)


def _reorder_preview_sections(sections: list[SplitSection], ordered_ids: list[str]) -> list[SplitSection]:
    current = {section.section_id: section for section in sections}
    if len(ordered_ids) != len(sections) or set(ordered_ids) != set(current):
        raise ValueError("The section list changed before it could be reordered. Refresh and try again.")
    return renumber_sections([current[section_id] for section_id in ordered_ids])


def _section_ids(sections: list[SplitSection]) -> list[str]:
    return [section.section_id for section in sections]


def _downloadable_outputs(outputs: list[Any]) -> list[dict[str, Any]]:
    files = []
    for output in outputs:
        if not isinstance(output, dict):
            continue
        path = output.get("path")
        if isinstance(path, str) and os.path.isfile(path):
            files.append(output)
    return files


def _unique_archive_name(filename: str, used_names: set[str]) -> str:
    base, ext = os.path.splitext(os.path.basename(filename) or "split.pdf")
    ext = ext or ".pdf"
    candidate = f"{base}{ext}"
    suffix = 1
    while candidate in used_names:
        candidate = f"{base}_{suffix}{ext}"
        suffix += 1
    used_names.add(candidate)
    return candidate


def _download_archive_stem(pdf_name: str) -> str:
    stem = os.path.splitext(os.path.basename(pdf_name))[0].strip()
    return stem or "split_pdfs"
