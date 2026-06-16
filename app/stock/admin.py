from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.urls import path

from stock.forms import MeasurementMaterialAdminForm, StockMovementAdminForm
from stock.initial_import import cancel_initial_stock_import, confirm_initial_stock_import, parse_initial_stock_file
from stock.models import (
    InitialStockImport,
    InitialStockImportItem,
    Material,
    MeasurementMaterial,
    MeasurementStockConsumption,
    StockBalance,
    StockLocation,
    StockMovement,
)
from stock.services import calculate_balance_after, register_stock_movement


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "category", "subcategory", "item_type", "brand", "unit", "is_active", "updated_at")
    list_filter = ("is_active", "unit", "category", "subcategory", "item_type")
    search_fields = ("code", "name", "brand", "category", "subcategory", "item_type", "description")
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


class InitialStockImportItemInline(admin.TabularInline):
    model = InitialStockImportItem
    extra = 0
    fields = (
        "row_number",
        "original_code",
        "original_category",
        "original_subcategory",
        "original_item_type",
        "original_description",
        "original_brand",
        "original_unit",
        "raw_quantity",
        "confirmed_quantity",
        "material",
        "status",
        "planned_action",
        "error_message",
        "stock_movement",
    )
    readonly_fields = (
        "row_number",
        "original_code",
        "original_category",
        "original_subcategory",
        "original_item_type",
        "original_description",
        "original_brand",
        "original_unit",
        "raw_quantity",
        "planned_action",
        "error_message",
        "stock_movement",
    )


@admin.register(InitialStockImport)
class InitialStockImportAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "destination_location",
        "status",
        "total_rows",
        "total_materials_created",
        "total_materials_updated",
        "total_movements_created",
        "created_at",
    )
    list_filter = ("status", "destination_location")
    search_fields = ("note", "destination_location__name")
    ordering = ("-created_at", "-id")
    readonly_fields = (
        "status",
        "created_by",
        "created_at",
        "updated_at",
        "confirmed_at",
        "total_rows",
        "total_materials_created",
        "total_materials_updated",
        "total_movements_created",
    )
    inlines = [InitialStockImportItemInline]
    actions = ("parse_selected_imports", "confirm_selected_imports", "cancel_selected_imports")

    def save_model(self, request, obj, form, change):
        if not change and request.user.is_authenticated:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
        if not change:
            try:
                created = parse_initial_stock_file(obj)
                self.message_user(request, f"Arquivo lido: {len(created)} itens para conferencia.", messages.SUCCESS)
            except ValidationError as exc:
                self.message_user(request, "; ".join(exc.messages), messages.ERROR)

    @admin.action(description="Processar/reprocessar arquivo de carga inicial")
    def parse_selected_imports(self, request, queryset):
        for import_batch in queryset:
            try:
                created = parse_initial_stock_file(import_batch)
                self.message_user(request, f"Carga {import_batch.id}: {len(created)} itens lidos.", messages.SUCCESS)
            except ValidationError as exc:
                self.message_user(request, f"Carga {import_batch.id}: {'; '.join(exc.messages)}", messages.ERROR)

    @admin.action(description="Confirmar carga inicial de estoque")
    def confirm_selected_imports(self, request, queryset):
        for import_batch in queryset:
            try:
                result = confirm_initial_stock_import(import_batch, user=request.user)
                self.message_user(
                    request,
                    (
                        f"Carga {import_batch.id}: {result['materials_created']} materiais criados, "
                        f"{result['materials_updated']} atualizados, "
                        f"{result['movements_created']} entradas iniciais."
                    ),
                    messages.SUCCESS,
                )
            except ValidationError as exc:
                self.message_user(request, f"Carga {import_batch.id}: {'; '.join(exc.messages)}", messages.ERROR)

    @admin.action(description="Cancelar carga inicial antes da confirmacao")
    def cancel_selected_imports(self, request, queryset):
        for import_batch in queryset:
            try:
                cancel_initial_stock_import(import_batch)
                self.message_user(request, f"Carga {import_batch.id}: cancelada.", messages.SUCCESS)
            except ValidationError as exc:
                self.message_user(request, f"Carga {import_batch.id}: {'; '.join(exc.messages)}", messages.ERROR)


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    form = StockMovementAdminForm
    list_display = (
        "material",
        "location",
        "target_location",
        "movement_type",
        "quantity",
        "balance_after",
        "occurred_at",
        "created_by",
    )
    list_filter = ("movement_type", "location__location_type", "location__project")
    search_fields = ("material__code", "material__name", "location__code", "note", "created_by__username")
    ordering = ("-occurred_at", "-id")
    readonly_fields = ("balance_after", "occurred_at", "created_by")

    class Media:
        css = {"all": ("stock/admin_stock_movement.css",)}
        js = ("stock/admin_stock_movement.js",)

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
            return (
                "material",
                "location",
                "target_location",
                "movement_type",
                "quantity",
                "balance_after",
                "note",
                "occurred_at",
                "created_by",
            )
        return (
            "material",
            "location",
            "target_location",
            "movement_type",
            "quantity",
            "available_quantity",
            "balance_after_display",
            "note",
        )

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
            target_location=form.cleaned_data.get("target_location"),
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


@admin.register(MeasurementMaterial)
class MeasurementMaterialAdmin(admin.ModelAdmin):
    form = MeasurementMaterialAdminForm
    list_display = ("measurement", "material", "quantity", "unit", "stock_location", "status", "updated_at")
    list_filter = ("status", "measurement__project", "stock_location")
    search_fields = ("measurement__project__name", "material__code", "material__name", "note")
    ordering = ("measurement", "id")
    fields = (
        "measurement",
        "material",
        "stock_location",
        "available_quantity",
        "quantity",
        "note",
        "unit",
        "status",
        "created_at",
        "updated_at",
    )
    readonly_fields = ("unit", "status", "created_at", "updated_at")

    class Media:
        js = ("stock/admin_measurement_material.js",)


@admin.register(MeasurementStockConsumption)
class MeasurementStockConsumptionAdmin(admin.ModelAdmin):
    list_display = ("measurement", "measurement_material", "stock_movement", "consumption_type", "created_at")
    list_filter = ("consumption_type", "measurement__project")
    search_fields = (
        "measurement__project__name",
        "measurement_material__material__code",
        "measurement_material__material__name",
        "stock_movement__note",
    )
    ordering = ("-created_at", "-id")
    readonly_fields = ("measurement", "measurement_material", "stock_movement", "consumption_type", "created_at")
