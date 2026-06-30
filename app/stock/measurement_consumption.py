from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction

from billing.models import WorkflowStatus
from stock.models import (
    MeasurementMaterial,
    MeasurementMaterialStatus,
    MeasurementStockConsumption,
    MeasurementStockConsumptionStatus,
    MeasurementStockConsumptionType,
    PurchaseRequest,
    PurchaseRequestItem,
    PurchaseRequestStatus,
    StockBalance,
    StockLocation,
    StockLocationType,
    StockMovementType,
)
from stock.services import register_stock_movement

QTY_Q = Decimal("0.001")


def q_qty(value) -> Decimal:
    return Decimal(str(value or "0")).quantize(QTY_Q)


def _movement_note(prefix: str, measurement, note: str) -> str:
    base = f"{prefix} {measurement.number} - {measurement.project.name}"
    note = (note or "").strip()
    if note:
        return f"{base}: {note}"
    return base


def _is_editable_measurement(measurement) -> bool:
    return measurement.workflow_status == WorkflowStatus.DRAFT


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


def _active_consumption(measurement_material: MeasurementMaterial) -> MeasurementStockConsumption | None:
    return (
        MeasurementStockConsumption.objects.select_for_update(of=("self",))
        .filter(measurement_material=measurement_material)
        .exclude(status__in=[MeasurementStockConsumptionStatus.REVERSED, MeasurementStockConsumptionStatus.CANCELLED])
        .order_by("-id")
        .first()
    )


def _status_for(consumed: Decimal, pending: Decimal) -> str:
    if pending > 0 and consumed > 0:
        return MeasurementStockConsumptionStatus.PARTIAL
    if pending > 0:
        return MeasurementStockConsumptionStatus.PENDING_STOCK
    return MeasurementStockConsumptionStatus.ACTIVE


def _material_status_for(consumed: Decimal, pending: Decimal) -> str:
    if pending > 0 and consumed > 0:
        return MeasurementMaterialStatus.PARTIAL
    if pending > 0:
        return MeasurementMaterialStatus.PENDING_STOCK
    return MeasurementMaterialStatus.APPLIED


def _get_or_create_balance(material, location) -> StockBalance:
    balance, _ = (
        StockBalance.objects.select_for_update()
        .get_or_create(material=material, location=location, defaults={"quantity": Decimal("0")})
    )
    return balance


def _ensure_consumption(measurement_material: MeasurementMaterial) -> MeasurementStockConsumption:
    consumption = _active_consumption(measurement_material)
    if consumption:
        return consumption
    return MeasurementStockConsumption.objects.create(
        measurement_material=measurement_material,
        measurement=measurement_material.measurement,
        stock_location=measurement_material.stock_location,
        material=measurement_material.material,
        consumed_quantity=Decimal("0"),
        pending_quantity=Decimal("0"),
        status=MeasurementStockConsumptionStatus.ACTIVE,
        consumption_type=MeasurementStockConsumptionType.OUT,
        note=measurement_material.note,
    )


@transaction.atomic
def sync_measurement_material_consumption(
    measurement_material: MeasurementMaterial,
    *,
    previous: dict | None = None,
    user=None,
) -> MeasurementStockConsumption | None:
    measurement_material = (
        MeasurementMaterial.objects.select_for_update(of=("self",))
        .select_related("measurement__project", "material", "stock_location")
        .get(pk=measurement_material.pk)
    )
    if not _is_editable_measurement(measurement_material.measurement):
        return _active_consumption(measurement_material)

    if previous and _material_or_location_changed(measurement_material, previous):
        reverse_measurement_material_consumption(
            measurement_material,
            reason="Material ou local de estoque alterado",
            user=user,
        )

    consumption = _ensure_consumption(measurement_material)
    desired_quantity = q_qty(measurement_material.quantity)
    current_consumed = q_qty(consumption.consumed_quantity)

    if desired_quantity > current_consumed:
        _consume_delta(measurement_material, consumption, desired_quantity - current_consumed, user=user)
    elif desired_quantity < current_consumed:
        _reverse_delta(measurement_material, consumption, current_consumed - desired_quantity, user=user)
    else:
        _refresh_consumption_status(measurement_material, consumption)
    return consumption


def _material_or_location_changed(measurement_material: MeasurementMaterial, previous: dict) -> bool:
    return (
        previous.get("material_id") != measurement_material.material_id
        or previous.get("stock_location_id") != measurement_material.stock_location_id
    )


def _consume_delta(
    measurement_material: MeasurementMaterial,
    consumption: MeasurementStockConsumption,
    delta: Decimal,
    *,
    user=None,
) -> None:
    balance = _get_or_create_balance(measurement_material.material, measurement_material.stock_location)
    available = q_qty(balance.quantity)
    consume_quantity = min(delta, available)
    movement = None
    if consume_quantity > 0:
        movement = register_stock_movement(
            material=measurement_material.material,
            location=measurement_material.stock_location,
            movement_type=StockMovementType.MEASUREMENT_OUT,
            quantity=consume_quantity,
            note=_movement_note("Baixa real de material da medicao", measurement_material.measurement, measurement_material.note),
            created_by=user,
            measurement=measurement_material.measurement,
            measurement_material=measurement_material,
        )

    new_consumed = q_qty(consumption.consumed_quantity + consume_quantity)
    pending = max(q_qty(measurement_material.quantity) - new_consumed, Decimal("0"))
    consumption.material = measurement_material.material
    consumption.stock_location = measurement_material.stock_location
    consumption.consumed_quantity = new_consumed
    consumption.pending_quantity = pending
    consumption.stock_movement = movement or consumption.stock_movement
    consumption.status = _status_for(new_consumed, pending)
    consumption.note = measurement_material.note
    consumption.save(
        update_fields=[
            "material",
            "stock_location",
            "consumed_quantity",
            "pending_quantity",
            "stock_movement",
            "status",
            "note",
            "updated_at",
        ]
    )
    _update_measurement_material_status(measurement_material, new_consumed, pending)
    _refresh_measurement_shortage_alert(consumption)


def _reverse_delta(
    measurement_material: MeasurementMaterial,
    consumption: MeasurementStockConsumption,
    delta: Decimal,
    *,
    user=None,
) -> None:
    reverse_quantity = min(delta, q_qty(consumption.consumed_quantity))
    movement = None
    if reverse_quantity > 0:
        movement = register_stock_movement(
            material=consumption.material or measurement_material.material,
            location=consumption.stock_location or measurement_material.stock_location,
            movement_type=StockMovementType.MEASUREMENT_OUT_REVERSAL,
            quantity=reverse_quantity,
            note=_movement_note("Estorno real de material da medicao", measurement_material.measurement, measurement_material.note),
            created_by=user,
            measurement=measurement_material.measurement,
            measurement_material=measurement_material,
        )

    new_consumed = q_qty(consumption.consumed_quantity - reverse_quantity)
    pending = max(q_qty(measurement_material.quantity) - new_consumed, Decimal("0"))
    consumption.consumed_quantity = new_consumed
    consumption.pending_quantity = pending
    consumption.reversal_movement = movement or consumption.reversal_movement
    consumption.status = _status_for(new_consumed, pending)
    consumption.save(update_fields=["consumed_quantity", "pending_quantity", "reversal_movement", "status", "updated_at"])
    _update_measurement_material_status(measurement_material, new_consumed, pending)
    _refresh_measurement_shortage_alert(consumption)


def _refresh_consumption_status(measurement_material: MeasurementMaterial, consumption: MeasurementStockConsumption) -> None:
    consumed = q_qty(consumption.consumed_quantity)
    pending = max(q_qty(measurement_material.quantity) - consumed, Decimal("0"))
    consumption.pending_quantity = pending
    consumption.status = _status_for(consumed, pending)
    consumption.save(update_fields=["pending_quantity", "status", "updated_at"])
    _update_measurement_material_status(measurement_material, consumed, pending)
    _refresh_measurement_shortage_alert(consumption)


def _update_measurement_material_status(measurement_material: MeasurementMaterial, consumed: Decimal, pending: Decimal) -> None:
    MeasurementMaterial.objects.filter(pk=measurement_material.pk).update(status=_material_status_for(consumed, pending))


def _refresh_measurement_shortage_alert(consumption: MeasurementStockConsumption) -> None:
    from stock.stock_alerts import create_or_update_measurement_shortage_alert

    create_or_update_measurement_shortage_alert(consumption)


@transaction.atomic
def reverse_measurement_material_consumption(
    measurement_material: MeasurementMaterial,
    *,
    reason: str | None = None,
    user=None,
) -> MeasurementStockConsumption | None:
    measurement_material = (
        MeasurementMaterial.objects.select_for_update(of=("self",))
        .select_related("measurement__project", "material", "stock_location")
        .get(pk=measurement_material.pk)
    )
    consumption = _active_consumption(measurement_material)
    if not consumption:
        return None
    if q_qty(consumption.consumed_quantity) > 0:
        movement = register_stock_movement(
            material=consumption.material or measurement_material.material,
            location=consumption.stock_location or measurement_material.stock_location,
            movement_type=StockMovementType.MEASUREMENT_OUT_REVERSAL,
            quantity=consumption.consumed_quantity,
            note=_movement_note(reason or "Estorno de material da medicao", measurement_material.measurement, measurement_material.note),
            created_by=user,
            measurement=measurement_material.measurement,
            measurement_material=measurement_material,
        )
        consumption.reversal_movement = movement
        MeasurementStockConsumption.objects.create(
            measurement_material=measurement_material,
            measurement=measurement_material.measurement,
            stock_location=consumption.stock_location or measurement_material.stock_location,
            material=consumption.material or measurement_material.material,
            consumed_quantity=Decimal("0"),
            pending_quantity=Decimal("0"),
            status=MeasurementStockConsumptionStatus.REVERSED,
            stock_movement=movement,
            reversal_movement=movement,
            consumption_type=MeasurementStockConsumptionType.REVERSAL,
            note=reason or consumption.note,
        )
    consumption.consumed_quantity = Decimal("0")
    consumption.pending_quantity = Decimal("0")
    consumption.status = MeasurementStockConsumptionStatus.REVERSED
    consumption.note = reason or consumption.note
    consumption.save(update_fields=["consumed_quantity", "pending_quantity", "reversal_movement", "status", "note", "updated_at"])
    MeasurementMaterial.objects.filter(pk=measurement_material.pk).update(status=MeasurementMaterialStatus.REVERSED)
    _refresh_measurement_shortage_alert(consumption)
    return consumption


@transaction.atomic
def apply_measurement_stock_consumption(measurement, user=None) -> list[MeasurementStockConsumption]:
    # Kept as an idempotent compatibility hook for finalization.
    # Real consumption is synchronized while MeasurementMaterial is edited in DRAFT.
    return []


@transaction.atomic
def reverse_measurement_stock_consumption(measurement, user=None) -> list[MeasurementStockConsumption]:
    materials = list(MeasurementMaterial.objects.select_for_update(of=("self",)).filter(measurement=measurement).order_by("id"))
    reversed_consumptions: list[MeasurementStockConsumption] = []
    for material in materials:
        consumption = reverse_measurement_material_consumption(material, reason="Estorno por mudanca de status da medicao", user=user)
        if consumption:
            reversed_consumptions.append(consumption)
    return reversed_consumptions


@transaction.atomic
def sync_pending_measurement_stock_consumptions(*, measurement=None, user=None) -> dict:
    queryset = (
        MeasurementMaterial.objects.select_for_update(of=("self",))
        .select_related("measurement", "material", "stock_location")
        .filter(measurement__workflow_status=WorkflowStatus.DRAFT)
        .order_by("id")
    )
    if measurement is not None:
        queryset = queryset.filter(measurement=measurement)

    synced = 0
    skipped = 0
    skipped_materials: list[MeasurementMaterial] = []
    for measurement_material in queryset:
        if not measurement_material.stock_location_id:
            skipped += 1
            skipped_materials.append(measurement_material)
            continue
        sync_measurement_material_consumption(measurement_material, user=user)
        synced += 1

    return {
        "synced": synced,
        "skipped": skipped,
        "skipped_materials": skipped_materials,
    }


def validate_measurement_stock_availability(measurement) -> list[str]:
    errors: list[str] = []
    pending_consumptions = MeasurementStockConsumption.objects.select_related("material", "stock_location").filter(
        measurement=measurement,
        pending_quantity__gt=0,
    ).exclude(status__in=[MeasurementStockConsumptionStatus.REVERSED, MeasurementStockConsumptionStatus.CANCELLED])
    for consumption in pending_consumptions:
        errors.append(
            f"{consumption.material}: saldo insuficiente; consumo pendente por falta de estoque em {consumption.stock_location} "
            f"(pendente {consumption.pending_quantity})."
        )
    return errors


def check_low_stock_for_work(work) -> list[dict]:
    alerts: list[dict] = []
    balances = StockBalance.objects.select_related("material", "location").filter(
        location__project=work,
        location__location_type=StockLocationType.PROJECT,
        minimum_quantity__gt=0,
        quantity__lte=models.F("minimum_quantity"),
    )
    for balance in balances:
        shortage = max(q_qty(balance.minimum_quantity - balance.quantity), Decimal("0"))
        if shortage > 0:
            alerts.append({"balance": balance, "material": balance.material, "quantity": shortage})
    return alerts


@transaction.atomic
def generate_purchase_request_from_real_shortage(work, *, user=None) -> PurchaseRequest | None:
    pending_consumptions = list(
        MeasurementStockConsumption.objects.select_for_update(of=("self",))
        .select_related("material", "measurement__project")
        .filter(measurement__project=work, pending_quantity__gt=0)
        .exclude(status__in=[MeasurementStockConsumptionStatus.REVERSED, MeasurementStockConsumptionStatus.CANCELLED])
    )
    low_stock_alerts = check_low_stock_for_work(work)
    if not pending_consumptions and not low_stock_alerts:
        return None

    purchase_request = PurchaseRequest.objects.create(
        project=work,
        material_request=None,
        status=PurchaseRequestStatus.GENERATED,
        created_by=user,
        note="Pedido gerado por falta real de estoque da medicao",
    )
    created_items = 0

    for consumption in pending_consumptions:
        if hasattr(consumption, "purchase_request_item"):
            continue
        PurchaseRequestItem.objects.create(
            purchase_request=purchase_request,
            measurement_stock_consumption=consumption,
            material=consumption.material,
            quantity=consumption.pending_quantity,
            unit=consumption.material.unit,
            note="Falta real por consumo de medicao",
        )
        created_items += 1

    for alert in low_stock_alerts:
        balance = alert["balance"]
        if PurchaseRequestItem.objects.filter(stock_balance=balance).exists():
            continue
        PurchaseRequestItem.objects.create(
            purchase_request=purchase_request,
            stock_balance=balance,
            material=balance.material,
            quantity=alert["quantity"],
            unit=balance.material.unit,
            note="Reposicao de estoque minimo da obra",
        )
        created_items += 1

    if created_items == 0:
        purchase_request.delete()
        return None

    purchase_request.total_items = created_items
    purchase_request.save(update_fields=["total_items", "updated_at"])
    return purchase_request
