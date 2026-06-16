from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from billing.models import MeasurementPeriod, MeasurementWorkflowHistory, WorkflowStatus


ALLOWED_TRANSITIONS = {
    WorkflowStatus.DRAFT: {WorkflowStatus.FINALIZED, WorkflowStatus.CANCELLED},
    WorkflowStatus.FINALIZED: {WorkflowStatus.DRAFT, WorkflowStatus.SENT, WorkflowStatus.CANCELLED},
    WorkflowStatus.SENT: {WorkflowStatus.FINALIZED, WorkflowStatus.IN_REVIEW, WorkflowStatus.CANCELLED},
    WorkflowStatus.IN_REVIEW: {WorkflowStatus.AUTHORIZED, WorkflowStatus.REJECTED, WorkflowStatus.CANCELLED},
    WorkflowStatus.REJECTED: {WorkflowStatus.DRAFT},
    WorkflowStatus.AUTHORIZED: set(),
    WorkflowStatus.CANCELLED: set(),
}

DATE_FIELD_BY_STATUS = {
    WorkflowStatus.FINALIZED: "finalized_at",
    WorkflowStatus.SENT: "sent_at",
    WorkflowStatus.IN_REVIEW: "in_review_at",
    WorkflowStatus.AUTHORIZED: "authorized_at",
    WorkflowStatus.REJECTED: "rejected_at",
    WorkflowStatus.CANCELLED: "cancelled_at",
}

NOTE_FIELD_BY_STATUS = {
    WorkflowStatus.SENT: "sent_note",
    WorkflowStatus.IN_REVIEW: "review_note",
    WorkflowStatus.AUTHORIZED: "authorization_note",
    WorkflowStatus.REJECTED: "rejection_reason",
    WorkflowStatus.CANCELLED: "cancellation_reason",
}

REQUIRED_NOTE_STATUSES = {WorkflowStatus.REJECTED, WorkflowStatus.CANCELLED}


def _status_label(status: str) -> str:
    return WorkflowStatus(status).label if status in WorkflowStatus.values else status


def _can_reopen_finalized(user) -> bool:
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    return user.has_perm("billing.change_measurementperiod")


def can_transition(period: MeasurementPeriod, to_status: str, user=None, note: str | None = None) -> list[str]:
    from_status = period.workflow_status
    errors: list[str] = []

    if to_status not in WorkflowStatus.values:
        return [f"Status de destino invalido: {to_status}."]

    if from_status == to_status:
        return [f"A medicao ja esta em {_status_label(to_status)}."]

    if to_status not in ALLOWED_TRANSITIONS.get(from_status, set()):
        return [
            (
                "Transicao de status nao permitida: "
                f"{_status_label(from_status)} -> {_status_label(to_status)}."
            )
        ]

    if from_status == WorkflowStatus.FINALIZED and to_status == WorkflowStatus.DRAFT:
        if not _can_reopen_finalized(user):
            errors.append("Somente usuarios com permissao de alterar medicoes podem reabrir medicao finalizada.")

    if to_status in REQUIRED_NOTE_STATUSES and not (note or "").strip():
        if to_status == WorkflowStatus.REJECTED:
            errors.append("Motivo da rejeicao e obrigatorio.")
        elif to_status == WorkflowStatus.CANCELLED:
            errors.append("Motivo do cancelamento e obrigatorio.")

    return errors


@transaction.atomic
def transition_measurement_status(period, to_status: str, user=None, note: str | None = None):
    locked_period = MeasurementPeriod.objects.select_for_update().get(pk=period.pk)
    note = (note or "").strip()
    errors = can_transition(locked_period, to_status, user=user, note=note)
    if errors:
        raise ValidationError(errors)

    from_status = locked_period.workflow_status
    locked_period.workflow_status = to_status

    update_fields = ["workflow_status"]
    date_field = DATE_FIELD_BY_STATUS.get(to_status)
    if date_field:
        setattr(locked_period, date_field, timezone.now())
        update_fields.append(date_field)

    note_field = NOTE_FIELD_BY_STATUS.get(to_status)
    if note_field is not None:
        setattr(locked_period, note_field, note)
        update_fields.append(note_field)

    locked_period.save(update_fields=update_fields)

    if to_status in {WorkflowStatus.DRAFT, WorkflowStatus.REJECTED, WorkflowStatus.CANCELLED}:
        from stock.measurement_consumption import reverse_measurement_stock_consumption

        reverse_measurement_stock_consumption(locked_period, user=user)

    MeasurementWorkflowHistory.objects.create(
        period=locked_period,
        from_status=from_status,
        to_status=to_status,
        note=note,
        changed_by=user if getattr(user, "is_authenticated", False) else None,
    )
    return locked_period


def record_measurement_workflow_history(period, from_status: str, to_status: str, user=None, note: str | None = None):
    return MeasurementWorkflowHistory.objects.create(
        period=period,
        from_status=from_status,
        to_status=to_status,
        note=(note or "").strip(),
        changed_by=user if getattr(user, "is_authenticated", False) else None,
    )
