from decimal import Decimal

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.db.models import Max
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render

from billing.forms import ExtraLineForm, MeasurementLineForm, MeasurementPeriodForm, SettlementForm
from billing.models import MeasurementLine, MeasurementLineKind, MeasurementPeriod, WorkflowStatus
from billing.services.measurement_calc import (
    compute_incc_factor,
    compute_period_totals,
    finalize_period,
    get_item_cumulative,
)
from core.models import Project
from exports.models import ExportStatus
from exports.services import generate_pdf_boletim
from pricing.models import AdjustmentApplyTo


def _compute_indexed_preview(
    *,
    total_material: Decimal,
    total_labor: Decimal,
    apply_to: str,
    factor: Decimal,
) -> Decimal:
    if apply_to == AdjustmentApplyTo.MATERIAL:
        return (total_material * factor) + total_labor
    if apply_to == AdjustmentApplyTo.LABOR:
        return total_material + (total_labor * factor)
    return (total_material + total_labor) * factor


@staff_member_required
def project_list_view(request):
    projects = Project.objects.select_related("client").order_by("name")
    return render(request, "billing/project_list.html", {"projects": projects})


@staff_member_required
def project_detail_view(request, project_id: int):
    project = get_object_or_404(Project.objects.select_related("client"), pk=project_id)
    periods = project.measurement_periods.all().order_by("number")
    return render(
        request,
        "billing/project_detail.html",
        {
            "project": project,
            "periods": periods,
        },
    )


@staff_member_required
def measurement_new_view(request, project_id: int):
    project = get_object_or_404(Project, pk=project_id)

    if request.method == "POST":
        form = MeasurementPeriodForm(request.POST)
        if form.is_valid():
            next_number = (
                MeasurementPeriod.objects.filter(project=project).aggregate(max_number=Max("number"))[
                    "max_number"
                ]
                or 0
            ) + 1
            period = form.save(commit=False)
            period.project = project
            period.number = next_number
            period.save()
            messages.success(request, f"Mediacao {period.number} criada.")
            return redirect("billing:measurement_detail", measurement_id=period.id)
    else:
        form = MeasurementPeriodForm()

    return render(
        request,
        "billing/measurement_new.html",
        {
            "project": project,
            "form": form,
        },
    )


@staff_member_required
def measurement_detail_view(request, measurement_id: int):
    period = get_object_or_404(MeasurementPeriod.objects.select_related("project"), pk=measurement_id)
    is_draft = period.workflow_status == WorkflowStatus.DRAFT

    period_form = MeasurementPeriodForm(instance=period, prefix="period")
    contracted_form = MeasurementLineForm(period=period, prefix="contracted")
    extra_form = ExtraLineForm(period=period, prefix="extra")
    settlement_form = SettlementForm(prefix="settlement")

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "update_period":
            if not is_draft:
                messages.error(request, "Periodo nao esta em DRAFT.")
                return redirect("billing:measurement_detail", measurement_id=period.id)
            period_form = MeasurementPeriodForm(request.POST, instance=period, prefix="period")
            if period_form.is_valid():
                period_form.save()
                messages.success(request, "Dados da medicao atualizados.")
                return redirect("billing:measurement_detail", measurement_id=period.id)

        elif action == "add_contracted":
            if not is_draft:
                messages.error(request, "Periodo nao esta em DRAFT.")
                return redirect("billing:measurement_detail", measurement_id=period.id)
            contracted_form = MeasurementLineForm(request.POST, period=period, prefix="contracted")
            if contracted_form.is_valid():
                contracted_form.save()
                messages.success(request, "Linha contratada adicionada.")
                return redirect("billing:measurement_detail", measurement_id=period.id)

        elif action == "add_extra":
            if not is_draft:
                messages.error(request, "Periodo nao esta em DRAFT.")
                return redirect("billing:measurement_detail", measurement_id=period.id)
            extra_form = ExtraLineForm(request.POST, period=period, prefix="extra")
            if extra_form.is_valid():
                extra_form.save()
                messages.success(request, "Linha extra adicionada.")
                return redirect("billing:measurement_detail", measurement_id=period.id)

        elif action == "finalize":
            if not is_draft:
                messages.error(request, "Periodo nao esta em DRAFT.")
                return redirect("billing:measurement_detail", measurement_id=period.id)
            try:
                finalize_period(period.id, request.user)
            except ValidationError as exc:
                for message in exc.messages:
                    messages.error(request, message)
            else:
                messages.success(request, "Mediacao finalizada com sucesso.")
            return redirect("billing:measurement_detail", measurement_id=period.id)

        elif action == "add_settlement":
            settlement_form = SettlementForm(request.POST, prefix="settlement")
            if settlement_form.is_valid():
                settlement = settlement_form.save(commit=False)
                settlement.period = period
                settlement.save()
                messages.success(request, "Pagamento/abatimento registrado.")
                return redirect("billing:measurement_detail", measurement_id=period.id)

        elif action == "recalc":
            messages.success(request, "Recalculo concluido.")
            return redirect("billing:measurement_detail", measurement_id=period.id)

    lines = list(
        period.lines.select_related("item", "location", "extra_unit").order_by("id")
    )
    contracted_lines = [line for line in lines if line.line_kind == MeasurementLineKind.CONTRACTED]
    extra_lines = [line for line in lines if line.line_kind == MeasurementLineKind.EXTRA]

    period_qty_by_item: dict[int, Decimal] = {}
    for line in contracted_lines:
        if line.item_id:
            period_qty_by_item[line.item_id] = period_qty_by_item.get(line.item_id, Decimal("0")) + (
                line.qty_period or Decimal("0")
            )

    contracted_line_stats = []
    for line in contracted_lines:
        if not line.item_id:
            continue
        previous = get_item_cumulative(period.project, line.item_id, period.number - 1)
        accumulated = previous + period_qty_by_item.get(line.item_id, Decimal("0"))
        contracted_qty = line.item.qty_contracted or Decimal("0")
        balance = contracted_qty - accumulated
        percent = (
            (accumulated / contracted_qty * Decimal("100"))
            if contracted_qty > 0
            else Decimal("0")
        )
        unit_price = (line.item.pu_material or Decimal("0")) + (line.item.pu_labor or Decimal("0"))
        period_value = (line.qty_period or Decimal("0")) * unit_price

        contracted_line_stats.append(
            {
                "line": line,
                "previous": previous,
                "accumulated": accumulated,
                "balance": balance,
                "percent": percent,
                "period_value": period_value,
            }
        )

    settlements = period.settlements.order_by("event_date", "id")

    total_material, total_labor, total_total = compute_period_totals(period.id)
    indexed_preview = total_total
    incc_data = {
        "code": "",
        "base_month": None,
        "ref_month": None,
        "factor": Decimal("1.0"),
        "apply_to": AdjustmentApplyTo.TOTAL,
    }
    incc_error = ""
    try:
        incc_data = compute_incc_factor(period.project, period.ref_month)
        indexed_preview = _compute_indexed_preview(
            total_material=total_material,
            total_labor=total_labor,
            apply_to=incc_data["apply_to"],
            factor=incc_data["factor"],
        )
    except ValidationError as exc:
        incc_error = "; ".join(exc.messages)

    return render(
        request,
        "billing/measurement_detail.html",
        {
            "period": period,
            "is_draft": is_draft,
            "period_form": period_form,
            "contracted_form": contracted_form,
            "extra_form": extra_form,
            "settlement_form": settlement_form,
            "contracted_line_stats": contracted_line_stats,
            "extra_lines": extra_lines,
            "settlements": settlements,
            "total_material": total_material,
            "total_labor": total_labor,
            "total_total": total_total,
            "indexed_preview": indexed_preview,
            "incc_data": incc_data,
            "incc_error": incc_error,
        },
    )


@staff_member_required
def measurement_line_edit_view(request, line_id: int):
    line = get_object_or_404(MeasurementLine.objects.select_related("period"), pk=line_id)
    period = line.period

    if period.workflow_status != WorkflowStatus.DRAFT:
        messages.error(request, "Periodo nao esta em DRAFT.")
        return redirect("billing:measurement_detail", measurement_id=period.id)

    if line.line_kind == MeasurementLineKind.EXTRA:
        form_class = ExtraLineForm
        title = "Editar linha extra"
    else:
        form_class = MeasurementLineForm
        title = "Editar linha contratada"

    if request.method == "POST":
        form = form_class(request.POST, instance=line, period=period)
        if form.is_valid():
            form.save()
            messages.success(request, "Linha atualizada.")
            return redirect("billing:measurement_detail", measurement_id=period.id)
    else:
        form = form_class(instance=line, period=period)

    return render(
        request,
        "billing/measurement_line_form.html",
        {
            "form": form,
            "period": period,
            "line": line,
            "title": title,
        },
    )


@staff_member_required
def measurement_line_delete_view(request, line_id: int):
    line = get_object_or_404(MeasurementLine.objects.select_related("period"), pk=line_id)
    period = line.period

    if request.method == "POST":
        try:
            line.delete()
            messages.success(request, "Linha removida.")
        except ValidationError as exc:
            for message in exc.messages:
                messages.error(request, message)
        return redirect("billing:measurement_detail", measurement_id=period.id)

    return render(
        request,
        "billing/measurement_line_delete.html",
        {
            "line": line,
            "period": period,
        },
    )


@staff_member_required
def measurement_export_pdf_view(request, measurement_id: int):
    period = get_object_or_404(MeasurementPeriod, pk=measurement_id)
    layout = request.GET.get("layout", "landscape")
    export_record, pdf_path = generate_pdf_boletim(period.id, layout=layout)

    if export_record.status != ExportStatus.OK or pdf_path is None:
        messages.error(
            request,
            f"Falha ao gerar PDF ({layout}): {export_record.error_message or 'erro desconhecido'}",
        )
        return redirect("billing:measurement_detail", measurement_id=period.id)

    return FileResponse(
        open(pdf_path, "rb"),
        as_attachment=True,
        filename=pdf_path.name,
    )
