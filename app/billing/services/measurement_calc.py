from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import DecimalField, ExpressionWrapper, F, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from billing.models import (
    FinancialStatus,
    MeasurementLine,
    MeasurementLineKind,
    MeasurementPeriod,
    MeasurementSettlement,
    SettlementStatus,
    WorkflowStatus,
)
from pricing.models import AdjustmentApplyTo, PriceIndexValue, ProjectPriceAdjustment

MONEY_Q = Decimal("0.01")
FACTOR_Q = Decimal("0.000001")


def _q_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_Q, rounding=ROUND_HALF_UP)


def _format_br_decimal(value: Decimal, places: int = 2) -> str:
    quant = Decimal("1").scaleb(-places)
    normalized = value.quantize(quant, rounding=ROUND_HALF_UP)
    formatted = f"{normalized:,.{places}f}"
    return formatted.replace(",", "_").replace(".", ",").replace("_", ".")


def get_item_cumulative(project, item_id: int, up_to_period_number: int) -> Decimal:
    if up_to_period_number <= 0:
        return Decimal("0")

    effective_qty_expr = ExpressionWrapper(
        F("qty_period") - Coalesce(F("excess_qty"), Value(Decimal("0"))),
        output_field=DecimalField(max_digits=14, decimal_places=3),
    )
    qty = (
        MeasurementLine.objects.filter(
            period__project=project,
            line_kind=MeasurementLineKind.CONTRACTED,
            item_id=item_id,
            period__number__lte=up_to_period_number,
        )
        .exclude(period__workflow_status=WorkflowStatus.CANCELLED)
        .aggregate(total=Coalesce(Sum(effective_qty_expr), Decimal("0")))
        .get("total")
    )
    return qty or Decimal("0")


def split_contracted_and_excess(line: MeasurementLine) -> tuple[Decimal, Decimal, Decimal]:
    qty_period = line.qty_period or Decimal("0")
    if line.line_kind != MeasurementLineKind.CONTRACTED or not line.item_id:
        return qty_period, Decimal("0"), Decimal("0")

    previous = get_item_cumulative(line.period.project, line.item_id, line.period.number - 1)
    contracted_qty = (line.item.qty_contracted if line.item else Decimal("0")) or Decimal("0")
    saldo_antes = contracted_qty - previous

    existing_lines = MeasurementLine.objects.filter(
        period=line.period,
        line_kind=MeasurementLineKind.CONTRACTED,
        item_id=line.item_id,
    )
    if line.pk:
        existing_lines = existing_lines.exclude(pk=line.pk)

    # Estimate available saldo for this line assuming it is the last entered line.
    for existing in existing_lines.order_by("id"):
        existing_qty = existing.qty_period or Decimal("0")
        consume = min(existing_qty, max(saldo_antes, Decimal("0")))
        saldo_antes -= consume

    available_qty = max(saldo_antes, Decimal("0"))
    contracted_effective_qty = min(qty_period, available_qty)
    excess_qty = max(qty_period - available_qty, Decimal("0"))
    return contracted_effective_qty, excess_qty, saldo_antes


def refresh_period_excess(period_id: int) -> None:
    lines = list(
        MeasurementLine.objects.select_related("period__project", "item")
        .filter(period_id=period_id, line_kind=MeasurementLineKind.CONTRACTED)
        .order_by("item_id", "id")
    )
    grouped: dict[int, list[MeasurementLine]] = {}
    for line in lines:
        if not line.item_id or line.item is None:
            continue
        grouped.setdefault(line.item_id, []).append(line)

    for item_id, item_lines in grouped.items():
        first_line = item_lines[0]
        previous = get_item_cumulative(first_line.period.project, item_id, first_line.period.number - 1)
        contracted_qty = (first_line.item.qty_contracted or Decimal("0")) if first_line.item else Decimal("0")
        remaining = contracted_qty - previous

        for line in item_lines:
            qty_period = line.qty_period or Decimal("0")
            available_qty = max(remaining, Decimal("0"))
            effective_qty = min(qty_period, available_qty)
            excess_qty = max(qty_period - effective_qty, Decimal("0"))
            remaining -= effective_qty

            excess_justification = (line.excess_justification or "").strip()
            if excess_qty == 0:
                excess_justification = ""

            if line.excess_qty != excess_qty or (line.excess_justification or "") != excess_justification:
                MeasurementLine.objects.filter(pk=line.pk).update(
                    excess_qty=excess_qty,
                    excess_justification=excess_justification,
                )
                line.excess_qty = excess_qty
                line.excess_justification = excess_justification


def compute_period_totals(period_id: int) -> tuple[Decimal, Decimal, Decimal]:
    lines = MeasurementLine.objects.select_related("item").filter(period_id=period_id)

    total_material = Decimal("0")
    total_labor = Decimal("0")

    for line in lines:
        qty = line.qty_period or Decimal("0")
        if line.line_kind == MeasurementLineKind.CONTRACTED:
            pu_material = (line.item.pu_material if line.item else Decimal("0")) or Decimal("0")
            pu_labor = (line.item.pu_labor if line.item else Decimal("0")) or Decimal("0")
        else:
            pu_material = line.extra_pu_material or Decimal("0")
            pu_labor = line.extra_pu_labor or Decimal("0")

        total_material += qty * pu_material
        total_labor += qty * pu_labor

    total_material = _q_money(total_material)
    total_labor = _q_money(total_labor)
    total = _q_money(total_material + total_labor)
    return total_material, total_labor, total


def compute_incc_factor(project, ref_month):
    adjustment = (
        ProjectPriceAdjustment.objects.select_related("price_index")
        .filter(project=project, is_active=True)
        .order_by("-id")
        .first()
    )

    if not adjustment:
        return {
            "code": "",
            "base_month": None,
            "ref_month": None,
            "factor": Decimal("1.0"),
            "apply_to": AdjustmentApplyTo.TOTAL,
            "adjustment": None,
        }

    base_value = (
        PriceIndexValue.objects.filter(
            price_index=adjustment.price_index,
            ref_month=adjustment.base_month,
        )
        .values_list("value", flat=True)
        .first()
    )
    ref_value = (
        PriceIndexValue.objects.filter(
            price_index=adjustment.price_index,
            ref_month=ref_month,
        )
        .values_list("value", flat=True)
        .first()
    )

    if base_value is None or ref_value is None:
        raise ValidationError(
            "INCC ativo sem valores para mes base e/ou mes de referencia."
        )
    if base_value <= 0:
        raise ValidationError("Valor base do INCC deve ser maior que zero.")
    if ref_value <= 0:
        raise ValidationError("Valor de referencia do INCC deve ser maior que zero.")

    raw_factor = Decimal(ref_value) / Decimal(base_value)
    # Business rule: INCC correction must not reduce measurement totals.
    factor = raw_factor if raw_factor >= Decimal("1") else (Decimal("1") / raw_factor)
    factor = factor.quantize(FACTOR_Q, rounding=ROUND_HALF_UP)
    return {
        "code": adjustment.price_index.code,
        "base_month": adjustment.base_month,
        "ref_month": ref_month,
        "factor": factor,
        "apply_to": adjustment.apply_to,
        "adjustment": adjustment,
    }


def validate_finalize(period_id: int) -> list[str]:
    period = MeasurementPeriod.objects.select_related("project").get(pk=period_id)
    lines = list(
        MeasurementLine.objects.select_related("item")
        .filter(period=period)
        .order_by("id")
    )

    errors: list[str] = []

    if not lines:
        errors.append("Nao pode finalizar sem linhas.")
        return errors

    for line in lines:
        if line.line_kind == MeasurementLineKind.CONTRACTED:
            qty = line.qty_period or Decimal("0")
            if qty < 0:
                errors.append(f"Linha {line.id}: qty_period deve ser >= 0.")
                continue
            if not line.item_id:
                errors.append(f"Linha {line.id}: item obrigatorio para CONTRACTED.")
                continue

            excess_qty = line.excess_qty or Decimal("0")
            if excess_qty > 0 and not (line.excess_justification or "").strip():
                errors.append(
                    f"Linha {line.id}: justificativa obrigatoria para excedente "
                    f"(excedente {_format_br_decimal(excess_qty, 2)})."
                )

        if line.line_kind == MeasurementLineKind.EXTRA:
            if not (line.justification or "").strip():
                errors.append(f"Linha {line.id}: justificativa obrigatoria para item EXTRA.")

    try:
        compute_incc_factor(period.project, period.ref_month)
    except ValidationError as exc:
        for message in exc.messages:
            errors.append(message)

    return errors


def _compute_indexed_total(
    *,
    total_material: Decimal,
    total_labor: Decimal,
    apply_to: str,
    factor: Decimal,
) -> Decimal:
    if apply_to == AdjustmentApplyTo.MATERIAL:
        indexed = (total_material * factor) + total_labor
    elif apply_to == AdjustmentApplyTo.LABOR:
        indexed = total_material + (total_labor * factor)
    else:
        indexed = (total_material + total_labor) * factor
    return _q_money(indexed)


def finalize_period(period_id: int, user=None):
    with transaction.atomic():
        period = (
            MeasurementPeriod.objects.select_for_update()
            .select_related("project")
            .get(pk=period_id)
        )

        if period.workflow_status != WorkflowStatus.DRAFT:
            raise ValidationError("Somente periodos em DRAFT podem ser finalizados.")

        refresh_period_excess(period.id)
        errors = validate_finalize(period.id)
        if errors:
            raise ValidationError(errors)

        total_material, total_labor, total_total = compute_period_totals(period.id)
        incc_data = compute_incc_factor(period.project, period.ref_month)
        indexed_total = _compute_indexed_total(
            total_material=total_material,
            total_labor=total_labor,
            apply_to=incc_data["apply_to"],
            factor=incc_data["factor"],
        )

        period.total_material_snapshot = total_material
        period.total_labor_snapshot = total_labor
        period.total_total_snapshot = total_total
        period.total_indexed_snapshot = indexed_total
        period.index_code_snapshot = incc_data["code"]
        period.index_base_month_snapshot = incc_data["base_month"]
        period.index_ref_month_snapshot = incc_data["ref_month"]
        period.index_factor_snapshot = incc_data["factor"]
        period.workflow_status = WorkflowStatus.FINALIZED
        period.finalized_at = timezone.now()
        period.save(
            update_fields=[
                "total_material_snapshot",
                "total_labor_snapshot",
                "total_total_snapshot",
                "total_indexed_snapshot",
                "index_code_snapshot",
                "index_base_month_snapshot",
                "index_ref_month_snapshot",
                "index_factor_snapshot",
                "workflow_status",
                "finalized_at",
            ]
        )

        update_financial_status(period.id)
        period.refresh_from_db()
        from billing.services.workflow import record_measurement_workflow_history

        record_measurement_workflow_history(
            period,
            WorkflowStatus.DRAFT,
            WorkflowStatus.FINALIZED,
            user=user,
        )
        return period


def update_financial_status(period_id: int):
    period = MeasurementPeriod.objects.get(pk=period_id)
    paid = (
        MeasurementSettlement.objects.filter(
            period=period,
            status=SettlementStatus.ACTIVE,
        )
        .aggregate(total=Coalesce(Sum("amount"), Decimal("0")))
        .get("total")
        or Decimal("0")
    )

    total = period.total_indexed_snapshot
    if total <= 0:
        total = period.total_total_snapshot

    if paid == 0:
        new_status = FinancialStatus.OPEN
    elif paid < total:
        new_status = FinancialStatus.PARTIALLY_PAID
    else:
        new_status = FinancialStatus.PAID

    if period.financial_status != new_status:
        period.financial_status = new_status
        period.save(update_fields=["financial_status"])
    return new_status
