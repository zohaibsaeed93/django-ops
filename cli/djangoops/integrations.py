"""Bounded, allowlisted DjangoOps ecosystem integration contract.

Integrations are compiled into DjangoOps. User configuration can select only these
registered definitions; it cannot name import paths, packages, images, repositories,
commands, GraphQL documents, or Kubernetes resources.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

MAX_INTEGRATIONS = 8
MAX_CONFIG_FIELDS = 16
MAX_CONFIG_VALUE_CHARS = 256
MAX_RESULT_FIELDS = 16
MAX_RESULT_VALUE_CHARS = 256
DEFAULT_BUDGET_MS = 100
MAX_BUDGET_MS = 1000

_SECRET_KEY_RE = re.compile(
    r"(?:secret|token|password|authorization|api[_-]?key|access[_-]?key|credential)", re.I
)
_SAFE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,47}$")
_SAFE_NAMESPACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
_SAFE_HEALTH_PATH_RE = re.compile(r"^/[A-Za-z0-9/_-]{0,127}$")


class IntegrationCapability(StrEnum):
    METADATA = "metadata"
    HEALTH = "health"
    TELEMETRY = "telemetry"
    DEPLOYMENT_CONFIG = "deployment_config"


class IntegrationError(ValueError):
    """Explicit bounded ecosystem contract error."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class IntegrationDefinition:
    name: str
    versions: tuple[str, ...]
    capabilities: tuple[IntegrationCapability, ...]
    config_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IntegrationConfig:
    name: str
    version: str
    enabled: bool
    capabilities: tuple[str, ...]
    config: Mapping[str, str]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "enabled": self.enabled,
            "capabilities": list(self.capabilities),
            "config": dict(self.config),
        }


@dataclass(frozen=True, slots=True)
class IntegrationResult:
    name: str
    version: str
    status: str
    capabilities: tuple[str, ...]
    summary: Mapping[str, str]


class IntegrationRegistry:
    """Deterministic registry of built-in, audited integration definitions."""

    def __init__(self, definitions: tuple[IntegrationDefinition, ...]) -> None:
        registered: dict[str, IntegrationDefinition] = {}
        for definition in definitions:
            if definition.name in registered:
                raise IntegrationError(
                    "DUPLICATE_REGISTRATION",
                    f"duplicate integration registration: {definition.name}",
                )
            if not _SAFE_NAME_RE.fullmatch(definition.name):
                raise IntegrationError("INVALID_REGISTRATION", "integration name is invalid")
            registered[definition.name] = definition
        self._definitions = MappingProxyType(registered)

    def definitions(self) -> tuple[IntegrationDefinition, ...]:
        return tuple(self._definitions[name] for name in sorted(self._definitions))

    def resolve(self, name: str, version: str) -> IntegrationDefinition:
        definition = self._definitions.get(name)
        if definition is None:
            raise IntegrationError("UNKNOWN_INTEGRATION", f"unknown integration: {name}")
        if version not in definition.versions:
            raise IntegrationError(
                "UNSUPPORTED_VERSION", f"unsupported {name} integration version: {version}"
            )
        return definition


BUILTIN_REGISTRY = IntegrationRegistry(
    (
        IntegrationDefinition(
            name="django-runtime",
            versions=("v1",),
            capabilities=(IntegrationCapability.METADATA, IntegrationCapability.HEALTH),
            config_keys=("health_path", "metadata_label"),
        ),
        IntegrationDefinition(
            name="otlp-export",
            versions=("v1",),
            capabilities=(IntegrationCapability.TELEMETRY, IntegrationCapability.DEPLOYMENT_CONFIG),
            config_keys=("endpoint", "service_namespace"),
        ),
    )
)


def parse_integrations(value: object) -> tuple[IntegrationConfig, ...]:
    """Validate and normalize project integration configuration.

    The input is intentionally data-only. No imports or dynamic installation occur.
    """
    if value is None:
        return ()
    if not isinstance(value, list):
        raise IntegrationError("INVALID_CONFIG", "integrations must be a list")
    if len(value) > MAX_INTEGRATIONS:
        raise IntegrationError(
            "LIMIT_EXCEEDED", f"at most {MAX_INTEGRATIONS} integrations are allowed"
        )

    seen: set[str] = set()
    parsed: list[IntegrationConfig] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict) or not all(isinstance(key, str) for key in raw):
            raise IntegrationError("INVALID_CONFIG", f"integrations[{index}] must be a mapping")
        if set(raw) != {"name", "version", "enabled", "capabilities", "config"}:
            raise IntegrationError(
                "SCHEMA_DRIFT", f"integrations[{index}] keys do not match ecosystem contract v1"
            )
        name = _string(raw["name"], f"integrations[{index}].name")
        version = _string(raw["version"], f"integrations[{index}].version")
        enabled = raw["enabled"]
        if not isinstance(enabled, bool):
            raise IntegrationError(
                "INVALID_CONFIG", f"integrations[{index}].enabled must be boolean"
            )
        if name in seen:
            raise IntegrationError(
                "DUPLICATE_INTEGRATION", f"integration configured more than once: {name}"
            )
        seen.add(name)
        definition = BUILTIN_REGISTRY.resolve(name, version)

        capabilities_raw = raw["capabilities"]
        if not isinstance(capabilities_raw, list) or not all(
            isinstance(item, str) for item in capabilities_raw
        ):
            raise IntegrationError(
                "INVALID_CONFIG", f"integrations[{index}].capabilities must be a list of strings"
            )
        if len(capabilities_raw) != len(set(capabilities_raw)):
            raise IntegrationError(
                "INVALID_CONFIG", f"{name} capabilities must not contain duplicates"
            )
        supported = {capability.value for capability in definition.capabilities}
        requested = tuple(capabilities_raw)
        unsupported = sorted(set(requested) - supported)
        if unsupported:
            raise IntegrationError(
                "UNSUPPORTED_CAPABILITY", f"{name} does not support: {', '.join(unsupported)}"
            )

        config = _validate_config(definition, raw["config"], index=index)
        parsed.append(
            IntegrationConfig(
                name=name,
                version=version,
                enabled=enabled,
                capabilities=requested,
                config=MappingProxyType(config),
            )
        )
    return tuple(parsed)


def evaluate_integration(
    integration: IntegrationConfig,
    *,
    context: Mapping[str, str] | None = None,
    budget_ms: int = DEFAULT_BUDGET_MS,
) -> IntegrationResult:
    """Evaluate one built-in integration with explicit time/result bounds and no retries."""
    if budget_ms < 1 or budget_ms > MAX_BUDGET_MS:
        raise IntegrationError("INVALID_BUDGET", f"budget_ms must be between 1 and {MAX_BUDGET_MS}")
    started = time.monotonic_ns()
    if not integration.enabled:
        return IntegrationResult(
            integration.name,
            integration.version,
            "disabled",
            integration.capabilities,
            MappingProxyType({}),
        )
    definition = BUILTIN_REGISTRY.resolve(integration.name, integration.version)
    supported = {capability.value for capability in definition.capabilities}
    if not set(integration.capabilities).issubset(supported):
        raise IntegrationError(
            "UNSUPPORTED_CAPABILITY", "configured capabilities no longer match registry"
        )

    safe_context = _validate_context(context or {})
    if integration.name == "django-runtime":
        summary = _django_runtime_summary(integration.config, safe_context)
    elif integration.name == "otlp-export":
        summary = _otlp_export_summary(integration.config, safe_context)
    else:  # registry and dispatcher must remain in lock-step
        raise IntegrationError("UNKNOWN_INTEGRATION", f"unknown integration: {integration.name}")

    elapsed_ms = (time.monotonic_ns() - started) / 1_000_000
    if elapsed_ms > budget_ms:
        raise IntegrationError(
            "TIMEOUT", f"{integration.name} integration exceeded its execution budget"
        )
    bounded = _bounded_result(summary)
    return IntegrationResult(
        integration.name,
        integration.version,
        "ready",
        integration.capabilities,
        MappingProxyType(bounded),
    )


def _validate_config(
    definition: IntegrationDefinition, value: object, *, index: int
) -> dict[str, str]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise IntegrationError("INVALID_CONFIG", f"integrations[{index}].config must be a mapping")
    if len(value) > MAX_CONFIG_FIELDS:
        raise IntegrationError("LIMIT_EXCEEDED", "integration config has too many fields")
    unknown = sorted(set(value) - set(definition.config_keys))
    if unknown:
        raise IntegrationError(
            "SCHEMA_DRIFT", f"unknown {definition.name} config keys: {', '.join(unknown)}"
        )

    validated: dict[str, str] = {}
    for key, raw_value in value.items():
        if _SECRET_KEY_RE.search(key):
            raise IntegrationError(
                "SECRET_REJECTED", f"secret-bearing config key is forbidden: {key}"
            )
        text = _string(raw_value, f"integration config {key}")
        if len(text) > MAX_CONFIG_VALUE_CHARS:
            raise IntegrationError("LIMIT_EXCEEDED", f"integration config {key} is too long")
        validated[key] = text

    if definition.name == "django-runtime":
        health_path = validated.get("health_path", "/healthz")
        if not _SAFE_HEALTH_PATH_RE.fullmatch(health_path) or ".." in health_path:
            raise IntegrationError("INVALID_CONFIG", "django-runtime health_path is invalid")
        validated["health_path"] = health_path
        metadata_label = validated.get("metadata_label", "django")
        if not _SAFE_NAMESPACE_RE.fullmatch(metadata_label):
            raise IntegrationError("INVALID_CONFIG", "django-runtime metadata_label is invalid")
        validated["metadata_label"] = metadata_label
    elif definition.name == "otlp-export":
        endpoint = validated.get("endpoint")
        if endpoint is None:
            raise IntegrationError("INVALID_CONFIG", "otlp-export requires endpoint")
        validated["endpoint"] = _validate_endpoint(endpoint)
        namespace = validated.get("service_namespace", "djangoops")
        if not _SAFE_NAMESPACE_RE.fullmatch(namespace):
            raise IntegrationError("INVALID_CONFIG", "otlp-export service_namespace is invalid")
        validated["service_namespace"] = namespace
    return validated


def _validate_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise IntegrationError(
            "SECRET_REJECTED",
            "OTLP endpoint must be a credential-free http(s) origin without query or fragment",
        )
    return value.rstrip("/")


def _validate_context(context: Mapping[str, str]) -> dict[str, str]:
    if len(context) > MAX_CONFIG_FIELDS:
        raise IntegrationError("LIMIT_EXCEEDED", "integration context has too many fields")
    result: dict[str, str] = {}
    for key, value in context.items():
        if _SECRET_KEY_RE.search(key):
            raise IntegrationError(
                "SECRET_REJECTED", "secret-bearing integration context is forbidden"
            )
        if len(key) > 64 or len(value) > MAX_CONFIG_VALUE_CHARS:
            raise IntegrationError("LIMIT_EXCEEDED", "integration context field exceeds bounds")
        result[key] = value
    return result


def _django_runtime_summary(
    config: Mapping[str, str], context: Mapping[str, str]
) -> dict[str, str]:
    return {
        "kind": "django-runtime",
        "health_path": config["health_path"],
        "metadata_label": config["metadata_label"],
        "project": context.get("project", "unknown"),
    }


def _otlp_export_summary(config: Mapping[str, str], context: Mapping[str, str]) -> dict[str, str]:
    return {
        "kind": "otlp-export",
        "endpoint": config["endpoint"],
        "service_namespace": config["service_namespace"],
        "project": context.get("project", "unknown"),
    }


def _bounded_result(value: Mapping[str, str]) -> dict[str, str]:
    if len(value) > MAX_RESULT_FIELDS:
        raise IntegrationError("LIMIT_EXCEEDED", "integration result has too many fields")
    result: dict[str, str] = {}
    for key, item in value.items():
        if _SECRET_KEY_RE.search(key) or len(key) > 64 or len(item) > MAX_RESULT_VALUE_CHARS:
            raise IntegrationError("RESULT_REJECTED", "integration result violates safety bounds")
        result[key] = item
    return result


def _string(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise IntegrationError("INVALID_CONFIG", f"{field} must be a string")
    return value
