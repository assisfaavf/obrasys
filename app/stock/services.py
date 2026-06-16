from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from stock.models import Material, StockBalance, StockLocation, StockMovement, StockMovementType

QTY_Q = Decimal("0.001")


def _q_qty(value: Decimal) -> Decimal:
    try:
        decimal_value = Decimal(str(value or "0").replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ValidationError("Quantidade invalida.")
    return decimal_value.quantize(QTY_Q)


def _movement_delta(movement_type: str, quantity: Decimal) -> Decimal:
    if movement_type in {StockMovementType.IN, StockMovementType.ADJUST_POSITIVE}:
        return quantity
    if movement_type in {StockMovementType.OUT, StockMovementType.ADJUST_NEGATIVE}:
        return -quantity
    raise ValidationError("Tipo de movimentacao ainda nao implementado para saldo.")


def calculate_balance_after(
    *,
    current_quantity: Decimal,
    movement_type: str,
    quantity: Decimal,
) -> Decimal:
    quantity = _q_qty(quantity)
    if quantity <= 0:
        raise ValidationError("Quantidade movimentada deve ser > 0.")
    return _q_qty((current_quantity or Decimal("0")) + _movement_delta(movement_type, quantity))


@transaction.atomic
def register_stock_movement(
    *,
    material: Material,
    location: StockLocation,
    movement_type: str,
    quantity: Decimal,
    note: str = "",
    created_by=None,
) -> StockMovement:
    quantity = _q_qty(quantity)
    if quantity <= 0:
        raise ValidationError("Quantidade movimentada deve ser > 0.")

    balance, _ = (
        StockBalance.objects.select_for_update()
        .get_or_create(material=material, location=location, defaults={"quantity": Decimal("0")})
    )
    new_quantity = calculate_balance_after(
        current_quantity=balance.quantity,
        movement_type=movement_type,
        quantity=quantity,
    )
    if new_quantity < 0:
        raise ValidationError("Movimentacao nao pode deixar saldo de estoque negativo.")

    balance.quantity = new_quantity
    balance.save(update_fields=["quantity", "updated_at"])

    return StockMovement.objects.create(
        material=material,
        location=location,
        movement_type=movement_type,
        quantity=quantity,
        balance_after=new_quantity,
        note=(note or "").strip(),
        created_by=created_by,
    )
