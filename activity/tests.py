import io
import os
import tempfile
import datetime as dt
import fitz
from PIL import Image

from django.contrib.auth import get_user_model
from django.test import TestCase, SimpleTestCase
from django.urls import reverse

from .models import Artifact
from .services import record_activity
from .filenames import generated_pdf_name, portable_stem
from .watermark import stamp_pdf
from booklets.flipped_a4 import build_flipped_a4_booklets_pipeline
from booklets.services import SourcePdfSpec, build_booklets_pipeline


class FooterWatermarkTests(SimpleTestCase):
    def _pdf(self, pages=3):
        handle, path = tempfile.mkstemp(suffix=".pdf")
        os.close(handle)
        document = fitz.open()
        for _ in range(pages):
            page = document.new_page(width=595, height=842)
            page.insert_text((100, 100), "Existing content")
        document.save(path)
        document.close()
        self.addCleanup(lambda: os.unlink(path))
        return path

    def test_regular_pdf_first_and_last_corners_and_bounded_filename(self):
        path = self._pdf()
        stamp_pdf(path, filename="Revisión " + "larguísimo" * 40 + ".pdf",
                  generated_at=dt.datetime(2026, 10, 18, 14, 30, tzinfo=dt.timezone.utc))
        with fitz.open(path) as document:
            self.assertEqual(document.page_count, 3)
            for page in document:
                self.assertEqual(tuple(page.rect), (0, 0, 595, 842))
                self.assertIn("Existing content", page.get_text())
            self.assertNotIn("18/10/26", document[1].get_text())
            for page, right in ((document[0], True), (document[-1], False)):
                footer = [line for block in page.get_text("dict")["blocks"] if "lines" in block
                          for line in block["lines"] if any("18/10/26" in span["text"] or "Revisión" in span["text"]
                                                           for span in line["spans"])]
                self.assertEqual(len(footer), 2)
                self.assertLess(max(line["bbox"][3] for line in footer) - min(line["bbox"][1] for line in footer), 28.35)
                self.assertTrue(all(line["bbox"][0] > 300 for line in footer) if right
                                else all(line["bbox"][2] < 300 for line in footer))
                self.assertTrue(all(line["bbox"][2] < 595 for line in footer))

    def test_rotated_page_footer_remains_at_visual_bottom(self):
        path = self._pdf(pages=1)
        with fitz.open(path) as document:
            document[0].set_rotation(90)
            document.saveIncr()
        stamp_pdf(path)
        with fitz.open(path) as document:
            page = document[0]
            self.assertEqual(page.rotation, 90)
            pixmap = page.get_pixmap(clip=fitz.Rect(0, page.rect.height - 28, page.rect.width, page.rect.height), alpha=False)
            self.assertTrue(any(channel < 180 for channel in pixmap.samples))

    def test_both_booklet_layouts_transform_marked_source_without_mutating_upload(self):
        source_path = self._pdf()
        with tempfile.TemporaryDirectory() as directory:
            spec = SourcePdfSpec(source_path, same_page_parity=True, margin_cm=0.5, add_watermark=False)
            for pipeline in (build_booklets_pipeline, build_flipped_a4_booklets_pipeline):
                result = pipeline(
                    specs=[spec], max_pages_per_split=40, final_output_dir=directory,
                    watermark=True, watermark_filename=True,
                    watermark_source_names=["source-name.pdf"],
                )
                with fitz.open(result.output_pdf_path) as generated:
                    output_text = "".join(page.get_text() for page in generated)
                    self.assertIn("source-name.pdf", output_text)
                with fitz.open(source_path) as original:
                    self.assertNotIn("source-name.pdf", "".join(page.get_text() for page in original))


class GeneratedFilenameTests(TestCase):
    def test_name_is_descriptive_portable_and_bounded(self):
        name = generated_pdf_name(source_names=["Mi informe: revisión?.PDF"], tool="ocrpdf")
        self.assertEqual(name, "mi-informe-revision_ocr.pdf")
        self.assertLessEqual(len(name), 120)

    def test_windows_reserved_and_duplicate_names_are_safe(self):
        self.assertEqual(portable_stem("CON.pdf"), "document")
        used = set()
        first = generated_pdf_name(source_names=["report.pdf"], tool="splitpdf", detail="01 Intro.pdf", index=1, used_names=used)
        second = generated_pdf_name(source_names=["report.pdf"], tool="splitpdf", detail="01 Intro.pdf", index=1, used_names=used)
        self.assertEqual(first, "report_split_01_intro.pdf")
        self.assertEqual(second, "report_split_01_intro_2.pdf")

    def test_activity_storage_path_stays_unique_and_independent_from_display_name(self):
        activity = record_activity(owner=get_user_model().objects.create_user("namer"), tool="splitpdf", title="Split", options={},
            inputs=[{"name": "source.pdf", "path": "/tmp/source.pdf"}],
            outputs=[{"name": "01 Same.pdf", "path": "/tmp/opaque-a.pdf"}, {"name": "01 Same.pdf", "path": "/tmp/opaque-b.pdf"}], generated_names=True)
        artifacts = list(activity.artifacts.filter(kind="output"))
        self.assertEqual([item.name for item in artifacts], ["source_split_01_same.pdf", "source_split_02_same.pdf"])
        self.assertEqual([item.path for item in artifacts], ["/tmp/opaque-a.pdf", "/tmp/opaque-b.pdf"])


class ActivitySecurityTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user("owner")
        self.other = User.objects.create_user("other")
        self.admin = User.objects.create_user("auditor", is_staff=True)
        handle, self.path = tempfile.mkstemp(suffix=".pdf")
        os.write(handle, b"private pdf")
        os.close(handle)
        self.activity = record_activity(
            owner=self.owner, tool="booklets", title="Private run", options={"margin_cm": 1.5},
            outputs=[{"name": "private.pdf", "path": self.path}],
            restore_state={"session_key": "booklets_items", "session_value": [{"path": self.path}], "form_initial": {"margin_cm": 1.5}},
        )
        self.artifact = Artifact.objects.get(activity=self.activity)

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_owner_can_view_and_download_own_activity(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("activity:detail", args=[self.activity.pk])).status_code, 200)
        response = self.client.get(reverse("activity:file", args=[self.artifact.public_id]))
        self.assertEqual(response.status_code, 200)

    def test_other_user_cannot_view_download_or_reopen(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("activity:detail", args=[self.activity.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("activity:file", args=[self.artifact.public_id])).status_code, 404)
        self.assertEqual(self.client.post(reverse("activity:reopen", args=[self.activity.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("activity:outputs", args=[self.activity.pk])).status_code, 404)

    def test_output_selector_returns_only_output_pdfs(self):
        Artifact.objects.create(activity=self.activity, kind="input", name="source.pdf", path=self.path, content_type="application/pdf", size=10)
        Artifact.objects.create(activity=self.activity, kind="output", name="notes.txt", path=self.path, content_type="text/plain", size=10)
        self.client.force_login(self.owner)
        response = self.client.get(reverse("activity:outputs", args=[self.activity.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["name"] for item in response.json()["files"]], ["private.pdf"])

    def test_recent_activity_groups_multiple_outputs(self):
        for number in range(4):
            Artifact.objects.create(activity=self.activity, kind="output", name=f"section-{number}.pdf", path=self.path, content_type="application/pdf", size=10)
        self.client.force_login(self.owner)
        response = self.client.get(reverse("booklets:form"))
        self.assertContains(response, "5 PDFs")
        self.assertContains(response, reverse("activity:outputs", args=[self.activity.pk]))
        self.assertNotContains(response, "Preview section-0.pdf")

    def test_admin_can_audit_everything(self):
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("activity:list")), "Private run")
        self.assertEqual(self.client.get(reverse("activity:detail", args=[self.activity.pk])).status_code, 200)

    def test_non_admin_history_only_contains_own_activity(self):
        record_activity(owner=self.other, tool="booklets", title="Hidden run", options={})
        self.client.force_login(self.owner)
        response = self.client.get(reverse("activity:list"))
        self.assertContains(response, "Private run")
        self.assertNotContains(response, "Hidden run")

    def test_reopen_restores_session_and_form_options(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("activity:reopen", args=[self.activity.pk]))
        self.assertRedirects(response, reverse("booklets:form"), fetch_redirect_response=False)
        session = self.client.session
        self.assertEqual(session["booklets_items"][0]["path"], self.path)
        self.assertEqual(session["activity_initial_booklets"]["margin_cm"], 1.5)

    def test_normal_tool_entry_clears_stale_workspace_but_keeps_history(self):
        self.client.force_login(self.owner)
        session = self.client.session
        session["booklets_items"] = [{"id": "old", "name": "old.pdf", "path": self.path, "size": 11}]
        session.save()

        response = self.client.get(reverse("booklets:form"))

        self.assertNotContains(response, "old.pdf")
        self.assertContains(response, "Private run")
        self.assertNotIn("booklets_items", self.client.session)

    def test_reopened_workspace_survives_target_get_only(self):
        self.client.force_login(self.owner)
        self.client.post(reverse("activity:reopen", args=[self.activity.pk]))

        reopened = self.client.get(reverse("booklets:form"))
        self.assertContains(reopened, os.path.basename(self.path))
        self.assertIn("booklets_items", self.client.session)

        fresh = self.client.get(reverse("booklets:form"))
        self.assertNotContains(fresh, os.path.basename(self.path))
        self.assertNotIn("booklets_items", self.client.session)

    def test_every_tool_shows_restored_inputs_and_results_on_reopen(self):
        routes = {
            "booklets": "booklets:form", "joinpdf": "joinpdf:form", "splitpdf": "splitpdf:form",
            "ocrpdf": "ocrpdf:form", "diary": "diary:form", "calendarpdf": "calendarpdf:form",
        }
        for tool, route in routes.items():
            activity = record_activity(
                owner=self.owner, tool=tool, title=f"Restore {tool}", options={},
                inputs=[{"name": f"{tool}-source.pdf", "path": self.path}],
                outputs=[{"name": f"{tool}-result.pdf", "path": self.path}],
                restore_state={"form_initial": {}},
            )
            self.client.force_login(self.owner)
            self.client.post(reverse("activity:reopen", args=[activity.pk]))
            response = self.client.get(reverse(route))
            self.assertContains(response, "Restored from activity")
            self.assertContains(response, f"{tool}-source.pdf")
            source = activity.artifacts.get(kind="input")
            self.assertContains(response, reverse("activity:file", args=[source.public_id]))
            self.assertContains(response, "1 PDF")

    def test_reopen_requires_post(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("activity:reopen", args=[self.activity.pk])).status_code, 405)

    def test_recent_activity_is_limited_to_current_user(self):
        record_activity(owner=self.other, tool="booklets", title="Other private run", options={})
        self.client.force_login(self.owner)
        response = self.client.get(reverse("booklets:form"))
        self.assertContains(response, "Private run")
        self.assertNotContains(response, "Other private run")


class ActivityListTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("activity-owner")
        self.paths = []

    def tearDown(self):
        for path in self.paths:
            if os.path.exists(path):
                os.unlink(path)

    def make_pdf(self, label):
        handle, path = tempfile.mkstemp(suffix=".pdf")
        os.close(handle)
        with fitz.open() as document:
            page = document.new_page()
            page.insert_text((72, 72), label)
            document.save(path)
        self.paths.append(path)
        return path

    def test_list_is_newest_first_and_paginated(self):
        for number in range(23):
            record_activity(owner=self.owner, tool="joinpdf", title=f"Run {number:02}", options={})
        self.client.force_login(self.owner)

        first_page = self.client.get(reverse("activity:list"))
        second_page = self.client.get(reverse("activity:list"), {"page": 2})

        self.assertEqual(first_page.context["activities"].paginator.count, 23)
        self.assertEqual(first_page.context["activities"].number, 1)
        self.assertEqual(second_page.context["activities"].number, 2)
        first_titles = [item.title for item in first_page.context["activities"]]
        second_titles = [item.title for item in second_page.context["activities"]]
        self.assertEqual(first_titles[0], "Run 22")
        self.assertEqual(first_titles[-1], "Run 03")
        self.assertEqual(second_titles, ["Run 02", "Run 01", "Run 00"])

    def test_recent_tool_strip_is_newest_first(self):
        older = record_activity(owner=self.owner, tool="joinpdf", title="Older", options={})
        newer = record_activity(owner=self.owner, tool="joinpdf", title="Newer", options={})
        self.client.force_login(self.owner)

        response = self.client.get(reverse("joinpdf:form"))

        recent_ids = [item.pk for item in response.context["recent_tool_activities"]]
        self.assertEqual(recent_ids[:2], [newer.pk, older.pk])

    def test_thumbnail_uses_first_output_pdf_and_is_owner_protected(self):
        first_path = self.make_pdf("first output")
        second_path = self.make_pdf("second output")
        activity = record_activity(
            owner=self.owner, tool="splitpdf", title="Several outputs", options={},
            outputs=[{"name": "first.pdf", "path": first_path}, {"name": "second.pdf", "path": second_path}],
        )
        self.client.force_login(self.owner)

        response = self.client.get(reverse("activity:thumbnail", args=[activity.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/jpeg")
        with Image.open(io.BytesIO(response.content)) as thumbnail:
            self.assertLessEqual(thumbnail.width, 220)
            self.assertLessEqual(thumbnail.height, 150)
        self.assertContains(self.client.get(reverse("activity:list")), reverse("activity:thumbnail", args=[activity.pk]))

        other = get_user_model().objects.create_user("activity-other")
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("activity:thumbnail", args=[activity.pk])).status_code, 404)
