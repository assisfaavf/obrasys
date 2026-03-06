from decimal import Decimal

from django.db import models


class Unit(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=80)

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"


class BudgetItem(models.Model):
    project = models.ForeignKey("core.Project", on_delete=models.CASCADE, related_name="budget_items")
    eap_code = models.CharField(max_length=40)
    description = models.CharField(max_length=255)
    unit = models.ForeignKey("catalog.Unit", on_delete=models.PROTECT, related_name="budget_items")
    qty_contracted = models.DecimalField(max_digits=14, decimal_places=3)
    pu_material = models.DecimalField(max_digits=14, decimal_places=4, default=Decimal("0"))
    pu_labor = models.DecimalField(max_digits=14, decimal_places=4, default=Decimal("0"))
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project", "eap_code"], name="uniq_budget_item_project_eap"),
        ]
        ordering = ["project_id", "eap_code"]

    def __str__(self) -> str:
        return f"{self.project.name} - {self.eap_code}"
