"""Behavior-focused tests for Phase 0 backup scheduling, listing, and restore."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import djangoops.backup as backup
import pytest
from djangoops.config import DjangoOpsConfig, parse_config, upgrade_config
from djangoops.deploy import DeploymentTarget


def _config() -> DjangoOpsConfig:
    return DjangoOpsConfig.create(
        project_name="sample-app",
        django_module="config.settings",
        hostname="app.example.com",
        acme_email="ops@example.com",
        storage_endpoint_url="https://objects.example.com",
        storage_region="eu-west-1",
        static_bucket="sample-static",
        media_bucket="sample-media",
        backup_schedule="17 2 * * *",
        backup_bucket="sample-backups",
    )


def _target() -> DeploymentTarget:
    return DeploymentTarget.create(
        host="vps.example.com",
        user="deploy",
        port=22,
        remote_base="/srv/sample-app",
        identity_file=None,
    )


def test_schema_v2_requires_explicit_upgrade_and_upgrade_adds_non_secret_backup_contract() -> None:
    v2 = """schema_version: 2
project:
  name: sample-app
django:
  module: config.settings
services:
  postgres: true
  redis: true
  celery: true
  celery_beat: true
tls:
  hostname: app.example.com
  acme_email: ops@example.com
storage:
  endpoint_url: https://objects.example.com
  region: eu-west-1
  static_bucket: sample-static
  media_bucket: sample-media
"""
    with pytest.raises(ValueError, match="config-upgrade"):
        parse_config(v2)
    upgraded = upgrade_config(v2, backup_bucket="sample-backups", backup_schedule="7 1 * * *")
    parsed = parse_config(upgraded)
    assert parsed.schema_version == 3
    assert parsed.backup.bucket == "sample-backups"
    assert parsed.backup.schedule == "7 1 * * *"
    assert "secret" not in upgraded.lower()
    assert "AWS_ACCESS_KEY_ID" not in upgraded


def test_upgrade_rejects_unknown_schema_v2_keys() -> None:
    v2 = (
        "schema_version: 2\nproject: {}\ndjango: {}\nservices: {}\n"
        "tls: {}\nstorage: {}\nextra: true\n"
    )
    with pytest.raises(ValueError, match="unknown or missing keys"):
        upgrade_config(v2, backup_bucket="sample-backups", backup_schedule="17 2 * * *")


def test_schedule_install_is_project_scoped_and_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[str] = []
    monkeypatch.setattr(backup, "_run_ssh", lambda _target, command: commands.append(command))
    backup.install_backup_schedule(_config(), _target())
    backup.install_backup_schedule(_config(), _target())
    assert len(commands) == 2
    assert commands[0] == commands[1]
    assert "djangoops-backup:sample-app" in commands[0]
    assert "grep -Fv" in commands[0]
    assert "17 2 * * *" in commands[0]
    assert "AWS_SECRET_ACCESS_KEY=" not in commands[0]


def test_manual_backup_uses_one_timestamped_identifier(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[str] = []
    monkeypatch.setattr(backup, "_run_ssh", lambda _target, command: commands.append(command))
    value = backup.run_backup(
        _config(),
        _target(),
        now=datetime(2026, 1, 20, 4, 40, tzinfo=UTC),
    )
    assert value == "b20260120T044000Z"
    assert value in commands[0]


def test_backup_script_dumps_compose_postgres_and_copies_media_to_same_backup_prefix() -> None:
    script = backup._backup_script(_config(), _target())
    assert "docker compose -p sample-app -f docker-compose.yml exec -T postgres" in script
    assert "pg_dump" in script
    assert "s3://sample-backups/djangoops-backups/sample-app/$backup_id" in script
    assert 'aws s3 sync "s3://sample-media/" "$dest/media/"' in script
    assert "AWS_SECRET_ACCESS_KEY=" not in script


def test_list_backups_is_deterministic_and_only_accepts_complete_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        backup,
        "_run_ssh_capture",
        lambda _target, _command: (
            "2026-01-20 00:00:00 1 djangoops-backups/sample-app/"
            "b20260120T020000Z/metadata.txt\n"
            "2026-01-19 00:00:00 1 djangoops-backups/sample-app/"
            "b20260119T020000Z/metadata.txt\n"
            "2026-01-18 00:00:00 1 djangoops-backups/sample-app/not-safe/metadata.txt\n"
        ),
    )
    assert [record.backup_id for record in backup.list_backups(_config(), _target())] == [
        "b20260120T020000Z",
        "b20260119T020000Z",
    ]


@pytest.mark.parametrize(
    "value",
    ["latest", "../backup", "b20260120T020000Z;rm", "b20260120T020000Z/../x", ""],
)
def test_restore_rejects_unsafe_or_implicit_backup_identifiers_before_ssh(
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def fail_if_called(_target: DeploymentTarget, _command: str) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(backup, "_run_ssh", fail_if_called)
    with pytest.raises(ValueError):
        backup.restore_backup(_config(), _target(), value)
    assert called is False


def test_restore_preflight_precedes_destructive_work_and_preserves_recovery_dump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[str] = []
    monkeypatch.setattr(backup, "_run_ssh", lambda _target, command: commands.append(command))
    backup.restore_backup(_config(), _target(), "b20260120T020000Z")
    command = commands[0]
    assert command.index("metadata.txt") < command.index(" stop web")
    assert command.index("database.sql.gz") < command.index(" stop web")
    assert command.index("media.ready") < command.index(" stop web")
    assert "pre-restore-b20260120T020000Z.sql.gz" in command
    assert command.index(" stop web") < command.index("dropdb")
    assert command.index("psql -v ON_ERROR_STOP=1") < command.index(" up -d web")
    assert "manage.py check --deploy" in command


def test_restore_failure_surfaces_hard_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(_target: DeploymentTarget, _command: str) -> None:
        raise backup.BackupError("remote backup/restore command failed")

    monkeypatch.setattr(backup, "_run_ssh", fail)
    with pytest.raises(backup.BackupError, match="operator recovery"):
        backup.restore_backup(_config(), _target(), "b20260120T020000Z")


def test_identity_file_validation_is_reused_for_backup_transport(tmp_path: Path) -> None:
    missing = tmp_path / "missing-key"
    with pytest.raises(ValueError, match="identity file"):
        DeploymentTarget.create(
            host="vps.example.com",
            user="deploy",
            port=22,
            remote_base="/srv/sample-app",
            identity_file=missing,
        )
