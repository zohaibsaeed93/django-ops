from __future__ import annotations

import importlib
import json
from typing import Any

import pytest
from django.contrib.auth.models import User
from django.test import Client

from controlplane.models import AgentRegistration, Project


@pytest.mark.django_db
def test_observability_graphql_is_project_scoped() -> None:
    owner = User.objects.create_user(username="owner", password="password")
    other = User.objects.create_user(username="other", password="password")
    own = Project.objects.create(slug="own", name="Own")
    foreign = Project.objects.create(slug="foreign", name="Foreign")
    own.members.add(owner)
    foreign.members.add(other)
    AgentRegistration.objects.create(project=own, agent_id="agent-own")
    AgentRegistration.objects.create(project=foreign, agent_id="agent-foreign")

    query = """
      query Summary($projectId: ID!) {
        observabilitySummary(projectId: $projectId) {
          totalAgents connectedAgents recentOperations recentReleases recentFailures
          otlpConfigured telemetryStatus
        }
      }
    """
    client = Client()
    client.force_login(owner)
    own_response = client.post(
        "/graphql/v1",
        data=json.dumps({"query": query, "variables": {"projectId": str(own.pk)}}),
        content_type="application/json",
    )
    assert own_response.status_code == 200
    payload = own_response.json()["data"]["observabilitySummary"]
    assert payload["totalAgents"] == 1
    assert payload["connectedAgents"] == 0

    foreign_response = client.post(
        "/graphql/v1",
        data=json.dumps({"query": query, "variables": {"projectId": str(foreign.pk)}}),
        content_type="application/json",
    )
    assert foreign_response.status_code == 400
    body = foreign_response.json()
    assert "data" not in body or body["data"]["observabilitySummary"] is None
    assert body["errors"] == [{"message": "request could not be completed"}]


@pytest.mark.django_db
def test_graphql_release_values_accept_secretless_otlp_configuration() -> None:
    schema_module: Any = importlib.import_module("controlplane.graphql.schema")

    values = schema_module._release_values(
        {
            "observabilityEnabled": True,
            "otlpEndpoint": "https://otel.example.test:4318",
            "otelServiceNamespace": "payments",
        }
    )
    assert values["observability"] == {
        "enabled": True,
        "otlpEndpoint": "https://otel.example.test:4318",
        "serviceNamespace": "payments",
    }
