import datetime as dt
import decimal
import os
import uuid
from pathlib import Path

from django.conf import settings

from .models import Activity, Artifact
from .filenames import generated_pdf_name


def json_safe(value):
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def record_activity(*, owner, tool, title, options, inputs=(), outputs=(), restore_state=None, status="done", generated_names=False):
    inputs, outputs = list(inputs), list(outputs)
    activity = Activity.objects.create(owner=owner, tool=tool, title=title, options=json_safe(options), restore_state=json_safe(restore_state or {}), status=status)
    artifacts = []
    source_names = [entry.get("name") for entry in inputs]
    used_names = set()
    for kind, entries in (("input", inputs), ("output", outputs)):
        for index, entry in enumerate(entries, start=1):
            path = str(entry["path"])
            name = str(entry.get("name") or os.path.basename(path))
            if generated_names and kind == "output" and str(entry.get("content_type") or "application/pdf") == "application/pdf":
                name = generated_pdf_name(source_names=source_names, tool=tool, detail=name, index=index if len(entries) > 1 else None, used_names=used_names)
            artifacts.append(Artifact(activity=activity, kind=kind, name=name, path=path, content_type=str(entry.get("content_type") or "application/pdf"), size=os.path.getsize(path) if os.path.isfile(path) else 0))
    Artifact.objects.bulk_create(artifacts)
    return activity


def persist_uploads(files, tool):
    directory = os.path.join(settings.MEDIA_ROOT, "activity_inputs", tool, uuid.uuid4().hex)
    os.makedirs(directory, exist_ok=True)
    saved = []
    for index, uploaded in enumerate(files):
        safe_name = os.path.basename(uploaded.name) or f"upload-{index + 1}"
        path = os.path.join(directory, f"{index + 1:02d}_{safe_name}")
        with open(path, "wb") as destination:
            for chunk in uploaded.chunks():
                destination.write(chunk)
        if hasattr(uploaded, "seek"):
            uploaded.seek(0)
        saved.append({"name": uploaded.name, "path": path, "content_type": getattr(uploaded, "content_type", "application/octet-stream")})
    return saved
