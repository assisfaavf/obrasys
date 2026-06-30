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
    MaterialAlias,
    MaterialRequest,
    MaterialRequestItem,
    MeasurementMaterial,
    MeasurementStockConsumption,
    PurchaseRequest,
    PurchaseRequestItem,
    StockBalance,
    StockImport,
    StockImportItem,
    StockLocation,
    StockMovement,
)
from stock.material_request import approve_material_request, calculate_material_request, cancel_material_request
from stock.material_request_processing import process_material_request
from stock.measurement_consumption import generate_purchase_request_from_real_shortage
from stock.purchase_import import cancel_stock_import, confirm_stock_import, parse_stock_import_file
from stock.services import calculate_balance_after, register_stock_movement


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "category", "subcategory", "item_type", "brand", "unit", "is_active", "updated_at")
    list_filter = ("is_active", "unit", "category", "subcategory", "item_type")
    search_fields = ("code", "name", "brand", "category", "subcategory", "item_type", "description")
    ordering = ("code",)


@admin.register(MaterialAlias)
class MaterialAliasAdmin(admin.ModelAdmin):
    list_display = ("alias", "supplier", "material", "created_at")
    list_filter = ("supplier",)
    search_fields = ("alias", "supplier", "material__code", "material__name")
    ordering = ("alias",)
    readonly_fields = ("normalized_alias", "created_at")


@admin.register(StockLocation)
class StockLocationAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "location_type", "project", "is_active", "updated_at")
    list_filter = ("location_type", "project", "is_active")
    search_fields = ("code", "name", "project__name")
    ordering = ("location_type", "code")


@admin.register(StockBalance)
class StockBalanceAdmin(admin.ModelAdmin):
    list_display = ("material", "location", "quantity", "minimum_quantity", "is_low_stock", "updated_at")
    list_filter = ("location__location_type", "location__project")
    search_fields = ("material__code", "material__name", "location__code", "location__name")
    ordering = ("location", "material")
    readonly_fields = ("updated_at",)
    actions = ("generate_purchase_for_low_stock",)

    @admin.display(boolean=True, description="Baixo estoque")
    def is_low_stock(self, obj):
        return obj.minimum_quantity > 0 and obj.quantity <= obj.minimum_quantity

    @admin.action(description="Gerar pedido de compra por falta real da obra")
    def generate_purchase_for_low_stock(self, request, queryset):
        projects = {
            balance.location.project
            for balance in queryset.select_related("location__project")
            if balance.location.project_id
        }
        for project in projects:
            purchase_request = generate_purchase_request_from_real_shortage(
                project,
                user=request.user if request.user.is_authenticated else None,
            )
            if purchase_request:
                self.message_user(request, f"Pedido {purchase_request.id} gerado para {project}.", messages.SUCCESS)
            else:
                self.message_user(request, f"Nenhuma falta nova para {project}.", messages.INFO)


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


class StockImportItemInline(admin.TabularInline):
    model = StockImportItem
    extra = 0
    fields = (
        "row_number",
        "original_code",
        "supplier_code",
        "original_description",
        "original_unit",
        "raw_quantity",
        "original_quantity",
        "material",
        "confirmed_quantity",
        "status",
        "manual_adjustment",
        "note",
        "stock_movement",
    )
    readonly_fields = (
        "row_number",
        "original_code",
        "supplier_code",
        "original_description",
        "original_unit",
        "raw_quantity",
        "original_quantity",
        "stock_movement",
    )


class MaterialRequestItemInline(admin.TabularInline):
    model = MaterialRequestItem
    extra = 1
    fields = (
        "material",
        "unit_display",
        "requested_quantity",
        "project_available_quantity",
        "suggested_project_usage_quantity",
        "central_available_quantity",
        "suggested_transfer_quantity",
        "suggested_purchase_quantity",
        "approved_quantity",
        "transfer_movement",
        "purchase_request_item_display",
        "status",
        "note",
    )
    readonly_fields = (
        "unit_display",
        "project_available_quantity",
        "suggested_project_usage_quantity",
        "central_available_quantity",
        "suggested_transfer_quantity",
        "suggested_purchase_quantity",
        "approved_quantity",
        "transfer_movement",
        "purchase_request_item_display",
        "status",
    )
    autocomplete_fields = ("material",)

    @admin.display(description="Unidade")
    def unit_display(self, obj):
        if obj and obj.material_id:
            return obj.material.unit.code
        return "-"

    @admin.display(description="Item de compra")
    def purchase_request_item_display(self, obj):
        if not obj or not obj.pk:
            return "-"
        try:
            return obj.purchase_request_item
        except PurchaseRequestItem.DoesNotExist:
            return "-"


class PurchaseRequestItemInline(admin.TabularInline):
    model = PurchaseRequestItem
    extra = 0
    fields = ("material", "quantity", "unit", "material_request_item", "status", "note")
    readonly_fields = ("material", "quantity", "unit", "material_request_item", "status", "note")


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


@admin.register(MaterialRequest)
class MaterialRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "project",
        "requested_by",
        "status",
        "total_items",
        "total_items_with_project_stock",
        "total_items_with_transfer_suggestion",
        "total_items_with_purchase_suggestion",
        "created_at",
    )
    list_filter = ("status", "project")
    search_fields = ("project__name", "requested_by__username", "note")
    ordering = ("-created_at", "-id")
    readonly_fields = (
        "status",
        "requested_by",
        "created_at",
        "updated_at",
        "analyzed_at",
        "approved_at",
        "total_items",
        "total_items_with_project_stock",
        "total_items_with_transfer_suggestion",
        "total_items_with_purchase_suggestion",
    )
    inlines = [MaterialRequestItemInline]
    actions = (
        "calculate_selected_requests",
        "approve_selected_requests",
        "process_selected_requests",
        "cancel_selected_requests",
    )

    def save_model(self, request, obj, form, change):
        if not change and request.user.is_authenticated:
            obj.requested_by = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description="Calcular sugestoes de atendimento")
    def calculate_selected_requests(self, request, queryset):
        for material_request in queryset:
            try:
                calculate_material_request(material_request)
                self.message_user(request, f"Requisicao {material_request.id}: sugestoes recalculadas.", messages.SUCCESS)
            except ValidationError as exc:
                self.message_user(request, f"Requisicao {material_request.id}: {'; '.join(exc.messages)}", messages.ERROR)

    @admin.action(description="Aprovar requisicao sem movimentar estoque")
    def approve_selected_requests(self, request, queryset):
        for material_request in queryset:
            try:
                approve_material_request(material_request)
                self.message_user(request, f"Requisicao {material_request.id}: aprovada.", messages.SUCCESS)
            except ValidationError as exc:
                self.message_user(request, f"Requisicao {material_request.id}: {'; '.join(exc.messages)}", messages.ERROR)

    @admin.action(description="Gerar transferencia e pedido de compra")
    def process_selected_requests(self, request, queryset):
        for material_request in queryset:
            try:
                result = process_material_request(
                    material_request,
                    user=request.user if request.user.is_authenticated else None,
                )
                self.message_user(
                    request,
                    (
                        f"Requisicao {material_request.id}: "
                        f"{result.transfers_created} transferencia(s), "
                        f"{result.purchase_items_created} item(ns) de compra gerado(s)."
                    ),
                    messages.SUCCESS,
                )
            except ValidationError as exc:
                self.message_user(request, f"Requisicao {material_request.id}: {'; '.join(exc.messages)}", messages.ERROR)

    @admin.action(description="Cancelar requisicao")
    def cancel_selected_requests(self, request, queryset):
        for material_request in queryset:
            try:
                cancel_material_request(material_request)
                self.message_user(request, f"Requisicao {material_request.id}: cancelada.", messages.SUCCESS)
            except ValidationError as exc:
                self.message_user(request, f"Requisicao {material_request.id}: {'; '.join(exc.messages)}", messages.ERROR)


@admin.register(PurchaseRequest)
class PurchaseRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "project", "material_request", "status", "total_items", "created_by", "created_at")
    list_filter = ("status", "project")
    search_fields = ("project__name", "material_request__id", "created_by__username", "note")
    ordering = ("-created_at", "-id")
    readonly_fields = ("project", "material_request", "status", "created_by", "total_items", "created_at", "updated_at")
    inlines = [PurchaseRequestItemInline]


@admin.register(StockImport)
class StockImportAdmin(admin.ModelAdmin):
    list_display = ("id", "supplier", "destination_location", "project", "status", "received_at", "created_at")
    list_filter = ("status", "destination_location__location_type", "project")
    search_fields = ("supplier", "note", "destination_location__name", "project__name")
    ordering = ("-created_at", "-id")
    readonly_fields = ("status", "created_by", "created_at", "updated_at", "confirmed_at")
    inlines = [StockImportItemInline]
    actions = ("parse_selected_imports", "confirm_selected_imports", "cancel_selected_imports")

    def save_model(self, request, obj, form, change):
        if not change and request.user.is_authenticated:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)
        if not change:
            try:
                parse_stock_import_file(obj)
                self.message_user(request, "Arquivo lido e itens criados para conferencia.", messages.SUCCESS)
            except ValidationError as exc:
                self.message_user(request, "; ".join(exc.messages), messages.ERROR)

    @admin.action(description="Ler/reler arquivo e gerar itens para conferencia")
    def parse_selected_imports(self, request, queryset):
        for import_batch in queryset:
            try:
                created = parse_stock_import_file(import_batch)
                self.message_user(
                    request,
                    f"Importacao {import_batch.id}: {len(created)} itens lidos.",
                    messages.SUCCESS,
                )
            except ValidationError as exc:
                self.message_user(request, f"Importacao {import_batch.id}: {'; '.join(exc.messages)}", messages.ERROR)

    @admin.action(description="Confirmar entrada de estoque")
    def confirm_selected_imports(self, request, queryset):
        for import_batch in queryset:
            try:
                confirmed = confirm_stock_import(import_batch, user=request.user)
                self.message_user(
                    request,
                    f"Importacao {import_batch.id}: {len(confirmed)} entradas geradas.",
                    messages.SUCCESS,
                )
            except ValidationError as exc:
                self.message_user(request, f"Importacao {import_batch.id}: {'; '.join(exc.messages)}", messages.ERROR)

    @admin.action(description="Cancelar importacao antes da confirmacao")
    def cancel_selected_imports(self, request, queryset):
        for import_batch in queryset:
            try:
                cancel_stock_import(import_batch)
                self.message_user(request, f"Importacao {import_batch.id}: cancelada.", messages.SUCCESS)
            except ValidationError as exc:
                self.message_user(request, f"Importacao {import_batch.id}: {'; '.join(exc.messages)}", messages.ERROR)


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
    list_display = (
        "measurement",
        "measurement_material",
        "material",
        "stock_location",
        "consumed_quantity",
        "pending_quantity",
        "status",
        "stock_movement",
        "reversal_movement",
        "created_at",
    )
    list_filter = ("status", "consumption_type", "measurement__project")
    search_fields = (
        "measurement__project__name",
        "measurement_material__material__code",
        "measurement_material__material__name",
        "stock_movement__note",
    )
    ordering = ("-created_at", "-id")
    readonly_fields = (
        "measurement",
        "measurement_material",
        "material",
        "stock_location",
        "consumed_quantity",
        "pending_quantity",
        "status",
        "stock_movement",
        "reversal_movement",
        "consumption_type",
        "note",
        "created_at",
        "updated_at",
    )
    actions = ("generate_purchase_for_real_shortage",)

    @admin.action(description="Gerar pedido de compra por falta real")
    def generate_purchase_for_real_shortage(self, request, queryset):
        projects = {consumption.measurement.project for consumption in queryset.select_related("measurement__project")}
        for project in projects:
            purchase_request = generate_purchase_request_from_real_shortage(
                project,
                user=request.user if request.user.is_authenticated else None,
            )
            if purchase_request:
                self.message_user(request, f"Pedido {purchase_request.id} gerado para {project}.", messages.SUCCESS)
            else:
                self.message_user(request, f"Nenhuma falta nova para {project}.", messages.INFO)
