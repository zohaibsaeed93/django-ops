from __future__ import annotations

import json
from pathlib import Path

import djangoops.compose
import djangoops.config
import pytest
import yaml
from django.contrib.auth.models import User
from django.test import Client

from controlplane.kubernetes_observability import (
    inject_release_correlation,
    sanitize_observability,
)
from controlplane.models import AgentRegistration, DiagnosticOperation, Project
from controlplane.telemetry import (
    Telemetry,
    correlation_id,
    redact,
    traceparent,
    validate_otlp_endpoint,
)


def test_correlation_redaction_and_endpoint_validation_are_bounded() -> None:
    assert len(correlation_id("unsafe\n" + "x" * 1000)) <= 64
    assert "\n" not in correlation_id("unsafe\nvalue")
    assert "hunter2" not in redact("password=hunter2\nnext")
    assert len(redact("x" * 1000)) <= 256
    assert traceparent().startswith("00-")
    with pytest.raises(ValueError, match="without credentials"):
        validate_otlp_endpoint("https://user:pass@example.test:4318")


def test_metrics_have_bounded_labels_and_queue_pressure_is_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telemetry = Telemetry(max_buffer=8, endpoint="https://collector.example.test:4318")
    monkeypatch.setattr(telemetry, "_ensure_worker", lambda: None)
    for index in range(20):
        telemetry.observe(
            component="attacker\ncomponent",
            kind=f"unbounded-{index}",
            outcome=f"exception-{index}",
            duration_ms=index,
            correlation=f"op-{index}\nsecret",
        )
    exposed = telemetry.prometheus()
    assert 'component="controlplane"' in exposed
    assert 'kind="diagnostic"' in exposed
    assert 'outcome="failed"' in exposed
    assert "unbounded-19" not in exposed
    assert "exception-19" not in exposed
    assert "djangoops_telemetry_dropped_total 12" in exposed


@pytest.mark.django_db
def test_metrics_surface_is_staff_only() -> None:
    client = Client()
    member = User.objects.create_user(username="member", password="password")
    staff = User.objects.create_user(username="staff", password="password", is_staff=True)

    client.force_login(member)
    assert client.get("/internal/metrics").status_code == 403
    client.force_login(staff)
    response = client.get("/internal/metrics")
    assert response.status_code == 200
    body = response.content.decode()
    assert "djangoops_operations_total" in body
    assert "djangoops_agents_connected" in body
    assert "project=" not in body


@pytest.mark.django_db
def test_terminal_diagnostic_is_correlated_without_raw_result_data() -> None:
    user = User.objects.create_user(username="operator", password="password")
    project = Project.objects.create(slug="phase4", name="Phase 4")
    project.members.add(user)
    agent = AgentRegistration.objects.create(project=project, agent_id="agent-phase4")
    operation = DiagnosticOperation.objects.create(
        project=project,
        agent=agent,
        requested_by=user,
        diagnostic="django_health",
        result_summary={"detail": "password=hunter2"},
    )
    operation.status = DiagnosticOperation.Status.SUCCEEDED
    operation.save(update_fields=("status", "updated_at"))

    from controlplane.telemetry import telemetry

    events = telemetry.recent_for_project(project.pk)
    assert any(event["correlation_id"] == str(operation.public_id) for event in events)
    assert "hunter2" not in json.dumps(events)


def test_compose_otel_wiring_is_safe_off_and_secretless() -> None:
    config = djangoops.config.DjangoOpsConfig.create("sample-app", "config")
    document = yaml.safe_load(djangoops.compose.render_compose(config))
    web_env = document["services"]["web"]["environment"]
    worker_env = document["services"]["celery"]["environment"]
    assert web_env["OTEL_SDK_DISABLED"] == "${OTEL_SDK_DISABLED:-true}"
    assert web_env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "${OTEL_EXPORTER_OTLP_ENDPOINT:-}"
    assert web_env["OTEL_SERVICE_NAME"] == "sample-app-web"
    assert worker_env["OTEL_SERVICE_NAME"] == "sample-app-worker"
    assert "collector_token" not in djangoops.compose.render_compose(config).lower()


def test_helm_observability_rejects_credentials_and_injects_server_operation_id() -> None:
    with pytest.raises(ValueError):
        sanitize_observability(
            {"enabled": True, "otlpEndpoint": "https://user:pass@example.test:4318"}
        )
    sanitized = sanitize_observability(
        {
            "enabled": True,
            "otlpEndpoint": "https://otel.example.test:4318",
            "serviceNamespace": "djangoops",
            "correlationId": "client-forged",
        }
    )
    assert sanitized["correlationId"] == ""
    rendered = inject_release_correlation(
        json.dumps({"observability": sanitized}), "k8s-server-generated-123"
    )
    assert json.loads(rendered)["observability"]["correlationId"] == "k8s-server-generated-123"


def test_helm_templates_are_opt_in_and_do_not_add_collector_credentials() -> None:
    root = Path("charts/djangoops")
    values = yaml.safe_load((root / "values.yaml").read_text())
    assert values["observability"]["enabled"] is False
    templates = ("web.yaml", "celery.yaml")
    text = "\n".join((root / "templates" / name).read_text() for name in templates)
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" in text
    assert "DJANGOOPS_CORRELATION_ID" in text
    assert "token" not in text.lower()
    assert "password" not in text.lower()
