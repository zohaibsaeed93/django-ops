"""Typed Phase 0 project configuration for DjangoOps."""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

SCHEMA_VERSION = 1
_PROJECT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,62}$")
_MODULE_RE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")


@dataclass(frozen=True, slots=True)
class ServiceConfig:
    """Phase 0 service enablement used by later generators."""

    postgres: bool = True
    redis: bool = True
    celery: bool = True
    celery_beat: bool = True


@dataclass(frozen=True, slots=True)
class DjangoConfig:
    """Django application metadata that is safe to store in project config."""

    module: str


@dataclass(frozen=True, slots=True)
class ProjectConfig:
    """Project identity metadata."""

    name: str


@dataclass(frozen=True, slots=True)
class DjangoOpsConfig:
    """Complete non-secret Phase 0 project configuration."""

    schema_version: int
    project: ProjectConfig
    django: DjangoConfig
    services: ServiceConfig

    @classmethod
    def create(cls, project_name: str, django_module: str) -> DjangoOpsConfig:
        """Validate inputs and build the deterministic Phase 0 defaults."""
        return cls(
            schema_version=SCHEMA_VERSION,
            project=ProjectConfig(name=validate_project_name(project_name)),
            django=DjangoConfig(module=validate_django_module(django_module)),
            services=ServiceConfig(),
        )

    @classmethod
    def from_mapping(cls, value: object) -> DjangoOpsConfig:
        """Parse and validate a mapping loaded from YAML."""
        if not isinstance(value, dict):
            raise ValueError("configuration root must be a mapping")
        expected = {"schema_version", "project", "django", "services"}
        if set(value) != expected:
            raise ValueError("configuration keys do not match the Phase 0 schema")
        if value["schema_version"] != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version: {value['schema_version']!r}")

        project = _require_mapping(value["project"], "project")
        django = _require_mapping(value["django"], "django")
        services = _require_mapping(value["services"], "services")
        if set(project) != {"name"} or set(django) != {"module"}:
            raise ValueError("project or django configuration keys are invalid")
        if set(services) != {"postgres", "redis", "celery", "celery_beat"}:
            raise ValueError("service configuration keys are invalid")

        service_values: dict[str, bool] = {}
        for key in ("postgres", "redis", "celery", "celery_beat"):
            service_value = services[key]
            if not isinstance(service_value, bool):
                raise ValueError(f"services.{key} must be a boolean")
            service_values[key] = service_value

        project_name = _require_string(project["name"], "project.name")
        django_module = _require_string(django["module"], "django.module")
        return cls(
            schema_version=SCHEMA_VERSION,
            project=ProjectConfig(name=validate_project_name(project_name)),
            django=DjangoConfig(module=validate_django_module(django_module)),
            services=ServiceConfig(**service_values),
        )

    def to_mapping(self) -> dict[str, Any]:
        """Return a deterministic serialization mapping."""
        return asdict(self)


def validate_project_name(value: str) -> str:
    """Validate a portable, human-readable DjangoOps project name."""
    if not _PROJECT_RE.fullmatch(value):
        raise ValueError(
            "project name must start with a letter and contain only letters, numbers, '-' or '_'"
        )
    return value


def validate_django_module(value: str) -> str:
    """Validate a dotted Python module identifier without importing it."""
    if not _MODULE_RE.fullmatch(value):
        raise ValueError("Django module must be a dotted Python identifier, for example 'config'")
    return value


def render_config(config: DjangoOpsConfig) -> str:
    """Render stable, human-readable YAML without secret material."""
    return yaml.safe_dump(config.to_mapping(), sort_keys=False, default_flow_style=False)


def parse_config(text: str) -> DjangoOpsConfig:
    """Parse YAML and validate it against the current typed schema."""
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError("invalid YAML configuration") from exc
    return DjangoOpsConfig.from_mapping(loaded)


def write_new_config(path: Path, config: DjangoOpsConfig) -> None:
    """Create a new config exclusively and remove partial output on write failure."""
    write_new_text(path, render_config(config))


def write_new_text(path: Path, rendered: str) -> None:
    """Create a UTF-8 text file exclusively and clean it up if writing fails."""
    fd: int | None = None
    created = False
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        created = True
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            fd = None
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        if fd is not None:
            os.close(fd)
        if created:
            path.unlink(missing_ok=True)
        raise


def _require_mapping(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{field} must be a mapping")
    return value


def _require_string(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value
