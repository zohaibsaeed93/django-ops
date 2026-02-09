"""Compatibility layer for optional ecosystem configuration in djangoops.yaml."""

from __future__ import annotations

from typing import Any

import yaml

from djangoops.config import DjangoOpsConfig, parse_config
from djangoops.integrations import IntegrationConfig, parse_integrations


def parse_project_config(text: str) -> tuple[DjangoOpsConfig, tuple[IntegrationConfig, ...]]:
    """Parse core project config plus an optional data-only ``integrations`` section.

    The core schema remains schema_version 3 for backwards compatibility. The optional
    ecosystem section has its own explicit v1 contract and is removed before delegating
    to the established core parser, so Phase 0 behavior is unchanged when no integrations
    are configured.
    """
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError("invalid YAML configuration") from exc
    if not isinstance(loaded, dict) or not all(isinstance(key, str) for key in loaded):
        raise ValueError("configuration root must be a mapping")

    integrations = parse_integrations(loaded.get("integrations"))
    core: dict[str, Any] = dict(loaded)
    core.pop("integrations", None)
    core_text = yaml.safe_dump(core, sort_keys=False, default_flow_style=False)
    return parse_config(core_text), integrations


def render_project_config(
    config: DjangoOpsConfig, integrations: tuple[IntegrationConfig, ...]
) -> str:
    """Render a deterministic non-secret config with optional ecosystem registrations."""
    mapping = config.to_mapping()
    if integrations:
        mapping["integrations"] = [integration.to_mapping() for integration in integrations]
    return yaml.safe_dump(mapping, sort_keys=False, default_flow_style=False)
