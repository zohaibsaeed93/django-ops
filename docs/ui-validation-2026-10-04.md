# Interface refinement and repeat validation — 4 October 2026

The application was restarted at `http://127.0.0.1:8000` after refining the dashboard
and sign-in screen. Both use shared semantic theme colors, indigo accents, clearer
typography, local SVG icons, and responsive layouts. The overview includes a labeled
sample release diagram, application metrics, release gates, and a guided tour.

The light/dark toggle follows the system on first visit and persists an explicit
choice in browser storage. It applies that choice before painting, shares it across
sign-in/demo/live pages, and remains functional if storage cannot be used. Native
controls retain keyboard access, visible focus, reduced-motion handling, and a
skip-to-content link that preserves the selected demo section.

## Behavior corrected or added

- Deploying a sample image no longer clears an existing database or worker failure.
  Database failure blocks the migration gate; a worker failure fails verification
  and remains visible after compatible application rollback.
- Corrupt sample backups are rejected at validation, before stopping writers or
  changing the dataset.
- Tour completion reflects completed exercises. Reset invalidates an in-flight
  sample request and clears the tour progress.
- The overview distinguishes the last diagnostic from the release/stack status.
- The live brand link preserves live mode. The setup page maps each capability to
  its actual dashboard, CLI, or GraphQL interface and explains external requirements.
- The small-phone release diagram has extra vertical spacing to keep its labels
  separate from the release in the center.

## Repeat automated validation

| Check | Result |
| --- | --- |
| Complete Python suite, Ubuntu WSL / Python 3.12.3 | 157 passed |
| Complete Python suite, Windows / Python 3.12.15 | 156 passed, 1 skipped |
| Windows skip | Go/OpenSSL-dependent TLS integration; passed in the Linux suite |
| Dashboard, authorization/CSRF, and wheel distribution regressions after final template changes | 17 passed |
| Ruff lint and formatting | Passed; 74 Python files formatted |
| Strict mypy | Passed; 62 source files |
| Django system checks | Passed; no issues |
| Migration consistency and local startup | Passed; no schema changes |
| Go 1.23.12 tests and vet | Passed |
| Go race checks, Kubernetes/transport/config | Passed |
| Protobuf regeneration | All four generated bindings match, normalizing checkout line endings |
| Generated Compose contract | Docker Compose validation passed with synthetic runtime placeholders |
| Helm lint, deterministic render, opt-in telemetry render | Passed |
| Real Go agent / TLS / Kind acceptance | Passed |
| JavaScript syntax and formatting | Parsed with Node; CSS/scripts formatted with Prettier |

The Kind run created an isolated cluster and loopback-only registry, rebuilt the
Django acceptance image, and used a namespace-scoped service account. It verified
credential rejection, target validation, migration before activation, failed
migration blocking activation, unsafe rollback blocking, compatible rollback,
cancellation, durable replay after agent restart, and explicit recovery after an
interrupted release. The temporary cluster, registry, credentials, and agent state
were removed afterward. Existing unrelated Docker containers were left running.

## Browser validation

Both themes were inspected at desktop, tablet, and phone widths, including 1440,
768, 390, and 320 pixels. The document had no horizontal page overflow; wider
capability tables scroll inside their cards. Small-phone diagram labels were
checked for overlap after the final adjustment.

The browser exercised healthy/database/worker diagnostics, successful release,
migration failure, safe and blocked rollback, cancellation before and after
activation, reset during a deployment, backup creation, latest-backup restore,
corrupt-backup rejection, preserved pre-restore data, and healthy/unresolved-health
restore outcomes. All four tour exercises reached completion only after running.
Theme changes persisted across reloads, workspace switches, and sign-out/in.
Failed sign-in feedback, successful sign-in, the architecture dialog, and keyboard
skip-to-content behavior were checked. No console errors or warnings were observed.

Local test XML, browser observations, and preview images are saved under `.venv/`
and excluded from Git.

## What this establishes

The local Python/Go behavior, packaged application, browser demonstrations, and
real Kubernetes release acceptance passed. The demo is still a browser simulation
and performs no real deployment, backup, restore, or project-record writes.

The current live workspace's registered agent is offline; the dashboard-only
launcher does not start the TLS gateway. Real VPS SSH operations, public DNS/ACME,
an external S3 account, production PostgreSQL/media recovery, and a configured
telemetry collector were not exercised against user infrastructure. Their code
paths have controlled automated coverage; the feature table does not claim that
the local demo provisions or verifies those external systems.
