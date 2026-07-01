from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from stock.models import Material, StockBalance, StockLocation, StockMovement, StockMovementType
from stock.purchase_import import normalize_description
from stock.services import register_stock_movement

QTY_Q = Decimal("0.001")
INDIVISIBLE_UNITS = {"un", "unidade", "vara", "lata", "peca", "pc", "pç", "cx"}


def q_qty(value) -> Decimal:
    try:
        return Decimal(str(value or "0").replace(",", ".")).quantize(QTY_Q)
    except (InvalidOperation, ValueError):
        raise ValidationError("Quantidade invalida.")


def is_indivisible_unit(unit) -> bool:
    if not unit:
        return False
    return normalize_description(unit.code) in INDIVISIBLE_UNITS or normalize_description(unit.name) in INDIVISIBLE_UNITS


def validate_stock_transfer(
    *,
    material: Material,
    origin: StockLocation,
    destination: StockLocation,
    quantity,
) -> Decimal:
    if not material:
        raise ValidationError("Material obrigatorio.")
    if not origin:
        raise ValidationError("Local de origem obrigatorio.")
    if not destination:
        raise ValidationError("Local de destino obrigatorio.")
    if origin.id == destination.id:
        raise ValidationError("Origem e destino nao podem ser o mesmo local.")
    if not origin.is_active:
        raise ValidationError("Local de origem deve estar ativo.")
    if not destination.is_active:
        raise ValidationError("Local de destino deve estar ativo.")

    quantity = q_qty(quantity)
    if quantity <= 0:
        raise ValidationError("Quantidade deve ser maior que zero.")
    if is_indivisible_unit(material.unit) and quantity != quantity.to_integral_value():
        raise ValidationError("Unidade indivisivel nao permite quantidade quebrada.")

    balance = StockBalance.objects.filter(material=material, location=origin).first()
    available = q_qty(balance.quantity if balance else Decimal("0"))
    if quantity > available:
        raise ValidationError(f"Saldo insuficiente na origem. Disponivel: {available}. Solicitado: {quantity}.")
    return quantity


@transaction.atomic
def transfer_stock_between_locations(
    *,
    material: Material,
    origin: StockLocation,
    destination: StockLocation,
    quantity,
    user=None,
    note: str | None = None,
) -> StockMovement:
    balance = StockBalance.objects.select_for_update().filter(material=material, location=origin).first()
    available = q_qty(balance.quantity if balance else Decimal("0"))
    quantity = validate_stock_transfer(
        material=material,
        origin=origin,
        destination=destination,
        quantity=quantity,
    )
    if quantity > available:
        raise ValidationError(f"Saldo insuficiente na origem. Disponivel: {available}. Solicitado: {quantity}.")

    movement_note = f"Transferencia rapida: {origin} -> {destination}"
    note = (note or "").strip()
    if note:
        movement_note = f"{movement_note} | {note}"

    return register_stock_movement(
        material=material,
        location=origin,
        target_location=destination,
        movement_type=StockMovementType.TRANSFER,
        quantity=quantity,
        note=movement_note,
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )
