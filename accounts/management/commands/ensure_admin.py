import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "Create the initial administrator if it does not already exist."

    @transaction.atomic
    def handle(self, *args, **options):
        User = get_user_model()
        username = os.environ.get("DJANGO_INITIAL_ADMIN_USERNAME", "admin")
        password = os.environ.get("DJANGO_INITIAL_ADMIN_PASSWORD", "change_me")
        user, created = User.objects.get_or_create(
            username=username,
            defaults={"is_staff": True, "is_superuser": True, "is_active": True},
        )
        if created:
            user.set_password(password)
            user.save(update_fields=["password"])
            self.stdout.write(self.style.SUCCESS(f"Initial administrator '{username}' created."))
        else:
            fields_to_update = []
            for field in ("is_staff", "is_superuser", "is_active"):
                if not getattr(user, field):
                    setattr(user, field, True)
                    fields_to_update.append(field)
            if fields_to_update:
                user.save(update_fields=fields_to_update)
            self.stdout.write(f"Administrator bootstrap skipped: user '{username}' already exists (password unchanged).")
