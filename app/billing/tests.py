from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from io import StringIO
from pathlib import Path
import tempfile

from billing.forms import MeasurementPeriodForm
from billing.models import (
    FinancialStatus,
    MeasurementLine,
    MeasurementLineHistory,
    MeasurementPeriod,
    MeasurementSettlement,
    MeasurementWorkflowHistory,
    PredefinedEnvironment,
    PredefinedEnvironmentDiscipline,
    PredefinedEnvironmentMaterial,
    WorkflowStatus,
)
from billing.services.measurement_calc import (
    finalize_period,
    get_item_cumulative,
    split_contracted_and_excess,
    update_financial_status,
)
from billing.services.workflow import transition_measurement_status
from catalog.models import BudgetItem, Discipline, Unit
from core.models import Client, Project, ProjectLocation
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
        history = MeasurementWorkflowHistory.objects.get(period=finalized)
        self.assertEqual(history.from_status, WorkflowStatus.DRAFT)
        self.assertEqual(history.to_status, WorkflowStatus.FINALIZED)

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

    def test_finalize_period_with_incc_never_reduces_total(self):
        period = self._create_period(1, date(2026, 2, 1))
        MeasurementLine.objects.create(
            period=period,
            line_kind="CONTRACTED",
            item=self.item,
            qty_period=Decimal("2"),
        )

        index = PriceIndex.objects.create(code="INCC_DEC", name="INCC decrescente")
        PriceIndexValue.objects.create(price_index=index, ref_month=date(2026, 1, 1), value=Decimal("120"))
        PriceIndexValue.objects.create(price_index=index, ref_month=date(2026, 2, 1), value=Decimal("100"))
        ProjectPriceAdjustment.objects.create(
            project=self.project,
            price_index=index,
            base_month=date(2026, 1, 1),
            apply_to="TOTAL",
            is_active=True,
        )

        finalized = finalize_period(period.id)
        self.assertEqual(finalized.index_factor_snapshot, Decimal("1.200000"))
        self.assertEqual(finalized.total_total_snapshot, Decimal("30.00"))
        self.assertEqual(finalized.total_indexed_snapshot, Decimal("36.00"))

    def test_split_contracted_and_excess_qty_less_than_saldo(self):
        period_prev = self._create_period(1, date(2026, 1, 1))
        MeasurementLine.objects.create(
            period=period_prev,
            line_kind="CONTRACTED",
            item=self.item,
            qty_period=Decimal("80"),
        )
        period = self._create_period(2, date(2026, 2, 1))
        probe_line = MeasurementLine(
            period=period,
            line_kind="CONTRACTED",
            item=self.item,
            qty_period=Decimal("10"),
        )

        contracted_qty, excess_qty, saldo_antes = split_contracted_and_excess(probe_line)

        self.assertEqual(saldo_antes, Decimal("20"))
        self.assertEqual(contracted_qty, Decimal("10"))
        self.assertEqual(excess_qty, Decimal("0"))

    def test_split_contracted_and_excess_qty_equal_to_saldo(self):
        period_prev = self._create_period(1, date(2026, 1, 1))
        MeasurementLine.objects.create(
            period=period_prev,
            line_kind="CONTRACTED",
            item=self.item,
            qty_period=Decimal("80"),
        )
        period = self._create_period(2, date(2026, 2, 1))
        probe_line = MeasurementLine(
            period=period,
            line_kind="CONTRACTED",
            item=self.item,
            qty_period=Decimal("20"),
        )

        contracted_qty, excess_qty, saldo_antes = split_contracted_and_excess(probe_line)

        self.assertEqual(saldo_antes, Decimal("20"))
        self.assertEqual(contracted_qty, Decimal("20"))
        self.assertEqual(excess_qty, Decimal("0"))

    def test_split_contracted_and_excess_qty_greater_than_saldo(self):
        period_prev = self._create_period(1, date(2026, 1, 1))
        MeasurementLine.objects.create(
            period=period_prev,
            line_kind="CONTRACTED",
            item=self.item,
            qty_period=Decimal("80"),
        )
        period = self._create_period(2, date(2026, 2, 1))
        probe_line = MeasurementLine(
            period=period,
            line_kind="CONTRACTED",
            item=self.item,
            qty_period=Decimal("30"),
        )

        contracted_qty, excess_qty, saldo_antes = split_contracted_and_excess(probe_line)

        self.assertEqual(saldo_antes, Decimal("20"))
        self.assertEqual(contracted_qty, Decimal("20"))
        self.assertEqual(excess_qty, Decimal("10"))

    def test_finalize_period_blocks_when_excess_has_no_justification(self):
        period_prev = self._create_period(1, date(2026, 1, 1))
        MeasurementLine.objects.create(
            period=period_prev,
            line_kind="CONTRACTED",
            item=self.item,
            qty_period=Decimal("80"),
        )
        period = self._create_period(2, date(2026, 2, 1))
        MeasurementLine.objects.create(
            period=period,
            line_kind="CONTRACTED",
            item=self.item,
            qty_period=Decimal("30"),
        )

        with self.assertRaises(ValidationError) as exc:
            finalize_period(period.id)

        self.assertIn("justificativa obrigatoria para excedente", str(exc.exception).lower())


class MeasurementPeriodFormTests(TestCase):
    def test_ref_month_accepts_mm_yyyy_and_normalizes_day_one(self):
        form = MeasurementPeriodForm(
            data={
                "ref_month": "03/2026",
                "start_date": "2026-03-01",
                "end_date": "2026-03-31",
                "notes": "",
            }
        )

        self.assertTrue(form.is_valid(), form.errors.as_text())
        self.assertEqual(form.cleaned_data["ref_month"], date(2026, 3, 1))

    def test_ref_month_accepts_html_month_input(self):
        form = MeasurementPeriodForm(
            data={
                "ref_month": "2026-04",
                "start_date": "2026-04-01",
                "end_date": "2026-04-30",
                "notes": "",
            }
        )

        self.assertTrue(form.is_valid(), form.errors.as_text())
        self.assertEqual(form.cleaned_data["ref_month"], date(2026, 4, 1))


class MeasurementBulkMaterialsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="bulk-admin",
            email="bulk-admin@example.com",
            password="test",
        )
        self.regular_user = get_user_model().objects.create_user(username="bulk-user", password="test")
        self.client_obj = Client.objects.create(name="Cliente Lote")
        self.project = Project.objects.create(name="Projeto Lote", client=self.client_obj)
        self.other_project = Project.objects.create(name="Outro Projeto", client=self.client_obj)
        self.unit = Unit.objects.create(code="UN", name="Unidade")
        self.location = ProjectLocation.objects.create(
            project=self.project,
            code="PAV-01",
            name="Pavimento 1",
            order_index=1,
        )
        self.other_location = ProjectLocation.objects.create(
            project=self.other_project,
            code="OUTRO",
            name="Outro local",
            order_index=1,
        )
        self.period = MeasurementPeriod.objects.create(
            project=self.project,
            number=1,
            ref_month=date(2026, 7, 1),
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 31),
        )
        self.item_a = self._create_item("1.1", "Tubo PVC 100mm")
        self.item_b = self._create_item("1.2", "Joelho PVC 100mm")
        self.item_c = self._create_item("1.3", "Te PVC 100mm")
        self.foreign_item = BudgetItem.objects.create(
            project=self.other_project,
            eap_code="9.1",
            description="Item de outra obra",
            unit=self.unit,
            qty_contracted=Decimal("100"),
            pu_material=Decimal("1"),
            pu_labor=Decimal("1"),
        )

    def test_authorized_user_can_access_bulk_page(self):
        self.client.login(username="bulk-admin", password="test")

        response = self.client.get(reverse("billing:measurement_bulk_materials_add", args=[self.period.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Informacoes compartilhadas")
        self.assertContains(response, "id_items-TOTAL_FORMS")

    def test_anonymous_user_is_redirected(self):
        response = self.client.get(reverse("billing:measurement_bulk_materials_add", args=[self.period.id]))

        self.assertEqual(response.status_code, 302)

    def test_non_staff_user_cannot_use_bulk_page(self):
        self.client.login(username="bulk-user", password="test")

        response = self.client.post(
            reverse("billing:measurement_bulk_materials_add", args=[self.period.id]),
            self._post_data([(self.item_a, "1")]),
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(MeasurementLine.objects.exists())

    def test_bulk_creates_multiple_independent_material_lines(self):
        self.client.login(username="bulk-admin", password="test")

        response = self.client.post(
            reverse("billing:measurement_bulk_materials_add", args=[self.period.id]),
            self._post_data(
                [(self.item_a, "10"), (self.item_b, "4"), (self.item_c, "2")],
                note="Material aplicado na prumada principal",
            ),
        )

        self.assertRedirects(response, reverse("billing:measurement_detail", args=[self.period.id]))
        lines = list(MeasurementLine.objects.order_by("item__eap_code"))
        self.assertEqual(len(lines), 3)
        self.assertEqual([line.item for line in lines], [self.item_a, self.item_b, self.item_c])
        self.assertEqual([line.qty_period for line in lines], [Decimal("10.000"), Decimal("4.000"), Decimal("2.000")])
        self.assertEqual({line.location for line in lines}, {self.location})
        self.assertEqual({line.note for line in lines}, {"Material aplicado na prumada principal"})
        self.assertEqual({line.application_reference for line in lines}, {"Banheiro Casal - 301"})
        histories = list(MeasurementLineHistory.objects.order_by("line__item__eap_code"))
        self.assertEqual(len(histories), 3)
        self.assertEqual({history.application_date for history in histories}, {date(2026, 7, 20)})
        self.assertEqual({history.created_by for history in histories}, {self.user})
        self.assertEqual({history.application_reference for history in histories}, {"Banheiro Casal - 301"})

    def test_bulk_saves_different_quantities_per_item(self):
        self.client.login(username="bulk-admin", password="test")

        self.client.post(
            reverse("billing:measurement_bulk_materials_add", args=[self.period.id]),
            self._post_data([(self.item_a, "1.5"), (self.item_b, "7")]),
        )

        self.assertEqual(
            list(MeasurementLine.objects.order_by("item__eap_code").values_list("qty_period", flat=True)),
            [Decimal("1.500"), Decimal("7.000")],
        )

    def test_bulk_invalid_quantity_does_not_create_any_line(self):
        self.client.login(username="bulk-admin", password="test")

        response = self.client.post(
            reverse("billing:measurement_bulk_materials_add", args=[self.period.id]),
            self._post_data([(self.item_a, "2"), (self.item_b, "0")]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "quantity_added deve ser &gt; 0.")
        self.assertFalse(MeasurementLine.objects.exists())

    def test_bulk_rejects_item_from_another_project(self):
        self.client.login(username="bulk-admin", password="test")

        response = self.client.post(
            reverse("billing:measurement_bulk_materials_add", args=[self.period.id]),
            self._post_data([(self.foreign_item, "2")]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(MeasurementLine.objects.exists())

    def test_bulk_rejects_location_from_another_project(self):
        self.client.login(username="bulk-admin", password="test")

        response = self.client.post(
            reverse("billing:measurement_bulk_materials_add", args=[self.period.id]),
            self._post_data([(self.item_a, "2")], location=self.other_location),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(MeasurementLine.objects.exists())

    def test_bulk_blocks_locked_measurement(self):
        self.period.workflow_status = WorkflowStatus.FINALIZED
        self.period.save(update_fields=["workflow_status"])
        self.client.login(username="bulk-admin", password="test")

        response = self.client.post(
            reverse("billing:measurement_bulk_materials_add", args=[self.period.id]),
            self._post_data([(self.item_a, "2")]),
        )

        self.assertRedirects(response, reverse("billing:measurement_detail", args=[self.period.id]))
        self.assertFalse(MeasurementLine.objects.exists())

    def test_bulk_is_atomic_when_later_item_is_invalid(self):
        self.client.login(username="bulk-admin", password="test")

        response = self.client.post(
            reverse("billing:measurement_bulk_materials_add", args=[self.period.id]),
            self._post_data([(self.item_a, "2"), (self.foreign_item, "3")]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(MeasurementLine.objects.exists())
        self.assertFalse(MeasurementLineHistory.objects.exists())

    def test_bulk_rejects_duplicate_item_in_same_submission(self):
        self.client.login(username="bulk-admin", password="test")

        response = self.client.post(
            reverse("billing:measurement_bulk_materials_add", args=[self.period.id]),
            self._post_data([(self.item_a, "2"), (self.item_a, "3")]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ha itens duplicados no cadastro em lote.")
        self.assertFalse(MeasurementLine.objects.exists())

    def test_bulk_ignores_extra_empty_row(self):
        self.client.login(username="bulk-admin", password="test")

        self.client.post(
            reverse("billing:measurement_bulk_materials_add", args=[self.period.id]),
            self._post_data([(self.item_a, "2"), (self.item_b, "3"), (None, "")]),
        )

        self.assertEqual(MeasurementLine.objects.count(), 2)

    def test_bulk_rejects_partially_filled_row(self):
        self.client.login(username="bulk-admin", password="test")

        response = self.client.post(
            reverse("billing:measurement_bulk_materials_add", args=[self.period.id]),
            self._post_data([(self.item_a, "2"), (self.item_b, "")]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Quantidade obrigatoria.")
        self.assertFalse(MeasurementLine.objects.exists())

    def test_bulk_deleted_row_is_not_processed(self):
        self.client.login(username="bulk-admin", password="test")

        self.client.post(
            reverse("billing:measurement_bulk_materials_add", args=[self.period.id]),
            self._post_data([(self.item_a, "2"), (self.item_b, "3")], deleted_indexes={1}),
        )

        line = MeasurementLine.objects.get()
        self.assertEqual(line.item, self.item_a)

    def test_bulk_allows_empty_note(self):
        self.client.login(username="bulk-admin", password="test")

        self.client.post(
            reverse("billing:measurement_bulk_materials_add", args=[self.period.id]),
            self._post_data([(self.item_a, "2")], note=""),
        )

        self.assertEqual(MeasurementLine.objects.get().note, "")

    def test_bulk_success_message_contains_created_count(self):
        self.client.login(username="bulk-admin", password="test")

        response = self.client.post(
            reverse("billing:measurement_bulk_materials_add", args=[self.period.id]),
            self._post_data([(self.item_a, "2"), (self.item_b, "3")]),
            follow=True,
        )

        messages = [str(message) for message in response.context["messages"]]
        self.assertIn("2 materiais foram adicionados a medicao com sucesso.", messages)

    def test_individual_contracted_line_add_still_works(self):
        self.client.login(username="bulk-admin", password="test")

        response = self.client.post(
            reverse("billing:measurement_detail", args=[self.period.id]),
            {
                "action": "add_contracted",
                "contracted-item": str(self.item_a.id),
                "contracted-location": str(self.location.id),
                "contracted-qty_period": "2",
                "contracted-application_date": "2026-07-20",
                "contracted-application_reference": "Banheiro Social - 402",
                "contracted-note": "individual",
                "contracted-excess_justification": "",
            },
        )

        self.assertRedirects(response, reverse("billing:measurement_detail", args=[self.period.id]))
        line = MeasurementLine.objects.get()
        self.assertEqual(line.item, self.item_a)
        self.assertEqual(line.qty_period, Decimal("2.000"))
        self.assertEqual(line.application_reference, "Banheiro Social - 402")
        self.assertEqual(line.histories.get().note, "individual")
        self.assertEqual(line.histories.get().application_reference, "Banheiro Social - 402")

    def test_individual_line_edit_updates_and_removes_application_reference_without_changing_note(self):
        line = MeasurementLine.objects.create(
            period=self.period,
            line_kind="CONTRACTED",
            item=self.item_a,
            location=self.location,
            qty_period=Decimal("2"),
            application_reference="Banheiro Casal - 301",
            note="nota preservada",
        )
        MeasurementLineHistory.objects.create(
            line=line,
            quantity_added=Decimal("2"),
            application_date=date(2026, 7, 20),
            application_reference="Banheiro Casal - 301",
            note="nota preservada",
        )
        self.client.login(username="bulk-admin", password="test")

        self.client.post(
            reverse("billing:line_edit", args=[line.id]),
            {
                "item": str(self.item_a.id),
                "location": str(self.location.id),
                "qty_period": "2",
                "application_reference": "Prumada A",
                "note": "nota preservada",
                "excess_justification": "",
            },
        )
        line.refresh_from_db()
        self.assertEqual(line.application_reference, "Prumada A")
        self.assertEqual(line.note, "nota preservada")

        self.client.post(
            reverse("billing:line_edit", args=[line.id]),
            {
                "item": str(self.item_a.id),
                "location": str(self.location.id),
                "qty_period": "2",
                "application_reference": "",
                "note": "nota preservada",
                "excess_justification": "",
            },
        )
        line.refresh_from_db()
        self.assertEqual(line.application_reference, "")
        self.assertEqual(line.note, "nota preservada")

    def test_bulk_merges_with_existing_line_like_individual_flow(self):
        MeasurementLine.objects.create(
            period=self.period,
            line_kind="CONTRACTED",
            item=self.item_a,
            location=self.location,
            qty_period=Decimal("1"),
            application_reference="Banheiro Casal - 301",
        )
        self.client.login(username="bulk-admin", password="test")

        self.client.post(
            reverse("billing:measurement_bulk_materials_add", args=[self.period.id]),
            self._post_data([(self.item_a, "2")]),
        )

        self.assertEqual(MeasurementLine.objects.count(), 1)
        self.assertEqual(MeasurementLine.objects.get().qty_period, Decimal("3.000"))

    def _create_item(self, eap_code: str, description: str) -> BudgetItem:
        return BudgetItem.objects.create(
            project=self.project,
            eap_code=eap_code,
            description=description,
            unit=self.unit,
            qty_contracted=Decimal("100"),
            pu_material=Decimal("1"),
            pu_labor=Decimal("1"),
        )

    def _post_data(
        self,
        rows,
        *,
        location=None,
        note: str = "Notas compartilhadas",
        deleted_indexes: set[int] | None = None,
    ) -> dict:
        deleted_indexes = deleted_indexes or set()
        data = {
            "bulk-location": str((location or self.location).id),
            "bulk-application_date": "2026-07-20",
            "bulk-application_reference": "Banheiro Casal - 301",
            "bulk-note": note,
            "bulk-excess_justification": "",
            "items-TOTAL_FORMS": str(len(rows)),
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "0",
            "items-MAX_NUM_FORMS": "1000",
        }
        for index, (item, quantity) in enumerate(rows):
            data[f"items-{index}-item"] = "" if item is None else str(item.id)
            data[f"items-{index}-qty_period"] = quantity
            if index in deleted_indexes:
                data[f"items-{index}-DELETE"] = "on"
        return data


class PredefinedEnvironmentMaterialsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="environment-admin",
            email="environment-admin@example.com",
            password="test",
        )
        self.regular_user = get_user_model().objects.create_user(username="environment-user", password="test")
        self.client_obj = Client.objects.create(name="Cliente Ambiente")
        self.project = Project.objects.create(name="Projeto Ambiente", client=self.client_obj)
        self.other_project = Project.objects.create(name="Outra Obra", client=self.client_obj)
        self.unit = Unit.objects.create(code="UN-ENV", name="Unidade ambiente")
        self.discipline = Discipline.objects.create(name="Instalacoes sanitarias", code="SAN")
        self.other_discipline = Discipline.objects.create(name="Agua fria", code="AF")
        self.location = ProjectLocation.objects.create(
            project=self.project,
            code="PAV-TIPO",
            name="Pavimento tipo",
            order_index=1,
        )
        self.period = MeasurementPeriod.objects.create(
            project=self.project,
            number=1,
            ref_month=date(2026, 7, 1),
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 31),
        )
        self.item_a = self._create_item("1.1", "Tubo PVC 100mm")
        self.item_b = self._create_item("1.2", "Joelho PVC 100mm")
        self.item_c = self._create_item("1.3", "Luva PVC 50mm")
        self.other_discipline_item = BudgetItem.objects.create(
            project=self.project,
            eap_code="2.1",
            description="Registro agua fria",
            unit=self.unit,
            qty_contracted=Decimal("100"),
            pu_material=Decimal("1"),
            pu_labor=Decimal("1"),
            discipline=self.other_discipline,
        )
        self.foreign_item = BudgetItem.objects.create(
            project=self.other_project,
            eap_code="9.1",
            description="Item outra obra",
            unit=self.unit,
            qty_contracted=Decimal("100"),
            pu_material=Decimal("1"),
            pu_labor=Decimal("1"),
        )
        self.environment = PredefinedEnvironment.objects.create(
            project=self.project,
            name="Banheiro Casal",
            description="Padrao banheiro casal",
        )
        self.environment_discipline = PredefinedEnvironmentDiscipline.objects.create(
            environment=self.environment,
            discipline=self.discipline,
        )
        self.material_a = PredefinedEnvironmentMaterial.objects.create(
            environment_discipline=self.environment_discipline,
            item=self.item_a,
            default_quantity=Decimal("2"),
            order_index=1,
        )
        self.material_b = PredefinedEnvironmentMaterial.objects.create(
            environment_discipline=self.environment_discipline,
            item=self.item_b,
            default_quantity=Decimal("4"),
            order_index=2,
        )

    def test_create_environment_validates_project_scope_and_duplicate_name(self):
        duplicated = PredefinedEnvironment(project=self.project, name="  banheiro   casal ")

        with self.assertRaises(ValidationError):
            duplicated.save()

        other_project_environment = PredefinedEnvironment.objects.create(
            project=self.other_project,
            name="Banheiro Casal",
        )
        self.assertEqual(other_project_environment.name, "Banheiro Casal")

    def test_discipline_and_material_validations_block_duplicates_and_foreign_item(self):
        with self.assertRaises(ValidationError):
            PredefinedEnvironmentDiscipline.objects.create(
                environment=self.environment,
                discipline=self.discipline,
            )

        with self.assertRaises(ValidationError):
            PredefinedEnvironmentMaterial.objects.create(
                environment_discipline=self.environment_discipline,
                item=self.item_a,
                default_quantity=Decimal("1"),
            )

        with self.assertRaises(ValidationError):
            PredefinedEnvironmentMaterial.objects.create(
                environment_discipline=self.environment_discipline,
                item=self.foreign_item,
                default_quantity=Decimal("1"),
            )

        with self.assertRaises(ValidationError):
            PredefinedEnvironmentMaterial.objects.create(
                environment_discipline=self.environment_discipline,
                item=self.other_discipline_item,
                default_quantity=Decimal("1"),
            )

        with self.assertRaises(ValidationError):
            PredefinedEnvironmentMaterial.objects.create(
                environment_discipline=self.environment_discipline,
                item=self.item_c,
                default_quantity=Decimal("0"),
            )

    def test_material_pattern_dropdown_lists_only_items_from_selected_discipline(self):
        self.client.login(username="environment-admin", password="test")

        response = self.client.get(reverse("billing:predefined_environment_detail", args=[self.environment.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tubo PVC 100mm")
        self.assertContains(response, "Joelho PVC 100mm")
        self.assertNotContains(response, "Registro agua fria")

    def test_material_pattern_quantity_can_be_edited_inline(self):
        self.client.login(username="environment-admin", password="test")

        response = self.client.post(
            reverse("billing:predefined_environment_detail", args=[self.environment.id]),
            {
                "action": "update_material",
                "material_id": str(self.material_a.id),
                "default_quantity": "3.750",
                "order_index": "7",
                "is_active": "on",
            },
        )

        self.assertRedirects(response, reverse("billing:predefined_environment_detail", args=[self.environment.id]))
        self.material_a.refresh_from_db()
        self.assertEqual(self.material_a.default_quantity, Decimal("3.750"))
        self.assertEqual(self.material_a.order_index, 7)
        self.assertTrue(self.material_a.is_active)

    def test_material_pattern_edit_does_not_change_existing_measurement_line(self):
        MeasurementLine.objects.create(
            period=self.period,
            line_kind="CONTRACTED",
            item=self.item_a,
            location=self.location,
            qty_period=Decimal("2"),
            application_reference="Banheiro Casal - 301",
        )
        self.client.login(username="environment-admin", password="test")

        self.client.post(
            reverse("billing:predefined_environment_detail", args=[self.environment.id]),
            {
                "action": "update_material",
                "material_id": str(self.material_a.id),
                "default_quantity": "4.500",
                "order_index": "1",
                "is_active": "on",
            },
        )

        self.material_a.refresh_from_db()
        line = MeasurementLine.objects.get()
        self.assertEqual(self.material_a.default_quantity, Decimal("4.500"))
        self.assertEqual(line.qty_period, Decimal("2.000"))

    def test_authorized_user_can_access_environment_application_page(self):
        self.client.login(username="environment-admin", password="test")

        response = self.client.get(reverse("billing:measurement_environment_materials_add", args=[self.period.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Adicionar materiais por ambiente")
        self.assertContains(response, "Banheiro Casal")

    def test_anonymous_user_is_redirected_from_environment_application_page(self):
        response = self.client.get(reverse("billing:measurement_environment_materials_add", args=[self.period.id]))

        self.assertEqual(response.status_code, 302)

    def test_non_staff_user_cannot_apply_environment_materials(self):
        self.client.login(username="environment-user", password="test")

        response = self.client.post(
            reverse("billing:measurement_environment_materials_add", args=[self.period.id]),
            self._application_post_data([(self.item_a, "1")]),
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(MeasurementLine.objects.exists())

    def test_locked_measurement_blocks_environment_application(self):
        self.period.workflow_status = WorkflowStatus.FINALIZED
        self.period.save(update_fields=["workflow_status"])
        self.client.login(username="environment-admin", password="test")

        response = self.client.post(
            reverse("billing:measurement_environment_materials_add", args=[self.period.id]),
            self._application_post_data([(self.item_a, "1")]),
        )

        self.assertRedirects(response, reverse("billing:measurement_detail", args=[self.period.id]))
        self.assertFalse(MeasurementLine.objects.exists())

    def test_get_loads_default_materials_for_selected_environment_discipline(self):
        self.client.login(username="environment-admin", password="test")

        response = self.client.get(
            reverse("billing:measurement_environment_materials_add", args=[self.period.id]),
            {
                "environment": str(self.environment.id),
                "environment_discipline": str(self.environment_discipline.id),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "value=\"2.000\"")
        self.assertContains(response, "value=\"4.000\"")

    def test_apply_environment_materials_uses_adjusted_quantities_and_preserves_pattern(self):
        self.client.login(username="environment-admin", password="test")

        response = self.client.post(
            reverse("billing:measurement_environment_materials_add", args=[self.period.id]),
            self._application_post_data(
                [(self.item_a, "1.5"), (self.item_b, "5")],
                note="Ajuste proximo ao shaft",
            ),
        )

        self.assertRedirects(response, reverse("billing:measurement_detail", args=[self.period.id]))
        self.material_a.refresh_from_db()
        self.material_b.refresh_from_db()
        self.assertEqual(self.material_a.default_quantity, Decimal("2.000"))
        self.assertEqual(self.material_b.default_quantity, Decimal("4.000"))
        lines = list(MeasurementLine.objects.order_by("item__eap_code"))
        self.assertEqual([line.qty_period for line in lines], [Decimal("1.500"), Decimal("5.000")])
        self.assertEqual({line.location for line in lines}, {self.location})
        self.assertEqual({line.application_reference for line in lines}, {"Banheiro Casal - apartamento 301"})
        self.assertEqual({line.note for line in lines}, {"Ajuste proximo ao shaft"})

    def test_removed_default_material_is_not_created_and_pattern_is_preserved(self):
        self.client.login(username="environment-admin", password="test")

        self.client.post(
            reverse("billing:measurement_environment_materials_add", args=[self.period.id]),
            self._application_post_data([(self.item_a, "1"), (self.item_b, "2")], deleted_indexes={1}),
        )

        line = MeasurementLine.objects.get()
        self.assertEqual(line.item, self.item_a)
        self.assertTrue(PredefinedEnvironmentMaterial.objects.filter(pk=self.material_b.pk).exists())

    def test_extra_material_is_created_only_in_measurement(self):
        self.client.login(username="environment-admin", password="test")

        self.client.post(
            reverse("billing:measurement_environment_materials_add", args=[self.period.id]),
            self._application_post_data([(self.item_a, "1"), (self.item_b, "2"), (self.item_c, "3")]),
        )

        self.assertEqual(MeasurementLine.objects.count(), 3)
        self.assertFalse(
            PredefinedEnvironmentMaterial.objects.filter(
                environment_discipline=self.environment_discipline,
                item=self.item_c,
            ).exists()
        )

    def test_duplicate_item_in_application_blocks_all_lines(self):
        self.client.login(username="environment-admin", password="test")

        response = self.client.post(
            reverse("billing:measurement_environment_materials_add", args=[self.period.id]),
            self._application_post_data([(self.item_a, "1"), (self.item_a, "2")]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ha itens duplicados no cadastro em lote.")
        self.assertFalse(MeasurementLine.objects.exists())

    def test_empty_application_blocks_all_lines(self):
        self.client.login(username="environment-admin", password="test")

        response = self.client.post(
            reverse("billing:measurement_environment_materials_add", args=[self.period.id]),
            self._application_post_data([(self.item_a, "1")], deleted_indexes={0}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Informe pelo menos um item para cadastrar.")
        self.assertFalse(MeasurementLine.objects.exists())

    def test_inactive_environment_is_not_available_for_application(self):
        self.environment.is_active = False
        self.environment.save(update_fields=["is_active"])
        self.client.login(username="environment-admin", password="test")

        response = self.client.get(reverse("billing:measurement_environment_materials_add", args=[self.period.id]))

        self.assertNotContains(response, "Banheiro Casal</option>")

    def test_applying_same_pattern_to_different_references_creates_independent_lines(self):
        self.client.login(username="environment-admin", password="test")

        self.client.post(
            reverse("billing:measurement_environment_materials_add", args=[self.period.id]),
            self._application_post_data([(self.item_a, "1")], application_reference="Banheiro Casal - 301"),
        )
        self.client.post(
            reverse("billing:measurement_environment_materials_add", args=[self.period.id]),
            self._application_post_data([(self.item_a, "2")], application_reference="Banheiro Casal - 302"),
        )

        lines = list(MeasurementLine.objects.order_by("application_reference"))
        self.assertEqual(len(lines), 2)
        self.assertEqual([line.application_reference for line in lines], ["Banheiro Casal - 301", "Banheiro Casal - 302"])
        self.assertEqual([line.qty_period for line in lines], [Decimal("1.000"), Decimal("2.000")])

    def _create_item(self, eap_code: str, description: str) -> BudgetItem:
        return BudgetItem.objects.create(
            project=self.project,
            eap_code=eap_code,
            description=description,
            unit=self.unit,
            qty_contracted=Decimal("100"),
            pu_material=Decimal("1"),
            pu_labor=Decimal("1"),
            discipline=self.discipline,
        )

    def _application_post_data(
        self,
        rows,
        *,
        note: str = "Notas por ambiente",
        application_reference: str = "Banheiro Casal - apartamento 301",
        deleted_indexes: set[int] | None = None,
    ) -> dict:
        deleted_indexes = deleted_indexes or set()
        data = {
            "env-environment": str(self.environment.id),
            "env-environment_discipline": str(self.environment_discipline.id),
            "env-location": str(self.location.id),
            "env-application_reference": application_reference,
            "env-application_date": "2026-07-20",
            "env-note": note,
            "env-excess_justification": "",
            "items-TOTAL_FORMS": str(len(rows)),
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "0",
            "items-MAX_NUM_FORMS": "1000",
        }
        for index, (item, quantity) in enumerate(rows):
            data[f"items-{index}-item"] = str(item.id)
            data[f"items-{index}-qty_period"] = quantity
            if index in deleted_indexes:
                data[f"items-{index}-DELETE"] = "on"
        return data


class ApplicationReferenceMigrationCommandTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(name="Cliente Referencia")
        self.project = Project.objects.create(name="Projeto Referencia", client=self.client_obj)
        self.unit = Unit.objects.create(code="UN-REF", name="Unidade referencia")
        self.location = ProjectLocation.objects.create(
            project=self.project,
            code="PAV-TIPO",
            name="Pavimento tipo",
        )
        self.period = MeasurementPeriod.objects.create(
            project=self.project,
            number=1,
            ref_month=date(2026, 7, 1),
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 31),
        )
        self.item = BudgetItem.objects.create(
            project=self.project,
            eap_code="1.1",
            description="Tubo PVC",
            unit=self.unit,
            qty_contracted=Decimal("100"),
            pu_material=Decimal("1"),
            pu_labor=Decimal("1"),
        )

    def test_command_dry_run_does_not_change_reference_note(self):
        line = self._line(note="Banheiro Casal - 301")

        output = StringIO()
        call_command("migrar_notas_referencia_aplicacao", "--dry-run", stdout=output)

        line.refresh_from_db()
        self.assertEqual(line.application_reference, "")
        self.assertEqual(line.note, "Banheiro Casal - 301")
        self.assertIn("Referencias identificadas: 1", output.getvalue())

    def test_command_apply_moves_safe_note_to_reference(self):
        line = self._line(note="Banheiro Casal - 301")

        call_command("migrar_notas_referencia_aplicacao", "--apply", stdout=StringIO())

        line.refresh_from_db()
        self.assertEqual(line.application_reference, "Banheiro Casal - 301")
        self.assertEqual(line.note, "")

    def test_command_preserves_descriptive_note(self):
        note = "Foi necessario alterar o trajeto proximo ao banheiro do apartamento 301."
        line = self._line(note=note)

        call_command("migrar_notas_referencia_aplicacao", "--apply", stdout=StringIO())

        line.refresh_from_db()
        self.assertEqual(line.application_reference, "")
        self.assertEqual(line.note, note)

    def test_command_keeps_ambiguous_reference_and_observation_note(self):
        note = "Banheiro Casal - 301 - quantidade ajustada"
        line = self._line(note=note)

        call_command("migrar_notas_referencia_aplicacao", "--apply", stdout=StringIO())

        line.refresh_from_db()
        self.assertEqual(line.application_reference, "")
        self.assertEqual(line.note, note)

    def test_command_does_not_overwrite_existing_reference(self):
        line = self._line(note="Banheiro Casal - 301", application_reference="Banheiro Social - 201")

        call_command("migrar_notas_referencia_aplicacao", "--apply", stdout=StringIO())

        line.refresh_from_db()
        self.assertEqual(line.application_reference, "Banheiro Social - 201")
        self.assertEqual(line.note, "Banheiro Casal - 301")

    def test_command_normalizes_spaces_and_separator_variants(self):
        line_a = self._line(note="  Banheiro Casal   -   301  ")
        line_b = self._line(note="Banheiro Social \u2013 402", eap_code="1.2")
        line_c = self._line(note="Cozinha \u2014 Apartamento 201", eap_code="1.3")

        call_command("migrar_notas_referencia_aplicacao", "--apply", stdout=StringIO())

        line_a.refresh_from_db()
        line_b.refresh_from_db()
        line_c.refresh_from_db()
        self.assertEqual(line_a.application_reference, "Banheiro Casal - 301")
        self.assertEqual(line_b.application_reference, "Banheiro Social \u2013 402")
        self.assertEqual(line_c.application_reference, "Cozinha \u2014 Apartamento 201")

    def test_command_migrates_apartment_reference_without_separator(self):
        line = self._line(note="Banheiro casal 301")

        call_command("migrar_notas_referencia_aplicacao", "--apply", stdout=StringIO())

        line.refresh_from_db()
        self.assertEqual(line.application_reference, "Banheiro Casal - apartamento 301")
        self.assertEqual(line.note, "")

    def test_command_migrates_reference_segment_and_preserves_remaining_note(self):
        line = self._line(note="Pias banheiro casal 302 | Banheiro Casal 301")

        call_command("migrar_notas_referencia_aplicacao", "--apply", stdout=StringIO())

        line.refresh_from_db()
        self.assertEqual(line.application_reference, "Banheiro Casal - apartamento 301")
        self.assertEqual(line.note, "Pias banheiro casal 302")

    def test_command_migrates_duplicate_reference_segments(self):
        line = self._line(note="Banheiro casal 301 | Banheiro Casal 301")

        call_command("migrar_notas_referencia_aplicacao", "--apply", stdout=StringIO())

        line.refresh_from_db()
        self.assertEqual(line.application_reference, "Banheiro Casal - apartamento 301")
        self.assertEqual(line.note, "")

    def test_command_preserves_action_note(self):
        line = self._line(note="Material danificado - trocar")

        call_command("migrar_notas_referencia_aplicacao", "--apply", stdout=StringIO())

        line.refresh_from_db()
        self.assertEqual(line.application_reference, "")
        self.assertEqual(line.note, "Material danificado - trocar")

    def test_command_apply_is_idempotent(self):
        line = self._line(note="Shaft - Prumada A")

        call_command("migrar_notas_referencia_aplicacao", "--apply", stdout=StringIO())
        call_command("migrar_notas_referencia_aplicacao", "--apply", stdout=StringIO())

        line.refresh_from_db()
        self.assertEqual(line.application_reference, "Shaft - Prumada A")
        self.assertEqual(line.note, "")

    def test_command_exports_csv_without_changing_data(self):
        line = self._line(note="Banheiro Casal - 301")

        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "referencias.csv"
            call_command(
                "migrar_notas_referencia_aplicacao",
                "--dry-run",
                "--export-csv",
                str(csv_path),
                stdout=StringIO(),
            )

            content = csv_path.read_text(encoding="utf-8-sig")
            self.assertIn("material_id,medicao_id,item,localizacao,notas_atuais,referencia_sugerida,status,motivo", content)
            self.assertIn("Banheiro Casal - 301", content)
        line.refresh_from_db()
        self.assertEqual(line.application_reference, "")
        self.assertEqual(line.note, "Banheiro Casal - 301")

    def test_command_migrates_history_note(self):
        line = self._line(note="")
        history = MeasurementLineHistory.objects.create(
            line=line,
            quantity_added=Decimal("2"),
            application_date=date(2026, 7, 20),
            note="Lavanderia - Unidade 202",
        )

        call_command("migrar_notas_referencia_aplicacao", "--apply", stdout=StringIO())

        history.refresh_from_db()
        self.assertEqual(history.application_reference, "Lavanderia - Unidade 202")
        self.assertEqual(history.note, "")

    def _line(self, *, note: str, application_reference: str = "", eap_code: str = "1.1") -> MeasurementLine:
        item = self.item
        if eap_code != self.item.eap_code:
            item = BudgetItem.objects.create(
                project=self.project,
                eap_code=eap_code,
                description=f"Item {eap_code}",
                unit=self.unit,
                qty_contracted=Decimal("100"),
                pu_material=Decimal("1"),
                pu_labor=Decimal("1"),
            )
        return MeasurementLine.objects.create(
            period=self.period,
            line_kind="CONTRACTED",
            item=item,
            location=self.location,
            qty_period=Decimal("1"),
            application_reference=application_reference,
            note=note,
        )


class MeasurementWorkflowServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="workflow-admin",
            email="workflow@example.com",
            password="test",
        )
        self.client_obj = Client.objects.create(name="Cliente Workflow")
        self.project = Project.objects.create(name="Projeto Workflow", client=self.client_obj)

    def _create_period(self, number: int, status: str) -> MeasurementPeriod:
        return MeasurementPeriod.objects.create(
            project=self.project,
            number=number,
            ref_month=date(2026, number, 1),
            start_date=date(2026, number, 1),
            end_date=date(2026, number, 28),
            workflow_status=status,
        )

    def _transition(self, from_status: str, to_status: str, note: str = "obs") -> MeasurementPeriod:
        period = self._create_period(1, from_status)
        updated = transition_measurement_status(period, to_status, user=self.user, note=note)
        updated.refresh_from_db()
        return updated

    def test_finalized_to_sent(self):
        period = self._transition(WorkflowStatus.FINALIZED, WorkflowStatus.SENT, note="Enviado ao cliente")

        self.assertEqual(period.workflow_status, WorkflowStatus.SENT)
        self.assertIsNotNone(period.sent_at)
        self.assertEqual(period.sent_note, "Enviado ao cliente")

    def test_sent_to_in_review(self):
        period = self._transition(WorkflowStatus.SENT, WorkflowStatus.IN_REVIEW, note="Em conferencia")

        self.assertEqual(period.workflow_status, WorkflowStatus.IN_REVIEW)
        self.assertIsNotNone(period.in_review_at)
        self.assertEqual(period.review_note, "Em conferencia")

    def test_in_review_to_authorized(self):
        period = self._transition(WorkflowStatus.IN_REVIEW, WorkflowStatus.AUTHORIZED, note="Aprovado")

        self.assertEqual(period.workflow_status, WorkflowStatus.AUTHORIZED)
        self.assertIsNotNone(period.authorized_at)
        self.assertEqual(period.authorization_note, "Aprovado")

    def test_in_review_to_rejected(self):
        period = self._transition(WorkflowStatus.IN_REVIEW, WorkflowStatus.REJECTED, note="Corrigir quantidades")

        self.assertEqual(period.workflow_status, WorkflowStatus.REJECTED)
        self.assertIsNotNone(period.rejected_at)
        self.assertEqual(period.rejection_reason, "Corrigir quantidades")

    def test_rejected_to_draft(self):
        period = self._transition(WorkflowStatus.REJECTED, WorkflowStatus.DRAFT, note="Reabrir para ajuste")

        self.assertEqual(period.workflow_status, WorkflowStatus.DRAFT)

    def test_sent_to_finalized(self):
        period = self._transition(WorkflowStatus.SENT, WorkflowStatus.FINALIZED, note="Retornar para correcao")

        self.assertEqual(period.workflow_status, WorkflowStatus.FINALIZED)
        self.assertIsNotNone(period.finalized_at)

    def test_finalized_to_draft_requires_reopen_permission(self):
        period = self._create_period(1, WorkflowStatus.FINALIZED)
        regular_user = get_user_model().objects.create_user(username="regular", password="test")

        with self.assertRaises(ValidationError):
            transition_measurement_status(period, WorkflowStatus.DRAFT, user=regular_user, note="Sem permissao")

        period.refresh_from_db()
        self.assertEqual(period.workflow_status, WorkflowStatus.FINALIZED)

        updated = transition_measurement_status(period, WorkflowStatus.DRAFT, user=self.user, note="Com permissao")
        self.assertEqual(updated.workflow_status, WorkflowStatus.DRAFT)

    def test_draft_to_cancelled(self):
        period = self._transition(WorkflowStatus.DRAFT, WorkflowStatus.CANCELLED, note="Cancelada antes de finalizar")

        self.assertEqual(period.workflow_status, WorkflowStatus.CANCELLED)
        self.assertIsNotNone(period.cancelled_at)
        self.assertEqual(period.cancellation_reason, "Cancelada antes de finalizar")

    def test_finalized_to_cancelled(self):
        period = self._transition(WorkflowStatus.FINALIZED, WorkflowStatus.CANCELLED, note="Cancelada finalizada")

        self.assertEqual(period.workflow_status, WorkflowStatus.CANCELLED)
        self.assertIsNotNone(period.cancelled_at)

    def test_sent_to_cancelled(self):
        period = self._transition(WorkflowStatus.SENT, WorkflowStatus.CANCELLED, note="Cancelada enviada")

        self.assertEqual(period.workflow_status, WorkflowStatus.CANCELLED)
        self.assertIsNotNone(period.cancelled_at)

    def test_in_review_to_cancelled(self):
        period = self._transition(WorkflowStatus.IN_REVIEW, WorkflowStatus.CANCELLED, note="Cancelada em avaliacao")

        self.assertEqual(period.workflow_status, WorkflowStatus.CANCELLED)
        self.assertIsNotNone(period.cancelled_at)

    def test_invalid_transition_is_blocked(self):
        period = self._create_period(1, WorkflowStatus.AUTHORIZED)

        with self.assertRaises(ValidationError):
            transition_measurement_status(period, WorkflowStatus.SENT, user=self.user, note="Nao pode")

        period.refresh_from_db()
        self.assertEqual(period.workflow_status, WorkflowStatus.AUTHORIZED)
        self.assertFalse(MeasurementWorkflowHistory.objects.filter(period=period).exists())

    def test_rejection_and_cancellation_require_reason(self):
        reject_period = self._create_period(1, WorkflowStatus.IN_REVIEW)
        cancel_period = self._create_period(2, WorkflowStatus.DRAFT)

        with self.assertRaises(ValidationError):
            transition_measurement_status(reject_period, WorkflowStatus.REJECTED, user=self.user, note="")
        with self.assertRaises(ValidationError):
            transition_measurement_status(cancel_period, WorkflowStatus.CANCELLED, user=self.user, note="")

    def test_history_created_for_each_transition(self):
        period = self._create_period(1, WorkflowStatus.FINALIZED)

        transition_measurement_status(period, WorkflowStatus.SENT, user=self.user, note="Envio")
        period.refresh_from_db()
        transition_measurement_status(period, WorkflowStatus.IN_REVIEW, user=self.user, note="Analise")

        history = list(period.workflow_history.order_by("changed_at", "id"))
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].from_status, WorkflowStatus.FINALIZED)
        self.assertEqual(history[0].to_status, WorkflowStatus.SENT)
        self.assertEqual(history[0].note, "Envio")
        self.assertEqual(history[0].changed_by, self.user)
        self.assertEqual(history[1].from_status, WorkflowStatus.SENT)
        self.assertEqual(history[1].to_status, WorkflowStatus.IN_REVIEW)
