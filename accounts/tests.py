from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse


class AuthenticationTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        self.admin = self.User.objects.create_user("manager", password="A-strong-password-123", is_staff=True)
        self.user = self.User.objects.create_user("reader", password="A-strong-password-123")

    def test_tools_require_login(self):
        response = self.client.get(reverse("booklets:form"))
        self.assertRedirects(response, f"{reverse('accounts:login')}?next={reverse('booklets:form')}")

    def test_login_and_logout(self):
        response = self.client.post(reverse("accounts:login"), {"username": "reader", "password": "A-strong-password-123"})
        self.assertRedirects(response, reverse("accounts:home"))
        self.assertEqual(self.client.get(reverse("accounts:home")).status_code, 200)
        self.assertRedirects(self.client.post(reverse("accounts:logout")), reverse("accounts:login"))

    def test_normal_user_cannot_manage_users(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("accounts:user_list")).status_code, 403)

    def test_admin_can_create_and_reset_user_password(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("accounts:user_create"), {"username": "new-user", "password1": "Another-pass-123!", "password2": "Another-pass-123!"})
        self.assertRedirects(response, reverse("accounts:user_list"))
        created = self.User.objects.get(username="new-user")
        response = self.client.post(reverse("accounts:user_password", args=[created.pk]), {"new_password1": "Changed-pass-456!", "new_password2": "Changed-pass-456!"})
        self.assertRedirects(response, reverse("accounts:user_list"))
        created.refresh_from_db()
        self.assertTrue(created.check_password("Changed-pass-456!"))

    def test_delete_is_post_only_and_admin_cannot_delete_self(self):
        self.client.force_login(self.admin)
        url = reverse("accounts:user_delete", args=[self.admin.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.post(url)
        self.assertTrue(self.User.objects.filter(pk=self.admin.pk).exists())


class EnsureAdminTests(TestCase):
    def test_creates_admin_then_preserves_existing_password(self):
        call_command("ensure_admin", stdout=StringIO())
        admin = get_user_model().objects.get(username="admin")
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)
        self.assertTrue(admin.check_password("change_me"))
        admin.set_password("kept-password")
        admin.save()
        call_command("ensure_admin", stdout=StringIO())
        admin.refresh_from_db()
        self.assertTrue(admin.check_password("kept-password"))
