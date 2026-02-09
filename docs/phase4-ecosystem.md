# Phase 4 ecosystem contract

DjangoOps Phase 4 adds a deliberately small ecosystem surface for Django operations. It is not a generic plugin runner or marketplace. Integrations are compiled into DjangoOps, explicitly registered, versioned, capability-limited, project-scoped where exposed by the control plane, and data-only from the operator's perspective.

## Contract and versioning

The first ecosystem contract is `v1`. Each built-in integration declares a stable name, accepted contract versions, capability allowlist, and exact non-secret configuration keys. Configuration selects only those built-ins. Unknown names, unknown versions, duplicate registrations/configuration, unknown keys, unsupported capabilities, malformed values, and schema drift fail closed with explicit error codes.

The contract is intentionally separate from `djangoops.yaml`'s core schema version. Existing schema-v3 files with no `integrations` section remain compatible. Projects may add an optional top-level `integrations` list; core Phase 0 configuration loading validates and separates that section before delegating to the established schema-v3 parser.

A configured entry has exactly these fields:

```yaml
integrations:
  - name: django-runtime
    version: v1
    enabled: true
    capabilities: [metadata, health]
    config:
      health_path: /healthz
      metadata_label: django
```

No import path, package name, repository URL, image, command, manifest, Python callback, Go callback, GraphQL document, or arbitrary Kubernetes object can be supplied through this contract.

## Reference integrations

### `django-runtime` v1

Capabilities: `metadata`, `health`.

When explicitly enabled in `djangoops.yaml`, Compose generation installs a bounded container health check on the Django web workload using the validated `health_path`. The check is an HTTP GET from inside the existing web container with a two-second request timeout; it does not add shell or remote execution authority. Setting `enabled: false` removes this ecosystem-provided health check. `metadata_label` remains bounded metadata available through evaluation.

Projects with no ecosystem section retain the pre-ecosystem Compose behavior, so adoption does not silently change an existing project's health semantics.

### `otlp-export` v1

Capabilities: `telemetry`, `deployment_config`.

When explicitly enabled in `djangoops.yaml`, Compose generation enables the existing OpenTelemetry workload path for web and Celery services, sets `OTEL_EXPORTER_OTLP_ENDPOINT` to the validated credential-free origin, and sets the configured `service.namespace` in `OTEL_RESOURCE_ATTRIBUTES`. Setting `enabled: false` disables the SDK and clears the OTLP endpoint for that generated Compose workload. The endpoint cannot contain username/password, query, or fragment data; authentication material remains runtime-provisioned outside project configuration.

Projects with no ecosystem section preserve the earlier environment-driven `OTEL_SDK_DISABLED` and `OTEL_EXPORTER_OTLP_ENDPOINT` behavior. Phase 3 Helm release observability remains controlled by the existing bounded Helm release values; ecosystem v1 does not widen the Kubernetes release API or synthesize arbitrary Helm values. This keeps the integration contract truthful about the concrete Compose workload path it controls while preserving the established Phase 3 authority boundary.

Each integration is independently enabled with its `enabled` boolean. Disabled integrations remain discoverable but evaluate to `disabled` and perform no adapter work.

## Authenticated product surface

The ecosystem is reachable through the existing Django control plane at `GET /projects/<project_id>/ecosystem` and `POST /projects/<project_id>/ecosystem`. Both operations require an authenticated session and resolve the requested project through server-side membership before returning any project-scoped result.

`GET` returns only the deterministic built-in catalog. `POST` accepts the same bounded, data-only integration list used by `djangoops.yaml`, validates it through the static registry, and returns bounded evaluation results. It does not persist arbitrary user code or mutate deploy, migration, rollback, backup, restore, diagnostic, or Kubernetes release state. Unknown projects and projects outside the authenticated user's membership are not exposed.

The control-plane surface is deliberately evaluation/discovery only. Workload behavior continues to come from operator-controlled non-secret project configuration and existing bounded deployment renderers rather than accepting runtime code, manifests, shell commands, or remote package references from HTTP clients.

## Registration and discovery

`djangoops.integrations.BUILTIN_REGISTRY` is deterministic and static. Definitions are sorted by name for discovery. Duplicate code registrations fail during registry construction. There is no filesystem scanning, Python entry-point loading, environment-driven package import, package installation, URL fetch, image pull, or repository checkout based on user input.

Both the authenticated product surface and helper layer resolve projects through `Project.objects.filter(members=user)`. Ecosystem discovery therefore cannot widen Phase 2 project authorization or Phase 1 agent trust.

## Capability model

The v1 capability vocabulary is intentionally closed:

- `metadata` — bounded Django/runtime metadata;
- `health` — bounded Django health configuration consumed by the existing Compose web workload path;
- `telemetry` — validated non-secret observability metadata;
- `deployment_config` — validated deployment-adjacent metadata consumed only by existing DjangoOps-controlled Compose observability wiring.

Requesting any capability not declared by the selected built-in integration fails closed. Capabilities do not imply arbitrary execution authority.

## Secrets and redaction

`djangoops.yaml` remains non-secret. Integration keys containing secret/token/password/authorization/API-key/access-key/credential semantics are rejected. OTLP URLs containing userinfo, query strings, or fragments are rejected so credentials cannot be smuggled into a nominally non-secret endpoint. Context and result maps apply the same secret-name checks and bounded field/value sizes.

Runtime credentials continue to use the existing environment/secret mechanisms. Integrations never serialize tokens, secret-bearing headers, database passwords, Django `SECRET_KEY`, SSH keys, S3 credentials, TLS private keys, or Kubernetes credentials into config, generated output, errors, logs, or telemetry summaries.

## Resource bounds and failure isolation

The ecosystem contract allows at most eight configured integrations, sixteen configuration/context/result fields, bounded field/value lengths, and a maximum 1000 ms adapter budget. There are no retry queues. Adapter evaluation is one-shot and side-band. Built-in adapters are local deterministic code and do not perform remote package installation or arbitrary network/shell execution.

An optional integration error or timeout raises a bounded `IntegrationError`; it does not mutate diagnostic, deploy, migration, rollback, backup, restore, Kubernetes release, cancellation, idempotency, or telemetry terminal state. The authenticated evaluation endpoint converts those errors into bounded error codes rather than leaking exception details.

## Compatibility and rollback

A project with no `integrations` section follows the previous core configuration and Compose observability path. To roll back an integration, set `enabled: false` or remove its registration from `djangoops.yaml`; no destructive database migration is required. Removing the entire ecosystem feature leaves Phase 0 direct SSH, Phase 1 outbound-only agent transport, Phase 2 authenticated dashboard/GraphQL, Phase 3 Kubernetes/Helm orchestration, and Phase 4 observability paths intact.

Contract-breaking integration changes require a new explicitly allowlisted contract version. Existing versions are never silently reinterpreted.

## Explicit non-goals

DjangoOps ecosystem support is not a marketplace, generic PaaS, remote shell, generic container platform, package manager, arbitrary plugin loader, generic Kubernetes control plane, Terraform runner, CI runner, or user-script host. The ecosystem exists only to extend Django-specific operations through audited built-in capabilities while preserving existing security and lifecycle semantics.
