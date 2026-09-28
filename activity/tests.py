import os
import tempfile

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Artifact
from .services import record_activity
from .filenames import generated_pdf_name, portable_stem


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
