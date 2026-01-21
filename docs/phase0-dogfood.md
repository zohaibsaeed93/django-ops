# Phase 0 dogfood runbook

This gate validates the complete Phase 0 product against a real Django application. The fixture
uses PostgreSQL, Redis, Celery, Celery Beat, S3-compatible static/media/backup storage,
migrations, and Traefik TLS.

## Current gate result

**PASS — external-storage acceptance completed on 2026-09-09.** The final evidence snapshot was
captured at `2026-09-09T14:16:56Z` and records only non-secret identifiers and object keys.
Phase 1 remains gated on the historical integration rules.

The user-provided SSH key was used only from the Work environment. It was never committed,
printed, uploaded to either VM, or included in evidence. Runtime `.env` files, object-storage
credentials, database dumps, ACME private material, and signed URLs were not recorded.

## Environment shape

- Application VPS: `172.198.75.78`, hostname `djangoops.172-198-75-78.sslip.io`, user `azureuser`.
- Temporary external storage VM: `172.198.69.56`, resource group
  `djangoops-s3-ext-rg-20260909`, separate Azure VM/VNet/NIC/NSG/public-IP boundary from the
  application VPS. MinIO was not part of the DjangoOps application Compose project.
- External project buckets: `phase0-dogfood-static`, `phase0-dogfood-media`, and
  `phase0-dogfood-backups`.
- External object prefix: `djangoops-backups/phase0-dogfood`.
- Dedicated project application account and least-privilege policy were used; values remain only
  in protected runtime files.
- No persistent DjangoOps agent was installed.

The earlier same-VPS MinIO run is retained only as development smoke-test evidence. It is not
the final Phase 0 acceptance evidence and does not satisfy the external failure-domain gate.

## Concrete product fixes covered by this gate

- Compose health service membership compares newline-separated service names exactly.
- Generated Compose declares the configured project name and pins Traefik `v3.7.1`.

## Repeatable fixture checks

```bash
uv sync --group dev
uv run djangoops compose --config examples/phase0-dogfood/djangoops.yaml --output /tmp/phase0-dogfood-compose.yml
uv run pytest tests/test_phase0_dogfood.py
uv run ruff check .
uv run ruff format --check .
uv run mypy cli/djangoops tests
uv run pytest
```

Expected: deterministic config/Compose checks pass; services are Traefik, web, PostgreSQL,
Redis, Celery, and Celery Beat; only Traefik publishes host ports 80/443.

## External-storage acceptance evidence

All evidence below belongs to the same Phase 0 run. Timestamps are UTC.

| Criterion | UTC evidence | Secret-safe evidence | Result |
| --- | --- | --- | --- |
| Existing Phase 0 behavior | prior reviewed run | Direct SSH/no persistent agent, migration recovery, HTTPS/Let's Encrypt, Celery worker/Beat, and health diagnostics remained intact and were rechecked after the external redeploy | PASS |
| External endpoint | 2026-09-09T13:34Z–14:16Z | Independent Azure storage VM `172.198.69.56`; three dedicated buckets; MinIO outside the application Compose project | PASS |
| Static collection | 2026-09-09T13:52:41Z | `collectstatic --noinput` succeeded; provider listing showed `phase0-dogfood-static/dogfood/marker.txt` | PASS |
| Media upload/read | 2026-09-09T13:54Z | Django `Artifact` uploaded `dogfood/phase0-external-media.txt`; storage existence and read-back both succeeded | PASS |
| Provider-side object verification | 2026-09-09T14:06:44Z | Restricted app account listed the static and media objects from the external MinIO endpoint | PASS |
| Backup schedule idempotence | 2026-09-09T14:01Z | `backup-schedule` run twice; both completed with `Backup schedule installed` and one deterministic cron entry | PASS |
| Backup run/list | 2026-09-09T14:05Z | Explicit ID `b20260909T141152Z`; `backup-list` returned the same ID | PASS |
| DB + media same prefix | 2026-09-09T14:05:17Z | `djangoops-backups/phase0-dogfood/b20260909T141152Z/` contained `database.sql.gz`, `media.ready`, `media/dogfood/phase0-external-media.txt`, and `metadata.txt` | PASS |
| Mutation followed by restore | 2026-09-09T14:06Z | Mutation created `phase0-mutated-media`; explicit restore of `b20260909T141152Z` returned count 3 and `MUTATION_PRESENT False` | PASS |
| Writer restart and post-restore health | 2026-09-09T14:06Z–14:11Z | Web/Celery/Celery Beat were running after restore; human health was `HEALTHY`; JSON health was `overall_status: healthy` with all five checks passing | PASS |
| Source/recovery preservation | 2026-09-09T14:06:40Z | External recovery prefix preserved `recovery/pre-restore-20260909T140639Z/database.sql.gz` and both pre-restore media objects; local pre-restore DB artifact also remained | PASS |
| Corrupt restore rejection | 2026-09-09T14:09:50Z–14:10Z | Isolated ID `b20260909T141500Z` had an 18-byte invalid `database.sql.gz`; restore rejected it before writer stop, web/Celery start times stayed unchanged, and no corrupt recovery artifact was created | PASS |
| HTTPS after redeploy | 2026-09-09T14:11:38Z | HTTP returned `308` to HTTPS; HTTPS returned `200` from `djangoops.172-198-75-78.sslip.io` | PASS |
| Celery after restore | 2026-09-09T14:12Z | Task `9e853351-faa9-4a6b-ac1b-8d883953a561` returned result `3`; Beat remained running | PASS |

## Gate decision

The external S3-compatible acceptance gate is **PASS**. Same-VPS MinIO is smoke-test history
only. The temporary Azure storage resource group is scheduled for immediate deletion after this
evidence and the final green repository checks are saved; no production-storage claim is made.
