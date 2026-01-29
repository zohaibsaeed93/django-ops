from __future__ import annotations

from typing import Any

from django.contrib.auth.models import User
from graphql import (
    GraphQLArgument,
    GraphQLBoolean,
    GraphQLEnumType,
    GraphQLEnumValue,
    GraphQLField,
    GraphQLID,
    GraphQLInt,
    GraphQLList,
    GraphQLNonNull,
    GraphQLObjectType,
    GraphQLSchema,
    GraphQLString,
)

from controlplane.models import AgentRegistration, DiagnosticOperation, Project
from controlplane.services import OperationsService


def _project(user: User, project_id: str) -> Project:
    return Project.objects.filter(members=user).get(pk=int(project_id))


def _operation(user: User, operation_id: str) -> DiagnosticOperation:
    return (
        DiagnosticOperation.objects.select_related("agent", "project")
        .filter(project__members=user)
        .get(public_id=operation_id)
    )


DiagnosticStatus = GraphQLEnumType(
    "DiagnosticStatus",
    {name.upper(): GraphQLEnumValue(name) for name, _ in DiagnosticOperation.Status.choices},
)
DiagnosticKind = GraphQLEnumType(
    "DiagnosticKind", {"DJANGO_HEALTH": GraphQLEnumValue("django_health")}
)
DiagnosticCheck = GraphQLObjectType(
    "DiagnosticCheck",
    lambda: {
        "name": GraphQLField(GraphQLNonNull(GraphQLString)),
        "status": GraphQLField(GraphQLNonNull(GraphQLString)),
        "detail": GraphQLField(GraphQLNonNull(GraphQLString)),
    },
)
ProjectType = GraphQLObjectType(
    "Project",
    lambda: {
        "id": GraphQLField(GraphQLNonNull(GraphQLID), resolve=lambda obj, info: str(obj.pk)),
        "slug": GraphQLField(GraphQLNonNull(GraphQLString)),
        "name": GraphQLField(GraphQLNonNull(GraphQLString)),
    },
)


def _agent_state_field(obj: AgentRegistration, key: str) -> Any:
    return OperationsService().agent_state(obj)[key]  # type: ignore[literal-required]


def _agent_fields() -> dict[str, GraphQLField]:
    return {
        "id": GraphQLField(GraphQLNonNull(GraphQLID), resolve=lambda obj, info: str(obj.pk)),
        "agentId": GraphQLField(
            GraphQLNonNull(GraphQLString), resolve=lambda obj, info: obj.agent_id
        ),
        "capabilities": GraphQLField(
            GraphQLNonNull(GraphQLList(GraphQLNonNull(GraphQLString))),
            resolve=lambda obj, info: _agent_state_field(obj, "capabilities"),
        ),
        "connected": GraphQLField(
            GraphQLNonNull(GraphQLBoolean),
            resolve=lambda obj, info: _agent_state_field(obj, "connected"),
        ),
        "heartbeatAgeSeconds": GraphQLField(
            GraphQLInt,
            resolve=lambda obj, info: _agent_state_field(obj, "heartbeat_age"),
        ),
        "protocolMajor": GraphQLField(
            GraphQLInt,
            resolve=lambda obj, info: _agent_state_field(obj, "protocol_major"),
        ),
        "protocolMinor": GraphQLField(
            GraphQLInt,
            resolve=lambda obj, info: _agent_state_field(obj, "protocol_minor"),
        ),
    }


AgentType = GraphQLObjectType("Agent", _agent_fields)


def _checks(obj: DiagnosticOperation, info: Any) -> list[dict[str, str]]:
    value = obj.result_summary.get("checks", [])
    return value if isinstance(value, list) else []


OperationType = GraphQLObjectType(
    "DiagnosticOperation",
    lambda: {
        "id": GraphQLField(GraphQLNonNull(GraphQLID), resolve=lambda obj, info: str(obj.public_id)),
        "diagnostic": GraphQLField(
            GraphQLNonNull(DiagnosticKind), resolve=lambda obj, info: obj.diagnostic
        ),
        "status": GraphQLField(
            GraphQLNonNull(DiagnosticStatus), resolve=lambda obj, info: obj.status
        ),
        "errorCode": GraphQLField(GraphQLString, resolve=lambda obj, info: obj.error_code or None),
        "checks": GraphQLField(
            GraphQLNonNull(GraphQLList(GraphQLNonNull(DiagnosticCheck))),
            resolve=_checks,
        ),
        "createdAt": GraphQLField(
            GraphQLNonNull(GraphQLString),
            resolve=lambda obj, info: obj.created_at.isoformat(),
        ),
    },
)
ApiError = GraphQLObjectType(
    "ApiError",
    lambda: {
        "code": GraphQLField(GraphQLNonNull(GraphQLString)),
        "message": GraphQLField(GraphQLNonNull(GraphQLString)),
    },
)
MutationPayload = GraphQLObjectType(
    "DiagnosticMutationPayload",
    lambda: {
        "operation": GraphQLField(OperationType),
        "error": GraphQLField(ApiError),
    },
)


def _projects(obj: Any, info: Any) -> Any:
    return Project.objects.filter(members=info.context.user)


def _agents(obj: Any, info: Any, projectId: str) -> Any:
    return _project(info.context.user, projectId).agents.all()


def _operations(obj: Any, info: Any, projectId: str, first: int = 20) -> Any:
    project = _project(info.context.user, projectId)
    return project.operations.select_related("agent")[: max(1, min(first, 50))]


def _start(obj: Any, info: Any, projectId: str, agentId: str, diagnostic: str) -> dict[str, Any]:
    try:
        project = _project(info.context.user, projectId)
        agent = project.agents.get(pk=int(agentId))
        operation = OperationsService().start(
            agent=agent, user=info.context.user, diagnostic=diagnostic
        )
        return {"operation": operation, "error": None}
    except Project.DoesNotExist:
        return {
            "operation": None,
            "error": {"code": "NOT_FOUND", "message": "project not found"},
        }
    except AgentRegistration.DoesNotExist:
        return {
            "operation": None,
            "error": {"code": "NOT_FOUND", "message": "agent not found"},
        }
    except ValueError:
        return {
            "operation": None,
            "error": {
                "code": "INVALID_DIAGNOSTIC",
                "message": "unsupported diagnostic",
            },
        }


def _cancel(obj: Any, info: Any, operationId: str) -> dict[str, Any]:
    try:
        operation = _operation(info.context.user, operationId)
        return {"operation": OperationsService().cancel(operation), "error": None}
    except DiagnosticOperation.DoesNotExist:
        return {
            "operation": None,
            "error": {"code": "NOT_FOUND", "message": "operation not found"},
        }


Query = GraphQLObjectType(
    "Query",
    lambda: {
        "projects": GraphQLField(
            GraphQLNonNull(GraphQLList(GraphQLNonNull(ProjectType))),
            resolve=_projects,
        ),
        "agents": GraphQLField(
            GraphQLNonNull(GraphQLList(GraphQLNonNull(AgentType))),
            args={"projectId": GraphQLArgument(GraphQLNonNull(GraphQLID))},
            resolve=_agents,
        ),
        "diagnosticOperations": GraphQLField(
            GraphQLNonNull(GraphQLList(GraphQLNonNull(OperationType))),
            args={
                "projectId": GraphQLArgument(GraphQLNonNull(GraphQLID)),
                "first": GraphQLArgument(GraphQLInt),
            },
            resolve=_operations,
        ),
        "diagnosticOperation": GraphQLField(
            OperationType,
            args={"id": GraphQLArgument(GraphQLNonNull(GraphQLID))},
            resolve=lambda obj, info, id: _operation(info.context.user, id),
        ),
    },
)
Mutation = GraphQLObjectType(
    "Mutation",
    lambda: {
        "startDiagnostic": GraphQLField(
            GraphQLNonNull(MutationPayload),
            args={
                "projectId": GraphQLArgument(GraphQLNonNull(GraphQLID)),
                "agentId": GraphQLArgument(GraphQLNonNull(GraphQLID)),
                "diagnostic": GraphQLArgument(GraphQLNonNull(DiagnosticKind)),
            },
            resolve=_start,
        ),
        "cancelDiagnostic": GraphQLField(
            GraphQLNonNull(MutationPayload),
            args={"operationId": GraphQLArgument(GraphQLNonNull(GraphQLID))},
            resolve=_cancel,
        ),
    },
)
schema = GraphQLSchema(query=Query, mutation=Mutation)
