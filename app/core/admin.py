from django.contrib import admin
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import path, reverse

from core.forms import ProjectAdminForm, ProjectStageCsvImportForm
from core.models import Client, LocationTemplate, LocationTemplateItem, Project, ProjectLocation, ProjectStage
from core.services import apply_location_template_to_project, import_project_stages_from_csv


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("name", "document", "email", "phone")
    search_fields = ("name", "document", "email", "phone")


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    form = ProjectAdminForm
    change_form_template = "admin/core/project/change_form.html"
    list_display = ("name", "status", "client", "location_template", "start_date", "planned_end_date")
    list_filter = ("status", "client", "location_template")
    search_fields = ("name", "client__name")

    def response_add(self, request, obj, post_url_continue=None):
        if "_apply_location_template" in request.POST:
            self._apply_template_and_message(request, obj)
            change_url = reverse("admin:core_project_change", args=[obj.pk])
            return HttpResponseRedirect(change_url)
        return super().response_add(request, obj, post_url_continue)

    def response_change(self, request, obj):
        if "_apply_location_template" in request.POST:
            self._apply_template_and_message(request, obj)
            return HttpResponseRedirect(request.path)
        return super().response_change(request, obj)

    def _apply_template_and_message(self, request, obj):
        try:
            result = apply_location_template_to_project(project=obj)
        except Exception as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
            return

        message = (
            f"Template '{result['template_name']}' aplicado: "
            f"{result['created_count']} locais criados, "
            f"{result['skipped_count']} ignorados."
        )
        self.message_user(request, message)


@admin.register(ProjectLocation)
class ProjectLocationAdmin(admin.ModelAdmin):
    list_display = ("project", "code", "name", "order_index", "is_active")
    list_filter = ("project", "is_active")
    search_fields = ("project__name", "code", "name")
    ordering = ("project", "order_index", "code")


@admin.register(ProjectStage)
class ProjectStageAdmin(admin.ModelAdmin):
    list_display = ("project", "code", "name", "order_index", "is_active")
    list_filter = ("project", "is_active")
    search_fields = ("project__name", "code", "name")
    ordering = ("project", "order_index", "code")
    change_list_template = "admin/core/projectstage/change_list.html"

    def get_urls(self):
        custom_urls = [
            path(
                "import-csv/",
                self.admin_site.admin_view(self.import_csv_view),
                name="core_projectstage_import_csv",
            ),
        ]
        return custom_urls + super().get_urls()

    def import_csv_view(self, request):
        summary = None
        if request.method == "POST":
            form = ProjectStageCsvImportForm(request.POST, request.FILES)
            if form.is_valid():
                summary = import_project_stages_from_csv(
                    form.cleaned_data["csv_file"],
                    form.cleaned_data["project"],
                )
                if summary["errors"]:
                    messages.warning(
                        request,
                        "Importacao concluida com erros: "
                        f"{summary['created']} criadas, {summary['updated']} atualizadas, "
                        f"{len(summary['errors'])} erro(s).",
                    )
                else:
                    messages.success(
                        request,
                        f"Importacao concluida: {summary['created']} criadas, {summary['updated']} atualizadas.",
                    )
        else:
            form = ProjectStageCsvImportForm()

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "Importar etapas por CSV",
            "form": form,
            "summary": summary,
        }
        return render(request, "admin/core/projectstage/import_csv.html", context)


class LocationTemplateItemInline(admin.TabularInline):
    model = LocationTemplateItem
    extra = 0
    ordering = ("order_index", "code")


@admin.register(LocationTemplate)
class LocationTemplateAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)
    inlines = (LocationTemplateItemInline,)


@admin.register(LocationTemplateItem)
class LocationTemplateItemAdmin(admin.ModelAdmin):
    list_display = ("template", "code", "name", "order_index")
    list_filter = ("template",)
    search_fields = ("template__name", "code", "name")
    ordering = ("template", "order_index", "code")
