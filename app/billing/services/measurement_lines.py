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
    source_line: MeasurementLine | None = None,
    source_item_snapshot: str = "",
    is_generated_additional_entry: bool = False,
    additional_materials_quantity: Decimal | None = None,
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
        source_line=source_line,
        source_item_snapshot=(source_item_snapshot or "").strip(),
        is_generated_additional_entry=is_generated_additional_entry,
        additional_materials_quantity=(
            Decimal("0")
            if is_generated_additional_entry
            else _q_qty(additional_materials_quantity if additional_materials_quantity is not None else Decimal("0"))
        ),
        created_by=created_by,
    )


def _generated_note(parent_line: MeasurementLine) -> str:
    if parent_line.item:
        return f"Gerado automaticamente por: {parent_line.item.description}"
    return "Gerado automaticamente por material adicional."


def _manual_histories(line: MeasurementLine) -> list[MeasurementLineHistory]:
    return list(
        line.histories.filter(is_generated_additional_entry=False).order_by(
            "-application_date",
            "-created_at",
            "-id",
        )
    )


def _sync_line_quantity_from_history(
    *,
    line: MeasurementLine,
    created_by=None,
) -> MeasurementLine:
    histories = _manual_histories(line)
    total_qty = _q_qty(sum((history.quantity_added for history in histories), Decimal("0")))
    additional_qty = _q_qty(
        sum(
            (history.additional_materials_quantity for history in histories),
            Decimal("0"),
        )
    )
    latest_note = next((history.note for history in histories if (history.note or "").strip()), "")

    line.qty_period = total_qty
    _prepare_manual_contracted_line(
        line,
        note=latest_note,
        excess_justification=line.excess_justification,
        use_additional_materials=additional_qty > 0,
        additional_materials_base_qty=additional_qty,
    )
    line.save()
    sync_generated_additional_lines(parent_line=line, created_by=created_by)
    line.refresh_from_db()
    return line


def _reconcile_manual_histories_with_total(
    *,
    line: MeasurementLine,
    desired_qty: Decimal,
    note: str = "",
    use_additional_materials: bool = False,
    created_by=None,
) -> MeasurementLine:
    desired_qty = _q_qty(desired_qty)
    if desired_qty <= 0:
        raise ValidationError("qty_period deve ser > 0.")

    histories = _manual_histories(line)
    if not histories:
        _create_history(
            line=line,
            quantity_added=desired_qty,
            application_date=timezone.localdate(),
            note=note,
            additional_materials_quantity=desired_qty if use_additional_materials else Decimal("0"),
            created_by=created_by,
        )
        return _sync_line_quantity_from_history(line=line, created_by=created_by)

    current_qty = _q_qty(sum((history.quantity_added for history in histories), Decimal("0")))
    for history in histories:
        desired_additional_qty = history.quantity_added if use_additional_materials else Decimal("0")
        if history.additional_materials_quantity != desired_additional_qty:
            history.additional_materials_quantity = desired_additional_qty
            history.save(update_fields=["additional_materials_quantity"])

    qty_delta = desired_qty - current_qty
    latest_history = histories[0]
    if note.strip():
        latest_history.note = note.strip()
        latest_history.save(update_fields=["note"])

    if qty_delta > 0:
        latest_history.quantity_added = _q_qty(latest_history.quantity_added + qty_delta)
        if use_additional_materials:
            latest_history.additional_materials_quantity = latest_history.quantity_added
            latest_history.save(update_fields=["quantity_added", "additional_materials_quantity"])
        else:
            latest_history.save(update_fields=["quantity_added"])
    elif qty_delta < 0:
        qty_to_reduce = -qty_delta
        for history in histories:
            if history.pk == latest_history.pk and note.strip():
                history.note = note.strip()
            if history.quantity_added > qty_to_reduce:
                history.quantity_added = _q_qty(history.quantity_added - qty_to_reduce)
                history.additional_materials_quantity = history.quantity_added if use_additional_materials else Decimal("0")
                update_fields = ["quantity_added", "additional_materials_quantity"]
                if history.pk == latest_history.pk and note.strip():
                    update_fields.append("note")
                history.save(update_fields=update_fields)
                qty_to_reduce = Decimal("0")
                break
            qty_to_reduce = _q_qty(qty_to_reduce - history.quantity_added)
            history.delete()
            if qty_to_reduce <= 0:
                break
        if qty_to_reduce > 0:
            raise ValidationError("qty_period deve ser > 0.")

    return _sync_line_quantity_from_history(line=line, created_by=created_by)


@transaction.atomic
def update_history_entry(
    *,
    history: MeasurementLineHistory,
    quantity_added: Decimal,
    application_date,
    note: str = "",
    uses_additional_materials: bool = False,
    created_by=None,
) -> MeasurementLineHistory:
    if history.is_generated_additional_entry:
        raise ValidationError("Historicos gerados automaticamente nao podem ser editados manualmente.")

    history.quantity_added = _q_qty(quantity_added)
    history.application_date = application_date or timezone.localdate()
    history.note = (note or "").strip()
    history.additional_materials_quantity = history.quantity_added if uses_additional_materials else Decimal("0")
    history.save()

    line = history.line
    _sync_line_quantity_from_history(line=line, created_by=created_by)
    history.refresh_from_db()
    return history


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


def _save_generated_line(
    *,
    period: MeasurementPeriod,
    item,
    location,
    generated_line: MeasurementLine | None,
    generated_qty: Decimal,
) -> MeasurementLine:
    generated_line = generated_line or MeasurementLine(
        period=period,
        line_kind=MeasurementLineKind.CONTRACTED,
        item=item,
        is_generated_additional=True,
    )
    generated_line.period = period
    generated_line.line_kind = MeasurementLineKind.CONTRACTED
    generated_line.item = item
    generated_line.location = location
    generated_line.generated_from_line = None
    generated_line.extra_description = ""
    generated_line.extra_unit = None
    generated_line.extra_pu_material = Decimal("0")
    generated_line.extra_pu_labor = Decimal("0")
    generated_line.qty_period = generated_qty
    generated_line.justification = ""
    generated_line.note = ""
    generated_line.is_generated_additional = True
    generated_line.use_additional_materials = False
    generated_line.additional_materials_base_qty = Decimal("0")
    _, excess_qty, _ = split_contracted_and_excess(generated_line)
    generated_line.excess_qty = excess_qty
    generated_line.excess_justification = GENERATED_EXCESS_JUSTIFICATION if excess_qty > 0 else ""
    generated_line.save()
    return generated_line


def _source_item_snapshot(parent_line: MeasurementLine) -> str:
    if parent_line.item:
        return f"{parent_line.item.eap_code} - {parent_line.item.description}"[:255]
    return f"Linha {parent_line.id}"[:255]


def _source_application_date(parent_line: MeasurementLine):
    latest_history = parent_line.histories.order_by("-application_date", "-created_at", "-id").first()
    if latest_history:
        return latest_history.application_date
    return timezone.localdate()


def _source_location(parent_line: MeasurementLine):
    return parent_line.location


def _active_additional_materials_by_parent(parent_item_ids: list[int]) -> dict[int, list[BudgetItemAdditionalMaterial]]:
    if not parent_item_ids:
        return {}

    relations = (
        BudgetItemAdditionalMaterial.objects.select_related("additional_item")
        .filter(
            parent_item_id__in=parent_item_ids,
            is_active=True,
            additional_item__is_active=True,
        )
        .order_by("parent_item_id", "additional_item__eap_code", "id")
    )
    relation_map: dict[int, list[BudgetItemAdditionalMaterial]] = {}
    for relation in relations:
        relation_map.setdefault(relation.parent_item_id, []).append(relation)
    return relation_map


@transaction.atomic
def rebuild_generated_additional_lines(
    *,
    period: MeasurementPeriod,
    created_by=None,
) -> None:
    existing_generated_lines = list(
        MeasurementLine.objects.select_for_update()
        .filter(
            period=period,
            line_kind=MeasurementLineKind.CONTRACTED,
            is_generated_additional=True,
        )
        .order_by("id")
    )
    retained_by_key: dict[tuple[int | None, int | None], MeasurementLine] = {}
    duplicated_lines: list[MeasurementLine] = []
    for generated_line in existing_generated_lines:
        key = (generated_line.item_id, generated_line.location_id)
        if key in retained_by_key:
            duplicated_lines.append(generated_line)
            continue
        retained_by_key[key] = generated_line

    for generated_line in existing_generated_lines:
        generated_line.histories.filter(is_generated_additional_entry=True).delete()

    source_lines = list(
        MeasurementLine.objects.select_related("item", "location")
        .filter(
            period=period,
            line_kind=MeasurementLineKind.CONTRACTED,
            is_generated_additional=False,
            additional_materials_base_qty__gt=0,
        )
        .order_by("id")
    )
    relation_map = _active_additional_materials_by_parent(
        [line.item_id for line in source_lines if line.item_id]
    )
    grouped_contributions: dict[tuple[int | None, int | None], list[dict]] = {}

    for source_line in source_lines:
        if not source_line.item_id:
            continue
        source_location = _source_location(source_line)
        for relation in relation_map.get(source_line.item_id, []):
            generated_qty = _q_qty(
                (source_line.additional_materials_base_qty or Decimal("0")) * relation.quantity_per_unit
            )
            if generated_qty <= 0:
                continue
            key = (relation.additional_item_id, source_location.id if source_location else None)
            grouped_contributions.setdefault(key, []).append(
                {
                    "item": relation.additional_item,
                    "location": source_location,
                    "quantity_added": generated_qty,
                    "application_date": _source_application_date(source_line),
                    "note": _generated_note(source_line),
                    "source_line": source_line,
                    "source_item_snapshot": _source_item_snapshot(source_line),
                }
            )

    for key, contributions in grouped_contributions.items():
        generated_line = retained_by_key.pop(key, None)
        total_qty = _q_qty(sum((entry["quantity_added"] for entry in contributions), Decimal("0")))
        if total_qty <= 0:
            continue

        generated_line = _save_generated_line(
            period=period,
            item=contributions[0]["item"],
            location=contributions[0]["location"],
            generated_line=generated_line,
            generated_qty=total_qty,
        )

        for entry in contributions:
            _create_history(
                line=generated_line,
                quantity_added=entry["quantity_added"],
                application_date=entry["application_date"],
                note=entry["note"],
                source_line=entry["source_line"],
                source_item_snapshot=entry["source_item_snapshot"],
                is_generated_additional_entry=True,
                created_by=created_by,
            )

    for generated_line in duplicated_lines:
        generated_line.delete()
    for generated_line in retained_by_key.values():
        generated_line.delete()

    refresh_period_excess(period.id)


def sync_generated_additional_lines(
    *,
    parent_line: MeasurementLine,
    application_date=None,
    created_by=None,
    history_delta_base_qty: Decimal | None = None,
) -> None:
    if parent_line.is_generated_additional:
        raise ValidationError("Linhas geradas nao podem sincronizar materiais adicionais.")

    rebuild_generated_additional_lines(period=parent_line.period, created_by=created_by)


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
            additional_materials_quantity=qty_period if use_additional_materials else Decimal("0"),
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
        additional_materials_quantity=qty_period if use_additional_materials else Decimal("0"),
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
    line.excess_justification = (excess_justification or "").strip()
    line.save(update_fields=["item", "location", "excess_justification"])
    _reconcile_manual_histories_with_total(
        line=line,
        desired_qty=qty_period,
        note=note,
        use_additional_materials=use_additional_materials,
        created_by=created_by,
    )
    line.refresh_from_db()
    return line
