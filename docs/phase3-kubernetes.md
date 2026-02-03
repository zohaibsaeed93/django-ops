# Phase 3 Kubernetes/K3s + Helm deployment path

DjangoOps Phase 3 targets an existing Kubernetes or K3s cluster. It does not provision clusters, expose a generic Kubernetes editor, or replace the Phase 0 direct-SSH fallback.

## Trust, credentials, and RBAC

A target belongs to one DjangoOps project, one outbound-only agent, one namespace, and one stable Helm release identity. The control plane stores only cluster metadata plus an opaque `credential_ref`; browser/GraphQL output never exposes kubeconfig, bearer tokens, client keys, or runtime secret contents. The baseline agent resolver accepts `file:<id>` references beneath `DJANGOOPS_KUBERNETES_CREDENTIAL_ROOT`; files must be non-world/group-readable. This makes the runtime file a secret-store mount point without putting credential material into Django models.

The cluster API URL must use HTTPS. Registration validates the agent's Kubernetes capability, API/namespace access and Helm availability before keeping a target. `docs/examples/phase3-agent-rbac.yaml` is the namespace-scoped baseline. Helm 3 stores release records as Secrets, so the agent needs namespaced Secret CRUD as well as Deployments, ReplicaSets, Pods, Services, ConfigMaps, Jobs, Events and Ingresses. Do not bind this identity to cluster-admin; namespace creation and cluster-scoped administration are intentionally excluded.

## Helm topology

`charts/djangoops` is the owned deployment contract. It renders a digest-pinned Django web Deployment/Service, optional Celery workers, optional singleton Celery Beat, a bounded pre-install/pre-upgrade migration Job, health probes, requests/limits and optional standards-based Ingress. TLS uses an existing Secret and ingress annotations can integrate with cert-manager. DjangoOps does not implement an ingress controller or CA.

Runtime settings, database credentials, Django secret key, and external S3-compatible static/media credentials come from an existing Kubernetes Secret reference. Static/media remain external S3-compatible storage; cluster-local media persistence is not the default.

## Lifecycle, migration, rollback, cancellation

The typed outbound control flow is validate target -> lint/render chart -> migration-hook + Helm apply -> rollout/health verification -> terminal result. The Helm migration hook has `backoffLimit: 0` and a bounded deadline; hook failure prevents application rollout. Values are allowlisted, deterministically encoded, and never contain plaintext credential fields. Application images are immutable `repository@sha256:digest` references.

Helm rollback is never DB rollback. DjangoOps records the previous immutable image, values digest and Helm revision. If rollout fails after migration while `migrationCompatibility` is `unknown`, the release ends `rollback_blocked`. When compatibility is explicitly `safe`, the agent performs a bounded `helm rollback` to the recorded prior revision and verifies the restored web rollout before returning `rolled_back`.

Release creation and the initial `validating` state are committed before any remote work begins. Long-running Helm/Kubernetes calls therefore run outside a Django database transaction, allowing an authenticated concurrent cancel request to observe and cancel the active operation. Terminal writes remain conditional on an active state, so late results cannot overwrite `cancelled` or another terminal status.

Control-plane retries for the same image/chart/values reuse the same durable release row and operation ID after an agent disconnect rather than minting a second migration identity. The agent additionally persists a bounded operation ledger at `<DJANGOOPS_PROJECT_ROOT>/.djangoops-agent-kubernetes.json` with mode `0600`. The ledger contains request fingerprints and bounded terminal metadata only, never credentials, Helm values, tokens, or kubeconfig contents. A completed operation is replayed from this ledger after reconnect or agent restart, so loss of the terminal network frame cannot rerun migration/Helm.

If an agent process disappears after durable admission but before a terminal result is persisted, the operation is retained as `in_flight`. A retry with the same operation ID returns `RECOVERY_REQUIRED` and **does not rerun migration or Helm automatically**. This is an intentional fail-closed boundary: inspect the namespace's Helm release/history and migration Job state, confirm whether database mutation or rollout is still active/completed, then either reconcile the observed release or start a new release only after the operator has established that duplicate migration is safe. Never delete the ledger entry merely to force a retry.

## Existing-cluster prerequisites

- Kubernetes/K3s API reachable from the outbound agent host.
- `kubectl` and Helm 3 available to the agent.
- Namespace pre-created with least-privilege RBAC and runtime Secret.
- Agent kubeconfig mounted beneath `DJANGOOPS_KUBERNETES_CREDENTIAL_ROOT`; the baseline resolver requires no group/world permission bits.
- `DJANGOOPS_PROJECT_ROOT` writable by the agent so the bounded `0600` operation ledger can be atomically persisted across reconnect/restart.
- Immutable application image (`repository@sha256:digest`).
- External PostgreSQL/Redis as applicable and external S3-compatible static/media storage.
- Optional ingress controller/cert-manager installed independently.

## Recovery and non-goals

A blocked rollback requires operator assessment of DB/schema compatibility; then either deploy a forward fix or explicitly mark a later release migration-safe. A failed credential/permission validation is fixed outside DjangoOps and target registration retried. `RECOVERY_REQUIRED` means the prior operation crossed durable admission without a replayable terminal result: inspect Helm history/status, migration Job/pod state, and application rollout before choosing a forward release. The Phase 0 direct-SSH runbook remains the break-glass fallback if Kubernetes or the agent path is unavailable.

Cluster bootstrap/lifecycle management, arbitrary Kubernetes administration, shell consoles, generic object browsing/editing, Coolify/Dokku adapters, marketplaces/buildpacks, and Phase 4 metrics/tracing/log aggregation are outside this baseline.
