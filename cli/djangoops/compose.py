"""Deterministic Phase 0 Docker Compose generation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from djangoops.config import DjangoOpsConfig, parse_config, write_new_text

COMPOSE_FILENAME = "docker-compose.yml"
POSTGRES_IMAGE = "postgres:16"
REDIS_IMAGE = "redis:7-alpine"
_APP_ENV_FILE = [".env"]
_APP_BUILD = {"context": "."}
_RESTART_POLICY = "unless-stopped"


def load_config(path: Path) -> DjangoOpsConfig:
    """Read and validate a DjangoOps project configuration."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValueError(f"{path.name} does not exist") from exc
    return parse_config(text)


def render_compose(config: DjangoOpsConfig) -> str:
    """Render stable Compose YAML for the configured Phase 0 topology."""
    services: dict[str, Any] = {}
    volumes: dict[str, Any] = {}

    dependencies = _infrastructure_dependencies(config)
    services["web"] = {
        "build": dict(_APP_BUILD),
        "command": [
            "gunicorn",
            f"{_django_project_module(config)}.wsgi:application",
            "--bind",
            "0.0.0.0:8000",
        ],
        "env_file": list(_APP_ENV_FILE),
        "restart": _RESTART_POLICY,
        "expose": ["8000"],
    }
    if dependencies:
        services["web"]["depends_on"] = dependencies

    if config.services.postgres:
        services["postgres"] = {
            "image": POSTGRES_IMAGE,
            "restart": _RESTART_POLICY,
            "environment": {
                "POSTGRES_DB": "${POSTGRES_DB:-djangoops}",
                "POSTGRES_USER": "${POSTGRES_USER:-djangoops}",
                "POSTGRES_PASSWORD": "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set}",
            },
            "volumes": ["postgres_data:/var/lib/postgresql/data"],
            "healthcheck": {
                "test": [
                    "CMD-SHELL",
                    "pg_isready -U $${POSTGRES_USER:-djangoops} -d $${POSTGRES_DB:-djangoops}",
                ],
                "interval": "10s",
                "timeout": "5s",
                "retries": 5,
            },
        }
        volumes["postgres_data"] = {}

    if config.services.redis:
        services["redis"] = {
            "image": REDIS_IMAGE,
            "restart": _RESTART_POLICY,
            "command": ["redis-server", "--appendonly", "yes"],
            "volumes": ["redis_data:/data"],
            "healthcheck": {
                "test": ["CMD", "redis-cli", "ping"],
                "interval": "10s",
                "timeout": "5s",
                "retries": 5,
            },
        }
        volumes["redis_data"] = {}

    if config.services.celery:
        services["celery"] = _celery_service(config, ["worker", "--loglevel=INFO"])
    if config.services.celery_beat:
        services["celery_beat"] = _celery_service(config, ["beat", "--loglevel=INFO"])

    document: dict[str, Any] = {"services": services}
    if volumes:
        document["volumes"] = volumes
    return yaml.safe_dump(document, sort_keys=False, default_flow_style=False)


def generate_compose(config_path: Path, output_path: Path) -> None:
    """Validate configuration, render Compose, then create output exclusively."""
    config = load_config(config_path)
    rendered = render_compose(config)
    write_new_text(output_path, rendered)


def _infrastructure_dependencies(config: DjangoOpsConfig) -> dict[str, dict[str, str]]:
    dependencies: dict[str, dict[str, str]] = {}
    if config.services.postgres:
        dependencies["postgres"] = {"condition": "service_healthy"}
    if config.services.redis:
        dependencies["redis"] = {"condition": "service_healthy"}
    return dependencies


def _celery_service(config: DjangoOpsConfig, action: list[str]) -> dict[str, Any]:
    service: dict[str, Any] = {
        "build": dict(_APP_BUILD),
        "command": ["celery", "-A", _django_project_module(config), *action],
        "env_file": list(_APP_ENV_FILE),
        "restart": _RESTART_POLICY,
    }
    dependencies = _infrastructure_dependencies(config)
    if dependencies:
        service["depends_on"] = dependencies
    return service


def _django_project_module(config: DjangoOpsConfig) -> str:
    """Return the import package that owns wsgi.py and the Celery app."""
    module = config.django.module
    if module.endswith(".settings"):
        return module.removesuffix(".settings")
    return module
