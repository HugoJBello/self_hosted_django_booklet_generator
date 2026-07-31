# ocrpdf/models.py
from __future__ import annotations

from django.db import models


class OcrJob(models.Model):
    STATUS_CHOICES = [
        ("queued", "Queued"),
        ("running", "Running"),
        ("done", "Done"),
        ("error", "Error"),
    ]

    job_id = models.CharField(max_length=64, unique=True)
    original_name = models.CharField(max_length=255, blank=True, default="")
    input_path = models.TextField()
    output_path = models.TextField(blank=True, default="")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="queued")
    error_message = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Opcional: guardar parámetros usados
    language = models.CharField(max_length=64, blank=True, default="spa")
    optimize = models.IntegerField(default=2)
    deskew = models.BooleanField(default=True)
    rotate_pages = models.BooleanField(default=True)
    force_ocr = models.BooleanField(default=False)

    def __str__(self) -> str:
        return f"{self.job_id} ({self.status})"

