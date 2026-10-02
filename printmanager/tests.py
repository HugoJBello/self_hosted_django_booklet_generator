import os
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from activity.models import Activity, Artifact

from .models import Printer, PrintJob
from .services import CupsError, parse_devices, parse_ipp_attributes, parse_option_lines, probe_printer, read_job_status
from .tasks import refresh_print_job, submit_print_job


class CupsParsingTests(TestCase):
    @override_settings(CUPS_STATUS_TIMEOUT=7)
    @patch("printmanager.services._ipp_job_operation", return_value="job-state (enum) = processing")
    def test_job_status_uses_short_status_timeout(self, operation):
        job = type("Job", (), {"job_uri": "ipp://printer/jobs/1", "printer": object()})()
        self.assertEqual(read_job_status(job)["status"], "processing")
        self.assertEqual(operation.call_args.kwargs["timeout"], 7)

    def test_printer_state_reasons_become_actionable_alerts(self):
        identity = parse_ipp_attributes("""printer-state (enum) = processing
printer-state-reasons (1setOf keyword) = media-jam-error, media-empty-warning, toner-empty-error""")
        self.assertEqual([alert["title"] for alert in identity["alerts"]], ["Paper jam", "Out of paper", "Toner is empty"])
        self.assertTrue(all(alert["severity"] == "danger" for alert in identity["alerts"]))

    def test_parses_supported_options(self):
        parsed = parse_option_lines("PageSize/Media Size: *A4 Letter\nDuplex/Duplex: None *DuplexNoTumble")
        self.assertEqual(parsed["PageSize"]["selected"], "A4")
        self.assertEqual(parsed["Duplex"]["choices"], ["None", "DuplexNoTumble"])

    def test_parses_discovered_printers_and_recommends_driverless(self):
        devices = parse_devices("network ipp://office.local/ipp/print\ndirect usb://HP/LaserJet\nnetwork http://not-a-printer/")
        self.assertEqual(len(devices), 2)
        self.assertEqual(devices[0]["driver"], "everywhere")
        self.assertEqual(devices[0]["name"], "office.local")
        self.assertEqual(devices[1]["driver"], "raw")

    @patch("printmanager.services._run")
    @patch("printmanager.services.discover_printers", return_value=[{"uri": "ipp://office/ipp/print"}])
    def test_probe_reads_options_and_removes_temporary_queue(self, discover, run):
        run.return_value = """media-default (keyword) = iso_a4_210x297mm
media-supported (1setOf keyword) = iso_a4_210x297mm, na_letter_8.5x11in
sides-default (keyword) = two-sided-long-edge
sides-supported (1setOf keyword) = one-sided, two-sided-long-edge"""
        identity = probe_printer("ipp://office/ipp/print")
        self.assertEqual(identity["options"]["media"]["selected"], "iso_a4_210x297mm")
        self.assertEqual(identity["options"]["sides"]["selected"], "two-sided-long-edge")
        self.assertEqual(run.call_args.args[0][0], "ipptool")


@override_settings(MEDIA_ROOT=tempfile.gettempdir())
class PrintingViewsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user("user", password="x")
        self.other = User.objects.create_user("other", password="x")
        self.admin = User.objects.create_user("admin", password="x", is_staff=True)
        self.printer = Printer.objects.create(name="office", device_uri="ipp://printer/ipp/print")
        self.availability = patch("printmanager.views.printer_availabilities").start()
        self.availability.side_effect = lambda printers: [
            {"printer": printer, "connected": True, "state": "ready", "label": "Ready", "detail": "Connected and ready."}
            for printer in printers
        ]
        self.queue = patch("printmanager.views.django_rq.get_queue").start().return_value
        self.connection = patch("printmanager.views.django_rq.get_connection").start().return_value
        self.addCleanup(patch.stopall)

    def test_only_staff_can_configure_printers(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("printmanager:printers"))
        self.assertRedirects(response, f'{reverse("accounts:login")}?next={reverse("printmanager:printers")}', fetch_redirect_response=False)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("printmanager:printers")).status_code, 200)

    @patch("printmanager.views.discover_printers", return_value=[{"uri": "ipp://new/ipp/print", "name": "new", "description": "new", "driver": "everywhere"}])
    def test_staff_can_discover_and_regular_user_cannot(self, discover):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("printmanager:printer_discover"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["devices"][0]["configured"])
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("printmanager:printer_discover")).status_code, 403)

    @patch("printmanager.views.probe_printer", return_value={"options": {"media": {"choices": ["A4", "Letter"], "selected": "A4"}}, "defaults": {"media": "A4"}})
    def test_staff_can_load_interactive_printer_options(self, probe):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("printmanager:printer_probe"), {"uri": "ipp://new/ipp/print", "driver": "everywhere"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["defaults"], {"media": "A4"})

    def test_print_page_prefers_connected_printer_but_allows_offline(self):
        offline = Printer.objects.create(name="offline", device_uri="ipp://offline/ipp/print", is_default=True)
        self.availability.side_effect = None
        self.availability.return_value = [
            {"printer": offline, "connected": False, "state": "offline", "label": "Offline", "detail": "Timed out."},
            {"printer": self.printer, "connected": True, "state": "ready", "label": "Ready", "detail": "Connected and ready."},
        ]
        self.client.force_login(self.user)
        response = self.client.get(reverse("printmanager:print"))
        self.assertContains(response, f'id="printer-{offline.pk}"')
        self.assertNotContains(response, f'id="printer-{offline.pk}" disabled')
        self.assertContains(response, f'id="printer-{self.printer.pk}" checked')
        self.assertContains(response, "Ready")

    def test_user_can_attempt_an_offline_printer(self):
        self.availability.side_effect = None
        self.availability.return_value = [
            {"printer": self.printer, "connected": False, "state": "offline", "label": "Offline", "detail": "Timed out."},
        ]
        self.client.force_login(self.user)
        response = self.client.post(reverse("printmanager:print"), {
            "printer": self.printer.pk, "source": "upload", "copies": 1,
            "document": SimpleUploadedFile("attempt.pdf", b"%PDF", content_type="application/pdf"),
        })
        job = PrintJob.objects.get()
        self.assertRedirects(response, f'{reverse("printmanager:jobs")}?printer={self.printer.pk}&highlight={job.pk}', fetch_redirect_response=False)
        self.assertEqual(job.status_detail, "Waiting for background submission.")
        self.queue.enqueue.assert_called_once_with(submit_print_job, job.pk)

    def test_user_can_submit_uploaded_pdf_without_waiting_for_printer(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("printmanager:print"), {
            "printer": self.printer.pk, "source": "upload", "copies": 2,
            "media": "A4", "sides": "one-sided", "collate": "on",
            "document": SimpleUploadedFile("test.pdf", b"%PDF-1.4", content_type="application/pdf"),
        })
        job = PrintJob.objects.get()
        self.assertRedirects(response, f'{reverse("printmanager:jobs")}?printer={self.printer.pk}&highlight={job.pk}', fetch_redirect_response=False)
        self.assertEqual((job.owner, job.status), (self.user, "queued"))
        self.assertEqual(job.options["copies"], 2)
        self.assertEqual(job.options["media"], "A4")
        self.queue.enqueue.assert_called_once_with(submit_print_job, job.pk)
        if os.path.exists(job.document_path):
            os.unlink(job.document_path)

    @patch("printmanager.tasks.submit_pdf", side_effect=CupsError("offline"))
    def test_background_submission_failure_is_audited(self, submit):
        job = PrintJob.objects.create(
            owner=self.user, printer=self.printer, document_name="failed.pdf",
            document_path="/tmp/failed.pdf", options={"copies": 1}, status="queued",
        )
        submit_print_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "error")
        self.assertEqual(job.error_message, "offline")

    @patch("printmanager.tasks.submit_pdf", return_value=(
        "IPP job 7 accepted by office", {"media": "A4"}, {"media": "A4"}, "ipp://printer/jobs/7"
    ))
    def test_background_submission_persists_printer_tracking(self, submit):
        job = PrintJob.objects.create(
            owner=self.user, printer=self.printer, document_name="large.pdf",
            document_path="/tmp/large.pdf",
            options={"copies": 3, "page_ranges": "2-8", "media": "A4"}, status="queued",
        )
        submit_print_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "queued")
        self.assertEqual(job.cups_job_id, "IPP job 7 accepted by office")
        self.assertEqual(job.job_uri, "ipp://printer/jobs/7")
        self.assertEqual(job.status_detail, "Accepted by the printer queue.")
        submit.assert_called_once_with(
            printer=self.printer, path="/tmp/large.pdf", title="large.pdf",
            copies=3, page_ranges="2-8", options={"media": "A4"},
        )

    @patch("printmanager.tasks.read_job_status", return_value={
        "status": "processing", "detail": "media-empty", "attributes": {"job-state-reasons": "media-empty"},
    })
    def test_out_of_paper_status_is_persisted_by_background_worker(self, read_status):
        job = PrintJob.objects.create(
            owner=self.user, printer=self.printer, document_name="large.pdf",
            document_path="/tmp/large.pdf", status="queued", job_uri="ipp://printer/jobs/8",
        )
        refresh_print_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "processing")
        self.assertEqual(job.status_detail, "media-empty")
        self.assertEqual(job.status_data["job-state-reasons"], "media-empty")

    def test_status_page_queues_refresh_without_contacting_printer(self):
        job = PrintJob.objects.create(
            owner=self.user, printer=self.printer, document_name="large.pdf",
            document_path="/tmp/large.pdf", status="queued", job_uri="ipp://printer/jobs/9",
        )
        self.connection.set.return_value = True
        self.client.force_login(self.user)

        response = self.client.get(reverse("printmanager:jobs"))

        self.assertEqual(response.status_code, 200)
        self.queue.enqueue.assert_called_once_with(refresh_print_job, job.pk, job_timeout=30)

    def test_user_cannot_select_another_users_artifact(self):
        activity = Activity.objects.create(owner=self.other, tool="booklets", title="secret")
        artifact = Artifact.objects.create(activity=activity, kind="output", name="secret.pdf", path="/tmp/secret.pdf", content_type="application/pdf")
        self.client.force_login(self.user)
        response = self.client.post(reverse("printmanager:print"), {
            "printer": self.printer.pk, "source": "recent", "artifact": artifact.pk, "copies": 1,
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PrintJob.objects.exists())
        self.assertContains(response, "Select a recent PDF")

    def test_job_history_is_private_for_regular_users(self):
        PrintJob.objects.create(owner=self.other, printer=self.printer, document_name="private.pdf", document_path="/tmp/private.pdf", status="completed")
        PrintJob.objects.create(owner=self.user, printer=self.printer, document_name="mine.pdf", document_path="/tmp/mine.pdf", status="completed")
        self.client.force_login(self.user)
        response = self.client.get(reverse("printmanager:jobs"))
        self.assertContains(response, "mine.pdf")
        self.assertNotContains(response, "private.pdf")
