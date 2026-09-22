import logging

import fitz
from django.http import HttpResponse
from django.shortcuts import render
from django.utils.translation import override

from .forms import CalendarForm
from .services import extract_uploaded_timetables, make_pdf

logger = logging.getLogger(__name__)


@override("en")
def calendar_view(request):
    form = CalendarForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            events = extract_uploaded_timetables(form.cleaned_data["images"])
            pdf = make_pdf(events)
        except (ValueError, OSError, fitz.FileDataError) as exc:
            form.add_error(None, str(exc))
        except Exception:
            logger.exception("Unexpected class calendar generation failure")
            form.add_error(None, "The calendar could not be generated. Check the files and try again.")
        else:
            response = HttpResponse(pdf, content_type="application/pdf")
            response["Content-Disposition"] = 'attachment; filename="class_calendar.pdf"'
            return response
    return render(request, "calendarpdf/form.html", {"form": form})
