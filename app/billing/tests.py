from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from billing.forms import MeasurementPeriodForm
from billing.models import (
    FinancialStatus,
    MeasurementLine,
    MeasurementPeriod,
    MeasurementSettlement,
    MeasurementWorkflowHistory,
    WorkflowStatus,
)
from billing.services.measurement_calc import (
    finalize_period,
    get_item_cumulative,
    split_contracted_and_excess,
    update_financial_status,
)
from billing.services.workflow import transition_measurement_status
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
