from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django import template

register = template.Library()


def _to_decimal(value):
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _format_br_number(value: Decimal, places: int = 2) -> str:
    quant = Decimal("1").scaleb(-places)
    normalized = value.quantize(quant, rounding=ROUND_HALF_UP)
    formatted = f"{normalized:,.{places}f}"
    return formatted.replace(",", "_").replace(".", ",").replace("_", ".")


@register.filter(name="br_decimal")
def br_decimal(value, places=2):
    decimal_value = _to_decimal(value)
    if decimal_value is None:
        return "-"

    try:
        places_int = int(places)
    except (TypeError, ValueError):
        places_int = 2

    return _format_br_number(decimal_value, places=max(0, places_int))


@register.filter(name="br_money")
def br_money(value):
    decimal_value = _to_decimal(value)
    if decimal_value is None:
        return "R$ -"
    return f"R$ {_format_br_number(decimal_value, places=2)}"

