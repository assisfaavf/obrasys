from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.forms.models import inlineformset_factory
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from pathlib import Path

from core.models import Project
from exports.models import ExportStatus
from rdo.forms import (
    DailyWorkActivityEntryForm,
    DailyWorkLogForm,
    DailyWorkMaterialEntryForm,
    DailyWorkOccurrenceForm,
    DailyWorkTeamEntryForm,
    ProjectWorkOrderInfoForm,
)
from rdo.models import (
    DailyWorkActivityEntry,
    DailyWorkLog,
    DailyWorkMaterialEntry,
    DailyWorkOccurrence,
    DailyWorkTeamEntry,
    ProjectWorkOrderInfo,
)
from rdo.services.rdo_export import TEMPLATE_NAME, generate_rdo_xlsx
from utils.paths import get_template_path


TeamEntryFormSet = inlineformset_factory(
    DailyWorkLog,
    DailyWorkTeamEntry,
    form=DailyWorkTeamEntryForm,
    fields=("team_name", "worker_count", "location", "activity_description"),
    extra=1,
    can_delete=True,
)

ActivityEntryFormSet = inlineformset_factory(
    DailyWorkLog,
    DailyWorkActivityEntry,
    form=DailyWorkActivityEntryForm,
    fields=("description", "location", "discipline", "notes"),
    extra=1,
    can_delete=True,
)

OccurrenceFormSet = inlineformset_factory(
    DailyWorkLog,
    DailyWorkOccurrence,
    form=DailyWorkOccurrenceForm,
    fields=("occurrence_type", "description", "notes"),
    extra=1,
    can_delete=True,
)

MaterialEntryFormSet = inlineformset_factory(
    DailyWorkLog,
    DailyWorkMaterialEntry,
    form=DailyWorkMaterialEntryForm,
    fields=("item", "location", "quantity", "unit_snapshot", "notes"),
    extra=1,
    can_delete=True,
)


@staff_member_required
def rdo_project_list_view(request, project_id: int):
    project = get_object_or_404(Project.objects.select_related("client"), pk=project_id)
    daily_logs = (
        project.daily_work_logs.select_related("created_by")
        .prefetch_related("team_entries", "activity_entries", "occurrences", "material_entries")
        .order_by("-log_date", "-id")
    )
    return render(
        request,
        "rdo/project_rdo_list.html",
        {
            "project": project,
            "daily_logs": daily_logs,
        },
    )


@staff_member_required
def rdo_new_view(request, project_id: int):
    project = get_object_or_404(Project, pk=project_id)
    form = DailyWorkLogForm(project=project)

    if request.method == "POST":
        form = DailyWorkLogForm(request.POST, project=project)
        if form.is_valid():
            daily_log = form.save(commit=False)
            daily_log.project = project
            daily_log.created_by = request.user
            daily_log.save()
            messages.success(request, "Diário de obra criado.")
            return redirect("rdo:daily_log_detail", daily_log_id=daily_log.id)

    return render(
        request,
        "rdo/daily_log_new.html",
        {
            "project": project,
            "form": form,
        },
    )


@staff_member_required
def daily_log_detail_view(request, daily_log_id: int):
    daily_log = get_object_or_404(
        DailyWorkLog.objects.select_related("project", "project__client", "created_by"),
        pk=daily_log_id,
    )
    project = daily_log.project

    if request.method == "POST":
        form = DailyWorkLogForm(request.POST, instance=daily_log, project=project)
        team_formset = TeamEntryFormSet(
            request.POST,
            instance=daily_log,
            prefix="teams",
            form_kwargs={"project": project},
        )
        activity_formset = ActivityEntryFormSet(
            request.POST,
            instance=daily_log,
            prefix="activities",
            form_kwargs={"project": project},
        )
        occurrence_formset = OccurrenceFormSet(request.POST, instance=daily_log, prefix="occurrences")
        material_formset = MaterialEntryFormSet(
            request.POST,
            instance=daily_log,
            prefix="materials",
            form_kwargs={"project": project},
        )

        form_is_valid = form.is_valid()
        team_formset_is_valid = team_formset.is_valid()
        activity_formset_is_valid = activity_formset.is_valid()
        occurrence_formset_is_valid = occurrence_formset.is_valid()
        material_formset_is_valid = material_formset.is_valid()

        if (
            form_is_valid
            and team_formset_is_valid
            and activity_formset_is_valid
            and occurrence_formset_is_valid
            and material_formset_is_valid
        ):
            form.save()
            team_formset.save()
            activity_formset.save()
            occurrence_formset.save()
            material_formset.save()
            messages.success(request, "Diário de obra atualizado.")
            return redirect("rdo:daily_log_detail", daily_log_id=daily_log.id)
        messages.error(request, "Não foi possível salvar o diário. Confira os campos destacados e tente novamente.")
    else:
        form = DailyWorkLogForm(instance=daily_log, project=project)
        team_formset = TeamEntryFormSet(instance=daily_log, prefix="teams", form_kwargs={"project": project})
        activity_formset = ActivityEntryFormSet(
            instance=daily_log,
            prefix="activities",
            form_kwargs={"project": project},
        )
        occurrence_formset = OccurrenceFormSet(instance=daily_log, prefix="occurrences")
        material_formset = MaterialEntryFormSet(
            instance=daily_log,
            prefix="materials",
            form_kwargs={"project": project},
        )

    rdo_template_available = True
    rdo_template_error = ""
    try:
        get_template_path(TEMPLATE_NAME)
    except FileNotFoundError:
        rdo_template_available = False
        rdo_template_error = f"Template ausente: assets/templates/{TEMPLATE_NAME}."

    material_units = {
        str(item.id): item.unit.code
        for item in project.budget_items.filter(is_active=True).select_related("unit").order_by("eap_code")
    }

    return render(
        request,
        "rdo/daily_log_detail.html",
        {
            "daily_log": daily_log,
            "project": project,
            "form": form,
            "team_formset": team_formset,
            "activity_formset": activity_formset,
            "occurrence_formset": occurrence_formset,
            "material_formset": material_formset,
            "material_units": material_units,
            "rdo_template_available": rdo_template_available,
            "rdo_template_error": rdo_template_error,
        },
    )


@staff_member_required
def work_order_view(request, project_id: int):
    project = get_object_or_404(Project.objects.select_related("client"), pk=project_id)
    work_order, _ = ProjectWorkOrderInfo.objects.get_or_create(
        project=project,
        defaults={
            "address_snapshot": project.address,
            "work_start_date": project.start_date,
            "expected_end_date": project.planned_end_date,
        },
    )
    form = ProjectWorkOrderInfoForm(instance=work_order)

    if request.method == "POST":
        form = ProjectWorkOrderInfoForm(request.POST, instance=work_order)
        if form.is_valid():
            form.save()
            messages.success(request, "Livro de Ordem atualizado.")
            return redirect("rdo:work_order", project_id=project.id)

    return render(
        request,
        "rdo/work_order_form.html",
        {
            "project": project,
            "form": form,
        },
    )


@staff_member_required
def rdo_export_xlsx_view(request, daily_log_id: int):
    daily_log = get_object_or_404(DailyWorkLog.objects.select_related("project"), pk=daily_log_id)
    export_record, output_path = generate_rdo_xlsx(daily_log.id)
    if export_record.status == ExportStatus.ERROR:
        messages.error(request, export_record.error_message)
        return redirect("rdo:daily_log_detail", daily_log_id=daily_log.id)

    messages.success(request, "RDO Excel gerado com sucesso.")
    return FileResponse(open(output_path, "rb"), as_attachment=True, filename=Path(output_path).name)
