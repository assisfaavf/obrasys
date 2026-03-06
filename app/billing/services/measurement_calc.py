from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
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


def get_item_cumulative(project, item_id: int, up_to_period_number: int) -> Decimal:
    if up_to_period_number <= 0:
        return Decimal("0")

    qty = (
        MeasurementLine.objects.filter(
            period__project=project,
            line_kind=MeasurementLineKind.CONTRACTED,
            item_id=item_id,
            period__number__lte=up_to_period_number,
        )
        .exclude(period__workflow_status=WorkflowStatus.CANCELLED)
        .aggregate(total=Coalesce(Sum("qty_period"), Decimal("0")))
        .get("total")
    )
    return qty or Decimal("0")


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

    factor = (Decimal(ref_value) / Decimal(base_value)).quantize(FACTOR_Q, rounding=ROUND_HALF_UP)
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

    period_qty_by_item: dict[int, Decimal] = {}
    for line in lines:
        if line.line_kind == MeasurementLineKind.CONTRACTED and line.item_id:
            period_qty_by_item[line.item_id] = period_qty_by_item.get(line.item_id, Decimal("0")) + (
                line.qty_period or Decimal("0")
            )

    for line in lines:
        if line.line_kind == MeasurementLineKind.CONTRACTED:
            qty = line.qty_period or Decimal("0")
            if qty < 0:
                errors.append(f"Linha {line.id}: qty_period deve ser >= 0.")
                continue
            if not line.item_id:
                errors.append(f"Linha {line.id}: item obrigatorio para CONTRACTED.")
                continue

            previous = get_item_cumulative(period.project, line.item_id, period.number - 1)
            period_item_qty = period_qty_by_item.get(line.item_id, Decimal("0"))
            accumulated = previous + period_item_qty
            contracted_qty = (line.item.qty_contracted if line.item else Decimal("0")) or Decimal("0")

            overflow_happens_now = previous <= contracted_qty and accumulated > contracted_qty
            if overflow_happens_now and not (line.justification or "").strip():
                errors.append(
                    f"Linha {line.id}: justificativa obrigatoria para excedente "
                    f"(acumulado {accumulated} > contratado {contracted_qty})."
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
