# Control plane

The control plane owns authenticated project-scoped operations while preserving the existing outbound-only agent trust boundary. `AgentGateway` accepts an agent-initiated bidirectional gRPC stream over TLS, authenticates separately provisioned runtime credentials, tracks liveness, and dispatches only typed diagnostics/Kubernetes operations. TLS material and `DJANGOOPS_AGENT_TOKENS_JSON` remain runtime-only secrets.

Phase 4 adds best-effort observability without becoming a telemetry backend. Authenticated staff may scrape `/internal/metrics`; project members see only their own observability summary in the dashboard/typed GraphQL API. Optional `DJANGOOPS_OTLP_ENDPOINT` enables a bounded, non-retrying OTLP exporter. Metric labels are allowlisted and low-cardinality, recent events are bounded in process, and secrets/raw logs/environment dumps are never exported by these hooks.

Telemetry failure does not participate in operation admission or terminal-state decisions. Unset the OTLP endpoint to disable export with no migration. See `docs/phase4-observability.md`.
