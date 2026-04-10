from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from billing.models import MeasurementLine, MeasurementLineHistory, MeasurementLineKind, MeasurementPeriod
from billing.services.measurement_calc import refresh_period_excess, split_contracted_and_excess


def _merge_text(existing_value: str, new_value: str, separator: str = " | ") -> str:
    existing_value = (existing_value or "").strip()
    new_value = (new_value or "").strip()

    if not new_value:
        return existing_value
    if not existing_value:
        return new_value
    if new_value == existing_value:
        return existing_value
    return f"{existing_value}{separator}{new_value}"


@transaction.atomic
def add_or_merge_contracted_line(
    *,
    period: MeasurementPeriod,
    item,
    location,
    qty_period: Decimal,
    application_date=None,
    note: str = "",
    excess_justification: str = "",
    created_by=None,
) -> tuple[MeasurementLine, bool]:
    if qty_period is None or qty_period <= 0:
        raise ValidationError("quantity_added deve ser > 0.")

    application_date = application_date or timezone.localdate()

    existing_line = (
        MeasurementLine.objects.select_for_update()
        .select_related("period__project", "item")
        .filter(
            period=period,
            line_kind=MeasurementLineKind.CONTRACTED,
            item=item,
            location=location,
        )
        .first()
    )

    if existing_line is None:
        line = MeasurementLine(
            period=period,
            line_kind=MeasurementLineKind.CONTRACTED,
            item=item,
            location=location,
            qty_period=qty_period,
            note=(note or "").strip(),
            justification="",
            excess_justification=(excess_justification or "").strip(),
        )
        _, excess_qty, _ = split_contracted_and_excess(line)
        line.excess_qty = excess_qty
        line.save()
        MeasurementLineHistory.objects.create(
            line=line,
            quantity_added=qty_period,
            application_date=application_date,
            note=(note or "").strip(),
            created_by=created_by,
        )
        refresh_period_excess(period.id)
        line.refresh_from_db()
        return line, False

    existing_line.qty_period = (existing_line.qty_period or Decimal("0")) + qty_period
    existing_line.note = _merge_text(existing_line.note, note)
    existing_line.excess_justification = _merge_text(
        existing_line.excess_justification,
        excess_justification,
    )
    _, excess_qty, _ = split_contracted_and_excess(existing_line)
    existing_line.excess_qty = excess_qty
    existing_line.save()
    MeasurementLineHistory.objects.create(
        line=existing_line,
        quantity_added=qty_period,
        application_date=application_date,
        note=(note or "").strip(),
        created_by=created_by,
    )
    refresh_period_excess(period.id)
    existing_line.refresh_from_db()
    return existing_line, True
