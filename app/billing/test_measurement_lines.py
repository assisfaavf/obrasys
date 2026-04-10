from datetime import datetime
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from billing.forms import ContractedLineAddForm
from billing.models import MeasurementLine, MeasurementLineHistory, MeasurementPeriod
from billing.services.measurement_lines import add_or_merge_contracted_line
from catalog.models import BudgetItem, BudgetItemAdditionalMaterial, Unit
from core.models import Client, Project


class ContractedLineMergeTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="staff-merge",
            password="secret123",
            is_staff=True,
        )
        self.client.force_login(self.user)
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
        self.screw_item = BudgetItem.objects.create(
            project=self.project,
            eap_code="6.6.2",
            description="Parafuso",
            unit=self.unit,
            qty_contracted=Decimal("200"),
            pu_material=Decimal("1"),
            pu_labor=Decimal("0"),
        )
        self.anchor_item = BudgetItem.objects.create(
            project=self.project,
            eap_code="6.6.3",
            description="Bucha",
            unit=self.unit,
            qty_contracted=Decimal("200"),
            pu_material=Decimal("1"),
            pu_labor=Decimal("0"),
        )
        self.item_b = BudgetItem.objects.create(
            project=self.project,
            eap_code="6.6.4",
            description="Tomada 10A",
            unit=self.unit,
            qty_contracted=Decimal("30"),
            pu_material=Decimal("12"),
            pu_labor=Decimal("6"),
        )
        BudgetItemAdditionalMaterial.objects.create(
            parent_item=self.item,
            additional_item=self.screw_item,
            quantity_per_unit=Decimal("2.0000"),
            note="2 parafusos por unidade",
        )
        BudgetItemAdditionalMaterial.objects.create(
            parent_item=self.item,
            additional_item=self.anchor_item,
            quantity_per_unit=Decimal("2.0000"),
            note="2 buchas por unidade",
        )
        BudgetItemAdditionalMaterial.objects.create(
            parent_item=self.item_b,
            additional_item=self.screw_item,
            quantity_per_unit=Decimal("1.0000"),
            note="1 parafuso por unidade",
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

    def test_addition_with_manual_application_date_creates_history(self):
        line, merged = add_or_merge_contracted_line(
            period=self.period,
            item=self.item,
            location=self.location_ter,
            qty_period=Decimal("4.500"),
            application_date=date(2026, 4, 5),
            note="lancamento manual",
            created_by=self.user,
        )

        history = line.histories.get()
        self.assertFalse(merged)
        self.assertEqual(history.quantity_added, Decimal("4.500"))
        self.assertEqual(history.application_date, date(2026, 4, 5))
        self.assertEqual(history.note, "lancamento manual")
        self.assertEqual(history.created_by, self.user)

    def test_measurement_detail_addition_with_today_checkbox_defaults_application_date(self):
        url = reverse("billing:measurement_detail", args=[self.period.id])
        expected_today = date(2026, 4, 10)

        with (
            patch("billing.forms.timezone.localdate", return_value=expected_today),
            patch("billing.views.timezone.localdate", return_value=expected_today),
            patch("billing.services.measurement_lines.timezone.localdate", return_value=expected_today),
        ):
            response = self.client.post(
                url,
                {
                    "action": "add_contracted",
                    "contracted-item": str(self.item.id),
                    "contracted-location": str(self.location_ter.id),
                    "contracted-qty_period": "2.000",
                    "contracted-use_today": "on",
                    "contracted-note": "feito hoje",
                },
            )

        self.assertEqual(response.status_code, 302)
        history = MeasurementLineHistory.objects.get()
        self.assertEqual(history.application_date, expected_today)
        self.assertEqual(history.note, "feito hoje")

    def test_measurement_detail_merge_keeps_sum_and_creates_one_history_per_addition(self):
        add_or_merge_contracted_line(
            period=self.period,
            item=self.item,
            location=self.location_ter,
            qty_period=Decimal("3"),
            application_date=date(2026, 4, 2),
            note="primeira",
            created_by=self.user,
        )

        url = reverse("billing:measurement_detail", args=[self.period.id])
        response = self.client.post(
            url,
            {
                "action": "add_contracted",
                "contracted-item": str(self.item.id),
                "contracted-location": str(self.location_ter.id),
                "contracted-qty_period": "1.500",
                "contracted-application_date": "2026-04-03",
                "contracted-note": "segunda",
            },
        )

        self.assertEqual(response.status_code, 302)
        line = MeasurementLine.objects.get(period=self.period, item=self.item, location=self.location_ter)
        histories = list(line.histories.order_by("created_at", "id"))

        self.assertEqual(line.qty_period, Decimal("4.500"))
        self.assertEqual(len(histories), 2)
        self.assertEqual(histories[0].quantity_added, Decimal("3.000"))
        self.assertEqual(histories[1].quantity_added, Decimal("1.500"))
        self.assertEqual(histories[1].application_date, date(2026, 4, 3))
        self.assertEqual(histories[1].created_by, self.user)

    def test_contracted_add_form_defaults_today_when_date_is_blank(self):
        expected_today = date(2026, 4, 11)

        with patch("billing.forms.timezone.localdate", return_value=expected_today):
            form = ContractedLineAddForm(
                data={
                    "item": str(self.item.id),
                    "location": str(self.location_ter.id),
                    "qty_period": "1.000",
                    "note": "",
                    "excess_justification": "",
                },
                period=self.period,
            )
            self.assertTrue(form.is_valid(), form.errors.as_text())

        self.assertEqual(form.cleaned_data["application_date"], expected_today)

    def test_line_edit_view_shows_history_ordered_by_application_date_then_created_at(self):
        line, _ = add_or_merge_contracted_line(
            period=self.period,
            item=self.item,
            location=self.location_ter,
            qty_period=Decimal("1"),
            application_date=date(2026, 4, 6),
            note="base",
            created_by=self.user,
        )
        base_history = line.histories.get(note="base")
        history_older = MeasurementLineHistory.objects.create(
            line=line,
            quantity_added=Decimal("2.000"),
            application_date=date(2026, 4, 6),
            note="mais antigo",
            created_by=self.user,
        )
        history_newer_same_day = MeasurementLineHistory.objects.create(
            line=line,
            quantity_added=Decimal("3.000"),
            application_date=date(2026, 4, 6),
            note="mais recente no mesmo dia",
            created_by=self.user,
        )
        history_latest_date = MeasurementLineHistory.objects.create(
            line=line,
            quantity_added=Decimal("4.000"),
            application_date=date(2026, 4, 7),
            note="data mais nova",
            created_by=self.user,
        )

        tz = timezone.get_current_timezone()
        MeasurementLineHistory.objects.filter(pk=base_history.pk).update(
            created_at=timezone.make_aware(datetime(2026, 4, 6, 7, 0), tz)
        )
        MeasurementLineHistory.objects.filter(pk=history_older.pk).update(
            created_at=timezone.make_aware(datetime(2026, 4, 6, 8, 0), tz)
        )
        MeasurementLineHistory.objects.filter(pk=history_newer_same_day.pk).update(
            created_at=timezone.make_aware(datetime(2026, 4, 6, 9, 0), tz)
        )
        MeasurementLineHistory.objects.filter(pk=history_latest_date.pk).update(
            created_at=timezone.make_aware(datetime(2026, 4, 7, 7, 0), tz)
        )

        response = self.client.get(reverse("billing:line_edit", args=[line.id]))

        self.assertEqual(response.status_code, 200)
        histories = list(response.context["histories"])
        self.assertEqual(
            [history.note for history in histories[:4]],
            ["data mais nova", "mais recente no mesmo dia", "mais antigo", "base"],
        )

    def test_history_row_can_be_edited_and_recalculates_line_total(self):
        line, _ = add_or_merge_contracted_line(
            period=self.period,
            item=self.item,
            location=self.location_ter,
            qty_period=Decimal("3.000"),
            application_date=date(2026, 4, 4),
            note="primeira",
            created_by=self.user,
        )
        add_or_merge_contracted_line(
            period=self.period,
            item=self.item,
            location=self.location_ter,
            qty_period=Decimal("1.500"),
            application_date=date(2026, 4, 5),
            note="segunda",
            created_by=self.user,
        )
        first_history = line.histories.get(note="primeira")

        response = self.client.post(
            reverse("billing:line_history_edit", args=[first_history.id]),
            {
                "quantity_added": "2.000",
                "application_date": "2026-04-06",
                "note": "ajustada",
            },
        )

        self.assertEqual(response.status_code, 302)
        line.refresh_from_db()
        first_history.refresh_from_db()
        self.assertEqual(line.qty_period, Decimal("3.500"))
        self.assertEqual(first_history.quantity_added, Decimal("2.000"))
        self.assertEqual(first_history.application_date, date(2026, 4, 6))
        self.assertEqual(first_history.note, "ajustada")

    def test_line_edit_keeps_history_sum_in_sync(self):
        line, _ = add_or_merge_contracted_line(
            period=self.period,
            item=self.item,
            location=self.location_ter,
            qty_period=Decimal("3.000"),
            application_date=date(2026, 4, 4),
            note="primeira",
            created_by=self.user,
        )
        add_or_merge_contracted_line(
            period=self.period,
            item=self.item,
            location=self.location_ter,
            qty_period=Decimal("1.500"),
            application_date=date(2026, 4, 5),
            note="segunda",
            created_by=self.user,
        )

        response = self.client.post(
            reverse("billing:line_edit", args=[line.id]),
            {
                "item": str(self.item.id),
                "location": str(self.location_ter.id),
                "qty_period": "4.000",
                "note": "ajuste total",
                "excess_justification": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        line.refresh_from_db()
        self.assertEqual(line.qty_period, Decimal("4.000"))
        self.assertEqual(
            sum(
                line.histories.filter(is_generated_additional_entry=False).values_list("quantity_added", flat=True),
                Decimal("0"),
            ),
            Decimal("4.000"),
        )

    def test_editing_history_entry_updates_generated_additional_totals(self):
        line, _ = add_or_merge_contracted_line(
            period=self.period,
            item=self.item,
            location=self.location_ter,
            qty_period=Decimal("10.000"),
            application_date=date(2026, 4, 8),
            use_additional_materials=True,
            created_by=self.user,
        )
        history = line.histories.get()

        response = self.client.post(
            reverse("billing:line_history_edit", args=[history.id]),
            {
                "quantity_added": "4.000",
                "application_date": "2026-04-08",
                "note": "reduzido no historico",
                "uses_additional_materials": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        line.refresh_from_db()
        screw_line = MeasurementLine.objects.get(
            period=self.period,
            is_generated_additional=True,
            item=self.screw_item,
            location=self.location_ter,
        )
        self.assertEqual(line.qty_period, Decimal("4.000"))
        self.assertEqual(line.additional_materials_base_qty, Decimal("4.000"))
        self.assertEqual(screw_line.qty_period, Decimal("8.000"))

    def test_item_with_two_additional_materials_generates_contracted_lines(self):
        line, _ = add_or_merge_contracted_line(
            period=self.period,
            item=self.item,
            location=self.location_ter,
            qty_period=Decimal("10.000"),
            application_date=date(2026, 4, 8),
            note="principal",
            use_additional_materials=True,
            created_by=self.user,
        )

        generated_lines = list(
            MeasurementLine.objects.filter(
                period=self.period,
                is_generated_additional=True,
            ).order_by("item__eap_code")
        )

        self.assertEqual(line.additional_materials_base_qty, Decimal("10.000"))
        self.assertEqual(len(generated_lines), 2)
        self.assertEqual([generated.item_id for generated in generated_lines], [self.screw_item.id, self.anchor_item.id])
        self.assertTrue(all(generated.is_generated_additional for generated in generated_lines))
        self.assertTrue(all(generated.location_id == self.location_ter.id for generated in generated_lines))
        self.assertEqual(generated_lines[0].qty_period, Decimal("20.000"))
        self.assertEqual(generated_lines[1].qty_period, Decimal("20.000"))
        self.assertEqual(generated_lines[0].histories.get().application_date, date(2026, 4, 8))
        self.assertEqual(generated_lines[0].histories.get().source_item_snapshot, "6.6.1 - Cabo 2,5mm")

    def test_unchecked_checkbox_does_not_generate_additional_lines(self):
        line, _ = add_or_merge_contracted_line(
            period=self.period,
            item=self.item,
            location=self.location_ter,
            qty_period=Decimal("10.000"),
            use_additional_materials=False,
            created_by=self.user,
        )

        self.assertEqual(line.additional_materials_base_qty, Decimal("0.000"))
        self.assertFalse(
            MeasurementLine.objects.filter(period=self.period, is_generated_additional=True).exists()
        )

    def test_merge_without_additional_materials_does_not_increase_generated_quantities(self):
        line, _ = add_or_merge_contracted_line(
            period=self.period,
            item=self.item,
            location=self.location_ter,
            qty_period=Decimal("10.000"),
            use_additional_materials=True,
            created_by=self.user,
        )

        line, merged = add_or_merge_contracted_line(
            period=self.period,
            item=self.item,
            location=self.location_ter,
            qty_period=Decimal("5.000"),
            use_additional_materials=False,
            created_by=self.user,
        )

        generated_lines = list(
            MeasurementLine.objects.filter(period=self.period, is_generated_additional=True).order_by("item__eap_code")
        )
        self.assertTrue(merged)
        self.assertEqual(line.qty_period, Decimal("15.000"))
        self.assertEqual(line.additional_materials_base_qty, Decimal("10.000"))
        self.assertEqual([generated.qty_period for generated in generated_lines], [Decimal("20.000"), Decimal("20.000")])

    def test_edit_quantity_updates_generated_additional_lines(self):
        line, _ = add_or_merge_contracted_line(
            period=self.period,
            item=self.item,
            location=self.location_ter,
            qty_period=Decimal("10.000"),
            use_additional_materials=True,
            created_by=self.user,
        )

        response = self.client.post(
            reverse("billing:line_edit", args=[line.id]),
            {
                "item": str(self.item.id),
                "location": str(self.location_ter.id),
                "qty_period": "12.000",
                "note": "ajustado",
                "excess_justification": "",
                "use_additional_materials": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        line.refresh_from_db()
        generated_lines = list(
            MeasurementLine.objects.filter(period=self.period, is_generated_additional=True).order_by("item__eap_code")
        )
        self.assertEqual(line.additional_materials_base_qty, Decimal("12.000"))
        self.assertEqual([generated.qty_period for generated in generated_lines], [Decimal("24.000"), Decimal("24.000")])

    def test_unchecking_additional_materials_on_edit_removes_generated_lines(self):
        line, _ = add_or_merge_contracted_line(
            period=self.period,
            item=self.item,
            location=self.location_ter,
            qty_period=Decimal("10.000"),
            use_additional_materials=True,
            created_by=self.user,
        )

        response = self.client.post(
            reverse("billing:line_edit", args=[line.id]),
            {
                "item": str(self.item.id),
                "location": str(self.location_ter.id),
                "qty_period": "10.000",
                "note": "sem adicionais",
                "excess_justification": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        line.refresh_from_db()
        self.assertEqual(line.additional_materials_base_qty, Decimal("0.000"))
        self.assertFalse(MeasurementLine.objects.filter(period=self.period, is_generated_additional=True).exists())

    def test_deleting_parent_line_removes_generated_additional_lines(self):
        line, _ = add_or_merge_contracted_line(
            period=self.period,
            item=self.item,
            location=self.location_ter,
            qty_period=Decimal("10.000"),
            use_additional_materials=True,
            created_by=self.user,
        )
        generated_ids = list(
            MeasurementLine.objects.filter(period=self.period, is_generated_additional=True).values_list("id", flat=True)
        )

        response = self.client.post(reverse("billing:line_delete", args=[line.id]))

        self.assertEqual(response.status_code, 302)
        self.assertFalse(MeasurementLine.objects.filter(pk=line.id).exists())
        self.assertFalse(MeasurementLine.objects.filter(pk__in=generated_ids).exists())

    def test_editing_same_parent_twice_does_not_duplicate_generated_lines(self):
        line, _ = add_or_merge_contracted_line(
            period=self.period,
            item=self.item,
            location=self.location_ter,
            qty_period=Decimal("10.000"),
            use_additional_materials=True,
            created_by=self.user,
        )

        for qty in ("11.000", "12.000"):
            response = self.client.post(
                reverse("billing:line_edit", args=[line.id]),
                {
                    "item": str(self.item.id),
                    "location": str(self.location_ter.id),
                    "qty_period": qty,
                    "note": "ajuste",
                    "excess_justification": "",
                    "use_additional_materials": "on",
                },
            )
            self.assertEqual(response.status_code, 302)

        line.refresh_from_db()
        generated_lines = list(
            MeasurementLine.objects.filter(period=self.period, is_generated_additional=True).order_by("item__eap_code")
        )
        self.assertEqual(len(generated_lines), 2)
        self.assertEqual([generated.qty_period for generated in generated_lines], [Decimal("24.000"), Decimal("24.000")])

    def test_two_parent_items_consolidate_same_additional_item_in_same_location(self):
        add_or_merge_contracted_line(
            period=self.period,
            item=self.item,
            location=self.location_ter,
            qty_period=Decimal("10.000"),
            application_date=date(2026, 4, 8),
            use_additional_materials=True,
            created_by=self.user,
        )
        add_or_merge_contracted_line(
            period=self.period,
            item=self.item_b,
            location=self.location_ter,
            qty_period=Decimal("5.000"),
            application_date=date(2026, 4, 9),
            use_additional_materials=True,
            created_by=self.user,
        )

        screw_line = MeasurementLine.objects.get(
            period=self.period,
            is_generated_additional=True,
            item=self.screw_item,
            location=self.location_ter,
        )

        self.assertEqual(
            MeasurementLine.objects.filter(
                period=self.period,
                is_generated_additional=True,
                item=self.screw_item,
                location=self.location_ter,
            ).count(),
            1,
        )
        self.assertEqual(screw_line.qty_period, Decimal("25.000"))
        self.assertEqual(
            list(
                screw_line.histories.order_by("-application_date", "-created_at", "-id").values_list(
                    "source_item_snapshot", flat=True
                )
            ),
            ["6.6.4 - Tomada 10A", "6.6.1 - Cabo 2,5mm"],
        )

    def test_editing_parent_line_recalculates_consolidated_additional_line(self):
        line_a, _ = add_or_merge_contracted_line(
            period=self.period,
            item=self.item,
            location=self.location_ter,
            qty_period=Decimal("10.000"),
            application_date=date(2026, 4, 8),
            use_additional_materials=True,
            created_by=self.user,
        )
        add_or_merge_contracted_line(
            period=self.period,
            item=self.item_b,
            location=self.location_ter,
            qty_period=Decimal("5.000"),
            application_date=date(2026, 4, 9),
            use_additional_materials=True,
            created_by=self.user,
        )

        response = self.client.post(
            reverse("billing:line_edit", args=[line_a.id]),
            {
                "item": str(self.item.id),
                "location": str(self.location_ter.id),
                "qty_period": "12.000",
                "note": "ajuste",
                "excess_justification": "",
                "use_additional_materials": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        screw_line = MeasurementLine.objects.get(
            period=self.period,
            is_generated_additional=True,
            item=self.screw_item,
            location=self.location_ter,
        )
        self.assertEqual(screw_line.qty_period, Decimal("29.000"))
        self.assertEqual(screw_line.histories.count(), 2)

    def test_reducing_parent_quantity_subtracts_from_consolidated_additional_line(self):
        line_a, _ = add_or_merge_contracted_line(
            period=self.period,
            item=self.item,
            location=self.location_ter,
            qty_period=Decimal("10.000"),
            application_date=date(2026, 4, 8),
            use_additional_materials=True,
            created_by=self.user,
        )
        add_or_merge_contracted_line(
            period=self.period,
            item=self.item_b,
            location=self.location_ter,
            qty_period=Decimal("5.000"),
            application_date=date(2026, 4, 9),
            use_additional_materials=True,
            created_by=self.user,
        )

        response = self.client.post(
            reverse("billing:line_edit", args=[line_a.id]),
            {
                "item": str(self.item.id),
                "location": str(self.location_ter.id),
                "qty_period": "4.000",
                "note": "reduzido",
                "excess_justification": "",
                "use_additional_materials": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        screw_line = MeasurementLine.objects.get(
            period=self.period,
            is_generated_additional=True,
            item=self.screw_item,
            location=self.location_ter,
        )
        self.assertEqual(screw_line.qty_period, Decimal("13.000"))
        self.assertEqual(
            list(
                screw_line.histories.order_by("-application_date", "-created_at", "-id").values_list(
                    "source_item_snapshot", "quantity_added"
                )
            ),
            [
                ("6.6.4 - Tomada 10A", Decimal("5.000")),
                ("6.6.1 - Cabo 2,5mm", Decimal("8.000")),
            ],
        )

    def test_deleting_parent_line_recalculates_consolidated_additional_line(self):
        line_a, _ = add_or_merge_contracted_line(
            period=self.period,
            item=self.item,
            location=self.location_ter,
            qty_period=Decimal("10.000"),
            application_date=date(2026, 4, 8),
            use_additional_materials=True,
            created_by=self.user,
        )
        add_or_merge_contracted_line(
            period=self.period,
            item=self.item_b,
            location=self.location_ter,
            qty_period=Decimal("5.000"),
            application_date=date(2026, 4, 9),
            use_additional_materials=True,
            created_by=self.user,
        )

        response = self.client.post(reverse("billing:line_delete", args=[line_a.id]))

        self.assertEqual(response.status_code, 302)
        screw_line = MeasurementLine.objects.get(
            period=self.period,
            is_generated_additional=True,
            item=self.screw_item,
            location=self.location_ter,
        )
        self.assertEqual(screw_line.qty_period, Decimal("5.000"))
        self.assertEqual(
            list(screw_line.histories.values_list("source_item_snapshot", flat=True)),
            ["6.6.4 - Tomada 10A"],
        )

    def test_measurement_grid_does_not_show_generated_from_text(self):
        add_or_merge_contracted_line(
            period=self.period,
            item=self.item,
            location=self.location_ter,
            qty_period=Decimal("10.000"),
            application_date=date(2026, 4, 8),
            use_additional_materials=True,
            created_by=self.user,
        )

        response = self.client.get(reverse("billing:measurement_detail", args=[self.period.id]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Gerado por material adicional de")

    def test_measurement_grid_shows_actions_for_generated_additional_lines(self):
        add_or_merge_contracted_line(
            period=self.period,
            item=self.item,
            location=self.location_ter,
            qty_period=Decimal("10.000"),
            application_date=date(2026, 4, 8),
            use_additional_materials=True,
            created_by=self.user,
        )
        generated_line = MeasurementLine.objects.get(
            period=self.period,
            is_generated_additional=True,
            item=self.screw_item,
            location=self.location_ter,
        )

        response = self.client.get(reverse("billing:measurement_detail", args=[self.period.id]))

        self.assertContains(response, reverse("billing:line_edit", args=[generated_line.id]))
        self.assertContains(response, reverse("billing:line_delete", args=[generated_line.id]))
