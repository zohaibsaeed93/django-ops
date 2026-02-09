from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from djangoops.compose import load_config
from djangoops.config import DjangoOpsConfig, render_config
from djangoops.ecosystem_config import parse_project_config, render_project_config
from djangoops.integrations import (
    BUILTIN_REGISTRY,
    IntegrationError,
    evaluate_integration,
    parse_integrations,
)

from controlplane.ecosystem import evaluate_project_integrations, project_catalog
from controlplane.models import AgentRegistration, DiagnosticOperation, Project

IntegrationMapping = dict[str, object]
IntegrationMutator = Callable[[list[IntegrationMapping]], None]


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


def _integrations() -> list[IntegrationMapping]:
    return [
        {
            "name": "django-runtime",
            "version": "v1",
            "enabled": True,
            "capabilities": ["metadata", "health"],
            "config": {"health_path": "/healthz", "metadata_label": "django"},
        },
        {
            "name": "otlp-export",
            "version": "v1",
            "enabled": False,
            "capabilities": ["telemetry", "deployment_config"],
            "config": {
                "endpoint": "https://collector.example.test:4318",
                "service_namespace": "djangoops",
            },
        },
    ]


def test_builtin_registry_is_deterministic_and_narrow() -> None:
    definitions = BUILTIN_REGISTRY.definitions()
    assert [definition.name for definition in definitions] == ["django-runtime", "otlp-export"]
    assert all(definition.versions == ("v1",) for definition in definitions)
    assert not any(
        forbidden in definition.config_keys
        for definition in definitions
        for forbidden in ("command", "image", "manifest", "python", "graphql")
    )


def test_valid_discovery_config_round_trip_and_reference_success() -> None:
    parsed = parse_integrations(_integrations())
    assert [item.name for item in parsed] == ["django-runtime", "otlp-export"]
    result = evaluate_integration(parsed[0], context={"project": "sample-app"})
    assert result.status == "ready"
    assert result.summary == {
        "kind": "django-runtime",
        "health_path": "/healthz",
        "metadata_label": "django",
        "project": "sample-app",
    }
    rendered = render_project_config(_core_config(), parsed)
    config, reparsed = parse_project_config(rendered)
    assert config.project.name == "sample-app"
    assert reparsed == parsed


def test_core_paths_remain_backwards_compatible_with_no_integrations(tmp_path: Path) -> None:
    text = render_config(_core_config())
    config, integrations = parse_project_config(text)
    assert config.project.name == "sample-app"
    assert integrations == ()

    path = tmp_path / "djangoops.yaml"
    path.write_text(text, encoding="utf-8")
    assert load_config(path) == config


def test_core_paths_accept_enabled_ecosystem_section_without_secret_material(
    tmp_path: Path,
) -> None:
    config = _core_config()
    parsed = parse_integrations(_integrations())
    text = render_project_config(config, parsed)
    assert "token" not in text.lower()
    assert "password" not in text.lower()
    assert "authorization" not in text.lower()
    path = tmp_path / "djangoops.yaml"
    path.write_text(text, encoding="utf-8")
    assert load_config(path) == config


def _set_unknown_name(values: list[IntegrationMapping]) -> None:
    values[0] = {**values[0], "name": "unknown"}


def _set_unknown_version(values: list[IntegrationMapping]) -> None:
    values[0] = {**values[0], "version": "v99"}


def _append_duplicate(values: list[IntegrationMapping]) -> None:
    values.append(dict(values[0]))


def _set_unknown_capability(values: list[IntegrationMapping]) -> None:
    values[0] = {
        **values[0],
        "capabilities": ["metadata", "arbitrary_shell"],
    }


@pytest.mark.parametrize(
    ("mutator", "code"),
    [
        (_set_unknown_name, "UNKNOWN_INTEGRATION"),
        (_set_unknown_version, "UNSUPPORTED_VERSION"),
        (_append_duplicate, "DUPLICATE_INTEGRATION"),
        (_set_unknown_capability, "UNSUPPORTED_CAPABILITY"),
    ],
)
def test_unknown_version_capability_and_duplicates_fail_closed(
    mutator: IntegrationMutator,
    code: str,
) -> None:
    values = _integrations()
    mutator(values)
    with pytest.raises(IntegrationError) as caught:
        parse_integrations(values)
    assert caught.value.code == code


def test_secret_bearing_endpoint_and_schema_drift_are_rejected() -> None:
    values = _integrations()
    values[1] = {
        **values[1],
        "config": {
            "endpoint": "https://user:token@collector.example.test:4318",
            "service_namespace": "djangoops",
        },
    }
    with pytest.raises(IntegrationError) as caught:
        parse_integrations(values)
    assert caught.value.code == "SECRET_REJECTED"

    values = _integrations()
    values[0] = {**values[0], "config": {"health_path": "/healthz", "command": "id"}}
    with pytest.raises(IntegrationError) as caught:
        parse_integrations(values)
    assert caught.value.code == "SCHEMA_DRIFT"


def test_integration_count_and_timeout_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(IntegrationError) as caught:
        parse_integrations([_integrations()[0] for _ in range(9)])
    assert caught.value.code == "LIMIT_EXCEEDED"

    integration = parse_integrations(_integrations())[0]
    ticks = iter((0, 2_000_000))
    monkeypatch.setattr("djangoops.integrations.time.monotonic_ns", lambda: next(ticks))
    with pytest.raises(IntegrationError) as caught:
        evaluate_integration(integration, budget_ms=1)
    assert caught.value.code == "TIMEOUT"


@pytest.mark.django_db
def test_catalog_and_evaluation_are_project_authorized() -> None:
    owner = User.objects.create_user(username="owner", password="password")
    stranger = User.objects.create_user(username="stranger", password="password")
    project = Project.objects.create(slug="sample", name="Sample")
    project.members.add(owner)

    assert [item.name for item in project_catalog(owner, project.pk)] == [
        "django-runtime",
        "otlp-export",
    ]
    with pytest.raises(Project.DoesNotExist):
        project_catalog(stranger, project.pk)

    results = evaluate_project_integrations(owner, project.pk, parse_integrations(_integrations()))
    assert [result.status for result in results] == ["ready", "disabled"]
    with pytest.raises(Project.DoesNotExist):
        evaluate_project_integrations(stranger, project.pk, parse_integrations(_integrations()))


@pytest.mark.django_db
def test_optional_integration_failure_does_not_mutate_core_operation_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = User.objects.create_user(username="owner", password="password")
    project = Project.objects.create(slug="sample", name="Sample")
    project.members.add(owner)
    agent = AgentRegistration.objects.create(project=project, agent_id="agent-1")
    operation = DiagnosticOperation.objects.create(
        project=project,
        agent=agent,
        requested_by=owner,
        status=DiagnosticOperation.Status.SUCCEEDED,
        result_summary={"checks": [{"name": "django", "status": "pass", "detail": "ok"}]},
    )
    integration = parse_integrations(_integrations())[0]
    ticks = iter((0, 200_000_000))
    monkeypatch.setattr("djangoops.integrations.time.monotonic_ns", lambda: next(ticks))
    with pytest.raises(IntegrationError, match="execution budget"):
        evaluate_project_integrations(owner, project.pk, (integration,))

    operation.refresh_from_db()
    assert operation.status == DiagnosticOperation.Status.SUCCEEDED
    assert operation.result_summary["checks"][0]["status"] == "pass"
