import os
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import fitz
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse

from .forms import SplitPdfForm
from .services import (
    SplitPdfJobOptions,
    SplitSection,
    build_sections_for_level,
    extract_toc,
    section_filename,
    split_pdf_by_sections,
)


class SplitPdfServiceTests(SimpleTestCase):
    def test_split_pdf_job_options_default_margin_matches_existing_default(self):
        self.assertEqual(SplitPdfJobOptions().margin_cm, 1.0)

    def test_build_sections_for_selected_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_path = os.path.join(tmp, "source.pdf")
            _create_pdf_with_toc(source_path)
            toc_entries, total_pages = extract_toc(source_path)

            sections = build_sections_for_level(toc_entries, total_pages, selected_level=2)

            self.assertEqual([section.title for section in sections], ["Scope", "Details", "Results"])
            self.assertEqual([(section.start_page, section.end_page) for section in sections], [(2, 2), (3, 4), (5, 5)])

    def test_section_filename_is_short_and_unix_safe(self):
        filename = section_filename(3, "Capítulo: very/long? title " * 12)

        self.assertTrue(filename.startswith("03_"))
        self.assertTrue(filename.endswith(".pdf"))
        self.assertLessEqual(len(filename), 79)
        self.assertNotIn("/", filename)
        self.assertNotIn("?", filename)

    def test_split_pdf_by_sections_writes_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_path = os.path.join(tmp, "source.pdf")
            output_dir = os.path.join(tmp, "outputs")
            _create_pdf_with_toc(source_path)
            toc_entries, total_pages = extract_toc(source_path)
            sections = build_sections_for_level(toc_entries, total_pages, selected_level=1)

            outputs = split_pdf_by_sections(
                source_path,
                sections,
                output_dir,
                SplitPdfJobOptions(apply_booklets=False),
            )

            self.assertEqual(len(outputs), 2)
            for output in outputs:
                self.assertTrue(os.path.isfile(output.path))
                with fitz.open(output.path) as doc:
                    self.assertEqual(len(doc), output.page_count)

    def test_booklet_margin_option_is_passed_to_booklet_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_path = os.path.join(tmp, "source.pdf")
            output_dir = os.path.join(tmp, "outputs")
            fake_booklet_path = os.path.join(tmp, "fake_booklet.pdf")
            _create_pdf_with_toc(source_path)
            with open(fake_booklet_path, "wb") as fake:
                fake.write(b"%PDF-1.4\n%%EOF\n")

            section = SplitSection(
                index=1,
                level=1,
                title="Chapter One",
                start_page=1,
                end_page=1,
                filename="01_Chapter_One.pdf",
            )

            with patch("splitpdf.services.build_booklets_pipeline") as pipeline:
                pipeline.return_value = SimpleNamespace(job_id="fake", output_pdf_path=fake_booklet_path)
                split_pdf_by_sections(
                    source_path,
                    [section],
                    output_dir,
                    SplitPdfJobOptions(apply_booklets=True, margin_cm=2.25),
                )

            specs = pipeline.call_args.kwargs["specs"]
            self.assertEqual(specs[0].margin_cm, 2.25)


class SplitPdfFormTests(SimpleTestCase):
    def test_form_exposes_shared_margin_default(self):
        form = SplitPdfForm(level_choices=[("1", "Level 1")])

        self.assertEqual(form.fields["margin_cm"].initial, 1.0)
        self.assertIn("Outer margin (cm)", form.as_p())


class SplitPdfViewTests(TestCase):
    def test_form_renders(self):
        response = Client().get(reverse("splitpdf:form"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Split PDF by sections")

    def test_upload_without_action_falls_back_to_detect(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_path = os.path.join(tmp, "source.pdf")
            _create_pdf_with_toc(source_path)
            with open(source_path, "rb") as source:
                upload = SimpleUploadedFile("source.pdf", source.read(), content_type="application/pdf")

        response = Client().post(reverse("splitpdf:form"), {"input_pdf": upload})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Table of contents detected")


def _create_pdf_with_toc(path: str) -> None:
    doc = fitz.open()
    for page_number in range(1, 6):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {page_number}", fontsize=16)
    doc.set_toc(
        [
            [1, "Chapter One", 1],
            [2, "Scope", 2],
            [2, "Details", 3],
            [1, "Chapter Two", 5],
            [2, "Results", 5],
        ]
    )
    doc.save(path)
    doc.close()
