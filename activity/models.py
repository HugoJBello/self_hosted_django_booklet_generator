import uuid

from django.conf import settings
from django.db import models


class Activity(models.Model):
    TOOL_CHOICES = [
        ("booklets", "Booklets"), ("joinpdf", "Join PDFs"), ("splitpdf", "Split PDF"),
        ("ocrpdf", "OCR"), ("diary", "Diary"), ("calendarpdf", "Class calendar"),
    ]
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="pdf_activities")
    tool = models.CharField(max_length=32, choices=TOOL_CHOICES, db_index=True)
    title = models.CharField(max_length=255)
    options = models.JSONField(default=dict, blank=True)
    restore_state = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, default="done", choices=[("queued", "Queued"), ("done", "Done"), ("error", "Error")])
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name_plural = "activities"

    def __str__(self):
        return f"{self.owner}: {self.get_tool_display()} - {self.title}"


class Artifact(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name="artifacts")
    kind = models.CharField(max_length=16, choices=[("input", "Input"), ("output", "Output")])
    name = models.CharField(max_length=255)
    path = models.TextField()
    content_type = models.CharField(max_length=100, default="application/pdf")
    size = models.PositiveBigIntegerField(default=0)

    class Meta:
        ordering = ["kind", "pk"]

    def __str__(self):
        return self.name
