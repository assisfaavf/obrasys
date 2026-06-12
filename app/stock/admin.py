from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.urls import path

from stock.forms import StockMovementAdminForm
from stock.models import Material, StockBalance, StockLocation, StockMovement
from stock.services import calculate_balance_after, register_stock_movement


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "brand", "unit", "is_active", "updated_at")
    list_filter = ("is_active", "unit")
    search_fields = ("code", "name", "brand", "description")
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
    form = StockMovementAdminForm
    list_display = ("material", "location", "movement_type", "quantity", "balance_after", "occurred_at", "created_by")
    list_filter = ("movement_type", "location__location_type", "location__project")
    search_fields = ("material__code", "material__name", "location__code", "note", "created_by__username")
    ordering = ("-occurred_at", "-id")
    readonly_fields = ("balance_after", "occurred_at", "created_by")

    def get_urls(self):
        custom_urls = [
            path(
                "available-balance/",
                self.admin_site.admin_view(self.available_balance_view),
                name="stock_stockmovement_available_balance",
            ),
        ]
        return custom_urls + super().get_urls()

    def get_fields(self, request, obj=None):
        if obj:
            return ("material", "location", "movement_type", "quantity", "balance_after", "note", "occurred_at", "created_by")
        return ("material", "location", "movement_type", "quantity", "available_quantity", "balance_after_display", "note")

    def has_change_permission(self, request, obj=None):
        if obj and request.method == "POST":
            return False
        return super().has_change_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if change:
            raise PermissionDenied("Movimentacoes de estoque nao podem ser alteradas depois de criadas.")

        movement = register_stock_movement(
            material=form.cleaned_data["material"],
            location=form.cleaned_data["location"],
            movement_type=form.cleaned_data["movement_type"],
            quantity=form.cleaned_data["quantity"],
            note=form.cleaned_data.get("note", ""),
            created_by=request.user if request.user.is_authenticated else None,
        )
        obj.pk = movement.pk
        obj.id = movement.id
        obj.balance_after = movement.balance_after
        obj.created_by = movement.created_by
        obj.occurred_at = movement.occurred_at

    def available_balance_view(self, request):
        material_id = request.GET.get("material")
        location_id = request.GET.get("location")
        movement_type = request.GET.get("movement_type") or ""
        quantity_value = request.GET.get("quantity") or "0"

        quantity = (
            StockBalance.objects.filter(material_id=material_id, location_id=location_id)
            .values_list("quantity", flat=True)
            .first()
        ) or 0

        balance_after = quantity
        if movement_type and quantity_value:
            try:
                balance_after = calculate_balance_after(
                    current_quantity=quantity,
                    movement_type=movement_type,
                    quantity=quantity_value,
                )
            except Exception:
                balance_after = quantity

        return JsonResponse(
            {
                "available_quantity": str(quantity),
                "balance_after": str(balance_after),
            }
        )
