from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from typing import Any, Protocol, TypedDict

from django.conf import settings

from controlplane.models import AgentRegistration, DiagnosticOperation
from controlplane.runtime import gateway_runtime

_MAX_HISTORY_PER_PROJECT = 200
_MAX_CHECKS = 32
_MAX_DETAIL = 2048
_SECRET_RE = re.compile(r"(?i)(secret|password|token|access[_-]?key)\s*[:=]\s*\S+")
_TERMINAL = {
    "succeeded",
    "failed",
    "cancelled",
    "offline",
    "overloaded",
    "disconnected",
    "timeout",
}
_ACTIVE = ("pending", "running")


class AgentState(TypedDict):
    connected: bool
    heartbeat_age: int | None
    capabilities: list[str]
    protocol_major: int | None
    protocol_minor: int | None


class OperationsBackend(Protocol):
    def connected(self, agent_id: str) -> bool: ...

    def heartbeat_age(self, agent_id: str) -> int | None: ...

    def capabilities(self, agent_id: str) -> tuple[str, ...]: ...

    def protocol_version(self, agent_id: str) -> tuple[int, int]: ...

    def start(self, operation: DiagnosticOperation) -> None: ...

    def cancel(self, operation: DiagnosticOperation) -> bool: ...


class RuntimeGatewayBackend:
    def connected(self, agent_id: str) -> bool:
        try:
            gateway, _ = gateway_runtime()
        except RuntimeError:
            return False
        return gateway.is_connected(agent_id)

    def heartbeat_age(self, agent_id: str) -> int | None:
        try:
            gateway, _ = gateway_runtime()
            return int(gateway.heartbeat_age(agent_id))
        except (RuntimeError, LookupError):
            return None

    def capabilities(self, agent_id: str) -> tuple[str, ...]:
        try:
            gateway, _ = gateway_runtime()
            return gateway.capabilities(agent_id)
        except (RuntimeError, LookupError):
            return ()

    def protocol_version(self, agent_id: str) -> tuple[int, int]:
        try:
            gateway, _ = gateway_runtime()
            return gateway.protocol_version(agent_id)
        except (RuntimeError, LookupError):
            return (0, 0)

    def start(self, operation: DiagnosticOperation) -> None:
        gateway, loop = gateway_runtime()
        asyncio.run_coroutine_threadsafe(_consume(gateway, operation.pk), loop)

    def cancel(self, operation: DiagnosticOperation) -> bool:
        gateway, loop = gateway_runtime()
        future = asyncio.run_coroutine_threadsafe(gateway.cancel(str(operation.public_id)), loop)
        return bool(future.result(timeout=2))


_backend_factory: Callable[[], OperationsBackend] = RuntimeGatewayBackend


def set_backend_factory(factory: Callable[[], OperationsBackend]) -> None:
    global _backend_factory
    _backend_factory = factory


class OperationsService:
    def __init__(self, backend: OperationsBackend | None = None) -> None:
        self.backend = backend or _backend_factory()

    def agent_state(self, agent: AgentRegistration) -> AgentState:
        connected = self.backend.connected(agent.agent_id)
        if not connected:
            return {
                "connected": False,
                "heartbeat_age": None,
                "capabilities": list(agent.capabilities),
                "protocol_major": None,
                "protocol_minor": None,
            }
        protocol = self.backend.protocol_version(agent.agent_id)
        capabilities = self.backend.capabilities(agent.agent_id)
        return {
            "connected": True,
            "heartbeat_age": self.backend.heartbeat_age(agent.agent_id),
            "capabilities": list(capabilities),
            "protocol_major": protocol[0],
            "protocol_minor": protocol[1],
        }

    def start(self, *, agent: AgentRegistration, user: Any, diagnostic: str) -> DiagnosticOperation:
        if diagnostic != "django_health":
            raise ValueError("unsupported diagnostic")
        operation = DiagnosticOperation.objects.create(
            project=agent.project,
            agent=agent,
            requested_by=user,
            diagnostic=diagnostic,
        )
        if not self.backend.connected(agent.agent_id):
            operation.status = DiagnosticOperation.Status.OFFLINE
            operation.error_code = "AGENT_OFFLINE"
            operation.save(update_fields=("status", "error_code", "updated_at"))
            self._trim_history(agent.project_id)
            return operation
        try:
            self.backend.start(operation)
        except RuntimeError as exc:
            code = "OVERLOADED" if "capacity" in str(exc).lower() else "GATEWAY_UNAVAILABLE"
            operation.status = (
                DiagnosticOperation.Status.OVERLOADED
                if code == "OVERLOADED"
                else DiagnosticOperation.Status.FAILED
            )
            operation.error_code = code
            operation.save(update_fields=("status", "error_code", "updated_at"))
        self._trim_history(agent.project_id)
        return operation

    def cancel(self, operation: DiagnosticOperation) -> DiagnosticOperation:
        operation.refresh_from_db(fields=("status", "error_code", "updated_at"))
        if operation.status in _TERMINAL:
            return operation
        try:
            accepted = self.backend.cancel(operation)
        except TimeoutError:
            status = DiagnosticOperation.Status.FAILED
            error_code = "CANCEL_TIMEOUT"
        except RuntimeError:
            status = DiagnosticOperation.Status.FAILED
            error_code = "CANCEL_FAILED"
        else:
            if accepted:
                status = DiagnosticOperation.Status.CANCELLED
                error_code = ""
            else:
                status = DiagnosticOperation.Status.DISCONNECTED
                error_code = "CANCEL_UNAVAILABLE"
        DiagnosticOperation.objects.filter(pk=operation.pk, status__in=_ACTIVE).update(
            status=status,
            error_code=error_code,
        )
        operation.refresh_from_db()
        return operation

    @staticmethod
    def _trim_history(project_id: int) -> None:
        terminal = DiagnosticOperation.objects.filter(project_id=project_id).exclude(
            status__in=(
                DiagnosticOperation.Status.PENDING,
                DiagnosticOperation.Status.RUNNING,
            )
        )
        stale = terminal.values_list("pk", flat=True)[_MAX_HISTORY_PER_PROJECT:]
        ids = list(stale)
        if ids:
            DiagnosticOperation.objects.filter(pk__in=ids).delete()


async def _consume(gateway: Any, operation_pk: int) -> None:
    operation = await asyncio.to_thread(
        DiagnosticOperation.objects.select_related("agent", "project").get,
        pk=operation_pk,
    )
    await asyncio.to_thread(_set_running, operation_pk)
    try:
        async with asyncio.timeout(settings.DIAGNOSTIC_TIMEOUT_SECONDS):
            async for frame in gateway.diagnostics(
                operation.agent.agent_id,
                str(operation.public_id),
                operation.project.release_root,
            ):
                if frame.HasField("progress"):
                    await asyncio.to_thread(
                        _save_progress,
                        operation_pk,
                        frame.progress.phase,
                        frame.progress.message,
                    )
                elif frame.HasField("result"):
                    checks = [
                        {
                            "name": check.name[:128],
                            "status": check.status[:32],
                            "detail": _redact(check.detail),
                        }
                        for check in list(frame.result.checks)[:_MAX_CHECKS]
                    ]
                    await asyncio.to_thread(_save_result, operation_pk, frame.result.status, checks)
    except TimeoutError:
        try:
            await gateway.cancel(str(operation.public_id))
        finally:
            await asyncio.to_thread(_save_error, operation_pk, "timeout", "DIAGNOSTIC_TIMEOUT")
    except LookupError:
        await asyncio.to_thread(_save_error, operation_pk, "offline", "AGENT_OFFLINE")
    except RuntimeError:
        await asyncio.to_thread(_save_error, operation_pk, "overloaded", "OVERLOADED")
    except Exception:
        await asyncio.to_thread(_save_error, operation_pk, "failed", "DIAGNOSTIC_FAILED")


def _redact(value: str) -> str:
    return _SECRET_RE.sub("[REDACTED]", value)[:_MAX_DETAIL]


def _set_running(pk: int) -> None:
    DiagnosticOperation.objects.filter(pk=pk, status="pending").update(status="running")


def _save_progress(pk: int, phase: str, message: str) -> None:
    DiagnosticOperation.objects.filter(pk=pk, status__in=_ACTIVE).update(
        result_summary={"progress": {"phase": phase[:64], "message": _redact(message)}}
    )


def _save_result(pk: int, status: str, checks: list[dict[str, str]]) -> None:
    if status == "succeeded":
        mapped = "succeeded"
    elif status == "cancelled":
        mapped = "cancelled"
    elif status == "disconnected":
        mapped = "disconnected"
    else:
        mapped = "failed"
    DiagnosticOperation.objects.filter(pk=pk, status__in=_ACTIVE).update(
        status=mapped,
        result_summary={"checks": checks},
        error_code="" if mapped == "succeeded" else status.upper()[:32],
    )


def _save_error(pk: int, status: str, code: str) -> None:
    DiagnosticOperation.objects.filter(pk=pk, status__in=_ACTIVE).update(
        status=status,
        error_code=code,
    )
