import os
import fitz
from django.http import FileResponse, Http404, HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_sameorigin

from .models import Activity, Artifact

TOOL_URLS = {"booklets": "booklets:form", "joinpdf": "joinpdf:form", "splitpdf": "splitpdf:form", "ocrpdf": "ocrpdf:form", "diary": "diary:form", "calendarpdf": "calendarpdf:form"}


def accessible_artifact(request, public_id):
    queryset = Artifact.objects.select_related("activity")
    if not request.user.is_staff:
        queryset = queryset.filter(activity__owner=request.user)
    artifact = get_object_or_404(queryset, public_id=public_id, content_type="application/pdf")
    if not os.path.isfile(artifact.path):
        raise Http404("File is no longer available")
    return artifact


def accessible_activity(request, activity_id):
    queryset = Activity.objects.select_related("owner").prefetch_related("artifacts")
    if not request.user.is_staff:
        queryset = queryset.filter(owner=request.user)
    return get_object_or_404(queryset, pk=activity_id)


def activity_list(request):
    activities = Activity.objects.select_related("owner").prefetch_related("artifacts")
    if not request.user.is_staff:
        activities = activities.filter(owner=request.user)
    return render(request, "activity/list.html", {"activities": activities[:250]})


def activity_detail(request, activity_id):
    return render(request, "activity/detail.html", {"activity": accessible_activity(request, activity_id)})


def activity_reopen(request, activity_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    activity = accessible_activity(request, activity_id)
    state = activity.restore_state
    if state.get("session_key"):
        request.session[state["session_key"]] = state.get("session_value", {})
    request.session[f"activity_initial_{activity.tool}"] = state.get("form_initial", activity.options)
    request.session["activity_reopen_tool"] = activity.tool
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
    try:
        with fitz.open(artifact.path) as document:
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
