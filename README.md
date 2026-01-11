# DjangoOps

DjangoOps is an opinionated operations platform for production Django applications. It targets the repeated integration work around Django, PostgreSQL, Redis, Celery, Docker Compose, HTTPS, backups, and deployment diagnostics with a Django-aware workflow.

## Current phase

Development is intentionally limited to **Phase 0 (MVP)**. The persistent Go deploy agent, gRPC transport, dashboard/GraphQL layer, Kubernetes target, and observability stack are later-phase work and are not part of the current implementation surface.

Phase 0 will ultimately provide the CLI and Django control-plane foundation, Docker Compose generation, direct-SSH deployment, migration safety and rollback, S3-compatible storage, Traefik HTTPS, Django-aware health checks, and backups. Phase 1 does not begin until Phase 0 has been dogfooded on a real Django application.

## Initialize a project

After `uv sync --group dev`, create the non-secret Phase 0 project configuration from your Django project directory:

```bash
uv run djangoops init --django-module config
```

Use `--project-name my-app` when the directory name should not be the DjangoOps project name. The command creates `djangoops.yaml` and refuses to overwrite an existing file. This file stores project metadata and service enablement only; credentials, private keys, database passwords, S3 secrets, and tokens belong in environment/runtime secret inputs, not in `djangoops.yaml`.

## Generate the Phase 0 Compose stack

From the same project directory, generate the deterministic deployment topology:

```bash
uv run djangoops compose
```

The command reads `djangoops.yaml` and creates `docker-compose.yml` without overwriting an existing file. The generated stack includes the enabled Django, PostgreSQL, Redis, Celery worker, and Celery Beat services. PostgreSQL and Redis stay on the internal Compose network; the Django service exposes port 8000 only to that network for later Traefik integration.

Runtime secrets remain outside generated artifacts. Put values such as `POSTGRES_PASSWORD`, Django `SECRET_KEY`, and application credentials in the ignored `.env`/runtime environment boundary. Compose generation does **not** start containers, connect to a VPS, configure HTTPS, or perform deployment.

## Deploy to one VPS over direct SSH

Phase 0 uses the system OpenSSH client directly; it does **not** install or run a persistent deployment agent. Before deploying, the VPS must already have OpenSSH access, Docker with the Compose plugin, a trusted host key in the operator's normal `known_hosts`, and the runtime environment file provisioned at the stable remote secret boundary:

```text
/srv/djangoops/my-app/shared/.env
```

DjangoOps never uploads that `.env` file. Provision it separately with host-appropriate permissions, then deploy from the Django project root after generating `docker-compose.yml`:

```bash
uv run djangoops deploy \
  --host app.example.com \
  --user deploy \
  --remote-base /srv/djangoops/my-app
```

Use `--port` for a non-default SSH port and `--identity-file /path/to/key` when OpenSSH should use a specific private-key file. DjangoOps passes the path to `ssh`; it does not read or print private-key contents. The deployment archive excludes `.env` variants, common PEM/key/certificate files, VCS state, virtual environments, caches, and symlinks; application-specific credential files must still remain outside the project tree or be handled through the runtime secret boundary. Host-key checking remains OpenSSH's normal secure default; DjangoOps does not add `StrictHostKeyChecking=no` or a known-host bypass.

Each deployment is uploaded under `<remote-base>/releases/<release-id>`. The release links its local `.env` path to `<remote-base>/shared/.env`, then starts the generated Compose stack with the stable Compose project name from `djangoops.yaml`. That stable identity keeps named PostgreSQL/Redis volumes attached across timestamped release directories.

After startup succeeds, DjangoOps runs a non-mutating Django migration pre-flight in the staged release with `manage.py migrate --plan --noinput`. Only if that succeeds does it execute `manage.py migrate --noinput`, exactly once, still against the staged release. The atomic `<remote-base>/current` switch happens only after the migration command reports success. Custom project-relative Compose paths are used consistently for startup, migration checks, migration execution, and recovery.

If upload fails, the existing `current` release is not changed and DjangoOps only attempts to remove the newly-created staging release. Before startup, DjangoOps records the exact pre-deployment `current` target in a bounded rollback pointer. If Compose startup, migration pre-flight, migration execution, or pointer activation fails—even when SSH returns an ambiguous failure—automatic recovery restores and restarts the exact saved previous application release when one exists. On a first deployment it stops only the staged Compose runtime. Recovery never uses `docker compose down -v`, volume pruning, database drop/recreate, broad host cleanup, or another destructive persistent-data operation.

Migration rollback has an explicit limit: DjangoOps does **not** claim that arbitrary database schema changes can be automatically reversed. A migration can be non-atomic or may have applied some operations before failing. In that case DjangoOps restores the previous application release/runtime and returns a non-zero migration error telling the operator that database inspection may be required; it does not guess migration targets or invent a generic database undo algorithm.

The saved rollback and temporary activation pointers are removed after successful activation or bounded recovery cleanup. Existing release directories remain available for operator inspection. Expected operational errors return a non-zero CLI status without a Python traceback. SSH/remote stdout and stderr are intentionally not echoed by DjangoOps so database URLs, Django settings output, or other runtime secret values from the host cannot accidentally leak into CLI logs.

HTTPS, S3, backups, diagnostics, the Go agent, dashboard, Kubernetes, and observability remain outside this Phase 0 deployment slice.

## Development

Python development is pinned to Python 3.12 and uses `uv` for dependency and environment management.

```bash
uv sync --group dev
just lint
just format-check
just test
```

Run the complete local quality gate with:

```bash
just check
```

The same lint, formatting, type-checking, and test checks run in GitHub Actions.

## Repository layout

- `controlplane/` — Django + DRF control plane (Phase 0+)
- `cli/` — Python CLI (Phase 0+)
- `docs/` — project and operational documentation

Later-phase directories are added only when their roadmap gates are reached.
