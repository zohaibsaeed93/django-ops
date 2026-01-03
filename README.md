# DjangoOps

DjangoOps is an opinionated operations platform for production Django applications. It targets the repeated integration work around Django, PostgreSQL, Redis, Celery, Docker Compose, HTTPS, backups, and deployment diagnostics with a Django-aware workflow.

## Current phase

Development is intentionally limited to **Phase 0 (MVP)**. The persistent Go deploy agent, gRPC transport, dashboard/GraphQL layer, Kubernetes target, and observability stack are later-phase work and are not part of the current implementation surface.

Phase 0 will ultimately provide the CLI and Django control-plane foundation, Docker Compose generation, direct-SSH deployment, migration safety and rollback, S3-compatible storage, Traefik HTTPS, Django-aware health checks, and backups. Phase 1 does not begin until Phase 0 has been dogfooded on a real Django application.

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
