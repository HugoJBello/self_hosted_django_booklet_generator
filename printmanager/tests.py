import os
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from activity.models import Activity, Artifact

from .models import Printer, PrintJob
from .services import CupsError, parse_devices, parse_option_lines, probe_printer


class CupsParsingTests(TestCase):
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
        self.addCleanup(patch.stopall)

    def test_only_staff_can_configure_printers(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("printmanager:printers")).status_code, 302)
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

    def test_print_page_selects_first_connected_printer_and_disables_offline(self):
        offline = Printer.objects.create(name="offline", device_uri="ipp://offline/ipp/print", is_default=True)
        self.availability.side_effect = None
        self.availability.return_value = [
            {"printer": offline, "connected": False, "state": "offline", "label": "Offline", "detail": "Timed out."},
            {"printer": self.printer, "connected": True, "state": "ready", "label": "Ready", "detail": "Connected and ready."},
        ]
        self.client.force_login(self.user)
        response = self.client.get(reverse("printmanager:print"))
        self.assertContains(response, f'id="printer-{offline.pk}" disabled')
        self.assertContains(response, f'id="printer-{self.printer.pk}"  checked')
        self.assertContains(response, "Ready")

    @patch("printmanager.views.submit_pdf", return_value=("request id is office-1", {"media": "A4"}, {"media": "A4"}))
    def test_user_can_submit_uploaded_pdf(self, submit):
        self.client.force_login(self.user)
        response = self.client.post(reverse("printmanager:print"), {
            "printer": self.printer.pk, "source": "upload", "copies": 2,
            "media": "A4", "sides": "one-sided", "collate": "on",
            "document": SimpleUploadedFile("test.pdf", b"%PDF-1.4", content_type="application/pdf"),
        })
        self.assertRedirects(response, reverse("printmanager:print"))
        job = PrintJob.objects.get()
        self.assertEqual((job.owner, job.status), (self.user, "submitted"))
        submit.assert_called_once()
        if os.path.exists(job.document_path):
            os.unlink(job.document_path)

    @patch("printmanager.views.submit_pdf", side_effect=CupsError("offline"))
    def test_failed_submission_is_audited(self, submit):
        self.client.force_login(self.user)
        response = self.client.post(reverse("printmanager:print"), {
            "printer": self.printer.pk, "source": "upload", "copies": 1,
            "document": SimpleUploadedFile("failed.pdf", b"%PDF", content_type="application/pdf"),
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PrintJob.objects.get().status, "error")

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
