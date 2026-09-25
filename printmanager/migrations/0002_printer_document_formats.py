from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("printmanager", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="printer",
            name="document_formats",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
