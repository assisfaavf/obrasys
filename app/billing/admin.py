from django.contrib import admin

from billing.models import MeasurementLine, MeasurementPeriod, MeasurementSettlement


@admin.register(MeasurementPeriod)
class MeasurementPeriodAdmin(admin.ModelAdmin):
    list_display = ("project", "number", "ref_month", "workflow_status", "financial_status", "created_at")
    list_filter = ("project", "workflow_status", "financial_status")
    search_fields = ("project__name", "=number")
    ordering = ("project", "number")


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
