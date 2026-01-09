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

Each deployment is uploaded under `<remote-base>/releases/<release-id>`. The release links its local `.env` path to `<remote-base>/shared/.env`, then starts the generated Compose stack with the stable Compose project name from `djangoops.yaml`. That stable identity keeps named PostgreSQL/Redis volumes attached across timestamped release directories. Only after Compose startup succeeds is `<remote-base>/current` atomically switched to the new release.

If upload fails, the existing `current` release is not changed and DjangoOps only attempts to remove the newly-created staging release. Before startup, DjangoOps records the exact pre-deployment `current` target in a bounded rollback pointer. If Compose startup or pointer activation fails—even when the remote switch completed but the SSH client could not observe its exit status—recovery uses that saved pre-deployment state instead of inferring it from post-failure `current`. The rollback and temporary activation pointers are removed after successful activation or bounded recovery cleanup. Existing release directories remain available; no `docker compose down -v`, volume pruning, broad host cleanup, or other destructive data operation is used.

Expected operational errors return a non-zero CLI status without a Python traceback. SSH/remote command output is intentionally not echoed by DjangoOps so runtime secret values from the host cannot accidentally leak into CLI logs.

**Migration safety is not part of this deployment slice.** `djangoops deploy` does not run `manage.py migrate`. Migration pre-flight and automatic rollback are the following Phase 0 capability, and HTTPS, S3, backups, diagnostics, the Go agent, dashboard, Kubernetes, and observability remain outside this command.

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
