import os
import tempfile

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import Artifact
from .services import record_activity


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

    def test_reopen_requires_post(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("activity:reopen", args=[self.activity.pk])).status_code, 405)

    def test_recent_activity_is_limited_to_current_user(self):
        record_activity(owner=self.other, tool="booklets", title="Other private run", options={})
        self.client.force_login(self.owner)
        response = self.client.get(reverse("booklets:form"))
        self.assertContains(response, "Private run")
        self.assertNotContains(response, "Other private run")
