# Exploring DjangoOps locally

The control plane has two clearly separated workspaces:

- **Interactive demo** is a browser simulation of a sample Django application.
  It explains the platform's decisions without connecting to infrastructure or
  changing project, operation, or release records. Sample state resets on reload.
- **Live workspace** shows only projects the signed-in user belongs to. Agent
  connectivity, diagnostic results, registered Kubernetes targets, release history,
  and observability summaries come from the existing server-side services.

## Start the demo

On Windows, install the development environment with `uv sync --group dev`, create
an operator account as described in the README, and run `scripts/run-local.ps1`.
The launcher enables `DJANGOOPS_WEB_DEMO=1`. The feature also requires Django debug
mode; a query parameter cannot turn it on in production. Authentication is still
required for both workspaces.

The shared theme toggle works in the demo, live workspace, and sign-in screen.
It follows the operating system on first visit and saves an explicit light/dark
choice in this browser. The saved preference is applied before painting and
survives navigation, sign-out, and reload. If browser storage is unavailable,
the toggle still works for the current page.

Use the four-step tour on the overview:

1. **Diagnostics:** compare a healthy application with a database or Celery failure.
   A database outage also prevents evaluating migration state. The result identifies
   the affected component and gives a next step.
2. **Deployments:** run a successful release, then simulate a failed migration.
   Migration failure prevents activation and keeps the previous release active.
3. **Rollback:** compare an unhealthy rollout with compatible and unknown database
   schemas. Compatible rollback restores the previous application; unknown
   compatibility blocks automatic rollback. Cancellation does not imply previously
   completed steps were undone.
4. **Recovery:** add sample data, optionally create a new recovery point, and restore
   the latest sample backup. The walkthrough shows validation, preservation of current
   state, database/media restore, writer restart, and health verification. Remaining
   release or service problems are not silently declared healthy after a data restore.
   Try the corrupt-backup scenario to see validation reject a restore before
   writers are stopped or the dataset changes.

The tour records completed exercises rather than merely visited sections. Reset
clears those markers and invalidates an in-flight sample request. Deploying a new
sample image does not repair an existing database or worker outage: the migration
or health gate still stops the release. "Last diagnostic" reports the last scan;
the release map separately shows whether the sample stack needs attention.

The **How DjangoOps works** guide explains the control plane, outbound authenticated
agent, and application environment. **Project setup** explains the transition to a
real VPS/Compose or existing Kubernetes target and points to the repository runbooks.
Its capability table explains which features have an interactive sample, which
remain CLI/API operations, and which need external infrastructure to verify.

## Live operations

The dashboard-only development server does not start the authenticated TLS gateway.
Registering an agent record does not launch an agent. Offline agents show connection
guidance rather than a button that predictably fails. Connected agents retain the
existing CSRF-protected diagnostic and cancellation controls.

Diagnostic check details are displayed as escaped, readable rows; technical result
payloads remain available in a disclosure. Active operations poll the existing status
endpoint with a bounded retry budget. Terminal results update the checks and remove
the cancellation control.

Kubernetes control remains in the existing typed GraphQL API. Direct SSH deployment,
database/media backup, and restore remain CLI operations. The demo does not add
browser endpoints that execute these workflows.

## Validation of the redesigned interface

The complete Windows Python suite passed with the three initial workspace-isolation
tests (155 passed, one Linux integration skipped). After adding the live-redirect
regression, the dashboard, authorization/CSRF, and distribution checks passed again.
Ruff, formatting, strict mypy, Django checks, and migration consistency also passed.

Browser checks exercised successful deployment, migration failure, compatible and
blocked rollback, cancellation, failed health diagnostics, sample backup creation,
restoring the newest backup, reset, workspace switching, the architecture guide,
and sign-in/out. Desktop and mobile layouts were inspected; the mobile document
had no horizontal page overflow. The demo uses no frontend framework, CDN fonts,
or external JavaScript assets. Shared styles and scripts ship with the existing
template package and work with both development and combined control-plane startup.
