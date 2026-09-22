from django.http import HttpResponse
from django.shortcuts import render
from django.utils.translation import override

from .forms import CalendarForm
from .services import extract_events, make_pdf


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
                    events.update(extract_events(uploaded.read(), uploaded.name))
                pdf = make_pdf(events)
            except (ValueError, OSError) as exc:
                form.add_error(None, str(exc))
            else:
                response = HttpResponse(pdf, content_type="application/pdf")
                response["Content-Disposition"] = 'attachment; filename="class_calendar.pdf"'
                return response
    return render(request, "calendarpdf/form.html", {"form": form})
