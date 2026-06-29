from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import Project
from stock.models import (
    Material,
    MaterialRequest,
    MaterialRequestItem,
    MaterialRequestItemStatus,
    MaterialRequestStatus,
    StockBalance,
    StockLocation,
    StockLocationType,
)

QTY_Q = Decimal("0.001")
INDIVISIBLE_UNIT_CODES = {"UN", "UND", "UNIDADE", "VARA", "LATA", "PECA", "PCA", "PC", "PÇ", "CX"}


def q_qty(value) -> Decimal:
    try:
        decimal_value = Decimal(str(value or "0").replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ValidationError("Quantidade invalida.")
    return decimal_value.quantize(QTY_Q)


def create_material_request(*, project: Project, requested_by=None, note: str = "") -> MaterialRequest:
    return MaterialRequest.objects.create(project=project, requested_by=requested_by, note=(note or "").strip())


def add_material_request_item(
    *,
    request: MaterialRequest,
    material: Material,
    requested_quantity,
    note: str = "",
) -> MaterialRequestItem:
    return MaterialRequestItem.objects.create(
        request=request,
        material=material,
        requested_quantity=q_qty(requested_quantity),
        note=(note or "").strip(),
    )


def validate_material_request_item(item: MaterialRequestItem) -> None:
    quantity = q_qty(item.requested_quantity)
    if quantity <= 0:
        raise ValidationError("Quantidade solicitada deve ser maior que zero.")
    if item.material_id and _is_indivisible_unit(item.material.unit) and quantity != quantity.to_integral_value():
        raise ValidationError("Quantidade fracionada nao permitida para unidade indivisivel.")


@transaction.atomic
def calculate_material_request(material_request: MaterialRequest) -> MaterialRequest:
    material_request = MaterialRequest.objects.select_for_update(of=("self",)).get(pk=material_request.pk)
    if material_request.status not in {MaterialRequestStatus.DRAFT, MaterialRequestStatus.IN_REVIEW}:
        raise ValidationError("Somente requisicoes em rascunho ou em analise podem ser recalculadas.")

    project_location = get_project_stock_location(material_request.project)
    central_location = get_central_stock_location()

    for item in material_request.items.select_for_update(of=("self",)).select_related("material", "material__unit"):
        calculate_material_request_item(
            item,
            project_location=project_location,
            central_location=central_location,
        )

    _update_request_counters(material_request)
    material_request.status = MaterialRequestStatus.IN_REVIEW
    material_request.analyzed_at = timezone.now()
    material_request.save(
        update_fields=[
            "status",
            "analyzed_at",
            "updated_at",
            "total_items",
            "total_items_with_project_stock",
            "total_items_with_transfer_suggestion",
            "total_items_with_purchase_suggestion",
        ]
    )
    return material_request


def calculate_material_request_item(
    item: MaterialRequestItem,
    *,
    project_location: StockLocation | None = None,
    central_location: StockLocation | None = None,
) -> MaterialRequestItem:
    validate_material_request_item(item)

    project_location = project_location or get_project_stock_location(item.request.project)
    central_location = central_location or get_central_stock_location()

    requested = q_qty(item.requested_quantity)
    project_available = get_available_balance(material=item.material, location=project_location)
    central_available = get_available_balance(material=item.material, location=central_location)

    project_usage = min(requested, project_available)
    remaining_after_project = requested - project_usage
    transfer = min(remaining_after_project, central_available)
    purchase = remaining_after_project - transfer

    item.project_available_quantity = q_qty(project_available)
    item.suggested_project_usage_quantity = q_qty(project_usage)
    item.central_available_quantity = q_qty(central_available)
    item.suggested_transfer_quantity = q_qty(transfer)
    item.suggested_purchase_quantity = q_qty(purchase)
    item.approved_quantity = requested
    item.status = _item_status(project_usage=project_usage, transfer=transfer, purchase=purchase)
    item.save(
        update_fields=[
            "project_available_quantity",
            "suggested_project_usage_quantity",
            "central_available_quantity",
            "suggested_transfer_quantity",
            "suggested_purchase_quantity",
            "approved_quantity",
            "status",
            "updated_at",
        ]
    )
    return item


@transaction.atomic
def approve_material_request(material_request: MaterialRequest) -> MaterialRequest:
    material_request = MaterialRequest.objects.select_for_update(of=("self",)).get(pk=material_request.pk)
    if material_request.status not in {MaterialRequestStatus.IN_REVIEW, MaterialRequestStatus.DRAFT}:
        raise ValidationError("Requisicao nao pode ser aprovada neste status.")
    if not material_request.items.exists():
        raise ValidationError("Requisicao sem itens nao pode ser aprovada.")
    if material_request.status == MaterialRequestStatus.DRAFT:
        calculate_material_request(material_request)
        material_request.refresh_from_db()
    material_request.status = MaterialRequestStatus.APPROVED
    material_request.approved_at = timezone.now()
    material_request.save(update_fields=["status", "approved_at", "updated_at"])
    return material_request


@transaction.atomic
def cancel_material_request(material_request: MaterialRequest) -> MaterialRequest:
    material_request = MaterialRequest.objects.select_for_update(of=("self",)).get(pk=material_request.pk)
    if material_request.status == MaterialRequestStatus.FULFILLED:
        raise ValidationError("Requisicao atendida nao pode ser cancelada.")
    material_request.status = MaterialRequestStatus.CANCELLED
    material_request.items.exclude(status=MaterialRequestItemStatus.FULFILLED).update(
        status=MaterialRequestItemStatus.CANCELLED,
        updated_at=timezone.now(),
    )
    material_request.save(update_fields=["status", "updated_at"])
    return material_request


def get_project_stock_location(project: Project) -> StockLocation:
    location = (
        StockLocation.objects.filter(
            project=project,
            location_type=StockLocationType.PROJECT,
            is_active=True,
        )
        .order_by("code", "id")
        .first()
    )
    if not location:
        raise ValidationError("Obra sem almoxarifado. Crie um local de estoque para a obra.")
    return location


def get_work_stock_location(work: Project) -> StockLocation:
    return get_project_stock_location(work)


def get_central_stock_location() -> StockLocation:
    location = (
        StockLocation.objects.filter(
            location_type=StockLocationType.CENTRAL,
            project__isnull=True,
            is_active=True,
        )
        .order_by("code", "id")
        .first()
    )
    if not location:
        raise ValidationError("Estoque central nao cadastrado.")
    return location


def get_stock_balance(material: Material, stock_location: StockLocation) -> Decimal:
    return get_available_balance(material=material, location=stock_location)


def get_available_balance(*, material: Material, location: StockLocation) -> Decimal:
    quantity = (
        StockBalance.objects.filter(material=material, location=location)
        .values_list("quantity", flat=True)
        .first()
    )
    return q_qty(quantity or Decimal("0"))


def _update_request_counters(material_request: MaterialRequest) -> None:
    items = list(material_request.items.all())
    material_request.total_items = len(items)
    material_request.total_items_with_project_stock = sum(1 for item in items if item.suggested_project_usage_quantity > 0)
    material_request.total_items_with_transfer_suggestion = sum(1 for item in items if item.suggested_transfer_quantity > 0)
    material_request.total_items_with_purchase_suggestion = sum(1 for item in items if item.suggested_purchase_quantity > 0)


def _item_status(*, project_usage: Decimal, transfer: Decimal, purchase: Decimal) -> str:
    used_sources = sum(1 for value in (project_usage, transfer, purchase) if value > 0)
    if used_sources > 1:
        return MaterialRequestItemStatus.MIXED
    if project_usage > 0:
        return MaterialRequestItemStatus.AVAILABLE_ON_PROJECT
    if transfer > 0:
        return MaterialRequestItemStatus.TRANSFER_SUGGESTED
    if purchase > 0:
        return MaterialRequestItemStatus.PURCHASE_SUGGESTED
    return MaterialRequestItemStatus.PENDING


def _is_indivisible_unit(unit) -> bool:
    code = (unit.code or "").strip().upper()
    name = (unit.name or "").strip().upper()
    return code in INDIVISIBLE_UNIT_CODES or name in INDIVISIBLE_UNIT_CODES
