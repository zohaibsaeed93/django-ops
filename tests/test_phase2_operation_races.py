from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from controlplane.models import AgentRegistration, DiagnosticOperation, Project
from controlplane.services import OperationsService, _save_error, _save_result

pytestmark = pytest.mark.django_db


class CancelBackend:
    def connected(self, agent_id: str) -> bool:
        return True

    def heartbeat_age(self, agent_id: str) -> int | None:
        return 0

    def capabilities(self, agent_id: str) -> tuple[str, ...]:
        return ("django_diagnostics_v1",)

    def protocol_version(self, agent_id: str) -> tuple[int, int]:
        return (1, 0)

    def start(self, operation: DiagnosticOperation) -> None:
        return None

    def cancel(self, operation: DiagnosticOperation) -> bool:
        return True


def operation() -> DiagnosticOperation:
    user = get_user_model().objects.create_user("race-user")
    project = Project.objects.create(slug="race", name="Race")
    agent = AgentRegistration.objects.create(project=project, agent_id="race-agent")
    return DiagnosticOperation.objects.create(
        project=project,
        agent=agent,
        requested_by=user,
        status=DiagnosticOperation.Status.RUNNING,
    )


def test_cancelled_operation_cannot_be_overwritten_by_late_success() -> None:
    item = operation()

    cancelled = OperationsService(CancelBackend()).cancel(item)
    _save_result(item.pk, "succeeded", [{"name": "django_check", "status": "pass", "detail": "ok"}])

    item.refresh_from_db()
    assert cancelled.status == DiagnosticOperation.Status.CANCELLED
    assert item.status == DiagnosticOperation.Status.CANCELLED
    assert item.result_summary == {}


def test_cancelled_operation_cannot_be_overwritten_by_late_disconnect() -> None:
    item = operation()

    cancelled = OperationsService(CancelBackend()).cancel(item)
    _save_error(item.pk, "disconnected", "AGENT_DISCONNECTED")

    item.refresh_from_db()
    assert cancelled.status == DiagnosticOperation.Status.CANCELLED
    assert item.status == DiagnosticOperation.Status.CANCELLED
    assert item.error_code == ""


def test_result_winning_race_cannot_be_overwritten_by_late_cancel() -> None:
    item = operation()
    stale = DiagnosticOperation.objects.get(pk=item.pk)
    _save_result(item.pk, "succeeded", [])

    updated = OperationsService(CancelBackend()).cancel(stale)

    assert updated.status == DiagnosticOperation.Status.SUCCEEDED
    assert updated.error_code == ""
