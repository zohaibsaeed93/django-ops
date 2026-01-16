"""Agentless Phase 0 Django-aware deployment health diagnostics."""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from typing import Final, Literal

from djangoops.deploy import DeploymentTarget

_SCHEMA_VERSION: Final = 1
_SENTINEL: Final = "DJANGOOPS_HEALTH"
_NOT_EVALUATED: Final = "not evaluated because there is no valid active release"
_CHECK_NAMES: Final = (
    "active_release",
    "compose_services",
    "django_deploy_check",
    "database_connectivity",
    "migrations_current",
)
_ALLOWED_DETAILS: Final = {
    "active_release": {
        "active release pointer is valid",
        "no active release is available",
        "current does not resolve to a managed release",
    },
    "compose_services": {
        _NOT_EVALUATED,
        "expected Compose services are running and inspectable",
        "expected Compose services are missing, stopped, or unhealthy",
    },
    "django_deploy_check": {
        _NOT_EVALUATED,
        "Django web container is unavailable",
        "Django deployment checks pass",
        "Django deployment checks failed",
    },
    "database_connectivity": {
        _NOT_EVALUATED,
        "Django web container is unavailable",
        "Django database connection succeeds",
        "Django database connection failed",
    },
    "migrations_current": {
        _NOT_EVALUATED,
        "Django web container is unavailable",
        "Django reports no unapplied migrations",
        "Django reports unapplied migrations or migration check failure",
    },
}


class HealthEvaluationError(RuntimeError):
    """Health could not be evaluated because transport or remote execution failed."""


@dataclass(frozen=True, slots=True)
class HealthCheck:
    """One stable, non-secret diagnostic result."""

    name: str
    status: Literal["pass", "fail"]
    detail: str

    @property
    def passed(self) -> bool:
        return self.status == "pass"

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class HealthReport:
    """Deterministic Phase 0 health report."""

    checks: tuple[HealthCheck, ...]
    schema_version: int = _SCHEMA_VERSION

    @property
    def status(self) -> Literal["healthy", "unhealthy"]:
        return "healthy" if all(check.passed for check in self.checks) else "unhealthy"

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "checks": [check.as_dict() for check in self.checks],
        }


def check_health(target: DeploymentTarget) -> HealthReport:
    """Evaluate the active Phase 0 deployment over one non-interactive SSH command."""
    command = _remote_health_command(target.remote_base)
    try:
        result = _run_command(_ssh_argv(target, command))
    except OSError as exc:
        raise HealthEvaluationError("unable to execute the system OpenSSH client") from exc

    if result.returncode != 0:
        raise HealthEvaluationError("health could not be evaluated over SSH")
    return _parse_report(result.stdout)


def _run_command(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, check=False, text=True)


def _ssh_argv(target: DeploymentTarget, remote_command: str) -> list[str]:
    argv = ["ssh", "-o", "BatchMode=yes", "-p", str(target.port)]
    if target.identity_file is not None:
        argv.extend(["-i", str(target.identity_file)])
    argv.extend([f"{target.user}@{target.host}", remote_command])
    return argv


def _remote_health_command(remote_base: str) -> str:
    # DeploymentTarget has already validated remote_base as an absolute safe POSIX path.
    base = shlex.quote(remote_base)
    return f"""set -u
base={base}
current_link="$base/current"
releases="$base/releases"
emit() {{ printf '%s|%s|%s|%s\\n' '{_SENTINEL}' "$1" "$2" "$3"; }}
not_evaluated() {{
  emit compose_services fail '{_NOT_EVALUATED}'
  emit django_deploy_check fail '{_NOT_EVALUATED}'
  emit database_connectivity fail '{_NOT_EVALUATED}'
  emit migrations_current fail '{_NOT_EVALUATED}'
}}
current=$(readlink -f -- "$current_link" 2>/dev/null || true)
if [ -z "$current" ] || [ ! -d "$current" ]; then
  emit active_release fail 'no active release is available'
  not_evaluated
  exit 0
fi
case "$current" in
  "$releases"/*) ;;
  *)
    emit active_release fail 'current does not resolve to a managed release'
    not_evaluated
    exit 0
    ;;
esac
emit active_release pass 'active release pointer is valid'

compose_file="$current/docker-compose.yml"
services_ok=1
web_id=''
if [ ! -f "$compose_file" ]; then
  services_ok=0
else
  expected=$(
    cd "$current" && docker compose -f docker-compose.yml config --services 2>/dev/null
  ) || services_ok=0
  if [ "$services_ok" -eq 1 ] && [ -z "$expected" ]; then services_ok=0; fi
  if [ "$services_ok" -eq 1 ]; then
    for service in $expected; do
      ids=$(
        docker ps -aq \
          --filter "label=com.docker.compose.project.working_dir=$current" \
          --filter "label=com.docker.compose.service=$service" 2>/dev/null
      ) || {{ services_ok=0; break; }}
      count=$(printf '%s\\n' "$ids" | sed '/^$/d' | wc -l | tr -d ' ')
      if [ "$count" != '1' ]; then services_ok=0; continue; fi
      id=$(printf '%s\\n' "$ids" | sed -n '1p')
      state=$(
        docker inspect -f \
          '{{{{.State.Status}}}} {{{{if .State.Health}}}}{{{{.State.Health.Status}}}}{{{{end}}}}' \
          "$id" 2>/dev/null
      ) || {{ services_ok=0; continue; }}
      case "$state" in
        'running '|'running healthy') ;;
        *) services_ok=0 ;;
      esac
      if [ "$service" = 'web' ]; then web_id="$id"; fi
    done
  fi
fi
if [ "$services_ok" -eq 1 ] && [ -n "$web_id" ]; then
  emit compose_services pass 'expected Compose services are running and inspectable'
else
  emit compose_services fail 'expected Compose services are missing, stopped, or unhealthy'
fi

if [ -z "$web_id" ]; then
  emit django_deploy_check fail 'Django web container is unavailable'
  emit database_connectivity fail 'Django web container is unavailable'
  emit migrations_current fail 'Django web container is unavailable'
  exit 0
fi

if docker exec "$web_id" python manage.py check --deploy >/dev/null 2>&1; then
  emit django_deploy_check pass 'Django deployment checks pass'
else
  emit django_deploy_check fail 'Django deployment checks failed'
fi
if docker exec "$web_id" python manage.py shell -c \
  'from django.db import connection; connection.ensure_connection(); connection.close()' \
  >/dev/null 2>&1; then
  emit database_connectivity pass 'Django database connection succeeds'
else
  emit database_connectivity fail 'Django database connection failed'
fi
if docker exec "$web_id" python manage.py migrate --check --noinput >/dev/null 2>&1; then
  emit migrations_current pass 'Django reports no unapplied migrations'
else
  emit migrations_current fail 'Django reports unapplied migrations or migration check failure'
fi
"""


def _parse_report(stdout: str) -> HealthReport:
    checks: list[HealthCheck] = []
    for raw_line in stdout.splitlines():
        if not raw_line.startswith(f"{_SENTINEL}|"):
            continue
        parts = raw_line.split("|", 3)
        if len(parts) != 4:
            raise HealthEvaluationError("remote health response was malformed")
        _, name, status, detail = parts
        if name not in _CHECK_NAMES or status not in {"pass", "fail"}:
            raise HealthEvaluationError("remote health response was malformed")
        if detail not in _ALLOWED_DETAILS[name]:
            raise HealthEvaluationError("remote health response was malformed")
        typed_status: Literal["pass", "fail"] = "pass" if status == "pass" else "fail"
        checks.append(HealthCheck(name=name, status=typed_status, detail=detail))

    if tuple(check.name for check in checks) != _CHECK_NAMES:
        raise HealthEvaluationError("remote health response was incomplete")
    return HealthReport(checks=tuple(checks))
