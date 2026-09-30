import logging
import os
import uuid
from contextlib import ExitStack

import fitz
from django.conf import settings
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import override

from activity.services import record_activity
from activity.timetable_uploads import session_key, stage_timetable_uploads, staged_upload_files
from activity.workspaces import prepare_workspace

from .forms import CalendarForm
from .services import extract_uploaded_timetables, filter_events_by_subject, make_pdf

logger = logging.getLogger(__name__)


@override("en")
def calendar_view(request):
    upload_session_key = session_key("calendarpdf")
    prepare_workspace(request, "calendarpdf", session_key=upload_session_key)
    initial = request.session.pop("activity_initial_calendarpdf", None) if request.method == "GET" else None
    form = CalendarForm(request.POST or None, request.FILES or None, initial=initial)
    staged_uploads = request.session.get(upload_session_key, [])
    found_subjects = None
    if request.method == "POST":
        form_valid = form.is_valid()
        try:
            staged_uploads = stage_timetable_uploads(
                request, "calendarpdf", form.cleaned_data.get("images", [])
            )
        except ValueError as exc:
            form.add_error("images", str(exc))
        if form_valid and not form.errors:
            if not staged_uploads:
                form.add_error("images", "Add at least one timetable to generate a calendar.")
            else:
                try:
                    with ExitStack() as stack:
                        files = staged_upload_files(staged_uploads, stack)
                        events = extract_uploaded_timetables(files)
                    events, found_subjects = filter_events_by_subject(
                        events,
                        form.cleaned_data["filter_subjects"],
                        form.cleaned_data["subject_filter"],
                    )
                    if not events:
                        raise ValueError("No subjects matched the active subject filter.")
                    pdf = make_pdf(events)
                except (ValueError, OSError, fitz.FileDataError) as exc:
                    form.add_error(None, str(exc))
                except Exception:
                    logger.exception("Unexpected class calendar generation failure")
                    form.add_error(None, "The calendar could not be generated. Check the files and try again.")
                else:
                    outputs_dir = os.path.join(settings.MEDIA_ROOT, "calendar_outputs")
                    os.makedirs(outputs_dir, exist_ok=True)
                    output_path = os.path.join(outputs_dir, f"{uuid.uuid4().hex}_class_calendar.pdf")
                    with open(output_path, "wb") as output_file:
                        output_file.write(pdf)
                    options = {
                        "filter_subjects": form.cleaned_data["filter_subjects"],
                        "subject_filter": form.cleaned_data["subject_filter"],
                    }
                    activity = record_activity(
                        owner=request.user, tool="calendarpdf", title=f"Calendar from {len(staged_uploads)} timetable(s)",
                        options=options,
                        inputs=staged_uploads, outputs=[{"name": "class_calendar.pdf", "path": output_path}],
                        restore_state={
                            "session_key": upload_session_key,
                            "session_value": staged_uploads,
                            "form_initial": options,
                        },
                        generated_names=True,
                    )
                    artifact = activity.artifacts.get(kind="output")
                    form = CalendarForm(initial=options)
                    return render(request, "calendarpdf/form.html", {
                        "form": form,
                        "staged_uploads": staged_uploads,
                        "upload_tool": "calendarpdf",
                        "result_download_url": reverse("activity:file", kwargs={"public_id": artifact.public_id}),
                        "result_preview_url": reverse("activity:preview", kwargs={"public_id": artifact.public_id}),
                        "result_artifact_id": artifact.pk,
                        "found_subjects": found_subjects,
                    })
    return render(request, "calendarpdf/form.html", {
        "form": form,
        "staged_uploads": staged_uploads,
        "upload_tool": "calendarpdf",
    })
