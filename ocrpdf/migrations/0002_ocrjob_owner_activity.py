import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("activity", "0001_initial"), migrations.swappable_dependency(settings.AUTH_USER_MODEL), ("ocrpdf", "0001_initial")]
    operations = [
        migrations.AddField(model_name="ocrjob", name="owner", field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name="ocr_jobs", to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name="ocrjob", name="activity", field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name="ocr_jobs", to="activity.activity")),
    ]
