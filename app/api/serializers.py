from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from rest_framework import serializers

from billing.models import MeasurementPeriod, WorkflowStatus
from billing.services.measurement_calc import compute_incc_factor, compute_period_totals
from catalog.models import BudgetItem
from core.models import Project
from pricing.models import AdjustmentApplyTo
from rdo.models import DailyWorkLog

MONEY_Q = Decimal("0.01")


def _money(value: Decimal) -> str:
    return str((value or Decimal("0")).quantize(MONEY_Q, rounding=ROUND_HALF_UP))


def _compute_indexed_total(
    *,
    total_material: Decimal,
    total_labor: Decimal,
    apply_to: str,
    factor: Decimal,
) -> Decimal:
    if apply_to == AdjustmentApplyTo.MATERIAL:
        return (total_material * factor) + total_labor
    if apply_to == AdjustmentApplyTo.LABOR:
        return total_material + (total_labor * factor)
    return (total_material + total_labor) * factor


class ProjectSerializer(serializers.ModelSerializer):
    client_name = serializers.CharField(source="client.name", read_only=True)
    location_template_name = serializers.CharField(source="location_template.name", read_only=True)
    locations_count = serializers.IntegerField(read_only=True)
    budget_items_count = serializers.IntegerField(read_only=True)
    measurement_periods_count = serializers.IntegerField(read_only=True)
    daily_work_logs_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Project
        fields = (
            "id",
            "name",
            "client",
            "client_name",
            "location_template",
            "location_template_name",
            "address",
            "start_date",
            "planned_end_date",
            "status",
            "locations_count",
            "budget_items_count",
            "measurement_periods_count",
            "daily_work_logs_count",
        )


class BudgetItemSerializer(serializers.ModelSerializer):
    project_name = serializers.CharField(source="project.name", read_only=True)
    unit_code = serializers.CharField(source="unit.code", read_only=True)
    discipline_name = serializers.CharField(source="discipline.name", read_only=True)

    class Meta:
        model = BudgetItem
        fields = (
            "id",
            "project",
            "project_name",
            "eap_code",
            "description",
            "unit",
            "unit_code",
            "qty_contracted",
            "pu_material",
            "pu_labor",
            "discipline",
            "discipline_name",
            "is_active",
        )


class MeasurementPeriodTotalsSerializer(serializers.ModelSerializer):
    project_name = serializers.CharField(source="project.name", read_only=True)
    total_material = serializers.SerializerMethodField()
    total_labor = serializers.SerializerMethodField()
    total_total = serializers.SerializerMethodField()
    total_indexed = serializers.SerializerMethodField()
    index_code = serializers.SerializerMethodField()
    index_base_month = serializers.SerializerMethodField()
    index_ref_month = serializers.SerializerMethodField()
    index_factor = serializers.SerializerMethodField()
    index_apply_to = serializers.SerializerMethodField()
    index_error = serializers.SerializerMethodField()

    class Meta:
        model = MeasurementPeriod
        fields = (
            "id",
            "project",
            "project_name",
            "number",
            "ref_month",
            "workflow_status",
            "financial_status",
            "total_material",
            "total_labor",
            "total_total",
            "total_indexed",
            "index_code",
            "index_base_month",
            "index_ref_month",
            "index_factor",
            "index_apply_to",
            "index_error",
        )

    def _totals(self, obj: MeasurementPeriod) -> dict:
        cache_name = "_api_totals"
        if hasattr(obj, cache_name):
            return getattr(obj, cache_name)

        if obj.workflow_status == WorkflowStatus.DRAFT:
            total_material, total_labor, total_total = compute_period_totals(obj.id)
            data = {
                "total_material": total_material,
                "total_labor": total_labor,
                "total_total": total_total,
                "total_indexed": total_total,
                "index_code": "",
                "index_base_month": None,
                "index_ref_month": None,
                "index_factor": Decimal("1.0"),
                "index_apply_to": AdjustmentApplyTo.TOTAL,
                "index_error": "",
            }
            try:
                incc_data = compute_incc_factor(obj.project, obj.ref_month)
            except ValidationError as exc:
                data["index_error"] = "; ".join(exc.messages)
            else:
                data.update(
                    {
                        "total_indexed": _compute_indexed_total(
                            total_material=total_material,
                            total_labor=total_labor,
                            apply_to=incc_data["apply_to"],
                            factor=incc_data["factor"],
                        ),
                        "index_code": incc_data["code"],
                        "index_base_month": incc_data["base_month"],
                        "index_ref_month": incc_data["ref_month"],
                        "index_factor": incc_data["factor"],
                        "index_apply_to": incc_data["apply_to"],
                    }
                )
        else:
            data = {
                "total_material": obj.total_material_snapshot,
                "total_labor": obj.total_labor_snapshot,
                "total_total": obj.total_total_snapshot,
                "total_indexed": obj.total_indexed_snapshot,
                "index_code": obj.index_code_snapshot,
                "index_base_month": obj.index_base_month_snapshot,
                "index_ref_month": obj.index_ref_month_snapshot,
                "index_factor": obj.index_factor_snapshot,
                "index_apply_to": "",
                "index_error": "",
            }

        setattr(obj, cache_name, data)
        return data

    def get_total_material(self, obj):
        return _money(self._totals(obj)["total_material"])

    def get_total_labor(self, obj):
        return _money(self._totals(obj)["total_labor"])

    def get_total_total(self, obj):
        return _money(self._totals(obj)["total_total"])

    def get_total_indexed(self, obj):
        return _money(self._totals(obj)["total_indexed"])

    def get_index_code(self, obj):
        return self._totals(obj)["index_code"]

    def get_index_base_month(self, obj):
        return self._totals(obj)["index_base_month"]

    def get_index_ref_month(self, obj):
        return self._totals(obj)["index_ref_month"]

    def get_index_factor(self, obj):
        return str(self._totals(obj)["index_factor"])

    def get_index_apply_to(self, obj):
        return self._totals(obj)["index_apply_to"]

    def get_index_error(self, obj):
        return self._totals(obj)["index_error"]


class MeasurementPeriodSerializer(MeasurementPeriodTotalsSerializer):
    lines_count = serializers.IntegerField(read_only=True)
    settlements_count = serializers.IntegerField(read_only=True)

    class Meta(MeasurementPeriodTotalsSerializer.Meta):
        fields = MeasurementPeriodTotalsSerializer.Meta.fields + (
            "start_date",
            "end_date",
            "notes",
            "created_at",
            "finalized_at",
            "sent_at",
            "in_review_at",
            "authorized_at",
            "rejected_at",
            "cancelled_at",
            "lines_count",
            "settlements_count",
        )


class DailyWorkLogSerializer(serializers.ModelSerializer):
    project_name = serializers.CharField(source="project.name", read_only=True)
    weather_morning_display = serializers.CharField(source="get_weather_morning_display", read_only=True)
    weather_afternoon_display = serializers.CharField(source="get_weather_afternoon_display", read_only=True)
    weather_night_display = serializers.CharField(source="get_weather_night_display", read_only=True)
    team_entries_count = serializers.IntegerField(read_only=True)
    activity_entries_count = serializers.IntegerField(read_only=True)
    occurrences_count = serializers.IntegerField(read_only=True)
    material_entries_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = DailyWorkLog
        fields = (
            "id",
            "project",
            "project_name",
            "log_date",
            "responsible_name",
            "weather_morning",
            "weather_morning_display",
            "weather_afternoon",
            "weather_afternoon_display",
            "weather_night",
            "weather_night_display",
            "notes",
            "general_observation",
            "interruption_reason",
            "created_by",
            "created_at",
            "updated_at",
            "team_entries_count",
            "activity_entries_count",
            "occurrences_count",
            "material_entries_count",
        )
