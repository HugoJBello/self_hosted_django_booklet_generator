import json
import os
import shutil
import uuid
from functools import wraps

import django_rq
import fitz
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.core.paginator import Paginator
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache

from activity.models import Artifact
from activity.services import record_activity
from activity.watermark import stamp_pdf

from .forms import PrinterForm, PrintForm
from .image_documents import convert_uploaded_image, is_supported_image
from .models import Printer, PrintJob
from .services import CupsError, _run, certify_printer, configure_printer, discover_printers, printer_availabilities, probe_printer
from .tasks import refresh_print_job, submit_print_job


staff_required = user_passes_test(lambda user: user.is_staff, login_url=settings.LOGIN_URL)


def staff_json_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"error": "Your session expired. Sign in again and retry."}, status=401)
        if not request.user.is_staff:
            return JsonResponse({"error": "Printer setup is available to administrators only."}, status=403)
        return view(request, *args, **kwargs)
    return wrapped


def _artifact_queryset(request):
    queryset = Artifact.objects.select_related("activity").filter(kind="output", content_type="application/pdf")
    if not request.user.is_staff:
        queryset = queryset.filter(activity__owner=request.user)
    return queryset.order_by("-activity__created_at", "name")


def _save_upload(uploaded):
    directory = os.path.join(settings.MEDIA_ROOT, "print_uploads", uuid.uuid4().hex)
    os.makedirs(directory, exist_ok=True)
    filename = os.path.basename(uploaded.name) or "document.pdf"
    path = os.path.join(directory, filename)
    with open(path, "wb") as destination:
        for chunk in uploaded.chunks():
            destination.write(chunk)
    return path, filename


def _json_list(value):
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _ordered_uploads(uploads, manifest):
    remaining = list(uploads)
    ordered = []
    for entry in manifest:
        if not isinstance(entry, dict):
            continue
        for index, uploaded in enumerate(remaining):
            if uploaded.name == entry.get("name") and uploaded.size == entry.get("size"):
                ordered.append(remaining.pop(index))
                break
    return ordered + remaining


def _documents_from_uploads(uploads, image_options, manifest):
    documents = []
    option_entries = list(image_options)
    for uploaded in _ordered_uploads(uploads, manifest):
        if is_supported_image(uploaded.name):
            raw_options = {}
            for index, entry in enumerate(option_entries):
                if isinstance(entry, dict) and entry.get("name") == uploaded.name and entry.get("size") == uploaded.size:
                    raw_options = entry.get("options", {})
                    option_entries.pop(index)
                    break
            path, name, normalized, source_path, source_name = convert_uploaded_image(uploaded, raw_options)
            documents.append({
                "path": path, "name": name, "image_options": normalized,
                "source_path": source_path, "source_name": source_name,
                "source_content_type": getattr(uploaded, "content_type", "application/octet-stream"),
            })
        else:
            path, name = _save_upload(uploaded)
            documents.append({"path": path, "name": name})
    return documents


def _enqueue_print_job(job):
    """Submit a saved PrintJob to RQ and leave an auditable failure if RQ is down."""
    try:
        django_rq.get_queue("default").enqueue(submit_print_job, job.pk)
        return True
    except Exception:
        job.status = "error"
        job.error_message = "The background print queue is unavailable. Try again shortly."
        job.status_detail = "The document was saved, but could not be queued for submission."
        job.completed_at = timezone.now()
        job.save(update_fields=("status", "error_message", "status_detail", "completed_at"))
        return False


def _automatic_pdf_orientation(path):
    """Return the IPP orientation enum when every PDF page agrees."""
    try:
        with fitz.open(path) as document:
            orientations = {"4" if page.rect.width > page.rect.height else "3" for page in document}
        return orientations.pop() if len(orientations) == 1 else ""
    except (fitz.FileDataError, OSError):
        return ""


@staff_required
def printer_list(request):
    printers = list(Printer.objects.prefetch_related("jobs").all())
    live = {item["printer"].pk: item for item in printer_availabilities(printers)}
    for printer in printers:
        printer.live_status = live[printer.pk]
        printer.job_count = printer.jobs.count()
        printer.active_job_count = printer.jobs.filter(status__in=("queued", "processing")).count()
    return render(request, "printmanager/printer_list.html", {"printers": printers})


@staff_json_required
def printer_discover(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    try:
        devices = discover_printers()
    except CupsError as exc:
        return JsonResponse({"devices": [], "error": str(exc)}, status=503)
    configured_uris = set(Printer.objects.values_list("device_uri", flat=True))
    for device in devices:
        device["configured"] = device["uri"] in configured_uris
        try:
            identity = probe_printer(device["uri"])
            device.update({
                "name": identity["name"] or device["name"],
                "queue_name": identity["queue_name"],
                "description": identity["description"] or identity["model"] or device["description"],
                "model": identity["model"],
                "location": identity["location"],
                "state": identity["state"],
                "options": identity["options"],
                "defaults": identity["defaults"],
                "document_formats": identity["document_formats"],
            })
        except CupsError as exc:
            device["probe_error"] = str(exc)
    return JsonResponse({"devices": devices})


@staff_json_required
def printer_probe(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        identity = probe_printer(request.POST.get("uri", ""), request.POST.get("driver", "everywhere"))
    except CupsError as exc:
        return JsonResponse({"options": {}, "error": str(exc)}, status=422)
    return JsonResponse(identity)


@staff_required
def printer_edit(request, pk=None):
    printer = get_object_or_404(Printer, pk=pk) if pk else Printer()
    if request.method == "POST":
        form = PrinterForm(request.POST, instance=printer)
        if form.is_valid():
            candidate = form.save(commit=False)
            try:
                configure_printer(candidate)
                identity = probe_printer(candidate.device_uri)
                candidate.supported_options = identity["options"]
                candidate.document_formats = identity["document_formats"]
                candidate.preferred_document_format = identity["preferred_document_format"]
                candidate.capability_validation = certify_printer(candidate)
                candidate.certified_at = timezone.now()
                candidate.last_synced_at = timezone.now()
                with transaction.atomic():
                    if candidate.is_default:
                        Printer.objects.exclude(pk=candidate.pk).update(is_default=False)
                    candidate.save()
                messages.success(request, f"Printer {candidate.name} was saved and applied to CUPS.")
                return redirect("printmanager:printers")
            except CupsError as exc:
                form.add_error(None, f"CUPS: {exc}")
    else:
        form = PrinterForm(instance=printer)
    return render(request, "printmanager/printer_form.html", {"form": form, "printer": printer})


@staff_required
def printer_sync(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    printer = get_object_or_404(Printer, pk=pk)
    try:
        identity = probe_printer(printer.device_uri)
        printer.supported_options = identity["options"]
        printer.document_formats = identity["document_formats"]
        printer.preferred_document_format = identity["preferred_document_format"]
        printer.capability_validation = certify_printer(printer)
        printer.certified_at = timezone.now()
        printer.last_synced_at = timezone.now()
        printer.save(update_fields=("supported_options", "document_formats", "preferred_document_format", "capability_validation", "certified_at", "last_synced_at", "updated_at"))
        messages.success(request, f"Options for {printer.name} synchronized.")
    except CupsError as exc:
        messages.error(request, f"CUPS: {exc}")
    return redirect("printmanager:printers")


@staff_required
def printer_delete(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    printer = get_object_or_404(Printer, pk=pk)
    if printer.jobs.exists():
        messages.error(request, "This printer has print history and cannot be deleted; disable it instead.")
        return redirect("printmanager:printers")
    try:
        _run(["lpadmin", "-x", printer.name])
        printer.delete()
        messages.success(request, "Printer removed from CUPS and this application.")
    except CupsError as exc:
        messages.error(request, f"Printer was not removed. CUPS: {exc}")
    return redirect("printmanager:printers")


@never_cache
def print_document(request):
    artifact_queryset = _artifact_queryset(request)
    restored_extra_options = {}
    requested_artifact = None
    requested_job = None
    requested_job_id = request.GET.get("job", "")
    if requested_job_id.isdigit():
        requested_job = get_object_or_404(_visible_jobs(request), pk=requested_job_id)
        if not os.path.isfile(requested_job.document_path):
            raise Http404("This print document is no longer available.")
    requested_artifact_id = request.GET.get("artifact", "")
    if not requested_job and requested_artifact_id.isdigit():
        requested_artifact = artifact_queryset.filter(pk=requested_artifact_id).first()
    visible_artifacts = artifact_queryset.filter(pk=requested_artifact.pk) if requested_artifact else artifact_queryset
    artifacts_page = Paginator(visible_artifacts, 12).get_page(request.GET.get("page"))
    artifacts = list(artifacts_page.object_list)
    enabled_printers = list(Printer.objects.filter(is_enabled=True).order_by("-is_default", "name"))
    printer_choices = printer_availabilities(enabled_printers)
    connected_printers = [item["printer"] for item in printer_choices if item["connected"]]
    default_printer = connected_printers[0] if connected_printers else (enabled_printers[0] if enabled_printers else None)
    if request.method == "POST":
        form = PrintForm(request.POST, request.FILES, artifacts=artifacts)
        form.fields["printer"].queryset = Printer.objects.filter(pk__in=[printer.pk for printer in enabled_printers])
        if form.is_valid():
            data = form.cleaned_data
            if data["source"] == "upload":
                try:
                    documents = _documents_from_uploads(
                        data["document"],
                        _json_list(data.get("image_options")),
                        _json_list(data.get("document_manifest")),
                    )
                except (OSError, ValueError) as exc:
                    form.add_error("document", f"An image could not be prepared for printing: {exc}")
                    documents = []
            elif data["source"] == "recent":
                selected = list(artifact_queryset.filter(pk__in=data["artifact"]))
                documents = [{"path": artifact.path, "name": artifact.name} for artifact in selected]
            else:
                source_job = get_object_or_404(_visible_jobs(request), pk=data["source_job"])
                if os.path.isfile(source_job.document_path):
                    documents = [{"path": source_job.document_path, "name": source_job.document_name}]
                else:
                    form.add_error("source_job", "This print document is no longer available.")
                    documents = []
            if documents and data["watermark"]:
                try:
                    stamped_dir = os.path.join(settings.MEDIA_ROOT, "print_watermark", uuid.uuid4().hex)
                    os.makedirs(stamped_dir, exist_ok=True)
                    for index, document in enumerate(documents):
                        if not document.get("source_path"):
                            copied_path = os.path.join(stamped_dir, f"{index + 1}.pdf")
                            shutil.copy2(document["path"], copied_path)
                            document["path"] = copied_path
                        stamp_pdf(document["path"], filename=document["name"] if data["watermark_filename"] else "")
                except (OSError, ValueError, fitz.FileDataError) as exc:
                    form.add_error(None, f"The PDF footer could not be added: {exc}")
                    documents = []
            options = dict(data["extra_options"])
            for key in ("media", "sides", "orientation_requested", "print_color_mode"):
                value = data.get(key)
                if value:
                    options[key.replace("_", "-")] = value
            # "Automatic" must let the PDF page define its own orientation.
            # Printer capability defaults may otherwise reintroduce portrait
            # through extra_options and contradict a generated landscape PDF.
            if not data.get("orientation_requested"):
                # Empty is an intentional override: submit_pdf merges printer
                # defaults later, so removing the key would restore portrait.
                options["orientation-requested"] = ""
            if data.get("scaling"):
                options["print-scaling"] = data["scaling"]
            if data.get("collate"):
                options["Collate"] = "True"
            generated_images = [document for document in documents if document.get("source_path")]
            if generated_images:
                activity = record_activity(
                    owner=request.user,
                    tool="printdocuments",
                    title=(generated_images[0]["name"] if len(generated_images) == 1 else f"{len(generated_images)} image documents"),
                    options={
                        "image_layouts": [
                            {"source": document["source_name"], **document["image_options"]}
                            for document in generated_images
                        ],
                        "print_settings": {"copies": data["copies"], "page_ranges": data["page_ranges"], **options},
                    },
                    inputs=[{
                        "path": document["source_path"], "name": document["source_name"],
                        "content_type": document["source_content_type"],
                    } for document in generated_images],
                    outputs=[{
                        "path": document["path"], "name": document["name"], "content_type": "application/pdf",
                    } for document in generated_images],
                )
                for document, artifact in zip(generated_images, activity.artifacts.filter(kind="output")):
                    document["artifact_id"] = artifact.pk
            jobs = []
            for document in documents:
                job_options = {"copies": data["copies"], "page_ranges": data["page_ranges"], **options}
                if not data.get("orientation_requested"):
                    job_options["orientation-requested"] = _automatic_pdf_orientation(document["path"])
                job = PrintJob.objects.create(
                    owner=request.user,
                    printer=data["printer"],
                    document_name=document["name"],
                    document_path=document["path"],
                    options=job_options,
                    status="queued",
                    status_detail="Waiting for background submission.",
                )
                _enqueue_print_job(job)
                jobs.append(job)
            accepted = sum(job.status != "error" for job in jobs)
            if accepted:
                messages.success(request, f"{accepted} document(s) accepted and queued.")
                return redirect(f'{reverse("printmanager:jobs")}?printer={data["printer"].pk}&highlight={jobs[-1].pk}')
            if documents:
                messages.error(request, "The printer did not accept the selected documents.")
    else:
        initial = {
            "source": "job" if requested_job else "recent" if requested_artifact or request.GET.get("page") else "upload",
            "source_job": requested_job.pk if requested_job else None,
            "artifact": [str(requested_artifact.pk)] if requested_artifact else [],
            "printer": requested_job.printer_id if requested_job and requested_job.printer.is_enabled else default_printer.pk if default_printer else None,
        }
        if requested_job:
            saved = dict(requested_job.options or {})
            initial.update({
                "copies": saved.pop("copies", 1),
                "page_ranges": saved.pop("page_ranges", ""),
                "media": saved.pop("media", ""),
                "sides": saved.pop("sides", ""),
                "orientation_requested": saved.pop("orientation-requested", ""),
                "print_color_mode": saved.pop("print-color-mode", ""),
                "scaling": saved.pop("print-scaling", ""),
                "collate": str(saved.pop("Collate", "")).lower() in {"true", "1", "yes"},
            })
            restored_extra_options = saved
            initial["extra_options"] = json.dumps(saved, indent=2)
        form = PrintForm(artifacts=artifacts, initial=initial)
    printer_capabilities = {
        str(printer.pk): {
            "options": {key: value for key, value in printer.supported_options.items() if key != "orientation-requested"},
            "defaults": {key: value for key, value in printer.default_options.items() if key != "orientation-requested"},
        }
        for printer in Printer.objects.filter(is_enabled=True)
    }
    return render(request, "printmanager/print_form.html", {
        "form": form,
        "artifacts": artifacts,
        "artifacts_page": artifacts_page,
        "printer_choices": printer_choices,
        "has_enabled_printers": bool(enabled_printers),
        "printer_capabilities": printer_capabilities,
        "focused_artifact": requested_artifact,
        "focused_job": requested_job,
        "focused_document": requested_artifact or requested_job,
        "restored_extra_options": restored_extra_options if requested_job else {},
    })


def _visible_jobs(request):
    jobs = PrintJob.objects.select_related("printer", "owner")
    return jobs if request.user.is_staff else jobs.filter(owner=request.user)


def _job_document(request, pk):
    job = get_object_or_404(_visible_jobs(request), pk=pk)
    if not os.path.isfile(job.document_path):
        raise Http404("This print document is no longer available.")
    return job


def _job_pdf_page(job, page_number, *, thumbnail=False):
    try:
        with fitz.open(job.document_path) as document:
            if page_number < 1 or page_number > document.page_count:
                raise Http404("PDF page does not exist.")
            page = document.load_page(page_number - 1)
            max_width = 220 if thumbnail else 1800
            scale = min(2.0, max_width / max(page.rect.width, 1))
            image = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            response = HttpResponse(image.tobytes("jpeg", jpg_quality=78 if thumbnail else 82), content_type="image/jpeg")
            response["Cache-Control"] = "private, max-age=300" if thumbnail else "private, max-age=3600"
            return response
    except (fitz.FileDataError, OSError) as exc:
        raise Http404("PDF cannot be opened.") from exc


def _queue_job_refreshes(jobs):
    try:
        queue = django_rq.get_queue("default")
        connection = django_rq.get_connection("default")
    except Exception:
        return
    for job in jobs:
        if job.status in {"completed", "canceled", "error"} or not job.job_uri:
            continue
        lock_key = f"print-job-refresh:{job.pk}"
        try:
            if connection.set(lock_key, "1", nx=True, ex=15):
                queue.enqueue(refresh_print_job, job.pk, job_timeout=30)
        except Exception:
            connection.delete(lock_key)


@never_cache
def job_list(request):
    printer_id = request.GET.get("printer", "")
    jobs = _visible_jobs(request)
    if printer_id.isdigit():
        jobs = jobs.filter(printer_id=printer_id)
    recent = list(jobs[:100])
    for job in recent:
        job.document_available = os.path.isfile(job.document_path)
        job.can_resend = job.document_available and job.printer.is_enabled
    _queue_job_refreshes(recent)
    printers = list(Printer.objects.filter(pk__in=_visible_jobs(request).values("printer_id")).distinct())
    statuses = printer_availabilities(printers)
    counts = {key: jobs.filter(status=key).count() for key in ("queued", "processing", "completed", "error")}
    return render(request, "printmanager/job_list.html", {"jobs": recent, "printers": printers, "printer_statuses": statuses, "selected_printer": printer_id, "counts": counts, "highlight": request.GET.get("highlight", "")})


def job_document(request, pk):
    job = _job_document(request, pk)
    response = FileResponse(
        open(job.document_path, "rb"),
        as_attachment=request.GET.get("download") == "1",
        filename=job.document_name,
        content_type="application/pdf",
    )
    response["Cache-Control"] = "private, no-store"
    return response


def job_preview_info(request, pk):
    job = _job_document(request, pk)
    try:
        with fitz.open(job.document_path) as document:
            return JsonResponse({"name": job.document_name, "pages": document.page_count})
    except (fitz.FileDataError, OSError) as exc:
        raise Http404("PDF cannot be opened.") from exc


def job_preview_page(request, pk, page_number):
    return _job_pdf_page(_job_document(request, pk), page_number)


def job_thumbnail(request, pk):
    return _job_pdf_page(_job_document(request, pk), 1, thumbnail=True)


def job_resend(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    source = get_object_or_404(_visible_jobs(request), pk=pk)
    if not os.path.isfile(source.document_path):
        messages.error(request, "This job cannot be resent because its saved document is no longer available.")
        return redirect(f'{reverse("printmanager:jobs")}?printer={source.printer_id}&highlight={source.pk}')
    if not source.printer.is_enabled:
        messages.error(request, "This job cannot be resent while its printer is disabled.")
        return redirect(f'{reverse("printmanager:jobs")}?printer={source.printer_id}&highlight={source.pk}')

    job = PrintJob.objects.create(
        owner=request.user,
        printer=source.printer,
        document_name=source.document_name,
        document_path=source.document_path,
        options=dict(source.options or {}),
        status="queued",
        status_detail=f"Queued again from print job #{source.pk} with the same settings.",
    )
    if _enqueue_print_job(job):
        messages.success(request, f"{source.document_name} was added to the queue again with the same settings.")
    else:
        messages.error(request, "The new job was saved, but the print queue is currently unavailable.")
    return redirect(f'{reverse("printmanager:jobs")}?printer={job.printer_id}&highlight={job.pk}')
