from __future__ import annotations

import importlib
from collections import deque
from collections.abc import Callable
from typing import Any, TypedDict, cast

from django.contrib.auth.models import User
from django.db.models.signals import post_save
from graphql import (
    GraphQLArgument,
    GraphQLBoolean,
    GraphQLField,
    GraphQLID,
    GraphQLInt,
    GraphQLNonNull,
    GraphQLObjectType,
    GraphQLString,
)

from controlplane.models import DiagnosticOperation, KubernetesRelease, Project
from controlplane.services import OperationsService
from controlplane.telemetry import telemetry, validate_otlp_endpoint

_MAX_SEEN = 2048
_seen: set[str] = set()
_seen_order: deque[str] = deque()
_installed = False
_graphql_installed = False


class ObservabilitySummary(TypedDict):
    total_agents: int
    connected_agents: int
    recent_operations: int
    recent_releases: int
    recent_failures: int
    otlp_configured: bool
    telemetry_status: str


def _remember(key: str) -> bool:
    if key in _seen:
        return False
    if len(_seen_order) >= _MAX_SEEN:
        _seen.discard(_seen_order.popleft())
    _seen.add(key)
    _seen_order.append(key)
    return True


def install_signals() -> None:
    global _installed
    if _installed:
        return
    post_save.connect(_diagnostic_saved, sender=DiagnosticOperation, weak=False)
    post_save.connect(_release_saved, sender=KubernetesRelease, weak=False)
    _installed = True


def _diagnostic_saved(
    sender: type[DiagnosticOperation],
    instance: DiagnosticOperation,
    **kwargs: Any,
) -> None:
    terminal = {
        "succeeded",
        "failed",
        "cancelled",
        "offline",
        "overloaded",
        "disconnected",
        "timeout",
    }
    seen_key = f"diagnostic:{instance.public_id}:{instance.status}"
    if instance.status not in terminal or not _remember(seen_key):
        return
    seconds = (instance.updated_at - instance.created_at).total_seconds()
    duration = int(max(0.0, seconds) * 1000)
    telemetry.observe(
        component="controlplane",
        kind="diagnostic",
        outcome=instance.status,
        duration_ms=duration,
        correlation=str(instance.public_id),
        project_id=instance.project_id,
    )


def _release_saved(
    sender: type[KubernetesRelease],
    instance: KubernetesRelease,
    **kwargs: Any,
) -> None:
    terminal = {"succeeded", "failed", "cancelled", "rolled_back", "rollback_blocked"}
    seen_key = f"release:{instance.operation_id}:{instance.status}"
    if instance.status not in terminal or not _remember(seen_key):
        return
    seconds = (instance.updated_at - instance.created_at).total_seconds()
    duration = int(max(0.0, seconds) * 1000)
    telemetry.observe(
        component="controlplane",
        kind="kubernetes_release",
        outcome=instance.status,
        duration_ms=duration,
        correlation=instance.operation_id,
        project_id=instance.project_id,
    )


def summary_for_project(user: User, project_id: int) -> ObservabilitySummary:
    project = Project.objects.filter(members=user).get(pk=project_id)
    service = OperationsService()
    agents = list(project.agents.all())
    connected = sum(1 for agent in agents if service.agent_state(agent)["connected"])
    operations = list(project.operations.all()[:50])
    releases = list(project.kubernetes_releases.all()[:50])
    failed_statuses = {
        "failed",
        "offline",
        "overloaded",
        "disconnected",
        "timeout",
        "rollback_blocked",
    }
    failures = sum(operation.status in failed_statuses for operation in operations)
    failures += sum(release.status in failed_statuses for release in releases)
    return {
        "total_agents": len(agents),
        "connected_agents": connected,
        "recent_operations": len(operations),
        "recent_releases": len(releases),
        "recent_failures": failures,
        "otlp_configured": bool(telemetry.endpoint),
        "telemetry_status": "configured" if telemetry.endpoint else "disabled",
    }


def prometheus_extra() -> dict[str, int]:
    registered = 0
    connected = 0
    service = OperationsService()
    for project in Project.objects.all().prefetch_related("agents"):
        for agent in project.agents.all():
            registered += 1
            if service.agent_state(agent)["connected"]:
                connected += 1
    return {
        "djangoops_agents_registered": registered,
        "djangoops_agents_connected": connected,
        "djangoops_diagnostics_inflight": DiagnosticOperation.objects.filter(
            status__in=("pending", "running")
        ).count(),
        "djangoops_kubernetes_releases_inflight": KubernetesRelease.objects.filter(
            status__in=("pending", "validating", "migrating", "applying", "verifying")
        ).count(),
    }


SummaryType = GraphQLObjectType(
    "ObservabilitySummary",
    lambda: {
        "totalAgents": GraphQLField(
            GraphQLNonNull(GraphQLInt), resolve=lambda obj, info: obj["total_agents"]
        ),
        "connectedAgents": GraphQLField(
            GraphQLNonNull(GraphQLInt), resolve=lambda obj, info: obj["connected_agents"]
        ),
        "recentOperations": GraphQLField(
            GraphQLNonNull(GraphQLInt), resolve=lambda obj, info: obj["recent_operations"]
        ),
        "recentReleases": GraphQLField(
            GraphQLNonNull(GraphQLInt), resolve=lambda obj, info: obj["recent_releases"]
        ),
        "recentFailures": GraphQLField(
            GraphQLNonNull(GraphQLInt), resolve=lambda obj, info: obj["recent_failures"]
        ),
        "otlpConfigured": GraphQLField(
            GraphQLNonNull(GraphQLBoolean), resolve=lambda obj, info: obj["otlp_configured"]
        ),
        "telemetryStatus": GraphQLField(
            GraphQLNonNull(GraphQLString), resolve=lambda obj, info: obj["telemetry_status"]
        ),
    },
)


def install_graphql_field() -> None:
    global _graphql_installed
    if _graphql_installed:
        return
    schema_module = cast(Any, importlib.import_module("controlplane.graphql.schema"))

    if "observabilitySummary" not in schema_module.Query.fields:
        schema_module.Query.fields["observabilitySummary"] = GraphQLField(
            GraphQLNonNull(SummaryType),
            args={"projectId": GraphQLArgument(GraphQLNonNull(GraphQLID))},
            resolve=lambda obj, info, projectId: summary_for_project(
                cast(User, info.context.user), int(projectId)
            ),
        )

    release_input = schema_module.KubernetesReleaseValuesInput.fields
    release_input.setdefault(
        "observabilityEnabled", schema_module.GraphQLInputField(GraphQLBoolean)
    )
    release_input.setdefault("otlpEndpoint", schema_module.GraphQLInputField(GraphQLString))
    release_input.setdefault("otelServiceNamespace", schema_module.GraphQLInputField(GraphQLString))
    original = cast(Callable[[dict[str, Any]], dict[str, Any]], schema_module._release_values)

    def release_values(raw: dict[str, Any]) -> dict[str, Any]:
        values = original(raw)
        if raw.get("observabilityEnabled"):
            endpoint = validate_otlp_endpoint(str(raw.get("otlpEndpoint", "")))
            namespace = str(raw.get("otelServiceNamespace", "djangoops"))[:63]
            values["observability"] = {
                "enabled": True,
                "otlpEndpoint": endpoint,
                "serviceNamespace": namespace,
            }
        return values

    schema_module._release_values = release_values
    _graphql_installed = True
