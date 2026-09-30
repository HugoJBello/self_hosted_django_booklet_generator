import io
import os
from pathlib import Path

import fitz
from PIL import Image, ImageOps
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_sameorigin

from .models import Activity, Artifact
from .selectors import accessible_activity
from .timetable_uploads import staged_upload_for_request
from .workspace_files import workspace_pdf

TOOL_URLS = {"booklets": "booklets:form", "joinpdf": "joinpdf:form", "splitpdf": "splitpdf:form", "ocrpdf": "ocrpdf:form", "diary": "diary:form", "calendarpdf": "calendarpdf:form"}


def accessible_artifact(request, public_id):
    queryset = Artifact.objects.select_related("activity")
    if not request.user.is_staff:
        queryset = queryset.filter(activity__owner=request.user)
    artifact = get_object_or_404(queryset, public_id=public_id, content_type="application/pdf")
    if not os.path.isfile(artifact.path):
        raise Http404("File is no longer available")
    return artifact


def activity_list(request):
    activities = Activity.objects.select_related("owner").annotate(
        output_pdf_count=Count("artifacts", filter=Q(artifacts__kind="output", artifacts__content_type="application/pdf")),
        artifact_count=Count("artifacts", distinct=True),
    )
    if not request.user.is_staff:
        activities = activities.filter(owner=request.user)
    activities = activities.order_by("-created_at", "-pk")
    page = Paginator(activities, 20).get_page(request.GET.get("page"))
    return render(request, "activity/list.html", {"activities": page})


def activity_thumbnail(request, activity_id):
    activity = accessible_activity(request.user, activity_id)
    artifact = activity.artifacts.filter(
        kind="output", content_type="application/pdf"
    ).order_by("pk").first()
    if not artifact or not os.path.isfile(artifact.path):
        raise Http404("PDF is no longer available")
    try:
        with fitz.open(artifact.path) as document:
            if not document.page_count:
                raise Http404("PDF has no pages")
            page = document.load_page(0)
            scale = min(0.7, 220 / max(page.rect.width, 1), 150 / max(page.rect.height, 1))
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            image = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")
        image.thumbnail((220, 150), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=78, optimize=True)
    except (fitz.FileDataError, OSError, Image.DecompressionBombError) as exc:
        raise Http404("PDF thumbnail cannot be generated") from exc
    response = HttpResponse(output.getvalue(), content_type="image/jpeg")
    response["Cache-Control"] = "private, max-age=3600"
    return response


def activity_detail(request, activity_id):
    activity = accessible_activity(request.user, activity_id)
    activity.output_pdf_count = activity.artifacts.filter(kind="output", content_type="application/pdf").count()
    return render(request, "activity/detail.html", {"activity": activity})


def activity_outputs(request, activity_id):
    activity = accessible_activity(request.user, activity_id)
    outputs = activity.artifacts.filter(kind="output", content_type="application/pdf")
    return JsonResponse({"activity": activity.title, "tool": activity.get_tool_display(), "files": [
        {"name": artifact.name, "size": artifact.size,
         "preview_url": reverse("activity:preview", args=[artifact.public_id]),
         "download_url": reverse("activity:file", args=[artifact.public_id]),
         "print_url": f'{reverse("printmanager:print")}?artifact={artifact.pk}'}
        for artifact in outputs
    ]})


def activity_reopen(request, activity_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    activity = accessible_activity(request.user, activity_id)
    state = activity.restore_state
    if state.get("session_key"):
        request.session[state["session_key"]] = state.get("session_value", {})
    request.session[f"activity_initial_{activity.tool}"] = state.get("form_initial", activity.options)
    request.session["activity_reopen_tool"] = activity.tool
    request.session["activity_reopen_id"] = activity.pk
    return redirect(TOOL_URLS[activity.tool])


def artifact_download(request, public_id):
    queryset = Artifact.objects.select_related("activity")
    if not request.user.is_staff:
        queryset = queryset.filter(activity__owner=request.user)
    artifact = get_object_or_404(queryset, public_id=public_id)
    if not os.path.isfile(artifact.path):
        raise Http404("File is no longer available")
    return FileResponse(open(artifact.path, "rb"), as_attachment=True, filename=artifact.name, content_type=artifact.content_type)


@xframe_options_sameorigin
def artifact_preview(request, public_id):
    artifact = accessible_artifact(request, public_id)
    return FileResponse(open(artifact.path, "rb"), as_attachment=False, filename=artifact.name, content_type="application/pdf")


def artifact_preview_info(request, public_id):
    artifact = accessible_artifact(request, public_id)
    try:
        with fitz.open(artifact.path) as document:
            return JsonResponse({"name": artifact.name, "pages": document.page_count})
    except (fitz.FileDataError, OSError) as exc:
        raise Http404("PDF cannot be opened") from exc


def artifact_preview_page(request, public_id, page_number):
    artifact = accessible_artifact(request, public_id)
    return _pdf_page_response(artifact.path, page_number)


def _pdf_page_response(path, page_number):
    try:
        with fitz.open(path) as document:
            if page_number < 1 or page_number > document.page_count:
                raise Http404("PDF page does not exist")
            page = document.load_page(page_number - 1)
            scale = min(2.0, 1800 / max(page.rect.width, 1))
            image = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            response = HttpResponse(image.tobytes("jpeg", jpg_quality=82), content_type="image/jpeg")
            response["Cache-Control"] = "private, max-age=3600"
            return response
    except (fitz.FileDataError, OSError) as exc:
        raise Http404("PDF cannot be opened") from exc


def timetable_upload_thumbnail(request, tool, upload_id):
    item = staged_upload_for_request(request, tool, upload_id)
    path = item["path"]
    try:
        if Path(item["name"]).suffix.lower() == ".pdf":
            with fitz.open(path) as document:
                if not document.page_count:
                    raise Http404("Timetable PDF has no pages.")
                page = document.load_page(0)
                pixmap = page.get_pixmap(matrix=fitz.Matrix(0.28, 0.28), alpha=False)
                image = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")
        else:
            with Image.open(path) as source:
                if source.width * source.height > 50_000_000:
                    raise Http404("Timetable image is too large for a thumbnail.")
                image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((180, 120), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=78, optimize=True)
    except (OSError, fitz.FileDataError, Image.DecompressionBombError) as exc:
        raise Http404("Timetable thumbnail cannot be generated.") from exc
    response = HttpResponse(output.getvalue(), content_type="image/jpeg")
    response["Cache-Control"] = "private, max-age=300"
    return response


def timetable_upload_image_preview(request, tool, upload_id):
    item = staged_upload_for_request(request, tool, upload_id)
    if item.get("is_pdf") or Path(item["name"]).suffix.lower() == ".pdf":
        raise Http404("This timetable is not an image.")
    try:
        with Image.open(item["path"]) as source:
            if source.width * source.height > 50_000_000:
                raise Http404("Timetable image is too large to preview.")
            image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail((1800, 1500), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=86, optimize=True)
    except (OSError, Image.DecompressionBombError) as exc:
        raise Http404("Timetable image preview cannot be generated.") from exc
    response = HttpResponse(output.getvalue(), content_type="image/jpeg")
    response["Cache-Control"] = "private, max-age=300"
    return response


def timetable_upload_file(request, tool, upload_id):
    item = staged_upload_for_request(request, tool, upload_id)
    response = FileResponse(
        open(item["path"], "rb"),
        as_attachment=request.GET.get("download") == "1",
        filename=item["name"],
        content_type=item.get("content_type", "application/octet-stream"),
    )
    response["Cache-Control"] = "private, no-store"
    return response


def workspace_preview(request, tool, file_id):
    source = workspace_pdf(request, tool, file_id)
    if not source:
        raise Http404("PDF is no longer available")
    return FileResponse(open(source["path"], "rb"), as_attachment=False, filename=source["name"], content_type="application/pdf")


def workspace_preview_info(request, tool, file_id):
    source = workspace_pdf(request, tool, file_id)
    if not source:
        raise Http404("PDF is no longer available")
    try:
        with fitz.open(source["path"]) as document:
            return JsonResponse({"name": source["name"], "pages": document.page_count})
    except (fitz.FileDataError, OSError) as exc:
        raise Http404("PDF cannot be opened") from exc


def workspace_preview_page(request, tool, file_id, page_number):
    source = workspace_pdf(request, tool, file_id)
    if not source:
        raise Http404("PDF is no longer available")
    return _pdf_page_response(source["path"], page_number)
