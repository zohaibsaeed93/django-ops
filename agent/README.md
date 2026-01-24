# djangoops-agent

`djangoops-agent` is the Phase 1 outbound-only executor for Django-aware diagnostics. It opens no inbound listener. It verifies the control-plane TLS chain and hostname, authenticates inside TLS, sends liveness heartbeats, executes only the protobuf-defined diagnostics job beneath its configured DjangoOps project root, and reconnects with bounded exponential backoff and jitter.

Diagnostics report active release identity, declared/running Compose services, `manage.py check --deploy`, Django database connectivity/backend, pending migrations, Django/Python versions, and Celery/Celery Beat status when configured. Command stdout/stderr is never streamed verbatim; only bounded, parsed or controlled messages enter result frames.

Cancellation propagates into the Go context and kills the diagnostic subprocess process group. Stream loss cancels all local in-flight jobs fail-closed. Completed job IDs are remembered for the agent process lifetime and are rejected instead of being executed again after reconnect.

There is deliberately no arbitrary command RPC, reverse SSH, inbound agent port, dashboard/GraphQL, Kubernetes/Helm, or generic PaaS surface. Removing/stopping this agent leaves Phase 0 direct-SSH deploy/rollback and backup/restore untouched.
