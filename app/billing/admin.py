from django.contrib import admin

from billing.models import (
    MeasurementLine,
    MeasurementLineHistory,
    MeasurementPeriod,
    MeasurementSettlement,
    MeasurementWorkflowHistory,
    PredefinedEnvironment,
    PredefinedEnvironmentDiscipline,
    PredefinedEnvironmentMaterial,
)


class MeasurementWorkflowHistoryInline(admin.TabularInline):
    model = MeasurementWorkflowHistory
    extra = 0
    can_delete = False
    readonly_fields = ("from_status", "to_status", "note", "changed_by", "changed_at")

    def has_add_permission(self, request, obj=None):
        return False


class PredefinedEnvironmentMaterialInline(admin.TabularInline):
    model = PredefinedEnvironmentMaterial
    extra = 1
    fields = ("item", "default_quantity", "order_index", "is_active")


class PredefinedEnvironmentDisciplineInline(admin.TabularInline):
    model = PredefinedEnvironmentDiscipline
    extra = 1
    fields = ("discipline", "is_active")


@admin.register(PredefinedEnvironment)
class PredefinedEnvironmentAdmin(admin.ModelAdmin):
    list_display = ("project", "name", "is_active", "updated_at")
    list_filter = ("project", "is_active")
    search_fields = ("project__name", "name", "description")
    ordering = ("project", "name")
    inlines = (PredefinedEnvironmentDisciplineInline,)


@admin.register(PredefinedEnvironmentDiscipline)
class PredefinedEnvironmentDisciplineAdmin(admin.ModelAdmin):
    list_display = ("environment", "discipline", "is_active", "updated_at")
    list_filter = ("environment__project", "discipline", "is_active")
    search_fields = ("environment__name", "environment__project__name", "discipline__name")
    ordering = ("environment", "discipline__name")
    inlines = (PredefinedEnvironmentMaterialInline,)


@admin.register(PredefinedEnvironmentMaterial)
class PredefinedEnvironmentMaterialAdmin(admin.ModelAdmin):
    list_display = ("environment_discipline", "item", "default_quantity", "order_index", "is_active")
    list_filter = ("environment_discipline__environment__project", "environment_discipline__discipline", "is_active")
    search_fields = (
        "environment_discipline__environment__name",
        "item__eap_code",
        "item__description",
    )
    ordering = ("environment_discipline", "order_index", "item__eap_code")


@admin.register(MeasurementPeriod)
class MeasurementPeriodAdmin(admin.ModelAdmin):
    list_display = (
        "project",
        "number",
        "ref_month",
        "workflow_status",
        "financial_status",
        "finalized_at",
        "sent_at",
        "in_review_at",
        "authorized_at",
        "created_at",
    )
    list_filter = ("project", "workflow_status", "financial_status")
    search_fields = ("project__name", "=number")
    ordering = ("project", "number")
    inlines = [MeasurementWorkflowHistoryInline]


@admin.register(MeasurementLine)
class MeasurementLineAdmin(admin.ModelAdmin):
    list_display = ("period", "location", "line_kind", "item", "qty_period", "created_at")
    list_filter = ("period__project", "line_kind", "location")
    search_fields = (
        "period__project__name",
        "item__eap_code",
        "item__description",
        "extra_description",
        "note",
    )


@admin.register(MeasurementSettlement)
class MeasurementSettlementAdmin(admin.ModelAdmin):
    list_display = ("period", "event_date", "amount", "method", "status", "created_at")
    list_filter = ("period__project", "method", "status")
    search_fields = ("period__project__name", "reference", "notes")
    ordering = ("period", "event_date", "id")


@admin.register(MeasurementLineHistory)
class MeasurementLineHistoryAdmin(admin.ModelAdmin):
    list_display = (
        "line",
        "application_date",
        "application_reference",
        "quantity_added",
        "source_item_snapshot",
        "created_by",
        "created_at",
    )
    list_filter = ("line__period__project", "application_date")
    search_fields = (
        "line__period__project__name",
        "line__item__eap_code",
        "line__item__description",
        "application_reference",
        "note",
        "created_by__username",
    )
    ordering = ("-application_date", "-created_at", "-id")


@admin.register(MeasurementWorkflowHistory)
class MeasurementWorkflowHistoryAdmin(admin.ModelAdmin):
    list_display = ("period", "from_status", "to_status", "changed_by", "changed_at")
    list_filter = ("to_status", "from_status", "period__project")
    search_fields = ("period__project__name", "note", "changed_by__username")
    ordering = ("-changed_at", "-id")
    readonly_fields = ("period", "from_status", "to_status", "note", "changed_by", "changed_at")
