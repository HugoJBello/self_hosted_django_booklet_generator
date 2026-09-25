from django.conf import settings
from django.db import models


class Printer(models.Model):
    name = models.SlugField(max_length=127, unique=True, help_text="CUPS queue name")
    description = models.CharField(max_length=255, blank=True)
    location = models.CharField(max_length=255, blank=True)
    device_uri = models.CharField(max_length=500)
    driver = models.CharField(max_length=500, default="everywhere", help_text="CUPS model/driver, e.g. everywhere")
    is_enabled = models.BooleanField(default=True)
    is_shared = models.BooleanField(default=False)
    is_default = models.BooleanField(default=False)
    default_options = models.JSONField(default=dict, blank=True)
    supported_options = models.JSONField(default=dict, blank=True)
    document_formats = models.JSONField(default=list, blank=True)
    preferred_document_format = models.CharField(max_length=127, blank=True)
    capability_validation = models.JSONField(default=dict, blank=True)
    certified_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.description or self.name


class PrintJob(models.Model):
    STATUS_CHOICES = [("submitted", "Submitted"), ("error", "Error")]
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="print_jobs")
    printer = models.ForeignKey(Printer, on_delete=models.PROTECT, related_name="jobs")
    document_name = models.CharField(max_length=255)
    document_path = models.TextField()
    options = models.JSONField(default=dict)
    effective_options = models.JSONField(default=dict, blank=True)
    transport = models.CharField(max_length=32, blank=True)
    cups_job_id = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
