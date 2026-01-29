from __future__ import annotations

import json
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.test import Client, override_settings

from controlplane.models import AgentRegistration, DiagnosticOperation, Project
from controlplane.services import OperationsService, set_backend_factory

pytestmark = pytest.mark.django_db
Data = tuple[Any, Any, Project, Project, AgentRegistration]


class FakeBackend:
    connected_agents = {"agent-a"}
    cancelled: list[str] = []
    cancel_error: Exception | None = None

    def connected(self, agent_id: str) -> bool:
        return agent_id in self.connected_agents

    def heartbeat_age(self, agent_id: str) -> int | None:
        return 3 if self.connected(agent_id) else None

    def capabilities(self, agent_id: str) -> tuple[str, ...]:
        return ("django_diagnostics_v1",) if self.connected(agent_id) else ()

    def protocol_version(self, agent_id: str) -> tuple[int, int]:
        return (1, 0) if self.connected(agent_id) else (0, 0)

    def start(self, operation: DiagnosticOperation) -> None:
        operation.status = DiagnosticOperation.Status.RUNNING
        operation.result_summary = {
            "checks": [{"name": "django_check", "status": "pass", "detail": "ok"}]
        }
        operation.save()

    def cancel(self, operation: DiagnosticOperation) -> bool:
        if self.cancel_error is not None:
            raise self.cancel_error
        self.cancelled.append(str(operation.public_id))
        return True


@pytest.fixture(autouse=True)
def fake_backend() -> None:
    FakeBackend.connected_agents = {"agent-a"}
    FakeBackend.cancelled = []
    FakeBackend.cancel_error = None
    set_backend_factory(FakeBackend)


@pytest.fixture
def data() -> Data:
    user_model = get_user_model()
    alice = user_model.objects.create_user("alice", password="pw")
    bob = user_model.objects.create_user("bob", password="pw")
    project = Project.objects.create(slug="alpha", name="Alpha", release_root="current")
    other = Project.objects.create(slug="beta", name="Beta")
    project.members.add(alice)
    other.members.add(bob)
    agent = AgentRegistration.objects.create(
        project=project,
        agent_id="agent-a",
        capabilities=["registered_capability"],
    )
    AgentRegistration.objects.create(
        project=other,
        agent_id="agent-b",
        capabilities=["django_diagnostics_v1"],
    )
    return alice, bob, project, other, agent


def gql(client: Client, query: str) -> Any:
    return client.post(
        "/graphql/v1",
        data=json.dumps({"query": query}),
        content_type="application/json",
    )


def test_anonymous_dashboard_and_graphql_fail_closed() -> None:
    client = Client()
    assert client.get("/").status_code == 302
    assert client.post("/graphql/v1", data="{}", content_type="application/json").status_code == 302


def test_project_isolation_and_typed_start(data: Data) -> None:
    alice, _, project, other, agent = data
    client = Client()
    client.force_login(alice)
    response = gql(client, "{ projects { id slug } }")
    assert response.status_code == 200
    assert response.json()["data"]["projects"] == [{"id": str(project.pk), "slug": "alpha"}]
    hidden = gql(client, f'{{ agents(projectId: "{other.pk}") {{ agentId }} }}')
    assert hidden.status_code == 400
    query = f'''mutation {{
      startDiagnostic(
        projectId: "{project.pk}",
        agentId: "{agent.pk}",
        diagnostic: DJANGO_HEALTH
      ) {{
        operation {{ status diagnostic checks {{ name status detail }} }}
        error {{ code }}
      }}
    }}'''
    started = gql(client, query)
    assert started.status_code == 200
    payload = started.json()["data"]["startDiagnostic"]["operation"]
    assert payload["status"] == "RUNNING"
    assert payload["diagnostic"] == "DJANGO_HEALTH"
    assert DiagnosticOperation.objects.count() == 1


def test_cross_project_mutations_fail_closed(data: Data) -> None:
    alice, bob, _, other, _ = data
    foreign_agent = other.agents.get(agent_id="agent-b")
    foreign_operation = DiagnosticOperation.objects.create(
        project=other,
        agent=foreign_agent,
        requested_by=bob,
        status=DiagnosticOperation.Status.RUNNING,
    )
    client = Client()
    client.force_login(alice)
    start_query = f'''mutation {{
      startDiagnostic(
        projectId: "{other.pk}",
        agentId: "{foreign_agent.pk}",
        diagnostic: DJANGO_HEALTH
      ) {{ operation {{ id }} error {{ code }} }}
    }}'''
    start = gql(client, start_query)
    assert start.json()["data"]["startDiagnostic"]["error"]["code"] == "NOT_FOUND"
    cancel_query = f'''mutation {{
      cancelDiagnostic(operationId: "{foreign_operation.public_id}") {{
        operation {{ id }} error {{ code }}
      }}
    }}'''
    cancel = gql(client, cancel_query)
    assert cancel.json()["data"]["cancelDiagnostic"]["error"]["code"] == "NOT_FOUND"


def test_agent_state_is_live_and_typed(data: Data) -> None:
    alice, _, project, _, _ = data
    client = Client()
    client.force_login(alice)
    query = f'''{{
      agents(projectId: "{project.pk}") {{
        connected heartbeatAgeSeconds capabilities protocolMajor protocolMinor
      }}
    }}'''
    response = gql(client, query)
    assert response.json()["data"]["agents"][0] == {
        "connected": True,
        "heartbeatAgeSeconds": 3,
        "capabilities": ["django_diagnostics_v1"],
        "protocolMajor": 1,
        "protocolMinor": 0,
    }
    body = client.get("/").content.decode()
    assert "connected" in body
    assert "Heartbeat age: 3s" in body
    assert "Protocol: v1.0" in body
    assert "django_diagnostics_v1" in body
    assert "registered_capability" not in body


def test_offline_is_explicit(data: Data) -> None:
    alice, _, _, _, agent = data
    FakeBackend.connected_agents.clear()
    operation = OperationsService(FakeBackend()).start(
        agent=agent, user=alice, diagnostic="django_health"
    )
    assert operation.status == "offline"
    assert operation.error_code == "AGENT_OFFLINE"


def test_cancel_failure_is_explicit(data: Data) -> None:
    alice, _, project, _, agent = data
    operation = DiagnosticOperation.objects.create(
        project=project,
        agent=agent,
        requested_by=alice,
        status=DiagnosticOperation.Status.RUNNING,
    )
    backend = FakeBackend()
    backend.cancel_error = TimeoutError()
    updated = OperationsService(backend).cancel(operation)
    assert updated.status == "failed"
    assert updated.error_code == "CANCEL_TIMEOUT"


def test_csrf_protects_browser_mutations(data: Data) -> None:
    alice, _, project, _, agent = data
    client = Client(enforce_csrf_checks=True)
    client.force_login(alice)
    start = client.post(f"/projects/{project.pk}/agents/{agent.pk}/diagnostics/start")
    assert start.status_code == 403
    graph = client.post(
        "/graphql/v1",
        data=json.dumps({"query": "{ projects { id } }"}),
        content_type="application/json",
    )
    assert graph.status_code == 403


@override_settings(GRAPHQL_MAX_FIELDS=2)
def test_graphql_complexity_counts_fragments(data: Data) -> None:
    alice, *_ = data
    client = Client()
    client.force_login(alice)
    response = gql(
        client,
        "fragment P on Project { id name slug } query { projects { ...P } }",
    )
    assert response.status_code == 400
    assert response.json()["errors"][0]["message"] == "QUERY_TOO_COMPLEX"


@override_settings(GRAPHQL_INTROSPECTION=False)
def test_production_introspection_is_disabled(data: Data) -> None:
    alice, *_ = data
    client = Client()
    client.force_login(alice)
    assert gql(client, '{ __type(name: "Project") { name } }').status_code == 400


def test_development_introspection_is_available(data: Data) -> None:
    alice, *_ = data
    client = Client()
    client.force_login(alice)
    response = gql(client, '{ __type(name: "Project") { name } }')
    assert response.status_code == 200
    assert response.json()["data"]["__type"]["name"] == "Project"


def test_invalid_graphql_is_bounded_client_error(data: Data) -> None:
    alice, *_ = data
    client = Client()
    client.force_login(alice)
    response = gql(client, "{ projects {")
    assert response.status_code == 400
    assert response.json() == {"errors": [{"message": "invalid GraphQL request"}]}


def test_dashboard_escapes_result_content(data: Data) -> None:
    alice, _, project, _, agent = data
    DiagnosticOperation.objects.create(
        project=project,
        agent=agent,
        requested_by=alice,
        result_summary={"checks": [{"detail": "<script>alert(1)</script>"}]},
    )
    client = Client()
    client.force_login(alice)
    body = client.get("/").content.decode()
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
