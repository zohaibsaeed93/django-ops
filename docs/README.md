# Documentation

Operational, architecture, and user documentation for DjangoOps lives here. The Software Design & Decision Document remains the roadmap and architecture authority while the project is being built.

## Architecture decisions

- [`phase3-build-vs-integrate.md`](phase3-build-vs-integrate.md) — mandatory pre-Phase-3 Coolify/Dokku build-vs-integrate evaluation, ownership matrix, security/rollback consequences, and Phase 3 entry gate.

## Operations

- [`phase4-observability.md`](phase4-observability.md) — Django-native correlation, authenticated Prometheus exposition, optional bounded OTLP export, Django/Celery wiring, project isolation, redaction/cardinality rules, and rollback.
