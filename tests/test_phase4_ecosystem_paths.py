from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from django.contrib.auth.models import User
from django.test import Client
from djangoops.compose import generate_compose
from djangoops.config import DjangoOpsConfig, render_config
from djangoops.ecosystem_config import render_project_config
from djangoops.integrations import parse_integrations

from controlplane.models import AgentRegistration, DiagnosticOperation, Project


def _core_config() -> DjangoOpsConfig:
    return DjangoOpsConfig.create(
        project_name="sample-app",
        django_module="config.settings",
        hostname="app.example.com",
        acme_email="ops@example.com",
        storage_endpoint_url="https://objects.example.com",
        storage_region="eu-west-1",
        static_bucket="sample-static",
        media_bucket="sample-media",
        backup_bucket="sample-backups",
    )


def _integration_payload(
    *, runtime_enabled: bool = True, otlp_enabled: bool = True
) -> list[dict[str, object]]:
    return [
        {
            "name": "django-runtime",
            "version": "v1",
            "enabled": runtime_enabled,
            "capabilities": ["metadata", "health"],
            "config": {"health_path": "/ready/", "metadata_label": "django"},
        },
        {
            "name": "otlp-export",
            "version": "v1",
            "enabled": otlp_enabled,
            "capabilities": ["telemetry", "deployment_config"],
            "config": {
                "endpoint": "https://collector.example.test:4318",
                "service_namespace": "tenant-a",
            },
        },
    ]


def _render_compose(tmp_path: Path, integrations: list[dict[str, object]] | None) -> dict[str, Any]:
    config_path = tmp_path / "djangoops.yaml"
    output_path = tmp_path / "compose.yaml"
    if integrations is None:
        config_path.write_text(render_config(_core_config()), encoding="utf-8")
    else:
        parsed = parse_integrations(integrations)
        config_path.write_text(render_project_config(_core_config(), parsed), encoding="utf-8")
    generate_compose(config_path, output_path)
    loaded = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_enabled_integrations_drive_compose_runtime_and_otlp_paths(tmp_path: Path) -> None:
    compose = _render_compose(tmp_path, _integration_payload())
    services = compose["services"]
    assert isinstance(services, dict)
    web = services["web"]
    assert isinstance(web, dict)
    environment = web["environment"]
    assert isinstance(environment, dict)
    assert environment["OTEL_SDK_DISABLED"] == "false"
    assert environment["OTEL_EXPORTER_OTLP_ENDPOINT"] == "https://collector.example.test:4318"
    assert "service.namespace=tenant-a" in environment["OTEL_RESOURCE_ATTRIBUTES"]
    healthcheck = web["healthcheck"]
    assert isinstance(healthcheck, dict)
    assert "/ready/" in " ".join(healthcheck["test"])


def test_disabled_integrations_disable_workload_behavior(tmp_path: Path) -> None:
    compose = _render_compose(
        tmp_path,
        _integration_payload(runtime_enabled=False, otlp_enabled=False),
    )
    web = compose["services"]["web"]
    assert "healthcheck" not in web
    assert web["environment"]["OTEL_SDK_DISABLED"] == "true"
    assert web["environment"]["OTEL_EXPORTER_OTLP_ENDPOINT"] == ""


def test_no_integrations_preserves_legacy_environment_driven_observability(tmp_path: Path) -> None:
    compose = _render_compose(tmp_path, None)
    web = compose["services"]["web"]
    assert "healthcheck" not in web
    assert web["environment"]["OTEL_SDK_DISABLED"] == "${OTEL_SDK_DISABLED:-true}"
    assert web["environment"]["OTEL_EXPORTER_OTLP_ENDPOINT"] == "${OTEL_EXPORTER_OTLP_ENDPOINT:-}"


@pytest.mark.django_db
def test_authenticated_project_ecosystem_surface_enforces_membership_and_evaluates() -> None:
    owner = User.objects.create_user(username="owner", password="password")
    stranger = User.objects.create_user(username="stranger", password="password")
    project = Project.objects.create(slug="sample", name="Sample")
    project.members.add(owner)

    client = Client()
    client.force_login(owner)
    response = client.get(f"/projects/{project.pk}/ecosystem")
    assert response.status_code == 200
    assert [item["name"] for item in response.json()["integrations"]] == [
        "django-runtime",
        "otlp-export",
    ]

    response = client.post(
        f"/projects/{project.pk}/ecosystem",
        data=json.dumps({"integrations": _integration_payload()}),
        content_type="application/json",
    )
    assert response.status_code == 200
    assert [item["status"] for item in response.json()["integrations"]] == ["ready", "ready"]
    assert response.json()["integrations"][0]["summary"]["health_path"] == "/ready/"

    client.force_login(stranger)
    assert client.get(f"/projects/{project.pk}/ecosystem").status_code == 404


@pytest.mark.django_db
def test_ecosystem_surface_fails_closed_on_secrets_without_mutating_core_state() -> None:
    owner = User.objects.create_user(username="owner", password="password")
    project = Project.objects.create(slug="sample", name="Sample")
    project.members.add(owner)
    agent = AgentRegistration.objects.create(project=project, agent_id="agent-1")
    operation = DiagnosticOperation.objects.create(
        project=project,
        agent=agent,
        requested_by=owner,
        status=DiagnosticOperation.Status.SUCCEEDED,
    )
    payload = _integration_payload()
    payload[1]["config"] = {
        "endpoint": "https://user:token@collector.example.test:4318",
        "service_namespace": "tenant-a",
    }

    client = Client()
    client.force_login(owner)
    response = client.post(
        f"/projects/{project.pk}/ecosystem",
        data=json.dumps({"integrations": payload}),
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json() == {"error": {"code": "SECRET_REJECTED"}}
    operation.refresh_from_db()
    assert operation.status == DiagnosticOperation.Status.SUCCEEDED
