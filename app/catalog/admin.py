from django.contrib import admin

from catalog.models import BudgetItem, Unit


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
