import datetime as dt
import decimal
import os
import uuid
from pathlib import Path

from django.conf import settings

from .models import Activity, Artifact


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


def record_activity(*, owner, tool, title, options, inputs=(), outputs=(), restore_state=None, status="done"):
    activity = Activity.objects.create(owner=owner, tool=tool, title=title, options=json_safe(options), restore_state=json_safe(restore_state or {}), status=status)
    artifacts = []
    for kind, entries in (("input", inputs), ("output", outputs)):
        for entry in entries:
            path = str(entry["path"])
            artifacts.append(Artifact(activity=activity, kind=kind, name=str(entry.get("name") or os.path.basename(path)), path=path, content_type=str(entry.get("content_type") or "application/pdf"), size=os.path.getsize(path) if os.path.isfile(path) else 0))
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
