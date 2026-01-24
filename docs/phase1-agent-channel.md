# Phase 1 secure agent control channel

Phase 1 changes the control transport without replacing proven Phase 0 deployment behavior. The Go agent initiates a single outbound bidirectional gRPC connection to the Python control plane. Production transport always uses TLS with CA-chain and hostname verification. Agent authentication is a separate secret sent only after TLS is established and compared server-side with constant-time comparison.

## Provisioning and trust

Provision the control-plane certificate/key and per-agent token outside Git. Install only the CA certificate on the agent. Configure the endpoint, expected TLS server name, stable agent ID, project root, and token through the runtime environment/service manager. The agent opens no inbound port and no insecure/plaintext code path exists.

`DJANGOOPS_COMPOSE_FILE` carries the same project-relative Compose path used by the Phase 0 deployment. It defaults to `docker-compose.yml`; custom paths such as `ops/compose.prod.yml` are accepted only when they are project-relative and contain no absolute, `.`/`..`, or control-character segments. Provision this value alongside `DJANGOOPS_PROJECT_ROOT` so Phase 1 diagnostics inspect the exact deployed Compose project rather than silently falling back to a different file.

The protocol is versioned at `proto/djangoops/agent/v1/agent.proto`. Generation is reproducible through `scripts/generate_proto.sh`; Go and Python outputs are committed and CI fails on drift.

## Diagnostics

Protocol v1 exposes exactly one executable request: Django diagnostics. The requested release path must be relative and cannot escape the configured project root. The agent emits sequence-numbered controlled progress frames and one terminal structured result. Checks cover active release identity, Compose declared/running services, `manage.py check --deploy`, database connectivity/backend, pending migrations, Django/Python metadata, and configured Celery/Celery Beat service status.

The agent never streams `.env`, process environment, credentials, `SECRET_KEY`, database URLs/passwords, S3 secrets, SSH keys, ACME private material, or unrestricted subprocess stdout/stderr. Captured subprocess output is memory-bounded and only narrowly parsed safe values are surfaced.

## Resource bounds, cancellation, and failure

The persistent channel is explicitly bounded. The agent admits at most four concurrent diagnostic jobs and rejects additional work fail-closed; it retains only the most recent 256 completed job IDs. The Python gateway applies the same four-job per-agent admission limit, retains at most 256 completed IDs, and uses bounded per-job event and per-session outgoing queues so a buggy or authenticated peer cannot create unbounded bookkeeping, concurrency, or queue growth.

A cancel for an active job is idempotent and cannot target a different/completed job. Cancellation propagates through gRPC into the Go context and terminates the diagnostic process group. If the stream is lost, all active local jobs are canceled before reconnect; they do not continue unobserved. Reconnect sends a fresh hello/capability announcement and heartbeat and does not replay stale commands. IDs still inside the bounded completed-retention window are rejected if replayed; evicted IDs may be reused after the retention window has moved on.

## Rollback

Phase 0 direct SSH remains the fallback. Rolling back this Phase 1 commit means stopping/removing the agent and gateway path only. It must not mutate active releases, PostgreSQL/Redis volumes, migrations, certificates, object-storage buckets, or backups.
