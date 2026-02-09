"""Project-scoped control-plane view of the bounded DjangoOps ecosystem."""

from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth.models import User
from djangoops.integrations import (
    BUILTIN_REGISTRY,
    IntegrationConfig,
    IntegrationResult,
    evaluate_integration,
)

from controlplane.models import Project


@dataclass(frozen=True, slots=True)
class EcosystemIntegrationStatus:
    name: str
    versions: tuple[str, ...]
    capabilities: tuple[str, ...]


def project_catalog(user: User, project_id: int) -> tuple[EcosystemIntegrationStatus, ...]:
    """Return only the built-in integration catalog after server-side membership auth."""
    Project.objects.filter(members=user).get(pk=project_id)
    return tuple(
        EcosystemIntegrationStatus(
            name=definition.name,
            versions=definition.versions,
            capabilities=tuple(capability.value for capability in definition.capabilities),
        )
        for definition in BUILTIN_REGISTRY.definitions()
    )


def evaluate_project_integrations(
    user: User,
    project_id: int,
    integrations: tuple[IntegrationConfig, ...],
) -> tuple[IntegrationResult, ...]:
    """Evaluate enabled project integrations without changing core operation state."""
    project = Project.objects.filter(members=user).get(pk=project_id)
    return tuple(
        evaluate_integration(integration, context={"project": project.slug})
        for integration in integrations
    )
