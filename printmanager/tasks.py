"""Background jobs for slow print conversion and submission."""
import logging

from django.utils import timezone

from .models import PrintJob
from .services import CupsError, read_job_status, submit_pdf


logger = logging.getLogger(__name__)


def submit_print_job(job_id):
    """Submit a persisted job to CUPS without tying it to an HTTP request."""
    try:
        job = PrintJob.objects.select_related("printer").get(pk=job_id)
    except PrintJob.DoesNotExist:
        return
    if job.status in {"completed", "canceled", "error"} or job.cups_job_id:
        return

    requested = dict(job.options)
    copies = requested.pop("copies", 1)
    page_ranges = requested.pop("page_ranges", "")
    job.status_detail = "Preparing and sending the document to the printer."
    job.started_at = timezone.now()
    job.save(update_fields=("status_detail", "started_at"))
    try:
        response = submit_pdf(
            printer=job.printer,
            path=job.document_path,
            title=job.document_name,
            copies=copies,
            page_ranges=page_ranges,
            options=requested,
        )
        result, merged, effective = response[:3]
        job.job_uri = response[3] if len(response) > 3 else ""
        job.options = {"copies": copies, "page_ranges": page_ranges, **merged}
        job.effective_options = effective
        job.transport = "ipp-create-send"
        job.cups_job_id = result
        job.status = "queued"
        job.status_detail = "Accepted by the printer queue."
        job.error_message = ""
        job.save(update_fields=(
            "job_uri", "options", "effective_options", "transport", "cups_job_id",
            "status", "status_detail", "error_message",
        ))
    except CupsError as exc:
        job.status = "error"
        job.error_message = str(exc)
        job.status_detail = "The document could not be submitted to the printer."
        job.completed_at = timezone.now()
        job.save(update_fields=("status", "error_message", "status_detail", "completed_at"))
    except Exception:
        logger.exception("Unexpected failure submitting print job %s", job.pk)
        job.status = "error"
        job.error_message = "An unexpected error occurred while submitting the document."
        job.status_detail = "The document could not be submitted to the printer."
        job.completed_at = timezone.now()
        job.save(update_fields=("status", "error_message", "status_detail", "completed_at"))


def refresh_print_job(job_id):
    """Refresh IPP state outside the web request, tolerating unavailable printers."""
    try:
        job = PrintJob.objects.select_related("printer").get(pk=job_id)
    except PrintJob.DoesNotExist:
        return
    if job.status in {"completed", "canceled", "error"} or not job.job_uri:
        return
    now = timezone.now()
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
        job.save(update_fields=(
            "status", "status_detail", "status_data", "started_at", "completed_at",
            "last_checked_at",
        ))
    except CupsError as exc:
        job.status_detail = str(exc)
        job.last_checked_at = now
        job.save(update_fields=("status_detail", "last_checked_at"))
    except Exception:
        logger.exception("Unexpected failure refreshing print job %s", job.pk)
        job.status_detail = "The printer status could not be refreshed."
        job.last_checked_at = now
        job.save(update_fields=("status_detail", "last_checked_at"))
