from django.contrib import admin

from rdo.models import (
    DailyWorkActivityEntry,
    DailyWorkLog,
    DailyWorkMaterialEntry,
    DailyWorkOccurrence,
    DailyWorkTeamEntry,
    ProjectWorkOrderInfo,
)


class DailyWorkTeamEntryInline(admin.TabularInline):
    model = DailyWorkTeamEntry
    extra = 1


class DailyWorkActivityEntryInline(admin.TabularInline):
    model = DailyWorkActivityEntry
    extra = 1


class DailyWorkOccurrenceInline(admin.TabularInline):
    model = DailyWorkOccurrence
    extra = 1


class DailyWorkMaterialEntryInline(admin.TabularInline):
    model = DailyWorkMaterialEntry
    extra = 1


@admin.register(ProjectWorkOrderInfo)
class ProjectWorkOrderInfoAdmin(admin.ModelAdmin):
    list_display = ("project", "art_number", "contractor_name", "technical_manager_name", "updated_at")
    list_filter = ("project",)
    search_fields = (
        "project__name",
        "art_number",
        "contractor_name",
        "technical_manager_name",
        "contract_number",
    )
    ordering = ("project__name",)


@admin.register(DailyWorkLog)
class DailyWorkLogAdmin(admin.ModelAdmin):
    list_display = ("project", "log_date", "responsible_name", "created_by", "updated_at")
    list_filter = ("project", "log_date")
    search_fields = ("project__name", "responsible_name", "notes", "general_observation")
    ordering = ("-log_date", "-id")
    inlines = (
        DailyWorkTeamEntryInline,
        DailyWorkActivityEntryInline,
        DailyWorkOccurrenceInline,
        DailyWorkMaterialEntryInline,
    )


@admin.register(DailyWorkTeamEntry)
class DailyWorkTeamEntryAdmin(admin.ModelAdmin):
    list_display = ("daily_log", "team_name", "contractor_name", "role_or_service", "worker_count")
    list_filter = ("daily_log__project",)
    search_fields = ("team_name", "contractor_name", "role_or_service")


@admin.register(DailyWorkActivityEntry)
class DailyWorkActivityEntryAdmin(admin.ModelAdmin):
    list_display = ("daily_log", "location", "discipline", "description")
    list_filter = ("daily_log__project", "discipline")
    search_fields = ("description", "notes", "location__code", "location__name", "discipline__name")


@admin.register(DailyWorkOccurrence)
class DailyWorkOccurrenceAdmin(admin.ModelAdmin):
    list_display = ("daily_log", "occurrence_type", "description")
    list_filter = ("daily_log__project", "occurrence_type")
    search_fields = ("description", "notes")


@admin.register(DailyWorkMaterialEntry)
class DailyWorkMaterialEntryAdmin(admin.ModelAdmin):
    list_display = ("daily_log", "item", "location", "quantity", "unit_snapshot")
    list_filter = ("daily_log__project", "location")
    search_fields = ("description_snapshot", "notes", "item__eap_code", "item__description")
