from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("stock", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="material",
            name="brand",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
    ]
