"""Behavior tests for Phase 0 direct-SSH deployment."""

from __future__ import annotations

import os
import pathlib
import shlex
import tarfile
from datetime import UTC, datetime
from io import BytesIO

import djangoops.cli
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


def _project(tmp_path: pathlib.Path) -> None:
    (tmp_path / "djangoops.yaml").write_text(_config_text(), encoding="utf-8")
    (tmp_path / "docker-compose.yml").write_text("services:\n  web: {}\n", encoding="utf-8")
    (tmp_path / "manage.py").write_text("print('django')\n", encoding="utf-8")


def _target(identity_file: pathlib.Path | None = None) -> djangoops.deploy.DeploymentTarget:
    return djangoops.deploy.DeploymentTarget.create(
        host="vps.example.com",
        user="deploy",
        port=2222,
        remote_base="/srv/django apps/$production",
        identity_file=identity_file,
    )


def _run_in(path: pathlib.Path, argv: list[str]) -> int:
    original = pathlib.Path.cwd()
    os.chdir(path)
    try:
        return djangoops.cli.main(argv)
    finally:
        os.chdir(original)


def test_successful_deploy_orders_upload_snapshot_start_then_activation(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _project(tmp_path)
    calls: list[tuple[str, bool]] = []

    def record(
        _target: djangoops.deploy.DeploymentTarget,
        command: str,
        *,
        input_bytes: bytes | None = None,
    ) -> None:
        calls.append((command, input_bytes is not None))

    monkeypatch.setattr(djangoops.deploy, "_run_ssh", record)

    release = djangoops.deploy.deploy_project(
        project_root=tmp_path,
        config_path=tmp_path / "djangoops.yaml",
        compose_path=tmp_path / "docker-compose.yml",
        target=_target(),
        now=datetime(2026, 1, 9, 5, 30, tzinfo=UTC),
    )

    assert release.startswith("r20260109T053000Z-")
    assert len(calls) == 7
    assert calls[0][1] is True
    assert "tar -xzf -" in calls[0][0]
    assert ".previous-r20260109T053000Z-" in calls[1][0]
    assert "docker compose -p sample-app" in calls[2][0]
    assert "migrate --plan --noinput" in calls[3][0]
    assert "migrate --noinput" in calls[4][0]
    assert "--plan" not in calls[4][0]
    assert "mv -Tf" in calls[5][0]
    assert calls[6][0].startswith("rm -f -- ")
    assert ".previous-r20260109T053000Z-" in calls[6][0]


def test_upload_failure_does_not_touch_current_and_only_cleans_staging(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _project(tmp_path)
    calls: list[str] = []

    def fail_upload(
        _target: djangoops.deploy.DeploymentTarget,
        command: str,
        *,
        input_bytes: bytes | None = None,
    ) -> None:
        calls.append(command)
        if input_bytes is not None:
            raise djangoops.deploy.DeployError("simulated upload failure")

    monkeypatch.setattr(djangoops.deploy, "_run_ssh", fail_upload)

    with pytest.raises(djangoops.deploy.DeployError, match="release upload failed"):
        djangoops.deploy.deploy_project(
            project_root=tmp_path,
            config_path=tmp_path / "djangoops.yaml",
            compose_path=tmp_path / "docker-compose.yml",
            target=_target(),
            now=datetime(2026, 1, 9, 5, 30, tzinfo=UTC),
        )

    assert len(calls) == 2
    assert "current" not in calls[0]
    assert calls[1].startswith("rm -rf -- ")
    assert "/releases/r20260109T053000Z-" in calls[1]


def test_startup_failure_uses_saved_previous_state_or_stops_first_deploy(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _project(tmp_path)
    calls: list[str] = []

    def fail_start(
        _target: djangoops.deploy.DeploymentTarget,
        command: str,
        *,
        input_bytes: bytes | None = None,
    ) -> None:
        calls.append(command)
        if "docker compose" in command and "/releases/" in command and " if " not in command:
            raise djangoops.deploy.DeployError("simulated startup failure")

    monkeypatch.setattr(djangoops.deploy, "_run_ssh", fail_start)

    with pytest.raises(djangoops.deploy.DeployError, match="startup failed"):
        djangoops.deploy.deploy_project(
            project_root=tmp_path,
            config_path=tmp_path / "djangoops.yaml",
            compose_path=tmp_path / "docker-compose.yml",
            target=_target(),
            now=datetime(2026, 1, 9, 5, 30, tzinfo=UTC),
        )

    assert len(calls) == 6
    assert ".previous-r20260109T053000Z-" in calls[1]
    assert "docker compose -p sample-app" in calls[2]
    recovery = calls[3]
    assert ".previous-r20260109T053000Z-" in recovery
    assert "previous_target=$(readlink" in recovery
    assert "docker compose -p sample-app" in recovery
    assert " down; " in recovery
    assert " down -v" not in recovery
    assert "volume prune" not in recovery
    assert calls[4].startswith("rm -f -- ")
    assert calls[5].startswith("rm -rf -- ")
    assert "mv -Tf" not in "\n".join(calls[:3])


def test_activation_failure_removes_pending_and_saved_previous_pointer(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _project(tmp_path)
    calls: list[str] = []

    def fail_activation(
        _target: djangoops.deploy.DeploymentTarget,
        command: str,
        *,
        input_bytes: bytes | None = None,
    ) -> None:
        calls.append(command)
        if ".current-" in command and "mv -Tf" in command:
            raise djangoops.deploy.DeployError("simulated activation failure")

    monkeypatch.setattr(djangoops.deploy, "_run_ssh", fail_activation)

    with pytest.raises(djangoops.deploy.DeployError, match="activation failed"):
        djangoops.deploy.deploy_project(
            project_root=tmp_path,
            config_path=tmp_path / "djangoops.yaml",
            compose_path=tmp_path / "docker-compose.yml",
            target=_target(),
            now=datetime(2026, 1, 9, 5, 30, tzinfo=UTC),
        )

    assert len(calls) == 10
    assert ".previous-r20260109T053000Z-" in calls[1]
    assert "migrate --plan --noinput" in calls[3]
    assert "migrate --noinput" in calls[4]
    assert "--plan" not in calls[4]
    assert "mv -Tf" in calls[5]
    recovery = calls[6]
    assert ".previous-r20260109T053000Z-" in recovery
    assert " down; " in recovery
    assert " down -v" not in recovery
    assert "volume prune" not in recovery
    assert calls[7].startswith("rm -f -- ")
    assert ".current-r20260109T053000Z-" in calls[7]
    assert calls[8].startswith("rm -f -- ")
    assert ".previous-r20260109T053000Z-" in calls[8]
    assert calls[9].startswith("rm -rf -- ")


def test_ambiguous_activation_failure_restores_exact_pre_activation_release(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _project(tmp_path)
    old_release = "/srv/django apps/$production/releases/r20260107T010000Z-7"
    failed_release = f"/srv/django apps/$production/releases/r20260109T053000Z-{os.getpid()}"
    state: dict[str, object] = {
        "current": old_release,
        "previous": None,
        "failed_release_removed": False,
    }

    def remote_state_machine(
        _target: djangoops.deploy.DeploymentTarget,
        command: str,
        *,
        input_bytes: bytes | None = None,
    ) -> None:
        if input_bytes is not None:
            return
        if ".previous-" in command and "previous_target=$(readlink -f" in command:
            state["previous"] = state["current"]
            return
        if ".current-" in command and "mv -Tf" in command:
            assert shlex.quote(failed_release) in command
            state["current"] = failed_release
            raise djangoops.deploy.DeployError("connection lost after remote mv completed")
        if "previous_target=$(readlink --" in command and "docker compose" in command:
            assert state["previous"] == old_release
            state["current"] = state["previous"]
            return
        if command.startswith("rm -rf -- "):
            state["failed_release_removed"] = True

    monkeypatch.setattr(djangoops.deploy, "_run_ssh", remote_state_machine)

    with pytest.raises(djangoops.deploy.DeployError, match="activation failed"):
        djangoops.deploy.deploy_project(
            project_root=tmp_path,
            config_path=tmp_path / "djangoops.yaml",
            compose_path=tmp_path / "docker-compose.yml",
            target=_target(),
            now=datetime(2026, 1, 9, 5, 30, tzinfo=UTC),
        )

    assert state["current"] == old_release
    assert state["failed_release_removed"] is True


def test_ambiguous_activation_failure_on_first_deploy_clears_current_and_stops_runtime(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _project(tmp_path)
    failed_release = f"/srv/django apps/$production/releases/r20260109T053000Z-{os.getpid()}"
    state: dict[str, object] = {
        "current": None,
        "previous": None,
        "down_called": False,
        "failed_release_removed": False,
    }

    def remote_state_machine(
        _target: djangoops.deploy.DeploymentTarget,
        command: str,
        *,
        input_bytes: bytes | None = None,
    ) -> None:
        if input_bytes is not None:
            return
        if ".previous-" in command and "previous_target=$(readlink -f" in command:
            state["previous"] = state["current"]
            return
        if ".current-" in command and "mv -Tf" in command:
            assert shlex.quote(failed_release) in command
            state["current"] = failed_release
            raise djangoops.deploy.DeployError("connection lost after remote mv completed")
        if "previous_target=$(readlink --" in command and "docker compose" in command:
            assert state["previous"] is None
            assert "if test -L" in command
            assert "current_target=$(readlink --" in command
            assert "docker compose -p sample-app -f docker-compose.yml down" in command
            assert "down -v" not in command
            state["current"] = None
            state["down_called"] = True
            return
        if command.startswith("rm -rf -- "):
            state["failed_release_removed"] = True

    monkeypatch.setattr(djangoops.deploy, "_run_ssh", remote_state_machine)

    with pytest.raises(djangoops.deploy.DeployError, match="activation failed"):
        djangoops.deploy.deploy_project(
            project_root=tmp_path,
            config_path=tmp_path / "djangoops.yaml",
            compose_path=tmp_path / "docker-compose.yml",
            target=_target(),
            now=datetime(2026, 1, 9, 5, 30, tzinfo=UTC),
        )

    assert state["current"] is None
    assert state["down_called"] is True
    assert state["failed_release_removed"] is True


def test_custom_compose_path_is_used_for_start_and_recovery(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _project(tmp_path)
    custom_dir = tmp_path / "deploy"
    custom_dir.mkdir()
    custom_compose = custom_dir / "production.yml"
    custom_compose.write_text("services:\n  web: {}\n", encoding="utf-8")
    calls: list[str] = []

    def fail_start(
        _target: djangoops.deploy.DeploymentTarget,
        command: str,
        *,
        input_bytes: bytes | None = None,
    ) -> None:
        calls.append(command)
        if "docker compose" in command and " if " not in command:
            raise djangoops.deploy.DeployError("simulated startup failure")

    monkeypatch.setattr(djangoops.deploy, "_run_ssh", fail_start)

    with pytest.raises(djangoops.deploy.DeployError, match="startup failed"):
        djangoops.deploy.deploy_project(
            project_root=tmp_path,
            config_path=tmp_path / "djangoops.yaml",
            compose_path=custom_compose,
            target=_target(),
            now=datetime(2026, 1, 9, 5, 30, tzinfo=UTC),
        )

    compose_commands = [command for command in calls if "docker compose" in command]
    assert len(compose_commands) == 2
    assert all("-f deploy/production.yml" in command for command in compose_commands)


def test_compose_project_identity_is_stable_across_release_ids() -> None:
    target = _target()
    first = djangoops.deploy.DeploymentPlan(
        project_root=pathlib.Path("/tmp/project"),
        project_name="sample-app",
        target=target,
        release_id="r20260109T053000Z-1",
    )
    second = djangoops.deploy.DeploymentPlan(
        project_root=pathlib.Path("/tmp/project"),
        project_name="sample-app",
        target=target,
        release_id="r20260110T053000Z-2",
    )
    commands: list[str] = []

    original = djangoops.deploy._run_ssh
    try:
        djangoops.deploy._run_ssh = lambda _target, command, **_kwargs: commands.append(command)
        djangoops.deploy._start_release(first)
        djangoops.deploy._start_release(second)
    finally:
        djangoops.deploy._run_ssh = original

    assert all("docker compose -p sample-app" in command for command in commands)
    assert "r20260109" in commands[0]
    assert "r20260110" in commands[1]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("host", "-oProxyCommand=evil"),
        ("host", "good.example;touch-pwned"),
        ("user", "deploy;id"),
        ("port", 0),
        ("port", 70000),
        ("remote_base", "relative/path"),
        ("remote_base", "/srv/app/../other"),
        ("remote_base", "/"),
    ],
)
def test_target_validation_rejects_unsafe_values(field: str, value: object) -> None:
    kwargs: dict[str, object] = {
        "host": "vps.example.com",
        "user": "deploy",
        "port": 22,
        "remote_base": "/srv/djangoops/app",
        "identity_file": None,
    }
    kwargs[field] = value

    with pytest.raises(ValueError):
        djangoops.deploy.DeploymentTarget.create(**kwargs)  # type: ignore[arg-type]


def test_remote_paths_are_shell_quoted_and_local_ssh_uses_argv(
    tmp_path: pathlib.Path,
) -> None:
    identity = tmp_path / "key with spaces"
    identity.write_text("not-read-by-djangoops", encoding="utf-8")
    target = _target(identity)
    plan = djangoops.deploy.DeploymentPlan(
        project_root=tmp_path,
        project_name="sample-app",
        target=target,
        release_id="r20260109T053000Z-1",
    )

    argv = djangoops.deploy._ssh_argv(target, "echo safe")
    assert argv[0] == "ssh"
    assert "StrictHostKeyChecking=no" not in argv
    assert argv[argv.index("-i") + 1] == str(identity)
    assert identity.read_text(encoding="utf-8") not in argv

    release = shlex.quote(plan.release_dir)
    assert release.startswith("'")
    assert "$production" in release


def test_archive_excludes_vcs_caches_symlinks_and_obvious_secrets(tmp_path: pathlib.Path) -> None:
    _project(tmp_path)
    (tmp_path / ".env").write_text("SECRET=developer-secret\n", encoding="utf-8")
    (tmp_path / "prod.pem").write_text("PRIVATE KEY\n", encoding="utf-8")
    (tmp_path / ".env.example").write_text("SECRET=\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("repo metadata", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "x.pyc").write_bytes(b"cache")
    outside = tmp_path.parent / "outside-secret"
    outside.write_text("outside", encoding="utf-8")
    (tmp_path / "linked-secret").symlink_to(outside)

    payload = djangoops.deploy._build_archive(tmp_path)
    with tarfile.open(fileobj=BytesIO(payload), mode="r:gz") as archive:
        names = set(archive.getnames())

    assert "manage.py" in names
    assert "docker-compose.yml" in names
    assert ".env.example" in names
    assert ".env" not in names
    assert "prod.pem" not in names
    assert ".git/config" not in names
    assert "__pycache__/x.pyc" not in names
    assert "linked-secret" not in names


def test_cli_missing_config_and_compose_fail_without_traceback(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = _run_in(
        tmp_path,
        [
            "deploy",
            "--host",
            "vps.example.com",
            "--user",
            "deploy",
            "--remote-base",
            "/srv/djangoops/sample-app",
        ],
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "error:" in captured.err
    assert "Traceback" not in captured.err


def test_cli_ssh_failure_is_non_secret_and_nonzero(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _project(tmp_path)
    secret = "PRIVATE-KEY-MATERIAL"
    identity = tmp_path / "deploy-key"
    identity.write_text(secret, encoding="utf-8")

    def fail(*_args: object, **_kwargs: object) -> None:
        raise djangoops.deploy.DeployError("release upload failed over SSH")

    monkeypatch.setattr(djangoops.deploy, "deploy_project", fail)
    monkeypatch.setattr(djangoops.cli, "deploy_project", fail)

    exit_code = _run_in(
        tmp_path,
        [
            "deploy",
            "--host",
            "vps.example.com",
            "--user",
            "deploy",
            "--remote-base",
            "/srv/djangoops/sample-app",
            "--identity-file",
            str(identity),
        ],
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "release upload failed" in captured.err
    assert secret not in captured.err
    assert "Traceback" not in captured.err
