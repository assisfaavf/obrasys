from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from stock.models import (
    MeasurementStockConsumption,
    MeasurementStockConsumptionStatus,
    PurchaseRequest,
    PurchaseRequestItem,
    PurchaseRequestStatus,
    StockAlert,
    StockAlertStatus,
    StockAlertType,
    StockBalance,
    StockLocationType,
    StockMinimumRule,
)

QTY_Q = Decimal("0.001")
ACTIVE_ALERT_STATUSES = (StockAlertStatus.OPEN, StockAlertStatus.IN_PURCHASE)
FINAL_CONSUMPTION_STATUSES = (
    MeasurementStockConsumptionStatus.REVERSED,
    MeasurementStockConsumptionStatus.CANCELLED,
)


def q_qty(value) -> Decimal:
    try:
        decimal_value = Decimal(str(value or "0").replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ValidationError("Quantidade invalida.")
    return decimal_value.quantize(QTY_Q)


def _central_available_quantity(material) -> Decimal:
    total = (
        StockBalance.objects.filter(
            material=material,
            location__location_type=StockLocationType.CENTRAL,
        ).aggregate(total=Sum("quantity"))["total"]
        or Decimal("0")
    )
    return q_qty(total)


def _balance_for(material, location) -> Decimal:
    quantity = (
        StockBalance.objects.filter(material=material, location=location)
        .values_list("quantity", flat=True)
        .first()
    )
    return q_qty(quantity)


def _active_alert(material, stock_location, alert_type: str) -> StockAlert | None:
    return (
        StockAlert.objects.select_for_update()
        .filter(
            material=material,
            stock_location=stock_location,
            alert_type=alert_type,
            status__in=ACTIVE_ALERT_STATUSES,
        )
        .order_by("id")
        .first()
    )


def _message_for_low_stock(material, stock_location, current_balance: Decimal, minimum_quantity: Decimal) -> str:
    shortage = q_qty(minimum_quantity - current_balance)
    central_available = _central_available_quantity(material)
    message = (
        f"{material} esta abaixo do estoque minimo em {stock_location}. "
        f"Saldo atual: {current_balance}; minimo: {minimum_quantity}; falta: {shortage}."
    )
    if stock_location.location_type == StockLocationType.PROJECT:
        message += f" Disponivel no estoque central: {central_available}."
    return message


def _message_for_measurement_shortage(material, stock_location, shortage: Decimal) -> str:
    central_available = _central_available_quantity(material)
    message = (
        f"{material} possui falta real em medicao para {stock_location}. "
        f"Quantidade pendente: {shortage}."
    )
    if stock_location.location_type == StockLocationType.PROJECT:
        message += f" Disponivel no estoque central: {central_available}."
    return message


@transaction.atomic
def create_or_update_low_stock_alert(material, stock_location, *, current_balance=None, minimum_quantity=None) -> StockAlert:
    current_balance = _balance_for(material, stock_location) if current_balance is None else q_qty(current_balance)
    minimum_quantity = q_qty(minimum_quantity)
    shortage = max(q_qty(minimum_quantity - current_balance), Decimal("0.000"))
    alert = _active_alert(material, stock_location, StockAlertType.LOW_STOCK)
    if alert is None:
        alert = StockAlert(material=material, stock_location=stock_location, alert_type=StockAlertType.LOW_STOCK)

    alert.current_balance = current_balance
    alert.minimum_quantity = minimum_quantity
    alert.shortage_quantity = shortage
    alert.suggested_purchase_quantity = shortage
    alert.message = _message_for_low_stock(material, stock_location, current_balance, minimum_quantity)
    alert.source_model = "stock.StockBalance"
    balance_id = (
        StockBalance.objects.filter(material=material, location=stock_location)
        .values_list("id", flat=True)
        .first()
    )
    alert.source_id = balance_id
    if alert.status not in ACTIVE_ALERT_STATUSES:
        alert.status = StockAlertStatus.OPEN
        alert.resolved_at = None
    alert.save()
    return alert


@transaction.atomic
def resolve_stock_alert(alert: StockAlert) -> StockAlert:
    alert = StockAlert.objects.select_for_update().get(pk=alert.pk)
    alert.status = StockAlertStatus.RESOLVED
    alert.shortage_quantity = Decimal("0.000")
    alert.suggested_purchase_quantity = Decimal("0.000")
    alert.resolved_at = timezone.now()
    alert.save(update_fields=["status", "shortage_quantity", "suggested_purchase_quantity", "resolved_at", "updated_at"])
    return alert


@transaction.atomic
def dismiss_stock_alert(alert: StockAlert) -> StockAlert:
    alert = StockAlert.objects.select_for_update().get(pk=alert.pk)
    alert.status = StockAlertStatus.DISMISSED
    alert.resolved_at = timezone.now()
    alert.save(update_fields=["status", "resolved_at", "updated_at"])
    return alert


@transaction.atomic
def reopen_stock_alert(alert: StockAlert) -> StockAlert:
    alert = StockAlert.objects.select_for_update().get(pk=alert.pk)
    alert.status = StockAlertStatus.OPEN
    alert.resolved_at = None
    alert.save(update_fields=["status", "resolved_at", "updated_at"])
    return alert


@transaction.atomic
def check_stock_alerts_for_balance(stock_balance: StockBalance) -> StockAlert | None:
    stock_balance = (
        StockBalance.objects.select_for_update()
        .select_related("material", "location")
        .get(pk=stock_balance.pk)
    )
    rule = (
        StockMinimumRule.objects.select_for_update()
        .filter(material=stock_balance.material, stock_location=stock_balance.location, is_active=True)
        .order_by("id")
        .first()
    )
    minimum_quantity = q_qty(rule.minimum_quantity if rule else stock_balance.minimum_quantity)
    current_balance = q_qty(stock_balance.quantity)
    if minimum_quantity > 0 and current_balance < minimum_quantity:
        return create_or_update_low_stock_alert(
            stock_balance.material,
            stock_balance.location,
            current_balance=current_balance,
            minimum_quantity=minimum_quantity,
        )

    for alert in StockAlert.objects.select_for_update().filter(
        material=stock_balance.material,
        stock_location=stock_balance.location,
        alert_type=StockAlertType.LOW_STOCK,
        status__in=ACTIVE_ALERT_STATUSES,
    ):
        resolve_stock_alert(alert)
    return None


def _pending_measurement_shortage(material, stock_location) -> Decimal:
    total = (
        MeasurementStockConsumption.objects.filter(
            material=material,
            stock_location=stock_location,
            pending_quantity__gt=0,
        )
        .exclude(status__in=FINAL_CONSUMPTION_STATUSES)
        .aggregate(total=Sum("pending_quantity"))["total"]
        or Decimal("0")
    )
    return q_qty(total)


@transaction.atomic
def create_or_update_measurement_shortage_alert(consumption: MeasurementStockConsumption) -> StockAlert | None:
    consumption = (
        MeasurementStockConsumption.objects.select_for_update(of=("self",))
        .select_related("material", "stock_location", "measurement__project")
        .get(pk=consumption.pk)
    )
    if not consumption.material_id or not consumption.stock_location_id:
        return None

    shortage = _pending_measurement_shortage(consumption.material, consumption.stock_location)
    alerts = StockAlert.objects.select_for_update().filter(
        material=consumption.material,
        stock_location=consumption.stock_location,
        alert_type=StockAlertType.REAL_SHORTAGE_FROM_MEASUREMENT,
        status__in=ACTIVE_ALERT_STATUSES,
    )
    if shortage <= 0:
        for alert in alerts:
            resolve_stock_alert(alert)
        return None

    alert = alerts.order_by("id").first()
    if alert is None:
        alert = StockAlert(
            material=consumption.material,
            stock_location=consumption.stock_location,
            alert_type=StockAlertType.REAL_SHORTAGE_FROM_MEASUREMENT,
        )
    alert.current_balance = _balance_for(consumption.material, consumption.stock_location)
    alert.minimum_quantity = Decimal("0.000")
    alert.shortage_quantity = shortage
    alert.suggested_purchase_quantity = shortage
    alert.source_model = "stock.MeasurementStockConsumption"
    alert.source_id = consumption.id
    alert.message = _message_for_measurement_shortage(consumption.material, consumption.stock_location, shortage)
    if alert.status not in ACTIVE_ALERT_STATUSES:
        alert.status = StockAlertStatus.OPEN
        alert.resolved_at = None
    alert.save()
    return alert


@transaction.atomic
def resolve_measurement_shortage_alert_if_regularized(material, stock_location) -> None:
    shortage = _pending_measurement_shortage(material, stock_location)
    if shortage > 0:
        alert = _active_alert(material, stock_location, StockAlertType.REAL_SHORTAGE_FROM_MEASUREMENT)
        if alert:
            alert.current_balance = _balance_for(material, stock_location)
            alert.shortage_quantity = shortage
            alert.suggested_purchase_quantity = shortage
            alert.message = _message_for_measurement_shortage(material, stock_location, shortage)
            alert.save(
                update_fields=[
                    "current_balance",
                    "shortage_quantity",
                    "suggested_purchase_quantity",
                    "message",
                    "updated_at",
                ]
            )
        return

    for alert in StockAlert.objects.select_for_update().filter(
        material=material,
        stock_location=stock_location,
        alert_type=StockAlertType.REAL_SHORTAGE_FROM_MEASUREMENT,
        status__in=ACTIVE_ALERT_STATUSES,
    ):
        resolve_stock_alert(alert)


def resolve_stock_alert_if_restocked(alert: StockAlert) -> StockAlert:
    if alert.alert_type == StockAlertType.LOW_STOCK:
        balance = StockBalance.objects.filter(material=alert.material, location=alert.stock_location).first()
        if balance:
            check_stock_alerts_for_balance(balance)
        return StockAlert.objects.get(pk=alert.pk)
    if alert.alert_type == StockAlertType.REAL_SHORTAGE_FROM_MEASUREMENT:
        resolve_measurement_shortage_alert_if_regularized(alert.material, alert.stock_location)
        return StockAlert.objects.get(pk=alert.pk)
    return alert


@transaction.atomic
def generate_purchase_need_from_alerts(alerts, *, user=None) -> list[PurchaseRequest]:
    selected_alerts = list(
        StockAlert.objects.select_for_update(of=("self",))
        .select_related("material__unit", "project", "stock_location")
        .filter(pk__in=[alert.pk for alert in alerts])
        .filter(status=StockAlertStatus.OPEN, suggested_purchase_quantity__gt=0)
        .order_by("project_id", "stock_location_id", "material_id", "id")
    )
    if not selected_alerts:
        return []

    grouped: dict[int, list[StockAlert]] = defaultdict(list)
    for alert in selected_alerts:
        if not alert.project_id:
            raise ValidationError("Alertas sem obra vinculada nao podem gerar pedido de compra nesta etapa.")
        if hasattr(alert, "purchase_request_item"):
            continue
        grouped[alert.project_id].append(alert)

    purchase_requests: list[PurchaseRequest] = []
    for project_id, project_alerts in grouped.items():
        if not project_alerts:
            continue
        purchase_request = PurchaseRequest.objects.create(
            project_id=project_id,
            material_request=None,
            status=PurchaseRequestStatus.GENERATED,
            created_by=user,
            note="Pedido gerado a partir de alertas de estoque real da obra",
        )
        created_items = 0
        for alert in project_alerts:
            PurchaseRequestItem.objects.create(
                purchase_request=purchase_request,
                stock_alert=alert,
                material=alert.material,
                quantity=alert.suggested_purchase_quantity,
                unit=alert.material.unit,
                note=alert.message,
            )
            alert.status = StockAlertStatus.IN_PURCHASE
            alert.save(update_fields=["status", "updated_at"])
            created_items += 1

        if created_items == 0:
            purchase_request.delete()
            continue
        purchase_request.total_items = created_items
        purchase_request.save(update_fields=["total_items", "updated_at"])
        purchase_requests.append(purchase_request)

    return purchase_requests
