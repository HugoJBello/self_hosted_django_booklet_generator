import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(name="Activity", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("tool", models.CharField(choices=[("booklets", "Booklets"), ("joinpdf", "Join PDFs"), ("splitpdf", "Split PDF"), ("ocrpdf", "OCR"), ("diary", "Diary"), ("calendarpdf", "Class calendar")], db_index=True, max_length=32)),
            ("title", models.CharField(max_length=255)), ("options", models.JSONField(blank=True, default=dict)),
            ("restore_state", models.JSONField(blank=True, default=dict)),
            ("status", models.CharField(choices=[("queued", "Queued"), ("done", "Done"), ("error", "Error")], default="done", max_length=16)),
            ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
            ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="pdf_activities", to=settings.AUTH_USER_MODEL)),
        ], options={"ordering": ["-created_at", "-pk"], "verbose_name_plural": "activities"}),
        migrations.CreateModel(name="Artifact", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
            ("kind", models.CharField(choices=[("input", "Input"), ("output", "Output")], max_length=16)),
            ("name", models.CharField(max_length=255)), ("path", models.TextField()),
            ("content_type", models.CharField(default="application/pdf", max_length=100)), ("size", models.PositiveBigIntegerField(default=0)),
            ("activity", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="artifacts", to="activity.activity")),
        ], options={"ordering": ["kind", "pk"]}),
    ]
