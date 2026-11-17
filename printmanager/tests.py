import gzip
import io
import os
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
import fitz
from PIL import Image

from activity.models import Activity, Artifact

from .models import Printer, PrintJob
from .image_documents import PAPER_SIZES, image_layout
from .services import (
    CupsError, _convert_pdf, _normalize_urf_luminance,
    _select_document_format,
    _ipp_job_operation, _transport_options, _validate_transport_options, parse_devices,
    certify_printer, parse_ipp_attributes, parse_option_lines, probe_printer, read_job_status,
    submit_pdf,
)
from .tasks import refresh_print_job, submit_print_job


class CupsParsingTests(TestCase):
    @patch("printmanager.services._run")
    def test_ipp_send_document_declares_lossless_compression(self, run):
        templates = []
        run.side_effect = lambda args, **kwargs: templates.append(Path(args[-1]).read_text()) or ""
        printer = type("Printer", (), {"device_uri": "ipp://printer/ipp/print"})()
        _ipp_job_operation(
            printer=printer, job_uri="ipp://printer/jobs/1", operation="Send-Document",
            source_path="/tmp/document.pwg.gz", document_format="image/pwg-raster",
            compression="gzip",
        )
        self.assertIn("ATTR keyword compression gzip", templates[0])
        self.assertIn("ATTR mimeMediaType document-format image/pwg-raster", templates[0])

    @patch("printmanager.services._run")
    def test_job_operations_use_job_uri_without_printer_uri(self, run):
        printer = type("Printer", (), {"device_uri": "ipp://printer/ipp/print"})()
        job_uri = "ipp://printer/jobs/8"
        requests = []
        run.side_effect = lambda args, **kwargs: requests.append(Path(args[-1]).read_text()) or ""
        for operation in ("Get-Job-Attributes", "Send-Document", "Cancel-Job"):
            with self.subTest(operation=operation):
                _ipp_job_operation(
                    printer=printer, job_uri=job_uri, operation=operation,
                    source_path="/tmp/document.urf", document_format="image/urf",
                )
                request = requests[-1]
                self.assertIn(f"ATTR uri job-uri {job_uri}", request)
                self.assertNotIn("ATTR uri printer-uri", request)

    @patch("printmanager.services._ipp_job_operation")
    @patch("printmanager.services._direct_ipp_request")
    @patch("printmanager.services._validate_transport_options")
    def test_certification_uses_validated_transport_options(self, validate, request, operation):
        printer = type("Printer", (), {
            "name": "office", "device_uri": "ipp://printer/ipp/print",
            "default_options": {"media-source": "auto", "printer-resolution": "4800x1200dpi",
                                "print-quality": "high", "orientation-requested": "3"},
            "supported_options": {"sides": {"choices": ["two-sided-long-edge"]}},
            "document_formats": ["image/urf"], "preferred_document_format": "image/urf",
        })()
        validate.return_value = {"sides": "two-sided-long-edge", "print-quality": "high"}
        request.return_value = "job-uri (uri) = ipp://printer/jobs/42"
        operation.side_effect = ["sides (keyword) = two-sided-long-edge", ""]
        report = certify_printer(printer)
        self.assertTrue(report["sides"]["two-sided-long-edge"]["verified"])
        self.assertEqual(validate.call_args.kwargs["options"]["printer-resolution"], "600dpi")
        self.assertNotIn("orientation-requested", validate.call_args.kwargs["options"])
        self.assertEqual(request.call_args.kwargs["options"], validate.return_value)

    @patch("printmanager.services._direct_ipp_request")
    def test_ipp_validation_retries_only_rejected_epson_options(self, request):
        printer = type("Printer", (), {
            "supported_options": {"media-source": {"selected": "main"}},
        })()
        request.side_effect = [
            CupsError("client-error-attributes-or-values-not-supported\nmedia-source (unsupported)"),
            CupsError("client-error-conflicting-attributes\nprinter-resolution (resolution) = 600dpi"),
            "successful-ok",
        ]
        options = {"media-source": "auto", "printer-resolution": "600dpi",
                   "print-quality": "high", "sides": "two-sided-long-edge"}
        prepared = _validate_transport_options(
            printer=printer, source_path="", title="test", copies=1,
            document_format="image/urf", options=options,
        )
        self.assertEqual(prepared, {"print-quality": "high", "sides": "two-sided-long-edge"})
        self.assertEqual(options["media-source"], "auto")
        self.assertEqual(request.call_count, 3)

    @patch("printmanager.services._direct_ipp_request")
    def test_ipp_validation_keeps_options_other_printers_accept(self, request):
        printer = object()
        options = {"media-source": "tray-2", "printer-resolution": "600dpi"}
        self.assertEqual(_validate_transport_options(
            printer=printer, source_path="", title="test", copies=1,
            document_format="image/urf", options=options,
        ), options)
        request.assert_called_once()

    @patch("printmanager.services._direct_ipp_request")
    def test_ipp_validation_does_not_discard_explicit_tray(self, request):
        printer = type("Printer", (), {
            "supported_options": {"media-source": {"selected": "main"}},
        })()
        request.side_effect = CupsError(
            "client-error-attributes-or-values-not-supported\nmedia-source (unsupported)"
        )
        with self.assertRaisesRegex(CupsError, "cannot select the alternate tray"):
            _validate_transport_options(
                printer=printer, source_path="", title="test", copies=1,
                document_format="image/urf", options={"media-source": "alternate"},
            )
        request.assert_called_once()

    def test_raster_transport_does_not_reapply_layout(self):
        requested = {
            "media": "iso_a4_210x297mm", "orientation-requested": "4",
            "print-scaling": "auto", "sides": "two-sided-long-edge",
        }
        for document_format in ("image/urf", "image/pwg-raster"):
            with self.subTest(document_format=document_format):
                prepared = _transport_options(requested, document_format)
                self.assertNotIn("orientation-requested", prepared)
                self.assertNotIn("print-scaling", prepared)
                self.assertEqual(prepared["media"], "iso_a4_210x297mm")
                self.assertEqual(prepared["sides"], "two-sided-long-edge")
        self.assertIn("orientation-requested", requested)

    def test_urf_normalization_preserves_luminance_and_uses_w8(self):
        handle = tempfile.NamedTemporaryFile(suffix=".urf", delete=False)
        header = bytearray(32)
        header[0] = 8
        header[1] = 4
        import struct
        struct.pack_into(">II", header, 12, 4, 1)
        handle.write(b"UNIRAST\0\0\0\0\0" + header + bytes((0, 253, 255, 0, 128, 255)))
        handle.close()
        try:
            _normalize_urf_luminance(Path(handle.name))
            normalized = Path(handle.name).read_bytes()
            self.assertEqual(normalized[:8], b"UNIRAST\0")
            self.assertEqual(normalized[13], 0)
            self.assertEqual(normalized[44:], bytes((0, 253, 255, 0, 128, 255)))
        finally:
            os.unlink(handle.name)

    def test_color_urf_conversion_uses_printer_supported_rgb24(self):
        source = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        source.close()
        document = fitz.open()
        page = document.new_page(width=595.28, height=841.89)
        page.draw_rect(fitz.Rect(50, 50, 150, 150), color=(1, 0, 0), fill=(1, 0, 0))
        document.save(source.name)
        document.close()
        output = None
        try:
            output = _convert_pdf(Path(source.name), "image/urf", {
                "media": "iso_a4_210x297mm", "printer-resolution": "300dpi",
                "print-color-mode": "color",
            })
            header = output.read_bytes()[:44]
            self.assertEqual(header[:8], b"UNIRAST\0")
            self.assertEqual(header[12:14], bytes((24, 5)))
        finally:
            if output:
                output.unlink(missing_ok=True)
            os.unlink(source.name)

    def test_image_pdf_uses_printer_preferred_raster_format(self):
        handle = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        handle.close()
        image = io.BytesIO()
        Image.new("RGB", (10, 10), "black").save(image, "PNG")
        document = fitz.open()
        page = document.new_page()
        page.insert_image(page.rect, stream=image.getvalue())
        document.save(handle.name)
        document.close()
        printer = type("Printer", (), {
            "document_formats": ["image/urf", "image/pwg-raster"],
            "preferred_document_format": "image/urf",
        })()
        try:
            self.assertEqual(_select_document_format(printer, Path(handle.name)), "image/urf")
        finally:
            os.unlink(handle.name)

    def test_document_format_override_takes_priority_and_must_be_supported(self):
        printer = type("Printer", (), {
            "document_formats": ["image/urf", "image/pwg-raster"],
            "preferred_document_format": "image/urf",
            "document_format_override": "image/pwg-raster",
        })()
        self.assertEqual(_select_document_format(printer), "image/pwg-raster")
        printer.document_format_override = "application/pdf"
        with self.assertRaisesRegex(CupsError, "not advertised"):
            _select_document_format(printer)

    @patch("printmanager.services.inspect_ipp_printer")
    def test_postscript_auto_sensing_requires_device_language(self, inspect):
        printer = type("Printer", (), {
            "device_uri": "ipp://printer/ipp/print",
            "document_formats": ["application/octet-stream", "image/urf"],
            "preferred_document_format": "image/urf",
            "document_format_override": "",
        })()
        inspect.return_value = {"postscript_supported": True}
        self.assertEqual(_select_document_format(printer), "application/octet-stream")
        inspect.return_value = {"postscript_supported": False}
        self.assertEqual(_select_document_format(printer), "image/urf")
        printer.document_formats.append("application/pdf")
        self.assertEqual(_select_document_format(printer), "application/pdf")

    def test_ipp_device_id_identifies_postscript_only_in_cmd(self):
        prefix = "document-format-supported (1setOf mimeMediaType) = application/octet-stream,image/urf\n"
        self.assertTrue(parse_ipp_attributes(prefix + "printer-device-id (textWithoutLanguage) = MFG:EPSON;CMD:PCL,POSTSCRIPT,URF;MDL:Test;")["postscript_supported"])
        self.assertFalse(parse_ipp_attributes(prefix + "printer-device-id (textWithoutLanguage) = MFG:EPSON;CMD:PCL,URF;MDL:POSTSCRIPT;")["postscript_supported"])

    def test_postscript_conversion_preserves_vectors_and_sets_long_edge_duplex(self):
        source = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        source.close()
        document = fitz.open()
        document.new_page().insert_text((72, 72), "Vector text")
        document.save(source.name)
        document.close()
        output = None
        try:
            output = _convert_pdf(Path(source.name), "application/octet-stream", {
                "sides": "two-sided-long-edge", "printer-resolution": "600dpi",
            })
            content = output.read_bytes()
            self.assertTrue(content.startswith(b"%!PS-Adobe"))
            self.assertIn(b"<< /Duplex true /Tumble false >> setpagedevice", content)
            self.assertIn(b"%%Page:", content)
            self.assertNotIn(b"image/pwg-raster", content)
        finally:
            if output:
                output.unlink(missing_ok=True)
            os.unlink(source.name)

    def test_image_pdf_prefers_original_pdf_when_supported(self):
        handle = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        handle.close()
        image = io.BytesIO()
        Image.new("RGB", (10, 10), "black").save(image, "PNG")
        document = fitz.open()
        page = document.new_page()
        page.insert_image(page.rect, stream=image.getvalue())
        document.save(handle.name)
        document.close()
        printer = type("Printer", (), {
            "document_formats": ["application/pdf", "image/urf"],
            "preferred_document_format": "application/pdf",
        })()
        try:
            self.assertEqual(_select_document_format(printer, Path(handle.name)), "application/pdf")
        finally:
            os.unlink(handle.name)

    @patch("printmanager.services._convert_pdf")
    @patch("printmanager.services._ipp_job_operation")
    @patch("printmanager.services._direct_ipp_request")
    def test_pdf_is_submitted_unchanged_without_rasterizing(self, direct, operation, convert):
        handle = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        handle.close()
        image = io.BytesIO()
        Image.new("RGB", (10, 10), "black").save(image, "PNG")
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), "Vector text must remain vector text")
        page.insert_image(fitz.Rect(72, 90, 82, 100), stream=image.getvalue())
        document.save(handle.name)
        document.close()
        printer = type("Printer", (), {
            "name": "office", "device_uri": "ipp://printer/ipp/print",
            "default_options": {},
            "document_formats": ["application/pdf", "image/urf"],
            "preferred_document_format": "image/urf",
        })()
        direct.side_effect = [
            "", "job-id (integer) = 42\njob-uri (uri) = ipp://printer/jobs/42",
        ]
        operation.side_effect = ["", ""]
        try:
            submit_pdf(printer=printer, path=handle.name, title="booklet.pdf")
            convert.assert_not_called()
            sent = operation.call_args_list[-1].kwargs
            self.assertEqual(sent["document_format"], "application/pdf")
            self.assertEqual(sent["source_path"], Path(handle.name))
        finally:
            os.unlink(handle.name)

    @patch("printmanager.services.PWG_GZIP_MIN_BYTES", 10)
    @patch("printmanager.services.inspect_ipp_printer")
    @patch("printmanager.services._convert_pdf")
    @patch("printmanager.services._validate_transport_options")
    @patch("printmanager.services._direct_ipp_request")
    @patch("printmanager.services._ipp_job_operation")
    def test_large_pwg_uses_gzip_only_when_supported(self, operation, direct, validate,
                                                      convert, inspect):
        printer = type("Printer", (), {
            "name": "office", "device_uri": "ipp://printer/ipp/print",
            "default_options": {"sides": "two-sided-long-edge", "printer-resolution": "600dpi"},
            "document_formats": ["image/pwg-raster"],
            "preferred_document_format": "image/pwg-raster",
            "document_format_override": "",
        })()
        source = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        source.close()
        Path(source.name).write_bytes(b"source")
        validate.side_effect = lambda **kwargs: kwargs["options"]
        direct.return_value = "job-id (integer) = 42\njob-uri (uri) = ipp://printer/jobs/42"
        sent = []

        def record_operation(**kwargs):
            if kwargs["operation"] == "Send-Document":
                sent.append((kwargs["compression"], Path(kwargs["source_path"]).read_bytes()))
            return "sides (keyword) = two-sided-long-edge"

        operation.side_effect = record_operation
        try:
            for compressions, payload, expected_gzip in (
                (["none", "gzip"], b"PwgRaster" * 4, True),
                (["none", "gzip"], b"small", False),
                (["none"], b"PwgRaster" * 4, False),
            ):
                with self.subTest(compressions=compressions, payload=payload):
                    converted = tempfile.NamedTemporaryFile(suffix=".pwg", delete=False)
                    converted.write(payload)
                    converted.close()
                    convert.return_value = Path(converted.name)
                    inspect.return_value = {"pwg_sheet_back": "rotated", "compressions": compressions}
                    _, merged, _, _ = submit_pdf(printer=printer, path=source.name, title="booklet.pdf")
                    compression, transmitted = sent[-1]
                    self.assertEqual(compression, "gzip" if expected_gzip else None)
                    self.assertEqual(gzip.decompress(transmitted) if expected_gzip else transmitted, payload)
                    self.assertEqual(merged["printer-resolution"], "600dpi")
                    self.assertEqual(convert.call_args.args[2]["printer-resolution"], "600dpi")
        finally:
            os.unlink(source.name)

    @patch("printmanager.services._run")
    def test_pdf_only_printer_gets_raster_backed_normalized_pdf(self, run):
        source = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        source.close()
        document = fitz.open()
        document.new_page(width=1190.55, height=841.89)
        document.save(source.name)
        document.close()

        def create_output(args, **_kwargs):
            output = next(item.split("=", 1)[1] for item in args if item.startswith("-sOutputFile="))
            Path(output).write_bytes(b"normalized pdf")
            return ""

        run.side_effect = create_output
        try:
            output = _convert_pdf(Path(source.name), "application/pdf", {
                "media": "A4", "orientation-requested": "4",
                "printer-resolution": "600dpi", "print-color-mode": "color",
            })
            args = run.call_args.args[0]
            self.assertIn("-sDEVICE=pdfimage24", args)
            self.assertIn("-r300", args)
            self.assertIn("-dPDFFitPage", args)
            self.assertIn("-dDEVICEWIDTHPOINTS=841.89", args)
            self.assertIn("-dDEVICEHEIGHTPOINTS=595.28", args)
            output.unlink(missing_ok=True)
        finally:
            os.unlink(source.name)

    @patch("printmanager.services._run")
    def test_pwg_conversion_uses_physical_a4_feed_dimensions(self, run):
        source = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        source.close()
        document = fitz.open()
        document.new_page(width=1190.55, height=841.89)
        document.save(source.name)
        document.close()

        def create_output(args, **_kwargs):
            output = next(item.split("=", 1)[1] for item in args if item.startswith("-sOutputFile="))
            mono = "-dcupsColorSpace=18" in args
            header = bytearray(1800)
            header[:4] = b"RaS2"
            header[4:14] = b"PwgRaster\0"
            import struct
            struct.pack_into(">II", header, 376, 4961, 7016)
            struct.pack_into(">II", header, 388, 8, 8 if mono else 24)
            struct.pack_into(">I", header, 404, 18 if mono else 19)
            Path(output).write_bytes(header)
            return ""

        run.side_effect = create_output
        try:
            output = _convert_pdf(Path(source.name), "image/pwg-raster", {
                "media": "iso_a4_210x297mm", "orientation-requested": "4",
                "printer-resolution": "1200dpi", "print-color-mode": "monochrome",
            })
            args = run.call_args.args[0]
            self.assertIn("-r600", args)
            self.assertIn("-dFIXEDMEDIA", args)
            self.assertIn("-dPDFFitPage", args)
            self.assertIn("-dDEVICEWIDTHPOINTS=595.28", args)
            self.assertIn("-dDEVICEHEIGHTPOINTS=841.89", args)
            self.assertIn("-dcupsColorSpace=18", args)
            self.assertIn("-dcupsBitsPerColor=8", args)
            self.assertIn("-dcupsColorOrder=0", args)
            self.assertNotIn("-c", args)
            output.unlink(missing_ok=True)
            output = _convert_pdf(Path(source.name), "image/pwg-raster", {
                "media": "iso_a4_210x297mm", "printer-resolution": "600dpi",
                "print-color-mode": "color",
            })
            args = run.call_args.args[0]
            self.assertIn("-dcupsColorSpace=19", args)
            self.assertIn("-dcupsBitsPerColor=8", args)
            self.assertIn("-dcupsColorOrder=0", args)
            self.assertNotIn("-c", args)
            output.unlink(missing_ok=True)
        finally:
            os.unlink(source.name)

    def test_pwg_duplex_header_matches_requested_binding_edge(self):
        source = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        source.close()
        document = fitz.open()
        for _ in range(2):
            page = document.new_page(width=595.28, height=841.89)
            page.draw_rect(fitz.Rect(20, 20, 70, 70), fill=(0, 0, 0))
        document.save(source.name)
        document.close()
        import struct
        try:
            for sides, expected in (
                ("one-sided", (0, 0)),
                ("two-sided-long-edge", (1, 0)),
                ("two-sided-short-edge", (1, 1)),
            ):
                with self.subTest(sides=sides):
                    output = _convert_pdf(Path(source.name), "image/pwg-raster", {
                        "media": "iso_a4_210x297mm", "printer-resolution": "300dpi",
                        "print-color-mode": "color", "sides": sides,
                    }, pwg_sheet_back="rotated")
                    try:
                        raster = output.read_bytes()
                        headers = [position for position in range(len(raster))
                                   if raster.startswith(b"PwgRaster", position)]
                        self.assertEqual(len(headers), 2)
                        header = raster[:408]
                        self.assertEqual((
                            struct.unpack_from(">I", header, 276)[0],
                            struct.unpack_from(">I", header, 372)[0],
                        ), expected)
                        back_transform = (-1, -1) if sides == "two-sided-long-edge" else (1, 1)
                        self.assertEqual(struct.unpack_from(">ii", raster, headers[1] + 456), back_transform)
                        front_pixels = raster[headers[0] + 1796:headers[1]]
                        back_pixels = raster[headers[1] + 1796:]
                        if sides == "two-sided-long-edge":
                            self.assertNotEqual(front_pixels, back_pixels)
                        else:
                            self.assertEqual(front_pixels, back_pixels)
                    finally:
                        output.unlink(missing_ok=True)
        finally:
            os.unlink(source.name)

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

    def test_upload_panel_contains_watermark_controls(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("printmanager:print"))
        html = response.content.decode()
        upload = html.split('id="upload-source"', 1)[1].split('id="recent-source"', 1)[0]
        self.assertIn('id="print-watermark-container"', upload)
        self.assertIn('data-watermark-options hidden', upload)
        self.assertEqual(html.count('name="watermark"'), 1)

    def test_uploaded_pdf_can_be_watermarked_before_printing(self):
        self.client.force_login(self.user)
        with fitz.open() as document:
            document.new_page()
            upload = SimpleUploadedFile("upload.pdf", document.tobytes(), content_type="application/pdf")
        response = self.client.post(reverse("printmanager:print"), {
            "printer": self.printer.pk, "source": "upload", "copies": 1,
            "document": upload, "watermark": "on", "watermark_filename": "on",
        })
        self.assertEqual(response.status_code, 302)
        job = PrintJob.objects.get()
        with fitz.open(job.document_path) as document:
            self.assertIn("upload.pdf", document[0].get_text())
            self.assertEqual(len(document[0].get_drawings()), 2)
        os.unlink(job.document_path)

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

    def test_multiple_uploaded_pdfs_are_queued_with_the_same_settings(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("printmanager:print"), {
            "printer": self.printer.pk, "source": "upload", "copies": 3,
            "media": "A4", "sides": "two-sided-long-edge", "scaling": "fit-to-page",
            "document": [
                SimpleUploadedFile("first.pdf", b"%PDF-first", content_type="application/pdf"),
                SimpleUploadedFile("second.pdf", b"%PDF-second", content_type="application/pdf"),
            ],
        })

        jobs = list(PrintJob.objects.order_by("pk"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual([job.document_name for job in jobs], ["first.pdf", "second.pdf"])
        self.assertEqual(jobs[0].options, jobs[1].options)
        self.assertEqual(jobs[0].options["copies"], 3)
        self.assertEqual(jobs[0].options["sides"], "two-sided-long-edge")
        self.assertEqual(jobs[0].options["print-scaling"], "fit-to-page")
        self.assertEqual(self.queue.enqueue.call_count, 2)
        for job in jobs:
            if os.path.exists(job.document_path):
                os.unlink(job.document_path)

    def test_print_link_focuses_only_the_requested_activity_pdf(self):
        activity = Activity.objects.create(owner=self.user, tool="splitpdf", title="Selected split")
        selected = Artifact.objects.create(
            activity=activity, kind="output", name="selected.pdf", path="/tmp/selected.pdf",
            content_type="application/pdf", size=1234,
        )
        other_activity = Activity.objects.create(owner=self.user, tool="ocrpdf", title="Other run")
        Artifact.objects.create(
            activity=other_activity, kind="output", name="must-not-appear.pdf", path="/tmp/other.pdf",
            content_type="application/pdf", size=4321,
        )
        self.client.force_login(self.user)

        response = self.client.get(f'{reverse("printmanager:print")}?artifact={selected.pk}')

        self.assertContains(response, "Print this document")
        self.assertContains(response, "selected.pdf")
        self.assertContains(response, 'name="source" value="recent"', html=False)
        self.assertContains(response, f'name="artifact" value="{selected.pk}"', html=False)
        self.assertNotContains(response, "must-not-appear.pdf")
        self.assertNotContains(response, "PDFs from activity")
        self.assertNotContains(response, "Upload documents")

    def test_uploaded_image_becomes_pdf_and_is_saved_in_activity(self):
        image_bytes = io.BytesIO()
        Image.new("RGB", (1200, 600), "#336699").save(image_bytes, "PNG")
        payload = image_bytes.getvalue()
        image_options = [{
            "name": "wide.png", "size": len(payload),
            "options": {"paper": "a4", "orientation": "landscape", "fit": "contain", "margin_mm": 12},
        }]
        self.client.force_login(self.user)

        response = self.client.post(reverse("printmanager:print"), {
            "printer": self.printer.pk, "source": "upload", "copies": 1,
            "document": SimpleUploadedFile("wide.png", payload, content_type="image/png"),
            "image_options": json.dumps(image_options),
            "document_manifest": json.dumps([{"name": "wide.png", "size": len(payload), "type": "image"}]),
        })

        job = PrintJob.objects.get()
        activity = Activity.objects.get(tool="printdocuments")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(job.document_name, "wide.pdf")
        self.assertEqual(activity.options["image_layouts"][0]["margin_mm"], 12.0)
        self.assertEqual(activity.artifacts.filter(kind="input", name="wide.png").count(), 1)
        output = activity.artifacts.get(kind="output")
        self.assertEqual(output.path, job.document_path)
        with fitz.open(output.path) as document:
            page = document[0]
            self.assertAlmostEqual(page.rect.width, PAPER_SIZES["a4"][1], delta=.1)
            self.assertAlmostEqual(page.rect.height, PAPER_SIZES["a4"][0], delta=.1)
            self.assertTrue(page.get_images(full=True))
        for artifact in activity.artifacts.all():
            if os.path.exists(artifact.path):
                os.unlink(artifact.path)

    def test_image_layout_respects_fit_orientation_and_margins(self):
        page, rect, options = image_layout((1600, 800), {
            "paper": "a4", "orientation": "landscape", "fit": "contain", "margin_mm": 10,
        })
        margin = options["margin_mm"] * 72 / 25.4
        self.assertGreater(page[0], page[1])
        self.assertGreaterEqual(rect[0], margin - 0.01)
        self.assertGreaterEqual(rect[1], margin - 0.01)
        self.assertLessEqual(rect[2], page[0] - margin + 0.01)
        self.assertLessEqual(rect[3], page[1] - margin + 0.01)

    def test_chunked_pdf_upload_is_reassembled_as_a_normal_form_file(self):
        self.client.force_login(self.user)
        start = self.client.post(reverse("activity:chunk_upload"), {
            "action": "start", "filename": "large.pdf", "field": "document", "size": "13",
        })
        self.assertEqual(start.status_code, 200)
        token = start.json()["token"]
        first = self.client.post(reverse("activity:chunk_upload"), {
            "action": "chunk", "token": token, "offset": "0",
            "chunk": SimpleUploadedFile("block", b"%PDF-1.", content_type="application/octet-stream"),
        })
        self.assertEqual(first.json()["offset"], 7)
        complete = self.client.post(reverse("activity:chunk_upload"), {
            "action": "chunk", "token": token, "offset": "7",
            "chunk": SimpleUploadedFile("block", b"4\nbody", content_type="application/octet-stream"),
        })
        self.assertTrue(complete.json()["complete"])

        response = self.client.post(reverse("printmanager:print"), {
            "printer": self.printer.pk, "source": "upload", "copies": 1,
            "chunk_upload_tokens": json.dumps([{"token": token, "field": "document"}]),
        })

        job = PrintJob.objects.get()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(job.document_name, "large.pdf")
        with open(job.document_path, "rb") as saved:
            self.assertEqual(saved.read(), b"%PDF-1.4\nbody")
        os.unlink(job.document_path)

    def test_chunk_upload_rejects_out_of_sequence_blocks(self):
        self.client.force_login(self.user)
        token = self.client.post(reverse("activity:chunk_upload"), {
            "action": "start", "filename": "large.pdf", "field": "document", "size": "8",
        }).json()["token"]
        response = self.client.post(reverse("activity:chunk_upload"), {
            "action": "chunk", "token": token, "offset": "2",
            "chunk": SimpleUploadedFile("block", b"%PDF", content_type="application/octet-stream"),
        })
        self.assertEqual(response.status_code, 409)

    def test_chunk_upload_token_is_private_to_the_session(self):
        self.client.force_login(self.user)
        token = self.client.post(reverse("activity:chunk_upload"), {
            "action": "start", "filename": "private.pdf", "field": "document", "size": "8",
        }).json()["token"]
        self.client.force_login(self.other)
        response = self.client.post(reverse("activity:chunk_upload"), {
            "action": "chunk", "token": token, "offset": "0",
            "chunk": SimpleUploadedFile("block", b"%PDF-1\n", content_type="application/octet-stream"),
        })
        self.assertEqual(response.status_code, 404)

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

    @patch("printmanager.tasks.read_job_status", side_effect=CupsError("client-error-not-found"))
    def test_printer_that_drops_completed_job_records_does_not_leave_job_queued(self, read_status):
        job = PrintJob.objects.create(
            owner=self.user, printer=self.printer, document_name="short-retention.pdf",
            document_path="/tmp/short-retention.pdf", status="queued",
            cups_job_id="IPP job 10 accepted", job_uri="ipp://printer/jobs/10",
        )
        refresh_print_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, "submitted")
        self.assertIn("no longer reports its status", job.status_detail)

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
