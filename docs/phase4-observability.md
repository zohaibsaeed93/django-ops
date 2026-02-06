# Phase 4 observability: Django-native correlation, Prometheus, and OpenTelemetry

DjangoOps Phase 4 integrates with operator-owned observability systems. DjangoOps does **not** deploy or operate Prometheus, Grafana, Loki, Tempo, Jaeger, or an OpenTelemetry Collector, and it does not add a proprietary metrics/log/trace store.

## Architecture and trust boundaries

Telemetry is best-effort side-band data. Diagnostic, deploy, backup/restore, and Kubernetes release admission, migration, rollback, cancellation, idempotency, and terminal states do not depend on telemetry delivery. Export failure cannot retry or duplicate product operations.

The control plane records only bounded operational metadata: a server-generated/bounded correlation identifier, allowlisted component and operation kind, allowlisted outcome, and bounded duration. Project IDs are used only in the in-process recent-event cache for authorized summaries; they are never Prometheus labels. User-controlled project names, hostnames, paths, exception text, operation IDs, and raw result text are never metric labels.

Diagnostic UUIDs and Kubernetes `operation_id` values are the existing operation identities and are reused as correlation IDs without changing idempotency semantics. Phase 0 deployment release IDs remain the durable deployment correlation handle; generated Compose and Helm workload environments additionally expose `DJANGOOPS_CORRELATION_ID` for application instrumentation.

## Prometheus scraping

The Django control plane exposes `GET /internal/metrics` in Prometheus text format. It requires an authenticated **staff** session; non-staff project members receive HTTP 403 and anonymous callers are redirected to login. Put the control plane behind the same trusted ingress/auth boundary used for administration, or scrape it through an authenticated internal proxy. There is no agent metrics listener.

Metrics use bounded labels only (`component`, operation `kind`, `outcome`) and aggregate gauges for registered/connected agents and in-flight diagnostics/releases. Telemetry drop and export-error counters make pressure and collector outages visible.

## OTLP export

Control-plane OTLP/HTTP JSON export is disabled by default. Set `DJANGOOPS_OTLP_ENDPOINT=https://collector.example.internal:4318` to enable it. The endpoint must be HTTP(S) and may not embed credentials, query parameters, or fragments. Export uses a bounded in-process queue, a one-second request timeout, and no retry loop: a failed export increments an error counter and the product operation continues.

The Go agent remains outbound-only. Optional `DJANGOOPS_AGENT_OTLP_ENDPOINT` enables its bounded OTLP exporter; invalid credential-bearing endpoint URLs fail configuration. The agent opens no metrics/admin listener. Collector authentication, when required, should be supplied outside repository-generated configuration by the operator's runtime/network policy; DjangoOps does not serialize collector credentials into Compose or Helm values.

DjangoOps uses W3C `traceparent` formatting for correlation helpers and standard OTLP resource/span structures. External collectors/backends remain the source of truth for telemetry retention and querying.

## Django and Celery workload wiring

Generated Compose services contain safe/off-by-default OTel environment wiring: `OTEL_SDK_DISABLED`, `OTEL_EXPORTER_OTLP_ENDPOINT`, deterministic `OTEL_SERVICE_NAME`/resource attributes, and `DJANGOOPS_CORRELATION_ID`. To opt in, install the standard OpenTelemetry Django/Celery instrumentation in the application image, provide an operator-owned collector endpoint at deployment time, and set `OTEL_SDK_DISABLED=false`. Collector credentials stay in the operator-owned runtime secret mechanism, not `djangoops.yaml` or generated Compose.

The Helm chart has an `observability` block with `enabled`, a credential-free `otlpEndpoint`, `serviceNamespace`, and a bounded `correlationId`. The control plane ignores client-supplied correlation IDs and injects the server-generated Kubernetes operation ID immediately before dispatch, so telemetry correlation cannot change values-digest idempotency. Existing `environmentSecretName` remains the place for runtime secrets.

A minimal fixture in `examples/phase4-telemetry/` shows standard Django request and Celery task instrumentation using the same service namespace/project metadata and propagated `DJANGOOPS_CORRELATION_ID`. No collector sidecar is required or installed by DjangoOps.

## Project-scoped operator surface

The dashboard shows connected/registered agents, recent diagnostics/releases, recent failures, and whether external OTLP export is configured. The typed GraphQL query is:

```graphql
query ProjectObservability($projectId: ID!) {
  observabilitySummary(projectId: $projectId) {
    totalAgents
    connectedAgents
    recentOperations
    recentReleases
    recentFailures
    otlpConfigured
    telemetryStatus
  }
}
```

Resolution starts from `Project.objects.filter(members=request.user)`, so a foreign project ID is not exposed. The surface intentionally has no PromQL proxy, trace store, log search, Kubernetes event browser, or shell.

Kubernetes release mutations additionally accept `observabilityEnabled`, `otlpEndpoint`, and `otelServiceNamespace` inside the existing typed release-values input. Endpoints are validated and secret-bearing URLs are rejected.

## Redaction, cardinality, backpressure, and retention

Structured telemetry replaces newlines, bounds every correlation/event value, and redacts secret-shaped key/value material before serialization. Prometheus labels are selected only from fixed allowlists. The control plane retains at most 256 recent in-process events by default and at most 256 pending export payloads; the agent exporter is similarly bounded. Queue saturation drops telemetry deterministically and increments a counter. There is no new database table for raw logs, metrics, or traces.

Because the recent summary is a cache, process restart simply loses recent in-process telemetry. Durable diagnostic/release rows remain authoritative for product state.

## Troubleshooting

If `djangoops_telemetry_export_errors_total` rises, verify collector DNS/TLS/routing and the configured OTLP HTTP endpoint. If `djangoops_telemetry_dropped_total` rises, the exporter is slower than event production; fix the collector/network rather than increasing cardinality or introducing unbounded buffering. Product operations should continue normally during either condition.

If workload traces are absent, verify the application image actually includes and initializes the standard Django/Celery OpenTelemetry instrumentation, `OTEL_SDK_DISABLED=false`, and the external collector is reachable. DjangoOps only injects the opt-in contract; it does not vendor instrumentation into user applications.

## Rollback / disable

Unset `DJANGOOPS_OTLP_ENDPOINT` and `DJANGOOPS_AGENT_OTLP_ENDPOINT`, leave Compose `OTEL_SDK_DISABLED` at its default `true`, and set Helm `observability.enabled=false`. The metrics endpoint and bounded local summary can remain without any external backend. Removing the Phase 4 hooks requires no data migration and does not change Phase 0 direct SSH, Phase 1 outbound agent trust, Phase 2 project authorization, or Phase 3 release/migration/rollback semantics.
