# Control plane

Phase 1 introduces only the secure agent transport/application boundary here. `AgentGateway` accepts an agent-initiated bidirectional gRPC stream over TLS, authenticates an agent with separately provisioned runtime credentials, rejects conflicting identities/protocols, tracks liveness, dispatches one typed diagnostics job, receives ordered events, and sends idempotent cancellation.

The gateway does not expose a dashboard, GraphQL API, remote shell, package manager, file browser, Kubernetes/Helm control, or generic container administration. TLS certificate/key and `DJANGOOPS_AGENT_TOKENS_JSON` are runtime secrets/configuration and must not be committed.

A lost stream marks the session disconnected and terminates server-side job observation; the agent independently cancels in-flight local work before reconnecting. Completed job IDs are never replayed automatically.
