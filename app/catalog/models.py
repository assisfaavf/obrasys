from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from catalog.utils import strip_manufacturer_hint


class Unit(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=80)

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"


class Discipline(models.Model):
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=40, blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        if self.code:
            return f"{self.code} - {self.name}"
        return self.name


class BudgetItem(models.Model):
    project = models.ForeignKey("core.Project", on_delete=models.CASCADE, related_name="budget_items")
    eap_code = models.CharField(max_length=40)
    description = models.CharField(max_length=255)
    unit = models.ForeignKey("catalog.Unit", on_delete=models.PROTECT, related_name="budget_items")
    qty_contracted = models.DecimalField(max_digits=14, decimal_places=3)
    pu_material = models.DecimalField(max_digits=14, decimal_places=4, default=Decimal("0"))
    pu_labor = models.DecimalField(max_digits=14, decimal_places=4, default=Decimal("0"))
    discipline = models.ForeignKey(
        "catalog.Discipline",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="budget_items",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project", "eap_code"], name="uniq_budget_item_project_eap"),
        ]
        ordering = ["project_id", "eap_code"]

    def save(self, *args, **kwargs):
        self.description = strip_manufacturer_hint(self.description)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.eap_code} — {self.description} [{self.unit.code}]"


class BudgetItemAdditionalMaterial(models.Model):
    parent_item = models.ForeignKey(
        "catalog.BudgetItem",
        on_delete=models.CASCADE,
        related_name="additional_materials",
    )
    additional_item = models.ForeignKey(
        "catalog.BudgetItem",
        on_delete=models.CASCADE,
        related_name="used_as_additional_material_in",
    )
    quantity_per_unit = models.DecimalField(max_digits=14, decimal_places=4)
    note = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["parent_item", "additional_item"],
                name="uniq_budget_additional_material",
            ),
        ]
        ordering = ["parent_item_id", "additional_item__eap_code", "id"]

    def __str__(self) -> str:
        return f"{self.parent_item} -> {self.additional_item}"

    def clean(self):
        super().clean()

        if not self.parent_item_id or not self.additional_item_id:
            return

        if self.parent_item_id == self.additional_item_id:
            raise ValidationError("parent_item e additional_item nao podem ser o mesmo item.")

        if self.quantity_per_unit is None or self.quantity_per_unit <= 0:
            raise ValidationError("quantity_per_unit deve ser > 0.")

        if self.parent_item.project_id != self.additional_item.project_id:
            raise ValidationError("Os materiais adicionais devem pertencer ao mesmo projeto.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class BudgetImportMode(models.TextChoices):
    CREATE_ONLY = "CREATE_ONLY", "Create only"
    UPSERT_BY_EAP = "UPSERT_BY_EAP", "Upsert by EAP"
    REPLACE_ALL = "REPLACE_ALL", "Replace all"


class BudgetImportJobStatus(models.TextChoices):
    PREVIEW = "PREVIEW", "Preview"
    APPLIED = "APPLIED", "Applied"
    ERROR = "ERROR", "Error"


class BudgetImportJob(models.Model):
    project = models.ForeignKey(
        "core.Project", on_delete=models.CASCADE, related_name="budget_import_jobs"
    )
    mode = models.CharField(
        max_length=20,
        choices=BudgetImportMode.choices,
        default=BudgetImportMode.UPSERT_BY_EAP,
    )
    original_filename = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="budget_import_jobs",
    )
    status = models.CharField(
        max_length=10,
        choices=BudgetImportJobStatus.choices,
        default=BudgetImportJobStatus.PREVIEW,
    )
    summary_json = models.JSONField(default=dict, blank=True)
    preview_json = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "id"]

    def __str__(self) -> str:
        return f"{self.project.name} - {self.original_filename}"
