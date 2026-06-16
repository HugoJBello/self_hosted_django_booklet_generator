from __future__ import annotations

import os
import shutil
import tempfile

import fitz
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .services import build_join_pipeline


TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="joinpdf_test_media_")


def build_pdf_bytes(page_count: int) -> bytes:
    doc = fitz.open()
    for idx in range(page_count):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {idx + 1}")
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class JoinPdfViewTests(TestCase):
    def setUp(self):
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)
        os.makedirs(TEST_MEDIA_ROOT, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def test_upload_accumulates_files_in_session(self):
        response = self.client.post(
            reverse("joinpdf:form"),
            data={
                "action": "upload",
                "input_pdf": [
                    SimpleUploadedFile("uno.pdf", build_pdf_bytes(1), content_type="application/pdf"),
                    SimpleUploadedFile("dos.pdf", build_pdf_bytes(1), content_type="application/pdf"),
                ],
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        items = self.client.session.get("joinpdf_items", [])
        self.assertEqual([item["name"] for item in items], ["uno.pdf", "dos.pdf"])

    def test_join_applies_requested_order_before_generating(self):
        uploads_dir = os.path.join(TEST_MEDIA_ROOT, "join_uploads")
        os.makedirs(uploads_dir, exist_ok=True)

        first_path = os.path.join(uploads_dir, "uno.pdf")
        second_path = os.path.join(uploads_dir, "dos.pdf")
        with open(first_path, "wb") as fh:
            fh.write(build_pdf_bytes(1))
        with open(second_path, "wb") as fh:
            fh.write(build_pdf_bytes(1))

        session = self.client.session
        session["joinpdf_items"] = [
            {"name": "uno.pdf", "path": first_path},
            {"name": "dos.pdf", "path": second_path},
        ]
        session.save()

        response = self.client.post(
            reverse("joinpdf:form"),
            data={
                "action": "join",
                "preserve_parity": "on",
                "item_order": ["1", "0"],
            },
        )

        self.assertEqual(response.status_code, 200)
        items = self.client.session.get("joinpdf_items", [])
        self.assertEqual([item["name"] for item in items], ["dos.pdf", "uno.pdf"])
        self.assertContains(response, "Download result")

    def test_join_cover_is_first_page_and_preserves_odd_starts(self):
        uploads_dir = os.path.join(TEST_MEDIA_ROOT, "join_uploads")
        outputs_dir = os.path.join(TEST_MEDIA_ROOT, "join_outputs")
        os.makedirs(uploads_dir, exist_ok=True)

        first_path = os.path.join(uploads_dir, "uno.pdf")
        second_path = os.path.join(uploads_dir, "dos.pdf")
        with open(first_path, "wb") as fh:
            fh.write(build_pdf_bytes(1))
        with open(second_path, "wb") as fh:
            fh.write(build_pdf_bytes(1))

        result = build_join_pipeline(
            input_paths=[first_path, second_path],
            final_output_dir=outputs_dir,
            preserve_parity=True,
            generate_cover=True,
            display_names=["uno.pdf", "dos.pdf"],
        )

        with fitz.open(result.output_pdf_path) as doc:
            self.assertEqual(doc.page_count, 5)
            self.assertIn("Document index", doc[0].get_text())
            self.assertIn("*", doc[0].get_text())
            self.assertEqual(doc[1].get_text().strip(), "")
            self.assertIn("Page 1", doc[2].get_text())
            self.assertEqual(doc[3].get_text().strip(), "")
            self.assertIn("Page 1", doc[4].get_text())
