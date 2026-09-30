from datetime import date
import io
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch

import fitz
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from calendarpdf.services import Event
from .class_overlay import _single_blocks, add_classes_to_diary
from .forms import DiaryForm
from .services import generate_diary_pdf


class DiaryClassTests(TestCase):
    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user("diary-test"))

    def test_timetable_upload_is_optional(self):
        form = DiaryForm()
        self.assertFalse(form.fields["class_timetables"].required)
        self.assertEqual(form.fields["class_timetables"].clean(None), [])

    def test_out_of_range_upload_reports_error(self):
        file = SimpleUploadedFile("schedule.png", b"test", content_type="image/png")
        data = {
            "start_date": "2026-09-14", "number_of_weeks": "1",
            "calendar_mode": "single", "output_mode": "pdf",
            "max_pages_per_split": "40", "content_margin_cm": "0.5",
            "class_timetables": [file],
        }
        event = Event(date(2026, 10, 1), "08:00", "Physics")
        with patch("diary.views.extract_uploaded_timetables", return_value={event}):
            response = self.client.post("/pdf_manager/diary/", data)
        self.assertContains(response, "No uploaded classes fall within the selected diary weeks")

    def test_diary_subject_filter_reports_no_matches(self):
        file = SimpleUploadedFile("schedule.png", b"test", content_type="image/png")
        data = {
            "start_date": "2026-09-14", "number_of_weeks": "1",
            "calendar_mode": "single", "output_mode": "pdf",
            "max_pages_per_split": "40", "content_margin_cm": "0.5",
            "class_timetables": [file], "filter_subjects": "on", "subject_filter": "physics",
        }
        event = Event(date(2026, 9, 14), "08:00", "Mathematics")
        with patch("diary.views.extract_uploaded_timetables", return_value={event}):
            response = self.client.post("/pdf_manager/diary/", data)
        self.assertContains(response, "No subjects matched the active subject filter")

    def test_diary_reuses_uploaded_timetables_after_filter_edit(self):
        first = Event(date(2026, 9, 14), "10:00", "Mathematics")
        second = Event(date(2026, 9, 15), "11:00", "Physics")
        image = io.BytesIO()
        Image.new("RGB", (24, 18), "white").save(image, format="PNG")
        image_bytes = image.getvalue()
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            output_path = Path(media_root) / "diary_outputs" / "diary.pdf"
            output_path.parent.mkdir(parents=True)
            output_path.write_bytes(b"diary pdf")
            files = [
                SimpleUploadedFile("first.png", image_bytes, content_type="image/png"),
                SimpleUploadedFile("second.png", image_bytes, content_type="image/png"),
            ]
            pipeline_result = SimpleNamespace(output_pdf_path=str(output_path))
            with patch("diary.views.extract_uploaded_timetables", return_value={first, second}) as extract, \
                    patch("diary.views.build_diary_pipeline", return_value=pipeline_result) as build:
                data = {
                    "start_date": "2026-09-14", "number_of_weeks": "1",
                    "calendar_mode": "single", "output_mode": "pdf",
                    "max_pages_per_split": "40", "content_margin_cm": "0.5",
                    "class_timetables": files,
                }
                response = self.client.post("/pdf_manager/diary/", data)
                self.assertEqual(response.status_code, 200)
                uploads = response.context["staged_uploads"]
                self.assertEqual([item["name"] for item in uploads], ["first.png", "second.png"])
                self.assertEqual(build.call_args.kwargs["class_events"], {first, second})

                response = self.client.post("/pdf_manager/diary/", {
                    "start_date": "2026-09-14", "number_of_weeks": "1",
                    "calendar_mode": "single", "output_mode": "pdf",
                    "max_pages_per_split": "40", "content_margin_cm": "0.5",
                    "timetable_upload_ids": [item["id"] for item in uploads],
                    "filter_subjects": "on", "subject_filter": "physics",
                })
                self.assertEqual(response.status_code, 200)
                self.assertEqual(extract.call_count, 2)
                self.assertEqual([file.name for file in extract.call_args.args[0]], ["first.png", "second.png"])
                self.assertEqual(build.call_args.kwargs["class_events"], {second})
                self.assertContains(response, "Physics")

    @skipUnless(shutil.which("pdflatex"), "requires pdflatex")
    def test_single_week_keeps_all_page_and_box_dimensions(self):
        events = {
            Event(date(2026, 9, 7), "08:00", "Statistics", "1T", "Room 07", "09:00"),
            Event(date(2026, 9, 7), "10:00", "Mathematics I", "1A", "Room 14", "11:00"),
            Event(date(2026, 9, 12), "09:00", "Workshop", "1P", "Room 3", "10:00"),
            Event(date(2026, 9, 13), "11:00", "Tutorial", "1T", "Room 4", "12:00"),
        }
        with tempfile.TemporaryDirectory() as tmp:
            baseline = generate_diary_pdf(date(2026, 9, 7), 1, "single", tmp)
            annotated = generate_diary_pdf(date(2026, 9, 7), 1, "single", tmp, class_events=events)
            with fitz.open(baseline.output_pdf_path) as plain, fitz.open(annotated.output_pdf_path) as filled:
                self.assertEqual(plain.page_count, filled.page_count)
                self.assertEqual([page.rect for page in plain], [page.rect for page in filled])
                self.assertEqual(_single_blocks(plain[-1]), _single_blocks(filled[-1]))
                self.assertNotIn("Statistics", plain[-1].get_text())
                self.assertIn("Statistics", filled[-1].get_text())
                self.assertIn("Room 14", filled[-1].get_text())
                self.assertIn("8h → 9h", filled[-1].get_text())
                self.assertIn("Workshop", filled[-1].get_text())
                self.assertIn("Tutorial", filled[-1].get_text())
                self.assertNotIn("Statistics", filled[1].get_text())
                self.assertNotIn("Consolidated weekly timetable", "".join(page.get_text() for page in filled))
            crowded = {Event(date(2026, 9, 7), f"{hour:02d}:00", f"Class {hour}", end_time="23:00")
                       for hour in range(20)}
            with self.assertRaisesRegex(ValueError, "Too many classes"):
                add_classes_to_diary(baseline.output_pdf_path, str(Path(tmp) / "crowded-single.pdf"),
                                     crowded, date(2026, 9, 7), 1, "single")

    @skipUnless(shutil.which("pdflatex"), "requires pdflatex")
    def test_legacy_grid_keeps_dimensions_and_rejects_overflow(self):
        event = Event(date(2026, 9, 10), "09:00", "Physics", "1L", "Laboratory 2", "10:00")
        with tempfile.TemporaryDirectory() as tmp:
            baseline = generate_diary_pdf(date(2026, 9, 7), 1, "double", tmp)
            output = str(Path(tmp) / "annotated.pdf")
            add_classes_to_diary(baseline.output_pdf_path, output, {event}, date(2026, 9, 7), 1, "double")
            with fitz.open(baseline.output_pdf_path) as plain, fitz.open(output) as filled:
                self.assertEqual([page.rect for page in plain], [page.rect for page in filled])
                self.assertIn("Physics", filled[-1].get_text())
                self.assertIn("9h → 10h", filled[-1].get_text())
            crowded = {Event(date(2026, 9, 10), "09:00", f"Class {number}", end_time="10:00")
                       for number in range(5)}
            with self.assertRaisesRegex(ValueError, "Too many classes"):
                add_classes_to_diary(baseline.output_pdf_path, str(Path(tmp) / "crowded.pdf"),
                                     crowded, date(2026, 9, 7), 1, "double")
