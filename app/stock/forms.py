from decimal import Decimal, InvalidOperation

from django import forms
from django.core.exceptions import ValidationError

from stock.models import MeasurementMaterial, StockBalance, StockLocation, StockLocationType, StockMovement, StockMovementType
from stock.services import calculate_balance_after


class StockMovementAdminForm(forms.ModelForm):
    available_quantity = forms.DecimalField(
        label="Quantidade disponivel no local",
        required=False,
        disabled=True,
        max_digits=14,
        decimal_places=3,
    )
    balance_after_display = forms.DecimalField(
        label="Quantidade apos a movimentacao",
        required=False,
        disabled=True,
        max_digits=14,
        decimal_places=3,
    )

    class Meta:
        model = StockMovement
        fields = (
            "material",
            "location",
            "target_location",
            "movement_type",
            "quantity",
            "available_quantity",
            "balance_after_display",
            "note",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        available = self._available_from_bound_data()
        self.fields["available_quantity"].initial = available
        self.fields["balance_after_display"].initial = self._balance_after_from_bound_data(available)

    def clean(self):
        cleaned_data = super().clean()
        material = cleaned_data.get("material")
        location = cleaned_data.get("location")
        target_location = cleaned_data.get("target_location")
        movement_type = cleaned_data.get("movement_type")
        quantity = cleaned_data.get("quantity")

        if not material or not location or not movement_type or quantity is None:
            return cleaned_data

        available = self._get_available_quantity(material.id, location.id)
        balance_after = calculate_balance_after(
            current_quantity=available,
            movement_type=movement_type,
            quantity=quantity,
        )
        if balance_after < 0:
            raise ValidationError("Movimentacao nao pode deixar saldo de estoque negativo.")
        if movement_type == StockMovementType.TRANSFER:
            if not target_location:
                raise ValidationError("Transferencia exige local de destino.")
            if target_location == location:
                raise ValidationError("Local de origem e destino devem ser diferentes.")

        cleaned_data["available_quantity"] = available
        cleaned_data["balance_after_display"] = balance_after
        return cleaned_data

    def _available_from_bound_data(self) -> Decimal:
        material_id = self.data.get(self.add_prefix("material")) if self.is_bound else self.initial.get("material")
        location_id = self.data.get(self.add_prefix("location")) if self.is_bound else self.initial.get("location")
        return self._get_available_quantity(material_id, location_id)

    def _balance_after_from_bound_data(self, available: Decimal) -> Decimal:
        if not self.is_bound:
            return available

        movement_type = self.data.get(self.add_prefix("movement_type")) or ""
        quantity_value = self.data.get(self.add_prefix("quantity")) or "0"
        try:
            quantity = Decimal(str(quantity_value).replace(",", "."))
        except (InvalidOperation, ValueError):
            return available

        if movement_type not in StockMovementType.values or quantity <= 0:
            return available

        try:
            return calculate_balance_after(
                current_quantity=available,
                movement_type=movement_type,
                quantity=quantity,
            )
        except ValidationError:
            return available

    @staticmethod
    def _get_available_quantity(material_id, location_id) -> Decimal:
        if not material_id or not location_id:
            return Decimal("0.000")

        quantity = (
            StockBalance.objects.filter(material_id=material_id, location_id=location_id)
            .values_list("quantity", flat=True)
            .first()
        )
        return quantity or Decimal("0.000")


class MeasurementMaterialAdminForm(forms.ModelForm):
    available_quantity = forms.DecimalField(
        label="Quantidade disponivel no local",
        required=False,
        disabled=True,
        max_digits=14,
        decimal_places=3,
    )

    class Meta:
        model = MeasurementMaterial
        fields = (
            "measurement",
            "material",
            "quantity",
            "note",
            "stock_location",
            "available_quantity",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["stock_location"].queryset = StockLocation.objects.filter(
            location_type=StockLocationType.PROJECT,
            is_active=True,
        ).select_related("project")
        self.fields["available_quantity"].initial = self._available_from_bound_data()

    def clean(self):
        cleaned_data = super().clean()
        material = cleaned_data.get("material")
        stock_location = cleaned_data.get("stock_location")
        measurement = cleaned_data.get("measurement") or getattr(self.instance, "measurement", None)

        if not material:
            return cleaned_data
        if not stock_location:
            raise ValidationError("Informe o local de retirada do material.")
        if measurement and stock_location.project_id != measurement.project_id:
            raise ValidationError("O local de retirada deve pertencer a obra da medicao.")

        cleaned_data["unit"] = material.unit
        cleaned_data["available_quantity"] = self._get_available_quantity(material.id, stock_location.id)
        return cleaned_data

    def _available_from_bound_data(self) -> Decimal:
        material_id = self.data.get(self.add_prefix("material")) if self.is_bound else self.initial.get("material")
        location_id = (
            self.data.get(self.add_prefix("stock_location"))
            if self.is_bound
            else self.initial.get("stock_location")
        )
        if not material_id:
            material_id = getattr(self.instance, "material_id", None)
        if not location_id:
            location_id = getattr(self.instance, "stock_location_id", None)
        return self._get_available_quantity(material_id, location_id)

    @staticmethod
    def _get_available_quantity(material_id, location_id) -> Decimal:
        if not material_id or not location_id:
            return Decimal("0.000")

        quantity = (
            StockBalance.objects.filter(material_id=material_id, location_id=location_id)
            .values_list("quantity", flat=True)
            .first()
        )
        return quantity or Decimal("0.000")
