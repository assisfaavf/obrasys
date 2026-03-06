from django.contrib import admin

from exports.models import MeasurementExport


@admin.register(MeasurementExport)
class MeasurementExportAdmin(admin.ModelAdmin):
    list_display = ("project", "period", "export_type", "status", "file_path", "created_at")
    list_filter = ("project", "export_type", "status")
    search_fields = ("project__name", "file_path", "error_message")
    ordering = ("-created_at", "id")
