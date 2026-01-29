# DjangoOps

DjangoOps is an opinionated operations platform for production Django applications. It keeps Django-specific deployment, backup, recovery, and diagnostics semantics rather than exposing a generic remote-shell or PaaS surface.

## Current phase

**Phase 2 is active.** Phase 0 direct-SSH deploy/rollback, health, Compose generation, and backup/restore remain the proven fallback path. Phase 1's outbound-only authenticated Go agent remains the only persistent execution channel. Phase 2 adds an authenticated Django operations dashboard and a project-scoped, typed GraphQL API over that existing agent-backed diagnostic path. It does not add arbitrary shell execution, generic container administration, Kubernetes/Helm, or observability.

See [`docs/phase2-dashboard-graphql.md`](docs/phase2-dashboard-graphql.md) for the dashboard/API trust boundary, GraphQL contract, resource limits, persistence, and rollback guidance. [`docs/phase1-agent-channel.md`](docs/phase1-agent-channel.md) remains authoritative for the agent transport. Phase 0 dogfood evidence remains in [`docs/phase0-dogfood.md`](docs/phase0-dogfood.md); the reusable Phase 0 operator workflow is preserved below.

## Initialize a project

After `uv sync --group dev`, create the non-secret configuration:

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

Use `--project-name my-app` when the directory name should not be the project name. The default backup schedule is `17 2 * * *`; override it with `--backup-schedule` when required. `djangoops.yaml` contains only non-secret project, TLS, object-storage, and backup policy metadata. S3 access keys, secret keys, database passwords, Django `SECRET_KEY`, SSH keys, and tokens must remain outside it.

Schema-v2 projects must be upgraded explicitly before using schema-v3 commands so DjangoOps never guesses a backup destination or silently reinterprets unknown keys:

```bash
uv run djangoops config-upgrade \
  --backup-bucket my-app-backups \
  --backup-schedule '17 2 * * *'
```

Review the resulting `backup` block and commit the non-secret configuration change normally. The hostname must resolve publicly to the deployment VPS before certificate issuance. The storage endpoint is an external S3-compatible service; DjangoOps does not run or proxy object storage and does not install MinIO in production.

## Generate the Phase 0 Compose stack

```bash
uv run djangoops compose
```

The generated stack contains Traefik plus the enabled Django/PostgreSQL/Redis/Celery services. Only Traefik publishes host ports (`80` and `443`). Django port `8000`, PostgreSQL, and Redis remain internal to Compose. Traefik redirects HTTP to HTTPS, routes the configured hostname to Django, and obtains Let's Encrypt certificates through ACME. Certificate state is persisted in the `traefik_acme` named volume and the Docker socket is mounted read-only for service discovery.

Runtime `.env` must provide at least the required application/database values plus object-storage credentials:

```text
AWS_ACCESS_KEY_ID=<provisioned outside the repository>
AWS_SECRET_ACCESS_KEY=<provisioned outside the repository>
POSTGRES_PASSWORD=<provisioned outside the repository>
```

DjangoOps passes these non-secret S3 settings to application containers from `djangoops.yaml`: `DJANGOOPS_S3_ENDPOINT_URL`, `DJANGOOPS_S3_STATIC_BUCKET`, `DJANGOOPS_S3_MEDIA_BUCKET`, and `AWS_REGION` when configured. Applications can map that contract into `django-storages`/their Django storage backend. Secret credential values are never rendered into generated Compose YAML. Provision S3 credentials with least privilege for only the configured static, media, and backup buckets and required object operations; do not use account-wide administrative keys.

## VPS, DNS, TLS, and backup prerequisites

Before deployment or backup scheduling:

- the configured hostname must resolve to the VPS;
- inbound TCP 80 and 443 must be reachable from the public internet for normal traffic and Let's Encrypt HTTP-01 issuance;
- Docker with the Compose plugin, OpenSSH access, `cron`/`crontab`, and the AWS CLI compatible with the configured S3 service must already exist on the VPS;
- the operator's normal `known_hosts` must trust the VPS host key;
- `/srv/djangoops/<project>/shared/.env` must be provisioned separately with runtime secrets and S3 credentials;
- the configured static/media/backup buckets must already exist and the supplied S3 credentials must have the intended project-scoped permissions.

Certificate issuance depends on public DNS/reachability. DjangoOps does not bypass those checks or disable SSH host-key verification.

## Deploy to one VPS over direct SSH

Phase 0 uses the system OpenSSH client directly and installs no persistent deployment agent:

```bash
uv run djangoops deploy \
  --host app.example.com \
  --user deploy \
  --remote-base /srv/djangoops/my-app
```

Use `--port` for a non-default SSH port and `--identity-file /path/to/key` for a specific key path. DjangoOps never reads or prints private-key contents and does not upload `.env`.

Deployments use staged release directories and a stable Compose project identity. DjangoOps starts the staged stack, runs `manage.py migrate --plan --noinput`, then `manage.py migrate --noinput`, and atomically activates the release only after migration succeeds. Existing direct-SSH, custom Compose-path, and rollback behavior remains unchanged.

Recovery restores/restarts the saved previous application release when possible. It never uses `docker compose down -v`, volume pruning, database drop/recreate, certificate-state deletion, bucket/object deletion, or guessed reverse migrations. PostgreSQL/Redis named volumes, `traefik_acme`, and external S3 data therefore survive application-release rollback. If migrations may have changed the schema, DjangoOps warns that operator inspection may be required instead of claiming arbitrary database rollback.

SSH/remote stdout and stderr are intentionally not echoed so runtime secrets cannot accidentally leak into CLI logs.

## Schedule, run, list, and restore backups

Phase 0 backups remain agentless. DjangoOps installs one project-scoped executable backup script under the remote `shared` directory and one marker-owned cron entry. Rerunning schedule installation replaces the DjangoOps-owned entry instead of appending duplicates:

```bash
uv run djangoops backup-schedule \
  --host app.example.com \
  --user deploy \
  --remote-base /srv/djangoops/my-app
```

The scheduled runner sources the already-provisioned remote `shared/.env`; credentials are not copied into `djangoops.yaml`, generated Compose, cron lines, or normal CLI output. Each run creates one UTC identifier such as `b20260120T044000Z`, performs `pg_dump` against the deployed Compose `postgres` service, snapshots the configured media bucket, and stores both under the same project-scoped backup prefix. Existing backups are retained; backup creation performs no implicit retention cleanup.

After installing the schedule, an operator can trigger the same runner manually:

```bash
uv run djangoops backup-run \
  --host app.example.com \
  --user deploy \
  --remote-base /srv/djangoops/my-app
```

List completed restore points deterministically:

```bash
uv run djangoops backup-list \
  --host app.example.com \
  --user deploy \
  --remote-base /srv/djangoops/my-app
```

Restore always requires an explicit identifier; there is intentionally no implicit `latest` restore:

```bash
uv run djangoops backup-restore b20260120T044000Z \
  --host app.example.com \
  --user deploy \
  --remote-base /srv/djangoops/my-app
```

Before stopping writers, restore verifies the active release, remote runtime environment, required Compose services, selected metadata/database/media artifacts, and S3 credentials/tools. It then snapshots the current database and media into operator-recoverable state, stops Django/Celery writers, restores the selected PostgreSQL dump and matching media set, restarts the configured writers, and runs `manage.py check --deploy`. If pre-flight fails, no destructive restore command is reached. If a failure occurs after mutation begins, DjangoOps reports a hard failure and does not claim success; preserved pre-restore artifacts are left for operator recovery. Restore does not delete the selected source backup.

## Check deployment health over direct SSH

`djangoops health` is a read-only Phase 0 diagnostic command. It uses the same validated OpenSSH target options as deploy and installs no persistent agent:

```bash
uv run djangoops health \
  --host app.example.com \
  --user deploy \
  --remote-base /srv/djangoops/my-app
```

For automation, request exactly one deterministic JSON document on stdout:

```bash
uv run djangoops health \
  --host app.example.com \
  --user deploy \
  --remote-base /srv/djangoops/my-app \
  --json
```

The health report resolves the active `current` release and independently checks the active release pointer, declared Compose services versus running services, `manage.py check --deploy`, Django database connectivity, and `manage.py migrate --check --noinput`. It does not restart containers, run migrations, rewrite Compose, change the active pointer, modify certificates, or alter storage.

Exit status `0` means every diagnostic passed. Exit status `1` means health was successfully evaluated but one or more diagnostics failed. Exit status `2` is reserved for invalid target/usage or transport/evaluation failures such as an SSH failure or malformed remote response. In JSON mode no diagnostic log chatter is written to stdout. Remote command output, `.env` contents, credentials, Django secrets, SSH private-key contents, and ACME material are not serialized into reports.

Use `--port` and `--identity-file` exactly as with deploy. OpenSSH runs with `BatchMode=yes` and normal host-key verification remains in effect.

## Phase 1 agent

The Phase 1 agent requires non-secret endpoint/identity/project-root settings plus separately provisioned secret authentication material:

```text
DJANGOOPS_CONTROLPLANE_ENDPOINT=control.example.com:8443
DJANGOOPS_CONTROLPLANE_SERVER_NAME=control.example.com
DJANGOOPS_CONTROLPLANE_CA_FILE=/etc/djangoops/controlplane-ca.pem
DJANGOOPS_AGENT_ID=prod-app-01
DJANGOOPS_AGENT_AUTH_TOKEN=<provisioned outside repository>
DJANGOOPS_PROJECT_ROOT=/srv/djangoops/my-app
DJANGOOPS_COMPOSE_FILE=ops/compose.prod.yml
```

`DJANGOOPS_COMPOSE_FILE` is optional and defaults to `docker-compose.yml`. When set, it must remain a validated project-relative path; absolute paths, traversal, dot segments, and control characters are rejected.

The agent opens no listening socket. TLS certificate-chain and hostname verification are mandatory; there is no insecure fallback. Authentication is sent only inside TLS and is never logged. The protocol exposes only allowlisted Django diagnostics scoped beneath `DJANGOOPS_PROJECT_ROOT`; it is not an arbitrary command or shell channel.

The control-plane process similarly reads its TLS key/certificate and agent-token mapping from runtime configuration. `DJANGOOPS_AGENT_TOKENS_JSON` is secret runtime material, not project configuration. Diagnostic admission, completed-job retention, and queues are bounded; overload fails closed or applies bounded backpressure. Disconnect cancels unobserved work, reconnect announces fresh state, and completed/stale jobs are not replayed.

Rollback of the Phase 1 task removes the persistent agent channel only. The direct-SSH Phase 0 deploy, recovery, health, backup scheduling, backup execution/listing, and explicit restore workflow above remains the supported fallback and does not require the agent.

## Phase 2 dashboard and GraphQL API

Bootstrap the Django control-plane database and an operator account:

```bash
export DJANGOOPS_WEB_SECRET_KEY='<provisioned outside repository>'
uv run python manage.py migrate
uv run python manage.py createsuperuser
```

Run `uv run djangoops-controlplane` with the existing Phase 1 gateway TLS/token environment. The web listener defaults to `127.0.0.1:8000`; terminate public TLS in front of it. Set `DJANGOOPS_WEB_DEBUG=0` in production, provide a strong `DJANGOOPS_WEB_SECRET_KEY`, and configure `DJANGOOPS_WEB_ALLOWED_HOSTS`. Production startup fails closed when the web secret is missing, session/CSRF cookies are secure, and GraphQL introspection is disabled.

The dashboard at `/` and `POST /graphql/v1` use Django session authentication and server-side project membership on every project/agent/operation access. GraphQL exposes only the existing `DJANGO_HEALTH` diagnostic through the Phase 1 gateway, not arbitrary commands. Active agent state comes from the live gateway session: connectivity, heartbeat age, negotiated protocol version, and capabilities. Operations persist bounded status/result summaries with explicit offline, timeout, cancellation, disconnect, overload/backpressure, and failure states. GraphQL depth/field-count limits and bounded dashboard update polling prevent unbounded client work.

See [`docs/phase2-dashboard-graphql.md`](docs/phase2-dashboard-graphql.md) for schema/API details, migration rollback, and security constraints.

## Development

Python development is pinned to Python 3.12 and uses `uv`. Phase 1+ additionally uses Go 1.23 and reproducible protobuf generation.

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

GitHub Actions runs Python quality/regression checks, Django migration/system/behavior checks, Go format/vet/tests, protobuf generation drift/contract checks, security assertions, and cross-language TLS integration tests.

## Repository layout

- `controlplane/` — Django control plane, authenticated Phase 2 dashboard/GraphQL API, and the Phase 1 gRPC transport/application boundary
- `cli/` — Python CLI and direct-SSH Phase 0 fallback
- `agent/` — outbound-only Go diagnostic executor introduced in Phase 1
- `proto/djangoops/agent/v1/` — versioned shared agent protocol contract
- `docs/` — project, phase, and operational documentation
