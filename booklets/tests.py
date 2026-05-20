from __future__ import annotations

import os
import shutil
import tempfile

import fitz
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .services import SourcePdfSpec, prepare_pages_for_specs


def build_pdf_bytes(page_count: int) -> bytes:
    doc = fitz.open()
    for idx in range(page_count):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {idx + 1}")
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="booklets_test_media_")


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class BookletsViewTests(TestCase):
    def setUp(self):
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)
        os.makedirs(TEST_MEDIA_ROOT, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def test_prepare_pages_for_specs_inserts_blank_to_preserve_even_start(self):
        tmpdir = tempfile.mkdtemp(prefix="booklets_specs_")
        try:
            first_path = os.path.join(tmpdir, "first.pdf")
            second_path = os.path.join(tmpdir, "second.pdf")

            with open(first_path, "wb") as fh:
                fh.write(build_pdf_bytes(1))
            with open(second_path, "wb") as fh:
                fh.write(build_pdf_bytes(2))

            specs = [
                SourcePdfSpec(first_path, same_page_parity=True, margin_cm=1.0, add_watermark=True),
                SourcePdfSpec(second_path, same_page_parity=True, margin_cm=2.0, add_watermark=False),
            ]

            prepared = prepare_pages_for_specs(specs, preserve_file_parity=True)

            self.assertEqual(len(prepared), 4)
            self.assertTrue(prepared[1].is_blank)
            self.assertEqual(prepared[2].source_pdf_path, second_path)
            self.assertEqual(prepared[2].margin_cm, 2.0)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_prepare_pages_for_specs_skips_blank_when_not_preserving(self):
        tmpdir = tempfile.mkdtemp(prefix="booklets_specs_")
        try:
            first_path = os.path.join(tmpdir, "first.pdf")
            second_path = os.path.join(tmpdir, "second.pdf")

            with open(first_path, "wb") as fh:
                fh.write(build_pdf_bytes(1))
            with open(second_path, "wb") as fh:
                fh.write(build_pdf_bytes(2))

            specs = [
                SourcePdfSpec(first_path, same_page_parity=True, margin_cm=1.0, add_watermark=True),
                SourcePdfSpec(second_path, same_page_parity=False, margin_cm=2.0, add_watermark=False),
            ]

            prepared = prepare_pages_for_specs(specs, preserve_file_parity=False)

            self.assertEqual(len(prepared), 3)
            self.assertFalse(any(page.is_blank for page in prepared))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_separate_mode_generates_one_result_per_file(self):
        response = self.client.post(
            reverse("booklets:form"),
            data={
                "input_pdf": [
                    SimpleUploadedFile("uno.pdf", build_pdf_bytes(2), content_type="application/pdf"),
                    SimpleUploadedFile("dos.pdf", build_pdf_bytes(3), content_type="application/pdf"),
                ],
                "processing_mode": "separate",
                "max_pages_per_split": "40",
                "preserve_file_parity": "on",
                "file_same_page_parity_0": "true",
                "file_margin_0": "1.0",
                "file_add_watermark_0": "true",
                "file_same_page_parity_1": "false",
                "file_margin_1": "1.5",
                "file_add_watermark_1": "false",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "uno.pdf")
        self.assertContains(response, "dos.pdf")

        outputs_dir = os.path.join(TEST_MEDIA_ROOT, "booklets_outputs")
        generated_files = [name for name in os.listdir(outputs_dir) if name.endswith(".pdf")]
        self.assertEqual(len(generated_files), 2)

    def test_combined_mode_generates_single_result(self):
        response = self.client.post(
            reverse("booklets:form"),
            data={
                "input_pdf": [
                    SimpleUploadedFile("uno.pdf", build_pdf_bytes(2), content_type="application/pdf"),
                    SimpleUploadedFile("dos.pdf", build_pdf_bytes(3), content_type="application/pdf"),
                ],
                "processing_mode": "combined",
                "max_pages_per_split": "40",
                "preserve_file_parity": "on",
                "file_same_page_parity_0": "true",
                "file_margin_0": "1.0",
                "file_add_watermark_0": "true",
                "file_same_page_parity_1": "true",
                "file_margin_1": "1.0",
                "file_add_watermark_1": "true",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Impresión unificada")

        outputs_dir = os.path.join(TEST_MEDIA_ROOT, "booklets_outputs")
        generated_files = [name for name in os.listdir(outputs_dir) if name.endswith(".pdf")]
        self.assertEqual(len(generated_files), 1)
