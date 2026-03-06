from django.contrib import admin

from pricing.models import PriceIndex, PriceIndexValue, ProjectPriceAdjustment


@admin.register(PriceIndex)
class PriceIndexAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "source")
    search_fields = ("code", "name", "source")


@admin.register(PriceIndexValue)
class PriceIndexValueAdmin(admin.ModelAdmin):
    list_display = ("price_index", "ref_month", "value")
    list_filter = ("price_index",)
    search_fields = ("price_index__code", "price_index__name")
    ordering = ("price_index", "ref_month")


@admin.register(ProjectPriceAdjustment)
class ProjectPriceAdjustmentAdmin(admin.ModelAdmin):
    list_display = ("project", "price_index", "base_month", "apply_to", "is_active")
    list_filter = ("project", "price_index", "apply_to", "is_active")
    search_fields = ("project__name", "price_index__code", "price_index__name")
