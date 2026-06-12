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


class Material(models.Model):
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=255)
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
    movement_type = models.CharField(max_length=20, choices=StockMovementType.choices)
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
