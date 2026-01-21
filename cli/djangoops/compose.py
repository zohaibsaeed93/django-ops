"""Deterministic Phase 0 Docker Compose generation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from djangoops.config import DjangoOpsConfig, parse_config, write_new_text

COMPOSE_FILENAME = "docker-compose.yml"
POSTGRES_IMAGE = "postgres:16"
REDIS_IMAGE = "redis:7-alpine"
TRAEFIK_IMAGE = "traefik:v3.7.1"
_APP_ENV_FILE = [".env"]
_APP_BUILD = {"context": "."}
_RESTART_POLICY = "unless-stopped"


def load_config(path: Path) -> DjangoOpsConfig:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValueError(f"{path.name} does not exist") from exc
    return parse_config(text)


def render_compose(config: DjangoOpsConfig) -> str:
    services: dict[str, Any] = {}
    volumes: dict[str, Any] = {"traefik_acme": {}}

    services["traefik"] = {
        "image": TRAEFIK_IMAGE,
        "restart": _RESTART_POLICY,
        "command": [
            "--providers.docker=true",
            "--providers.docker.exposedbydefault=false",
            "--entrypoints.web.address=:80",
            "--entrypoints.web.http.redirections.entrypoint.to=websecure",
            "--entrypoints.web.http.redirections.entrypoint.scheme=https",
            "--entrypoints.websecure.address=:443",
            f"--certificatesresolvers.letsencrypt.acme.email={config.tls.acme_email}",
            "--certificatesresolvers.letsencrypt.acme.storage=/letsencrypt/acme.json",
            "--certificatesresolvers.letsencrypt.acme.httpchallenge=true",
            "--certificatesresolvers.letsencrypt.acme.httpchallenge.entrypoint=web",
        ],
        "ports": ["80:80", "443:443"],
        "volumes": [
            "/var/run/docker.sock:/var/run/docker.sock:ro",
            "traefik_acme:/letsencrypt",
        ],
    }

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
        "environment": _storage_environment(config),
        "restart": _RESTART_POLICY,
        "expose": ["8000"],
        "labels": [
            "traefik.enable=true",
            f"traefik.http.routers.web.rule=Host(`{config.tls.hostname}`)",
            "traefik.http.routers.web.entrypoints=websecure",
            "traefik.http.routers.web.tls=true",
            "traefik.http.routers.web.tls.certresolver=letsencrypt",
            "traefik.http.services.web.loadbalancer.server.port=8000",
        ],
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
                    ("pg_isready -U $${POSTGRES_USER:-djangoops} -d $${POSTGRES_DB:-djangoops}"),
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

    document = {
        "name": config.project.name,
        "services": services,
        "volumes": volumes,
    }
    return yaml.safe_dump(document, sort_keys=False, default_flow_style=False)


def generate_compose(config_path: Path, output_path: Path) -> None:
    config = load_config(config_path)
    write_new_text(output_path, render_compose(config))


def _storage_environment(config: DjangoOpsConfig) -> dict[str, str]:
    environment = {
        "DJANGOOPS_S3_ENDPOINT_URL": config.storage.endpoint_url,
        "DJANGOOPS_S3_STATIC_BUCKET": config.storage.static_bucket,
        "DJANGOOPS_S3_MEDIA_BUCKET": config.storage.media_bucket,
        "AWS_ACCESS_KEY_ID": "${AWS_ACCESS_KEY_ID:?AWS_ACCESS_KEY_ID must be set}",
        "AWS_SECRET_ACCESS_KEY": ("${AWS_SECRET_ACCESS_KEY:?AWS_SECRET_ACCESS_KEY must be set}"),
    }
    if config.storage.region is not None:
        environment["AWS_REGION"] = config.storage.region
    return environment


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
        "environment": _storage_environment(config),
        "restart": _RESTART_POLICY,
    }
    dependencies = _infrastructure_dependencies(config)
    if dependencies:
        service["depends_on"] = dependencies
    return service


def _django_project_module(config: DjangoOpsConfig) -> str:
    module = config.django.module
    if module.endswith(".settings"):
        return module.removesuffix(".settings")
    return module
