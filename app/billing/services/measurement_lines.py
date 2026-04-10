from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from billing.models import MeasurementLine, MeasurementLineHistory, MeasurementLineKind, MeasurementPeriod
from billing.services.measurement_calc import refresh_period_excess, split_contracted_and_excess
from catalog.models import BudgetItemAdditionalMaterial

QTY_Q = Decimal("0.001")
GENERATED_EXCESS_JUSTIFICATION = "Gerado automaticamente por material adicional."


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


def _q_qty(value: Decimal) -> Decimal:
    return (value or Decimal("0")).quantize(QTY_Q, rounding=ROUND_HALF_UP)


def _create_history(
    *,
    line: MeasurementLine,
    quantity_added: Decimal,
    application_date,
    note: str = "",
    created_by=None,
) -> None:
    quantity_added = _q_qty(quantity_added)
    if quantity_added <= 0:
        return

    MeasurementLineHistory.objects.create(
        line=line,
        quantity_added=quantity_added,
        application_date=application_date,
        note=(note or "").strip(),
        created_by=created_by,
    )


def _generated_note(parent_line: MeasurementLine, relation: BudgetItemAdditionalMaterial) -> str:
    parts = [f"Gerado por material adicional de {parent_line.item}"]
    if (relation.note or "").strip():
        parts.append((relation.note or "").strip())
    return " | ".join(parts)[:255]


def _prepare_manual_contracted_line(
    line: MeasurementLine,
    *,
    note: str,
    excess_justification: str,
    use_additional_materials: bool,
    additional_materials_base_qty: Decimal,
) -> MeasurementLine:
    line.line_kind = MeasurementLineKind.CONTRACTED
    line.justification = ""
    line.note = (note or "").strip()
    line.generated_from_line = None
    line.is_generated_additional = False
    line.use_additional_materials = bool(use_additional_materials)
    line.additional_materials_base_qty = _q_qty(additional_materials_base_qty)
    line.excess_justification = (excess_justification or "").strip()
    _, excess_qty, _ = split_contracted_and_excess(line)
    line.excess_qty = excess_qty
    if line.excess_qty == 0:
        line.excess_justification = ""
    return line


def _active_additional_materials(parent_line: MeasurementLine) -> list[BudgetItemAdditionalMaterial]:
    if not parent_line.item_id:
        return []

    return list(
        BudgetItemAdditionalMaterial.objects.select_related("additional_item")
        .filter(
            parent_item_id=parent_line.item_id,
            is_active=True,
            additional_item__is_active=True,
        )
        .order_by("additional_item__eap_code", "id")
    )


def _save_generated_line(
    *,
    parent_line: MeasurementLine,
    relation: BudgetItemAdditionalMaterial,
    generated_line: MeasurementLine | None,
    generated_qty: Decimal,
) -> MeasurementLine:
    generated_line = generated_line or MeasurementLine(
        period=parent_line.period,
        line_kind=MeasurementLineKind.CONTRACTED,
        item=relation.additional_item,
        generated_from_line=parent_line,
        is_generated_additional=True,
    )
    generated_line.period = parent_line.period
    generated_line.line_kind = MeasurementLineKind.CONTRACTED
    generated_line.item = relation.additional_item
    generated_line.location = parent_line.location
    generated_line.generated_from_line = parent_line
    generated_line.extra_description = ""
    generated_line.extra_unit = None
    generated_line.extra_pu_material = Decimal("0")
    generated_line.extra_pu_labor = Decimal("0")
    generated_line.qty_period = generated_qty
    generated_line.justification = ""
    generated_line.note = _generated_note(parent_line, relation)
    generated_line.is_generated_additional = True
    generated_line.use_additional_materials = False
    generated_line.additional_materials_base_qty = Decimal("0")
    _, excess_qty, _ = split_contracted_and_excess(generated_line)
    generated_line.excess_qty = excess_qty
    generated_line.excess_justification = GENERATED_EXCESS_JUSTIFICATION if excess_qty > 0 else ""
    generated_line.save()
    return generated_line


def sync_generated_additional_lines(
    *,
    parent_line: MeasurementLine,
    application_date=None,
    created_by=None,
    history_delta_base_qty: Decimal | None = None,
) -> None:
    if parent_line.is_generated_additional:
        raise ValidationError("Linhas geradas nao podem sincronizar materiais adicionais.")

    application_date = application_date or timezone.localdate()
    history_delta_base_qty = _q_qty(history_delta_base_qty or Decimal("0"))

    existing_generated = {
        line.item_id: line
        for line in MeasurementLine.objects.select_for_update()
        .select_related("item")
        .filter(generated_from_line=parent_line)
    }

    base_qty = _q_qty(parent_line.additional_materials_base_qty)
    relations = _active_additional_materials(parent_line)

    for relation in relations:
        generated_qty = _q_qty(base_qty * relation.quantity_per_unit)
        generated_line = existing_generated.pop(relation.additional_item_id, None)

        if generated_qty <= 0:
            if generated_line is not None:
                generated_line.delete()
            continue

        generated_line = _save_generated_line(
            parent_line=parent_line,
            relation=relation,
            generated_line=generated_line,
            generated_qty=generated_qty,
        )

        if history_delta_base_qty > 0:
            _create_history(
                line=generated_line,
                quantity_added=history_delta_base_qty * relation.quantity_per_unit,
                application_date=application_date,
                note=generated_line.note,
                created_by=created_by,
            )

    for generated_line in existing_generated.values():
        generated_line.delete()

    refresh_period_excess(parent_line.period_id)


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
    use_additional_materials: bool = False,
    created_by=None,
) -> tuple[MeasurementLine, bool]:
    if qty_period is None or qty_period <= 0:
        raise ValidationError("quantity_added deve ser > 0.")

    application_date = application_date or timezone.localdate()
    qty_period = _q_qty(qty_period)

    existing_line = (
        MeasurementLine.objects.select_for_update()
        .select_related("period__project", "item")
        .filter(
            period=period,
            line_kind=MeasurementLineKind.CONTRACTED,
            item=item,
            location=location,
            is_generated_additional=False,
        )
        .first()
    )

    if existing_line is None:
        line = MeasurementLine(
            period=period,
            item=item,
            location=location,
            qty_period=qty_period,
        )
        _prepare_manual_contracted_line(
            line,
            note=note,
            excess_justification=excess_justification,
            use_additional_materials=use_additional_materials,
            additional_materials_base_qty=qty_period if use_additional_materials else Decimal("0"),
        )
        line.save()
        _create_history(
            line=line,
            quantity_added=qty_period,
            application_date=application_date,
            note=note,
            created_by=created_by,
        )
        sync_generated_additional_lines(
            parent_line=line,
            application_date=application_date,
            created_by=created_by,
            history_delta_base_qty=qty_period if use_additional_materials else Decimal("0"),
        )
        line.refresh_from_db()
        return line, False

    existing_line.qty_period = _q_qty((existing_line.qty_period or Decimal("0")) + qty_period)
    existing_line.note = _merge_text(existing_line.note, note)
    existing_line.excess_justification = _merge_text(
        existing_line.excess_justification,
        excess_justification,
    )
    new_base_qty = existing_line.additional_materials_base_qty or Decimal("0")
    if use_additional_materials:
        new_base_qty = _q_qty(new_base_qty + qty_period)
    _prepare_manual_contracted_line(
        existing_line,
        note=existing_line.note,
        excess_justification=existing_line.excess_justification,
        use_additional_materials=new_base_qty > 0,
        additional_materials_base_qty=new_base_qty,
    )
    existing_line.save()
    _create_history(
        line=existing_line,
        quantity_added=qty_period,
        application_date=application_date,
        note=note,
        created_by=created_by,
    )
    sync_generated_additional_lines(
        parent_line=existing_line,
        application_date=application_date,
        created_by=created_by,
        history_delta_base_qty=qty_period if use_additional_materials else Decimal("0"),
    )
    existing_line.refresh_from_db()
    return existing_line, True


@transaction.atomic
def update_contracted_line(
    *,
    line: MeasurementLine,
    item,
    location,
    qty_period: Decimal,
    note: str = "",
    excess_justification: str = "",
    use_additional_materials: bool = False,
    created_by=None,
) -> MeasurementLine:
    if line.is_generated_additional:
        raise ValidationError("Linhas geradas por material adicional nao podem ser editadas manualmente.")
    if qty_period is None or qty_period < 0:
        raise ValidationError("qty_period deve ser >= 0.")

    line.item = item
    line.location = location
    line.qty_period = _q_qty(qty_period)
    _prepare_manual_contracted_line(
        line,
        note=note,
        excess_justification=excess_justification,
        use_additional_materials=use_additional_materials,
        additional_materials_base_qty=line.qty_period if use_additional_materials else Decimal("0"),
    )
    line.save()
    sync_generated_additional_lines(
        parent_line=line,
        application_date=timezone.localdate(),
        created_by=created_by,
        history_delta_base_qty=None,
    )
    line.refresh_from_db()
    return line
