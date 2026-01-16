# DjangoOps

DjangoOps is an opinionated operations platform for production Django applications. It targets repeated Django integration work around PostgreSQL, Redis, Celery, Docker Compose, HTTPS, backups, deployment safety, and Django-aware diagnostics.

## Current phase

Development is intentionally limited to **Phase 0 (MVP)**. Persistent Go agents/gRPC, dashboard/GraphQL, Kubernetes/Helm, and observability are later-phase work.

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
  --media-bucket my-app-media
```

Use `--project-name my-app` when the directory name should not be the project name. `djangoops.yaml` contains only non-secret project, TLS, and object-storage metadata. S3 access keys, secret keys, database passwords, Django `SECRET_KEY`, SSH keys, and tokens must remain outside it.

The hostname must resolve publicly to the deployment VPS before certificate issuance. The storage endpoint is an external S3-compatible service; DjangoOps does not run or proxy object storage and does not install MinIO in production.

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

DjangoOps passes these non-secret S3 settings to application containers from `djangoops.yaml`: `DJANGOOPS_S3_ENDPOINT_URL`, `DJANGOOPS_S3_STATIC_BUCKET`, `DJANGOOPS_S3_MEDIA_BUCKET`, and `AWS_REGION` when configured. Applications can map that contract into `django-storages`/their Django storage backend. Secret credential values are never rendered into generated Compose YAML. Provision S3 credentials with least privilege for only the configured buckets and required object operations; do not use account-wide administrative keys.

## VPS, DNS, and TLS prerequisites

Before deployment:

- the configured hostname must resolve to the VPS;
- inbound TCP 80 and 443 must be reachable from the public internet for normal traffic and Let's Encrypt HTTP-01 issuance;
- Docker with the Compose plugin and OpenSSH access must already exist on the VPS;
- the operator's normal `known_hosts` must trust the VPS host key;
- `/srv/djangoops/<project>/shared/.env` must be provisioned separately with runtime secrets and S3 credentials;
- the configured static/media buckets must already exist and the supplied S3 credentials must have the intended permissions.

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

## Check deployment health over direct SSH

`djangoops health` is an on-demand, read-only Phase 0 diagnostic command. It uses the same validated OpenSSH target arguments as `deploy` and installs no persistent agent:

```bash
uv run djangoops health \
  --host app.example.com \
  --user deploy \
  --remote-base /srv/djangoops/my-app
```

For automation, request one deterministic JSON document on stdout:

```bash
uv run djangoops health \
  --host app.example.com \
  --user deploy \
  --remote-base /srv/djangoops/my-app \
  --json
```

The report has `schema_version: 1`, an overall `healthy`/`unhealthy` status, and stable named checks for the active `current` release pointer, expected Compose service runtime/health state, `manage.py check --deploy`, Django database connectivity, and `manage.py migrate --check --noinput`. The command discovers the active release through the managed `current` pointer and runs Django checks inside that release's active web container.

Exit status `0` means every health check passed. Exit status `1` means health was evaluated and the deployment is unhealthy; the complete report is still printed. Exit status `2` means the target was invalid or health could not be evaluated over SSH, so callers can distinguish transport/usage failures from an unhealthy deployment.

Health diagnostics never print remote command output or read back `.env`, Django `SECRET_KEY`, database/S3 credentials, SSH private-key contents, or ACME material. They do not restart services, run migrations, change `current`, modify Compose state, or alter storage. `--port` and `--identity-file` behave exactly as with `deploy`, including `BatchMode=yes` and normal SSH host-key verification.

## Development

Python development is pinned to Python 3.12 and uses `uv`.

```bash
uv sync --group dev
just check
```

The same Ruff lint/format, strict mypy, and pytest checks run in GitHub Actions.

## Repository layout

- `controlplane/` — Django + DRF control plane (Phase 0+)
- `cli/` — Python CLI (Phase 0+)
- `docs/` — project and operational documentation
