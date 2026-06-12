from django.contrib import admin

from stock.models import Material, StockBalance, StockLocation, StockMovement


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "unit", "is_active", "updated_at")
    list_filter = ("is_active", "unit")
    search_fields = ("code", "name", "description")
    ordering = ("code",)


@admin.register(StockLocation)
class StockLocationAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "location_type", "project", "is_active", "updated_at")
    list_filter = ("location_type", "project", "is_active")
    search_fields = ("code", "name", "project__name")
    ordering = ("location_type", "code")


@admin.register(StockBalance)
class StockBalanceAdmin(admin.ModelAdmin):
    list_display = ("material", "location", "quantity", "updated_at")
    list_filter = ("location__location_type", "location__project")
    search_fields = ("material__code", "material__name", "location__code", "location__name")
    ordering = ("location", "material")
    readonly_fields = ("updated_at",)


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("material", "location", "movement_type", "quantity", "balance_after", "occurred_at", "created_by")
    list_filter = ("movement_type", "location__location_type", "location__project")
    search_fields = ("material__code", "material__name", "location__code", "note", "created_by__username")
    ordering = ("-occurred_at", "-id")
    readonly_fields = ("occurred_at",)
