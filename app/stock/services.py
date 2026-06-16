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
    if movement_type in {
        StockMovementType.IN,
        StockMovementType.ADJUST_POSITIVE,
        StockMovementType.INITIAL_IN,
        StockMovementType.PURCHASE_IN,
        StockMovementType.MEASUREMENT_OUT_REVERSAL,
    }:
        return quantity
    if movement_type in {
        StockMovementType.OUT,
        StockMovementType.ADJUST_NEGATIVE,
        StockMovementType.TRANSFER,
        StockMovementType.MEASUREMENT_OUT,
    }:
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
    target_location: StockLocation | None = None,
    note: str = "",
    created_by=None,
) -> StockMovement:
    quantity = _q_qty(quantity)
    if quantity <= 0:
        raise ValidationError("Quantidade movimentada deve ser > 0.")
    if movement_type == StockMovementType.TRANSFER:
        if target_location is None:
            raise ValidationError("Transferencia exige local de destino.")
        if target_location.id == location.id:
            raise ValidationError("Local de origem e destino devem ser diferentes.")

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

    if movement_type == StockMovementType.TRANSFER:
        target_balance, _ = (
            StockBalance.objects.select_for_update()
            .get_or_create(material=material, location=target_location, defaults={"quantity": Decimal("0")})
        )
        target_balance.quantity = _q_qty(target_balance.quantity + quantity)
        target_balance.save(update_fields=["quantity", "updated_at"])

    return StockMovement.objects.create(
        material=material,
        location=location,
        target_location=target_location,
        movement_type=movement_type,
        quantity=quantity,
        balance_after=new_quantity,
        note=(note or "").strip(),
        created_by=created_by,
    )
