from django.urls import path

from stock import views

app_name = "stock"

urlpatterns = [
    path("stock/reports/", views.stock_reports_home_view, name="reports_home"),
    path("stock/reports/by-location/", views.stock_by_location_report_view, name="report_by_location"),
    path("stock/reports/by-material/", views.stock_by_material_report_view, name="report_by_material"),
    path("stock/reports/matrix/", views.stock_matrix_report_view, name="report_matrix"),
    path("stock/reports/movements/", views.stock_movements_report_view, name="report_movements"),
]
