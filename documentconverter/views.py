import logging
import shutil
from pathlib import Path

from django.shortcuts import render
from django.urls import reverse

from activity.services import record_activity

from .forms import ConvertDocumentsForm
from .services import ConversionError, convert_to_pdf

logger = logging.getLogger(__name__)


def converter_view(request):
    initial = request.session.pop("activity_initial_documentconverter", None) if request.method == "GET" else None
    form = ConvertDocumentsForm(request.POST or None, request.FILES or None, initial=initial)
    results = []
    if request.method == "POST" and form.is_valid():
        image_options = {key: form.cleaned_data[key] for key in ("paper", "orientation", "fit", "margin_mm")}
        converted = []
        try:
            for uploaded in form.cleaned_data["documents"]:
                converted.append(convert_to_pdf(uploaded, image_options))
        except (ConversionError, ValueError, OSError) as exc:
            for item in converted:
                shutil.rmtree(Path(item["output_path"]).parent, ignore_errors=True)
            form.add_error("documents", str(exc))
        except Exception:
            logger.exception("Unexpected document conversion failure")
            for item in converted:
                shutil.rmtree(Path(item["output_path"]).parent, ignore_errors=True)
            form.add_error(None, "The documents could not be converted. Check the files and try again.")
        else:
            options = {**image_options, "files": [{"name": item["source_name"], "kind": item["kind"]} for item in converted]}
            activity = record_activity(
                owner=request.user, tool="documentconverter",
                title=converted[0]["source_name"] if len(converted) == 1 else f"{len(converted)} converted documents",
                options=options,
                inputs=[{"name": item["source_name"], "path": item["source_path"], "content_type": item["content_type"]} for item in converted],
                outputs=[{"name": item["output_name"], "path": item["output_path"], "content_type": "application/pdf"} for item in converted],
                restore_state={"form_initial": image_options},
            )
            for artifact in activity.artifacts.filter(kind="output"):
                results.append({
                    "name": artifact.name, "artifact_id": artifact.pk,
                    "preview_url": reverse("activity:preview", args=[artifact.public_id]),
                    "download_url": reverse("activity:file", args=[artifact.public_id]),
                })
            form = ConvertDocumentsForm(initial=image_options)
    return render(request, "documentconverter/form.html", {"form": form, "results": results})
