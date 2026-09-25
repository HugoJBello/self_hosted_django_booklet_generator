import os
import uuid
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db import transaction
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from activity.models import Artifact

from .forms import PrinterForm, PrintForm
from .models import Printer, PrintJob
from .services import CupsError, _run, certify_printer, configure_printer, discover_printers, probe_printer, submit_pdf


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


@staff_member_required
def printer_list(request):
    return render(request, "printmanager/printer_list.html", {"printers": Printer.objects.all()})


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


@staff_member_required
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


@staff_member_required
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


@staff_member_required
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
    if request.method == "POST":
        form = PrintForm(request.POST, request.FILES, artifacts=artifacts)
        if form.is_valid():
            data = form.cleaned_data
            if data["source"] == "upload":
                path, name = _save_upload(data["document"])
            else:
                artifact = get_object_or_404(artifact_queryset, pk=data["artifact"])
                path, name = artifact.path, artifact.name
            options = dict(data["extra_options"])
            for key in ("media", "sides", "orientation_requested", "print_color_mode"):
                value = data.get(key)
                if value:
                    options[key.replace("_", "-")] = value
            if data.get("scaling"):
                options["print-scaling"] = data["scaling"]
            if data.get("collate"):
                options["Collate"] = "True"
            job = PrintJob(owner=request.user, printer=data["printer"], document_name=name, document_path=path, options=options, status="error")
            try:
                result, merged, effective = submit_pdf(printer=data["printer"], path=path, title=name, copies=data["copies"], page_ranges=data["page_ranges"], options=options)
                job.options = {"copies": data["copies"], "page_ranges": data["page_ranges"], **merged}
                job.effective_options = effective
                job.transport = "ipp-create-send"
                job.cups_job_id = result
                job.status = "submitted"
                messages.success(request, f"CUPS accepted {name}: {result}")
            except CupsError as exc:
                job.error_message = str(exc)
                messages.error(request, f"CUPS could not print the document: {exc}")
            job.save()
            if job.status == "submitted":
                return redirect("printmanager:print")
    else:
        form = PrintForm(artifacts=artifacts, initial={
            "source": "recent" if requested_artifact or request.GET.get("page") else "upload",
            "artifact": str(requested_artifact.pk) if requested_artifact else "",
        })
    jobs = PrintJob.objects.select_related("printer").filter(owner=request.user)[:20]
    printer_capabilities = {
        str(printer.pk): {"options": printer.supported_options, "defaults": printer.default_options}
        for printer in Printer.objects.filter(is_enabled=True)
    }
    return render(request, "printmanager/print_form.html", {
        "form": form,
        "jobs": jobs,
        "artifacts": artifacts,
        "artifacts_page": artifacts_page,
        "printer_capabilities": printer_capabilities,
    })
