from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.shortcuts import render

from stock.models import Material, StockLocation, StockMovementType
from stock.stock_reports import (
    get_stock_by_location_report,
    get_stock_by_material_report,
    get_stock_matrix_report,
    get_stock_movements_report,
    get_stock_summary_report,
)


@staff_member_required
def stock_reports_home_view(request):
    context = {
        "summary": get_stock_summary_report(_filters(request)),
        **_filter_options(),
    }
    return render(request, "stock/reports/home.html", context)


@staff_member_required
def stock_by_location_report_view(request):
    filters = _filters(request)
    context = {
        "filters": filters,
        "report": get_stock_by_location_report(filters),
        **_filter_options(),
    }
    return render(request, "stock/reports/by_location.html", context)


@staff_member_required
def stock_by_material_report_view(request):
    filters = _filters(request)
    context = {
        "filters": filters,
        "report": get_stock_by_material_report(filters),
        **_filter_options(),
    }
    return render(request, "stock/reports/by_material.html", context)


@staff_member_required
def stock_matrix_report_view(request):
    filters = _filters(request)
    context = {
        "filters": filters,
        "report": get_stock_matrix_report(filters),
        **_filter_options(),
    }
    return render(request, "stock/reports/matrix.html", context)


@staff_member_required
def stock_movements_report_view(request):
    filters = _filters(request)
    context = {
        "filters": filters,
        "report": get_stock_movements_report(filters),
        **_filter_options(include_users=True),
    }
    return render(request, "stock/reports/movements.html", context)


def _filters(request) -> dict:
    return {
        "stock_location": request.GET.get("stock_location", ""),
        "material": request.GET.get("material", ""),
        "category": request.GET.get("category", ""),
        "subcategory": request.GET.get("subcategory", ""),
        "brand": request.GET.get("brand", ""),
        "movement_type": request.GET.get("movement_type", ""),
        "date_from": request.GET.get("date_from", ""),
        "date_to": request.GET.get("date_to", ""),
        "user": request.GET.get("user", ""),
        "show_zero": request.GET.get("show_zero", ""),
    }


def _filter_options(*, include_users: bool = False) -> dict:
    materials = Material.objects.filter(is_active=True).order_by("name", "code")
    options = {
        "materials": materials,
        "locations": StockLocation.objects.filter(is_active=True).order_by("location_type", "name", "code"),
        "categories": _distinct_material_values("category"),
        "subcategories": _distinct_material_values("subcategory"),
        "brands": _distinct_material_values("brand"),
        "movement_types": StockMovementType.choices,
    }
    if include_users:
        options["users"] = get_user_model().objects.filter(stock_movements__isnull=False).distinct().order_by("username")
    return options


def _distinct_material_values(field_name: str) -> list[str]:
    return list(
        Material.objects.filter(is_active=True)
        .exclude(**{field_name: ""})
        .order_by(field_name)
        .values_list(field_name, flat=True)
        .distinct()
    )
