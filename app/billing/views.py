from decimal import Decimal

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count
from django.db.models import Max
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from billing.forms import (
    BulkContractedLineCommonForm,
    BulkContractedLineItemFormSet,
    ContractedLineAddForm,
    ContractedLineEditForm,
    EnvironmentMaterialApplicationForm,
    ExtraLineForm,
    MeasurementLineHistoryEditForm,
    MeasurementPeriodForm,
    PredefinedEnvironmentDisciplineForm,
    PredefinedEnvironmentForm,
    PredefinedEnvironmentMaterialForm,
    SettlementForm,
)
from billing.models import (
    MeasurementLine,
    MeasurementLineHistory,
    MeasurementLineKind,
    MeasurementPeriod,
    PredefinedEnvironment,
    PredefinedEnvironmentDiscipline,
    PredefinedEnvironmentMaterial,
    WorkflowStatus,
)
from billing.services.measurement_lines import (
    add_or_merge_contracted_line,
    update_contracted_line,
    update_history_entry,
)
from catalog.models import BudgetItemAdditionalMaterial
from billing.services.measurement_calc import (
    compute_incc_factor,
    compute_period_totals,
    finalize_period,
    get_item_cumulative,
)
from billing.services.workflow import transition_measurement_status
from core.forms import ProjectLocationTemplateForm
from core.models import Project
from core.services import apply_location_template_to_project
from exports.models import ExportStatus
from exports.services import generate_sienge_master, generate_sienge_snapshot, generate_xlsx_boletim
from pricing.models import AdjustmentApplyTo
from utils.paths import get_template_path


WORKFLOW_ACTIONS = {
    "workflow_send": WorkflowStatus.SENT,
    "workflow_review": WorkflowStatus.IN_REVIEW,
    "workflow_authorize": WorkflowStatus.AUTHORIZED,
    "workflow_reject": WorkflowStatus.REJECTED,
    "workflow_cancel": WorkflowStatus.CANCELLED,
    "workflow_reopen_rejected": WorkflowStatus.DRAFT,
    "workflow_reopen_finalized": WorkflowStatus.DRAFT,
    "workflow_return_finalized": WorkflowStatus.FINALIZED,
}

WORKFLOW_NOTE_FIELDS = {
    "workflow_send": "sent_note",
    "workflow_review": "review_note",
    "workflow_authorize": "authorization_note",
    "workflow_reject": "rejection_reason",
    "workflow_cancel": "cancellation_reason",
    "workflow_reopen_rejected": "workflow_note",
    "workflow_reopen_finalized": "workflow_note",
    "workflow_return_finalized": "workflow_note",
}


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


def _build_additional_materials_map(items) -> dict[str, list[dict[str, str]]]:
    item_ids = [item.id for item in items]
    if not item_ids:
        return {}

    relations = (
        BudgetItemAdditionalMaterial.objects.select_related("additional_item", "additional_item__unit")
        .filter(
            parent_item_id__in=item_ids,
            is_active=True,
            additional_item__is_active=True,
        )
        .order_by("parent_item_id", "additional_item__eap_code", "id")
    )

    summary_map: dict[str, list[dict[str, str]]] = {}
    for relation in relations:
        summary_map.setdefault(str(relation.parent_item_id), []).append(
            {
                "label": f"{relation.additional_item.eap_code} - {relation.additional_item.description}",
                "quantity_per_unit": str(relation.quantity_per_unit),
            }
        )
    return summary_map


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
def predefined_environment_list_view(request, project_id: int):
    project = get_object_or_404(Project, pk=project_id)
    environments = (
        PredefinedEnvironment.objects.filter(project=project)
        .annotate(
            discipline_count=Count("disciplines", distinct=True),
            material_count=Count("disciplines__materials", distinct=True),
        )
        .order_by("name")
    )
    return render(
        request,
        "billing/predefined_environment_list.html",
        {
            "project": project,
            "environments": environments,
        },
    )


@staff_member_required
def predefined_environment_create_view(request, project_id: int):
    project = get_object_or_404(Project, pk=project_id)
    if request.method == "POST":
        form = PredefinedEnvironmentForm(request.POST, project=project)
        if form.is_valid():
            environment = form.save()
            messages.success(request, "O ambiente predefinido foi cadastrado com sucesso.")
            return redirect("billing:predefined_environment_detail", environment_id=environment.id)
    else:
        form = PredefinedEnvironmentForm(project=project)

    return render(
        request,
        "billing/predefined_environment_form.html",
        {
            "project": project,
            "form": form,
            "title": "Novo ambiente predefinido",
        },
    )


@staff_member_required
def predefined_environment_edit_view(request, environment_id: int):
    environment = get_object_or_404(PredefinedEnvironment.objects.select_related("project"), pk=environment_id)
    if request.method == "POST":
        form = PredefinedEnvironmentForm(request.POST, instance=environment, project=environment.project)
        if form.is_valid():
            form.save()
            messages.success(request, "Ambiente predefinido atualizado.")
            return redirect("billing:predefined_environment_detail", environment_id=environment.id)
    else:
        form = PredefinedEnvironmentForm(instance=environment, project=environment.project)

    return render(
        request,
        "billing/predefined_environment_form.html",
        {
            "project": environment.project,
            "environment": environment,
            "form": form,
            "title": "Editar ambiente predefinido",
        },
    )


@staff_member_required
def predefined_environment_detail_view(request, environment_id: int):
    environment = get_object_or_404(
        PredefinedEnvironment.objects.select_related("project").prefetch_related(
            "disciplines__discipline",
            "disciplines__materials__item",
            "disciplines__materials__item__unit",
        ),
        pk=environment_id,
    )
    discipline_form = PredefinedEnvironmentDisciplineForm(environment=environment)
    material_forms: dict[int, PredefinedEnvironmentMaterialForm] = {}

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add_discipline":
            discipline_form = PredefinedEnvironmentDisciplineForm(request.POST, environment=environment)
            if discipline_form.is_valid():
                discipline_form.save()
                messages.success(request, "Disciplina adicionada ao ambiente.")
                return redirect("billing:predefined_environment_detail", environment_id=environment.id)
        elif action == "add_material":
            discipline = get_object_or_404(
                PredefinedEnvironmentDiscipline.objects.select_related("environment"),
                pk=request.POST.get("environment_discipline_id"),
                environment=environment,
            )
            material_form = PredefinedEnvironmentMaterialForm(request.POST, environment_discipline=discipline)
            material_forms[discipline.id] = material_form
            if material_form.is_valid():
                material_form.save()
                messages.success(request, "Material padrao adicionado.")
                return redirect("billing:predefined_environment_detail", environment_id=environment.id)
        elif action == "toggle_discipline":
            discipline = get_object_or_404(
                PredefinedEnvironmentDiscipline,
                pk=request.POST.get("environment_discipline_id"),
                environment=environment,
            )
            discipline.is_active = not discipline.is_active
            discipline.save(update_fields=["is_active", "updated_at"])
            messages.success(request, "Status da disciplina atualizado.")
            return redirect("billing:predefined_environment_detail", environment_id=environment.id)
        elif action == "toggle_material":
            material = get_object_or_404(
                PredefinedEnvironmentMaterial.objects.select_related("environment_discipline"),
                pk=request.POST.get("material_id"),
                environment_discipline__environment=environment,
            )
            material.is_active = not material.is_active
            material.save(update_fields=["is_active", "updated_at"])
            messages.success(request, "Status do material padrao atualizado.")
            return redirect("billing:predefined_environment_detail", environment_id=environment.id)
        elif action == "update_material":
            material = get_object_or_404(
                PredefinedEnvironmentMaterial.objects.select_related("environment_discipline"),
                pk=request.POST.get("material_id"),
                environment_discipline__environment=environment,
            )
            material.default_quantity = request.POST.get("default_quantity")
            material.order_index = request.POST.get("order_index") or 0
            material.is_active = request.POST.get("is_active") == "on"
            try:
                material.save()
            except ValidationError as exc:
                for message in exc.messages:
                    messages.error(request, message)
            else:
                messages.success(request, "Material padrao atualizado.")
                return redirect("billing:predefined_environment_detail", environment_id=environment.id)

    disciplines = list(environment.disciplines.select_related("discipline").prefetch_related("materials__item__unit"))
    discipline_sections = []
    for discipline in disciplines:
        material_forms.setdefault(
            discipline.id,
            PredefinedEnvironmentMaterialForm(environment_discipline=discipline),
        )
        discipline_sections.append({"discipline": discipline, "material_form": material_forms[discipline.id]})

    return render(
        request,
        "billing/predefined_environment_detail.html",
        {
            "environment": environment,
            "project": environment.project,
            "discipline_sections": discipline_sections,
            "discipline_form": discipline_form,
        },
    )


@staff_member_required
def predefined_environment_duplicate_view(request, environment_id: int):
    environment = get_object_or_404(
        PredefinedEnvironment.objects.select_related("project").prefetch_related("disciplines__materials"),
        pk=environment_id,
    )
    if request.method != "POST":
        messages.error(request, "Acao invalida para duplicar ambiente.")
        return redirect("billing:predefined_environment_detail", environment_id=environment.id)

    new_name = (request.POST.get("name") or "").strip()
    if not new_name:
        messages.error(request, "Informe o nome do novo ambiente.")
        return redirect("billing:predefined_environment_detail", environment_id=environment.id)

    try:
        with transaction.atomic():
            copy = PredefinedEnvironment.objects.create(
                project=environment.project,
                name=new_name,
                description=environment.description,
                is_active=environment.is_active,
            )
            for discipline in environment.disciplines.all():
                discipline_copy = PredefinedEnvironmentDiscipline.objects.create(
                    environment=copy,
                    discipline=discipline.discipline,
                    is_active=discipline.is_active,
                )
                for material in discipline.materials.all():
                    PredefinedEnvironmentMaterial.objects.create(
                        environment_discipline=discipline_copy,
                        item=material.item,
                        default_quantity=material.default_quantity,
                        order_index=material.order_index,
                        is_active=material.is_active,
                    )
    except ValidationError as exc:
        for message in exc.messages:
            messages.error(request, message)
        return redirect("billing:predefined_environment_detail", environment_id=environment.id)

    messages.success(request, "Ambiente duplicado com sucesso.")
    return redirect("billing:predefined_environment_detail", environment_id=copy.id)


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
                        application_reference=contracted_form.cleaned_data.get("application_reference", ""),
                        note=contracted_form.cleaned_data.get("note", ""),
                        excess_justification=contracted_form.cleaned_data.get("excess_justification", ""),
                        use_additional_materials=contracted_form.cleaned_data.get(
                            "use_additional_materials", False
                        ),
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

        elif action in WORKFLOW_ACTIONS:
            if action == "workflow_reopen_finalized" and request.POST.get("confirm_reopen") != "on":
                messages.error(request, "Confirme a reabertura da medicao finalizada.")
                return redirect("billing:measurement_detail", measurement_id=period.id)

            note_field = WORKFLOW_NOTE_FIELDS[action]
            note = request.POST.get(note_field, "")
            try:
                updated_period = transition_measurement_status(
                    period,
                    WORKFLOW_ACTIONS[action],
                    user=request.user,
                    note=note,
                )
            except ValidationError as exc:
                for message in exc.messages:
                    messages.error(request, message)
            else:
                messages.success(request, f"Status atualizado para {updated_period.workflow_status}.")
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
        period.lines.select_related(
            "item",
            "location",
            "extra_unit",
        ).order_by("id")
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

    contracted_item_additional_materials = _build_additional_materials_map(
        contracted_form.fields["item"].queryset
    )
    has_active_predefined_environments = (
        PredefinedEnvironment.objects.filter(
            project=period.project,
            is_active=True,
            disciplines__is_active=True,
            disciplines__materials__is_active=True,
            disciplines__materials__item__is_active=True,
        )
        .distinct()
        .exists()
    )

    settlements = period.settlements.order_by("event_date", "id")
    workflow_history = period.workflow_history.select_related("changed_by").all()

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
            "contracted_item_additional_materials": contracted_item_additional_materials,
            "has_active_predefined_environments": has_active_predefined_environments,
            "extra_lines": extra_lines,
            "settlements": settlements,
            "workflow_history": workflow_history,
            "can_reopen_finalized": request.user.has_perm("billing.change_measurementperiod"),
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
def measurement_bulk_materials_add_view(request, measurement_id: int):
    period = get_object_or_404(MeasurementPeriod.objects.select_related("project"), pk=measurement_id)

    if period.workflow_status != WorkflowStatus.DRAFT:
        messages.error(request, "Periodo nao esta em DRAFT.")
        return redirect("billing:measurement_detail", measurement_id=period.id)

    if request.method == "POST":
        common_form = BulkContractedLineCommonForm(request.POST, period=period, prefix="bulk")
        item_formset = BulkContractedLineItemFormSet(request.POST, period=period, prefix="items")
        if common_form.is_valid() and item_formset.is_valid():
            try:
                with transaction.atomic():
                    created_count = 0
                    for item_form in item_formset.valid_item_forms():
                        add_or_merge_contracted_line(
                            period=period,
                            item=item_form.cleaned_data["item"],
                            location=common_form.cleaned_data.get("location"),
                            qty_period=item_form.cleaned_data["qty_period"],
                            application_date=common_form.cleaned_data.get("application_date"),
                            application_reference=common_form.cleaned_data.get("application_reference", ""),
                            note=common_form.cleaned_data.get("note", ""),
                            excess_justification=common_form.cleaned_data.get("excess_justification", ""),
                            use_additional_materials=False,
                            created_by=request.user,
                        )
                        created_count += 1
            except ValidationError as exc:
                for message in exc.messages:
                    messages.error(request, message)
            else:
                label = "material foi adicionado" if created_count == 1 else "materiais foram adicionados"
                messages.success(request, f"{created_count} {label} a medicao com sucesso.")
                return redirect("billing:measurement_detail", measurement_id=period.id)
    else:
        common_form = BulkContractedLineCommonForm(period=period, prefix="bulk")
        item_formset = BulkContractedLineItemFormSet(period=period, prefix="items")

    return render(
        request,
        "billing/measurement_bulk_materials_form.html",
        {
            "period": period,
            "common_form": common_form,
            "item_formset": item_formset,
            "today_iso": timezone.localdate().isoformat(),
        },
    )


@staff_member_required
def measurement_environment_materials_add_view(request, measurement_id: int):
    period = get_object_or_404(MeasurementPeriod.objects.select_related("project"), pk=measurement_id)

    if period.workflow_status != WorkflowStatus.DRAFT:
        messages.error(request, "Periodo nao esta em DRAFT.")
        return redirect("billing:measurement_detail", measurement_id=period.id)

    selected_environment = None
    selected_discipline = None
    initial_items = []

    if request.method == "POST":
        common_form = EnvironmentMaterialApplicationForm(request.POST, period=period, prefix="env")
        item_formset = BulkContractedLineItemFormSet(request.POST, period=period, prefix="items")
        if common_form.is_valid() and item_formset.is_valid():
            environment = common_form.cleaned_data["environment"]
            discipline = common_form.cleaned_data["environment_discipline"]
            try:
                with transaction.atomic():
                    if not PredefinedEnvironmentDiscipline.objects.filter(
                        pk=discipline.pk,
                        environment=environment,
                        environment__project=period.project,
                        environment__is_active=True,
                        is_active=True,
                        materials__is_active=True,
                    ).exists():
                        raise ValidationError("O padrao selecionado foi alterado. Carregue os materiais novamente.")

                    created_count = 0
                    for item_form in item_formset.valid_item_forms():
                        add_or_merge_contracted_line(
                            period=period,
                            item=item_form.cleaned_data["item"],
                            location=common_form.cleaned_data.get("location"),
                            qty_period=item_form.cleaned_data["qty_period"],
                            application_date=common_form.cleaned_data.get("application_date"),
                            application_reference=common_form.cleaned_data.get("application_reference", ""),
                            note=common_form.cleaned_data.get("note", ""),
                            excess_justification=common_form.cleaned_data.get("excess_justification", ""),
                            use_additional_materials=False,
                            created_by=request.user,
                        )
                        created_count += 1
            except ValidationError as exc:
                for message in exc.messages:
                    messages.error(request, message)
            else:
                if created_count == 1:
                    messages.success(request, f"1 material do ambiente {environment.name} foi adicionado a medicao.")
                else:
                    messages.success(
                        request,
                        (
                            f"{created_count} materiais do ambiente {environment.name}, "
                            f"disciplina {discipline.discipline.name}, foram adicionados a medicao."
                        ),
                    )
                return redirect("billing:measurement_detail", measurement_id=period.id)
    else:
        environment_id = request.GET.get("environment") or request.GET.get("env-environment")
        discipline_id = request.GET.get("environment_discipline") or request.GET.get("env-environment_discipline")
        if environment_id:
            selected_environment = (
                PredefinedEnvironment.objects.filter(
                    pk=environment_id,
                    project=period.project,
                    is_active=True,
                )
                .first()
            )
        if selected_environment and discipline_id:
            selected_discipline = (
                PredefinedEnvironmentDiscipline.objects.filter(
                    pk=discipline_id,
                    environment=selected_environment,
                    is_active=True,
                )
                .first()
            )

        initial = {}
        if selected_environment:
            initial["environment"] = selected_environment
            initial["application_reference"] = selected_environment.name
        if selected_discipline:
            initial["environment_discipline"] = selected_discipline
            initial_items = [
                {
                    "item": material.item,
                    "qty_period": material.default_quantity,
                }
                for material in selected_discipline.materials.select_related("item")
                .filter(is_active=True, item__is_active=True)
                .order_by("order_index", "item__eap_code", "id")
            ]
        common_form = EnvironmentMaterialApplicationForm(period=period, prefix="env", initial=initial)
        item_formset = BulkContractedLineItemFormSet(period=period, prefix="items", initial=initial_items)

    return render(
        request,
        "billing/measurement_environment_materials_form.html",
        {
            "period": period,
            "common_form": common_form,
            "item_formset": item_formset,
            "today_iso": timezone.localdate().isoformat(),
        },
    )


@staff_member_required
def measurement_line_edit_view(request, line_id: int):
    line = get_object_or_404(MeasurementLine.objects.select_related("period", "item"), pk=line_id)
    period = line.period

    if period.workflow_status != WorkflowStatus.DRAFT:
        messages.error(request, "Periodo nao esta em DRAFT.")
        return redirect("billing:measurement_detail", measurement_id=period.id)

    if line.line_kind == MeasurementLineKind.EXTRA:
        form_class = ExtraLineForm
        title = "Editar linha extra"
    elif line.is_generated_additional:
        form_class = None
        title = "Detalhe do material adicional"
    else:
        form_class = ContractedLineEditForm
        title = "Editar linha contratada"

    if request.method == "POST":
        if line.is_generated_additional:
            messages.error(
                request,
                "Materiais adicionais gerados sao recalculados pelas linhas de origem.",
            )
            return redirect("billing:line_edit", line_id=line.id)
        form = form_class(request.POST, instance=line, period=period)
        if form.is_valid():
            if line.line_kind == MeasurementLineKind.EXTRA:
                form.save()
            else:
                update_contracted_line(
                    line=line,
                    item=form.cleaned_data["item"],
                    location=form.cleaned_data.get("location"),
                    qty_period=form.cleaned_data["qty_period"],
                    application_reference=form.cleaned_data.get("application_reference", ""),
                    note=form.cleaned_data.get("note", ""),
                    excess_justification=form.cleaned_data.get("excess_justification", ""),
                    use_additional_materials=form.cleaned_data.get("use_additional_materials", False),
                    created_by=request.user,
                )
            messages.success(request, "Linha atualizada.")
            return redirect("billing:measurement_detail", measurement_id=period.id)
    elif form_class is not None:
        form = form_class(instance=line, period=period)
    else:
        form = None

    histories = line.histories.select_related("created_by").order_by(
        "-application_date",
        "-created_at",
        "-id",
    )
    item_additional_materials = {}
    if line.line_kind == MeasurementLineKind.CONTRACTED and form is not None:
        item_additional_materials = _build_additional_materials_map(form.fields["item"].queryset)

    return render(
        request,
        "billing/measurement_line_form.html",
        {
            "form": form,
            "histories": histories,
            "item_additional_materials": item_additional_materials,
            "period": period,
            "line": line,
            "title": title,
        },
    )


@staff_member_required
def measurement_line_delete_view(request, line_id: int):
    line = get_object_or_404(
        MeasurementLine.objects.select_related("period"),
        pk=line_id,
    )
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
def measurement_line_history_edit_view(request, history_id: int):
    history = get_object_or_404(
        MeasurementLineHistory.objects.select_related("line__period", "line__item"),
        pk=history_id,
    )
    line = history.line
    period = line.period

    if period.workflow_status != WorkflowStatus.DRAFT:
        messages.error(request, "Periodo nao esta em DRAFT.")
        return redirect("billing:line_edit", line_id=line.id)

    if history.is_generated_additional_entry or line.is_generated_additional:
        messages.error(
            request,
            "Historicos gerados automaticamente sao recalculados pelas linhas de origem.",
        )
        return redirect("billing:line_edit", line_id=line.id)

    if request.method == "POST":
        form = MeasurementLineHistoryEditForm(request.POST, instance=history)
        if form.is_valid():
            update_history_entry(
                history=history,
                quantity_added=form.cleaned_data["quantity_added"],
                application_date=form.cleaned_data["application_date"],
                application_reference=form.cleaned_data.get("application_reference", ""),
                note=form.cleaned_data.get("note", ""),
                uses_additional_materials=form.cleaned_data.get("uses_additional_materials", False),
                created_by=request.user,
            )
            messages.success(request, "Lancamento historico atualizado.")
            return redirect("billing:line_edit", line_id=line.id)
    else:
        form = MeasurementLineHistoryEditForm(instance=history)

    return render(
        request,
        "billing/measurement_line_history_form.html",
        {
            "form": form,
            "history": history,
            "line": line,
            "period": period,
            "title": "Editar lancamento do historico",
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
