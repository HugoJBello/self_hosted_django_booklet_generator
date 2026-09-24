import os
import tempfile

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import OcrJob


class OcrJobSecurityTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user("ocr-owner")
        self.other = User.objects.create_user("ocr-other")
        descriptor, self.output_path = tempfile.mkstemp(suffix=".pdf")
        os.write(descriptor, b"ocr result")
        os.close(descriptor)
        self.job = OcrJob.objects.create(
            owner=self.owner,
            job_id="private-job",
            original_name="scan.pdf",
            input_path="/tmp/input.pdf",
            output_path=self.output_path,
            status="done",
        )

    def tearDown(self):
        if os.path.exists(self.output_path):
            os.unlink(self.output_path)

    def test_owner_can_read_status_and_download(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("ocrpdf:status", args=[self.job.job_id])).status_code, 200)
        self.assertEqual(self.client.get(reverse("ocrpdf:download", args=[self.job.job_id])).status_code, 200)

    def test_other_user_cannot_read_status_or_download(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("ocrpdf:status", args=[self.job.job_id])).status_code, 404)
        self.assertEqual(self.client.get(reverse("ocrpdf:download", args=[self.job.job_id])).status_code, 404)
