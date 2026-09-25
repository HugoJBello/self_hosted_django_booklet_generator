from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("printmanager", "0003_printer_preferred_document_format")]

    operations = [
        migrations.AddField(model_name="printer", name="capability_validation", field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name="printer", name="certified_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="printjob", name="effective_options", field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name="printjob", name="transport", field=models.CharField(blank=True, max_length=32)),
    ]
