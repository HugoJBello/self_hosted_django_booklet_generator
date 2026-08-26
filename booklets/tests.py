from __future__ import annotations

import os
import shutil
import tempfile

import fitz
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import BookletForm
from .flipped_a4 import (
    FLIPPED_A4_QUALITY_PROFILES,
    _cell_draw_rect,
    _clip_half_page,
    _find_half_split_y,
    _imposed_cell_pairs,
    _logical_half_pages_for_prepared_pages,
    build_flipped_a4_booklets_pipeline,
)
from .services import PreparedPage, SourcePdfSpec, build_booklets_pipeline, prepare_pages_for_specs


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

    def test_flipped_a4_checkbox_is_off_by_default(self):
        form = BookletForm()

        self.assertEqual(form.fields["booklet_layout"].initial, "side_by_side")
        self.assertTrue(form.fields["side_by_side_prepare_for_portrait_printing"].initial)
        self.assertFalse(form.fields["flipped_a4"].initial)
        self.assertEqual(form.fields["flipped_a4_quality"].initial, "medium")
        self.assertEqual(form.fields["flipped_a4_split_mode"].initial, "vector")
        self.assertEqual(form.fields["flipped_a4_center_gap_cm"].initial, 1.0)
        self.assertFalse(form.fields["flipped_a4_prepare_for_a5_printing"].initial)
        self.assertIn("Side-by-side booklet", form.as_p())
        self.assertIn("Prepare for portrait printing", form.as_p())
        self.assertIn("Flipped booklet", form.as_p())
        self.assertIn("Rendering quality", form.as_p())
        self.assertIn("Page split method", form.as_p())
        self.assertIn("Middle page separation", form.as_p())
        self.assertIn("Prepare for A5 printing", form.as_p())

    def test_flipped_a4_quality_profiles_preserve_old_low_and_high_as_lower_options(self):
        form = BookletForm()

        self.assertEqual(
            list(form.fields["flipped_a4_quality"].choices),
            [
                ("very_low", "Very low"),
                ("low", "Low"),
                ("medium", "Medium"),
                ("high", "High"),
                ("super_high", "Super high"),
            ],
        )
        self.assertEqual(FLIPPED_A4_QUALITY_PROFILES["very_low"], (2.5, 84))
        self.assertEqual(FLIPPED_A4_QUALITY_PROFILES["low"], (3.5, 88))
        self.assertGreater(FLIPPED_A4_QUALITY_PROFILES["medium"][0], FLIPPED_A4_QUALITY_PROFILES["low"][0])
        self.assertGreater(FLIPPED_A4_QUALITY_PROFILES["high"][0], FLIPPED_A4_QUALITY_PROFILES["medium"][0])
        self.assertGreater(FLIPPED_A4_QUALITY_PROFILES["super_high"][0], FLIPPED_A4_QUALITY_PROFILES["high"][0])

    def test_separate_mode_generates_one_result_per_file(self):
        response = self.client.post(
            reverse("booklets:form"),
            data={
                "input_pdf": [
                    SimpleUploadedFile("uno.pdf", build_pdf_bytes(2), content_type="application/pdf"),
                    SimpleUploadedFile("dos.pdf", build_pdf_bytes(3), content_type="application/pdf"),
                ],
                "processing_mode": "separate",
                "booklet_layout": "side_by_side",
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
        self.assertFalse(any("_flipped_a4_booklets_for_printing.pdf" in name for name in generated_files))

    def test_separate_mode_can_generate_flipped_a4_booklets(self):
        response = self.client.post(
            reverse("booklets:form"),
            data={
                "input_pdf": [
                    SimpleUploadedFile("uno.pdf", build_pdf_bytes(2), content_type="application/pdf"),
                    SimpleUploadedFile("dos.pdf", build_pdf_bytes(1), content_type="application/pdf"),
                ],
                "processing_mode": "separate",
                "booklet_layout": "flipped_a4",
                "max_pages_per_split": "40",
                "preserve_file_parity": "on",
                "flipped_a4_quality": "high",
                "flipped_a4_split_mode": "vector",
                "flipped_a4_center_gap_cm": "1.5",
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
        self.assertTrue(all("_flipped_a4_booklets_for_printing.pdf" in name for name in generated_files))

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
        self.assertContains(response, "Combined print file")

        outputs_dir = os.path.join(TEST_MEDIA_ROOT, "booklets_outputs")
        generated_files = [name for name in os.listdir(outputs_dir) if name.endswith(".pdf")]
        self.assertEqual(len(generated_files), 1)

    def test_uploaded_files_persist_for_regeneration(self):
        first_response = self.client.post(
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

        self.assertEqual(first_response.status_code, 200)
        stored_items = self.client.session.get("booklets_items", [])
        self.assertEqual([item["name"] for item in stored_items], ["uno.pdf", "dos.pdf"])
        self.assertContains(first_response, "uno.pdf")
        self.assertContains(first_response, "uploaded")

        second_response = self.client.post(
            reverse("booklets:form"),
            data={
                "processing_mode": "combined",
                "max_pages_per_split": "20",
                "preserve_file_parity": "on",
                "file_count": "2",
                "file_item_id_0": stored_items[1]["id"],
                "file_new_index_0": "",
                "file_same_page_parity_0": "true",
                "file_margin_0": "1.5",
                "file_add_watermark_0": "false",
                "file_item_id_1": stored_items[0]["id"],
                "file_new_index_1": "",
                "file_same_page_parity_1": "false",
                "file_margin_1": "2.0",
                "file_add_watermark_1": "true",
            },
        )

        self.assertEqual(second_response.status_code, 200)
        updated_items = self.client.session.get("booklets_items", [])
        self.assertEqual([item["name"] for item in updated_items], ["dos.pdf", "uno.pdf"])
        self.assertEqual(updated_items[0]["margin_cm"], 1.5)
        self.assertFalse(updated_items[0]["add_watermark"])
        self.assertFalse(updated_items[1]["same_page_parity"])

    def test_combined_mode_cover_keeps_booklet_sheet_parity(self):
        uploads_dir = os.path.join(TEST_MEDIA_ROOT, "uploads")
        outputs_dir = os.path.join(TEST_MEDIA_ROOT, "booklets_outputs")
        os.makedirs(uploads_dir, exist_ok=True)

        first_path = os.path.join(uploads_dir, "uno.pdf")
        second_path = os.path.join(uploads_dir, "dos.pdf")
        with open(first_path, "wb") as fh:
            fh.write(build_pdf_bytes(1))
        with open(second_path, "wb") as fh:
            fh.write(build_pdf_bytes(1))

        result = build_booklets_pipeline(
            specs=[
                SourcePdfSpec(first_path, same_page_parity=True, margin_cm=1.0, add_watermark=False),
                SourcePdfSpec(second_path, same_page_parity=True, margin_cm=1.0, add_watermark=False),
            ],
            max_pages_per_split=40,
            final_output_dir=outputs_dir,
            preserve_file_parity=True,
            generate_cover=True,
        )

        with fitz.open(result.output_pdf_path) as doc:
            # 4 logical pages (cover, blank, first PDF, blank, second PDF => padded to 8)
            # become 4 imposed booklet sheets.
            self.assertEqual(doc.page_count, 4)

    def test_side_by_side_pipeline_outputs_landscape_by_default(self):
        uploads_dir = os.path.join(TEST_MEDIA_ROOT, "uploads")
        outputs_dir = os.path.join(TEST_MEDIA_ROOT, "booklets_outputs")
        os.makedirs(uploads_dir, exist_ok=True)

        source_path = os.path.join(uploads_dir, "side_by_side_default.pdf")
        with open(source_path, "wb") as fh:
            fh.write(build_pdf_bytes(2))

        result = build_booklets_pipeline(
            specs=[SourcePdfSpec(source_path, same_page_parity=True, margin_cm=0.0, add_watermark=False)],
            max_pages_per_split=40,
            final_output_dir=outputs_dir,
            preserve_file_parity=True,
            generate_cover=False,
        )

        with fitz.open(result.output_pdf_path) as doc:
            self.assertEqual(doc.page_count, 2)
            self.assertEqual(round(doc[0].rect.width), 842)
            self.assertEqual(round(doc[0].rect.height), 595)
            self.assertEqual(doc[0].rotation, 0)

    def test_side_by_side_pipeline_can_prepare_output_for_portrait_printing(self):
        uploads_dir = os.path.join(TEST_MEDIA_ROOT, "uploads")
        outputs_dir = os.path.join(TEST_MEDIA_ROOT, "booklets_outputs")
        os.makedirs(uploads_dir, exist_ok=True)

        source_path = os.path.join(uploads_dir, "side_by_side_portrait.pdf")
        with open(source_path, "wb") as fh:
            fh.write(build_pdf_bytes(2))

        result = build_booklets_pipeline(
            specs=[SourcePdfSpec(source_path, same_page_parity=True, margin_cm=0.0, add_watermark=False)],
            max_pages_per_split=40,
            final_output_dir=outputs_dir,
            preserve_file_parity=True,
            generate_cover=False,
            prepare_for_portrait_printing=True,
        )

        with fitz.open(result.output_pdf_path) as doc:
            self.assertEqual(doc.page_count, 2)
            self.assertEqual(round(doc[0].rect.width), 595)
            self.assertEqual(round(doc[0].rect.height), 842)
            self.assertEqual(doc[0].rotation, 0)

    def test_flipped_a4_pipeline_splits_source_pages_into_half_pages(self):
        uploads_dir = os.path.join(TEST_MEDIA_ROOT, "uploads")
        outputs_dir = os.path.join(TEST_MEDIA_ROOT, "booklets_outputs")
        os.makedirs(uploads_dir, exist_ok=True)

        first_path = os.path.join(uploads_dir, "uno.pdf")
        with open(first_path, "wb") as fh:
            fh.write(build_pdf_bytes(2))

        result = build_flipped_a4_booklets_pipeline(
            specs=[
                SourcePdfSpec(first_path, same_page_parity=True, margin_cm=1.0, add_watermark=False),
            ],
            max_pages_per_split=40,
            final_output_dir=outputs_dir,
            preserve_file_parity=True,
            generate_cover=False,
        )

        self.assertTrue(result.output_pdf_path.endswith("_flipped_a4_booklets_for_printing.pdf"))
        with fitz.open(result.output_pdf_path) as doc:
            # 2 source pages become: blank, 4 half-pages, blank, padded to 8 logical pages.
            self.assertEqual(doc.page_count, 4)
            self.assertEqual(round(doc[0].rect.width), 595)
            self.assertEqual(round(doc[0].rect.height), 842)

    def test_flipped_a4_pipeline_can_prepare_output_for_a5_printing(self):
        uploads_dir = os.path.join(TEST_MEDIA_ROOT, "uploads")
        outputs_dir = os.path.join(TEST_MEDIA_ROOT, "booklets_outputs")
        os.makedirs(uploads_dir, exist_ok=True)

        source_path = os.path.join(uploads_dir, "a5_ready_source.pdf")
        with open(source_path, "wb") as fh:
            fh.write(build_pdf_bytes(1))

        result = build_flipped_a4_booklets_pipeline(
            specs=[SourcePdfSpec(source_path, same_page_parity=True, margin_cm=0.0, add_watermark=False)],
            max_pages_per_split=40,
            final_output_dir=outputs_dir,
            preserve_file_parity=True,
            generate_cover=False,
            prepare_for_a5_printing=True,
        )

        with fitz.open(result.output_pdf_path) as doc:
            self.assertEqual(doc.page_count, 2)
            self.assertEqual(round(doc[0].rect.width), 595)
            self.assertEqual(round(doc[0].rect.height), 842)
            self.assertEqual(doc[0].rotation, 0)
            self.assertFalse(_is_rendered_region_blank(doc[1], top=True))
            self.assertTrue(_is_rendered_region_blank(doc[1], top=False))

    def test_flipped_a4_vector_split_preserves_text_content(self):
        uploads_dir = os.path.join(TEST_MEDIA_ROOT, "uploads")
        outputs_dir = os.path.join(TEST_MEDIA_ROOT, "booklets_outputs")
        os.makedirs(uploads_dir, exist_ok=True)

        source_path = os.path.join(uploads_dir, "vector_source.pdf")
        doc = fitz.open()
        try:
            page = doc.new_page(width=595, height=842)
            page.insert_text((72, 96), "VECTOR TOP TEXT")
            page.insert_text((72, 650), "VECTOR BOTTOM TEXT")
            doc.save(source_path)
        finally:
            doc.close()

        vector_result = build_flipped_a4_booklets_pipeline(
            specs=[SourcePdfSpec(source_path, same_page_parity=True, margin_cm=0.0, add_watermark=False)],
            max_pages_per_split=40,
            final_output_dir=outputs_dir,
            preserve_file_parity=True,
            generate_cover=False,
            split_mode="vector",
        )
        raster_result = build_flipped_a4_booklets_pipeline(
            specs=[SourcePdfSpec(source_path, same_page_parity=True, margin_cm=0.0, add_watermark=False)],
            max_pages_per_split=40,
            final_output_dir=outputs_dir,
            preserve_file_parity=True,
            generate_cover=False,
            split_mode="raster",
        )

        with fitz.open(vector_result.output_pdf_path) as generated:
            text = "\n".join(page.get_text() for page in generated)
            self.assertIn("VECTOR TOP TEXT", text)
            self.assertIn("VECTOR BOTTOM TEXT", text)

        with fitz.open(raster_result.output_pdf_path) as generated:
            text = "\n".join(page.get_text() for page in generated)
            self.assertNotIn("VECTOR TOP TEXT", text)
            self.assertNotIn("VECTOR BOTTOM TEXT", text)

    def test_flipped_a4_vector_split_visually_clips_each_half(self):
        uploads_dir = os.path.join(TEST_MEDIA_ROOT, "uploads")
        outputs_dir = os.path.join(TEST_MEDIA_ROOT, "booklets_outputs")
        os.makedirs(uploads_dir, exist_ok=True)

        source_path = os.path.join(uploads_dir, "vector_marked_halves.pdf")
        doc = fitz.open()
        try:
            page = doc.new_page(width=595, height=842)
            split_y = page.rect.height / 2
            page.draw_rect(fitz.Rect(36, 36, 559, split_y - 36), fill=(0.95, 0.1, 0.1))
            page.draw_rect(fitz.Rect(36, split_y + 36, 559, 806), fill=(0.1, 0.8, 0.1))
            doc.save(source_path)
        finally:
            doc.close()

        result = build_flipped_a4_booklets_pipeline(
            specs=[SourcePdfSpec(source_path, same_page_parity=True, margin_cm=1.0, add_watermark=False)],
            max_pages_per_split=40,
            final_output_dir=outputs_dir,
            preserve_file_parity=True,
            generate_cover=False,
            split_mode="vector",
        )

        with fitz.open(result.output_pdf_path) as generated:
            self.assertEqual(generated.page_count, 2)
            top_rgb = _average_rendered_region_rgb(generated[1], top=True)
            bottom_rgb = _average_rendered_region_rgb(generated[1], top=False)
            self.assertGreater(top_rgb[0], top_rgb[1] + 80)
            self.assertGreater(top_rgb[0], top_rgb[2] + 80)
            self.assertGreater(bottom_rgb[1], bottom_rgb[0] + 80)
            self.assertGreater(bottom_rgb[1], bottom_rgb[2] + 80)

    def test_flipped_a4_vector_split_handles_shifted_mediabox_without_merging_halves(self):
        uploads_dir = os.path.join(TEST_MEDIA_ROOT, "uploads")
        outputs_dir = os.path.join(TEST_MEDIA_ROOT, "booklets_outputs")
        os.makedirs(uploads_dir, exist_ok=True)

        source_path = os.path.join(uploads_dir, "shifted_mediabox_halves.pdf")
        doc = fitz.open()
        try:
            page = doc.new_page(width=595, height=842)
            page.set_mediabox(fitz.Rect(0, -842, 595, 0))
            split_y = page.rect.height / 2
            page.draw_rect(fitz.Rect(36, 36, 559, split_y - 36), fill=(0.95, 0.1, 0.1))
            page.draw_rect(fitz.Rect(36, split_y + 36, 559, 806), fill=(0.1, 0.8, 0.1))
            doc.save(source_path)
        finally:
            doc.close()

        result = build_flipped_a4_booklets_pipeline(
            specs=[SourcePdfSpec(source_path, same_page_parity=True, margin_cm=1.0, add_watermark=False)],
            max_pages_per_split=40,
            final_output_dir=outputs_dir,
            preserve_file_parity=True,
            generate_cover=False,
            split_mode="vector",
        )

        with fitz.open(result.output_pdf_path) as generated:
            self.assertEqual(generated.page_count, 2)
            top_rgb = _average_rendered_region_rgb(generated[1], top=True)
            bottom_rgb = _average_rendered_region_rgb(generated[1], top=False)
            self.assertGreater(top_rgb[0], top_rgb[1] + 80)
            self.assertGreater(top_rgb[0], top_rgb[2] + 80)
            self.assertGreater(bottom_rgb[1], bottom_rgb[0] + 80)
            self.assertGreater(bottom_rgb[1], bottom_rgb[2] + 80)

    def test_flipped_a4_center_gap_changes_rendered_vector_split_spacing(self):
        uploads_dir = os.path.join(TEST_MEDIA_ROOT, "uploads")
        outputs_dir = os.path.join(TEST_MEDIA_ROOT, "booklets_outputs")
        os.makedirs(uploads_dir, exist_ok=True)

        source_path = os.path.join(uploads_dir, "full_bleed_halves.pdf")
        doc = fitz.open()
        try:
            page = doc.new_page(width=595, height=842)
            split_y = page.rect.height / 2
            page.draw_rect(fitz.Rect(0, 0, 595, split_y), fill=(0.95, 0.1, 0.1))
            page.draw_rect(fitz.Rect(0, split_y, 595, 842), fill=(0.1, 0.8, 0.1))
            doc.save(source_path)
        finally:
            doc.close()

        narrow_gap_result = build_flipped_a4_booklets_pipeline(
            specs=[SourcePdfSpec(source_path, same_page_parity=True, margin_cm=0.0, add_watermark=False)],
            max_pages_per_split=40,
            final_output_dir=outputs_dir,
            preserve_file_parity=True,
            generate_cover=False,
            split_mode="vector",
            center_gap_cm=0.0,
        )
        wide_gap_result = build_flipped_a4_booklets_pipeline(
            specs=[SourcePdfSpec(source_path, same_page_parity=True, margin_cm=0.0, add_watermark=False)],
            max_pages_per_split=40,
            final_output_dir=outputs_dir,
            preserve_file_parity=True,
            generate_cover=False,
            split_mode="vector",
            center_gap_cm=2.0,
        )

        with fitz.open(narrow_gap_result.output_pdf_path) as narrow_gap, fitz.open(wide_gap_result.output_pdf_path) as wide_gap:
            narrow_top_bbox = _rendered_non_white_bbox(narrow_gap[1], top=True)
            narrow_bottom_bbox = _rendered_non_white_bbox(narrow_gap[1], top=False)
            wide_top_bbox = _rendered_non_white_bbox(wide_gap[1], top=True)
            wide_bottom_bbox = _rendered_non_white_bbox(wide_gap[1], top=False)
            self.assertIsNotNone(narrow_top_bbox)
            self.assertIsNotNone(narrow_bottom_bbox)
            self.assertIsNotNone(wide_top_bbox)
            self.assertIsNotNone(wide_bottom_bbox)
            assert narrow_top_bbox is not None
            assert narrow_bottom_bbox is not None
            assert wide_top_bbox is not None
            assert wide_bottom_bbox is not None

            narrow_rendered_gap = narrow_bottom_bbox[1] - narrow_top_bbox[3]
            wide_rendered_gap = wide_bottom_bbox[1] - wide_top_bbox[3]
            self.assertGreater(wide_rendered_gap, narrow_rendered_gap + 35)

    def test_flipped_a4_cover_is_added_before_imposition(self):
        uploads_dir = os.path.join(TEST_MEDIA_ROOT, "uploads")
        outputs_dir = os.path.join(TEST_MEDIA_ROOT, "booklets_outputs")
        os.makedirs(uploads_dir, exist_ok=True)

        source_path = os.path.join(uploads_dir, "with_cover.pdf")
        with open(source_path, "wb") as fh:
            fh.write(build_pdf_bytes(1))

        with_cover = build_flipped_a4_booklets_pipeline(
            specs=[SourcePdfSpec(source_path, same_page_parity=True, margin_cm=0.0, add_watermark=False)],
            max_pages_per_split=40,
            final_output_dir=outputs_dir,
            preserve_file_parity=False,
            generate_cover=True,
        )
        without_cover = build_flipped_a4_booklets_pipeline(
            specs=[SourcePdfSpec(source_path, same_page_parity=True, margin_cm=0.0, add_watermark=False)],
            max_pages_per_split=40,
            final_output_dir=outputs_dir,
            preserve_file_parity=False,
            generate_cover=False,
        )

        with fitz.open(with_cover.output_pdf_path) as doc:
            self.assertEqual(doc.page_count, 4)
        with fitz.open(without_cover.output_pdf_path) as doc:
            self.assertEqual(doc.page_count, 2)

    def test_flipped_a4_combined_mode_preserves_file_parity_before_imposition(self):
        uploads_dir = os.path.join(TEST_MEDIA_ROOT, "uploads")
        os.makedirs(uploads_dir, exist_ok=True)

        first_path = os.path.join(uploads_dir, "first.pdf")
        second_path = os.path.join(uploads_dir, "second.pdf")
        with open(first_path, "wb") as fh:
            fh.write(build_pdf_bytes(1))
        with open(second_path, "wb") as fh:
            fh.write(build_pdf_bytes(1))

        prepared = prepare_pages_for_specs(
            [
                SourcePdfSpec(first_path, same_page_parity=True, margin_cm=0.0, add_watermark=False),
                SourcePdfSpec(second_path, same_page_parity=True, margin_cm=0.0, add_watermark=False),
            ],
            preserve_file_parity=True,
        )
        pairs = _imposed_cell_pairs(prepared)

        self.assertEqual(len(prepared), 3)
        self.assertTrue(prepared[1].is_blank)
        self.assertEqual(len(pairs), 4)
        self.assertEqual(pairs[-1][0].half_page.prepared_page.source_pdf_path, None)
        self.assertEqual(pairs[-1][1].half_page.prepared_page.source_pdf_path, None)

    def test_flipped_a4_split_limit_applies_before_each_flipped_imposition(self):
        uploads_dir = os.path.join(TEST_MEDIA_ROOT, "uploads")
        outputs_dir = os.path.join(TEST_MEDIA_ROOT, "booklets_outputs")
        os.makedirs(uploads_dir, exist_ok=True)

        source_path = os.path.join(uploads_dir, "large.pdf")
        with open(source_path, "wb") as fh:
            fh.write(build_pdf_bytes(5))

        result = build_flipped_a4_booklets_pipeline(
            specs=[SourcePdfSpec(source_path, same_page_parity=True, margin_cm=0.0, add_watermark=False)],
            max_pages_per_split=2,
            final_output_dir=outputs_dir,
            preserve_file_parity=True,
            generate_cover=False,
        )

        with fitz.open(result.output_pdf_path) as doc:
            # 5 prepared pages split as 2 + 2 + 1 source pages. Flipped imposition
            # gives 4 + 4 + 2 output pages.
            self.assertEqual(doc.page_count, 10)

    def test_flipped_a4_watermark_is_added_after_imposition(self):
        uploads_dir = os.path.join(TEST_MEDIA_ROOT, "uploads")
        outputs_dir = os.path.join(TEST_MEDIA_ROOT, "booklets_outputs")
        os.makedirs(uploads_dir, exist_ok=True)

        source_path = os.path.join(uploads_dir, "watermarked.pdf")
        with open(source_path, "wb") as fh:
            fh.write(build_pdf_bytes(1))

        result = build_flipped_a4_booklets_pipeline(
            specs=[SourcePdfSpec(source_path, same_page_parity=True, margin_cm=0.0, add_watermark=True)],
            max_pages_per_split=40,
            final_output_dir=outputs_dir,
            preserve_file_parity=True,
            generate_cover=False,
        )

        with fitz.open(result.output_pdf_path) as doc:
            self.assertEqual(doc.page_count, 2)
            self.assertNotIn("*", doc[0].get_text())
            self.assertIn("*", doc[1].get_text())

    def test_flipped_a4_logical_order_wraps_halves_with_blank_cover_pages(self):
        prepared = [
            PreparedPage("one.pdf", 0, 595, 842, 1.0, False),
            PreparedPage("two.pdf", 0, 595, 842, 1.0, False),
        ]

        half_pages = _logical_half_pages_for_prepared_pages(prepared)

        self.assertTrue(half_pages[0].is_blank)
        self.assertEqual(
            [(half.prepared_page.source_pdf_path, half.half) for half in half_pages[1:-1]],
            [
                ("one.pdf", "top"),
                ("one.pdf", "bottom"),
                ("two.pdf", "top"),
                ("two.pdf", "bottom"),
            ],
        )
        self.assertTrue(half_pages[-1].is_blank)

    def test_flipped_a4_clip_cuts_exact_halves_without_overlap(self):
        doc = fitz.open()
        try:
            page = doc.new_page(width=595, height=842)
            page.insert_text((72, 96), "Top content.")
            page.insert_text((72, 421), "This line sits on the physical half-page cut.")
            page.insert_text((72, 650), "Bottom content.")

            top_clip = _clip_half_page(page, "top")
            bottom_clip = _clip_half_page(page, "bottom")

            self.assertEqual(top_clip.y1, bottom_clip.y0)
            self.assertEqual(top_clip.y1, 421)
            self.assertEqual(top_clip.y0, 0)
            self.assertEqual(bottom_clip.y1, 842)
        finally:
            doc.close()

    def test_flipped_a4_split_is_exact_center_even_when_content_crosses_cut(self):
        doc = fitz.open()
        try:
            page = doc.new_page(width=595, height=842)
            page.insert_text((72, 96), "Top content.")
            page.insert_text((72, 421), "This can be cut.")
            page.insert_text((72, 650), "Bottom content.")

            split_y = _find_half_split_y(page)

            self.assertEqual(split_y, 421)
        finally:
            doc.close()

    def test_flipped_a4_draw_area_uses_one_cm_center_gap_and_small_outer_margin(self):
        page_width = 595
        page_height = 842
        center_y = page_height / 2

        top_rect = _cell_draw_rect(0, 0, page_width, center_y, outer_margin_pts=0, fold_edge="bottom")
        bottom_rect = _cell_draw_rect(0, center_y, page_width, page_height, outer_margin_pts=0, fold_edge="top")

        self.assertAlmostEqual(bottom_rect.y0 - top_rect.y1, 72 / 2.54, delta=0.5)
        self.assertLess(top_rect.x0, 5)
        self.assertLess(top_rect.y0, 5)
        self.assertLess(page_width - top_rect.x1, 5)
        self.assertLess(page_height - bottom_rect.y1, 5)

        wider_top_rect = _cell_draw_rect(0, 0, page_width, center_y, outer_margin_pts=0, fold_edge="bottom", center_gap_cm=1.8)
        wider_bottom_rect = _cell_draw_rect(0, center_y, page_width, page_height, outer_margin_pts=0, fold_edge="top", center_gap_cm=1.8)
        self.assertAlmostEqual(wider_bottom_rect.y0 - wider_top_rect.y1, 1.8 * 72 / 2.54, delta=0.5)

    def test_flipped_a4_three_page_imposition_matches_provided_algorithm(self):
        prepared = [
            PreparedPage("page1.pdf", 0, 595, 842, 1.0, False),
            PreparedPage("page2.pdf", 0, 595, 842, 1.0, False),
            PreparedPage("page3.pdf", 0, 595, 842, 1.0, False),
        ]
        pairs = _imposed_cell_pairs(prepared)

        printable_pairs = [
            (
                None if left.is_blank else (left.half_page.prepared_page.source_pdf_path, left.half_page.half, left.rotate_180),
                None if right.is_blank else (right.half_page.prepared_page.source_pdf_path, right.half_page.half, right.rotate_180),
            )
            for left, right in pairs
        ]

        self.assertEqual(
            printable_pairs,
            [
                (None, None),
                (("page1.pdf", "top", False), ("page3.pdf", "bottom", False)),
                (("page1.pdf", "bottom", True), ("page3.pdf", "top", True)),
                (("page2.pdf", "top", False), ("page2.pdf", "bottom", False)),
            ],
        )

    def test_flipped_a4_a5_printing_imposition_swaps_even_half_pages_without_rotation(self):
        prepared = [
            PreparedPage("page1.pdf", 0, 595, 842, 1.0, False),
            PreparedPage("page2.pdf", 0, 595, 842, 1.0, False),
            PreparedPage("page3.pdf", 0, 595, 842, 1.0, False),
        ]
        pairs = _imposed_cell_pairs(
            prepared,
            rotate_even_half_pages=False,
            swap_even_half_pages=True,
        )

        printable_pairs = [
            (
                None if left.is_blank else (left.half_page.prepared_page.source_pdf_path, left.half_page.half, left.rotate_180),
                None if right.is_blank else (right.half_page.prepared_page.source_pdf_path, right.half_page.half, right.rotate_180),
            )
            for left, right in pairs
        ]

        self.assertEqual(
            printable_pairs,
            [
                (None, None),
                (("page1.pdf", "top", False), ("page3.pdf", "bottom", False)),
                (("page3.pdf", "top", False), ("page1.pdf", "bottom", False)),
                (("page2.pdf", "top", False), ("page2.pdf", "bottom", False)),
            ],
        )

    def test_flipped_a4_five_page_imposition_matches_provided_algorithm(self):
        prepared = [
            PreparedPage(f"page{page_number}.pdf", 0, 595, 842, 1.0, False)
            for page_number in range(1, 6)
        ]
        pairs = _imposed_cell_pairs(prepared)

        printable_pairs = [
            (
                None if left.is_blank else (left.half_page.prepared_page.source_pdf_path, left.half_page.half, left.rotate_180),
                None if right.is_blank else (right.half_page.prepared_page.source_pdf_path, right.half_page.half, right.rotate_180),
            )
            for left, right in pairs
        ]

        self.assertEqual(
            printable_pairs,
            [
                (None, None),
                (("page1.pdf", "top", False), ("page5.pdf", "bottom", False)),
                (("page1.pdf", "bottom", True), ("page5.pdf", "top", True)),
                (("page2.pdf", "top", False), ("page4.pdf", "bottom", False)),
                (("page2.pdf", "bottom", True), ("page4.pdf", "top", True)),
                (("page3.pdf", "top", False), ("page3.pdf", "bottom", False)),
            ],
        )

    def test_flipped_a4_three_page_dummy_pdf_matches_expected_imposition(self):
        uploads_dir = os.path.join(TEST_MEDIA_ROOT, "uploads")
        outputs_dir = os.path.join(TEST_MEDIA_ROOT, "booklets_outputs")
        os.makedirs(uploads_dir, exist_ok=True)
        source_path = os.path.join(uploads_dir, "three_marked_pages.pdf")

        doc = fitz.open()
        try:
            half_fills = {
                (1, "top"): (0.78, 0.86, 0.96),
                (1, "bottom"): (0.96, 0.82, 0.70),
                (2, "top"): (0.74, 0.96, 0.78),
                (2, "bottom"): (0.92, 0.88, 0.62),
                (3, "top"): (0.86, 0.80, 0.96),
                (3, "bottom"): (0.96, 0.76, 0.82),
            }
            for page_number in range(1, 4):
                page = doc.new_page(width=595, height=842)
                split_y = page.rect.height / 2
                page.draw_rect(
                    fitz.Rect(36, 36, 559, split_y - 36),
                    color=(0, 0, 0),
                    fill=half_fills[(page_number, "top")],
                    width=2,
                )
                page.draw_rect(
                    fitz.Rect(36, split_y + 36, 559, 806),
                    color=(0, 0, 0),
                    fill=half_fills[(page_number, "bottom")],
                    width=2,
                )
                page.insert_textbox(
                    fitz.Rect(60, 120, 535, 260),
                    f"PAGE {page_number} TOP ONLY",
                    fontsize=32,
                    align=fitz.TEXT_ALIGN_CENTER,
                )
                page.insert_textbox(
                    fitz.Rect(60, split_y + 120, 535, split_y + 260),
                    f"PAGE {page_number} BOTTOM ONLY",
                    fontsize=32,
                    align=fitz.TEXT_ALIGN_CENTER,
                )
                page.insert_textbox(
                    fitz.Rect(60, split_y - 15, 535, split_y + 15),
                    f"CUT-LINE-{page_number}",
                    fontsize=18,
                    align=fitz.TEXT_ALIGN_CENTER,
                    color=(1, 0, 0),
                )
                page.draw_line((0, split_y), (595, split_y), color=(1, 0, 0), width=2)
            doc.save(source_path)
        finally:
            doc.close()

        result = build_flipped_a4_booklets_pipeline(
            specs=[SourcePdfSpec(source_path, same_page_parity=True, margin_cm=1.0, add_watermark=False)],
            max_pages_per_split=40,
            final_output_dir=outputs_dir,
            preserve_file_parity=True,
            generate_cover=False,
        )

        with fitz.open(result.output_pdf_path) as generated:
            self.assertEqual(generated.page_count, 4)
            self.assertTrue(_is_rendered_region_blank(generated[0], top=True))
            self.assertTrue(_is_rendered_region_blank(generated[0], top=False))
            for page_number in [1, 2, 3]:
                self.assertFalse(_is_rendered_region_blank(generated[page_number], top=True))
                self.assertFalse(_is_rendered_region_blank(generated[page_number], top=False))
                top_bbox = _rendered_non_white_bbox(generated[page_number], top=True)
                bottom_bbox = _rendered_non_white_bbox(generated[page_number], top=False)
                self.assertIsNotNone(top_bbox)
                self.assertIsNotNone(bottom_bbox)
                assert top_bbox is not None
                assert bottom_bbox is not None
                self.assertLess(top_bbox[3], bottom_bbox[1])
                self.assertLess(abs((top_bbox[3] - top_bbox[1]) - (bottom_bbox[3] - bottom_bbox[1])), 4)

            expected_regions = {
                (1, True): (1, "top"),
                (1, False): (3, "bottom"),
                (2, True): (1, "bottom"),
                (2, False): (3, "top"),
                (3, True): (2, "top"),
                (3, False): (2, "bottom"),
            }
            palette = {
                key: tuple(round(channel * 255) for channel in fill)
                for key, fill in half_fills.items()
            }
            for page_index, top in expected_regions:
                counts = _region_palette_counts(generated[page_index], top=top, palette=palette)
                expected_key = expected_regions[(page_index, top)]
                total = sum(counts.values())
                self.assertGreater(total, 1_000)
                self.assertGreater(counts[expected_key] / total, 0.90)
                for key, count in counts.items():
                    if key != expected_key:
                        self.assertLess(count / total, 0.03)

        output_size = os.path.getsize(result.output_pdf_path)
        self.assertLess(output_size, 1_000_000)

def _is_rendered_region_blank(page: fitz.Page, top: bool) -> bool:
    rect = fitz.Rect(page.rect)
    if top:
        rect.y1 = page.rect.height / 2
    else:
        rect.y0 = page.rect.height / 2

    pixmap = page.get_pixmap(matrix=fitz.Matrix(0.5, 0.5), clip=rect, alpha=False)
    samples = pixmap.samples
    non_white = 0
    for idx in range(0, len(samples), pixmap.n):
        if any(channel < 245 for channel in samples[idx : idx + 3]):
            non_white += 1
            if non_white > 25:
                return False
    return True


def _rendered_non_white_bbox(page: fitz.Page, top: bool) -> tuple[int, int, int, int] | None:
    rect = fitz.Rect(page.rect)
    if top:
        rect.y1 = page.rect.height / 2
    else:
        rect.y0 = page.rect.height / 2

    pixmap = page.get_pixmap(matrix=fitz.Matrix(1, 1), clip=rect, alpha=False)
    min_x = pixmap.w
    min_y = pixmap.h
    max_x = -1
    max_y = -1
    samples = pixmap.samples
    for y in range(pixmap.h):
        row_offset = y * pixmap.stride
        for x in range(pixmap.w):
            offset = row_offset + x * pixmap.n
            if any(channel < 245 for channel in samples[offset : offset + 3]):
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)

    if max_x < 0:
        return None

    y_offset = 0 if top else int(page.rect.height / 2)
    return min_x, min_y + y_offset, max_x, max_y + y_offset


def _region_palette_counts(
    page: fitz.Page,
    top: bool,
    palette: dict[tuple[int, str], tuple[int, int, int]],
) -> dict[tuple[int, str], int]:
    rect = fitz.Rect(page.rect)
    if top:
        rect.y1 = page.rect.height / 2
    else:
        rect.y0 = page.rect.height / 2

    pixmap = page.get_pixmap(matrix=fitz.Matrix(1, 1), clip=rect, alpha=False)
    counts = {key: 0 for key in palette}
    samples = pixmap.samples
    for y in range(pixmap.h):
        row_offset = y * pixmap.stride
        for x in range(pixmap.w):
            offset = row_offset + x * pixmap.n
            rgb = tuple(samples[offset : offset + 3])
            if max(rgb) > 248 or max(rgb) - min(rgb) < 15:
                continue
            if rgb[0] > 180 and rgb[1] < 80 and rgb[2] < 80:
                continue

            nearest_key = None
            nearest_distance = float("inf")
            for key, color in palette.items():
                distance = sum((rgb[idx] - color[idx]) ** 2 for idx in range(3))
                if distance < nearest_distance:
                    nearest_distance = distance
                    nearest_key = key
            if nearest_key is not None and nearest_distance < 7_500:
                counts[nearest_key] += 1

    return counts


def _average_rendered_region_rgb(page: fitz.Page, top: bool) -> tuple[int, int, int]:
    rect = fitz.Rect(page.rect)
    if top:
        rect.y1 = page.rect.height / 2
    else:
        rect.y0 = page.rect.height / 2

    pixmap = page.get_pixmap(matrix=fitz.Matrix(0.2, 0.2), clip=rect, alpha=False)
    totals = [0, 0, 0]
    count = 0
    for idx in range(0, len(pixmap.samples), pixmap.n):
        totals[0] += pixmap.samples[idx]
        totals[1] += pixmap.samples[idx + 1]
        totals[2] += pixmap.samples[idx + 2]
        count += 1

    return tuple(round(total / count) for total in totals)
