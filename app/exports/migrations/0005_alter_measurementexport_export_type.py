# Generated manually for RDO xlsx exports.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("exports", "0004_measurementexport_summary_json"),
    ]

    operations = [
        migrations.AlterField(
            model_name="measurementexport",
            name="export_type",
            field=models.CharField(
                choices=[
                    ("SIENGE_MASTER", "Sienge master"),
                    ("SIENGE_SNAPSHOT", "Sienge snapshot"),
                    ("DOCX_TIMBRADO", "Docx timbrado"),
                    ("PDF_TIMBRADO", "Pdf timbrado"),
                    ("XLSX_BOLETIM", "Xlsx boletim"),
                    ("RDO_XLSX", "Rdo xlsx"),
                ],
                max_length=20,
            ),
        ),
    ]
