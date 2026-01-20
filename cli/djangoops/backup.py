"""Phase 0 agentless backup/restore orchestration over direct SSH."""

from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from djangoops.compose import COMPOSE_FILENAME
from djangoops.config import DjangoOpsConfig
from djangoops.deploy import DeploymentTarget, _ssh_argv
from djangoops.health import _validate_compose_file

_BACKUP_ID_RE: Final = re.compile(r"^b[0-9]{8}T[0-9]{6}Z$")


class BackupError(RuntimeError):
    """Expected backup/restore operational failure safe to show to a user."""


@dataclass(frozen=True, slots=True)
class BackupRecord:
    backup_id: str


def validate_backup_id(value: str) -> str:
    if not _BACKUP_ID_RE.fullmatch(value):
        raise ValueError("backup identifier must match bYYYYMMDDTHHMMSSZ")
    return value


def backup_identifier(now: datetime | None = None) -> str:
    value = now if now is not None else datetime.now(UTC)
    if value.tzinfo is None:
        raise ValueError("backup timestamp must be timezone-aware")
    return "b" + value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def install_backup_schedule(
    config: DjangoOpsConfig,
    target: DeploymentTarget,
    *,
    compose_file: str = COMPOSE_FILENAME,
) -> None:
    """Install one deterministic DjangoOps-owned cron entry and backup runner."""
    validated_compose_file = _validate_compose_file(compose_file)
    base = shlex.quote(target.remote_base)
    marker = f"# djangoops-backup:{config.project.name}"
    runner = f"{target.remote_base}/shared/djangoops-backup.sh"
    runner_q = shlex.quote(runner)
    script = _backup_script(config, target, compose_file=validated_compose_file)
    cron_line = f"{config.backup.schedule} {runner_q} {marker}"
    heredoc = f"cat > {runner_q} <<'DJANGOOPS_BACKUP'\n{script}\nDJANGOOPS_BACKUP\n"
    command = (
        "set -eu; umask 077; "
        "command -v crontab >/dev/null; command -v docker >/dev/null; "
        "command -v aws >/dev/null; "
        f"mkdir -p {base}/shared; {heredoc}"
        f"chmod 700 {runner_q}; "
        "current=$(crontab -l 2>/dev/null || true); "
        f"filtered=$(printf '%s\\n' \"$current\" | grep -Fv -- {shlex.quote(marker)} || true); "
        f"{{ printf '%s\\n' \"$filtered\"; printf '%s\\n' {shlex.quote(cron_line)}; }} | "
        "sed '/^$/d' | crontab -"
    )
    _run_ssh(target, command)


def run_backup(
    config: DjangoOpsConfig,
    target: DeploymentTarget,
    *,
    now: datetime | None = None,
) -> str:
    backup_id = backup_identifier(now)
    runner = shlex.quote(target.remote_base + "/shared/djangoops-backup.sh")
    command = f"test -x {runner} && {runner} {shlex.quote(backup_id)}"
    _run_ssh(target, command)
    return backup_id


def list_backups(config: DjangoOpsConfig, target: DeploymentTarget) -> list[BackupRecord]:
    prefix = _object_prefix(config)
    destination = shlex.quote(f"s3://{config.backup.bucket}/{prefix}/")
    command = (
        f"set -eu; {_runtime_preflight(config, target)}; "
        f"{_aws_env_prefix(config)} aws s3 ls {destination} --recursive | awk '{{print $4}}'"
    )
    stdout = _run_ssh_capture(target, command)
    ids: set[str] = set()
    for line in stdout.splitlines():
        parts = line.strip().split("/")
        if len(parts) >= 2 and _BACKUP_ID_RE.fullmatch(parts[-2]) and parts[-1] == "metadata.txt":
            ids.add(parts[-2])
    return [BackupRecord(value) for value in sorted(ids, reverse=True)]


def restore_backup(
    config: DjangoOpsConfig,
    target: DeploymentTarget,
    backup_id: str,
    *,
    compose_file: str = COMPOSE_FILENAME,
) -> None:
    """Preflight a selected backup before quiescing writers and replacing data."""
    selected = validate_backup_id(backup_id)
    validated_compose_file = _validate_compose_file(compose_file)
    prefix = f"{_object_prefix(config)}/{selected}"
    base = shlex.quote(target.remote_base)
    current = shlex.quote(target.remote_base + "/current")
    compose = _compose_prefix(config, validated_compose_file)
    aws = _aws_env_prefix(config)
    bucket_path = f"s3://{config.backup.bucket}/{prefix}"
    writers = _writer_services(config)
    writer_args = " ".join(shlex.quote(service) for service in writers)
    service_checks = " ".join(
        f"{compose} config --services | grep -Fx {shlex.quote(service)} >/dev/null;"
        for service in ("postgres", *writers)
    )
    recovery_root = f"s3://{config.backup.bucket}/{_object_prefix(config)}/recovery"
    expected_project = shlex.quote("project=" + config.project.name)
    command = (
        "set -eu; umask 077; "
        f"{_runtime_preflight(config, target)}; command -v gzip >/dev/null; "
        f"test -L {current}; cd {current}; "
        f"{service_checks} "
        f"{aws} aws s3 cp {shlex.quote(bucket_path + '/metadata.txt')} "
        "/tmp/djangoops-restore-meta >/dev/null; "
        f"grep -Fx {shlex.quote('backup_id=' + selected)} "
        "/tmp/djangoops-restore-meta >/dev/null; "
        f"grep -Fx {expected_project} /tmp/djangoops-restore-meta >/dev/null; "
        "grep -Fx 'database=database.sql.gz' /tmp/djangoops-restore-meta >/dev/null; "
        "grep -Fx 'media=media/' /tmp/djangoops-restore-meta >/dev/null; "
        f"{aws} aws s3 cp {shlex.quote(bucket_path + '/database.sql.gz')} "
        "/tmp/djangoops-restore-db.sql.gz >/dev/null; "
        "gzip -t /tmp/djangoops-restore-db.sql.gz; "
        f"{aws} aws s3 cp {shlex.quote(bucket_path + '/media.ready')} "
        "/tmp/djangoops-restore-media-ready >/dev/null; "
        "recovery_id=pre-restore-$(date -u +%Y%m%dT%H%M%SZ); "
        f"recovery={shlex.quote(recovery_root)}/$recovery_id; "
        f"{compose} exec -T postgres sh -c "
        '\'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"\' '
        f"| gzip -c > {base}/shared/pre-restore-{selected}.sql.gz; "
        f"{aws} aws s3 cp {base}/shared/pre-restore-{selected}.sql.gz "
        '"$recovery/database.sql.gz" >/dev/null; '
        f"{aws} aws s3 sync {shlex.quote('s3://' + config.storage.media_bucket + '/')} "
        '"$recovery/media/" >/dev/null; '
        f"{compose} stop {writer_args}; "
        f"{compose} exec -T postgres sh -c "
        '\'dropdb -U "$POSTGRES_USER" --if-exists "$POSTGRES_DB" '
        '&& createdb -U "$POSTGRES_USER" "$POSTGRES_DB"\'; '
        f"gzip -dc /tmp/djangoops-restore-db.sql.gz | {compose} exec -T postgres "
        'sh -c \'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" "$POSTGRES_DB"\'; '
        f"{aws} aws s3 sync --delete {shlex.quote(bucket_path + '/media/')} "
        f"{shlex.quote('s3://' + config.storage.media_bucket + '/')} >/dev/null; "
        f"{compose} up -d {writer_args}; "
        f"{compose} exec -T web python manage.py check --deploy"
    )
    try:
        _run_ssh(target, command)
    except BackupError as exc:
        raise BackupError(
            "restore failed; application data may have been mutated. Pre-restore database/media "
            "artifacts were preserved for operator recovery and the selected source backup was "
            "not deleted"
        ) from exc


def _writer_services(config: DjangoOpsConfig) -> tuple[str, ...]:
    services = ["web"]
    if config.services.celery:
        services.append("celery")
    if config.services.celery_beat:
        services.append("celery_beat")
    return tuple(services)


def _object_prefix(config: DjangoOpsConfig) -> str:
    return f"{config.backup.prefix}/{config.project.name}"


def _aws_env_prefix(config: DjangoOpsConfig) -> str:
    endpoint = shlex.quote(config.storage.endpoint_url)
    region = (
        ""
        if config.storage.region is None
        else f" AWS_DEFAULT_REGION={shlex.quote(config.storage.region)}"
    )
    return f"AWS_ENDPOINT_URL={endpoint}{region}"


def _runtime_preflight(config: DjangoOpsConfig, target: DeploymentTarget) -> str:
    env_path = shlex.quote(target.remote_base + "/shared/.env")
    return (
        "command -v docker >/dev/null; command -v aws >/dev/null; "
        f"test -f {env_path}; set -a; . {env_path}; set +a; "
        'test -n "${POSTGRES_PASSWORD:-}"; test -n "${AWS_ACCESS_KEY_ID:-}"; '
        'test -n "${AWS_SECRET_ACCESS_KEY:-}"; '
        f"test -n {shlex.quote(config.project.name)}"
    )


def _compose_prefix(
    config: DjangoOpsConfig,
    compose_file: str = COMPOSE_FILENAME,
) -> str:
    validated = _validate_compose_file(compose_file)
    return f"docker compose -p {shlex.quote(config.project.name)} -f {shlex.quote(validated)}"


def _backup_script(
    config: DjangoOpsConfig,
    target: DeploymentTarget,
    *,
    compose_file: str = COMPOSE_FILENAME,
) -> str:
    base = shlex.quote(target.remote_base)
    current = shlex.quote(target.remote_base + "/current")
    compose = _compose_prefix(config, compose_file)
    prefix = _object_prefix(config)
    aws = _aws_env_prefix(config)
    bucket = config.backup.bucket
    media = config.storage.media_bucket
    project = shlex.quote(config.project.name)
    return f"""#!/bin/sh
set -eu
umask 077
backup_id="${{1:-b$(date -u +%Y%m%dT%H%M%SZ)}}"
printf '%s\n' "$backup_id" | grep -Eq '^b[0-9]{{8}}T[0-9]{{6}}Z$' || exit 2
command -v docker >/dev/null
command -v aws >/dev/null
cd {current}
set -a
. {base}/shared/.env
set +a
test -n "${{POSTGRES_PASSWORD:-}}"
test -n "${{AWS_ACCESS_KEY_ID:-}}"
test -n "${{AWS_SECRET_ACCESS_KEY:-}}"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
{compose} config --services | grep -Fx postgres >/dev/null
{compose} exec -T postgres sh -c \
  'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip -c > "$work/database.sql.gz"
printf 'backup_id=%s\nproject=%s\ndatabase=database.sql.gz\nmedia=media/\n' \
  "$backup_id" {project} > "$work/metadata.txt"
dest="s3://{bucket}/{prefix}/$backup_id"
{aws} aws s3 cp "$work/database.sql.gz" "$dest/database.sql.gz" >/dev/null
{aws} aws s3 sync "s3://{media}/" "$dest/media/" >/dev/null
: > "$work/media.ready"
{aws} aws s3 cp "$work/media.ready" "$dest/media.ready" >/dev/null
{aws} aws s3 cp "$work/metadata.txt" "$dest/metadata.txt" >/dev/null
"""


def _run_ssh(target: DeploymentTarget, command: str) -> None:
    _run_ssh_capture(target, command)


def _run_ssh_capture(target: DeploymentTarget, command: str) -> str:
    try:
        result = subprocess.run(
            _ssh_argv(target, command),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise BackupError("unable to execute the system OpenSSH client") from exc
    if result.returncode != 0:
        raise BackupError("remote backup/restore command failed")
    return result.stdout
