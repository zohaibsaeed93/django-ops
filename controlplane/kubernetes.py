from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Protocol, cast
from urllib.parse import urlparse

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from controlplane.gateway_runtime import gateway_runtime
from controlplane.models import AgentRegistration, KubernetesRelease, KubernetesTarget, Project

_DNS_LABEL = re.compile(r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$")
_CREDENTIAL_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:/_.-]{2,127}$")
_DIGEST_IMAGE = re.compile(r"^.+@sha256:[0-9a-f]{64}$")
_TERMINAL = {"succeeded", "failed", "cancelled", "rolled_back", "rollback_blocked"}
_ACTIVE = ("pending", "validating", "migrating", "applying", "verifying")
_RECOVERABLE_ERRORS = {
    "AGENT_UNAVAILABLE",
    "AGENT_RESULT_UNAVAILABLE",
    "CANCEL_UNAVAILABLE",
    "DURABLE_STATE_UNAVAILABLE",
    "RECOVERY_REQUIRED",
}
_REQUIRED_PERMISSIONS = ("deployments", "jobs", "services", "ingresses", "secrets")


@dataclass(frozen=True)
class TargetValidation:
    reachable: bool
    namespace_access: bool
    helm_available: bool
    permissions: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return self.reachable and self.namespace_access and self.helm_available


@dataclass(frozen=True)
class ReleaseRequest:
    operation_id: str
    namespace: str
    release_name: str
    image: str
    values_json: str
    credential_ref: str
    rollback_policy: str
    mode: str = "release"
    previous_revision: int = 0
    migration_timeout_seconds: int = 300
    rollout_timeout_seconds: int = 300


@dataclass(frozen=True)
class ReleaseResult:
    status: str
    helm_revision: int | None = None
    previous_revision: int | None = None
    rollback_status: str = ""
    error_code: str = ""


class KubernetesBackend(Protocol):
    def validate(self, target: KubernetesTarget) -> TargetValidation: ...
    def start(self, target: KubernetesTarget, request: ReleaseRequest) -> ReleaseResult: ...
    def cancel(self, operation_id: str) -> bool: ...


class RuntimeKubernetesBackend:
    def _run(self, agent_id: str, request: ReleaseRequest) -> ReleaseResult:
        gateway = gateway_runtime.gateway
        if gateway is None or gateway_runtime.loop is None:
            raise RuntimeError("kubernetes agent backend unavailable")

        async def collect() -> ReleaseResult:
            result: Any | None = None
            async for frame in gateway.kubernetes_release(
                agent_id=agent_id,
                job_id=request.operation_id,
                namespace=request.namespace,
                release_name=request.release_name,
                chart_path="charts/djangoops",
                image=request.image,
                values_json=request.values_json,
                credential_ref=request.credential_ref,
                migration_timeout_seconds=request.migration_timeout_seconds,
                rollout_timeout_seconds=request.rollout_timeout_seconds,
                rollback_policy=request.rollback_policy,
                mode=request.mode,
                previous_revision=request.previous_revision,
            ):
                if frame.HasField("kubernetes_result"):
                    result = frame.kubernetes_result
            if result is None:
                raise RuntimeError("agent returned no Kubernetes result")
            return ReleaseResult(
                status=result.status,
                helm_revision=result.helm_revision or None,
                previous_revision=result.previous_revision or None,
                rollback_status=result.rollback_status,
                error_code=result.error_code,
            )

        future = asyncio.run_coroutine_threadsafe(collect(), gateway_runtime.loop)
        try:
            return future.result(timeout=float(settings.DIAGNOSTIC_TIMEOUT_SECONDS) + 600.0)
        except (TimeoutError, RuntimeError) as exc:
            future.cancel()
            raise RuntimeError("kubernetes agent operation failed") from exc

    def validate(self, target: KubernetesTarget) -> TargetValidation:
        result = self._run(
            target.agent.agent_id,
            ReleaseRequest(
                operation_id=f"k8sv-{uuid.uuid4().hex[:20]}",
                namespace=target.namespace,
                release_name=target.release_name,
                image="validation.invalid/app@sha256:" + "0" * 64,
                values_json="{}",
                credential_ref=target.credential_ref,
                rollback_policy="block-after-migration",
                mode="validate",
            ),
        )
        if result.status != "succeeded":
            return TargetValidation(False, False, False, ())
        return TargetValidation(True, True, True, _REQUIRED_PERMISSIONS)

    def start(self, target: KubernetesTarget, request: ReleaseRequest) -> ReleaseResult:
        return self._run(target.agent.agent_id, request)

    def cancel(self, operation_id: str) -> bool:
        if gateway_runtime.gateway is None or gateway_runtime.loop is None:
            return False
        future = asyncio.run_coroutine_threadsafe(
            gateway_runtime.gateway.cancel(operation_id), gateway_runtime.loop
        )
        try:
            return bool(future.result(timeout=5.0))
        except TimeoutError:
            future.cancel()
            return False


def _validate_name(value: str, *, release: bool = False) -> str:
    value = value.strip()
    limit = 53 if release else 63
    if not value or len(value) > limit or not _DNS_LABEL.fullmatch(value):
        raise ValueError("invalid Kubernetes name")
    return value


def _validate_api_server(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("api_server must be an https endpoint without embedded credentials")
    return value.rstrip("/")


def _validate_credential_ref(value: str) -> str:
    if not _CREDENTIAL_REF.fullmatch(value):
        raise ValueError("credential_ref must be an opaque server-side reference")
    return value


def _validate_image(value: str) -> str:
    if not _DIGEST_IMAGE.fullmatch(value):
        raise ValueError("release image must use an immutable sha256 digest")
    return value


def sanitize_values(values: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "webReplicas",
        "celeryWorker",
        "celeryBeat",
        "ingress",
        "resources",
        "health",
        "environmentSecretName",
        "migrationCompatibility",
    }
    unknown = set(values) - allowed
    if unknown:
        raise ValueError(f"unsupported Helm values: {', '.join(sorted(unknown))}")
    encoded = json.dumps(values, sort_keys=True, separators=(",", ":"))
    lowered = encoded.lower()
    secret_tokens = ('"password"', '"token"', '"secretkey"', '"access_key"')
    if any(token in lowered for token in secret_tokens):
        raise ValueError("plaintext secret-like Helm values are not accepted")
    return cast(dict[str, Any], json.loads(encoded))


def values_digest(values: dict[str, Any]) -> str:
    payload = json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


class KubernetesService:
    def __init__(self, backend: KubernetesBackend | None = None) -> None:
        self.backend = backend or RuntimeKubernetesBackend()

    def register_target(
        self,
        *,
        project: Project,
        agent: AgentRegistration,
        name: str,
        api_server: str,
        namespace: str,
        release_name: str,
        credential_ref: str,
        ingress_class: str = "",
        tls_secret_name: str = "",
    ) -> KubernetesTarget:
        if agent.project_id != project.pk:
            raise ValueError("agent and target project must match")
        api_server = _validate_api_server(api_server)
        namespace = _validate_name(namespace)
        release_name = _validate_name(release_name, release=True)
        if (
            KubernetesTarget.objects.filter(
                api_server=api_server,
                namespace=namespace,
                release_name=release_name,
            )
            .exclude(project=project)
            .exists()
        ):
            raise ValueError("cluster namespace/release identity belongs to another project")
        target = KubernetesTarget.objects.create(
            project=project,
            agent=agent,
            name=name.strip()[:80],
            api_server=api_server,
            namespace=namespace,
            release_name=release_name,
            credential_ref=_validate_credential_ref(credential_ref),
            ingress_class=_validate_name(ingress_class) if ingress_class else "",
            tls_secret_name=_validate_name(tls_secret_name) if tls_secret_name else "",
        )
        validation = self.backend.validate(target)
        if not validation.valid:
            target.delete()
            raise ValueError("cluster validation failed")
        target.validation_summary = {
            "reachable": validation.reachable,
            "namespaceAccess": validation.namespace_access,
            "helmAvailable": validation.helm_available,
            "permissions": list(validation.permissions)[:32],
        }
        target.validated_at = timezone.now()
        target.save(update_fields=("validation_summary", "validated_at", "updated_at"))
        return target

    def start_release(
        self,
        *,
        target: KubernetesTarget,
        user: Any,
        image: str,
        values: dict[str, Any],
        chart_version: str = "0.1.0",
    ) -> KubernetesRelease:
        clean_values = sanitize_values(values)
        image = _validate_image(image)
        digest = values_digest(clean_values)
        compatibility = str(clean_values.get("migrationCompatibility", "unknown"))
        if compatibility not in {"safe", "unknown"}:
            raise ValueError("migrationCompatibility must be safe or unknown")

        dispatch = True
        with transaction.atomic():
            locked_target = KubernetesTarget.objects.select_for_update().get(pk=target.pk)
            existing = (
                locked_target.releases.filter(
                    image=image,
                    chart_version=chart_version,
                    values_digest=digest,
                )
                .filter(
                    Q(status__in=_ACTIVE) | Q(status="failed", error_code__in=_RECOVERABLE_ERRORS)
                )
                .first()
            )
            if existing is not None:
                release = existing
                if existing.status in _ACTIVE:
                    dispatch = False
                else:
                    KubernetesRelease.objects.filter(pk=existing.pk).update(
                        status="validating", error_code=""
                    )
                    release.refresh_from_db()
            else:
                previous = locked_target.releases.filter(
                    status=KubernetesRelease.Status.SUCCEEDED
                ).first()
                release = KubernetesRelease.objects.create(
                    project=locked_target.project,
                    target=locked_target,
                    requested_by=user,
                    operation_id=f"k8s-{uuid.uuid4().hex[:20]}",
                    image=image,
                    chart_version=chart_version,
                    values_digest=digest,
                    values_snapshot=clean_values,
                    previous_helm_revision=previous.helm_revision if previous else None,
                    previous_image=previous.image if previous else "",
                    previous_values_digest=previous.values_digest if previous else "",
                    migration_compatibility=compatibility,
                    status="validating",
                )

        # The durable row/state above is committed before any network or Kubernetes work.
        # This keeps cancellation observable and avoids holding a database transaction
        # open across a potentially minutes-long agent operation.
        if not dispatch:
            return release

        request = ReleaseRequest(
            operation_id=release.operation_id,
            namespace=target.namespace,
            release_name=target.release_name,
            image=image,
            values_json=json.dumps(clean_values, sort_keys=True, separators=(",", ":")),
            credential_ref=target.credential_ref,
            rollback_policy="allow" if compatibility == "safe" else "block-after-migration",
            previous_revision=release.previous_helm_revision or 0,
        )
        try:
            result = self.backend.start(target, request)
        except RuntimeError:
            KubernetesRelease.objects.filter(pk=release.pk, status__in=_ACTIVE).update(
                status="failed", error_code="AGENT_UNAVAILABLE"
            )
        else:
            mapped = result.status if result.status in _TERMINAL else "failed"
            KubernetesRelease.objects.filter(pk=release.pk, status__in=_ACTIVE).update(
                status=mapped,
                helm_revision=result.helm_revision,
                previous_helm_revision=result.previous_revision or release.previous_helm_revision,
                rollback_status=result.rollback_status[:32],
                error_code=result.error_code[:48],
            )
        release.refresh_from_db()
        return release

    def cancel_release(self, release: KubernetesRelease) -> KubernetesRelease:
        release.refresh_from_db(fields=("status", "error_code", "updated_at"))
        if release.status in _TERMINAL:
            return release
        accepted = self.backend.cancel(release.operation_id)
        KubernetesRelease.objects.filter(pk=release.pk, status__in=_ACTIVE).update(
            status="cancelled" if accepted else "failed",
            error_code="" if accepted else "CANCEL_UNAVAILABLE",
        )
        release.refresh_from_db()
        return release
