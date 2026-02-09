# DjangoOps

DjangoOps is an opinionated operations platform for production Django applications. It preserves Django-specific deployment, migration, backup, recovery, diagnostics, telemetry, and runtime semantics instead of exposing a generic remote-shell, container-platform, or PaaS surface.

## Product status

Phases 0 through 4 are implemented in the repository:

- **Phase 0 — single-VPS control path and recovery fallback.** Python CLI, deterministic Docker Compose generation, direct OpenSSH deploy/rollback, Django-aware health, scheduled backups, explicit restore, TLS and external S3-compatible storage.
- **Phase 1 — secure persistent execution channel.** Outbound-only authenticated Go agent over TLS/gRPC with versioned protobufs, allowlisted Django diagnostics, cancellation, reconnect handling, resource bounds, and no inbound agent listener.
- **Phase 2 — authenticated operations control plane.** Django dashboard and project-scoped typed GraphQL API whose authorization starts from server-side project membership and whose operations remain allowlisted.
- **Phase 3 — Kubernetes/K3s deployment path.** Existing-cluster registration, namespace-scoped least privilege, DjangoOps-owned Helm releases, migration gates, cancellation, durable idempotency, rollback compatibility checks, and real Kind acceptance coverage.
- **Phase 4 — observability and bounded ecosystem.** Django-native correlation, authenticated low-cardinality Prometheus metrics, optional bounded OTLP export, plus a versioned static ecosystem contract with two built-in reference integrations and no dynamic third-party code loading.

The direct-SSH Phase 0 path remains the recovery fallback even when later control-plane components are unavailable. Later phases add capability without removing earlier safety boundaries.

Detailed phase documentation is indexed in [`docs/README.md`](docs/README.md). In particular: [`docs/phase1-agent-channel.md`](docs/phase1-agent-channel.md), [`docs/phase2-dashboard-graphql.md`](docs/phase2-dashboard-graphql.md), [`docs/phase3-kubernetes.md`](docs/phase3-kubernetes.md), [`docs/phase4-observability.md`](docs/phase4-observability.md), and [`docs/phase4-ecosystem.md`](docs/phase4-ecosystem.md).

## Initialize a project

After `uv sync --group dev`, create the non-secret project configuration:

```bash
uv run djangoops init \
  --django-module config \
  --hostname app.example.com \
  --acme-email ops@example.com \
  --storage-endpoint-url https://s3.example-provider.com \
  --storage-region us-east-1 \
  --static-bucket my-app-static \
  --media-bucket my-app-media \
  --backup-bucket my-app-backups
```

Use `--project-name my-app` when the directory name should not be the project name. `djangoops.yaml` is non-secret: it contains project/TLS/storage/backup policy and may contain bounded integration metadata, but never runtime credentials, tokens, secret-bearing URLs, database passwords, Django `SECRET_KEY`, SSH keys, TLS private keys, or Kubernetes credentials.

Schema-v2 projects still upgrade explicitly to schema v3 so DjangoOps never guesses a backup destination:

```bash
uv run djangoops config-upgrade \
  --backup-bucket my-app-backups \
  --backup-schedule '17 2 * * *'
```

The optional Phase 4 `integrations` section has its own `v1` contract and does not change the core schema version. Existing schema-v3 files with no integrations remain compatible. See [`docs/phase4-ecosystem.md`](docs/phase4-ecosystem.md).

## Phase 0: Compose, VPS prerequisites, deploy, health, backup, restore

Generate the deterministic single-VPS stack:

```bash
uv run djangoops compose
```

The stack contains Traefik plus enabled Django/PostgreSQL/Redis/Celery services. Only Traefik publishes host ports 80/443. Django 8000, PostgreSQL, and Redis remain internal. Traefik redirects HTTP to HTTPS and uses ACME for certificates. The Docker socket mount is read-only.

Before deployment, operators must provision:

- public DNS for the configured hostname and inbound TCP 80/443 for traffic and ACME HTTP-01;
- Docker + Compose, OpenSSH, cron, and an S3-compatible AWS CLI on the VPS;
- trusted SSH host keys in normal `known_hosts`;
- `/srv/djangoops/<project>/shared/.env` with runtime secrets and credentials;
- external static/media/backup buckets and least-privilege S3-compatible credentials.

DjangoOps does not install or proxy production object storage. Secret material stays in runtime environment/secret systems and is not copied into project YAML.

Deploy over direct SSH:

```bash
uv run djangoops deploy \
  --host app.example.com \
  --user deploy \
  --remote-base /srv/djangoops/my-app
```

Use `--port` and `--identity-file` as needed. OpenSSH keeps normal host-key verification. Deployments use staged releases, run Django migration planning and migrations, and atomically activate only after the migration gate succeeds. Recovery restores the previous application release where compatible and never performs guessed reverse migrations, `docker compose down -v`, volume pruning, database drop/recreate, certificate deletion, or object-store deletion.

Run read-only Django-aware health checks:

```bash
uv run djangoops health \
  --host app.example.com \
  --user deploy \
  --remote-base /srv/djangoops/my-app \
  --json
```

Health verifies the active release, declared versus running services, `manage.py check --deploy`, database connectivity, and pending migrations. Exit 0 means healthy, 1 means diagnostics ran and found a failure, and 2 means invalid input/transport/evaluation failure. Runtime command output and secrets are not serialized into the report.

Install or replace the project-owned backup schedule:

```bash
uv run djangoops backup-schedule --host app.example.com --user deploy --remote-base /srv/djangoops/my-app
```

Run/list/restore:

```bash
uv run djangoops backup-run --host app.example.com --user deploy --remote-base /srv/djangoops/my-app
uv run djangoops backup-list --host app.example.com --user deploy --remote-base /srv/djangoops/my-app
uv run djangoops backup-restore b20260120T044000Z \
  --host app.example.com --user deploy --remote-base /srv/djangoops/my-app
```

Restore always requires an explicit backup identifier. It performs preflight before stopping writers, preserves operator-recoverable pre-restore state, restores matching PostgreSQL/media artifacts, restarts configured writers, and runs Django deployment checks. It never silently chooses `latest` and never claims success after a partial restore failure.

## Phase 1: outbound-only agent trust model

The persistent agent requires separately provisioned runtime authentication plus non-secret endpoint/project settings:

```text
DJANGOOPS_CONTROLPLANE_ENDPOINT=control.example.com:8443
DJANGOOPS_CONTROLPLANE_SERVER_NAME=control.example.com
DJANGOOPS_CONTROLPLANE_CA_FILE=/etc/djangoops/controlplane-ca.pem
DJANGOOPS_AGENT_ID=prod-app-01
DJANGOOPS_AGENT_AUTH_TOKEN=<runtime secret>
DJANGOOPS_PROJECT_ROOT=/srv/djangoops/my-app
DJANGOOPS_COMPOSE_FILE=ops/compose.prod.yml
```

The agent opens no listening socket. TLS chain/hostname verification is mandatory with no insecure fallback. Commands are not accepted from arbitrary user strings: the protocol exposes versioned, allowlisted DjangoOps operations under the validated project root. Queues, admitted work, retained results, and concurrency are bounded. Disconnect/cancellation/reconnect behavior is explicit and stale completed work is not replayed.

If the agent/control plane is unavailable, operators still have the Phase 0 direct-SSH recovery path above.

## Phase 2: dashboard and GraphQL control API

Bootstrap the Django control-plane database and operator account:

```bash
export DJANGOOPS_WEB_SECRET_KEY='<runtime secret>'
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run djangoops-controlplane
```

The web listener defaults to loopback; public deployments should terminate TLS in front of it. Production startup fails closed without the web secret. Session/CSRF cookie protections remain enabled and production GraphQL introspection is disabled.

Dashboard and `POST /graphql/v1` authorization begins with server-side project membership for each project/agent/operation/target/release access. The API is typed and bounded; it does not expose raw GraphQL passthrough to integrations, arbitrary shell, arbitrary containers, or generic Kubernetes manifests.

## Phase 3: Kubernetes/K3s and Helm

DjangoOps can register an existing Kubernetes/K3s cluster target and deploy through a DjangoOps-owned Helm chart. The agent remains outbound-only and receives only bounded Kubernetes lifecycle commands. Credentials are referenced as operator-provisioned runtime material; they are not serialized into GraphQL state or release values.

The Phase 3 lifecycle keeps namespace-scoped least privilege, release idempotency, migration-before-activation safety, cancellation visibility, rollback compatibility checks, and terminal-state monotonicity. Optional integrations cannot bypass or mutate these semantics. See [`docs/phase3-kubernetes.md`](docs/phase3-kubernetes.md) for target registration, Helm values, migration/rollback behavior, and recovery.

## Phase 4: observability

DjangoOps correlates control-plane and workload operations with bounded identifiers while preserving secret redaction and low-cardinality labels. Prometheus exposition is authenticated. OTLP export is optional, non-blocking, bounded, and configured with non-secret endpoint metadata; credentials remain runtime material. Backpressure/drop accounting is bounded and observability failures do not change deploy/diagnostic terminal state.

See [`docs/phase4-observability.md`](docs/phase4-observability.md).

## Phase 4: ecosystem contract

The ecosystem surface is an allowlisted data contract, not arbitrary code execution. Built-ins are statically registered in DjangoOps and currently prove the contract with:

- `django-runtime` `v1` — Django runtime metadata and bounded health-path capability;
- `otlp-export` `v1` — validated secretless OTLP endpoint/service-namespace metadata around the existing observability path.

Configuration selects exact name/version/capabilities and can independently enable or disable each integration. Unknown names/versions/capabilities, duplicate registrations, unknown config keys, secret-bearing URLs/fields, over-limit configuration, and schema drift fail closed. There is no user-driven import, Python entry point, package/repository installation, image pull, shell command, manifest, raw GraphQL, or unrestricted Kubernetes resource.

Example:

```yaml
integrations:
  - name: django-runtime
    version: v1
    enabled: true
    capabilities: [metadata, health]
    config:
      health_path: /healthz
      metadata_label: django
  - name: otlp-export
    version: v1
    enabled: false
    capabilities: [telemetry, deployment_config]
    config:
      endpoint: https://collector.example.test:4318
      service_namespace: djangoops
```

Integration evaluation is one-shot, count/time/size bounded, has no unbounded retry queue, and remains side-band. Disabling/removing an integration requires no destructive database migration and leaves all core Phase 0–4 paths available. See [`docs/phase4-ecosystem.md`](docs/phase4-ecosystem.md) for versioning, discovery, capability negotiation, secret handling, isolation, compatibility, and explicit anti-PaaS non-goals.

## Security boundaries

DjangoOps intentionally does not become a marketplace, generic plugin runner, generic PaaS, remote shell, generic container platform, or generic Kubernetes control plane. Across all phases:

- runtime secrets are operator-provisioned and excluded from persisted project configuration/results;
- Phase 1 remains outbound-only and allowlisted;
- Phase 2 authorization begins from server-side project membership;
- Phase 3 remains namespace/release scoped with migration, cancellation, idempotency, and rollback gates;
- Phase 4 telemetry remains redacted, low-cardinality, bounded, and failure-isolated;
- Phase 4 ecosystem configuration is static, versioned, capability-limited, and cannot install/load user-selected code.

## Development and CI

Python is pinned to 3.12; the agent uses Go 1.23.

```bash
uv sync --group dev
uv run ruff check .
uv run ruff format --check .
uv run mypy cli/djangoops controlplane tests
uv run pytest
uv run python manage.py makemigrations --check --dry-run
cd agent && go test ./...
scripts/generate_proto.sh
git diff --exit-code -- controlplane/generated agent/gen
```

GitHub Actions runs the Python suite plus Phase 1, Phase 2, Phase 3 (including Kind), and Phase 4 observability/ecosystem/current-documentation checks.

## Repository layout

- `cli/` — Python CLI, direct-SSH Phase 0 fallback, non-secret project configuration, and bounded ecosystem contract
- `controlplane/` — Django control plane, dashboard/GraphQL, gRPC gateway, Kubernetes lifecycle, observability, and project-scoped ecosystem view
- `agent/` — outbound-only Go diagnostic/Kubernetes executor and telemetry client
- `charts/djangoops/` — DjangoOps-owned Helm chart
- `proto/djangoops/agent/v1/` — versioned shared agent protocol
- `docs/` — architecture, security, phase, and operator documentation
