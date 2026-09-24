import logging
import os
import uuid

import fitz
from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import render
from django.utils.translation import override

from activity.services import persist_uploads, record_activity
from activity.workspaces import prepare_workspace

from .forms import CalendarForm
from .services import extract_uploaded_timetables, make_pdf

logger = logging.getLogger(__name__)


@override("en")
def calendar_view(request):
    prepare_workspace(request, "calendarpdf")
    form = CalendarForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        saved_inputs = persist_uploads(form.cleaned_data["images"], "calendarpdf")
        try:
            events = extract_uploaded_timetables(form.cleaned_data["images"])
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
            record_activity(
                owner=request.user, tool="calendarpdf", title=f"Calendar from {len(saved_inputs)} timetable(s)", options={},
                inputs=saved_inputs, outputs=[{"name": "class_calendar.pdf", "path": output_path}], restore_state={"form_initial": {}},
            )
            response = HttpResponse(pdf, content_type="application/pdf")
            response["Content-Disposition"] = 'attachment; filename="class_calendar.pdf"'
            return response
    return render(request, "calendarpdf/form.html", {"form": form})
