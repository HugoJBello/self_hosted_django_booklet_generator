import logging
import os
import uuid

import fitz
from django.conf import settings
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import override

from activity.services import persist_uploads, record_activity
from activity.workspaces import prepare_workspace

from .forms import CalendarForm
from .services import extract_uploaded_timetables, filter_events_by_subject, make_pdf

logger = logging.getLogger(__name__)


@override("en")
def calendar_view(request):
    prepare_workspace(request, "calendarpdf")
    form = CalendarForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        saved_inputs = persist_uploads(form.cleaned_data["images"], "calendarpdf")
        try:
            events = extract_uploaded_timetables(form.cleaned_data["images"])
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
            activity = record_activity(
                owner=request.user, tool="calendarpdf", title=f"Calendar from {len(saved_inputs)} timetable(s)",
                options={
                    "filter_subjects": form.cleaned_data["filter_subjects"],
                    "subject_filter": form.cleaned_data["subject_filter"],
                },
                inputs=saved_inputs, outputs=[{"name": "class_calendar.pdf", "path": output_path}],
                restore_state={"form_initial": {
                    "filter_subjects": form.cleaned_data["filter_subjects"],
                    "subject_filter": form.cleaned_data["subject_filter"],
                }},
                generated_names=True,
            )
            artifact = activity.artifacts.get(kind="output")
            return render(request, "calendarpdf/form.html", {
                "form": CalendarForm(),
                "result_download_url": reverse("activity:file", kwargs={"public_id": artifact.public_id}),
                "result_preview_url": reverse("activity:preview", kwargs={"public_id": artifact.public_id}),
                "result_artifact_id": artifact.pk,
                "found_subjects": found_subjects,
            })
    return render(request, "calendarpdf/form.html", {"form": form})
