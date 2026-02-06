from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any, cast

import controlplane.kubernetes as kubernetes
from controlplane.models import KubernetesTarget
from controlplane.telemetry import correlation_id, validate_otlp_endpoint

_NAMESPACE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")


def sanitize_observability(value: object) -> dict[str, object]:
    if value in (None, {}):
        return {}
    if not isinstance(value, dict) or set(value) - {
        "enabled",
        "otlpEndpoint",
        "serviceNamespace",
        "correlationId",
    }:
        raise ValueError("observability Helm values are invalid")
    enabled = value.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("observability.enabled must be a boolean")
    if not enabled:
        return {"enabled": False}
    endpoint = validate_otlp_endpoint(str(value.get("otlpEndpoint", "")))
    if not endpoint:
        raise ValueError("observability.otlpEndpoint is required when enabled")
    namespace = str(value.get("serviceNamespace", "djangoops"))
    if not _NAMESPACE.fullmatch(namespace):
        raise ValueError("observability.serviceNamespace is invalid")
    return {
        "enabled": True,
        "otlpEndpoint": endpoint,
        "serviceNamespace": namespace,
        "correlationId": "",
    }


def inject_release_correlation(values_json: str, operation_id: str) -> str:
    raw = json.loads(values_json)
    if not isinstance(raw, dict):
        raise ValueError("Helm values must be a mapping")
    observability = raw.get("observability")
    if isinstance(observability, dict) and observability.get("enabled") is True:
        updated = dict(observability)
        updated["correlationId"] = correlation_id(operation_id)
        raw["observability"] = updated
    return json.dumps(raw, sort_keys=True, separators=(",", ":"))


def install_kubernetes_observability() -> None:
    original_sanitize = kubernetes.sanitize_values
    if getattr(original_sanitize, "_djangoops_observability", False):
        return

    def sanitize_values(values: dict[str, Any]) -> dict[str, Any]:
        rest = dict(values)
        observability = rest.pop("observability", None)
        cleaned = original_sanitize(rest)
        safe_observability = sanitize_observability(observability)
        if safe_observability:
            cleaned["observability"] = safe_observability
        return cleaned

    sanitize_values._djangoops_observability = True  # type: ignore[attr-defined]
    kubernetes.sanitize_values = sanitize_values

    original_start = kubernetes.RuntimeKubernetesBackend.start

    def start(
        self: kubernetes.RuntimeKubernetesBackend,
        target: KubernetesTarget,
        request: kubernetes.ReleaseRequest,
    ) -> kubernetes.ReleaseResult:
        correlated = replace(
            request,
            values_json=inject_release_correlation(request.values_json, request.operation_id),
        )
        return original_start(self, target, correlated)

    runtime_backend = cast(Any, kubernetes.RuntimeKubernetesBackend)
    runtime_backend.start = start
