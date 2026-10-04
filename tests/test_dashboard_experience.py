from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client, override_settings

from controlplane.models import AgentRegistration, DiagnosticOperation, Project

pytestmark = pytest.mark.django_db


@override_settings(DEBUG=True, WEB_DEMO_ENABLED=True)
def test_demo_is_isolated_from_live_project_state() -> None:
    user = User.objects.create_user("operator")
    project = Project.objects.create(name="Private live project", slug="private-live")
    project.members.add(user)
    AgentRegistration.objects.create(project=project, agent_id="real-agent")
    client = Client()
    client.force_login(user)

    response = client.get("/")
    body = response.content.decode()
    assert response.status_code == 200
    assert 'data-demo="true"' in body
    assert "Every result on this screen is simulated" in body
    assert "Private live project" not in body
    assert "real-agent" not in body
    assert Project.objects.count() == 1
    assert AgentRegistration.objects.count() == 1
    assert DiagnosticOperation.objects.count() == 0

    live = client.get("/?mode=live").content.decode()
    assert 'data-demo="false"' in live
    assert "Private live project" in live
    assert "real-agent" in live
    assert "No agent is connected to this project" in live


@override_settings(DEBUG=False, WEB_DEMO_ENABLED=True)
def test_production_cannot_enable_demo_via_query_parameter() -> None:
    user = User.objects.create_user("operator")
    client = Client()
    client.force_login(user)
    body = client.get("/?mode=demo").content.decode()
    assert 'data-demo="false"' in body
    assert "Every result on this screen is simulated" not in body
    assert "Atlas commerce" not in body


@override_settings(DEBUG=True, WEB_DEMO_ENABLED=False)
def test_live_dashboard_explains_empty_workspace_without_demo() -> None:
    user = User.objects.create_user("operator")
    client = Client()
    client.force_login(user)
    body = client.get("/").content.decode()
    assert "No authorized projects." in body
    assert "Connect your first environment" in body
    assert "Being a superuser does not replace project membership" in body
    assert "Interactive demo</a>" not in body


@override_settings(DEBUG=True, WEB_DEMO_ENABLED=True)
def test_live_diagnostic_returns_to_live_workspace() -> None:
    user = User.objects.create_user("operator")
    project = Project.objects.create(name="Live project", slug="live")
    project.members.add(user)
    agent = AgentRegistration.objects.create(project=project, agent_id="test-offline-agent")
    client = Client()
    client.force_login(user)
    response = client.post(f"/projects/{project.pk}/agents/{agent.pk}/diagnostics/start")
    assert response.status_code == 302
    assert response["Location"] == "/?mode=live"
    operation = DiagnosticOperation.objects.get()
    assert operation.agent_id == agent.pk
    assert operation.status == DiagnosticOperation.Status.OFFLINE
