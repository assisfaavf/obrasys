from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction


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
    in_review_at = models.DateTimeField(null=True, blank=True)
    authorized_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    sent_note = models.TextField(blank=True)
    review_note = models.TextField(blank=True)
    authorization_note = models.TextField(blank=True)
    rejection_reason = models.TextField(blank=True)
    cancellation_reason = models.TextField(blank=True)
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

    SNAPSHOT_FIELDS = (
        "total_material_snapshot",
        "total_labor_snapshot",
        "total_total_snapshot",
        "total_indexed_snapshot",
        "index_code_snapshot",
        "index_base_month_snapshot",
        "index_ref_month_snapshot",
        "index_factor_snapshot",
    )

    def clean(self):
        super().clean()
        if not self.pk:
            return

        previous = MeasurementPeriod.objects.filter(pk=self.pk).first()
        if not previous:
            return

        if previous.workflow_status != WorkflowStatus.DRAFT:
            changed_fields = [
                field
                for field in self.SNAPSHOT_FIELDS
                if getattr(previous, field) != getattr(self, field)
            ]
            if changed_fields:
                raise ValidationError(
                    "Snapshots nao podem ser alterados apos sair de DRAFT."
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class MeasurementWorkflowHistory(models.Model):
    period = models.ForeignKey(
        "billing.MeasurementPeriod",
        on_delete=models.CASCADE,
        related_name="workflow_history",
    )
    from_status = models.CharField(max_length=20, choices=WorkflowStatus.choices)
    to_status = models.CharField(max_length=20, choices=WorkflowStatus.choices)
    note = models.TextField(blank=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="measurement_workflow_changes",
    )
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-changed_at", "-id"]

    def __str__(self) -> str:
        return f"{self.period} - {self.from_status} -> {self.to_status}"


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
    generated_from_line = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="generated_lines",
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
    excess_qty = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0"))
    excess_justification = models.TextField(blank=True)
    justification = models.TextField(blank=True)
    note = models.CharField(max_length=255, blank=True)
    is_generated_additional = models.BooleanField(default=False)
    use_additional_materials = models.BooleanField(default=False)
    additional_materials_base_qty = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=Decimal("0"),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["period", "item"], name="idx_measure_line_period_item"),
        ]

    def __str__(self) -> str:
        return f"{self.period} - {self.line_kind}"

    def clean(self):
        super().clean()

        if not self.period_id:
            return

        period_status = (
            MeasurementPeriod.objects.filter(pk=self.period_id)
            .values_list("workflow_status", flat=True)
            .first()
        )
        if period_status != WorkflowStatus.DRAFT:
            raise ValidationError("Linhas so podem ser editadas em periodos DRAFT.")

        if self.qty_period is not None and self.qty_period < 0:
            raise ValidationError("qty_period deve ser >= 0.")

        if self.additional_materials_base_qty is not None and self.additional_materials_base_qty < 0:
            raise ValidationError("additional_materials_base_qty deve ser >= 0.")

        if self.generated_from_line_id and self.generated_from_line_id == self.pk:
            raise ValidationError("generated_from_line nao pode apontar para a propria linha.")

        if self.line_kind == MeasurementLineKind.CONTRACTED and not self.item_id:
            raise ValidationError("Linhas CONTRACTED exigem item.")
        if (
            self.line_kind == MeasurementLineKind.CONTRACTED
            and self.excess_qty is not None
            and self.excess_qty > 0
            and not (self.excess_justification or "").strip()
        ):
            raise ValidationError("Justificativa do excedente e obrigatoria.")

        if self.line_kind == MeasurementLineKind.EXTRA:
            if not self.extra_description:
                raise ValidationError("Linhas EXTRA exigem descricao.")
            if not self.extra_unit_id:
                raise ValidationError("Linhas EXTRA exigem unidade.")

        if self.is_generated_additional:
            if self.line_kind != MeasurementLineKind.CONTRACTED:
                raise ValidationError("Linhas geradas devem ser CONTRACTED.")
            if self.additional_materials_base_qty != Decimal("0"):
                raise ValidationError("Linhas geradas nao podem manter additional_materials_base_qty.")
            if self.use_additional_materials:
                raise ValidationError("Linhas geradas nao usam use_additional_materials.")
        elif self.generated_from_line_id:
            raise ValidationError("Somente linhas geradas podem ter generated_from_line.")

        if not self.is_generated_additional and self.additional_materials_base_qty > (self.qty_period or Decimal("0")):
            raise ValidationError("additional_materials_base_qty nao pode exceder qty_period.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            period_status = (
                MeasurementPeriod.objects.filter(pk=self.period_id)
                .values_list("workflow_status", flat=True)
                .first()
            )
            if period_status != WorkflowStatus.DRAFT:
                raise ValidationError("Linhas so podem ser removidas em periodos DRAFT.")
            period = self.period
            should_rebuild_generated = (
                self.line_kind == MeasurementLineKind.CONTRACTED and not self.is_generated_additional
            )
            super().delete(*args, **kwargs)
            if should_rebuild_generated:
                from billing.services.measurement_lines import rebuild_generated_additional_lines

                rebuild_generated_additional_lines(period=period)


class MeasurementLineHistory(models.Model):
    line = models.ForeignKey(
        "billing.MeasurementLine",
        on_delete=models.CASCADE,
        related_name="histories",
    )
    quantity_added = models.DecimalField(max_digits=14, decimal_places=3)
    application_date = models.DateField()
    note = models.TextField(blank=True)
    source_line = models.ForeignKey(
        "billing.MeasurementLine",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_history_entries",
    )
    source_item_snapshot = models.CharField(max_length=255, blank=True)
    is_generated_additional_entry = models.BooleanField(default=False)
    additional_materials_quantity = models.DecimalField(
        max_digits=14,
        decimal_places=3,
        default=Decimal("0"),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="measurement_line_histories",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-application_date", "-created_at", "-id"]
        indexes = [
            models.Index(fields=["line", "application_date"], name="idx_measure_history_line_app"),
        ]

    def __str__(self) -> str:
        return f"{self.line_id} - {self.quantity_added}"

    def clean(self):
        super().clean()

        if self.quantity_added is None or self.quantity_added <= 0:
            raise ValidationError("quantity_added deve ser > 0.")

        if not self.application_date:
            raise ValidationError("application_date e obrigatoria.")

        if self.is_generated_additional_entry and not self.source_item_snapshot:
            raise ValidationError("Entradas geradas exigem source_item_snapshot.")

        if self.additional_materials_quantity is None or self.additional_materials_quantity < 0:
            raise ValidationError("additional_materials_quantity deve ser >= 0.")

        if self.additional_materials_quantity > self.quantity_added:
            raise ValidationError("additional_materials_quantity nao pode exceder quantity_added.")

        if self.is_generated_additional_entry and self.additional_materials_quantity > 0:
            raise ValidationError("Entradas geradas nao podem manter additional_materials_quantity.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


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

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        from billing.services.measurement_calc import update_financial_status

        update_financial_status(self.period_id)

    def delete(self, *args, **kwargs):
        period_id = self.period_id
        super().delete(*args, **kwargs)
        from billing.services.measurement_calc import update_financial_status

        update_financial_status(period_id)
