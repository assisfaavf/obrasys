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
        "measurements/<int:measurement_id>/export/pdf",
        views.measurement_export_pdf_view,
        name="measurement_export_pdf",
    ),
    path("measurement-lines/<int:line_id>/edit/", views.measurement_line_edit_view, name="line_edit"),
    path("measurement-lines/<int:line_id>/delete/", views.measurement_line_delete_view, name="line_delete"),
]
