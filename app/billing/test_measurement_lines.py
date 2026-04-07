from datetime import date
from decimal import Decimal

from django.test import TestCase

from billing.models import MeasurementLine, MeasurementPeriod
from billing.services.measurement_lines import add_or_merge_contracted_line
from catalog.models import BudgetItem, Unit
from core.models import Client, Project


class ContractedLineMergeTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(name="Cliente Merge")
        self.project = Project.objects.create(name="Projeto Merge", client=self.client_obj)
        self.unit = Unit.objects.create(code="M2", name="Metro quadrado")
        self.item = BudgetItem.objects.create(
            project=self.project,
            eap_code="6.6.1",
            description="Cabo 2,5mm",
            unit=self.unit,
            qty_contracted=Decimal("30"),
            pu_material=Decimal("10"),
            pu_labor=Decimal("5"),
        )
        self.location_ter = self.project.locations.create(code="TER", name="Terreo", order_index=1)
        self.location_cob = self.project.locations.create(code="COB", name="Cobertura", order_index=2)
        self.period = MeasurementPeriod.objects.create(
            project=self.project,
            number=1,
            ref_month=date(2026, 4, 1),
            start_date=date(2026, 4, 1),
            end_date=date(2026, 4, 30),
        )

    def test_same_item_and_same_location_merges_qty(self):
        first_line, merged = add_or_merge_contracted_line(
            period=self.period,
            item=self.item,
            location=self.location_ter,
            qty_period=Decimal("10"),
            note="primeiro",
        )
        self.assertFalse(merged)

        final_line, merged = add_or_merge_contracted_line(
            period=self.period,
            item=self.item,
            location=self.location_ter,
            qty_period=Decimal("5"),
            note="segundo",
        )

        self.assertTrue(merged)
        self.assertEqual(MeasurementLine.objects.filter(period=self.period).count(), 1)
        self.assertEqual(final_line.id, first_line.id)
        self.assertEqual(final_line.qty_period, Decimal("15"))
        self.assertEqual(final_line.note, "primeiro | segundo")

    def test_same_item_and_different_location_creates_new_line(self):
        add_or_merge_contracted_line(
            period=self.period,
            item=self.item,
            location=self.location_ter,
            qty_period=Decimal("10"),
        )
        add_or_merge_contracted_line(
            period=self.period,
            item=self.item,
            location=self.location_cob,
            qty_period=Decimal("5"),
        )

        self.assertEqual(
            MeasurementLine.objects.filter(period=self.period, line_kind="CONTRACTED").count(),
            2,
        )

    def test_same_item_with_null_location_merges_qty(self):
        add_or_merge_contracted_line(
            period=self.period,
            item=self.item,
            location=None,
            qty_period=Decimal("3"),
        )
        final_line, merged = add_or_merge_contracted_line(
            period=self.period,
            item=self.item,
            location=None,
            qty_period=Decimal("2"),
        )

        self.assertTrue(merged)
        self.assertEqual(
            MeasurementLine.objects.filter(period=self.period, item=self.item, location__isnull=True).count(),
            1,
        )
        self.assertEqual(final_line.qty_period, Decimal("5"))

    def test_extra_lines_are_not_merged(self):
        MeasurementLine.objects.create(
            period=self.period,
            line_kind="EXTRA",
            extra_description="Servico extra A",
            extra_unit=self.unit,
            qty_period=Decimal("2"),
            justification="motivo A",
        )
        MeasurementLine.objects.create(
            period=self.period,
            line_kind="EXTRA",
            extra_description="Servico extra A",
            extra_unit=self.unit,
            qty_period=Decimal("2"),
            justification="motivo B",
        )

        self.assertEqual(
            MeasurementLine.objects.filter(period=self.period, line_kind="EXTRA").count(),
            2,
        )

    def test_merge_keeps_excess_calculation_active(self):
        self.item.qty_contracted = Decimal("12")
        self.item.save(update_fields=["qty_contracted"])

        first_line, _ = add_or_merge_contracted_line(
            period=self.period,
            item=self.item,
            location=self.location_ter,
            qty_period=Decimal("10"),
        )
        second_line, _ = add_or_merge_contracted_line(
            period=self.period,
            item=self.item,
            location=self.location_cob,
            qty_period=Decimal("1"),
        )

        final_line, merged = add_or_merge_contracted_line(
            period=self.period,
            item=self.item,
            location=self.location_ter,
            qty_period=Decimal("5"),
            excess_justification="ajuste de campo",
        )

        first_line.refresh_from_db()
        second_line.refresh_from_db()
        self.assertTrue(merged)
        self.assertEqual(final_line.id, first_line.id)
        self.assertEqual(first_line.qty_period, Decimal("15"))
        self.assertEqual(first_line.excess_qty, Decimal("3"))
        self.assertEqual(first_line.excess_justification, "ajuste de campo")
        self.assertEqual(second_line.excess_qty, Decimal("1"))
