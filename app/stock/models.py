from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class StockLocationType(models.TextChoices):
    CENTRAL = "CENTRAL", "Central"
    PROJECT = "PROJECT", "Obra"


class StockMovementType(models.TextChoices):
    IN = "IN", "Entrada"
    OUT = "OUT", "Saida"
    ADJUST_POSITIVE = "ADJUST_POSITIVE", "Ajuste positivo"
    ADJUST_NEGATIVE = "ADJUST_NEGATIVE", "Ajuste negativo"
    TRANSFER = "TRANSFER", "Transferencia"
    INITIAL_IN = "ENTRADA_INICIAL", "Entrada inicial"
    MEASUREMENT_OUT = "MEASUREMENT_OUT", "Saida de medicao"
    MEASUREMENT_OUT_REVERSAL = "MEASUREMENT_OUT_REVERSAL", "Estorno de saida de medicao"


class MeasurementMaterialStatus(models.TextChoices):
    PENDING = "PENDING", "Pendente"
    APPLIED = "APPLIED", "Baixado"
    REVERSED = "REVERSED", "Estornado"


class MeasurementStockConsumptionType(models.TextChoices):
    OUT = "OUT", "Saida"
    REVERSAL = "REVERSAL", "Estorno"


class InitialStockImportStatus(models.TextChoices):
    DRAFT = "DRAFT", "Rascunho"
    PENDING_REVIEW = "PENDING_REVIEW", "Pendente de conferencia"
    CONFIRMED = "CONFIRMED", "Confirmado"
    CANCELLED = "CANCELLED", "Cancelado"
    ERROR = "ERROR", "Com erros"


class InitialStockImportItemStatus(models.TextChoices):
    PENDING = "PENDING", "Pendente"
    NEW_MATERIAL = "NEW_MATERIAL", "Novo material"
    EXISTING_MATERIAL = "EXISTING_MATERIAL", "Material existente"
    UPDATE_MATERIAL = "UPDATE_MATERIAL", "Atualizar material"
    ERROR = "ERROR", "Erro"
    IGNORED = "IGNORED", "Ignorado"
    CONFIRMED = "CONFIRMED", "Confirmado"


class Material(models.Model):
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=120, blank=True, default="")
    subcategory = models.CharField(max_length=120, blank=True, default="")
    item_type = models.CharField(max_length=120, blank=True, default="")
    brand = models.CharField(max_length=120, blank=True, default="")
    unit = models.ForeignKey("catalog.Unit", on_delete=models.PROTECT, related_name="stock_materials")
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"


class StockLocation(models.Model):
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=255)
    location_type = models.CharField(
        max_length=20,
        choices=StockLocationType.choices,
        default=StockLocationType.CENTRAL,
    )
    project = models.ForeignKey(
        "core.Project",
        on_delete=models.CASCADE,
        related_name="stock_locations",
        null=True,
        blank=True,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["location_type", "code"]
        constraints = [
            models.CheckConstraint(
                check=(
                    Q(location_type=StockLocationType.CENTRAL, project__isnull=True)
                    | Q(location_type=StockLocationType.PROJECT, project__isnull=False)
                ),
                name="stock_location_type_project_consistency",
            ),
        ]

    def __str__(self) -> str:
        if self.project_id:
            return f"{self.code} - {self.name} ({self.project.name})"
        return f"{self.code} - {self.name}"

    def clean(self):
        super().clean()
        if self.location_type == StockLocationType.CENTRAL and self.project_id:
            raise ValidationError("Estoque central nao deve estar vinculado a uma obra.")
        if self.location_type == StockLocationType.PROJECT and not self.project_id:
            raise ValidationError("Estoque de obra exige uma obra vinculada.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class StockBalance(models.Model):
    material = models.ForeignKey("stock.Material", on_delete=models.CASCADE, related_name="balances")
    location = models.ForeignKey("stock.StockLocation", on_delete=models.CASCADE, related_name="balances")
    quantity = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0"))
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["location_id", "material_id"]
        constraints = [
            models.UniqueConstraint(fields=["material", "location"], name="uniq_stock_balance_material_location"),
            models.CheckConstraint(check=Q(quantity__gte=0), name="stock_balance_quantity_non_negative"),
        ]

    def __str__(self) -> str:
        return f"{self.material} @ {self.location}: {self.quantity}"

    def clean(self):
        super().clean()
        if self.quantity is not None and self.quantity < 0:
            raise ValidationError("Saldo de estoque nao pode ser negativo.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class StockMovement(models.Model):
    material = models.ForeignKey("stock.Material", on_delete=models.PROTECT, related_name="stock_movements")
    location = models.ForeignKey("stock.StockLocation", on_delete=models.PROTECT, related_name="stock_movements")
    target_location = models.ForeignKey(
        "stock.StockLocation",
        on_delete=models.PROTECT,
        related_name="incoming_transfer_movements",
        null=True,
        blank=True,
    )
    movement_type = models.CharField(max_length=30, choices=StockMovementType.choices)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    balance_after = models.DecimalField(max_digits=14, decimal_places=3)
    note = models.TextField(blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_movements",
    )

    class Meta:
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(fields=["material", "location"], name="idx_stock_mov_material_loc"),
            models.Index(fields=["movement_type", "occurred_at"], name="idx_stock_mov_type_date"),
        ]
        constraints = [
            models.CheckConstraint(check=Q(quantity__gt=0), name="stock_movement_quantity_positive"),
            models.CheckConstraint(check=Q(balance_after__gte=0), name="stock_movement_balance_non_negative"),
        ]

    def __str__(self) -> str:
        return f"{self.material} - {self.movement_type} - {self.quantity}"

    def clean(self):
        super().clean()
        if self.quantity is not None and self.quantity <= 0:
            raise ValidationError("Quantidade movimentada deve ser > 0.")
        if self.balance_after is not None and self.balance_after < 0:
            raise ValidationError("Saldo apos movimentacao nao pode ser negativo.")
        if self.movement_type == StockMovementType.TRANSFER:
            if not self.target_location_id:
                raise ValidationError("Transferencia exige local de destino.")
            if self.target_location_id == self.location_id:
                raise ValidationError("Local de origem e destino devem ser diferentes.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class InitialStockImport(models.Model):
    original_file = models.FileField(upload_to="stock/initial-imports/")
    destination_location = models.ForeignKey(
        "stock.StockLocation",
        on_delete=models.PROTECT,
        related_name="initial_stock_imports",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=30,
        choices=InitialStockImportStatus.choices,
        default=InitialStockImportStatus.DRAFT,
    )
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="initial_stock_imports",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    total_rows = models.PositiveIntegerField(default=0)
    total_materials_created = models.PositiveIntegerField(default=0)
    total_materials_updated = models.PositiveIntegerField(default=0)
    total_movements_created = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"Carga inicial de estoque #{self.pk or 'nova'}"

    def clean(self):
        super().clean()
        if self.destination_location_id and self.destination_location.location_type != StockLocationType.CENTRAL:
            raise ValidationError("A carga inicial deve usar o estoque central nesta etapa.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class InitialStockImportItem(models.Model):
    import_batch = models.ForeignKey(
        "stock.InitialStockImport",
        on_delete=models.CASCADE,
        related_name="items",
    )
    row_number = models.PositiveIntegerField()
    original_code = models.CharField(max_length=80, blank=True)
    original_category = models.CharField(max_length=120, blank=True)
    original_subcategory = models.CharField(max_length=120, blank=True)
    original_item_type = models.CharField(max_length=120, blank=True)
    original_description = models.CharField(max_length=255, blank=True)
    original_brand = models.CharField(max_length=120, blank=True)
    original_unit = models.CharField(max_length=40, blank=True)
    raw_quantity = models.CharField(max_length=80, blank=True)
    original_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0"))
    confirmed_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0"))
    material = models.ForeignKey(
        "stock.Material",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="initial_stock_import_items",
    )
    status = models.CharField(
        max_length=30,
        choices=InitialStockImportItemStatus.choices,
        default=InitialStockImportItemStatus.PENDING,
    )
    planned_action = models.CharField(max_length=255, blank=True)
    error_message = models.TextField(blank=True)
    stock_movement = models.ForeignKey(
        "stock.StockMovement",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="initial_stock_import_items",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["import_batch_id", "row_number", "id"]
        constraints = [
            models.UniqueConstraint(fields=["import_batch", "row_number"], name="uniq_initial_stock_import_row"),
        ]

    def __str__(self) -> str:
        return f"{self.import_batch_id} - linha {self.row_number}: {self.original_description}"

    def clean(self):
        super().clean()
        if self.original_quantity is not None and self.original_quantity < 0:
            raise ValidationError("Quantidade original nao pode ser negativa.")
        if self.confirmed_quantity is not None and self.confirmed_quantity < 0:
            raise ValidationError("Quantidade confirmada nao pode ser negativa.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class MeasurementMaterial(models.Model):
    measurement = models.ForeignKey(
        "billing.MeasurementPeriod",
        on_delete=models.CASCADE,
        related_name="stock_materials",
    )
    material = models.ForeignKey("stock.Material", on_delete=models.PROTECT, related_name="measurement_materials")
    unit = models.ForeignKey("catalog.Unit", on_delete=models.PROTECT, related_name="measurement_stock_materials")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    note = models.TextField(blank=True)
    stock_location = models.ForeignKey(
        "stock.StockLocation",
        on_delete=models.PROTECT,
        related_name="measurement_materials",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=20,
        choices=MeasurementMaterialStatus.choices,
        default=MeasurementMaterialStatus.PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["measurement_id", "id"]
        indexes = [
            models.Index(fields=["measurement", "status"], name="idx_meas_mat_measure_status"),
            models.Index(fields=["material", "stock_location"], name="idx_meas_mat_material_loc"),
        ]

    def __str__(self) -> str:
        return f"{self.measurement} - {self.material} - {self.quantity}"

    def clean(self):
        super().clean()
        if self.quantity is not None and self.quantity <= 0:
            raise ValidationError("Quantidade aplicada deve ser > 0.")
        if self.material_id and self.unit_id and self.material.unit_id != self.unit_id:
            raise ValidationError("Unidade aplicada deve ser igual a unidade do material.")
        if self.material_id and not self.stock_location_id:
            raise ValidationError("Informe o local de retirada do material.")
        if self.stock_location_id:
            if self.stock_location.location_type != StockLocationType.PROJECT:
                raise ValidationError("Materiais de medicao devem sair de um estoque de obra.")
            if self.measurement_id and self.stock_location.project_id != self.measurement.project_id:
                raise ValidationError("O local de retirada deve pertencer a obra da medicao.")

    def save(self, *args, **kwargs):
        if self.material_id:
            self.unit_id = self.material.unit_id
        self.full_clean()
        super().save(*args, **kwargs)


class MeasurementStockConsumption(models.Model):
    measurement_material = models.ForeignKey(
        "stock.MeasurementMaterial",
        on_delete=models.CASCADE,
        related_name="consumptions",
    )
    measurement = models.ForeignKey(
        "billing.MeasurementPeriod",
        on_delete=models.CASCADE,
        related_name="stock_consumptions",
    )
    stock_movement = models.ForeignKey(
        "stock.StockMovement",
        on_delete=models.PROTECT,
        related_name="measurement_consumptions",
    )
    consumption_type = models.CharField(max_length=20, choices=MeasurementStockConsumptionType.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["measurement", "consumption_type"], name="idx_meas_stock_cons_type"),
            models.Index(fields=["stock_movement"], name="idx_meas_stock_cons_mov"),
        ]

    def __str__(self) -> str:
        return f"{self.measurement} - {self.consumption_type} - {self.stock_movement_id}"
