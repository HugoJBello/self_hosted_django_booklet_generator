import logging

import fitz
from django.http import HttpResponse
from django.shortcuts import render
from django.utils.translation import override

from .forms import CalendarForm
from .services import extract_events, make_pdf

logger = logging.getLogger(__name__)


@override("en")
def calendar_view(request):
    form = CalendarForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        files = form.cleaned_data["images"]
        if len(files) > 20:
            form.add_error("images", "Select no more than 20 files.")
        else:
            try:
                events = set()
                for uploaded in files:
                    if uploaded.size > 25 * 1024 * 1024:
                        raise ValueError(f"{uploaded.name}: exceeds 25 MB.")
                    try:
                        extracted = extract_events(uploaded.read(), uploaded.name)
                    except ValueError as exc:
                        raise ValueError(f"{uploaded.name}: {exc}") from exc
                    if not extracted:
                        raise ValueError(
                            f"{uploaded.name}: no dated classes could be read. "
                            "Check that the image is clear and contains dates, times and subjects."
                        )
                    events.update(extracted)
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
