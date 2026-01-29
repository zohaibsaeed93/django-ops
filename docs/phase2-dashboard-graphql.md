# Phase 2 dashboard and GraphQL control API

Phase 2 adds the authenticated operator surface without changing the agent trust boundary. The Go agent remains outbound-only and exposes only the versioned Phase 1 typed Django diagnostics channel. The browser and GraphQL clients talk only to Django; Django delegates execution to the existing Phase 1 gateway application boundary.

## Local bootstrap

```bash
uv sync --group dev
export DJANGOOPS_WEB_SECRET_KEY='development-only-change-me'
uv run python manage.py migrate
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

For the combined production-shaped process, provision the existing Phase 1 TLS/gateway environment and run `uv run djangoops-controlplane`. It serves the Django ASGI application on `DJANGOOPS_WEB_HOST`/`DJANGOOPS_WEB_PORT` (defaults `127.0.0.1:8000`) while keeping the agent gRPC/TLS listener and Django gateway adapter in one process.

Create `Project` and `AgentRegistration` records through operator-controlled Django tooling, then attach Django users to `Project.members`. Membership is the server-side authorization boundary. Agent tokens and TLS private keys stay in runtime configuration and are never stored in these tables.

## GraphQL v1

`POST /graphql/v1` requires an authenticated Django session and CSRF token. The typed schema exposes authorized projects, registered agents with Boolean connection state and heartbeat age, bounded recent diagnostic operations, individual operation status/results, `startDiagnostic`, and `cancelDiagnostic`. `startDiagnostic` accepts only `DJANGO_HEALTH`, which maps to the existing Phase 1 diagnostics request; there is no shell, arbitrary command, container exec, package operation, or user-defined RPC payload.

Queries are bounded by depth and field-count limits (`DJANGOOPS_GRAPHQL_MAX_DEPTH`, `DJANGOOPS_GRAPHQL_MAX_FIELDS`), including fragment expansions, and operation history is capped per project. Diagnostic checks and detail strings are bounded and secret-like key/value text is redacted before persistence. Production should set `DJANGOOPS_WEB_DEBUG=0`; `__schema` and `__type` introspection are then rejected at the HTTP guard. Development introspection remains enabled and tested.

Failure states are explicit: offline, timeout, overloaded/backpressure, disconnected, failed, cancelled, pending, running, and succeeded. `DJANGOOPS_DIAGNOSTIC_TIMEOUT_SECONDS` defaults to 60 seconds; timeout requests cancellation through the existing gateway and records `DIAGNOSTIC_TIMEOUT`. Resolver exceptions are not returned verbatim to clients. Cross-project ID guesses resolve to not-found/error behavior instead of exposing foreign state.

## Dashboard

`/` requires login and renders only projects visible to the authenticated user. It shows registered agents, capabilities, recent typed diagnostic history, and start/cancel controls. Browser mutations use Django CSRF protection. Active rows fetch a bounded operation-status endpoint at a fixed interval and stop after a terminal state or 120 attempts, avoiding an unbounded page-wide polling loop. Django template auto-escaping protects rendered diagnostic content.

## Persistence and rollback

Phase 2 persists only projects, agent registrations/capability metadata, and bounded diagnostic operation metadata/result summaries. It does not persist agent auth tokens, TLS private material, arbitrary command payloads, raw logs, environment data, or infrastructure inventory. Migration `0001_phase2_controlplane` creates only those Phase 2 tables and can be rolled back with `python manage.py migrate controlplane zero` after disabling the web/API surface.

Removing the Phase 2 web/API process leaves the Phase 1 outbound gRPC agent/gateway protocol unchanged and leaves every Phase 0 direct-SSH deploy, rollback, health, backup, and restore command operational.
