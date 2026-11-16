from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("printmanager", "0005_printjob_tracking"),
    ]

    operations = [
        migrations.AddField(
            model_name="printer",
            name="document_format_override",
            field=models.CharField(
                blank=True,
                choices=[
                    ("application/pdf", "PDF"),
                    ("image/pwg-raster", "PWG Raster"),
                    ("image/urf", "URF"),
                ],
                help_text="Optional. Use when a printer rejects its advertised preferred format.",
                max_length=127,
            ),
        ),
    ]
