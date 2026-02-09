# DjangoOps documentation

The Software Design & Decision Document is the roadmap and architecture authority. These repository docs describe the implemented product and its operational boundaries.

## Product phases

- [`phase0-dogfood.md`](phase0-dogfood.md) — single-VPS direct-SSH/Compose deployment, Django-aware health, backup/restore dogfood evidence, and the fallback path retained by later phases.
- [`phase1-agent-channel.md`](phase1-agent-channel.md) — outbound-only authenticated Go agent, TLS/protocol contract, bounded diagnostic execution, cancellation, reconnect semantics, and security boundary.
- [`phase2-dashboard-graphql.md`](phase2-dashboard-graphql.md) — authenticated Django dashboard plus project-scoped typed GraphQL control API over allowlisted operations.
- [`phase3-build-vs-integrate.md`](phase3-build-vs-integrate.md) — pre-Phase-3 build-vs-integrate ADR and explicit rejection of a generic PaaS control plane.
- [`phase3-kubernetes.md`](phase3-kubernetes.md) — existing-cluster Kubernetes/K3s registration, namespace-scoped access, DjangoOps-owned Helm releases, migration gates, cancellation, idempotency, rollback safety, and Kind acceptance.
- [`phase4-observability.md`](phase4-observability.md) — Django-native correlation, authenticated Prometheus metrics, optional bounded OTLP export, project isolation, redaction/cardinality/backpressure rules, and rollback.
- [`phase4-ecosystem.md`](phase4-ecosystem.md) — versioned allowlisted ecosystem contract, deterministic discovery, two built-in reference integrations, secret handling, project authorization, resource bounds, compatibility, disable/rollback, and anti-PaaS non-goals.

## Operational precedence

Later phases do not remove the Phase 0 recovery path. If the persistent agent/control-plane path is unavailable, operators can still use the documented direct-SSH deploy, health, backup, restore, and rollback workflow from the repository root README and Phase 0 documentation.

Security rules accumulate rather than replace each other: Phase 1 remains outbound-only, Phase 2 authorization begins from server-side project membership, Phase 3 remains namespace/release scoped with migration and rollback gates, and Phase 4 telemetry/ecosystem extensions remain bounded, redacted, optional, and unable to widen core execution authority.
