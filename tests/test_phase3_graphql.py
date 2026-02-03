from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User
from graphql import graphql_sync

from controlplane.graphql.schema import schema
from controlplane.models import AgentRegistration, KubernetesTarget, Project


@pytest.mark.django_db
def test_kubernetes_targets_are_project_scoped_and_do_not_expose_credentials() -> None:
    owner = User.objects.create_user("owner")
    outsider = User.objects.create_user("outsider")
    project = Project.objects.create(slug="scope", name="Scoped")
    project.members.add(owner)
    agent = AgentRegistration.objects.create(project=project, agent_id="scope-agent")
    KubernetesTarget.objects.create(
        project=project,
        agent=agent,
        name="prod",
        api_server="https://cluster.example",
        namespace="scope",
        release_name="scope-web",
        credential_ref="file:super-secret-ref",
    )
    query = "query($id: ID!){ kubernetesTargets(projectId:$id){ name namespace releaseName } }"
    owner_result = graphql_sync(
        schema,
        query,
        variable_values={"id": str(project.pk)},
        context_value=SimpleNamespace(user=owner),
    )
    assert owner_result.errors is None
    assert owner_result.data == {
        "kubernetesTargets": [{"name": "prod", "namespace": "scope", "releaseName": "scope-web"}]
    }
    outsider_result = graphql_sync(
        schema,
        query,
        variable_values={"id": str(project.pk)},
        context_value=SimpleNamespace(user=outsider),
    )
    assert outsider_result.errors


def test_kubernetes_target_schema_has_no_credential_field() -> None:
    fields = schema.get_type("KubernetesTarget").fields  # type: ignore[union-attr]
    assert "credentialRef" not in fields
    assert "kubeconfig" not in fields
