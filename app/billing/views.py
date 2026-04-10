from decimal import Decimal

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.db.models import Max
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from billing.forms import (
    ContractedLineAddForm,
    ExtraLineForm,
    MeasurementLineForm,
    MeasurementPeriodForm,
    SettlementForm,
)
from billing.models import MeasurementLine, MeasurementLineKind, MeasurementPeriod, WorkflowStatus
from billing.services.measurement_lines import add_or_merge_contracted_line
from billing.services.measurement_calc import (
    compute_incc_factor,
    compute_period_totals,
    finalize_period,
    get_item_cumulative,
)
from core.forms import ProjectLocationTemplateForm
from core.models import Project
from core.services import apply_location_template_to_project
from exports.models import ExportStatus
from exports.services import generate_sienge_master, generate_sienge_snapshot, generate_xlsx_boletim
from pricing.models import AdjustmentApplyTo
from utils.paths import get_template_path


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
    project = get_object_or_404(Project.objects.select_related("client", "location_template"), pk=project_id)
    template_form = ProjectLocationTemplateForm(instance=project, prefix="project")

    if request.method == "POST":
        action = request.POST.get("action")
        if action in {"save_location_template", "apply_location_template"}:
            template_form = ProjectLocationTemplateForm(request.POST, instance=project, prefix="project")
            if template_form.is_valid():
                project = template_form.save()
                if action == "save_location_template":
                    messages.success(request, "Template de locais atualizado.")
                else:
                    try:
                        result = apply_location_template_to_project(project=project)
                    except ValidationError as exc:
                        for message in exc.messages:
                            messages.error(request, message)
                    else:
                        messages.success(
                            request,
                            (
                                f"Template '{result['template_name']}' aplicado: "
                                f"{result['created_count']} locais criados, "
                                f"{result['skipped_count']} ignorados."
                            ),
                        )
                return redirect("billing:project_detail", project_id=project.id)

    periods = project.measurement_periods.all().order_by("number")
    locations = project.locations.all().order_by("order_index", "code")
    sienge_template_available = True
    sienge_template_error = ""
    try:
        get_template_path("sienge_template.xlsx")
    except FileNotFoundError:
        sienge_template_available = False
        sienge_template_error = "Template ausente: assets/templates/sienge_template.xlsx."
    return render(
        request,
        "billing/project_detail.html",
        {
            "project": project,
            "periods": periods,
            "locations": locations,
            "template_form": template_form,
            "sienge_template_available": sienge_template_available,
            "sienge_template_error": sienge_template_error,
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
    boletim_template_available = True
    boletim_template_error = ""
    sienge_template_available = True
    sienge_template_error = ""
    try:
        get_template_path("boletim_template.xlsx")
    except FileNotFoundError:
        boletim_template_available = False
        boletim_template_error = "Template ausente: assets/templates/boletim_template.xlsx."
    try:
        get_template_path("sienge_template.xlsx")
    except FileNotFoundError:
        sienge_template_available = False
        sienge_template_error = "Template ausente: assets/templates/sienge_template.xlsx."

    period_form = MeasurementPeriodForm(instance=period, prefix="period")
    contracted_form = ContractedLineAddForm(period=period, prefix="contracted")
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
            contracted_form = ContractedLineAddForm(request.POST, period=period, prefix="contracted")
            if contracted_form.is_valid():
                try:
                    _, merged = add_or_merge_contracted_line(
                        period=period,
                        item=contracted_form.cleaned_data["item"],
                        location=contracted_form.cleaned_data.get("location"),
                        qty_period=contracted_form.cleaned_data["qty_period"],
                        application_date=contracted_form.cleaned_data.get("application_date"),
                        note=contracted_form.cleaned_data.get("note", ""),
                        excess_justification=contracted_form.cleaned_data.get("excess_justification", ""),
                        created_by=request.user,
                    )
                except ValidationError as exc:
                    for message in exc.messages:
                        messages.error(request, message)
                else:
                    if merged:
                        messages.success(request, "Quantidade somada a linha existente.")
                    else:
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
                finalized_period = finalize_period(period.id, request.user)
            except ValidationError as exc:
                for message in exc.messages:
                    messages.error(request, message)
            else:
                snapshot_export, _ = generate_sienge_snapshot(finalized_period.id)
                master_export, _ = generate_sienge_master(finalized_period.project_id)
                messages.success(request, "Mediacao finalizada com sucesso.")
                if snapshot_export.status != ExportStatus.OK:
                    messages.warning(
                        request,
                        "A medicao foi finalizada, mas houve falha ao gerar o snapshot Sienge.",
                    )
                if master_export.status != ExportStatus.OK:
                    messages.warning(
                        request,
                        "A medicao foi finalizada, mas houve falha ao atualizar o mestre Sienge.",
                    )
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

    contracted_effective_by_line: dict[int, Decimal] = {}
    contracted_excess_by_line: dict[int, Decimal] = {}
    period_effective_qty_by_item: dict[int, Decimal] = {}

    contracted_lines_by_item: dict[int, list[MeasurementLine]] = {}
    for line in contracted_lines:
        if line.item_id:
            contracted_lines_by_item.setdefault(line.item_id, []).append(line)

    for item_id, item_lines in contracted_lines_by_item.items():
        first_line = item_lines[0]
        previous = get_item_cumulative(period.project, item_id, period.number - 1)
        contracted_qty = (first_line.item.qty_contracted or Decimal("0")) if first_line.item else Decimal("0")
        remaining = contracted_qty - previous
        total_effective = Decimal("0")

        for line in sorted(item_lines, key=lambda current: current.id):
            qty_period = line.qty_period or Decimal("0")
            available = max(remaining, Decimal("0"))
            effective_qty = min(qty_period, available)
            excess_qty = max(qty_period - effective_qty, Decimal("0"))
            remaining -= effective_qty
            total_effective += effective_qty
            contracted_effective_by_line[line.id] = effective_qty
            contracted_excess_by_line[line.id] = excess_qty

        period_effective_qty_by_item[item_id] = total_effective

    contracted_line_stats = []
    for line in contracted_lines:
        if not line.item_id:
            continue
        effective_qty = contracted_effective_by_line.get(line.id, Decimal("0"))
        excess_qty = contracted_excess_by_line.get(line.id, Decimal("0"))
        previous = get_item_cumulative(period.project, line.item_id, period.number - 1)
        accumulated = previous + period_effective_qty_by_item.get(line.item_id, Decimal("0"))
        contracted_qty = line.item.qty_contracted or Decimal("0")
        balance = contracted_qty - accumulated
        percent = (
            (accumulated / contracted_qty * Decimal("100"))
            if contracted_qty > 0
            else Decimal("0")
        )
        unit_price = (line.item.pu_material or Decimal("0")) + (line.item.pu_labor or Decimal("0"))
        period_value = effective_qty * unit_price
        excess_value = excess_qty * unit_price

        contracted_line_stats.append(
            {
                "line": line,
                "effective_qty": effective_qty,
                "excess_qty": excess_qty,
                "previous": previous,
                "accumulated": accumulated,
                "balance": balance,
                "percent": percent,
                "period_value": period_value,
                "excess_value": excess_value,
            }
        )

    contracted_item_balances: dict[str, str] = {}
    for item in contracted_form.fields["item"].queryset:
        previous = get_item_cumulative(period.project, item.id, period.number - 1)
        consumed_in_period = period_effective_qty_by_item.get(item.id, Decimal("0"))
        saldo_antes = (item.qty_contracted or Decimal("0")) - previous - consumed_in_period
        contracted_item_balances[str(item.id)] = str(max(saldo_antes, Decimal("0")))

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
            "boletim_template_available": boletim_template_available,
            "boletim_template_error": boletim_template_error,
            "sienge_template_available": sienge_template_available,
            "sienge_template_error": sienge_template_error,
            "period_form": period_form,
            "contracted_form": contracted_form,
            "extra_form": extra_form,
            "settlement_form": settlement_form,
            "contracted_line_stats": contracted_line_stats,
            "contracted_item_balances": contracted_item_balances,
            "extra_lines": extra_lines,
            "settlements": settlements,
            "total_material": total_material,
            "total_labor": total_labor,
            "total_total": total_total,
            "indexed_preview": indexed_preview,
            "incc_data": incc_data,
            "incc_error": incc_error,
            "today_iso": timezone.localdate().isoformat(),
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

    histories = line.histories.select_related("created_by").order_by(
        "-application_date",
        "-created_at",
        "-id",
    )

    return render(
        request,
        "billing/measurement_line_form.html",
        {
            "form": form,
            "histories": histories,
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
def measurement_export_xlsx_view(request, measurement_id: int):
    period = get_object_or_404(MeasurementPeriod, pk=measurement_id)
    export_record, xlsx_path = generate_xlsx_boletim(period.id)

    if export_record.status != ExportStatus.OK or xlsx_path is None:
        messages.error(
            request,
            f"Falha ao gerar boletim Excel: {export_record.error_message or 'erro desconhecido'}",
        )
        return redirect("billing:measurement_detail", measurement_id=period.id)

    return FileResponse(
        open(xlsx_path, "rb"),
        as_attachment=True,
        filename=xlsx_path.name,
    )


@staff_member_required
def measurement_export_sienge_snapshot_view(request, measurement_id: int):
    period = get_object_or_404(MeasurementPeriod, pk=measurement_id)
    export_record, snapshot_path = generate_sienge_snapshot(period.id)

    if export_record.status != ExportStatus.OK or snapshot_path is None:
        messages.error(
            request,
            f"Falha ao gerar snapshot Sienge: {export_record.error_message or 'erro desconhecido'}",
        )
        return redirect("billing:measurement_detail", measurement_id=period.id)

    return FileResponse(
        open(snapshot_path, "rb"),
        as_attachment=True,
        filename=snapshot_path.name,
    )


@staff_member_required
def project_export_sienge_master_view(request, project_id: int):
    project = get_object_or_404(Project, pk=project_id)
    export_record, master_path = generate_sienge_master(project.id)

    if export_record.status != ExportStatus.OK or master_path is None:
        messages.error(
            request,
            f"Falha ao gerar mestre Sienge: {export_record.error_message or 'erro desconhecido'}",
        )
        return redirect("billing:project_detail", project_id=project.id)

    return FileResponse(
        open(master_path, "rb"),
        as_attachment=True,
        filename=master_path.name,
    )
