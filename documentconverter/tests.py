import io
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import fitz
from PIL import Image
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from activity.models import Activity

from .services import ConversionError, convert_office_to_pdf


def _png_upload(name="scan.png"):
    content = io.BytesIO()
    Image.new("RGB", (120, 80), "navy").save(content, "PNG")
    return SimpleUploadedFile(name, content.getvalue(), content_type="image/png")


class DocumentConverterTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.user = get_user_model().objects.create_user("converter", password="secret")
        self.client.force_login(self.user)

    def test_image_conversion_reuses_activity_preview_print_and_download(self):
        response = self.client.post(reverse("documentconverter:form"), {
            "documents": _png_upload(),
            "paper": "a4",
            "orientation": "portrait",
            "fit": "contain",
            "margin_mm": "10",
        })

        self.assertEqual(response.status_code, 200)
        activity = Activity.objects.get(tool="documentconverter")
        source = activity.artifacts.get(kind="input")
        output = activity.artifacts.get(kind="output")
        self.assertEqual(source.content_type, "image/png")
        self.assertContains(response, reverse("activity:preview", args=[output.public_id]))
        self.assertContains(response, reverse("activity:file", args=[output.public_id]))
        self.assertContains(response, f'{reverse("printmanager:print")}?artifact={output.pk}')
        with fitz.open(output.path) as document:
            self.assertEqual(document.page_count, 1)
            self.assertAlmostEqual(document[0].rect.width, 595.28, places=1)
            self.assertAlmostEqual(document[0].rect.height, 841.89, places=1)

    @override_settings(OFFICE_CONVERSION_TIMEOUT=37)
    @patch("documentconverter.services.subprocess.run")
    def test_office_conversion_uses_isolated_libreoffice_profile(self, run):
        def create_pdf(command, **kwargs):
            output_dir = Path(command[command.index("--outdir") + 1])
            source = Path(command[-1])
            document = fitz.open()
            document.new_page()
            document.save(output_dir / f"{source.stem}.pdf")
            document.close()
            return subprocess.CompletedProcess(command, 0, "", "")

        run.side_effect = create_pdf
        uploaded = SimpleUploadedFile(
            "lesson plan.docx", b"docx bytes",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        converted = convert_office_to_pdf(uploaded)

        command = run.call_args.args[0]
        self.assertEqual(command[0], "libreoffice")
        self.assertIn("--headless", command)
        self.assertIn("pdf:writer_pdf_Export", command)
        self.assertTrue(any(value.startswith("-env:UserInstallation=file:") for value in command))
        self.assertEqual(run.call_args.kwargs["timeout"], 37)
        self.assertEqual(converted["output_name"], "lesson-plan.pdf")
        self.assertTrue(Path(converted["source_path"]).is_file())
        self.assertTrue(Path(converted["output_path"]).is_file())

    @patch("documentconverter.services.subprocess.run", side_effect=subprocess.TimeoutExpired("libreoffice", 1))
    def test_failed_office_conversion_removes_its_files(self, _run):
        uploaded = SimpleUploadedFile("slow.odt", b"odt bytes", content_type="application/vnd.oasis.opendocument.text")

        with self.assertRaisesMessage(ConversionError, "too long"):
            convert_office_to_pdf(uploaded)

        conversion_root = Path(self.media.name) / "document_conversions"
        self.assertFalse(list(conversion_root.glob("*")))

    def test_unsupported_upload_is_rejected_without_activity(self):
        response = self.client.post(reverse("documentconverter:form"), {
            "documents": SimpleUploadedFile("notes.txt", b"text", content_type="text/plain"),
            "paper": "a4", "orientation": "auto", "fit": "contain", "margin_mm": "10",
        })

        self.assertContains(response, "unsupported file type")
        self.assertFalse(Activity.objects.exists())
