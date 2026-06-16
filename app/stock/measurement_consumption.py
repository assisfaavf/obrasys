from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from stock.models import (
    MeasurementMaterial,
    MeasurementMaterialStatus,
    MeasurementStockConsumption,
    MeasurementStockConsumptionType,
    StockBalance,
    StockLocation,
    StockLocationType,
    StockMovementType,
)
from stock.services import register_stock_movement


def _project_stock_location(measurement) -> StockLocation:
    if not measurement.project_id:
        raise ValidationError("Medicao sem obra vinculada.")

    location = (
        StockLocation.objects.filter(
            project=measurement.project,
            location_type=StockLocationType.PROJECT,
            is_active=True,
        )
        .order_by("id")
        .first()
    )
    if location is None:
        raise ValidationError("A obra da medicao nao possui almoxarifado de obra ativo.")
    return location


def _pending_materials(measurement):
    return MeasurementMaterial.objects.select_related("material", "unit", "stock_location").filter(
        measurement=measurement,
    ).exclude(status=MeasurementMaterialStatus.APPLIED)


def validate_measurement_stock_availability(measurement) -> list[str]:
    materials = list(_pending_materials(measurement))
    if not materials:
        return []

    errors: list[str] = []
    requested_by_material_location: dict[tuple[int, int], Decimal] = {}
    item_by_key = {}
    for item in materials:
        if item.quantity is None or item.quantity <= 0:
            errors.append(f"{item.material}: quantidade aplicada deve ser > 0.")
            continue
        if not item.stock_location_id:
            errors.append(f"{item.material}: informe o local de retirada do material.")
            continue
        if item.stock_location.location_type != StockLocationType.PROJECT:
            errors.append(f"{item.material}: local de retirada deve ser um estoque de obra.")
        if item.stock_location.project_id != measurement.project_id:
            errors.append(f"{item.material}: local de retirada deve pertencer a obra da medicao.")
        if not item.material.is_active:
            errors.append(f"{item.material}: material inativo nao pode ser baixado.")
        if item.unit_id != item.material.unit_id:
            errors.append(f"{item.material}: unidade diferente da unidade cadastrada no material.")
        key = (item.material_id, item.stock_location_id)
        requested_by_material_location[key] = requested_by_material_location.get(key, Decimal("0")) + item.quantity
        item_by_key[key] = item

    if errors:
        return errors

    balances = {
        (row["material_id"], row["location_id"]): row["quantity"]
        for row in StockBalance.objects.filter(
            material_id__in={material_id for material_id, _location_id in requested_by_material_location.keys()},
            location_id__in={location_id for _material_id, location_id in requested_by_material_location.keys()},
        ).values("material_id", "location_id", "quantity")
    }

    for key, requested_qty in requested_by_material_location.items():
        available_qty = balances.get(key, Decimal("0"))
        if available_qty < requested_qty:
            item = item_by_key[key]
            errors.append(
                f"{item.material}: saldo insuficiente em {item.stock_location} "
                f"(disponivel {available_qty}, solicitado {requested_qty})."
            )

    return errors


@transaction.atomic
def apply_measurement_stock_consumption(measurement, user=None) -> list[MeasurementStockConsumption]:
    materials = list(
        _pending_materials(measurement)
        .select_for_update()
        .select_related("material", "unit", "measurement__project")
        .order_by("id")
    )
    if not materials:
        return []

    errors = validate_measurement_stock_availability(measurement)
    if errors:
        raise ValidationError(errors)

    created_consumptions: list[MeasurementStockConsumption] = []
    for item in materials:
        movement = register_stock_movement(
            material=item.material,
            location=item.stock_location,
            movement_type=StockMovementType.MEASUREMENT_OUT,
            quantity=item.quantity,
            note=_movement_note("Baixa de material da medicao", measurement, item.note),
            created_by=user,
        )
        consumption = MeasurementStockConsumption.objects.create(
            measurement_material=item,
            measurement=measurement,
            stock_movement=movement,
            consumption_type=MeasurementStockConsumptionType.OUT,
        )
        MeasurementMaterial.objects.filter(pk=item.pk).update(
            status=MeasurementMaterialStatus.APPLIED,
        )
        created_consumptions.append(consumption)

    return created_consumptions


@transaction.atomic
def reverse_measurement_stock_consumption(measurement, user=None) -> list[MeasurementStockConsumption]:
    materials = list(
        MeasurementMaterial.objects.select_for_update()
        .select_related("material", "stock_location", "measurement__project")
        .filter(measurement=measurement, status=MeasurementMaterialStatus.APPLIED)
        .order_by("id")
    )
    if not materials:
        return []

    created_reversals: list[MeasurementStockConsumption] = []
    for item in materials:
        stock_location = item.stock_location or _project_stock_location(measurement)
        movement = register_stock_movement(
            material=item.material,
            location=stock_location,
            movement_type=StockMovementType.MEASUREMENT_OUT_REVERSAL,
            quantity=item.quantity,
            note=_movement_note("Estorno de material da medicao", measurement, item.note),
            created_by=user,
        )
        reversal = MeasurementStockConsumption.objects.create(
            measurement_material=item,
            measurement=measurement,
            stock_movement=movement,
            consumption_type=MeasurementStockConsumptionType.REVERSAL,
        )
        MeasurementMaterial.objects.filter(pk=item.pk).update(status=MeasurementMaterialStatus.REVERSED)
        created_reversals.append(reversal)

    return created_reversals


def _movement_note(prefix: str, measurement, note: str) -> str:
    base = f"{prefix} {measurement.number} - {measurement.project.name}"
    note = (note or "").strip()
    if note:
        return f"{base}: {note}"
    return base
