# DjangoOps

DjangoOps is an opinionated operations platform for deploying, operating, diagnosing, backing up, and recovering production Django applications.

The project is built around one core idea: **production operations should understand Django instead of treating a Django app like an arbitrary container workload**. DjangoOps keeps deployment, migrations, health checks, Celery workers, backups, static/media storage, rollback, telemetry, and runtime diagnostics tied to Django-specific semantics while deliberately avoiding a generic remote-shell or PaaS control plane.

It is designed for solo developers, small teams, startups, and open-source projects that want a production path from a single VPS through to a Kubernetes/K3s environment without giving up a simple recovery model.

## What DjangoOps does

DjangoOps provides an end-to-end operations path for Django applications:

- creates a validated non-secret `djangoops.yaml` project configuration;
- generates a deterministic production Docker Compose stack for Django, PostgreSQL, Redis, Celery, Celery Beat, and Traefik;
- deploys releases over direct OpenSSH with normal host-key verification;
- runs Django migration preflight before activation and performs bounded application rollback/recovery on failure;
- provisions HTTPS routing through Traefik and Let's Encrypt;
- integrates external S3-compatible storage for static files, media, and backups;
- runs Django-aware health diagnostics for releases, Compose services, `manage.py check --deploy`, database connectivity, migrations, framework/runtime versions, and Celery state;
- schedules PostgreSQL and media backups and supports explicit, preflighted restore;
- provides an outbound-only Go agent over authenticated TLS/gRPC for persistent diagnostics, progress streaming, cancellation, reconnect handling, and later Kubernetes lifecycle work;
- exposes an authenticated Django operations dashboard and project-scoped typed GraphQL API;
- supports existing Kubernetes/K3s clusters through a DjangoOps-owned Helm deployment path with migration gates, cancellation, idempotency, rollback compatibility checks, and health verification;
- exposes bounded Prometheus/OpenTelemetry observability and a static, allowlisted integration contract without dynamic third-party code execution.

The original direct-SSH Phase 0 path remains available as the recovery fallback even when later control-plane components are unavailable.

## Architecture

```text
Developer / Operator
        |
        | djangoops CLI / Dashboard / GraphQL
        v
+-----------------------------+
| DjangoOps Control Plane     |
| Django + project auth       |
| diagnostics / releases      |
| observability / ecosystem   |
+-----------------------------+
        |
        | authenticated TLS/gRPC
        v
+-----------------------------+
| djangoops-agent (Go)        |
| outbound-only               |
| typed bounded operations    |
+-----------------------------+
        |
        +-----------------------------+
        |                             |
        v                             v
Single VPS / Docker Compose      Existing Kubernetes/K3s
Django + Postgres + Redis        DjangoOps Helm releases
Celery + Beat + Traefik          migration/rollback gates
        |
        v
External S3-compatible storage
static / media / backups
```

DjangoOps intentionally does **not** expose arbitrary shell execution, arbitrary containers, unrestricted Kubernetes resources, dynamic plugin loading, or a generic hosting marketplace.

## Technology stack

### Core application

- **Python 3.12** — CLI, configuration, deployment orchestration, health, backup/restore, and control-plane logic
- **Django 5.2** — authenticated control plane, project membership, operator UI, persistent operation state
- **GraphQL** — typed, project-scoped operations API with bounded queries and mutations
- **uv** — Python dependency and environment management

### Runtime and data services

- **PostgreSQL** — Django application/control-plane relational state and production database runtime
- **Redis** — cache/broker runtime support
- **Celery + Celery Beat** — background workers and scheduled task execution
- **Docker Compose** — deterministic single-VPS deployment topology and recovery path
- **Traefik** — production HTTP/HTTPS ingress and Let's Encrypt ACME handling
- **S3-compatible object storage** — external static files, media, and backup artifacts

### Agent and control channel

- **Go 1.23** — `djangoops-agent`
- **gRPC + Protocol Buffers** — versioned typed control protocol
- **TLS + runtime authentication** — authenticated encrypted agent/control-plane communication
- outbound-only agent model with no inbound agent listener

### Kubernetes path

- **Kubernetes / K3s** — registered existing cluster targets
- **Helm** — DjangoOps-owned release contract for Django web, Celery workers/Beat, migrations, ingress, resources, and health checks
- namespace-scoped least privilege rather than a generic cluster-admin control surface

### Observability

- **Prometheus-compatible metrics** — authenticated, bounded, low-cardinality control-plane metrics
- **OpenTelemetry / OTLP** — optional bounded telemetry export for Django/Celery workloads and operations
- redacted correlation identifiers and failure-isolated telemetry behavior

### Quality and CI

- **Ruff** — linting and formatting checks
- **mypy** — strict Python type checking
- **pytest** — Python behavior and integration tests
- **Go test / vet / race checks** — agent verification
- **pre-commit** — local quality hooks
- **GitHub Actions** — Python, Phase 1, Phase 2, Phase 3/Kind, and Phase 4 verification

## Product phases

All planned phases are implemented:

- **Phase 0 — single-VPS production path.** Python CLI, Docker Compose, direct SSH deployment, migration safety, HTTPS, S3-compatible storage, Django-aware health, backups, restore, and real-app dogfood.
- **Phase 1 — secure persistent agent channel.** Outbound-only Go agent, TLS/gRPC, versioned protobufs, live progress, cancellation, reconnect handling, and richer diagnostics.
- **Phase 2 — authenticated operations control plane.** Django dashboard and project-scoped typed GraphQL API.
- **Phase 3 — Kubernetes/K3s deployment path.** Existing-cluster registration, namespace-scoped access, DjangoOps-owned Helm releases, migration gates, cancellation, idempotency, rollback safety, and Kind acceptance.
- **Phase 4 — observability and bounded ecosystem.** Django-native telemetry correlation, Prometheus/OpenTelemetry, and a versioned static integration contract.

Detailed phase documentation lives in [`docs/`](docs/README.md).

## Quick start for development

Requirements:

- Python 3.12
- `uv`
- Go 1.23+

Install dependencies:

```bash
uv sync --group dev
```

Run the Python checks:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy cli/djangoops controlplane tests
uv run pytest
uv run python manage.py makemigrations --check --dry-run
```

Run the Go agent tests and verify generated protobufs:

```bash
cd agent
go test ./...
cd ..

scripts/generate_proto.sh
git diff --exit-code -- controlplane/generated agent/gen
```

## Run the control plane locally

```bash
export DJANGOOPS_WEB_SECRET_KEY='dev-only-secret'
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run djangoops-controlplane
```

The control-plane listener defaults to loopback. Public production deployments should terminate TLS in front of it.

## Initialize a Django project

From a Django application directory:

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

`djangoops.yaml` is intentionally non-secret. Runtime credentials, database passwords, Django `SECRET_KEY`, SSH keys, TLS private keys, agent tokens, and Kubernetes credentials stay outside project configuration.

Generate the single-VPS production stack:

```bash
uv run djangoops compose
```

## Deploy to a VPS

Before deployment, provision:

- public DNS and inbound TCP 80/443;
- Docker + Compose, OpenSSH, cron, and an S3-compatible AWS CLI on the VPS;
- trusted SSH host keys in normal `known_hosts`;
- `/srv/djangoops/<project>/shared/.env` containing runtime secrets;
- external static/media/backup buckets with least-privilege credentials.

Deploy:

```bash
uv run djangoops deploy \
  --host app.example.com \
  --user deploy \
  --remote-base /srv/djangoops/my-app
```

Deployments stage a new release, run migration planning and migrations, and only activate the release after the migration gate succeeds. Recovery never guesses reverse migrations and never performs destructive volume/database cleanup such as `docker compose down -v`, volume pruning, or database drop/recreate.

## Django-aware health

```bash
uv run djangoops health \
  --host app.example.com \
  --user deploy \
  --remote-base /srv/djangoops/my-app \
  --json
```

Health checks cover the active release, declared/running services, Django deployment checks, database connectivity, pending migrations, and configured worker state. Exit codes are:

- `0` — healthy
- `1` — diagnostics ran and found an unhealthy condition
- `2` — invalid input, transport, or evaluation failure

## Backup and restore

Install or replace the project backup schedule:

```bash
uv run djangoops backup-schedule \
  --host app.example.com \
  --user deploy \
  --remote-base /srv/djangoops/my-app
```

Run and list backups:

```bash
uv run djangoops backup-run --host app.example.com --user deploy --remote-base /srv/djangoops/my-app
uv run djangoops backup-list --host app.example.com --user deploy --remote-base /srv/djangoops/my-app
```

Restore an explicit backup:

```bash
uv run djangoops backup-restore b20260120T044000Z \
  --host app.example.com \
  --user deploy \
  --remote-base /srv/djangoops/my-app
```

Restore performs validation before destructive work, preserves operator-recoverable pre-restore state, restarts configured writers, and verifies Django health after recovery.

## Outbound agent

The persistent agent uses non-secret endpoint/project configuration plus separately provisioned runtime authentication:

```text
DJANGOOPS_CONTROLPLANE_ENDPOINT=control.example.com:8443
DJANGOOPS_CONTROLPLANE_SERVER_NAME=control.example.com
DJANGOOPS_CONTROLPLANE_CA_FILE=/etc/djangoops/controlplane-ca.pem
DJANGOOPS_AGENT_ID=prod-app-01
DJANGOOPS_AGENT_AUTH_TOKEN=<runtime secret>
DJANGOOPS_PROJECT_ROOT=/srv/djangoops/my-app
DJANGOOPS_COMPOSE_FILE=ops/compose.prod.yml
```

The agent opens no listening socket. TLS chain/hostname verification is mandatory and the protocol exposes only versioned, allowlisted DjangoOps operations.

## Kubernetes/K3s

DjangoOps can register an existing Kubernetes/K3s target and deploy through [`charts/djangoops`](charts/djangoops). The Kubernetes path keeps Django-specific release semantics: migration-before-activation, immutable image references, cancellation, durable idempotency, rollback compatibility checks, health verification, and namespace-scoped access.

See [`docs/phase3-kubernetes.md`](docs/phase3-kubernetes.md).

## Observability and integrations

Phase 4 adds bounded Django-native observability and two built-in reference integrations:

- `django-runtime` `v1` — Django runtime metadata and bounded health-path capability
- `otlp-export` `v1` — validated OTLP deployment metadata around the existing observability path

The ecosystem is a static allowlisted data contract. It cannot dynamically install/import user-selected Python packages, repositories, images, manifests, shell commands, raw GraphQL, or unrestricted Kubernetes resources.

See [`docs/phase4-observability.md`](docs/phase4-observability.md) and [`docs/phase4-ecosystem.md`](docs/phase4-ecosystem.md).

## Security model

DjangoOps keeps execution authority intentionally narrow:

- runtime secrets are operator-provisioned and excluded from committed config/results/log output;
- SSH keeps normal host-key verification;
- the Go agent is outbound-only and authenticated over encrypted transport;
- GraphQL/dashboard authorization begins with server-side project membership;
- Kubernetes access is namespace/release scoped;
- telemetry is bounded, redacted, and failure-isolated;
- integrations are static, versioned, and capability-limited;
- Phase 0 direct SSH remains available as a recovery path if later control-plane components fail.

## Repository layout

- `cli/` — CLI, configuration, Compose generation, direct-SSH deploy/recovery, health, backup/restore, integrations
- `controlplane/` — Django control plane, dashboard/GraphQL, gRPC gateway, Kubernetes lifecycle, observability
- `agent/` — outbound-only Go agent
- `charts/djangoops/` — DjangoOps-owned Helm chart
- `proto/djangoops/agent/v1/` — versioned shared protocol
- `docs/` — architecture, security, phase, and operator documentation
- `examples/` — dogfood and telemetry fixtures

## Documentation

Start with [`docs/README.md`](docs/README.md), then see:

- [`docs/phase0-dogfood.md`](docs/phase0-dogfood.md)
- [`docs/phase1-agent-channel.md`](docs/phase1-agent-channel.md)
- [`docs/phase2-dashboard-graphql.md`](docs/phase2-dashboard-graphql.md)
- [`docs/phase3-build-vs-integrate.md`](docs/phase3-build-vs-integrate.md)
- [`docs/phase3-kubernetes.md`](docs/phase3-kubernetes.md)
- [`docs/phase4-observability.md`](docs/phase4-observability.md)
- [`docs/phase4-ecosystem.md`](docs/phase4-ecosystem.md)
