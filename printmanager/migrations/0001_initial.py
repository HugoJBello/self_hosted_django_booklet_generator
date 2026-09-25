import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(
            name="Printer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.SlugField(help_text="CUPS queue name", max_length=127, unique=True)),
                ("description", models.CharField(blank=True, max_length=255)),
                ("location", models.CharField(blank=True, max_length=255)),
                ("device_uri", models.CharField(max_length=500)),
                ("driver", models.CharField(default="everywhere", help_text="CUPS model/driver, e.g. everywhere", max_length=500)),
                ("is_enabled", models.BooleanField(default=True)),
                ("is_shared", models.BooleanField(default=False)),
                ("is_default", models.BooleanField(default=False)),
                ("default_options", models.JSONField(blank=True, default=dict)),
                ("supported_options", models.JSONField(blank=True, default=dict)),
                ("last_synced_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ], options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="PrintJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("document_name", models.CharField(max_length=255)),
                ("document_path", models.TextField()),
                ("options", models.JSONField(default=dict)),
                ("cups_job_id", models.CharField(blank=True, max_length=255)),
                ("status", models.CharField(choices=[("submitted", "Submitted"), ("error", "Error")], max_length=16)),
                ("error_message", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="print_jobs", to=settings.AUTH_USER_MODEL)),
                ("printer", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="jobs", to="printmanager.printer")),
            ], options={"ordering": ["-created_at"]},
        ),
    ]
