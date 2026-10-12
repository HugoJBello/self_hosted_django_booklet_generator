import json
import os
import uuid

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.files.uploadedfile import UploadedFile
from django.http import JsonResponse
from django.views.decorators.http import require_POST

SESSION_KEY = "chunked_pdf_uploads"
CHUNK_MAX_BYTES = 20 * 1024 * 1024
FILE_MAX_BYTES = 10 * 1024 * 1024 * 1024


def _uploads(request):
    value = request.session.get(SESSION_KEY, {})
    return value if isinstance(value, dict) else {}


def _save_uploads(request, uploads):
    request.session[SESSION_KEY] = uploads
    request.session.modified = True


def _valid_pdf(path):
    try:
        with open(path, "rb") as source:
            return b"%PDF-" in source.read(1024)
    except OSError:
        return False


@login_required
@require_POST
def chunk_upload(request):
    action = request.POST.get("action", "")
    uploads = _uploads(request)
    if action == "start":
        filename = os.path.basename(request.POST.get("filename", ""))
        field = request.POST.get("field", "")
        try:
            size = int(request.POST.get("size", ""))
        except (TypeError, ValueError):
            return JsonResponse({"error": "Invalid file size."}, status=400)
        if not filename.lower().endswith(".pdf") or not field:
            return JsonResponse({"error": "Only named PDF form fields are accepted."}, status=400)
        if size < 1 or size > FILE_MAX_BYTES:
            return JsonResponse({"error": "The PDF must be between 1 byte and 10 GB."}, status=400)
        token = uuid.uuid4().hex
        directory = os.path.join(settings.MEDIA_ROOT, "chunked_uploads", f"user_{request.user.pk}", token)
        os.makedirs(directory, exist_ok=False)
        path = os.path.join(directory, filename)
        uploads[token] = {"name": filename, "field": field, "path": path, "size": size, "received": 0, "complete": False}
        _save_uploads(request, uploads)
        return JsonResponse({"token": token, "offset": 0})

    token = request.POST.get("token", "")
    upload = uploads.get(token)
    if action != "chunk" or not upload:
        return JsonResponse({"error": "The upload session does not exist or has expired."}, status=404)
    if upload.get("complete"):
        return JsonResponse({"token": token, "offset": upload["received"], "complete": True})
    try:
        offset = int(request.POST.get("offset", ""))
    except (TypeError, ValueError):
        return JsonResponse({"error": "Invalid chunk offset."}, status=400)
    chunk = request.FILES.get("chunk")
    if chunk is None or chunk.size < 1 or chunk.size > CHUNK_MAX_BYTES:
        return JsonResponse({"error": "Each upload block must be between 1 byte and 20 MB."}, status=400)
    if offset != upload["received"] or offset + chunk.size > upload["size"]:
        return JsonResponse({"error": "The upload block is out of sequence."}, status=409)
    partial_path = f'{upload["path"]}.part'
    with open(partial_path, "ab") as destination:
        for piece in chunk.chunks():
            destination.write(piece)
    upload["received"] += chunk.size
    if upload["received"] == upload["size"]:
        if not _valid_pdf(partial_path):
            os.unlink(partial_path)
            uploads.pop(token, None)
            _save_uploads(request, uploads)
            return JsonResponse({"error": "The uploaded file is not a valid PDF."}, status=400)
        os.replace(partial_path, upload["path"])
        upload["complete"] = True
    uploads[token] = upload
    _save_uploads(request, uploads)
    return JsonResponse({"token": token, "offset": upload["received"], "complete": upload["complete"]})


class ChunkedPdfUploadMiddleware:
    """Turn completed browser chunk uploads back into ordinary request.FILES."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        opened = []
        paths = []
        tokens = []
        if request.method == "POST" and request.user.is_authenticated:
            raw = request.POST.get("chunk_upload_tokens", "")
            if raw:
                try:
                    submitted = json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    submitted = []
                uploads = _uploads(request)
                for item in submitted if isinstance(submitted, list) else []:
                    token = item.get("token", "") if isinstance(item, dict) else ""
                    field = item.get("field", "") if isinstance(item, dict) else ""
                    upload = uploads.get(token)
                    path = upload.get("path", "") if upload else ""
                    if not upload or not upload.get("complete") or field != upload.get("field") or not os.path.isfile(path):
                        continue
                    if os.path.getsize(path) != upload.get("size"):
                        continue
                    source = open(path, "rb")
                    opened.append(source)
                    paths.append(path)
                    tokens.append(token)
                    request.FILES.appendlist(field, UploadedFile(source, upload["name"], "application/pdf", upload["size"]))
                for token in tokens:
                    uploads.pop(token, None)
                if tokens:
                    _save_uploads(request, uploads)
        try:
            return self.get_response(request)
        finally:
            for source in opened:
                source.close()
            for path in paths:
                try:
                    os.unlink(path)
                    os.rmdir(os.path.dirname(path))
                except OSError:
                    pass
