from datetime import date
from decimal import Decimal

from django.test import TestCase

from billing.models import FinancialStatus, MeasurementLine, MeasurementPeriod, MeasurementSettlement
from billing.services.measurement_calc import (
    finalize_period,
    get_item_cumulative,
    update_financial_status,
)
from catalog.models import BudgetItem, Unit
from core.models import Client, Project
from pricing.models import PriceIndex, PriceIndexValue, ProjectPriceAdjustment


class MeasurementCalcTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(name="Cliente Teste")
        self.project = Project.objects.create(name="Projeto Teste", client=self.client_obj)
        self.unit = Unit.objects.create(code="M2", name="Metro quadrado")
        self.item = BudgetItem.objects.create(
            project=self.project,
            eap_code="1.1",
            description="Item 1",
            unit=self.unit,
            qty_contracted=Decimal("100"),
            pu_material=Decimal("10"),
            pu_labor=Decimal("5"),
        )

    def _create_period(self, number: int, ref_month: date) -> MeasurementPeriod:
        return MeasurementPeriod.objects.create(
            project=self.project,
            number=number,
            ref_month=ref_month,
            start_date=ref_month,
            end_date=ref_month,
        )

    def test_get_item_cumulative(self):
        period1 = self._create_period(1, date(2026, 1, 1))
        period2 = self._create_period(2, date(2026, 2, 1))

        MeasurementLine.objects.create(
            period=period1,
            line_kind="CONTRACTED",
            item=self.item,
            qty_period=Decimal("2"),
        )
        MeasurementLine.objects.create(
            period=period2,
            line_kind="CONTRACTED",
            item=self.item,
            qty_period=Decimal("3"),
        )

        self.assertEqual(get_item_cumulative(self.project, self.item.id, 1), Decimal("2"))
        self.assertEqual(get_item_cumulative(self.project, self.item.id, 2), Decimal("5"))

    def test_update_financial_status(self):
        period = self._create_period(1, date(2026, 1, 1))
        period.total_total_snapshot = Decimal("100")
        period.total_indexed_snapshot = Decimal("120")
        period.save(update_fields=["total_total_snapshot", "total_indexed_snapshot"])

        self.assertEqual(update_financial_status(period.id), FinancialStatus.OPEN)

        MeasurementSettlement.objects.create(
            period=period,
            event_date=date(2026, 1, 10),
            amount=Decimal("50"),
            method="PIX",
        )
        self.assertEqual(update_financial_status(period.id), FinancialStatus.PARTIALLY_PAID)

        MeasurementSettlement.objects.create(
            period=period,
            event_date=date(2026, 1, 11),
            amount=Decimal("70"),
            method="TRANSFER",
        )
        self.assertEqual(update_financial_status(period.id), FinancialStatus.PAID)

    def test_finalize_period_without_incc_adjustment(self):
        period = self._create_period(1, date(2026, 1, 1))
        MeasurementLine.objects.create(
            period=period,
            line_kind="CONTRACTED",
            item=self.item,
            qty_period=Decimal("2"),
        )

        finalized = finalize_period(period.id)
        self.assertEqual(finalized.workflow_status, "FINALIZED")
        self.assertEqual(finalized.total_material_snapshot, Decimal("20.00"))
        self.assertEqual(finalized.total_labor_snapshot, Decimal("10.00"))
        self.assertEqual(finalized.total_total_snapshot, Decimal("30.00"))
        self.assertEqual(finalized.total_indexed_snapshot, Decimal("30.00"))
        self.assertEqual(finalized.index_factor_snapshot, Decimal("1.000000"))

    def test_finalize_period_with_incc_adjustment(self):
        period = self._create_period(1, date(2026, 2, 1))
        MeasurementLine.objects.create(
            period=period,
            line_kind="CONTRACTED",
            item=self.item,
            qty_period=Decimal("2"),
        )

        index = PriceIndex.objects.create(code="INCC_M", name="INCC M")
        PriceIndexValue.objects.create(price_index=index, ref_month=date(2026, 1, 1), value=Decimal("100"))
        PriceIndexValue.objects.create(price_index=index, ref_month=date(2026, 2, 1), value=Decimal("120"))
        ProjectPriceAdjustment.objects.create(
            project=self.project,
            price_index=index,
            base_month=date(2026, 1, 1),
            apply_to="TOTAL",
            is_active=True,
        )

        finalized = finalize_period(period.id)
        self.assertEqual(finalized.workflow_status, "FINALIZED")
        self.assertEqual(finalized.index_code_snapshot, "INCC_M")
        self.assertEqual(finalized.index_factor_snapshot, Decimal("1.200000"))
        self.assertEqual(finalized.total_total_snapshot, Decimal("30.00"))
        self.assertEqual(finalized.total_indexed_snapshot, Decimal("36.00"))
