"""Typed Phase 0 project configuration for DjangoOps."""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

SCHEMA_VERSION = 3
_PROJECT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,62}$")
_MODULE_RE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")
_HOST_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_BUCKET_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{1,61}[a-z0-9])$")
_CRON_RE = re.compile(r"^[0-9*/?, -]+$")
_LEGACY_SCHEMA_1_KEYS = {"schema_version", "project", "django", "services", "tls", "storage"}
_SCHEMA_3_KEYS = {*_LEGACY_SCHEMA_1_KEYS, "backup"}


@dataclass(frozen=True, slots=True)
class ServiceConfig:
    postgres: bool = True
    redis: bool = True
    celery: bool = True
    celery_beat: bool = True


@dataclass(frozen=True, slots=True)
class DjangoConfig:
    module: str


@dataclass(frozen=True, slots=True)
class ProjectConfig:
    name: str


@dataclass(frozen=True, slots=True)
class TLSConfig:
    hostname: str
    acme_email: str


@dataclass(frozen=True, slots=True)
class StorageConfig:
    endpoint_url: str
    region: str | None
    static_bucket: str
    media_bucket: str


@dataclass(frozen=True, slots=True)
class BackupConfig:
    schedule: str
    bucket: str
    prefix: str = "djangoops-backups"


@dataclass(frozen=True, slots=True)
class DjangoOpsConfig:
    """Complete non-secret Phase 0 project configuration."""

    schema_version: int
    project: ProjectConfig
    django: DjangoConfig
    services: ServiceConfig
    tls: TLSConfig
    storage: StorageConfig
    backup: BackupConfig

    @classmethod
    def create(
        cls,
        project_name: str,
        django_module: str,
        hostname: str = "example.com",
        acme_email: str = "ops@example.com",
        storage_endpoint_url: str = "https://s3.example.com",
        storage_region: str | None = None,
        static_bucket: str = "djangoops-static",
        media_bucket: str = "djangoops-media",
        backup_schedule: str = "17 2 * * *",
        backup_bucket: str = "djangoops-backups",
        backup_prefix: str = "djangoops-backups",
    ) -> DjangoOpsConfig:
        return cls(
            schema_version=SCHEMA_VERSION,
            project=ProjectConfig(name=validate_project_name(project_name)),
            django=DjangoConfig(module=validate_django_module(django_module)),
            services=ServiceConfig(),
            tls=TLSConfig(
                hostname=validate_hostname(hostname),
                acme_email=validate_email(acme_email),
            ),
            storage=StorageConfig(
                endpoint_url=validate_storage_endpoint(storage_endpoint_url),
                region=validate_region(storage_region),
                static_bucket=validate_bucket(static_bucket, "static bucket"),
                media_bucket=validate_bucket(media_bucket, "media bucket"),
            ),
            backup=BackupConfig(
                schedule=validate_backup_schedule(backup_schedule),
                bucket=validate_bucket(backup_bucket, "backup bucket"),
                prefix=validate_backup_prefix(backup_prefix),
            ),
        )

    @classmethod
    def from_mapping(cls, value: object) -> DjangoOpsConfig:
        if not isinstance(value, dict):
            raise ValueError("configuration root must be a mapping")
        if "schema_version" not in value:
            raise ValueError("configuration is missing schema_version")

        schema_version = value["schema_version"]
        if schema_version == 1:
            if set(value) != _LEGACY_SCHEMA_1_KEYS:
                raise ValueError(
                    "djangoops.yaml uses schema_version 1; recreate or upgrade it "
                    "with the required TLS and S3 storage settings before using this release"
                )
            # Preserve the exact legacy compatibility accepted by the Phase 0 base. Schema v1
            # predates backup policy, so it receives only the historical project-scoped defaults
            # in memory. Schema v2 is deliberately different: it must be upgraded explicitly so
            # an operator chooses the backup destination rather than DjangoOps guessing one.
            value = dict(value)
            value["schema_version"] = SCHEMA_VERSION
            value["backup"] = {
                "schedule": "17 2 * * *",
                "bucket": "djangoops-backups",
                "prefix": "djangoops-backups",
            }
            schema_version = SCHEMA_VERSION
        elif schema_version == 2:
            raise ValueError(
                "djangoops.yaml uses schema_version 2; run 'djangoops config-upgrade' "
                "and review the generated backup settings before continuing"
            )

        if schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version: {schema_version!r}")
        if set(value) != _SCHEMA_3_KEYS:
            raise ValueError("configuration keys do not match schema_version 3")

        project = _require_mapping(value["project"], "project")
        django = _require_mapping(value["django"], "django")
        services = _require_mapping(value["services"], "services")
        tls = _require_mapping(value["tls"], "tls")
        storage = _require_mapping(value["storage"], "storage")
        backup = _require_mapping(value["backup"], "backup")
        if set(project) != {"name"} or set(django) != {"module"}:
            raise ValueError("project or django configuration keys are invalid")
        if set(services) != {"postgres", "redis", "celery", "celery_beat"}:
            raise ValueError("service configuration keys are invalid")
        if set(tls) != {"hostname", "acme_email"}:
            raise ValueError("TLS configuration keys are invalid")
        if set(storage) != {"endpoint_url", "region", "static_bucket", "media_bucket"}:
            raise ValueError("storage configuration keys are invalid")
        if set(backup) != {"schedule", "bucket", "prefix"}:
            raise ValueError("backup configuration keys are invalid")

        service_values: dict[str, bool] = {}
        for key in ("postgres", "redis", "celery", "celery_beat"):
            service_value = services[key]
            if not isinstance(service_value, bool):
                raise ValueError(f"services.{key} must be a boolean")
            service_values[key] = service_value

        region_value = storage["region"]
        if region_value is not None and not isinstance(region_value, str):
            raise ValueError("storage.region must be a string or null")

        project_name = _require_string(project["name"], "project.name")
        django_module = _require_string(django["module"], "django.module")
        hostname = _require_string(tls["hostname"], "tls.hostname")
        acme_email = _require_string(tls["acme_email"], "tls.acme_email")
        endpoint_url = _require_string(storage["endpoint_url"], "storage.endpoint_url")
        static_bucket = _require_string(storage["static_bucket"], "storage.static_bucket")
        media_bucket = _require_string(storage["media_bucket"], "storage.media_bucket")
        backup_schedule = _require_string(backup["schedule"], "backup.schedule")
        backup_bucket = _require_string(backup["bucket"], "backup.bucket")
        backup_prefix = _require_string(backup["prefix"], "backup.prefix")

        return cls(
            schema_version=SCHEMA_VERSION,
            project=ProjectConfig(name=validate_project_name(project_name)),
            django=DjangoConfig(module=validate_django_module(django_module)),
            services=ServiceConfig(**service_values),
            tls=TLSConfig(
                hostname=validate_hostname(hostname),
                acme_email=validate_email(acme_email),
            ),
            storage=StorageConfig(
                endpoint_url=validate_storage_endpoint(endpoint_url),
                region=validate_region(region_value),
                static_bucket=validate_bucket(static_bucket, "static bucket"),
                media_bucket=validate_bucket(media_bucket, "media bucket"),
            ),
            backup=BackupConfig(
                schedule=validate_backup_schedule(backup_schedule),
                bucket=validate_bucket(backup_bucket, "backup bucket"),
                prefix=validate_backup_prefix(backup_prefix),
            ),
        )

    def to_mapping(self) -> dict[str, Any]:
        return asdict(self)


def validate_project_name(value: str) -> str:
    if not _PROJECT_RE.fullmatch(value):
        raise ValueError(
            "project name must start with a letter and contain only letters, numbers, '-' or '_'"
        )
    return value


def validate_django_module(value: str) -> str:
    if not _MODULE_RE.fullmatch(value):
        raise ValueError("Django module must be a dotted Python identifier, for example 'config'")
    return value


def validate_hostname(value: str) -> str:
    candidate = value.rstrip(".")
    labels_valid = all(_HOST_LABEL_RE.fullmatch(label) for label in candidate.split("."))
    if len(candidate) > 253 or "." not in candidate or not labels_valid:
        raise ValueError("hostname must be a valid public DNS hostname")
    return candidate.lower()


def validate_email(value: str) -> str:
    if len(value) > 254 or not _EMAIL_RE.fullmatch(value):
        raise ValueError("ACME email must be a valid email address")
    return value


def validate_storage_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    has_forbidden_part = any((parsed.username, parsed.password, parsed.query, parsed.fragment))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or has_forbidden_part:
        raise ValueError(
            "storage endpoint must be an http(s) URL without credentials, query, or fragment"
        )
    if parsed.path not in {"", "/"}:
        raise ValueError("storage endpoint must not contain a path")
    return value.rstrip("/")


def validate_region(value: str | None) -> str | None:
    if value is None:
        return None
    if not value or len(value) > 63 or not re.fullmatch(r"[A-Za-z0-9-]+", value):
        raise ValueError("storage region must contain only letters, numbers, and '-' or be null")
    return value


def validate_bucket(value: str, field: str) -> str:
    separators_valid = ".." not in value and ".-" not in value and "-." not in value
    if not _BUCKET_RE.fullmatch(value) or not separators_valid:
        raise ValueError(f"{field} must be a valid S3-compatible bucket name")
    return value


def validate_backup_schedule(value: str) -> str:
    fields = value.split()
    fields_are_short = all(len(field) <= 32 for field in fields)
    if len(fields) != 5 or not _CRON_RE.fullmatch(value) or not fields_are_short:
        raise ValueError("backup schedule must be a five-field cron expression")
    return " ".join(fields)


def validate_backup_prefix(value: str) -> str:
    safe_chars = re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9/_-]*", value)
    if (
        not value
        or len(value) > 128
        or value.startswith("/")
        or value.endswith("/")
        or ".." in value
        or not safe_chars
    ):
        raise ValueError("backup prefix must be a safe relative object prefix")
    return value


def render_config(config: DjangoOpsConfig) -> str:
    return yaml.safe_dump(config.to_mapping(), sort_keys=False, default_flow_style=False)


def parse_config(text: str) -> DjangoOpsConfig:
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError("invalid YAML configuration") from exc
    return DjangoOpsConfig.from_mapping(loaded)


def upgrade_config(text: str, *, backup_bucket: str, backup_schedule: str) -> str:
    """Explicitly upgrade an exact schema-v2 document to schema v3."""
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError("invalid YAML configuration") from exc
    if not isinstance(loaded, dict) or loaded.get("schema_version") != 2:
        raise ValueError("config-upgrade currently requires an exact schema_version 2 document")
    if set(loaded) != _LEGACY_SCHEMA_1_KEYS:
        raise ValueError("schema_version 2 configuration contains unknown or missing keys")
    upgraded = dict(loaded)
    upgraded["schema_version"] = SCHEMA_VERSION
    upgraded["backup"] = {
        "schedule": validate_backup_schedule(backup_schedule),
        "bucket": validate_bucket(backup_bucket, "backup bucket"),
        "prefix": "djangoops-backups",
    }
    return render_config(DjangoOpsConfig.from_mapping(upgraded))


def write_new_config(path: Path, config: DjangoOpsConfig) -> None:
    write_new_text(path, render_config(config))


def write_new_text(path: Path, rendered: str) -> None:
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
