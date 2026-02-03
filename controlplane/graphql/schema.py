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
    GraphQLInputField,
    GraphQLInputObjectType,
    GraphQLInt,
    GraphQLList,
    GraphQLNonNull,
    GraphQLObjectType,
    GraphQLSchema,
    GraphQLString,
)

from controlplane.kubernetes import KubernetesService
from controlplane.models import (
    AgentRegistration,
    DiagnosticOperation,
    KubernetesRelease,
    KubernetesTarget,
    Project,
)
from controlplane.services import OperationsService


def _project(user: User, project_id: str) -> Project:
    return Project.objects.filter(members=user).get(pk=int(project_id))


def _operation(user: User, operation_id: str) -> DiagnosticOperation:
    return (
        DiagnosticOperation.objects.select_related("agent", "project")
        .filter(project__members=user)
        .get(public_id=operation_id)
    )


def _target(user: User, target_id: str) -> KubernetesTarget:
    return (
        KubernetesTarget.objects.select_related("agent", "project")
        .filter(project__members=user)
        .get(public_id=target_id)
    )


def _release(user: User, release_id: str) -> KubernetesRelease:
    return (
        KubernetesRelease.objects.select_related("target", "project")
        .filter(project__members=user)
        .get(public_id=release_id)
    )


DiagnosticStatus = GraphQLEnumType(
    "DiagnosticStatus",
    {name.upper(): GraphQLEnumValue(name) for name, _ in DiagnosticOperation.Status.choices},
)
DiagnosticKind = GraphQLEnumType(
    "DiagnosticKind", {"DJANGO_HEALTH": GraphQLEnumValue("django_health")}
)
KubernetesReleaseStatus = GraphQLEnumType(
    "KubernetesReleaseStatus",
    {name.upper(): GraphQLEnumValue(name) for name, _ in KubernetesRelease.Status.choices},
)
MigrationCompatibility = GraphQLEnumType(
    "MigrationCompatibility",
    {
        "SAFE": GraphQLEnumValue("safe"),
        "UNKNOWN": GraphQLEnumValue("unknown"),
    },
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


AgentType = GraphQLObjectType(
    "Agent",
    lambda: {
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
    },
)


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
            GraphQLNonNull(GraphQLList(GraphQLNonNull(DiagnosticCheck))), resolve=_checks
        ),
        "createdAt": GraphQLField(
            GraphQLNonNull(GraphQLString), resolve=lambda obj, info: obj.created_at.isoformat()
        ),
    },
)
KubernetesTargetType = GraphQLObjectType(
    "KubernetesTarget",
    lambda: {
        "id": GraphQLField(GraphQLNonNull(GraphQLID), resolve=lambda obj, info: str(obj.public_id)),
        "name": GraphQLField(GraphQLNonNull(GraphQLString)),
        "apiServer": GraphQLField(
            GraphQLNonNull(GraphQLString), resolve=lambda obj, info: obj.api_server
        ),
        "namespace": GraphQLField(GraphQLNonNull(GraphQLString)),
        "releaseName": GraphQLField(
            GraphQLNonNull(GraphQLString), resolve=lambda obj, info: obj.release_name
        ),
        "ingressClass": GraphQLField(
            GraphQLString, resolve=lambda obj, info: obj.ingress_class or None
        ),
        "tlsSecretName": GraphQLField(
            GraphQLString, resolve=lambda obj, info: obj.tls_secret_name or None
        ),
        "validatedAt": GraphQLField(
            GraphQLString,
            resolve=lambda obj, info: obj.validated_at.isoformat() if obj.validated_at else None,
        ),
    },
)
KubernetesReleaseType = GraphQLObjectType(
    "KubernetesRelease",
    lambda: {
        "id": GraphQLField(GraphQLNonNull(GraphQLID), resolve=lambda obj, info: str(obj.public_id)),
        "status": GraphQLField(
            GraphQLNonNull(KubernetesReleaseStatus), resolve=lambda obj, info: obj.status
        ),
        "image": GraphQLField(GraphQLNonNull(GraphQLString)),
        "chartVersion": GraphQLField(
            GraphQLNonNull(GraphQLString), resolve=lambda obj, info: obj.chart_version
        ),
        "valuesDigest": GraphQLField(
            GraphQLNonNull(GraphQLString), resolve=lambda obj, info: obj.values_digest
        ),
        "helmRevision": GraphQLField(GraphQLInt, resolve=lambda obj, info: obj.helm_revision),
        "previousHelmRevision": GraphQLField(
            GraphQLInt, resolve=lambda obj, info: obj.previous_helm_revision
        ),
        "rollbackStatus": GraphQLField(
            GraphQLString, resolve=lambda obj, info: obj.rollback_status or None
        ),
        "errorCode": GraphQLField(GraphQLString, resolve=lambda obj, info: obj.error_code or None),
        "createdAt": GraphQLField(
            GraphQLNonNull(GraphQLString), resolve=lambda obj, info: obj.created_at.isoformat()
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
DiagnosticMutationPayload = GraphQLObjectType(
    "DiagnosticMutationPayload",
    lambda: {"operation": GraphQLField(OperationType), "error": GraphQLField(ApiError)},
)
KubernetesTargetMutationPayload = GraphQLObjectType(
    "KubernetesTargetMutationPayload",
    lambda: {"target": GraphQLField(KubernetesTargetType), "error": GraphQLField(ApiError)},
)
KubernetesReleaseMutationPayload = GraphQLObjectType(
    "KubernetesReleaseMutationPayload",
    lambda: {"release": GraphQLField(KubernetesReleaseType), "error": GraphQLField(ApiError)},
)
KubernetesReleaseValuesInput = GraphQLInputObjectType(
    "KubernetesReleaseValuesInput",
    lambda: {
        "webReplicas": GraphQLInputField(GraphQLInt),
        "environmentSecretName": GraphQLInputField(GraphQLString),
        "migrationCompatibility": GraphQLInputField(MigrationCompatibility),
        "celeryWorkerEnabled": GraphQLInputField(GraphQLBoolean),
        "celeryBeatEnabled": GraphQLInputField(GraphQLBoolean),
        "ingressHost": GraphQLInputField(GraphQLString),
        "ingressClass": GraphQLInputField(GraphQLString),
        "tlsSecretName": GraphQLInputField(GraphQLString),
    },
)


def _projects(obj: Any, info: Any) -> Any:
    return Project.objects.filter(members=info.context.user)


def _agents(obj: Any, info: Any, projectId: str) -> Any:
    return _project(info.context.user, projectId).agents.all()


def _operations(obj: Any, info: Any, projectId: str, first: int = 20) -> Any:
    project = _project(info.context.user, projectId)
    return project.operations.select_related("agent")[: max(1, min(first, 50))]


def _targets(obj: Any, info: Any, projectId: str) -> Any:
    return _project(info.context.user, projectId).kubernetes_targets.select_related("agent")


def _releases(obj: Any, info: Any, projectId: str, first: int = 20) -> Any:
    project = _project(info.context.user, projectId)
    return project.kubernetes_releases.select_related("target")[: max(1, min(first, 50))]


def _start(obj: Any, info: Any, projectId: str, agentId: str, diagnostic: str) -> dict[str, Any]:
    try:
        project = _project(info.context.user, projectId)
        agent = project.agents.get(pk=int(agentId))
        operation = OperationsService().start(
            agent=agent, user=info.context.user, diagnostic=diagnostic
        )
        return {"operation": operation, "error": None}
    except (Project.DoesNotExist, AgentRegistration.DoesNotExist):
        return {"operation": None, "error": {"code": "NOT_FOUND", "message": "resource not found"}}
    except ValueError:
        return {
            "operation": None,
            "error": {"code": "INVALID_DIAGNOSTIC", "message": "unsupported diagnostic"},
        }


def _cancel(obj: Any, info: Any, operationId: str) -> dict[str, Any]:
    try:
        operation = _operation(info.context.user, operationId)
        return {"operation": OperationsService().cancel(operation), "error": None}
    except DiagnosticOperation.DoesNotExist:
        return {"operation": None, "error": {"code": "NOT_FOUND", "message": "operation not found"}}


def _register_target(obj: Any, info: Any, **args: Any) -> dict[str, Any]:
    try:
        project = _project(info.context.user, args["projectId"])
        agent = project.agents.get(pk=int(args["agentId"]))
        target = KubernetesService().register_target(
            project=project,
            agent=agent,
            name=args["name"],
            api_server=args["apiServer"],
            namespace=args["namespace"],
            release_name=args["releaseName"],
            credential_ref=args["credentialRef"],
            ingress_class=args.get("ingressClass", ""),
            tls_secret_name=args.get("tlsSecretName", ""),
        )
        return {"target": target, "error": None}
    except (Project.DoesNotExist, AgentRegistration.DoesNotExist):
        return {"target": None, "error": {"code": "NOT_FOUND", "message": "resource not found"}}
    except (ValueError, RuntimeError) as exc:
        return {"target": None, "error": {"code": "TARGET_VALIDATION_FAILED", "message": str(exc)}}


def _release_values(raw: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key in ("webReplicas", "environmentSecretName", "migrationCompatibility"):
        if raw.get(key) is not None:
            values[key] = raw[key]
    if raw.get("celeryWorkerEnabled") is not None:
        values["celeryWorker"] = {"enabled": raw["celeryWorkerEnabled"]}
    if raw.get("celeryBeatEnabled") is not None:
        values["celeryBeat"] = {"enabled": raw["celeryBeatEnabled"]}
    if raw.get("ingressHost"):
        values["ingress"] = {
            "enabled": True,
            "host": raw["ingressHost"],
            "className": raw.get("ingressClass", ""),
            "tlsSecretName": raw.get("tlsSecretName", ""),
        }
    return values


def _start_kubernetes_release(
    obj: Any, info: Any, targetId: str, image: str, values: dict[str, Any]
) -> dict[str, Any]:
    try:
        target = _target(info.context.user, targetId)
        release = KubernetesService().start_release(
            target=target,
            user=info.context.user,
            image=image,
            values=_release_values(values),
        )
        return {"release": release, "error": None}
    except KubernetesTarget.DoesNotExist:
        return {"release": None, "error": {"code": "NOT_FOUND", "message": "target not found"}}
    except (ValueError, RuntimeError) as exc:
        return {"release": None, "error": {"code": "RELEASE_FAILED", "message": str(exc)}}


def _cancel_kubernetes_release(obj: Any, info: Any, releaseId: str) -> dict[str, Any]:
    try:
        release = _release(info.context.user, releaseId)
        return {"release": KubernetesService().cancel_release(release), "error": None}
    except KubernetesRelease.DoesNotExist:
        return {"release": None, "error": {"code": "NOT_FOUND", "message": "release not found"}}


Query = GraphQLObjectType(
    "Query",
    lambda: {
        "projects": GraphQLField(
            GraphQLNonNull(GraphQLList(GraphQLNonNull(ProjectType))), resolve=_projects
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
        "kubernetesTargets": GraphQLField(
            GraphQLNonNull(GraphQLList(GraphQLNonNull(KubernetesTargetType))),
            args={"projectId": GraphQLArgument(GraphQLNonNull(GraphQLID))},
            resolve=_targets,
        ),
        "kubernetesReleases": GraphQLField(
            GraphQLNonNull(GraphQLList(GraphQLNonNull(KubernetesReleaseType))),
            args={
                "projectId": GraphQLArgument(GraphQLNonNull(GraphQLID)),
                "first": GraphQLArgument(GraphQLInt),
            },
            resolve=_releases,
        ),
    },
)
Mutation = GraphQLObjectType(
    "Mutation",
    lambda: {
        "startDiagnostic": GraphQLField(
            GraphQLNonNull(DiagnosticMutationPayload),
            args={
                "projectId": GraphQLArgument(GraphQLNonNull(GraphQLID)),
                "agentId": GraphQLArgument(GraphQLNonNull(GraphQLID)),
                "diagnostic": GraphQLArgument(GraphQLNonNull(DiagnosticKind)),
            },
            resolve=_start,
        ),
        "cancelDiagnostic": GraphQLField(
            GraphQLNonNull(DiagnosticMutationPayload),
            args={"operationId": GraphQLArgument(GraphQLNonNull(GraphQLID))},
            resolve=_cancel,
        ),
        "registerKubernetesTarget": GraphQLField(
            GraphQLNonNull(KubernetesTargetMutationPayload),
            args={
                "projectId": GraphQLArgument(GraphQLNonNull(GraphQLID)),
                "agentId": GraphQLArgument(GraphQLNonNull(GraphQLID)),
                "name": GraphQLArgument(GraphQLNonNull(GraphQLString)),
                "apiServer": GraphQLArgument(GraphQLNonNull(GraphQLString)),
                "namespace": GraphQLArgument(GraphQLNonNull(GraphQLString)),
                "releaseName": GraphQLArgument(GraphQLNonNull(GraphQLString)),
                "credentialRef": GraphQLArgument(GraphQLNonNull(GraphQLString)),
                "ingressClass": GraphQLArgument(GraphQLString),
                "tlsSecretName": GraphQLArgument(GraphQLString),
            },
            resolve=_register_target,
        ),
        "startKubernetesRelease": GraphQLField(
            GraphQLNonNull(KubernetesReleaseMutationPayload),
            args={
                "targetId": GraphQLArgument(GraphQLNonNull(GraphQLID)),
                "image": GraphQLArgument(GraphQLNonNull(GraphQLString)),
                "values": GraphQLArgument(GraphQLNonNull(KubernetesReleaseValuesInput)),
            },
            resolve=_start_kubernetes_release,
        ),
        "cancelKubernetesRelease": GraphQLField(
            GraphQLNonNull(KubernetesReleaseMutationPayload),
            args={"releaseId": GraphQLArgument(GraphQLNonNull(GraphQLID))},
            resolve=_cancel_kubernetes_release,
        ),
    },
)
schema = GraphQLSchema(query=Query, mutation=Mutation)
