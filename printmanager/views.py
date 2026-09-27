import os
import uuid
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.core.paginator import Paginator
from django.db import transaction
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from activity.models import Artifact

from .forms import PrinterForm, PrintForm
from .models import Printer, PrintJob
from .services import CupsError, _run, certify_printer, configure_printer, discover_printers, printer_availabilities, probe_printer, read_job_status, submit_pdf


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


@staff_required
def printer_list(request):
    printers = list(Printer.objects.prefetch_related("jobs").all())
    live = {item["printer"].pk: item for item in printer_availabilities(printers)}
    for printer in printers:
        printer.live_status = live[printer.pk]
        printer.job_count = printer.jobs.count()
        printer.active_job_count = printer.jobs.filter(status__in=("queued", "processing", "submitted")).count()
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


def print_document(request):
    artifact_queryset = _artifact_queryset(request)
    requested_artifact = None
    requested_artifact_id = request.GET.get("artifact", "")
    if requested_artifact_id.isdigit():
        requested_artifact = artifact_queryset.filter(pk=requested_artifact_id).first()
    artifacts_page = Paginator(artifact_queryset, 12).get_page(request.GET.get("page"))
    artifacts = list(artifacts_page.object_list)
    if requested_artifact and all(item.pk != requested_artifact.pk for item in artifacts):
        artifacts.insert(0, requested_artifact)
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
                documents = [_save_upload(upload) for upload in data["document"]]
            else:
                selected = list(artifact_queryset.filter(pk__in=data["artifact"]))
                documents = [(artifact.path, artifact.name) for artifact in selected]
            options = dict(data["extra_options"])
            for key in ("media", "sides", "orientation_requested", "print_color_mode"):
                value = data.get(key)
                if value:
                    options[key.replace("_", "-")] = value
            if data.get("scaling"):
                options["print-scaling"] = data["scaling"]
            if data.get("collate"):
                options["Collate"] = "True"
            jobs = []
            for path, name in documents:
                job = PrintJob(owner=request.user, printer=data["printer"], document_name=name, document_path=path, options=options, status="error")
                try:
                    response = submit_pdf(printer=data["printer"], path=path, title=name, copies=data["copies"], page_ranges=data["page_ranges"], options=options)
                    result, merged, effective = response[:3]
                    job.job_uri = response[3] if len(response) > 3 else ""
                    job.options = {"copies": data["copies"], "page_ranges": data["page_ranges"], **merged}
                    job.effective_options = effective
                    job.transport = "ipp-create-send"
                    job.cups_job_id = result
                    job.status = "queued"
                except CupsError as exc:
                    job.error_message = str(exc)
                job.save()
                jobs.append(job)
            accepted = sum(job.status != "error" for job in jobs)
            if accepted:
                messages.success(request, f"{accepted} document(s) accepted and queued.")
                return redirect(f'{reverse("printmanager:jobs")}?printer={data["printer"].pk}&highlight={jobs[-1].pk}')
            messages.error(request, "The printer did not accept the selected documents.")
    else:
        form = PrintForm(artifacts=artifacts, initial={
            "source": "recent" if requested_artifact or request.GET.get("page") else "upload",
            "artifact": [str(requested_artifact.pk)] if requested_artifact else [],
            "printer": default_printer.pk if default_printer else None,
        })
    printer_capabilities = {
        str(printer.pk): {"options": printer.supported_options, "defaults": printer.default_options}
        for printer in Printer.objects.filter(is_enabled=True)
    }
    return render(request, "printmanager/print_form.html", {
        "form": form,
        "artifacts": artifacts,
        "artifacts_page": artifacts_page,
        "printer_choices": printer_choices,
        "has_enabled_printers": bool(enabled_printers),
        "printer_capabilities": printer_capabilities,
    })


def _visible_jobs(request):
    jobs = PrintJob.objects.select_related("printer", "owner")
    return jobs if request.user.is_staff else jobs.filter(owner=request.user)


def _refresh_jobs(jobs):
    now = timezone.now()
    for job in jobs:
        if job.status in {"completed", "canceled", "error"} or not job.job_uri:
            continue
        try:
            live = read_job_status(job)
            job.status = live["status"]
            job.status_detail = live["detail"]
            job.status_data = live["attributes"]
            if job.status == "processing" and not job.started_at:
                job.started_at = now
            if job.status in {"completed", "canceled", "error"} and not job.completed_at:
                job.completed_at = now
            job.last_checked_at = now
            job.save(update_fields=("status", "status_detail", "status_data", "started_at", "completed_at", "last_checked_at"))
        except CupsError as exc:
            job.status_detail = str(exc)
            job.last_checked_at = now
            job.save(update_fields=("status_detail", "last_checked_at"))


def job_list(request):
    printer_id = request.GET.get("printer", "")
    jobs = _visible_jobs(request)
    if printer_id.isdigit():
        jobs = jobs.filter(printer_id=printer_id)
    recent = list(jobs[:100])
    _refresh_jobs(recent)
    printers = list(Printer.objects.filter(pk__in=_visible_jobs(request).values("printer_id")).distinct())
    statuses = printer_availabilities(printers)
    counts = {key: jobs.filter(status=key).count() for key in ("queued", "processing", "completed", "error")}
    return render(request, "printmanager/job_list.html", {"jobs": recent, "printers": printers, "printer_statuses": statuses, "selected_printer": printer_id, "counts": counts, "highlight": request.GET.get("highlight", "")})
