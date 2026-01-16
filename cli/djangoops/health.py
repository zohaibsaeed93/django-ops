"""Read-only Phase 0 Django health diagnostics over direct SSH."""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Final

from djangoops.compose import COMPOSE_FILENAME
from djangoops.deploy import DeploymentTarget, _ssh_argv

SCHEMA_VERSION: Final = 1
_CHECK_NAMES: Final = (
    "active_release",
    "compose_services",
    "django_deploy_check",
    "database_connectivity",
    "pending_migrations",
)


class HealthTransportError(RuntimeError):
    """Health evaluation could not be completed over the transport."""


@dataclass(frozen=True, slots=True)
class HealthCheck:
    """One deterministic, secret-free diagnostic result."""

    name: str
    status: str

    @property
    def healthy(self) -> bool:
        return self.status == "pass"


@dataclass(frozen=True, slots=True)
class HealthReport:
    """Typed health report rendered by both human and JSON CLI modes."""

    checks: tuple[HealthCheck, ...]

    @property
    def healthy(self) -> bool:
        return all(check.healthy for check in self.checks)

    @property
    def overall_status(self) -> str:
        return "healthy" if self.healthy else "unhealthy"

    def to_json(self) -> str:
        payload = {
            "checks": [{"name": check.name, "status": check.status} for check in self.checks],
            "overall_status": self.overall_status,
            "schema_version": SCHEMA_VERSION,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def health_project(
    target: DeploymentTarget,
    compose_file: str = COMPOSE_FILENAME,
) -> HealthReport:
    """Evaluate the active release without mutating remote state."""
    validated_compose_file = _validate_compose_file(compose_file)
    command = _health_remote_command(target.remote_base, validated_compose_file)
    try:
        result = subprocess.run(
            _ssh_argv(target, command),
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError as exc:
        raise HealthTransportError("unable to execute the system OpenSSH client") from exc
    if result.returncode != 0:
        raise HealthTransportError("health evaluation failed over SSH")
    return _parse_report(result.stdout)


def _validate_compose_file(value: str) -> str:
    if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Compose file path contains control characters")
    path = PurePosixPath(value)
    if path.is_absolute():
        raise ValueError("Compose file path must be project-relative")
    if any(part in {".", ".."} for part in path.parts):
        raise ValueError("Compose file path must not contain '.' or '..' segments")
    return path.as_posix()


def _parse_report(output: str) -> HealthReport:
    results: dict[str, str] = {}
    for raw_line in output.splitlines():
        parts = raw_line.split("\t")
        if len(parts) != 3 or parts[0] != "DJANGOOPS_HEALTH":
            continue
        name, remote_status = parts[1], parts[2]
        valid = remote_status in {"PASS", "FAIL"}
        if name in _CHECK_NAMES and name not in results and valid:
            results[name] = "pass" if remote_status == "PASS" else "fail"
    if tuple(results) != _CHECK_NAMES:
        raise HealthTransportError("remote health response was incomplete or invalid")
    checks = tuple(HealthCheck(name, results[name]) for name in _CHECK_NAMES)
    return HealthReport(checks)


def _health_remote_command(remote_base: str, compose_file: str) -> str:
    base = shlex.quote(remote_base)
    current = shlex.quote(f"{remote_base}/current")
    compose = shlex.quote(compose_file)
    db_probe = shlex.quote("from django.db import connection; connection.ensure_connection()")
    return (
        "set -u; "
        'emit() { printf \'DJANGOOPS_HEALTH\\t%s\\t%s\\n\' "$1" "$2"; }; '
        f"release=$(readlink -f -- {current} 2>/dev/null || true); "
        f'case "$release" in {base}/releases/*) '
        'if test -d "$release"; then active=1; emit active_release PASS; '
        "else active=0; emit active_release FAIL; fi ;; "
        "*) active=0; emit active_release FAIL ;; esac; "
        'if test "$active" -eq 1; then '
        f'if (cd "$release" && declared=$(docker compose -f {compose} '
        "config --services 2>/dev/null) && "
        f"running=$(docker compose -f {compose} ps --services "
        "--filter status=running 2>/dev/null) && "
        'test -n "$declared" && test -n "$running" && ok=1; '
        'for service in $declared; do case " $running " in '
        '*" $service "*) ;; *) ok=0 ;; esac; done; '
        'test "$ok" -eq 1); then emit compose_services PASS; '
        "else emit compose_services FAIL; fi; "
        f'if (cd "$release" && docker compose -f {compose} exec -T web '
        "python manage.py check --deploy >/dev/null 2>&1); "
        "then emit django_deploy_check PASS; else emit django_deploy_check FAIL; fi; "
        f'if (cd "$release" && docker compose -f {compose} exec -T web '
        f"python manage.py shell -c {db_probe} >/dev/null 2>&1); "
        "then emit database_connectivity PASS; else emit database_connectivity FAIL; fi; "
        f'if (cd "$release" && docker compose -f {compose} exec -T web '
        "python manage.py migrate --check --noinput >/dev/null 2>&1); "
        "then emit pending_migrations PASS; else emit pending_migrations FAIL; fi; "
        "else emit compose_services FAIL; emit django_deploy_check FAIL; "
        "emit database_connectivity FAIL; emit pending_migrations FAIL; fi"
    )
