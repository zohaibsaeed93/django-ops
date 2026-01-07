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
