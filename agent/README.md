# djangoops-agent

`djangoops-agent` is the outbound-only executor for Django-aware diagnostics and namespace-scoped Kubernetes release work. It opens no inbound listener. It verifies the control-plane TLS chain and hostname, authenticates inside TLS, sends liveness heartbeats, executes only typed allowlisted jobs beneath its configured project/credential roots, and reconnects with bounded exponential backoff and jitter.

Diagnostics report Django-specific health rather than arbitrary command output. Kubernetes execution retains durable idempotency/recovery and migration/rollback safety. Cancellation propagates into local contexts and stream loss cancels in-flight work fail-closed.

Phase 4 optionally enables bounded outbound OTLP export with `DJANGOOPS_AGENT_OTLP_ENDPOINT`. The endpoint cannot contain embedded credentials/query/fragment; exporter failures are side-band and never change job results. There is still no agent metrics/admin listener, reverse SSH, arbitrary shell/log-file RPC, or generic PaaS surface. See `docs/phase4-observability.md`.
