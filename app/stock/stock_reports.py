from datetime import datetime, time
from decimal import Decimal

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from stock.models import Material, StockBalance, StockLocation, StockMovement


def truthy(value) -> bool:
    return str(value or "").lower() in {"1", "true", "on", "yes", "sim"}


def parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def get_stock_by_location_report(filters: dict) -> dict:
    show_zero = truthy(filters.get("show_zero"))
    location_id = filters.get("stock_location") or ""
    materials = _filtered_materials(filters)
    balances = (
        StockBalance.objects.select_related("material__unit", "location")
        .filter(material__in=materials)
        .order_by(
            "material__category",
            "material__subcategory",
            "material__name",
            "location__name",
        )
    )
    if location_id:
        balances = balances.filter(location_id=location_id)
    if not show_zero:
        balances = balances.filter(quantity__gt=0)

    rows = [_balance_row(balance) for balance in balances]
    if show_zero and location_id:
        rows.extend(_missing_location_zero_rows(materials, location_id, rows))
        rows.sort(key=lambda row: (row["category"], row["subcategory"], row["material_name"]))

    return {
        "rows": rows,
        "total_quantity": sum((row["quantity"] for row in rows), Decimal("0")),
    }


def get_stock_by_material_report(filters: dict) -> dict:
    show_zero = truthy(filters.get("show_zero"))
    material_id = filters.get("material") or ""
    location_id = filters.get("stock_location") or ""
    materials = _filtered_materials(filters)
    if material_id:
        materials = materials.filter(id=material_id)

    balances = (
        StockBalance.objects.select_related("material__unit", "location")
        .filter(material__in=materials)
        .order_by("material__name", "location__location_type", "location__name")
    )
    if location_id:
        balances = balances.filter(location_id=location_id)
    if not show_zero:
        balances = balances.filter(quantity__gt=0)

    rows = [_balance_row(balance) for balance in balances]
    if show_zero and material_id:
        rows.extend(_missing_material_zero_rows(materials.get(id=material_id), location_id, rows))
        rows.sort(key=lambda row: (row["material_name"], row["location_name"]))

    total_quantity = sum((row["quantity"] for row in rows), Decimal("0"))
    return {
        "rows": rows,
        "total_quantity": total_quantity,
        "unit": rows[0]["unit"] if rows else "",
    }


def get_stock_matrix_report(filters: dict) -> dict:
    show_zero = truthy(filters.get("show_zero"))
    locations = StockLocation.objects.filter(is_active=True).order_by("location_type", "name", "code")
    balances = (
        StockBalance.objects.select_related("material__unit", "location")
        .filter(material__in=_filtered_materials(filters), location__in=locations)
        .order_by("material__category", "material__subcategory", "material__name")
    )
    if filters.get("stock_location"):
        balances = balances.filter(location_id=filters["stock_location"])
        locations = locations.filter(id=filters["stock_location"])
    if not show_zero:
        balances = balances.filter(quantity__gt=0)

    rows_by_material = {}
    for balance in balances:
        material = balance.material
        row = rows_by_material.setdefault(
            material.id,
            {
                "material": material,
                "material_name": material.name,
                "unit": material.unit.code,
                "quantities": {location.id: Decimal("0") for location in locations},
                "total": Decimal("0"),
                "material_admin_url": _admin_url("stock_material_change", material.id),
            },
        )
        row["quantities"][balance.location_id] = balance.quantity
        row["total"] += balance.quantity

    locations = list(locations)
    rows = list(rows_by_material.values())
    for row in rows:
        row["cells"] = [row["quantities"][location.id] for location in locations]
    return {"locations": locations, "rows": rows}


def get_stock_movements_report(filters: dict) -> dict:
    movements = StockMovement.objects.select_related(
        "material__unit",
        "location",
        "target_location",
        "created_by",
    ).order_by("-occurred_at", "-id")

    if filters.get("material"):
        movements = movements.filter(material_id=filters["material"])
    if filters.get("stock_location"):
        movements = movements.filter(Q(location_id=filters["stock_location"]) | Q(target_location_id=filters["stock_location"]))
    if filters.get("movement_type"):
        movements = movements.filter(movement_type=filters["movement_type"])
    if filters.get("user"):
        movements = movements.filter(created_by_id=filters["user"])

    date_from = parse_date(filters.get("date_from"))
    if date_from:
        movements = movements.filter(occurred_at__gte=timezone.make_aware(datetime.combine(date_from, time.min)))
    date_to = parse_date(filters.get("date_to"))
    if date_to:
        movements = movements.filter(occurred_at__lte=timezone.make_aware(datetime.combine(date_to, time.max)))

    rows = [_movement_row(movement) for movement in movements]
    return {
        "rows": rows,
        "total_quantity": sum((row["quantity"] for row in rows), Decimal("0")),
        "count": len(rows),
    }


def get_stock_summary_report(filters: dict) -> dict:
    movements = get_stock_movements_report(filters)
    return {
        "total_materials": Material.objects.filter(is_active=True).count(),
        "total_locations": StockLocation.objects.filter(is_active=True).count(),
        "positive_balance_items": StockBalance.objects.filter(quantity__gt=0).count(),
        "movement_count": movements["count"],
        "latest_movements": movements["rows"][:10],
    }


def _filtered_materials(filters: dict):
    materials = Material.objects.select_related("unit").filter(is_active=True)
    if filters.get("material"):
        materials = materials.filter(id=filters["material"])
    if filters.get("category"):
        materials = materials.filter(category__iexact=filters["category"])
    if filters.get("subcategory"):
        materials = materials.filter(subcategory__iexact=filters["subcategory"])
    if filters.get("brand"):
        materials = materials.filter(brand__iexact=filters["brand"])
    return materials.order_by("category", "subcategory", "name", "code")


def _balance_row(balance: StockBalance) -> dict:
    material = balance.material
    location = balance.location
    return {
        "balance": balance,
        "material": material,
        "location": location,
        "material_name": material.name,
        "material_code": material.code,
        "category": material.category,
        "subcategory": material.subcategory,
        "brand": material.brand,
        "unit": material.unit.code,
        "location_name": location.name,
        "quantity": balance.quantity,
        "material_admin_url": _admin_url("stock_material_change", material.id),
        "location_admin_url": _admin_url("stock_stocklocation_change", location.id),
        "balance_admin_url": _admin_url("stock_stockbalance_change", balance.id),
    }


def _movement_row(movement: StockMovement) -> dict:
    return {
        "movement": movement,
        "occurred_at": movement.occurred_at,
        "movement_type": movement.get_movement_type_display(),
        "movement_type_code": movement.movement_type,
        "material": movement.material,
        "material_name": movement.material.name,
        "unit": movement.material.unit.code,
        "location": movement.location,
        "target_location": movement.target_location,
        "quantity": movement.quantity,
        "balance_after": movement.balance_after,
        "created_by": movement.created_by,
        "note": movement.note,
        "movement_admin_url": _admin_url("stock_stockmovement_change", movement.id),
        "material_admin_url": _admin_url("stock_material_change", movement.material_id),
        "location_admin_url": _admin_url("stock_stocklocation_change", movement.location_id),
        "target_location_admin_url": _admin_url("stock_stocklocation_change", movement.target_location_id)
        if movement.target_location_id
        else "",
    }


def _missing_location_zero_rows(materials, location_id, existing_rows: list[dict]) -> list[dict]:
    location = StockLocation.objects.get(id=location_id)
    existing_material_ids = {row["material"].id for row in existing_rows}
    return [_zero_balance_row(material, location) for material in materials.exclude(id__in=existing_material_ids)]


def _missing_material_zero_rows(material, location_id, existing_rows: list[dict]) -> list[dict]:
    existing_location_ids = {row["location"].id for row in existing_rows}
    locations = StockLocation.objects.filter(is_active=True).exclude(id__in=existing_location_ids)
    if location_id:
        locations = locations.filter(id=location_id)
    return [_zero_balance_row(material, location) for location in locations.order_by("location_type", "name")]


def _zero_balance_row(material: Material, location: StockLocation) -> dict:
    return {
        "balance": None,
        "material": material,
        "location": location,
        "material_name": material.name,
        "material_code": material.code,
        "category": material.category,
        "subcategory": material.subcategory,
        "brand": material.brand,
        "unit": material.unit.code,
        "location_name": location.name,
        "quantity": Decimal("0.000"),
        "material_admin_url": _admin_url("stock_material_change", material.id),
        "location_admin_url": _admin_url("stock_stocklocation_change", location.id),
        "balance_admin_url": "",
    }


def _admin_url(name: str, object_id) -> str:
    if not object_id:
        return ""
    return reverse(f"admin:{name}", args=[object_id])
