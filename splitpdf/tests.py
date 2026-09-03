import os
import tempfile
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

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
    merge_adjacent_sections,
    section_filename,
    split_section_for_preview,
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

    def test_single_section_is_split_by_source_page_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_path = os.path.join(tmp, "source.pdf")
            _create_pdf_with_toc(source_path)
            toc_entries, total_pages = extract_toc(source_path)
            chapter = build_sections_for_level(toc_entries, total_pages, selected_level=1)[0]

            children = split_section_for_preview(chapter)

            self.assertEqual([child.title for child in children], ["Chapter One part 1", "Chapter One part 2"])
            self.assertEqual([(child.start_page, child.end_page) for child in children], [(1, 2), (3, 4)])

    def test_adjacent_sections_can_be_merged_and_renamed(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_path = os.path.join(tmp, "source.pdf")
            _create_pdf_with_toc(source_path)
            toc_entries, total_pages = extract_toc(source_path)
            first, second = build_sections_for_level(toc_entries, total_pages, selected_level=1)

            merged = merge_adjacent_sections(first, second)

            self.assertEqual(merged.title, "Chapter One + Chapter Two")
            self.assertEqual((merged.start_page, merged.end_page), (1, 5))
            self.assertEqual(merged.toc_indexes, (*first.toc_indexes, *second.toc_indexes))
            self.assertEqual([part.title for part in merged.included_parts], ["Chapter One", "Chapter Two"])

    def test_merged_section_split_restores_merged_parts(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_path = os.path.join(tmp, "source.pdf")
            _create_pdf_with_toc(source_path)
            toc_entries, total_pages = extract_toc(source_path)
            first, second = build_sections_for_level(toc_entries, total_pages, selected_level=1)
            merged = merge_adjacent_sections(first, second)

            children = split_section_for_preview(merged)

            self.assertEqual([child.title for child in children], ["Chapter One", "Chapter Two"])
            self.assertEqual([(child.start_page, child.end_page) for child in children], [(1, 4), (5, 5)])

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
                    SplitPdfJobOptions(
                        apply_booklets=True,
                        margin_cm=2.25,
                        preserve_file_parity=False,
                        same_page_parity=False,
                    ),
                )

            specs = pipeline.call_args.kwargs["specs"]
            self.assertEqual(specs[0].margin_cm, 2.25)
            self.assertFalse(specs[0].same_page_parity)
            self.assertFalse(pipeline.call_args.kwargs["preserve_file_parity"])


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

    def test_preview_section_can_be_split_from_view(self):
        client = Client()
        with tempfile.TemporaryDirectory() as tmp:
            source_path = os.path.join(tmp, "source.pdf")
            _create_pdf_with_toc(source_path)
            with open(source_path, "rb") as source:
                upload = SimpleUploadedFile("source.pdf", source.read(), content_type="application/pdf")

        detect_response = client.post(reverse("splitpdf:form"), {"input_pdf": upload})
        self.assertEqual(detect_response.status_code, 200)
        state = client.session["splitpdf_state"]
        first_section_id = state["preview_sections"][0]["section_id"]

        response = client.post(
            reverse("splitpdf:form"),
            {
                "action": "split_section",
                "section_id": first_section_id,
                "selected_level": "1",
            },
        )

        self.assertEqual(response.status_code, 200)
        preview_titles = [section["title"] for section in client.session["splitpdf_state"]["preview_sections"]]
        self.assertEqual(preview_titles, ["Chapter One part 1", "Chapter One part 2", "Chapter Two"])

    def test_merged_preview_section_can_be_split_back_apart_from_view(self):
        client = Client()
        with tempfile.TemporaryDirectory() as tmp:
            source_path = os.path.join(tmp, "source.pdf")
            _create_pdf_with_toc(source_path)
            with open(source_path, "rb") as source:
                upload = SimpleUploadedFile("source.pdf", source.read(), content_type="application/pdf")

        detect_response = client.post(reverse("splitpdf:form"), {"input_pdf": upload})
        self.assertEqual(detect_response.status_code, 200)
        first_section_id = client.session["splitpdf_state"]["preview_sections"][0]["section_id"]

        merge_response = client.post(
            reverse("splitpdf:form"),
            {
                "action": "merge_next",
                "section_id": first_section_id,
                "selected_level": "1",
            },
        )
        self.assertEqual(merge_response.status_code, 200)
        merged_section_id = client.session["splitpdf_state"]["preview_sections"][0]["section_id"]

        response = client.post(
            reverse("splitpdf:form"),
            {
                "action": "split_section",
                "section_id": merged_section_id,
                "selected_level": "1",
            },
        )

        self.assertEqual(response.status_code, 200)
        preview_titles = [section["title"] for section in client.session["splitpdf_state"]["preview_sections"]]
        self.assertEqual(preview_titles, ["Chapter One", "Chapter Two"])

    def test_preview_sections_can_be_merged_from_view(self):
        client = Client()
        with tempfile.TemporaryDirectory() as tmp:
            source_path = os.path.join(tmp, "source.pdf")
            _create_pdf_with_toc(source_path)
            with open(source_path, "rb") as source:
                upload = SimpleUploadedFile("source.pdf", source.read(), content_type="application/pdf")

        detect_response = client.post(reverse("splitpdf:form"), {"input_pdf": upload})
        self.assertEqual(detect_response.status_code, 200)
        state = client.session["splitpdf_state"]
        first_section_id = state["preview_sections"][0]["section_id"]

        response = client.post(
            reverse("splitpdf:form"),
            {
                "action": "merge_next",
                "section_id": first_section_id,
                "selected_level": "1",
            },
        )

        self.assertEqual(response.status_code, 200)
        preview = client.session["splitpdf_state"]["preview_sections"]
        self.assertEqual(len(preview), 1)
        self.assertEqual(preview[0]["title"], "Chapter One + Chapter Two")

    def test_download_all_returns_zip_with_generated_pdfs(self):
        client = Client()
        with tempfile.TemporaryDirectory() as tmp:
            first_path = os.path.join(tmp, "first.pdf")
            second_path = os.path.join(tmp, "second.pdf")
            _create_pdf(first_path, page_count=1)
            _create_pdf(second_path, page_count=1)

            session = client.session
            session["splitpdf_state"] = {
                "pdf_name": "source.pdf",
                "outputs": [
                    {"path": first_path, "filename": "01_intro.pdf"},
                    {"path": second_path, "filename": "02_body.pdf"},
                ],
            }
            session.save()

            response = client.get(reverse("splitpdf:download_all"))

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], "application/zip")
            payload = b"".join(response.streaming_content)
            with ZipFile(BytesIO(payload)) as archive:
                self.assertEqual(sorted(archive.namelist()), ["01_intro.pdf", "02_body.pdf"])

    def test_download_all_requires_generated_pdfs(self):
        response = Client().get(reverse("splitpdf:download_all"))

        self.assertEqual(response.status_code, 404)


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


def _create_pdf(path: str, page_count: int) -> None:
    doc = fitz.open()
    for page_number in range(1, page_count + 1):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {page_number}", fontsize=16)
    doc.save(path)
    doc.close()
