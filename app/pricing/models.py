from django.db import models


class AdjustmentApplyTo(models.TextChoices):
    TOTAL = "TOTAL", "Total"
    MATERIAL = "MATERIAL", "Material"
    LABOR = "LABOR", "Labor"


class PriceIndex(models.Model):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=120)
    source = models.CharField(max_length=120, default="FGV/IBRE")

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"


class PriceIndexValue(models.Model):
    price_index = models.ForeignKey(
        "pricing.PriceIndex", on_delete=models.CASCADE, related_name="values"
    )
    ref_month = models.DateField()
    value = models.DecimalField(max_digits=14, decimal_places=6)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["price_index", "ref_month"], name="uniq_price_index_ref_month"
            ),
        ]
        ordering = ["price_index_id", "ref_month"]

    def __str__(self) -> str:
        return f"{self.price_index.code} - {self.ref_month:%Y-%m}"


class ProjectPriceAdjustment(models.Model):
    project = models.ForeignKey(
        "core.Project", on_delete=models.CASCADE, related_name="price_adjustments"
    )
    price_index = models.ForeignKey(
        "pricing.PriceIndex", on_delete=models.PROTECT, related_name="project_adjustments"
    )
    base_month = models.DateField()
    apply_to = models.CharField(
        max_length=10,
        choices=AdjustmentApplyTo.choices,
        default=AdjustmentApplyTo.TOTAL,
    )
    is_active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return f"{self.project.name} - {self.price_index.code}"
