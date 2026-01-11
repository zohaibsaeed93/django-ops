"""Behavior tests for Phase 0 migration-safe deployment."""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime

import djangoops.deploy
import pytest
import yaml


def _config_text() -> str:
    return yaml.safe_dump(
        {
            "schema_version": 1,
            "project": {"name": "sample-app"},
            "django": {"module": "config"},
            "services": {
                "postgres": True,
                "redis": True,
                "celery": True,
                "celery_beat": True,
            },
        },
        sort_keys=False,
    )


def _project(tmp_path: pathlib.Path, compose: str = "docker-compose.yml") -> pathlib.Path:
    (tmp_path / "djangoops.yaml").write_text(_config_text(), encoding="utf-8")
    compose_path = tmp_path / compose
    compose_path.parent.mkdir(parents=True, exist_ok=True)
    compose_path.write_text("services:\n  web: {}\n", encoding="utf-8")
    (tmp_path / "manage.py").write_text("print('django')\n", encoding="utf-8")
    return compose_path


def _target() -> djangoops.deploy.DeploymentTarget:
    return djangoops.deploy.DeploymentTarget.create(
        host="vps.example.com",
        user="deploy",
        port=22,
        remote_base="/srv/djangoops/sample-app",
        identity_file=None,
    )


def _deploy(tmp_path: pathlib.Path, compose_path: pathlib.Path) -> str:
    return djangoops.deploy.deploy_project(
        project_root=tmp_path,
        config_path=tmp_path / "djangoops.yaml",
        compose_path=compose_path,
        target=_target(),
        now=datetime(2026, 1, 11, 6, 0, tzinfo=UTC),
    )


def test_success_orders_preflight_migrate_before_activation(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_path = _project(tmp_path)
    calls: list[str] = []

    def record(
        _target: djangoops.deploy.DeploymentTarget,
        command: str,
        *,
        input_bytes: bytes | None = None,
    ) -> None:
        calls.append(command)

    monkeypatch.setattr(djangoops.deploy, "_run_ssh", record)
    _deploy(tmp_path, compose_path)

    start = next(i for i, c in enumerate(calls) if " up -d --build" in c and "if test -L" not in c)
    preflight = next(i for i, c in enumerate(calls) if "migrate --plan --noinput" in c)
    migrate = next(i for i, c in enumerate(calls) if "migrate --noinput" in c and "--plan" not in c)
    activate = next(i for i, c in enumerate(calls) if ".current-" in c and "mv -Tf" in c)

    assert start < preflight < migrate < activate
    assert "exec -T web" in calls[preflight]
    assert "exec -T web" in calls[migrate]
    assert "-p sample-app" in calls[preflight]
    assert "-p sample-app" in calls[migrate]
    assert sum("migrate --noinput" in c and "--plan" not in c for c in calls) == 1


@pytest.mark.parametrize("stage", ["preflight", "migrate"])
def test_migration_stage_failure_rolls_back_and_never_activates(
    stage: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_path = _project(tmp_path)
    calls: list[str] = []

    def fail_stage(
        _target: djangoops.deploy.DeploymentTarget,
        command: str,
        *,
        input_bytes: bytes | None = None,
    ) -> None:
        calls.append(command)
        if stage == "preflight" and "migrate --plan --noinput" in command:
            raise djangoops.deploy.DeployError("SECRET_DATABASE_URL=postgres://hidden")
        if stage == "migrate" and "migrate --noinput" in command and "--plan" not in command:
            raise djangoops.deploy.DeployError("SECRET_DATABASE_URL=postgres://hidden")

    monkeypatch.setattr(djangoops.deploy, "_run_ssh", fail_stage)

    expected = "migration pre-flight failed" if stage == "preflight" else "migration failed"
    with pytest.raises(djangoops.deploy.DeployError, match=expected) as exc_info:
        _deploy(tmp_path, compose_path)

    assert "postgres://hidden" not in str(exc_info.value)
    assert not any(".current-" in c and "mv -Tf" in c for c in calls)
    recovery = next(c for c in calls if "previous_target=$(readlink --" in c)
    assert "docker compose -p sample-app -f docker-compose.yml" in recovery
    assert " down; " in recovery
    assert "down -v" not in recovery
    assert "volume prune" not in recovery
    assert calls[-1].startswith("rm -rf -- ")


def test_preflight_failure_does_not_run_migrate(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_path = _project(tmp_path)
    calls: list[str] = []

    def fail_preflight(
        _target: djangoops.deploy.DeploymentTarget,
        command: str,
        *,
        input_bytes: bytes | None = None,
    ) -> None:
        calls.append(command)
        if "migrate --plan --noinput" in command:
            raise djangoops.deploy.DeployError("preflight failed remotely")

    monkeypatch.setattr(djangoops.deploy, "_run_ssh", fail_preflight)

    with pytest.raises(djangoops.deploy.DeployError, match="pre-flight"):
        _deploy(tmp_path, compose_path)

    assert not any("migrate --noinput" in c and "--plan" not in c for c in calls)


def test_migration_failure_warns_database_may_need_operator_inspection(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_path = _project(tmp_path)

    def fail_migrate(
        _target: djangoops.deploy.DeploymentTarget,
        command: str,
        *,
        input_bytes: bytes | None = None,
    ) -> None:
        if "migrate --noinput" in command and "--plan" not in command:
            raise djangoops.deploy.DeployError("remote detail must stay hidden")

    monkeypatch.setattr(djangoops.deploy, "_run_ssh", fail_migrate)

    with pytest.raises(djangoops.deploy.DeployError) as exc_info:
        _deploy(tmp_path, compose_path)

    message = str(exc_info.value)
    assert "operator inspection" in message
    assert "partially applied" in message
    assert "remote detail" not in message


def test_custom_compose_path_is_used_for_start_migrations_and_recovery(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_path = _project(tmp_path, "deploy/production.yml")
    calls: list[str] = []

    def fail_migrate(
        _target: djangoops.deploy.DeploymentTarget,
        command: str,
        *,
        input_bytes: bytes | None = None,
    ) -> None:
        calls.append(command)
        if "migrate --noinput" in command and "--plan" not in command:
            raise djangoops.deploy.DeployError("ambiguous SSH result")

    monkeypatch.setattr(djangoops.deploy, "_run_ssh", fail_migrate)

    with pytest.raises(djangoops.deploy.DeployError, match="migration failed"):
        _deploy(tmp_path, compose_path)

    compose_calls = [c for c in calls if "docker compose" in c]
    assert compose_calls
    assert all("-f deploy/production.yml" in c for c in compose_calls)
    assert not any("down -v" in c for c in compose_calls)


def test_first_deploy_migration_failure_runtime_cleanup_preserves_volumes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_path = _project(tmp_path)
    calls: list[str] = []

    def fail_migrate(
        _target: djangoops.deploy.DeploymentTarget,
        command: str,
        *,
        input_bytes: bytes | None = None,
    ) -> None:
        calls.append(command)
        if "migrate --noinput" in command and "--plan" not in command:
            raise djangoops.deploy.DeployError("connection lost")

    monkeypatch.setattr(djangoops.deploy, "_run_ssh", fail_migrate)

    with pytest.raises(djangoops.deploy.DeployError, match="migration failed"):
        _deploy(tmp_path, compose_path)

    recovery = next(c for c in calls if "previous_target=$(readlink --" in c)
    assert "docker compose -p sample-app -f docker-compose.yml down" in recovery
    assert "down -v" not in recovery
    assert "volume prune" not in recovery
    assert "docker volume" not in recovery
    assert not any(".current-" in c and "mv -Tf" in c for c in calls)


def test_activation_failure_after_migrations_warns_database_compatibility(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compose_path = _project(tmp_path)
    calls: list[str] = []

    def fail_activation(
        _target: djangoops.deploy.DeploymentTarget,
        command: str,
        *,
        input_bytes: bytes | None = None,
    ) -> None:
        calls.append(command)
        if ".current-" in command and "mv -Tf" in command:
            raise djangoops.deploy.DeployError("ambiguous SSH activation result")

    monkeypatch.setattr(djangoops.deploy, "_run_ssh", fail_activation)

    with pytest.raises(djangoops.deploy.DeployError) as exc_info:
        _deploy(tmp_path, compose_path)

    message = str(exc_info.value)
    assert "migrations completed" in message
    assert "database schema" in message.lower()
    assert "operator inspection" in message
    assert "ambiguous SSH activation result" not in message
    assert any("previous_target=$(readlink --" in c for c in calls)
    recovery = next(c for c in calls if "previous_target=$(readlink --" in c)
    assert "down -v" not in recovery
    assert "volume prune" not in recovery
    assert "docker volume" not in recovery
