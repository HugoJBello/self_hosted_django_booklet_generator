from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("printmanager", "0002_printer_document_formats")]

    operations = [
        migrations.AddField(
            model_name="printer",
            name="preferred_document_format",
            field=models.CharField(blank=True, max_length=127),
        ),
    ]
