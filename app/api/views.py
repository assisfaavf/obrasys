from django.db.models import Count
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from billing.models import MeasurementPeriod
from catalog.models import BudgetItem
from core.models import Project, ProjectStage
from rdo.models import DailyWorkLog

from api.serializers import (
    BudgetItemSerializer,
    DailyWorkLogSerializer,
    MeasurementPeriodSerializer,
    MeasurementPeriodTotalsSerializer,
    ProjectSerializer,
    ProjectStageSerializer,
)


class ProjectViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ProjectSerializer

    def get_queryset(self):
        return (
            Project.objects.select_related("client", "location_template")
            .annotate(
                locations_count=Count("locations", distinct=True),
                budget_items_count=Count("budget_items", distinct=True),
                measurement_periods_count=Count("measurement_periods", distinct=True),
                daily_work_logs_count=Count("daily_work_logs", distinct=True),
            )
            .order_by("name", "id")
        )


class BudgetItemViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = BudgetItemSerializer

    def get_queryset(self):
        queryset = BudgetItem.objects.select_related("project", "unit", "discipline").order_by(
            "project_id",
            "eap_code",
            "id",
        )
        project_id = self.request.query_params.get("project")
        if project_id:
            queryset = queryset.filter(project_id=project_id)

        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() in {"1", "true", "yes", "sim"})
        return queryset


class ProjectStageViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ProjectStageSerializer

    def get_queryset(self):
        queryset = ProjectStage.objects.select_related("project").order_by("project_id", "order_index", "code", "id")
        project_id = self.request.query_params.get("project")
        if project_id:
            queryset = queryset.filter(project_id=project_id)

        is_active = self.request.query_params.get("is_active")
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() in {"1", "true", "yes", "sim"})
        return queryset


class MeasurementPeriodViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = MeasurementPeriodSerializer

    def get_queryset(self):
        queryset = (
            MeasurementPeriod.objects.select_related("project", "project__client")
            .annotate(
                lines_count=Count("lines", distinct=True),
                settlements_count=Count("settlements", distinct=True),
            )
            .order_by("project_id", "number", "id")
        )
        project_id = self.request.query_params.get("project")
        if project_id:
            queryset = queryset.filter(project_id=project_id)

        workflow_status = self.request.query_params.get("workflow_status")
        if workflow_status:
            queryset = queryset.filter(workflow_status=workflow_status)
        return queryset

    @action(detail=True, methods=["get"])
    def totals(self, request, pk=None):
        period = self.get_object()
        serializer = MeasurementPeriodTotalsSerializer(period, context=self.get_serializer_context())
        return Response(serializer.data)


class DailyWorkLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = DailyWorkLogSerializer

    def get_queryset(self):
        queryset = (
            DailyWorkLog.objects.select_related("project", "project__client", "created_by")
            .annotate(
                team_entries_count=Count("team_entries", distinct=True),
                activity_entries_count=Count("activity_entries", distinct=True),
                occurrences_count=Count("occurrences", distinct=True),
                material_entries_count=Count("material_entries", distinct=True),
            )
            .order_by("-log_date", "-id")
        )
        project_id = self.request.query_params.get("project")
        if project_id:
            queryset = queryset.filter(project_id=project_id)

        log_date = self.request.query_params.get("log_date")
        if log_date:
            queryset = queryset.filter(log_date=log_date)
        return queryset
