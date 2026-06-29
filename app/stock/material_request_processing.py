from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from stock.material_request import get_central_stock_location, get_project_stock_location, q_qty
from stock.models import (
    MaterialRequest,
    MaterialRequestItem,
    MaterialRequestItemStatus,
    MaterialRequestStatus,
    PurchaseRequest,
    PurchaseRequestItem,
    PurchaseRequestStatus,
    StockBalance,
    StockMovementType,
)
from stock.services import register_stock_movement


@dataclass
class MaterialRequestProcessingResult:
    transfers_created: int = 0
    purchase_request: PurchaseRequest | None = None
    purchase_items_created: int = 0


@transaction.atomic
def process_material_request(material_request: MaterialRequest, *, user=None) -> MaterialRequestProcessingResult:
    material_request = (
        MaterialRequest.objects.select_for_update(of=("self",))
        .select_related("project", "requested_by")
        .get(pk=material_request.pk)
    )
    validate_material_request_can_be_processed(material_request)
    validate_current_central_stock_for_transfer(material_request)

    result = MaterialRequestProcessingResult()
    result.transfers_created = generate_transfers_from_material_request(material_request, user=user)
    result.purchase_request, result.purchase_items_created = generate_purchase_request_from_material_request(
        material_request,
        user=user,
    )
    mark_material_request_processed(material_request)
    return result


def generate_transfers_from_material_request(material_request: MaterialRequest, *, user=None) -> int:
    central_location = get_central_stock_location()
    project_location = get_project_stock_location(material_request.project)
    created_count = 0

    for item in _items_for_processing(material_request).filter(suggested_transfer_quantity__gt=0):
        if item.transfer_movement_id:
            raise ValidationError(f"Item {item.id}: transferencia ja gerada.")

        movement = register_stock_movement(
            material=item.material,
            location=central_location,
            target_location=project_location,
            movement_type=StockMovementType.TRANSFER,
            quantity=item.suggested_transfer_quantity,
            note=f"Transferencia gerada pela requisicao de materiais #{material_request.pk}",
            created_by=user or material_request.requested_by,
        )
        item.transfer_movement = movement
        _update_item_processing_status(item)
        item.save(update_fields=["transfer_movement", "status", "updated_at"])
        created_count += 1

    return created_count


def generate_purchase_request_from_material_request(
    material_request: MaterialRequest,
    *,
    user=None,
) -> tuple[PurchaseRequest | None, int]:
    purchase_items = list(_items_for_processing(material_request).filter(suggested_purchase_quantity__gt=0))
    if not purchase_items:
        return None, 0

    if hasattr(material_request, "purchase_request"):
        purchase_request = material_request.purchase_request
    else:
        purchase_request = PurchaseRequest.objects.create(
            project=material_request.project,
            material_request=material_request,
            status=PurchaseRequestStatus.GENERATED,
            created_by=user or material_request.requested_by,
            note=f"Pedido gerado pela requisicao de materiais #{material_request.pk}",
        )

    created_count = 0
    for item in purchase_items:
        if hasattr(item, "purchase_request_item"):
            raise ValidationError(f"Item {item.id}: pedido de compra ja gerado.")

        PurchaseRequestItem.objects.create(
            purchase_request=purchase_request,
            material_request_item=item,
            material=item.material,
            quantity=item.suggested_purchase_quantity,
            unit=item.material.unit,
            note=item.note,
        )
        _update_item_processing_status(item)
        item.save(update_fields=["status", "updated_at"])
        created_count += 1

    purchase_request.total_items = purchase_request.items.count()
    purchase_request.save(update_fields=["total_items", "updated_at"])
    return purchase_request, created_count


def validate_material_request_can_be_processed(material_request: MaterialRequest) -> None:
    if material_request.status == MaterialRequestStatus.DRAFT:
        raise ValidationError("Requisicao em rascunho deve ser calculada e aprovada antes do processamento.")
    if material_request.status == MaterialRequestStatus.CANCELLED:
        raise ValidationError("Requisicao cancelada nao pode ser processada.")
    if material_request.status == MaterialRequestStatus.FULFILLED:
        raise ValidationError("Requisicao ja processada.")
    if material_request.status != MaterialRequestStatus.APPROVED:
        raise ValidationError("Somente requisicoes aprovadas podem ser processadas.")
    if not material_request.items.exists():
        raise ValidationError("Requisicao sem itens nao pode ser processada.")

    for item in material_request.items.select_related("material"):
        if not item.material_id:
            raise ValidationError(f"Item {item.id}: material obrigatorio.")
        if item.requested_quantity is None or item.requested_quantity <= 0:
            raise ValidationError(f"Item {item.id}: quantidade solicitada invalida.")
        if _item_requires_action(item) and _item_already_processed(item):
            raise ValidationError(f"Item {item.id}: acao ja gerada.")
        if _item_requires_action(item) and not _suggestions_were_calculated(item):
            raise ValidationError(f"Item {item.id}: sugestoes ainda nao calculadas.")


def validate_current_central_stock_for_transfer(material_request: MaterialRequest) -> None:
    central_location = get_central_stock_location()
    for item in material_request.items.select_related("material").filter(suggested_transfer_quantity__gt=0):
        balance = (
            StockBalance.objects.select_for_update()
            .filter(material=item.material, location=central_location)
            .first()
        )
        current_quantity = q_qty(balance.quantity if balance else Decimal("0"))
        if current_quantity < item.suggested_transfer_quantity:
            raise ValidationError(
                f"Saldo central insuficiente para {item.material}. Recalcule a requisicao antes de processar."
            )


def mark_material_request_processed(material_request: MaterialRequest) -> MaterialRequest:
    for item in material_request.items.select_related("material"):
        _update_item_processing_status(item)
        item.save(update_fields=["status", "updated_at"])

    material_request.status = MaterialRequestStatus.FULFILLED
    material_request.save(update_fields=["status", "updated_at"])
    return material_request


def _items_for_processing(material_request: MaterialRequest):
    return material_request.items.select_for_update(of=("self",)).select_related("material", "material__unit")


def _item_requires_action(item: MaterialRequestItem) -> bool:
    return item.suggested_transfer_quantity > 0 or item.suggested_purchase_quantity > 0


def _item_already_processed(item: MaterialRequestItem) -> bool:
    return bool(item.transfer_movement_id) or hasattr(item, "purchase_request_item")


def _suggestions_were_calculated(item: MaterialRequestItem) -> bool:
    total_suggested = (
        item.suggested_project_usage_quantity
        + item.suggested_transfer_quantity
        + item.suggested_purchase_quantity
    )
    return total_suggested == item.requested_quantity


def _update_item_processing_status(item: MaterialRequestItem) -> None:
    has_transfer = bool(item.transfer_movement_id)
    has_purchase = hasattr(item, "purchase_request_item")

    if has_transfer and has_purchase:
        item.status = MaterialRequestItemStatus.TRANSFER_PURCHASE_GENERATED
    elif has_transfer:
        item.status = MaterialRequestItemStatus.TRANSFER_GENERATED
    elif has_purchase:
        item.status = MaterialRequestItemStatus.PURCHASE_GENERATED
    elif not _item_requires_action(item):
        item.status = MaterialRequestItemStatus.FULFILLED
