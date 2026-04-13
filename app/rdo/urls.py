from django.urls import path

from rdo import views

app_name = "rdo"

urlpatterns = [
    path("projects/<int:project_id>/rdo/", views.rdo_project_list_view, name="project_rdo_list"),
    path("projects/<int:project_id>/rdo/new", views.rdo_new_view, name="daily_log_new"),
    path("projects/<int:project_id>/work-order/", views.work_order_view, name="work_order"),
    path("rdo/<int:daily_log_id>/", views.daily_log_detail_view, name="daily_log_detail"),
    path("rdo/<int:daily_log_id>/export/xlsx", views.rdo_export_xlsx_view, name="rdo_export_xlsx"),
]
