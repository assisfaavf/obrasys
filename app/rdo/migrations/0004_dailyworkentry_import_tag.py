from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("rdo", "0003_remove_dailyworkteamentry_contractor_name_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="dailyworkactivityentry",
            name="import_tag",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="dailyworkmaterialentry",
            name="import_tag",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="dailyworkoccurrence",
            name="import_tag",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="dailyworkteamentry",
            name="import_tag",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
    ]
