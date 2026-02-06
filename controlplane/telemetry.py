from __future__ import annotations

import json
import logging
import os
import queue
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, deque
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit

_MAX_CORRELATION: Final = 64
_MAX_VALUE: Final = 256
_ALLOWED_COMPONENTS: Final = frozenset({"controlplane", "agent", "cli"})
_ALLOWED_KINDS: Final = frozenset(
    {"diagnostic", "kubernetes_release", "deploy", "health", "backup", "restore", "graphql"}
)
_ALLOWED_OUTCOMES: Final = frozenset(
    {
        "pending",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "offline",
        "overloaded",
        "disconnected",
        "timeout",
        "rolled_back",
        "rollback_blocked",
        "recovery_required",
    }
)
_SAFE = re.compile(r"[^a-zA-Z0-9_.:/@+-]")
_SENSITIVE = re.compile(
    r"(?i)(credential|password|secret|token|authorization|access[_-]?key|database[_-]?url|kubeconfig)\s*[:=]\s*\S+"
)


def correlation_id(value: str | None = None) -> str:
    if value is None:
        return secrets.token_hex(16)
    cleaned = _SAFE.sub("_", value.replace("\n", "_").replace("\r", "_"))[:_MAX_CORRELATION]
    return cleaned or secrets.token_hex(16)


def redact(value: str) -> str:
    return _SENSITIVE.sub("[REDACTED]", value.replace("\r", " ").replace("\n", " "))[:_MAX_VALUE]


def validate_otlp_endpoint(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("OTLP endpoint must be an http(s) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("OTLP endpoint must be configured without credentials, query, or fragment")
    return value.rstrip("/")


def traceparent(trace_id: str | None = None, span_id: str | None = None) -> str:
    trace = trace_id if trace_id and len(trace_id) == 32 else secrets.token_hex(16)
    span = span_id if span_id and len(span_id) == 16 else secrets.token_hex(8)
    return f"00-{trace}-{span}-01"


@dataclass(frozen=True)
class TelemetryEvent:
    component: str
    kind: str
    outcome: str
    duration_ms: int
    correlation: str
    project_id: int | None = None

    def payload(self) -> dict[str, object]:
        return {
            "component": self.component,
            "kind": self.kind,
            "outcome": self.outcome,
            "duration_ms": self.duration_ms,
            "correlation_id": self.correlation,
            "project_id": self.project_id,
        }


class Telemetry:
    def __init__(self, max_buffer: int = 256, endpoint: str | None = None) -> None:
        size = max(8, min(max_buffer, 1024))
        configured = os.environ.get("DJANGOOPS_OTLP_ENDPOINT", "") if endpoint is None else endpoint
        self.endpoint = validate_otlp_endpoint(configured)
        self._lock = threading.Lock()
        self._counts: Counter[tuple[str, str, str]] = Counter()
        self._durations: Counter[tuple[str, str, str]] = Counter()
        self._inflight: Counter[tuple[str, str]] = Counter()
        self._events: deque[TelemetryEvent] = deque(maxlen=size)
        self._exports: queue.Queue[dict[str, object]] = queue.Queue(maxsize=size)
        self._dropped = 0
        self._export_errors = 0
        self._worker_started = False

    def start(self, *, component: str, kind: str) -> tuple[str, float]:
        component, kind, _ = self._labels(component, kind, "pending")
        with self._lock:
            self._inflight[(component, kind)] += 1
        return correlation_id(), time.monotonic()

    def finish(
        self,
        *,
        component: str,
        kind: str,
        outcome: str,
        correlation: str,
        started: float,
        project_id: int | None = None,
    ) -> None:
        duration_ms = min(int(max(0.0, time.monotonic() - started) * 1000), 86_400_000)
        self.observe(
            component=component,
            kind=kind,
            outcome=outcome,
            duration_ms=duration_ms,
            correlation=correlation,
            project_id=project_id,
            decrement_inflight=True,
        )

    def observe(
        self,
        *,
        component: str,
        kind: str,
        outcome: str,
        duration_ms: int = 0,
        correlation: str | None = None,
        project_id: int | None = None,
        decrement_inflight: bool = False,
    ) -> None:
        component, kind, outcome = self._labels(component, kind, outcome)
        event = TelemetryEvent(
            component,
            kind,
            outcome,
            max(0, min(duration_ms, 86_400_000)),
            correlation_id(correlation),
            project_id,
        )
        with self._lock:
            self._counts[(component, kind, outcome)] += 1
            self._durations[(component, kind, outcome)] += event.duration_ms
            if decrement_inflight:
                key = (component, kind)
                self._inflight[key] = max(0, self._inflight[key] - 1)
            self._events.append(event)
        logging.getLogger("djangoops.telemetry").info(
            "djangoops_event %s", json.dumps(event.payload(), sort_keys=True)
        )
        self._enqueue(event)

    def recent_for_project(self, project_id: int, limit: int = 20) -> list[dict[str, object]]:
        with self._lock:
            values = [event.payload() for event in self._events if event.project_id == project_id]
        return values[-max(1, min(limit, 50)) :]

    def prometheus(self, extra: dict[str, int] | None = None) -> str:
        lines = ["# TYPE djangoops_operations_total counter"]
        with self._lock:
            for (component, kind, outcome), value in sorted(self._counts.items()):
                labels = f'component="{component}",kind="{kind}",outcome="{outcome}"'
                lines.append(f"djangoops_operations_total{{{labels}}} {value}")
            lines.append("# TYPE djangoops_operation_duration_milliseconds_total counter")
            for (component, kind, outcome), value in sorted(self._durations.items()):
                labels = f'component="{component}",kind="{kind}",outcome="{outcome}"'
                lines.append(f"djangoops_operation_duration_milliseconds_total{{{labels}}} {value}")
            lines.append("# TYPE djangoops_operations_inflight gauge")
            for (component, kind), value in sorted(self._inflight.items()):
                labels = f'component="{component}",kind="{kind}"'
                lines.append(f"djangoops_operations_inflight{{{labels}}} {value}")
            lines.append(f"djangoops_telemetry_dropped_total {self._dropped}")
            lines.append(f"djangoops_telemetry_export_errors_total {self._export_errors}")
        for name, value in sorted((extra or {}).items()):
            if re.fullmatch(r"djangoops_[a-z0-9_]+", name):
                lines.append(f"{name} {max(0, int(value))}")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _labels(component: str, kind: str, outcome: str) -> tuple[str, str, str]:
        return (
            component if component in _ALLOWED_COMPONENTS else "controlplane",
            kind if kind in _ALLOWED_KINDS else "diagnostic",
            outcome if outcome in _ALLOWED_OUTCOMES else "failed",
        )

    def _enqueue(self, event: TelemetryEvent) -> None:
        if not self.endpoint:
            return
        payload = self._otlp_payload(event)
        try:
            self._exports.put_nowait(payload)
        except queue.Full:
            with self._lock:
                self._dropped += 1
            return
        self._ensure_worker()

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._worker_started:
                return
            self._worker_started = True
        threading.Thread(target=self._export_loop, name="djangoops-otlp", daemon=True).start()

    def _export_loop(self) -> None:
        while True:
            payload = self._exports.get()
            try:
                request = urllib.request.Request(
                    f"{self.endpoint}/v1/traces",
                    data=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=1.0):  # noqa: S310
                    pass
            except (OSError, TimeoutError, urllib.error.URLError):
                with self._lock:
                    self._export_errors += 1
            finally:
                self._exports.task_done()

    @staticmethod
    def _otlp_payload(event: TelemetryEvent) -> dict[str, object]:
        return {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {
                                "key": "service.name",
                                "value": {"stringValue": "djangoops-controlplane"},
                            }
                        ]
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": "djangoops"},
                            "spans": [
                                {
                                    "traceId": secrets.token_hex(16),
                                    "spanId": secrets.token_hex(8),
                                    "name": f"djangoops.{event.kind}",
                                    "attributes": [
                                        {
                                            "key": "djangoops.outcome",
                                            "value": {"stringValue": event.outcome},
                                        },
                                        {
                                            "key": "djangoops.correlation_id",
                                            "value": {"stringValue": event.correlation},
                                        },
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        }


telemetry = Telemetry()
