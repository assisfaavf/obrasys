# Generated manually for safe migration from free-text disciplines to catalog records.

import django.db.models.deletion
from django.db import migrations, models


def migrate_discipline_text_to_fk(apps, schema_editor):
    BudgetItem = apps.get_model("catalog", "BudgetItem")
    Discipline = apps.get_model("catalog", "Discipline")

    for item in BudgetItem.objects.exclude(discipline_text__isnull=True).exclude(discipline_text=""):
        name = (item.discipline_text or "").strip()
        if not name:
            continue
        discipline, _ = Discipline.objects.get_or_create(
            name=name,
            defaults={"code": "", "is_active": True},
        )
        item.discipline = discipline
        item.save(update_fields=["discipline"])


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0004_budgetitem_discipline"),
    ]

    operations = [
        migrations.CreateModel(
            name="Discipline",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, unique=True)),
                ("code", models.CharField(blank=True, default="", max_length=40)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["name"],
            },
        ),
        migrations.RenameField(
            model_name="budgetitem",
            old_name="discipline",
            new_name="discipline_text",
        ),
        migrations.AddField(
            model_name="budgetitem",
            name="discipline",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="budget_items",
                to="catalog.discipline",
            ),
        ),
        migrations.RunPython(migrate_discipline_text_to_fk, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="budgetitem",
            name="discipline_text",
        ),
    ]
