from __future__ import annotations

import os

from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import render
from django.urls import reverse

from .forms import DiaryForm
from .services import build_diary_pipeline


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _initial_form(form: DiaryForm) -> DiaryForm:
    return DiaryForm(initial=form.cleaned_data)


def diary_view(request):
    result_download_url = None

    if request.method == "POST":
        form = DiaryForm(request.POST)
        if form.is_valid():
            outputs_dir = os.path.join(settings.MEDIA_ROOT, "diary_outputs")
            _ensure_dir(outputs_dir)

            try:
                result = build_diary_pipeline(
                    start_date=form.cleaned_data["start_date"],
                    number_of_weeks=form.cleaned_data["number_of_weeks"],
                    calendar_mode=form.cleaned_data["calendar_mode"],
                    include_progress_graph=bool(form.cleaned_data["include_progress_graph"]),
                    include_constellation_map=bool(form.cleaned_data["include_constellation_map"]),
                    output_mode=form.cleaned_data["output_mode"],
                    final_output_dir=outputs_dir,
                    max_pages_per_split=form.cleaned_data["max_pages_per_split"],
                    content_margin_cm=form.cleaned_data["content_margin_cm"],
                    side_by_side_prepare_for_portrait_printing=bool(
                        form.cleaned_data["side_by_side_prepare_for_portrait_printing"]
                    ),
                    flipped_a4_prepare_for_a5_printing=bool(form.cleaned_data["flipped_a4_prepare_for_a5_printing"]),
                    flipped_a4_center_gap_cm=form.cleaned_data["flipped_a4_center_gap_cm"],
                    flipped_a4_split_mode=form.cleaned_data["flipped_a4_split_mode"],
                    flipped_a4_quality=form.cleaned_data["flipped_a4_quality"],
                )
            except FileNotFoundError as exc:
                if exc.filename == "pdflatex":
                    messages.error(request, "pdflatex is not installed in this environment.")
                else:
                    messages.error(request, f"Missing file while generating diary: {exc}")
            except Exception as exc:
                messages.error(request, f"Error generating diary: {exc}")
            else:
                messages.success(request, "Diary generated successfully.")
                result_download_url = reverse("diary:download", kwargs={"job_id": result.job_id})
                form = _initial_form(form)
    else:
        form = DiaryForm()

    return render(
        request,
        "diary/diary_form.html",
        {
            "form": form,
            "result_download_url": result_download_url,
        },
    )


def download_diary(request, job_id: str):
    outputs_dir = os.path.join(settings.MEDIA_ROOT, "diary_outputs")
    candidates = [
        f"{job_id}_diary.pdf",
        f"{job_id}_booklets_for_printing.pdf",
        f"{job_id}_flipped_a4_booklets_for_printing.pdf",
    ]

    for filename in candidates:
        pdf_path = os.path.join(outputs_dir, filename)
        if os.path.isfile(pdf_path):
            return FileResponse(
                open(pdf_path, "rb"),
                as_attachment=True,
                filename=os.path.basename(pdf_path),
                content_type="application/pdf",
            )

    raise Http404("File not found")
