import re

from django.db import migrations


MANUFACTURER_SUFFIX_RE = re.compile(
    r"(?iu)\s*(?:[-–—,:;/]\s*|\(\s*)?(?:FAB(?:RICANTE)?|MARCA)\.?\s*[:\-]?\s*[^()\[\]]*(?:\)|\])?\s*$"
)


def strip_manufacturer_hint(description: str) -> str:
    text = str(description or "").strip()
    if not text:
        return ""

    previous = None
    while text and text != previous:
        previous = text
        text = MANUFACTURER_SUFFIX_RE.sub("", text).rstrip(" -–—,:;/(").strip()

    return text


def cleanup_budget_item_descriptions(apps, schema_editor):
    BudgetItem = apps.get_model("catalog", "BudgetItem")

    for item in BudgetItem.objects.all().only("id", "description"):
        cleaned = strip_manufacturer_hint(item.description)
        if cleaned != item.description:
            BudgetItem.objects.filter(pk=item.pk).update(description=cleaned)


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0005_discipline_model_and_budgetitem_fk"),
    ]

    operations = [
        migrations.RunPython(cleanup_budget_item_descriptions, migrations.RunPython.noop),
    ]
