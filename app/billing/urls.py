from django.urls import path

from billing import views

app_name = "billing"

urlpatterns = [
    path("projects/", views.project_list_view, name="project_list"),
    path("projects/<int:project_id>/", views.project_detail_view, name="project_detail"),
    path(
        "projects/<int:project_id>/measurements/new",
        views.measurement_new_view,
        name="measurement_new",
    ),
    path("measurements/<int:measurement_id>/", views.measurement_detail_view, name="measurement_detail"),
    path(
        "measurements/<int:measurement_id>/materials/bulk-add/",
        views.measurement_bulk_materials_add_view,
        name="measurement_bulk_materials_add",
    ),
    path(
        "measurements/<int:measurement_id>/export/xlsx",
        views.measurement_export_xlsx_view,
        name="measurement_export_xlsx",
    ),
    path(
        "measurements/<int:measurement_id>/export/sienge-snapshot",
        views.measurement_export_sienge_snapshot_view,
        name="measurement_export_sienge_snapshot",
    ),
    path(
        "projects/<int:project_id>/export/sienge-master",
        views.project_export_sienge_master_view,
        name="project_export_sienge_master",
    ),
    path("measurement-lines/<int:line_id>/edit/", views.measurement_line_edit_view, name="line_edit"),
    path("measurement-lines/<int:line_id>/delete/", views.measurement_line_delete_view, name="line_delete"),
    path(
        "measurement-line-histories/<int:history_id>/edit/",
        views.measurement_line_history_edit_view,
        name="line_history_edit",
    ),
]
