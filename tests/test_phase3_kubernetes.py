from __future__ import annotations

import json
import threading
from typing import Any

import pytest
from django.contrib.auth.models import User

from controlplane.kubernetes import (
    KubernetesService,
    ReleaseRequest,
    ReleaseResult,
    TargetValidation,
    sanitize_values,
)
from controlplane.models import AgentRegistration, KubernetesRelease, Project


class Backend:
    def __init__(self, *, valid: bool = True, result: ReleaseResult | None = None) -> None:
        self.valid = valid
        self.result = result or ReleaseResult("succeeded", helm_revision=2, previous_revision=1)
        self.requests: list[ReleaseRequest] = []
        self.cancelled: list[str] = []

    def validate(self, target: Any) -> TargetValidation:
        permissions = ("deployments", "jobs", "services", "ingresses")
        return TargetValidation(self.valid, self.valid, self.valid, permissions)

    def start(self, target: Any, request: ReleaseRequest) -> ReleaseResult:
        self.requests.append(request)
        return self.result

    def cancel(self, operation_id: str) -> bool:
        self.cancelled.append(operation_id)
        return True


class BlockingBackend(Backend):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.cancel_requested = threading.Event()
        self.release_result = threading.Event()

    def start(self, target: Any, request: ReleaseRequest) -> ReleaseResult:
        self.requests.append(request)
        self.started.set()
        assert self.cancel_requested.wait(timeout=5)
        assert self.release_result.wait(timeout=5)
        return ReleaseResult("cancelled", error_code="CANCELLED")

    def cancel(self, operation_id: str) -> bool:
        self.cancelled.append(operation_id)
        self.cancel_requested.set()
        return True


class RetryBackend(Backend):
    def start(self, target: Any, request: ReleaseRequest) -> ReleaseResult:
        self.requests.append(request)
        if len(self.requests) == 1:
            raise RuntimeError("connection lost after durable admission")
        return ReleaseResult("succeeded", helm_revision=3, previous_revision=2)


def fixture(
    slug: str = "p3", agent_id: str = "agent-p3"
) -> tuple[User, Project, AgentRegistration]:
    user = User.objects.create_user(f"user-{slug}", password="test")
    project = Project.objects.create(slug=slug, name=f"Project {slug}")
    project.members.add(user)
    agent = AgentRegistration.objects.create(
        project=project,
        agent_id=agent_id,
        capabilities=["django_diagnostics_v1", "kubernetes_release_v1"],
    )
    return user, project, agent


def register(service: KubernetesService, project: Project, agent: AgentRegistration) -> Any:
    return service.register_target(
        project=project,
        agent=agent,
        name="prod",
        api_server="https://cluster.example:6443",
        namespace="project-p3",
        release_name="p3-web",
        credential_ref="file:cluster-prod",
    )


@pytest.mark.django_db
def test_target_registration_is_project_scoped_and_opaque() -> None:
    _, project, agent = fixture()
    target = register(KubernetesService(Backend()), project, agent)
    assert target.project == project
    assert target.validated_at is not None
    assert "kubeconfig" not in json.dumps(target.validation_summary).lower()


@pytest.mark.django_db
def test_underprivileged_target_fails_closed_without_persisting() -> None:
    _, project, agent = fixture()
    with pytest.raises(ValueError, match="validation failed"):
        register(KubernetesService(Backend(valid=False)), project, agent)
    assert project.kubernetes_targets.count() == 0


@pytest.mark.django_db
def test_cross_project_cluster_release_collision_is_rejected() -> None:
    _, project_a, agent_a = fixture("a", "agent-a")
    _, project_b, agent_b = fixture("b", "agent-b")
    service = KubernetesService(Backend())
    register(service, project_a, agent_a)
    with pytest.raises(ValueError, match="belongs to another project"):
        register(service, project_b, agent_b)


@pytest.mark.django_db
def test_release_requires_digest_and_records_non_secret_revision() -> None:
    user, project, agent = fixture()
    backend = Backend()
    service = KubernetesService(backend)
    target = register(service, project, agent)
    image = "registry.example/app@sha256:" + "a" * 64
    release = service.start_release(
        target=target,
        user=user,
        image=image,
        values={
            "webReplicas": 2,
            "environmentSecretName": "runtime",
            "migrationCompatibility": "safe",
        },
    )
    assert release.status == KubernetesRelease.Status.SUCCEEDED
    assert release.helm_revision == 2
    assert backend.requests[0].credential_ref == "file:cluster-prod"
    assert "password" not in json.dumps(release.values_snapshot).lower()


@pytest.mark.django_db
def test_rollout_failure_with_unknown_migration_blocks_rollback() -> None:
    user, project, agent = fixture()
    backend = Backend(
        result=ReleaseResult(
            "rollback_blocked",
            rollback_status="blocked",
            error_code="ROLLOUT_FAILED_DB_COMPATIBILITY_UNKNOWN",
        )
    )
    service = KubernetesService(backend)
    target = register(service, project, agent)
    release = service.start_release(
        target=target,
        user=user,
        image="registry.example/app@sha256:" + "b" * 64,
        values={"migrationCompatibility": "unknown"},
    )
    assert release.status == KubernetesRelease.Status.ROLLBACK_BLOCKED
    assert release.rollback_status == "blocked"


@pytest.mark.django_db
def test_terminal_release_is_not_overwritten_by_late_cancel() -> None:
    user, project, agent = fixture()
    backend = Backend()
    service = KubernetesService(backend)
    target = register(service, project, agent)
    release = service.start_release(
        target=target,
        user=user,
        image="registry.example/app@sha256:" + "c" * 64,
        values={},
    )
    assert service.cancel_release(release).status == KubernetesRelease.Status.SUCCEEDED
    assert backend.cancelled == []


@pytest.mark.django_db(transaction=True)
def test_release_is_committed_and_cancellable_while_backend_is_in_flight() -> None:
    user, project, agent = fixture("cancel", "agent-cancel")
    backend = BlockingBackend()
    service = KubernetesService(backend)
    target = register(service, project, agent)
    image = "registry.example/app@sha256:" + "d" * 64
    result: list[KubernetesRelease] = []

    thread = threading.Thread(
        target=lambda: result.append(
            service.start_release(target=target, user=user, image=image, values={})
        )
    )
    thread.start()
    assert backend.started.wait(timeout=5)
    active = KubernetesRelease.objects.get(target=target)
    assert active.status == KubernetesRelease.Status.VALIDATING
    cancelled = service.cancel_release(active)
    assert cancelled.status == KubernetesRelease.Status.CANCELLED
    assert backend.cancel_requested.is_set()
    backend.release_result.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert result[0].status == KubernetesRelease.Status.CANCELLED
    assert backend.cancelled == [active.operation_id]


@pytest.mark.django_db
def test_retry_reuses_durable_operation_id_after_agent_disconnect() -> None:
    user, project, agent = fixture("retry", "agent-retry")
    backend = RetryBackend()
    service = KubernetesService(backend)
    target = register(service, project, agent)
    image = "registry.example/app@sha256:" + "e" * 64
    first = service.start_release(target=target, user=user, image=image, values={})
    assert first.status == KubernetesRelease.Status.FAILED
    assert first.error_code == "AGENT_UNAVAILABLE"
    second = service.start_release(target=target, user=user, image=image, values={})
    assert second.status == KubernetesRelease.Status.SUCCEEDED
    assert first.pk == second.pk
    assert len({request.operation_id for request in backend.requests}) == 1
    assert KubernetesRelease.objects.filter(target=target).count() == 1


@pytest.mark.django_db
def test_recovery_required_retry_keeps_same_operation_id() -> None:
    user, project, agent = fixture("recovery", "agent-recovery")
    backend = Backend(result=ReleaseResult("failed", error_code="RECOVERY_REQUIRED"))
    service = KubernetesService(backend)
    target = register(service, project, agent)
    image = "registry.example/app@sha256:" + "f" * 64
    first = service.start_release(target=target, user=user, image=image, values={})
    second = service.start_release(target=target, user=user, image=image, values={})
    assert first.pk == second.pk
    assert first.operation_id == second.operation_id
    assert len({request.operation_id for request in backend.requests}) == 1
    assert second.error_code == "RECOVERY_REQUIRED"


def test_plaintext_secret_like_values_rejected() -> None:
    with pytest.raises(ValueError, match="secret-like"):
        sanitize_values({"environmentSecretName": "runtime", "resources": {"password": "nope"}})
