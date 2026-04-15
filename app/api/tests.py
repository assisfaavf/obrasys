from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from billing.models import MeasurementLine, MeasurementLineKind, MeasurementPeriod
from catalog.models import BudgetItem, Unit
from core.models import Client, Project
from rdo.models import DailyWorkLog


@override_settings(ROOT_URLCONF="config.urls")
class ApiReadOnlyTests(TestCase):
    def setUp(self):
        self.api_client = APIClient()
        self.user = get_user_model().objects.create_user(
            username="staff",
            password="secret",
            is_staff=True,
        )
        self.client_record = Client.objects.create(name="Cliente API")
        self.project = Project.objects.create(name="Obra API", client=self.client_record)
        self.unit = Unit.objects.create(code="m2", name="Metro quadrado")
        self.item = BudgetItem.objects.create(
            project=self.project,
            eap_code="1.1",
            description="Alvenaria",
            unit=self.unit,
            qty_contracted=Decimal("100.000"),
            pu_material=Decimal("10.0000"),
            pu_labor=Decimal("5.0000"),
        )
        self.period = MeasurementPeriod.objects.create(
            project=self.project,
            number=1,
            ref_month="2026-04-01",
            start_date="2026-04-01",
            end_date="2026-04-30",
        )
        MeasurementLine.objects.create(
            period=self.period,
            line_kind=MeasurementLineKind.CONTRACTED,
            item=self.item,
            qty_period=Decimal("2.000"),
        )
        self.daily_log = DailyWorkLog.objects.create(
            project=self.project,
            log_date="2026-04-15",
            responsible_name="Engenheiro",
            created_by=self.user,
        )

    def test_api_requires_staff_user(self):
        response = self.api_client.get(reverse("api:project-list"))

        self.assertIn(response.status_code, {401, 403})

    def test_project_list_returns_project_summary(self):
        self.api_client.force_authenticate(self.user)

        response = self.api_client.get(reverse("api:project-list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["name"], "Obra API")
        self.assertEqual(response.data["results"][0]["client_name"], "Cliente API")
        self.assertEqual(response.data["results"][0]["budget_items_count"], 1)

    def test_budget_items_can_be_filtered_by_project(self):
        other_client = Client.objects.create(name="Outro cliente")
        other_project = Project.objects.create(name="Outra obra", client=other_client)
        BudgetItem.objects.create(
            project=other_project,
            eap_code="9.9",
            description="Outro item",
            unit=self.unit,
            qty_contracted=Decimal("1.000"),
            pu_material=Decimal("1.0000"),
            pu_labor=Decimal("1.0000"),
        )
        self.api_client.force_authenticate(self.user)

        response = self.api_client.get(reverse("api:budgetitem-list"), {"project": self.project.id})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["eap_code"], "1.1")

    def test_measurement_period_totals_endpoint_returns_live_totals(self):
        self.api_client.force_authenticate(self.user)

        response = self.api_client.get(
            reverse("api:measurementperiod-totals", kwargs={"pk": self.period.id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_material"], "20.00")
        self.assertEqual(response.data["total_labor"], "10.00")
        self.assertEqual(response.data["total_total"], "30.00")

    def test_daily_work_logs_can_be_filtered_by_project(self):
        self.api_client.force_authenticate(self.user)

        response = self.api_client.get(reverse("api:dailyworklog-list"), {"project": self.project.id})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["responsible_name"], "Engenheiro")
