from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("activity", "0002_activity_printdocuments")]
    operations = [
        migrations.AlterField(
            model_name="activity",
            name="tool",
            field=models.CharField(
                choices=[
                    ("booklets", "Booklets"), ("joinpdf", "Join PDFs"),
                    ("splitpdf", "Split PDF"), ("ocrpdf", "OCR"),
                    ("diary", "Diary"), ("calendarpdf", "Class calendar"),
                    ("printdocuments", "Print documents"),
                    ("documentconverter", "Convert to PDF"),
                ],
                db_index=True, max_length=32,
            ),
        ),
    ]
