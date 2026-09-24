import os
from django.http import FileResponse, Http404, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .models import Activity, Artifact

TOOL_URLS = {"booklets": "booklets:form", "joinpdf": "joinpdf:form", "splitpdf": "splitpdf:form", "ocrpdf": "ocrpdf:form", "diary": "diary:form", "calendarpdf": "calendarpdf:form"}


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
    request.session["reopened_activity_id"] = activity.pk
    return redirect(TOOL_URLS[activity.tool])


def artifact_download(request, public_id):
    queryset = Artifact.objects.select_related("activity")
    if not request.user.is_staff:
        queryset = queryset.filter(activity__owner=request.user)
    artifact = get_object_or_404(queryset, public_id=public_id)
    if not os.path.isfile(artifact.path):
        raise Http404("File is no longer available")
    return FileResponse(open(artifact.path, "rb"), as_attachment=True, filename=artifact.name, content_type=artifact.content_type)
