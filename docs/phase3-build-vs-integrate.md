# Phase 3 build-vs-integrate decision: Coolify, Dokku, or DjangoOps-owned Kubernetes

Status: **ACCEPTED FOR REVIEW**  
Decision date: 2026-02-02  
Evidence refreshed: 2026-09-10  
Applies to: Phase 3 entry gate

## Decision summary

DjangoOps will **build and own the Phase 3 Django-specific orchestration layer directly on Kubernetes/K3s and Helm**, while **reusing Kubernetes, K3s, Helm, ingress/TLS controllers, container registries, and standard storage primitives as infrastructure**. DjangoOps will **not make Coolify or Dokku a required control-plane dependency** for the Phase 3 baseline.

Coolify and Dokku remain valid external deployment products, but neither is selected as DjangoOps's primary Phase 3 runtime/control plane. Their strongest capabilities overlap the generic-PaaS layer that DjangoOps deliberately does not want to own, while introducing a second source of deployment state, authorization, rollback semantics, and privileged host/platform access. That duplication would dilute DjangoOps's product identity: Django-aware migrations, static/media handling, Celery topology, backup/restore, health diagnostics, project-scoped authorization, deterministic cancellation, and the already-proven Phase 0/1/2 trust model.

The approved Phase 3 direction is therefore a narrow hybrid in the architectural sense: **Build the DjangoOps product semantics; reuse standard Kubernetes ecosystem primitives; reject Coolify/Dokku as mandatory orchestration dependencies.** Optional adapters may be reconsidered later only as explicitly bounded integrations after the core Phase 3 model is stable.

## Problem statement

Phase 3 introduces Kubernetes/Helm as an additional deployment target without turning DjangoOps into a generic PaaS. The architecture must answer who owns deployment state, cluster connectivity, workload packaging, ingress/TLS, runtime configuration, logs/cancellation, rollback, persistence, and project authorization.

The decision must preserve these existing invariants:

- Phase 0 direct SSH remains a supported fallback and rollback path for single-VPS deployments.
- Phase 1's outbound-only authenticated Go agent remains the only persistent remote execution channel; no arbitrary inbound command listener is introduced.
- Phase 2's authenticated Django dashboard and project-scoped typed GraphQL API remain the operator control plane.
- Browser/API clients never connect directly to an agent, node, Docker socket, or Kubernetes API.
- Remote operations remain typed, allowlisted, bounded, redacted, cancellable, and project-scoped.
- Django-specific diagnostics and lifecycle semantics remain the product moat.

### Non-goals

Phase 3 is **not** an attempt to build another Heroku, Coolify, Dokku, generic container dashboard, generic Kubernetes control plane, or arbitrary remote shell. It is not a replacement for cloud-provider cluster lifecycle products. It does not remove the Phase 0 single-VPS path, and it does not make the Phase 1 agent optional for the persistent managed execution model.

This decision document contains no Phase 3 implementation code, manifests, charts, platform installers, or adapters.

## Evidence baseline

The evaluation below uses primary upstream documentation and current product material retrieved on **2026-09-10**.

### Coolify

Current documentation describes Coolify as an open-source self-hostable PaaS/control plane that connects to servers over SSH, prepares Docker hosts, and coordinates builds, deployments, domains, HTTPS, health checks, logs, storage, and lifecycle operations. Connected servers remain standard Docker hosts and already-running workloads continue when the Coolify control plane is unavailable.

For Docker Compose, Coolify supports repository-backed Compose applications and stored Compose services. In the normal path it parses and can normalize or add container names, labels, volume names, networks, environment references, and proxy configuration before writing a deployable Compose definition. A raw Compose mode exists for repository-backed applications, but then the operator owns proxy labels/networking and related behavior.

Coolify provides dashboard/API/CLI automation and deploy webhooks. Current multi-server documentation notes that the application's Servers page does **not** support Docker Compose applications and prevents another server when persistent storage is configured. Current destination documentation also marks Docker Swarm destinations deprecated. The reviewed current documentation does not establish a first-class Kubernetes/K3s/Helm application path equivalent to the Phase 3 target, so that capability is treated as **UNVERIFIED / SPIKE REQUIRED**, not assumed.

Coolify's current changelog identifies v4.3.0 as the recent release line and notes API authorization/behavior changes in v4.2.0, including read-only Member roles and POST-only state-changing endpoints. Coolify states that its projects are Apache-2.0 licensed and open source.

Primary sources:

- https://next.coolify.io/docs/core/what-is-coolify
- https://next.coolify.io/docs/core/build-deployment-model
- https://next.coolify.io/docs/applications/builds/docker-compose
- https://next.coolify.io/docs/services/configuration/docker-compose
- https://next.coolify.io/docs/core/infrastructure/scaling/multi-server-deployments
- https://next.coolify.io/docs/core/networking/destinations/overview
- https://next.coolify.io/docs/cli/what-is-the-coolify-cli
- https://next.coolify.io/docs/core/automation/deploy-webhooks
- https://coolify.io/changelog
- https://coolify.io/philosophy

### Dokku

Current Dokku material identifies Dokku v0.38.27 and describes it as a Docker-powered Heroku-like PaaS. Its architecture is plugin-driven across receive/build/release/deploy stages with file-based state. Commands are exposed locally and over SSH; current documentation does not present a general REST control API equivalent to DjangoOps's typed GraphQL boundary.

Dokku now includes a first-class K3s scheduler path. It can initialize K3s, add server/worker nodes over SSH, use nginx or Traefik ingress, manage TLS integration, deploy applications through Helm-backed resources, interact with an external Kubernetes cluster through a configured kubeconfig, and expose deployment settings including timeout and rollback-on-failure. K3s bootstrap requires privileged/root operations, and the initial Dokku server remains a deployment/control dependency whose state must be backed up/restorable.

Dokku v0.38 adds structured build records with live output and cancellation. The storage plugin now has scheduler-aware named storage and can provision K3s PVC/PV resources. Dokku's plugin architecture is extensible, but third-party/core plugin versioning and migration behavior create an additional upgrade surface. Dokku Pro exists alongside the open-source product, so any recommendation must avoid assuming Pro-only UI/API capabilities are present in the open-source baseline.

Primary sources:

- https://dokku.com/
- https://dokku.com/docs/development/architecture/
- https://dokku.com/docs/deployment/schedulers/k3s/
- https://dokku.com/docs/advanced-usage/builds/
- https://dokku.com/docs/advanced-usage/persistent-storage/
- https://dokku.com/docs/advanced-usage/plugin-management/
- https://dokku.com/docs/development/plugin-creation/
- https://dokku.com/docs/deployment/remote-commands/
- https://dokku.com/docs/deployment/zero-downtime-deploys/
- https://dokku.com/docs/configuration/ssl/
- https://dokku.com/docs/appendices/0.38.0-migration-guide/

## Options

### Option A — Build DjangoOps Phase 3 directly on Kubernetes/K3s + Helm

DjangoOps owns its typed deployment contract, Django-aware release sequencing, project authorization, agent command set, release records, health semantics, logs/cancel semantics, rollback policy, and dashboard/GraphQL exposure. It reuses Kubernetes/K3s as the scheduler/runtime, Helm as the packaging/release primitive, standard ingress/TLS controllers for routing/certificates, registries for immutable images, and Kubernetes storage/secrets/config primitives under least-privilege service accounts.

This is not "build Kubernetes." DjangoOps would build the product-specific adapter and policy layer that maps a Django project to reviewed charts/manifests and bounded operations.

### Option B — Integrate primarily with Coolify

DjangoOps delegates generic deployment/server orchestration to Coolify and adapts its Django control plane to Coolify's resources/API. This reduces some Docker-host lifecycle work but makes Coolify's resource model and deployable Compose mutation part of DjangoOps's production semantics. Its current Kubernetes/K3s/Helm fit for the Phase 3 target is not established by reviewed primary documentation.

### Option C — Integrate primarily with Dokku

DjangoOps delegates app build/release/deploy/K3s orchestration to Dokku and drives Dokku over SSH/commands or a narrow adapter. Dokku has the strongest verified Kubernetes/K3s overlap of the two external products, including Helm-backed K3s scheduling, build tracking/cancel, ingress/TLS, PVC handling, and rollback settings. The cost is a second PaaS deployment state machine and a privileged Dokku host/control dependency.

## Capability and risk comparison

| Criterion | A. DjangoOps-owned K8s/Helm | B. Coolify-primary | C. Dokku-primary |
| --- | --- | --- | --- |
| Deployment/runtime model | Direct typed DjangoOps release model mapped to Kubernetes objects/Helm releases. | Coolify resource/deployment queue mapped primarily to Docker resources on connected servers. | Dokku receive/build/release/deploy pipeline mapped to docker-local or K3s scheduler. |
| Docker Compose compatibility/mutation | Preserve Phase 0 Compose as fallback; Phase 3 uses a separate explicit Helm/K8s target. No hidden Compose rewriting. | Strong Compose support, but normal mode parses/mutates deployable Compose; raw mode shifts proxy/network ownership back to caller. | Compose is not the primary Dokku application contract; migration from DjangoOps Compose semantics would require translation/repackaging. |
| Kubernetes/K3s/Helm path | Native target; DjangoOps owns only its charts/policies, not cluster internals. | **UNVERIFIED / SPIKE REQUIRED** from reviewed current docs; Swarm is deprecated and multi-server Compose has limitations. | Verified first-class K3s scheduler with Helm-backed resources and external kubeconfig support. |
| Remote-node connectivity/trust | Preserve outbound-only agent; cluster credentials stay server-side and project-scoped. | Coolify connects to hosts over SSH and manages Docker; introduces another host trust/control plane. | Dokku initializes/adds K3s nodes over SSH and requires privileged operations; initial Dokku server is a control dependency. |
| API/automation surface | Existing typed GraphQL remains public API; internal agent protocol stays allowlisted. | REST API/CLI/webhooks are capable but create a second authorization/resource API to reconcile. | Command/SSH and plugin interfaces are strong; no reviewed general REST API equivalent to DjangoOps GraphQL. |
| Live logs/cancel/health | Keep current deterministic operation state machine; extend with K8s watch/log APIs through bounded agent operations. | Deployment logs/health exist, but DjangoOps would need to translate Coolify states/cancel semantics. | v0.38 build records support live output/cancel; app/container health semantics still need translation into DjangoOps's contract. |
| Django migrations | First-class DjangoOps-owned preflight/migrate sequencing and failure reporting. | Generic hooks/tasks would require Django-specific orchestration layered on top. | Could use release/deploy hooks or one-off commands, but DjangoOps-specific safe migration semantics would still need an adapter. |
| Static/media | Preserve external object-storage contract and Django settings integration. | Can manage environment/storage generically, but Django-specific static/media policy remains DjangoOps work. | Generic config/storage can support apps, but Django-specific static/media separation remains DjangoOps work. |
| Celery workers/beat | Explicit chart process roles with Django-aware validation and resource policy. | Compose/service components can represent workers; Kubernetes target remains unverified. | Procfile/process types and K3s formations fit workers, but DjangoOps must still own Celery-specific validation/diagnostics. |
| Backup/restore | Preserve DjangoOps external-S3 backup identity, preflight, quiesce, restore, and recovery semantics. | Coolify has database backup features, creating overlapping backup ownership and a risk of split restore semantics. | Datastore/storage plugins can help, but DjangoOps's coordinated DB+media restore contract would remain separate. |
| Secrets/TLS | Kubernetes Secrets/config via least-privilege server-side credentials; ingress cert-manager/standard controller integration. No browser exposure. | Coolify manages env/TLS and API tokens; adds another secret store and privileged server relationship. | Dokku config/certs/K3s TLS are mature but add another secret/config authority on the Dokku host/cluster. |
| Rollback | DjangoOps records app release + migration compatibility; Helm rollback only when policy says safe; Phase 0 remains explicit fallback. | Requires reconciling Coolify deployment rollback/redeploy semantics with DjangoOps DB migration policy. | K3s scheduler has rollback-on-failure and Helm releases, but DjangoOps still must prevent unsafe DB-schema rollback claims. |
| Multi-project authorization/isolation | Existing Django membership is authoritative; map projects to namespaces/service accounts/labels and enforce server-side. | Coolify teams/projects/environments add a second RBAC model and potential authorization drift. | Dokku apps and cluster namespaces are not a drop-in replacement for DjangoOps project membership; adapter must enforce both. |
| Persistence/state ownership | Django DB owns product state; Kubernetes owns runtime desired/observed state; Helm owns package release state. Boundaries are explicit. | DjangoOps DB + Coolify DB/config + Docker state creates three-way reconciliation. | DjangoOps DB + Dokku file/plugin state + K3s/Helm state creates multi-owner reconciliation. |
| Observability hooks | Add only when roadmap permits; use K8s events/status as operational inputs without prematurely building an observability product. | Coolify monitoring exists but would couple future observability to its model. | Dokku logs/build records are useful inputs, but persistent observability remains external. |
| Extensibility/plugin model | Typed DjangoOps capability additions; Kubernetes standards remain extension boundary. | Coolify API/resource ecosystem; DjangoOps-specific extension still custom. | Strong plugin/trigger model, but plugin lifecycle becomes part of DjangoOps support burden. |
| Upgrade/migration burden | Own DjangoOps charts/API compatibility; Kubernetes/K3s/Helm upgrades remain explicit infrastructure dependencies. | Must track Coolify breaking API/resource/Compose behavior plus DjangoOps compatibility. | Must track Dokku core, K3s scheduler, plugins, Helm rendering, and migration guides plus DjangoOps compatibility. |
| Operational complexity | One DjangoOps control plane + outbound agent + cluster. Lowest number of product state machines, but requires careful K8s expertise. | DjangoOps + Coolify + Docker hosts/clusters. Simpler generic host UX, higher control-plane duplication. | DjangoOps + Dokku control host + K3s + plugins. Strong capability but highest overlapping orchestration ownership. |
| License/dependency risk | Kubernetes/K3s/Helm are replaceable standard dependencies; adapter remains DjangoOps-owned. | Apache-2.0 open-source project; version/API behavior still a product dependency. | Open-source core plus optional Pro surface; plugin/version compatibility adds dependency risk. |
| Testability | Deterministic contract tests against rendered Helm and ephemeral K3s; fault injection can target DjangoOps's own state transitions. | Requires Coolify fixture/version matrix and API/resource reconciliation tests. | Requires Dokku/K3s/plugin/version fixture matrix in addition to DjangoOps tests. |
| Exit/reversibility | High: workloads remain standard images, Helm/K8s resources, external S3; Phase 0 fallback remains supported. | Moderate: workloads are standard Docker, but Coolify resource state/mutations must be unwound or exported. | Moderate: standard Docker/K3s underneath, but Dokku app/plugin/file state and generated Helm resources must be migrated. |

## Phase 3 ownership matrix

Decision meanings:

- **Build** — DjangoOps owns the product-specific behavior and contract.
- **Reuse** — use a standard infrastructure primitive directly without wrapping it into a competing product.
- **Integrate** — consume a bounded external API/component while DjangoOps stays authoritative.
- **Reject** — do not make this external product/capability part of the required Phase 3 baseline.

| Phase 3 surface | Decision | Ownership boundary |
| --- | --- | --- |
| Cluster bootstrap | Reuse / Integrate | Prefer existing managed Kubernetes or standard K3s provisioning. DjangoOps may register a cluster but does not become a cloud/cluster installer in the baseline. A tightly bounded K3s bootstrap helper may be a later deliverable only if required by the design gate. |
| Cluster registration | Build | DjangoOps stores project/cluster association and credential reference metadata; secret kubeconfig/token material remains server-side and least-privileged. |
| Workload packaging | Build + Reuse | DjangoOps owns Django-aware values/schema and release contract; reuse OCI images and Helm packaging conventions. |
| Kubernetes manifests/Helm | Build + Reuse | DjangoOps owns reviewed chart/templates for its supported topology; reuse Helm/Kubernetes APIs, not another PaaS's generated model. |
| Deploy orchestration | Build | Typed operation from Django control plane through outbound agent to Helm/Kubernetes; bounded queue, timeout, cancel, deterministic terminal states. |
| Ingress/TLS | Reuse / Integrate | Reuse ingress-nginx or Traefik plus cert-manager/standard TLS primitives. DjangoOps configures only the supported subset. |
| Secrets/config | Reuse + Build policy | Reuse Kubernetes Secret/ConfigMap mechanisms; DjangoOps owns validation, references, redaction, and project authorization. Never expose raw secrets to browser clients. |
| Migrations | Build | Django-aware migration plan/preflight/apply ordering and failure policy remain DjangoOps-owned. |
| Celery workers/beat | Build | DjangoOps owns supported process topology, resource defaults/limits, health expectations, and rollout coupling. |
| Static/media integration | Build + Integrate | Preserve DjangoOps external object-storage contract; integrate S3-compatible service through application configuration, not cluster-local object storage. |
| Health/diagnostics | Build | Extend Django-specific checks to Kubernetes context while preserving typed results and product semantics. |
| Logs/cancel | Build + Reuse | Reuse Kubernetes pod logs/watch/delete/cancel primitives; DjangoOps owns authorization, bounds, redaction, operation IDs, and monotonic state transitions. |
| Rollback | Build + Reuse | Reuse Helm rollback/revision mechanics only behind DjangoOps's migration-aware safety policy. |
| Backups | Build + Integrate | Keep project-scoped database/media backup/restore semantics and external S3 destination. Kubernetes is execution environment, not backup authority. |
| Dashboard/GraphQL | Build | Phase 2 remains the operator surface; Phase 3 adds typed fields/mutations without exposing generic Kubernetes CRUD. |
| Coolify primary deployment control | Reject | Not required in Phase 3 baseline. Optional future adapter only if a concrete user need justifies dual-state complexity. |
| Dokku primary deployment control | Reject | Not required in Phase 3 baseline despite strong K3s capability. Optional future adapter only behind a stable DjangoOps contract. |
| Arbitrary shell / generic kubectl / Docker socket API | Reject | Not exposed through DjangoOps public API or agent protocol. |

## Why not Coolify-primary

Coolify is strongest where DjangoOps intentionally wants to remain narrow: generic application/server/database deployment, dashboard operations, environment management, proxying, and PaaS ergonomics. Making it primary would create overlapping product surfaces and two operator control planes.

The Compose overlap is also not free. DjangoOps Phase 0 deliberately generates a known Compose topology with Django-specific safety guarantees. Coolify's normal Compose path may add or normalize labels, names, networks, volumes, environment references, and proxy configuration. Raw Compose avoids much of that but then removes much of the value of delegating to Coolify. More importantly, reviewed current docs do not verify the required first-class Kubernetes/K3s/Helm path for Phase 3. That gap is enough to reject Coolify as the baseline dependency without claiming the capability is impossible.

**UNVERIFIED / SPIKE REQUIRED:** if a future Coolify release introduces a stable Kubernetes target, evaluate only a bounded adapter against the then-current API and permission model. Do not pre-commit the Phase 3 architecture to that possibility.

## Why not Dokku-primary

Dokku is technically closer to the Phase 3 target. Its K3s scheduler, Helm-backed resources, build tracking/cancellation, TLS, storage, process formations, and plugin architecture are substantial and verified. If DjangoOps were trying to become a generic PaaS quickly, Dokku would be the stronger integration candidate.

That is also the reason not to make it the baseline. DjangoOps would inherit a second deployment state machine that already owns build/release/deploy, app state, config, TLS, storage, scheduler state, and Helm rendering. DjangoOps would then need to translate and reconcile those semantics into its own project-scoped operation records, migration safety, backup identity, GraphQL authorization, cancellation, and rollback policy.

The trust boundary would also expand. K3s initialization is root-level, cluster nodes are joined through SSH, and the initial Dokku host remains a deployment/control dependency. Plugin management and version migrations become part of DjangoOps's supported production matrix. These costs are justified for a standalone PaaS, but not for a Django-specific product whose Phase 1 already supplies a narrower outbound execution channel.

A future optional Dokku adapter remains possible once DjangoOps's Phase 3 contract is stable. Such an adapter must treat Dokku as an implementation backend, never as an authorization authority, and must not require browser-to-Dokku access or generic SSH commands.

## Security consequences

The selected approach keeps the smallest privilege footprint compatible with Kubernetes deployment:

1. **No inbound arbitrary command surface.** The agent remains outbound-only and exposes only versioned allowlisted operations.
2. **No browser-to-agent or browser-to-cluster path.** Browser/GraphQL requests terminate in Django, which performs project authorization before dispatch.
3. **Least-privilege cluster credentials.** Credentials are server-side, scoped to the minimum namespaces/resources/verbs required. Cluster-admin kubeconfigs are not normal runtime credentials.
4. **No public generic kubectl/exec API.** Phase 3 operations are purpose-built deploy, status, health, logs, cancel, rollback, backup, and restore contracts.
5. **Bounded operations.** Queue sizes, log/result bytes, watch durations, retries, timeouts, and concurrency remain bounded with fail-closed/backpressure behavior.
6. **Redaction.** Kubernetes events, pod logs, Helm output, and errors pass through redaction/size limits before persistence or GraphQL exposure.
7. **Server-side project authorization.** Project membership remains authoritative in Django; namespaces/labels are defense in depth, not the authorization source.
8. **Docker socket/root-equivalent dependencies are avoided by default.** Unlike a generic Docker PaaS control plane, the normal Phase 3 agent should use constrained Kubernetes credentials. Any future bootstrap operation requiring root, SSH, or a Docker/container-runtime socket must be separate, explicit, short-lived, audited, and never reachable as generic user input.
9. **External object storage remains independent.** Static/media/backups stay in external S3-compatible storage with project-scoped credentials; Phase 3 does not collapse them into cluster-local persistence.

## Persistence and state ownership

To avoid split-brain orchestration, Phase 3 defines three explicit authorities:

- **DjangoOps database:** projects, memberships, registered targets, operation records, release intent/history, bounded result summaries, policy metadata.
- **Kubernetes API:** desired and observed runtime state for namespaced workloads and services.
- **Helm release state:** package revision/rendered release history used as a deployment primitive.

DjangoOps reconciles its release intent with Kubernetes/Helm state through typed operations. It does not mirror every Kubernetes object into its database and does not let Helm or Kubernetes replace Django project authorization.

External S3 remains authoritative for project backup objects and configured media/static data. Database state remains application data, not deployment metadata.

## Migration and rollback strategy

### Phase 0 single-VPS -> Phase 3

Migration is opt-in per project. A Phase 0 deployment remains valid while a Phase 3 target is prepared.

A future migration workflow must:

1. validate a registered cluster target and least-privilege credentials;
2. render/validate the DjangoOps Helm release without mutating the existing VPS;
3. verify external S3 static/media/backup access from the new runtime;
4. provision/validate PostgreSQL/Redis strategy according to the supported Phase 3 topology;
5. run migration preflight and Django deployment checks through typed operations;
6. deploy the new release behind a non-production route or controlled cutover;
7. switch traffic only after health succeeds;
8. retain the prior VPS release/config long enough for an operator-approved fallback window.

The exact data-migration technique is a later implementation deliverable because managed database versus in-cluster database support changes the procedure. Phase 3 must never imply that `helm rollback` reverses arbitrary Django schema changes.

### Phase 3 -> Phase 0 fallback

The Phase 0 direct-SSH path remains documented and supported. Falling back requires a compatible application revision, a database state compatible with that revision, existing external S3 media/static, and separately provisioned Phase 0 runtime secrets. If Phase 3 has applied irreversible migrations, DjangoOps must stop and require operator-directed database restore/forward-fix rather than claim an automatic rollback.

The Phase 1 agent can be disabled or removed without deleting the Phase 0 path. Likewise, reverting this architecture decision is a documentation-only Git revert; this PR performs no runtime state migration.

## Phase 3 implementation boundary

After this decision is reviewed and integrated, Phase 3 should be decomposed into a small number of coherent product deliverables rather than file-level microtasks:

1. **Kubernetes target registration and trust boundary.** Project-scoped target model, secret reference strategy, namespace/service-account permission contract, outbound-agent capability negotiation, and secure connectivity validation.
2. **DjangoOps Helm packaging and deterministic rendering.** Supported Django/PostgreSQL/Redis/Celery topology, resource/security defaults, ingress/TLS integration, external S3 configuration, values schema, render validation, and upgrade compatibility policy.
3. **Agent-backed deploy/rollback operation model.** Typed deploy/status/log/cancel/rollback operations, migration preflight/apply sequencing, Helm revision handling, bounded concurrency, failure recovery, and monotonic operation persistence.
4. **Kubernetes-aware Django health and recovery.** Django deployment checks, DB/migration state, pod/workload readiness, Celery process health, diagnostic redaction, and operator-safe failure guidance.
5. **Phase 3 dashboard/GraphQL exposure and end-to-end dogfood.** Project-authorized target/release/status operations through the existing Phase 2 control plane, plus a real cluster acceptance run demonstrating deploy, migration failure behavior, cancellation, rollback constraints, backup/restore continuity, and Phase 0 fallback.

These are roadmap boundaries only. No Phase 3 implementation begins in this decision PR.

## Decision consequences

### Positive

- DjangoOps keeps one product control plane and one project authorization model.
- The Django-specific lifecycle remains first-class instead of being translated through a generic PaaS.
- Kubernetes/Helm are used as portable standards, reducing vendor exit cost.
- The Phase 0 fallback and Phase 1 outbound trust model remain intact.
- Testing can focus on DjangoOps contracts against ephemeral K3s/Kubernetes rather than a matrix of third-party PaaS versions.
- Privilege can be constrained to namespace/service-account operations instead of granting normal runtime access to a Docker socket or generic root SSH channel.

### Negative

- DjangoOps must own and test its Helm charts, Kubernetes adapter behavior, upgrade policy, and cluster failure handling.
- Operators need a supported Kubernetes/K3s target; DjangoOps will not initially provide the broad server-management UX of Coolify or Dokku.
- Some capabilities available in mature PaaS products must be deliberately rebuilt only when they are DjangoOps requirements.

### Risks and mitigations

- **Risk: accidental generic-Kubernetes scope creep.** Mitigation: only typed Django operations enter the public API; generic CRUD/exec stays out of scope.
- **Risk: unsafe schema rollback.** Mitigation: migration-aware release metadata and explicit refusal to equate Helm rollback with database rollback.
- **Risk: cluster credential escalation.** Mitigation: namespace-scoped service accounts, explicit verb/resource allowlists, server-side secret storage, and tests asserting denied operations.
- **Risk: drift between DjangoOps and live cluster state.** Mitigation: reconcile only product-owned labeled resources and report drift rather than silently adopting unrelated objects.
- **Risk: ecosystem churn.** Mitigation: pin tested Kubernetes/K3s/Helm version ranges and keep the adapter boundary narrow.

## Phase 3 entry gate

**Phase 3 implementation is blocked until Reviewer posts `AUTONOMOUS REVIEW: PASS` on the implementation PR for this decision and this exact decision commit is integrated into `main`.**

Until that happens, no Kubernetes manifests, Helm charts, Coolify/Dokku adapters, cluster bootstrap code, or other Phase 3 runtime implementation may be added.
