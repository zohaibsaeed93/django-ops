"""Regression coverage for backup/restore safety and deployed Compose paths."""

from __future__ import annotations

import djangoops.backup as backup
import pytest
from djangoops.config import DjangoOpsConfig
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


def test_restore_integrity_check_precedes_all_destructive_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[str] = []
    monkeypatch.setattr(
        backup,
        "_run_ssh",
        lambda _target, command: commands.append(command),
    )

    backup.restore_backup(_config(), _target(), "b20260120T020000Z")

    command = commands[0]
    integrity = command.index("gzip -t /tmp/djangoops-restore-db.sql.gz")
    assert integrity < command.index(" stop web")
    assert integrity < command.index("dropdb")
    assert integrity < command.index("aws s3 sync --delete")


def test_backup_and_restore_honor_custom_project_relative_compose_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[str] = []
    monkeypatch.setattr(
        backup,
        "_run_ssh",
        lambda _target, command: commands.append(command),
    )

    backup.install_backup_schedule(
        _config(),
        _target(),
        compose_file="ops/compose.prod.yml",
    )
    backup.restore_backup(
        _config(),
        _target(),
        "b20260120T020000Z",
        compose_file="ops/compose.prod.yml",
    )

    assert "-f ops/compose.prod.yml" in commands[0]
    assert "-f ops/compose.prod.yml" in commands[1]
    assert "-f docker-compose.yml" not in commands[0]
    assert "-f docker-compose.yml" not in commands[1]


@pytest.mark.parametrize(
    "value",
    ["/tmp/compose.yml", "../compose.yml", "ops/../compose.yml"],
)
def test_backup_compose_path_rejects_absolute_or_traversal_before_ssh(
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def fail_if_called(_target: DeploymentTarget, _command: str) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(backup, "_run_ssh", fail_if_called)
    with pytest.raises(ValueError):
        backup.install_backup_schedule(_config(), _target(), compose_file=value)
    assert called is False
