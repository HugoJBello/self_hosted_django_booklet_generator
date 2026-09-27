from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("printmanager", "0004_printer_certification_and_job_effective_options")]
    operations = [
        migrations.AddField(model_name="printjob", name="job_uri", field=models.CharField(blank=True, max_length=500)),
        migrations.AddField(model_name="printjob", name="status_detail", field=models.TextField(blank=True)),
        migrations.AddField(model_name="printjob", name="status_data", field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name="printjob", name="started_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="printjob", name="completed_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="printjob", name="last_checked_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AlterField(model_name="printjob", name="status", field=models.CharField(choices=[("queued", "Queued"), ("processing", "Printing"), ("completed", "Completed"), ("canceled", "Canceled"), ("error", "Error"), ("submitted", "Submitted")], max_length=16)),
    ]
