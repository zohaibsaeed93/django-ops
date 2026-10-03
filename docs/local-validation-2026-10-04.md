# Local validation — 4 October 2026

The control-plane dashboard runs at http://127.0.0.1:8000 using the development
database `.venv/djangoops-local.sqlite3`. A local operator account and a demo project
were created. The demo registration intentionally has no running agent; its offline
diagnostic result was used to verify the browser's failure handling.

## Results

| Check | Result |
| --- | --- |
| Python suite in Ubuntu WSL, Python 3.12.3 | 153 passed |
| Python suite on Windows, Python 3.12.15 | 152 passed, 1 skipped |
| Windows skipped integration | Requires Go and OpenSSL on PATH; the same integration passed in WSL |
| Ruff lint and formatting | Passed; 73 Python files formatted |
| Strict mypy | Passed; 61 source files |
| Django system checks | Passed; no issues |
| Migration consistency | Passed; no changes detected |
| Applying migrations to a local SQLite database | Passed |
| Browser sign-in, dashboard, sign-out, and offline diagnostic | Passed |
| Windows launcher on a separate port | Passed; login page returned HTTP 200 |
| CLI init and compose, outside the repository | Passed |
| Generated Compose file validation by Docker Compose | Passed |
| Go 1.23.12 tests and vet | Passed |
| Go race checks for Kubernetes, transport, and config | Passed |
| Go/Python TLS diagnostics, cancellation, reconnect, and rejection of invalid credentials/CA/protocol | Passed |
| Protobuf regeneration comparison | Passed; all four generated files match, ignoring checkout CRLF |
| Helm chart lint | Passed |
| Deterministic Helm render and opt-in telemetry render | Passed |
| Wheel installation outside the source checkout | Passed; Django startup, migrations, login, and dashboard render |
| Kind Kubernetes acceptance | Passed on retry after resolving the local clock conflict |

The packaging regression was also rerun after strengthening its assertion that the
imported control-plane module comes from the extracted wheel.

The live Kind test exercised credential rejection, cluster validation, deployment of
an immutable Django image, migration-before-activation, failed migration preventing
deployment, blocking rollback with unknown database compatibility, restoring a healthy
release with safe rollback, in-flight cancellation, durable replay after agent restart,
and requiring explicit recovery after an interrupted release. All assertions passed.

The first Kind attempt timed out while waiting for a successful migration pod's Job
to complete. Windows and WSL/Docker clocks differed by roughly seven minutes, and
the Ubuntu time service repeatedly undid Docker's clock synchronization. The retry
passed after temporarily pausing that service, aligning clocks, and restarting the
temporary node's kubelet. Ubuntu time synchronization was restored afterward.
The temporary Kind cluster, registry container, and agent operation-state file were
removed. Cached test images and development tools remain available locally.

## Fixes made

- Normalize the dogfood fixture's paths with `as_posix()` so its existing file check
  works on Windows.
- Package `controlplane`, including generated bindings, GraphQL code, migrations,
  and dashboard templates. Previously the distribution contained only the CLI,
  and importing the installed control-plane entry point outside the checkout failed.
- Add a wheel regression test that runs the packaged application outside the checkout.
- Add `scripts/run-local.ps1`, clarify dashboard versus combined TLS gateway startup
  in the README, and ignore the default local SQLite database.
- Save `uv.lock` so the tested dependency versions can be reproduced.

## Run again on this machine

From the repository in PowerShell:

```powershell
.\scripts\run-local.ps1
```

The server has already been left running on port 8000. Stop it before restarting on
that port, or choose another port with `-Port 8001`.

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ruff.exe format --check .
.\.venv\Scripts\mypy.exe cli/djangoops controlplane tests
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
```

Test-result XML files are in `.venv/test-results/`. A dashboard screenshot is saved
at `.venv/local-dashboard.jpg`. These local artifacts and the development database
are excluded from Git.

## Scope of the result

These checks validate the local dashboard, package, CLI behavior, agent protocol,
and the tested recovery and authorization cases. They do not establish production
readiness of a particular deployment. No external VPS, real S3 storage account,
public DNS, ACME certificate issuance, or production database backup/restore was
tested in this session because those environments and credentials were not supplied.
The existing SSH and backup tests use controlled fixtures rather than live infrastructure.

The dashboard-only launcher does not start the gRPC agent gateway. The combined
`djangoops-controlplane` process requires operator-provisioned TLS and agent token
configuration as documented in `docs/phase1-agent-channel.md`.
