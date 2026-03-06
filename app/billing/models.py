from decimal import Decimal

from django.db import models


class WorkflowStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    FINALIZED = "FINALIZED", "Finalized"
    SENT = "SENT", "Sent"
    IN_REVIEW = "IN_REVIEW", "In review"
    AUTHORIZED = "AUTHORIZED", "Authorized"
    REJECTED = "REJECTED", "Rejected"
    CANCELLED = "CANCELLED", "Cancelled"


class FinancialStatus(models.TextChoices):
    OPEN = "OPEN", "Open"
    PARTIALLY_PAID = "PARTIALLY_PAID", "Partially paid"
    PAID = "PAID", "Paid"
    OVERDUE = "OVERDUE", "Overdue"


class MeasurementLineKind(models.TextChoices):
    CONTRACTED = "CONTRACTED", "Contracted"
    EXTRA = "EXTRA", "Extra"


class SettlementMethod(models.TextChoices):
    CASH = "CASH", "Cash"
    PIX = "PIX", "Pix"
    TRANSFER = "TRANSFER", "Transfer"
    BOLETO = "BOLETO", "Boleto"
    DESCONTO_BALAO = "DESCONTO_BALAO", "Desconto balao"
    DESCONTO_PARCELA = "DESCONTO_PARCELA", "Desconto parcela"


class SettlementStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    VOID = "VOID", "Void"


class MeasurementPeriod(models.Model):
    project = models.ForeignKey("core.Project", on_delete=models.CASCADE, related_name="measurement_periods")
    number = models.IntegerField()
    ref_month = models.DateField()
    start_date = models.DateField()
    end_date = models.DateField()
    workflow_status = models.CharField(
        max_length=20,
        choices=WorkflowStatus.choices,
        default=WorkflowStatus.DRAFT,
    )
    financial_status = models.CharField(
        max_length=20,
        choices=FinancialStatus.choices,
        default=FinancialStatus.OPEN,
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finalized_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    authorized_at = models.DateTimeField(null=True, blank=True)
    total_material_snapshot = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0")
    )
    total_labor_snapshot = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0")
    )
    total_total_snapshot = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0")
    )
    total_indexed_snapshot = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0")
    )
    index_code_snapshot = models.CharField(max_length=30, blank=True)
    index_base_month_snapshot = models.DateField(null=True, blank=True)
    index_ref_month_snapshot = models.DateField(null=True, blank=True)
    index_factor_snapshot = models.DecimalField(
        max_digits=14, decimal_places=6, default=Decimal("1.0")
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project", "number"], name="uniq_measurement_project_number"),
        ]
        ordering = ["project_id", "number"]

    def __str__(self) -> str:
        return f"{self.project.name} - M{self.number}"


class MeasurementLine(models.Model):
    period = models.ForeignKey(
        "billing.MeasurementPeriod", on_delete=models.CASCADE, related_name="lines"
    )
    location = models.ForeignKey(
        "core.ProjectLocation",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="measurement_lines",
    )
    line_kind = models.CharField(max_length=20, choices=MeasurementLineKind.choices)
    item = models.ForeignKey(
        "catalog.BudgetItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="measurement_lines",
    )
    extra_description = models.CharField(max_length=255, blank=True)
    extra_unit = models.ForeignKey(
        "catalog.Unit",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="extra_measurement_lines",
    )
    extra_pu_material = models.DecimalField(max_digits=14, decimal_places=4, default=Decimal("0"))
    extra_pu_labor = models.DecimalField(max_digits=14, decimal_places=4, default=Decimal("0"))
    qty_period = models.DecimalField(max_digits=14, decimal_places=3)
    justification = models.TextField(blank=True)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["period", "item"], name="idx_measure_line_period_item"),
        ]

    def __str__(self) -> str:
        return f"{self.period} - {self.line_kind}"


class MeasurementSettlement(models.Model):
    period = models.ForeignKey(
        "billing.MeasurementPeriod", on_delete=models.CASCADE, related_name="settlements"
    )
    event_date = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    method = models.CharField(max_length=20, choices=SettlementMethod.choices)
    reference = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(
        max_length=10,
        choices=SettlementStatus.choices,
        default=SettlementStatus.ACTIVE,
    )
    void_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["period_id", "event_date", "id"]

    def __str__(self) -> str:
        return f"{self.period} - {self.amount}"
