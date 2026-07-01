from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
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
    PURCHASE_IN = "PURCHASE_IN", "Entrada de compra"
    MEASUREMENT_OUT = "MEASUREMENT_OUT", "Saida de medicao"
    MEASUREMENT_OUT_REVERSAL = "MEASUREMENT_OUT_REVERSAL", "Estorno de saida de medicao"


class MeasurementMaterialStatus(models.TextChoices):
    PENDING = "PENDING", "Pendente"
    APPLIED = "APPLIED", "Baixado"
    PARTIAL = "PARTIAL", "Parcial"
    PENDING_STOCK = "PENDING_STOCK", "Pendente de estoque"
    REVERSED = "REVERSED", "Estornado"


class MeasurementStockConsumptionType(models.TextChoices):
    OUT = "OUT", "Saida"
    REVERSAL = "REVERSAL", "Estorno"


class MeasurementStockConsumptionStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Ativo"
    PENDING_STOCK = "PENDING_STOCK", "Pendente de estoque"
    PARTIAL = "PARTIAL", "Parcial"
    REVERSED = "REVERSED", "Estornado"
    CANCELLED = "CANCELLED", "Cancelado"


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


class StockImportStatus(models.TextChoices):
    DRAFT = "DRAFT", "Rascunho"
    PENDING_REVIEW = "PENDING_REVIEW", "Pendente de conferencia"
    CONFIRMED = "CONFIRMED", "Confirmado"
    CANCELLED = "CANCELLED", "Cancelado"
    ERROR = "ERROR", "Com erros"


class StockImportItemStatus(models.TextChoices):
    OK = "OK", "OK"
    PENDING_MATERIAL = "PENDING_MATERIAL", "Pendente de material"
    PENDING_UNIT = "PENDING_UNIT", "Pendente de unidade"
    PENDING_QUANTITY = "PENDING_QUANTITY", "Pendente de quantidade"
    IGNORED = "IGNORED", "Ignorado"
    CONFIRMED = "CONFIRMED", "Confirmado"


class StockTransferImportStatus(models.TextChoices):
    DRAFT = "DRAFT", "Rascunho"
    PENDING_REVIEW = "PENDING_REVIEW", "Pendente de conferencia"
    CONFIRMED = "CONFIRMED", "Confirmado"
    CANCELLED = "CANCELLED", "Cancelado"
    ERROR = "ERROR", "Com erros"


class StockTransferImportItemStatus(models.TextChoices):
    PENDING = "PENDING", "Pendente"
    OK = "OK", "OK"
    PENDING_MATERIAL = "PENDING_MATERIAL", "Pendente de material"
    PENDING_QUANTITY = "PENDING_QUANTITY", "Pendente de quantidade"
    INSUFFICIENT_STOCK = "INSUFFICIENT_STOCK", "Saldo insuficiente"
    IGNORED = "IGNORED", "Ignorado"
    CONFIRMED = "CONFIRMED", "Confirmado"
    ERROR = "ERROR", "Erro"


class MaterialRequestStatus(models.TextChoices):
    DRAFT = "DRAFT", "Rascunho"
    IN_REVIEW = "IN_REVIEW", "Em analise"
    APPROVED = "APPROVED", "Aprovada"
    PARTIALLY_FULFILLED = "PARTIALLY_FULFILLED", "Parcialmente atendida"
    FULFILLED = "FULFILLED", "Atendida"
    CANCELLED = "CANCELLED", "Cancelada"


class MaterialRequestItemStatus(models.TextChoices):
    PENDING = "PENDING", "Pendente"
    AVAILABLE_ON_PROJECT = "AVAILABLE_ON_PROJECT", "Disponivel na obra"
    TRANSFER_SUGGESTED = "TRANSFER_SUGGESTED", "Transferencia sugerida"
    PURCHASE_SUGGESTED = "PURCHASE_SUGGESTED", "Compra sugerida"
    MIXED = "MIXED", "Misto"
    TRANSFER_GENERATED = "TRANSFER_GENERATED", "Transferencia gerada"
    PURCHASE_GENERATED = "PURCHASE_GENERATED", "Compra gerada"
    TRANSFER_PURCHASE_GENERATED = "TRANSFER_PURCHASE_GENERATED", "Transferencia e compra geradas"
    FULFILLED = "FULFILLED", "Atendido"
    CANCELLED = "CANCELLED", "Cancelado"


class PurchaseRequestStatus(models.TextChoices):
    DRAFT = "DRAFT", "Rascunho"
    GENERATED = "GENERATED", "Gerado"
    QUOTING = "QUOTING", "Em cotacao"
    PURCHASED = "PURCHASED", "Comprado"
    CANCELLED = "CANCELLED", "Cancelado"


class PurchaseRequestItemStatus(models.TextChoices):
    PENDING = "PENDING", "Pendente"
    QUOTED = "QUOTED", "Cotado"
    PURCHASED = "PURCHASED", "Comprado"
    CANCELLED = "CANCELLED", "Cancelado"


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


class MaterialAlias(models.Model):
    material = models.ForeignKey("stock.Material", on_delete=models.CASCADE, related_name="aliases")
    alias = models.CharField(max_length=255)
    supplier = models.CharField(max_length=255, blank=True)
    normalized_alias = models.CharField(max_length=255, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["alias"]
        constraints = [
            models.UniqueConstraint(
                fields=["normalized_alias", "supplier"],
                name="uniq_material_alias_normalized_supplier",
            ),
        ]

    def __str__(self) -> str:
        if self.supplier:
            return f"{self.alias} ({self.supplier}) -> {self.material}"
        return f"{self.alias} -> {self.material}"

    def save(self, *args, **kwargs):
        from stock.purchase_import import normalize_description

        self.normalized_alias = normalize_description(self.alias)
        self.full_clean()
        super().save(*args, **kwargs)


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
    minimum_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0"))
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
    measurement = models.ForeignKey(
        "billing.MeasurementPeriod",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_movements",
    )
    measurement_material = models.ForeignKey(
        "stock.MeasurementMaterial",
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


class StockImport(models.Model):
    original_file = models.FileField(upload_to="stock/imports/")
    supplier = models.CharField(max_length=255, blank=True)
    project = models.ForeignKey(
        "core.Project",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_imports",
    )
    destination_location = models.ForeignKey(
        "stock.StockLocation",
        on_delete=models.PROTECT,
        related_name="stock_imports",
    )
    received_at = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=30,
        choices=StockImportStatus.choices,
        default=StockImportStatus.DRAFT,
    )
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_imports",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"Importacao de estoque #{self.pk or 'nova'}"

    def clean(self):
        super().clean()
        if not self.destination_location_id:
            raise ValidationError("Local de destino obrigatorio.")
        if self.destination_location_id:
            if self.destination_location.location_type == StockLocationType.CENTRAL and self.project_id:
                raise ValidationError("Entrada em estoque central nao deve ter obra vinculada.")
            if self.destination_location.location_type == StockLocationType.PROJECT:
                if not self.destination_location.project_id:
                    raise ValidationError("Estoque de obra exige uma obra vinculada.")
                if self.project_id and self.destination_location.project_id != self.project_id:
                    raise ValidationError("Local de destino deve pertencer a obra informada.")

    def save(self, *args, **kwargs):
        if self.destination_location_id and self.destination_location.location_type == StockLocationType.PROJECT:
            self.project_id = self.destination_location.project_id
        self.full_clean()
        super().save(*args, **kwargs)


class StockImportItem(models.Model):
    import_batch = models.ForeignKey("stock.StockImport", on_delete=models.CASCADE, related_name="items")
    row_number = models.PositiveIntegerField()
    original_code = models.CharField(max_length=80, blank=True)
    supplier_code = models.CharField(max_length=80, blank=True)
    original_description = models.CharField(max_length=255, blank=True)
    original_unit = models.CharField(max_length=40, blank=True)
    raw_quantity = models.CharField(max_length=80, blank=True)
    original_quantity = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    unit_price = models.DecimalField(max_digits=14, decimal_places=4, null=True, blank=True)
    total_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    material = models.ForeignKey(
        "stock.Material",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="stock_import_items",
    )
    confirmed_quantity = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    status = models.CharField(
        max_length=30,
        choices=StockImportItemStatus.choices,
        default=StockImportItemStatus.PENDING_MATERIAL,
    )
    note = models.TextField(blank=True)
    manual_adjustment = models.BooleanField(default=False)
    stock_movement = models.ForeignKey(
        "stock.StockMovement",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="stock_import_items",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["import_batch_id", "row_number", "id"]
        constraints = [
            models.UniqueConstraint(fields=["import_batch", "row_number"], name="uniq_stock_import_item_row"),
        ]

    def __str__(self) -> str:
        return f"{self.import_batch_id} - linha {self.row_number}: {self.original_description}"

    def clean(self):
        super().clean()
        if self.status in {StockImportItemStatus.OK, StockImportItemStatus.CONFIRMED}:
            if not self.material_id:
                raise ValidationError("Item confirmado exige material vinculado.")
            if self.confirmed_quantity is None or self.confirmed_quantity <= 0:
                raise ValidationError("Item confirmado exige quantidade maior que zero.")
        if self.confirmed_quantity is not None and self.confirmed_quantity <= 0:
            raise ValidationError("Quantidade confirmada deve ser maior que zero.")
        if self.original_quantity is not None and self.original_quantity <= 0:
            raise ValidationError("Quantidade original deve ser maior que zero.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class StockTransferImport(models.Model):
    original_file = models.FileField(upload_to="stock/transfer-imports/")
    origin_location = models.ForeignKey(
        "stock.StockLocation",
        on_delete=models.PROTECT,
        related_name="outgoing_transfer_imports",
    )
    destination_location = models.ForeignKey(
        "stock.StockLocation",
        on_delete=models.PROTECT,
        related_name="incoming_transfer_imports",
    )
    status = models.CharField(
        max_length=30,
        choices=StockTransferImportStatus.choices,
        default=StockTransferImportStatus.DRAFT,
    )
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stock_transfer_imports",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    total_rows = models.PositiveIntegerField(default=0)
    total_valid_items = models.PositiveIntegerField(default=0)
    total_error_items = models.PositiveIntegerField(default=0)
    total_transferred_items = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"Transferencia importada #{self.pk or 'nova'}"

    def clean(self):
        super().clean()
        if not self.origin_location_id:
            raise ValidationError("Local de origem obrigatorio.")
        if not self.destination_location_id:
            raise ValidationError("Local de destino obrigatorio.")
        if self.origin_location_id and self.destination_location_id and self.origin_location_id == self.destination_location_id:
            raise ValidationError("Origem e destino devem ser diferentes.")
        if self.origin_location_id and not self.origin_location.is_active:
            raise ValidationError("Local de origem deve estar ativo.")
        if self.destination_location_id and not self.destination_location.is_active:
            raise ValidationError("Local de destino deve estar ativo.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class StockTransferImportItem(models.Model):
    transfer_import = models.ForeignKey(
        "stock.StockTransferImport",
        on_delete=models.CASCADE,
        related_name="items",
    )
    row_number = models.PositiveIntegerField()
    original_code = models.CharField(max_length=80, blank=True)
    original_description = models.CharField(max_length=255, blank=True)
    original_brand = models.CharField(max_length=120, blank=True)
    original_unit = models.CharField(max_length=40, blank=True)
    raw_quantity = models.CharField(max_length=80, blank=True)
    original_quantity = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    confirmed_quantity = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    available_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0"))
    material = models.ForeignKey(
        "stock.Material",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="transfer_import_items",
    )
    status = models.CharField(
        max_length=30,
        choices=StockTransferImportItemStatus.choices,
        default=StockTransferImportItemStatus.PENDING,
    )
    error_message = models.TextField(blank=True)
    note = models.TextField(blank=True)
    transfer_movement = models.ForeignKey(
        "stock.StockMovement",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="transfer_import_items",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["transfer_import_id", "row_number", "id"]
        constraints = [
            models.UniqueConstraint(fields=["transfer_import", "row_number"], name="uniq_stock_transfer_import_row"),
        ]

    @property
    def unit(self):
        return self.material.unit if self.material_id else None

    def __str__(self) -> str:
        return f"{self.transfer_import_id} - linha {self.row_number}: {self.original_description}"

    def clean(self):
        super().clean()
        if self.status in {StockTransferImportItemStatus.OK, StockTransferImportItemStatus.CONFIRMED}:
            if not self.material_id:
                raise ValidationError("Item confirmado exige material vinculado.")
            if self.confirmed_quantity is None or self.confirmed_quantity <= 0:
                raise ValidationError("Quantidade confirmada deve ser maior que zero.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class MaterialRequest(models.Model):
    project = models.ForeignKey("core.Project", on_delete=models.PROTECT, related_name="material_requests")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="material_requests",
    )
    status = models.CharField(
        max_length=30,
        choices=MaterialRequestStatus.choices,
        default=MaterialRequestStatus.DRAFT,
    )
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    analyzed_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    total_items = models.PositiveIntegerField(default=0)
    total_items_with_project_stock = models.PositiveIntegerField(default=0)
    total_items_with_transfer_suggestion = models.PositiveIntegerField(default=0)
    total_items_with_purchase_suggestion = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"Requisicao de materiais #{self.pk or 'nova'} - {self.project}"

    def clean(self):
        super().clean()
        if not self.project_id:
            raise ValidationError("Obra obrigatoria.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class MaterialRequestItem(models.Model):
    request = models.ForeignKey("stock.MaterialRequest", on_delete=models.CASCADE, related_name="items")
    material = models.ForeignKey("stock.Material", on_delete=models.PROTECT, related_name="request_items")
    requested_quantity = models.DecimalField(max_digits=14, decimal_places=3)
    project_available_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0"))
    suggested_project_usage_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0"))
    central_available_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0"))
    suggested_transfer_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0"))
    suggested_purchase_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0"))
    approved_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0"))
    note = models.TextField(blank=True)
    status = models.CharField(
        max_length=30,
        choices=MaterialRequestItemStatus.choices,
        default=MaterialRequestItemStatus.PENDING,
    )
    transfer_movement = models.ForeignKey(
        "stock.StockMovement",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="material_request_transfer_items",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["request_id", "id"]
        constraints = [
            models.UniqueConstraint(fields=["request", "material"], name="uniq_material_request_item_material"),
            models.CheckConstraint(check=Q(requested_quantity__gt=0), name="material_request_item_qty_positive"),
        ]

    @property
    def unit(self):
        return self.material.unit if self.material_id else None

    def __str__(self) -> str:
        return f"{self.request_id} - {self.material} - {self.requested_quantity}"

    def clean(self):
        super().clean()
        if not self.material_id:
            raise ValidationError("Material obrigatorio.")
        if self.requested_quantity is None or self.requested_quantity <= 0:
            raise ValidationError("Quantidade solicitada deve ser maior que zero.")
        if self.material_id and self.requested_quantity is not None:
            from stock.material_request import validate_material_request_item

            validate_material_request_item(self)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class PurchaseRequest(models.Model):
    project = models.ForeignKey("core.Project", on_delete=models.PROTECT, related_name="purchase_requests")
    material_request = models.OneToOneField(
        "stock.MaterialRequest",
        on_delete=models.PROTECT,
        related_name="purchase_request",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=20,
        choices=PurchaseRequestStatus.choices,
        default=PurchaseRequestStatus.GENERATED,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_requests",
    )
    note = models.TextField(blank=True)
    total_items = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"Pedido de compra #{self.pk or 'novo'} - {self.project}"

    def clean(self):
        super().clean()
        if not self.project_id:
            raise ValidationError("Obra obrigatoria.")
        if self.project_id and self.material_request_id and self.project_id != self.material_request.project_id:
            raise ValidationError("Pedido de compra deve pertencer a mesma obra da requisicao.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class PurchaseRequestItem(models.Model):
    purchase_request = models.ForeignKey("stock.PurchaseRequest", on_delete=models.CASCADE, related_name="items")
    material_request_item = models.OneToOneField(
        "stock.MaterialRequestItem",
        on_delete=models.PROTECT,
        related_name="purchase_request_item",
        null=True,
        blank=True,
    )
    measurement_stock_consumption = models.OneToOneField(
        "stock.MeasurementStockConsumption",
        on_delete=models.PROTECT,
        related_name="purchase_request_item",
        null=True,
        blank=True,
    )
    stock_balance = models.ForeignKey(
        "stock.StockBalance",
        on_delete=models.PROTECT,
        related_name="purchase_request_items",
        null=True,
        blank=True,
    )
    material = models.ForeignKey("stock.Material", on_delete=models.PROTECT, related_name="purchase_request_items")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit = models.ForeignKey("catalog.Unit", on_delete=models.PROTECT, related_name="purchase_request_items")
    note = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=PurchaseRequestItemStatus.choices,
        default=PurchaseRequestItemStatus.PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["purchase_request_id", "id"]
        constraints = [
            models.CheckConstraint(check=Q(quantity__gt=0), name="purchase_request_item_qty_positive"),
        ]

    def __str__(self) -> str:
        return f"{self.purchase_request_id} - {self.material} - {self.quantity}"

    def clean(self):
        super().clean()
        if not self.material_id:
            raise ValidationError("Material obrigatorio.")
        if self.quantity is None or self.quantity <= 0:
            raise ValidationError("Quantidade deve ser maior que zero.")
        if self.material_id and self.unit_id and self.material.unit_id != self.unit_id:
            raise ValidationError("Unidade do pedido deve ser igual a unidade do material.")
        if self.material_request_item_id and self.material_id and self.material_request_item.material_id != self.material_id:
            raise ValidationError("Item do pedido deve usar o mesmo material da requisicao.")
        if (
            self.measurement_stock_consumption_id
            and self.material_id
            and self.measurement_stock_consumption.material_id
            and self.measurement_stock_consumption.material_id != self.material_id
        ):
            raise ValidationError("Item do pedido deve usar o mesmo material do consumo.")

    def save(self, *args, **kwargs):
        if self.material_id:
            self.unit_id = self.material.unit_id
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
        sync_stock = kwargs.pop("sync_stock", False)
        previous = None
        if self.pk:
            previous = (
                MeasurementMaterial.objects.filter(pk=self.pk)
                .values("material_id", "stock_location_id", "quantity", "status")
                .first()
            )
        if self.material_id:
            self.unit_id = self.material.unit_id
        self.full_clean()
        with transaction.atomic():
            super().save(*args, **kwargs)
            if sync_stock:
                from stock.measurement_consumption import sync_measurement_material_consumption

                sync_measurement_material_consumption(self, previous=previous)

    def delete(self, *args, **kwargs):
        from stock.measurement_consumption import reverse_measurement_material_consumption

        with transaction.atomic():
            reverse_measurement_material_consumption(self, reason="Item removido da medicao")
            return super().delete(*args, **kwargs)


class MeasurementStockConsumption(models.Model):
    measurement_material = models.ForeignKey(
        "stock.MeasurementMaterial",
        on_delete=models.SET_NULL,
        related_name="consumptions",
        null=True,
        blank=True,
    )
    measurement = models.ForeignKey(
        "billing.MeasurementPeriod",
        on_delete=models.CASCADE,
        related_name="stock_consumptions",
    )
    stock_location = models.ForeignKey(
        "stock.StockLocation",
        on_delete=models.PROTECT,
        related_name="measurement_stock_consumptions",
        null=True,
        blank=True,
    )
    material = models.ForeignKey(
        "stock.Material",
        on_delete=models.PROTECT,
        related_name="measurement_stock_consumptions",
        null=True,
        blank=True,
    )
    consumed_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0"))
    pending_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=Decimal("0"))
    status = models.CharField(
        max_length=20,
        choices=MeasurementStockConsumptionStatus.choices,
        default=MeasurementStockConsumptionStatus.ACTIVE,
    )
    stock_movement = models.ForeignKey(
        "stock.StockMovement",
        on_delete=models.PROTECT,
        related_name="measurement_consumptions",
        null=True,
        blank=True,
    )
    reversal_movement = models.ForeignKey(
        "stock.StockMovement",
        on_delete=models.PROTECT,
        related_name="measurement_reversal_consumptions",
        null=True,
        blank=True,
    )
    consumption_type = models.CharField(max_length=20, choices=MeasurementStockConsumptionType.choices)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["measurement", "consumption_type"], name="idx_meas_stock_cons_type"),
            models.Index(fields=["stock_movement"], name="idx_meas_stock_cons_mov"),
        ]

    def __str__(self) -> str:
        return f"{self.measurement} - {self.consumption_type} - {self.stock_movement_id}"
