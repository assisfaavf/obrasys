import json

from django.contrib import admin
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import path, reverse
from django.utils.html import format_html

from catalog.forms import BudgetImportUploadForm
from catalog.models import BudgetImportJob, BudgetImportJobStatus, BudgetItem, Unit
from catalog.services.budget_import import apply_import_job, create_preview_job


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ("code", "name")
    search_fields = ("code", "name")


@admin.register(BudgetItem)
class BudgetItemAdmin(admin.ModelAdmin):
    list_display = ("project", "eap_code", "description", "unit", "qty_contracted", "is_active")
    list_filter = ("project", "is_active", "unit")
    search_fields = ("project__name", "eap_code", "description")
    ordering = ("project", "eap_code")


@admin.register(BudgetImportJob)
class BudgetImportJobAdmin(admin.ModelAdmin):
    list_display = ("project", "mode", "status", "original_filename", "created_at", "created_by")
    list_filter = ("project", "mode", "status")
    search_fields = ("project__name", "original_filename", "created_by__username")
    ordering = ("-created_at", "id")
    change_list_template = "admin/catalog/budgetimportjob/change_list.html"
    change_form_template = "admin/catalog/budgetimportjob/change_form.html"
    readonly_fields = (
        "project",
        "mode",
        "original_filename",
        "created_at",
        "created_by",
        "status",
        "preview_rows_display",
        "preview_errors_display",
        "summary_display",
    )
    fields = (
        "project",
        "mode",
        "original_filename",
        "created_at",
        "created_by",
        "status",
        "preview_rows_display",
        "preview_errors_display",
        "summary_display",
    )

    def get_urls(self):
        custom_urls = [
            path(
                "new-import/",
                self.admin_site.admin_view(self.new_import_view),
                name="catalog_budgetimportjob_new_import",
            ),
            path(
                "<int:job_id>/apply/",
                self.admin_site.admin_view(self.apply_import_view),
                name="catalog_budgetimportjob_apply_import",
            ),
        ]
        return custom_urls + super().get_urls()

    def has_add_permission(self, request):
        return False

    def new_import_view(self, request):
        if request.method == "POST":
            form = BudgetImportUploadForm(request.POST, request.FILES)
            if form.is_valid():
                csv_file = form.cleaned_data["csv_file"]
                try:
                    job = create_preview_job(
                        project=form.cleaned_data["project"],
                        mode=form.cleaned_data["mode"],
                        original_filename=csv_file.name,
                        csv_content=csv_file.read(),
                        created_by=request.user,
                    )
                except Exception as exc:
                    messages.error(request, f"Falha ao gerar preview: {exc}")
                else:
                    if job.status == BudgetImportJobStatus.ERROR:
                        messages.error(
                            request,
                            f"Import registrado com erro: {job.summary_json.get('error_message', 'erro desconhecido')}",
                        )
                    else:
                        preview_errors = len((job.preview_json or {}).get("errors") or [])
                        messages.success(
                            request,
                            f"Preview criado: {job.preview_json.get('rows_total', 0)} linhas, {preview_errors} erro(s).",
                        )
                    return HttpResponseRedirect(
                        reverse("admin:catalog_budgetimportjob_change", args=[job.pk])
                    )
        else:
            form = BudgetImportUploadForm()

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "Novo Import de Orcamento (CSV)",
            "form": form,
        }
        return render(request, "admin/catalog/budgetimportjob/new_import.html", context)

    def apply_import_view(self, request, job_id: int):
        job = get_object_or_404(BudgetImportJob, pk=job_id)
        if request.method != "POST":
            messages.error(request, "Acao invalida para aplicar import.")
            return HttpResponseRedirect(reverse("admin:catalog_budgetimportjob_change", args=[job.pk]))

        try:
            summary = apply_import_job(job)
        except Exception as exc:
            messages.error(request, f"Erro ao aplicar import: {exc}")
        else:
            messages.success(
                request,
                "Import aplicado com sucesso: "
                f"criados={summary['created']}, "
                f"atualizados={summary['updated']}, "
                f"desativados={summary['deactivated']}, "
                f"pulados={summary['skipped']}.",
            )

        return HttpResponseRedirect(reverse("admin:catalog_budgetimportjob_change", args=[job.pk]))

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = extra_context or {}
        job = self.get_object(request, object_id)
        can_apply = bool(
            job
            and job.status == BudgetImportJobStatus.PREVIEW
            and not (job.preview_json or {}).get("has_fatal_errors")
        )
        extra_context["can_apply_import"] = can_apply
        extra_context["apply_import_url"] = (
            reverse("admin:catalog_budgetimportjob_apply_import", args=[job.pk]) if job else ""
        )
        return super().change_view(request, object_id, form_url, extra_context)

    @admin.display(description="Preview (primeiras 20 linhas)")
    def preview_rows_display(self, obj: BudgetImportJob):
        rows = (obj.preview_json or {}).get("preview_rows", [])
        return format_html(
            "<pre>{}</pre>",
            json.dumps(rows, indent=2, ensure_ascii=False),
        )

    @admin.display(description="Validacoes/erros do preview")
    def preview_errors_display(self, obj: BudgetImportJob):
        errors = (obj.preview_json or {}).get("errors", [])
        return format_html(
            "<pre>{}</pre>",
            json.dumps(errors, indent=2, ensure_ascii=False),
        )

    @admin.display(description="Resumo da aplicacao")
    def summary_display(self, obj: BudgetImportJob):
        return format_html(
            "<pre>{}</pre>",
            json.dumps(obj.summary_json or {}, indent=2, ensure_ascii=False),
        )
