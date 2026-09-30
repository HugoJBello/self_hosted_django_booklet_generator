"""Session-scoped timetable uploads shared by Calendar and Diary."""
from __future__ import annotations

import os
import uuid
import mimetypes
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.http import Http404


TIMETABLE_TOOLS = {"calendarpdf", "diary"}
MAX_FILES = 20
MAX_FILE_SIZE = 25 * 1024 * 1024
ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".pdf"}


def session_key(tool: str) -> str:
    if tool not in TIMETABLE_TOOLS:
        raise ValueError("Unsupported timetable upload tool.")
    return f"timetable_uploads_{tool}"


def prepare_staged_uploads(items) -> list[dict]:
    prepared = [item for item in items if isinstance(item, dict)]
    for item in prepared:
        item.setdefault("is_pdf", Path(item.get("name", "")).suffix.lower() == ".pdf")
    return prepared


def stage_timetable_uploads(request, tool: str, new_files=()) -> list[dict]:
    """Keep only posted existing IDs, then append newly uploaded files."""
    key = session_key(tool)
    requested_ids = set(request.POST.getlist("timetable_upload_ids"))
    previous = request.session.get(key, [])
    selected = [
        item for item in previous
        if isinstance(item, dict) and item.get("id") in requested_ids and _safe_upload_path(tool, item.get("path"))
    ]
    for uploaded in new_files or ():
        if Path(uploaded.name).suffix.lower() not in ALLOWED_SUFFIXES:
            raise ValueError(f"{uploaded.name}: unsupported file type.")
        if uploaded.size > MAX_FILE_SIZE:
            raise ValueError(f"{uploaded.name}: exceeds 25 MB.")
    if len(selected) + len(new_files or ()) > MAX_FILES:
        raise ValueError(f"Select no more than {MAX_FILES} timetable files.")

    if new_files:
        directory = os.path.join(settings.MEDIA_ROOT, "activity_inputs", tool, uuid.uuid4().hex)
        os.makedirs(directory, exist_ok=True)
        for index, uploaded in enumerate(new_files, start=1):
            name = os.path.basename(uploaded.name) or f"upload-{index}"
            path = os.path.join(directory, f"{index:02d}_{name}")
            with open(path, "wb") as destination:
                for chunk in uploaded.chunks():
                    destination.write(chunk)
            if hasattr(uploaded, "seek"):
                uploaded.seek(0)
            selected.append({
                "id": uuid.uuid4().hex,
                "name": name,
                "path": path,
                "content_type": mimetypes.guess_type(name)[0] or getattr(uploaded, "content_type", "application/octet-stream"),
                "is_pdf": Path(name).suffix.lower() == ".pdf",
                "size": os.path.getsize(path),
            })
    request.session[key] = selected
    return prepare_staged_uploads(selected)


def staged_upload_files(items: list[dict], stack):
    """Open safe staged paths as Django files while preserving original names."""
    opened = []
    for item in items:
        path = item.get("path")
        if not path or not os.path.isfile(path):
            raise ValueError(f"{item.get('name', 'A timetable file')}: file is no longer available. Upload it again.")
        source = stack.enter_context(open(path, "rb"))
        opened.append(File(source, name=item.get("name") or os.path.basename(path)))
    return opened


def staged_upload_for_request(request, tool: str, upload_id: str) -> dict:
    try:
        key = session_key(tool)
    except ValueError as exc:
        raise Http404("Timetable upload is no longer available.") from exc
    for item in request.session.get(key, []):
        if isinstance(item, dict) and item.get("id") == upload_id and _safe_upload_path(tool, item.get("path")):
            if os.path.isfile(item["path"]):
                return item
    raise Http404("Timetable upload is no longer available.")


def _safe_upload_path(tool: str, path) -> bool:
    if tool not in TIMETABLE_TOOLS or not isinstance(path, str):
        return False
    root = os.path.realpath(os.path.join(settings.MEDIA_ROOT, "activity_inputs", tool))
    candidate = os.path.realpath(path)
    try:
        return os.path.commonpath((root, candidate)) == root
    except ValueError:
        return False
