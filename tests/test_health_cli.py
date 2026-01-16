from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import djangoops.cli
import djangoops.deploy
import djangoops.health
import pytest

_CHECKS = (
    "active_release",
    "compose_services",
    "django_deploy_check",
    "database_connectivity",
    "pending_migrations",
)


def _target(tmp_path: Path) -> djangoops.deploy.DeploymentTarget:
    identity = tmp_path / "id_test"
    identity.write_text("PRIVATE-SECRET-CONTENT", encoding="utf-8")
    return djangoops.deploy.DeploymentTarget.create(
        host="example.test",
        user="deploy",
        port=2222,
        remote_base="/srv/djangoops/app",
        identity_file=identity,
    )


def _stdout(*, failed: set[str] | None = None) -> str:
    failures = failed or set()
    return "".join(
        f"DJANGOOPS_HEALTH\t{name}\t{'FAIL' if name in failures else 'PASS'}\n" for name in _CHECKS
    )


def _report(*, failed: set[str] | None = None) -> djangoops.health.HealthReport:
    failures = failed or set()
    return djangoops.health.HealthReport(
        tuple(
            djangoops.health.HealthCheck(name, "fail" if name in failures else "pass")
            for name in _CHECKS
        )
    )


def test_health_project_uses_batch_openssh_and_django_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _target(tmp_path)
    seen: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object) -> SimpleNamespace:
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout=_stdout(), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    report = djangoops.health.health_project(target)

    assert report.healthy
    assert [check.name for check in report.checks] == list(_CHECKS)
    argv = seen["argv"]
    assert isinstance(argv, list)
    assert argv[:3] == ["ssh", "-o", "BatchMode=yes"]
    remote = str(argv[-1])
    assert "manage.py check --deploy" in remote
    assert "connection.ensure_connection()" in remote
    assert "manage.py migrate --check --noinput" in remote
    assert "docker compose" in remote
    assert "docker-compose.yml" in remote
    assert seen["kwargs"] == {
        "capture_output": True,
        "check": False,
        "text": True,
    }


def test_health_project_uses_non_default_project_relative_compose_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _target(tmp_path)
    seen: dict[str, object] = {}

    def fake_run(argv: list[str], **kwargs: object) -> SimpleNamespace:
        seen["argv"] = argv
        return SimpleNamespace(returncode=0, stdout=_stdout(), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    report = djangoops.health.health_project(target, compose_file="ops/compose.prod.yml")

    assert report.healthy
    argv = seen["argv"]
    assert isinstance(argv, list)
    remote = str(argv[-1])
    assert "ops/compose.prod.yml" in remote
    assert "docker-compose.yml" not in remote


@pytest.mark.parametrize(
    "compose_file",
    ["/tmp/compose.yml", "../compose.yml", "ops/../compose.yml", ""],
)
def test_health_rejects_unsafe_compose_paths_before_ssh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    compose_file: str,
) -> None:
    target = _target(tmp_path)

    def unexpected_run(*args: object, **kwargs: object) -> SimpleNamespace:
        raise AssertionError("SSH must not run for an invalid Compose path")

    monkeypatch.setattr(subprocess, "run", unexpected_run)
    with pytest.raises(ValueError):
        djangoops.health.health_project(target, compose_file=compose_file)


def test_individual_failures_keep_complete_unhealthy_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _target(tmp_path)

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            returncode=0,
            stdout=_stdout(failed={"compose_services", "database_connectivity"}),
            stderr="remote secret-looking output",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    report = djangoops.health.health_project(target)
    assert not report.healthy
    assert [check.name for check in report.checks] == list(_CHECKS)
    assert [check.status for check in report.checks] == [
        "pass",
        "fail",
        "pass",
        "fail",
        "pass",
    ]


def test_missing_current_pointer_is_clean_unhealthy_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _target(tmp_path)

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            returncode=0,
            stdout=_stdout(failed=set(_CHECKS)),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    report = djangoops.health.health_project(target)
    assert not report.healthy
    assert report.checks[0] == djangoops.health.HealthCheck("active_release", "fail")


def test_transport_and_invalid_response_do_not_echo_remote_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _target(tmp_path)

    def transport_failure(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=255, stdout="", stderr="TOPSECRET")

    monkeypatch.setattr(subprocess, "run", transport_failure)
    with pytest.raises(djangoops.health.HealthTransportError, match="failed over SSH") as exc_info:
        djangoops.health.health_project(target)
    assert "TOPSECRET" not in str(exc_info.value)

    def malformed(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            returncode=0,
            stdout="DJANGOOPS_HEALTH\tactive_release\tPASS\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", malformed)
    with pytest.raises(djangoops.health.HealthTransportError, match="incomplete or invalid"):
        djangoops.health.health_project(target)


def _health_args(*, json_mode: bool = False, compose_file: str | None = None) -> list[str]:
    args = [
        "health",
        "--host",
        "example.test",
        "--user",
        "deploy",
        "--remote-base",
        "/srv/djangoops/app",
    ]
    if compose_file is not None:
        args.extend(["--compose-file", compose_file])
    if json_mode:
        args.append("--json")
    return args


def test_json_cli_is_one_deterministic_document_and_unhealthy_exit_is_one(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "djangoops.cli.health_project",
        lambda target, compose_file: _report(failed={"pending_migrations"}),
    )
    code = djangoops.cli.main(_health_args(json_mode=True))
    captured = capsys.readouterr()

    assert code == 1
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    payload = json.loads(captured.out)
    assert payload["schema_version"] == 1
    assert payload["overall_status"] == "unhealthy"
    assert [item["name"] for item in payload["checks"]] == list(_CHECKS)
    expected = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    assert captured.out.strip() == expected


def test_health_cli_passes_custom_compose_path(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def fake_health(
        target: djangoops.deploy.DeploymentTarget,
        compose_file: str,
    ) -> djangoops.health.HealthReport:
        seen["compose_file"] = compose_file
        return _report()

    monkeypatch.setattr("djangoops.cli.health_project", fake_health)
    code = djangoops.cli.main(_health_args(compose_file="ops/compose.prod.yml"))
    assert code == 0
    assert seen["compose_file"] == "ops/compose.prod.yml"


def test_human_cli_lists_every_check_and_overall_status(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("djangoops.cli.health_project", lambda target, compose_file: _report())
    code = djangoops.cli.main(_health_args())
    captured = capsys.readouterr()

    assert code == 0
    for name in _CHECKS:
        assert f"[PASS] {name}" in captured.out
    assert "Overall: HEALTHY" in captured.out


def test_cli_transport_error_is_exit_two_with_clean_stdout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(
        target: djangoops.deploy.DeploymentTarget,
        compose_file: str,
    ) -> djangoops.health.HealthReport:
        raise djangoops.health.HealthTransportError("health evaluation failed over SSH")

    monkeypatch.setattr("djangoops.cli.health_project", fail)
    code = djangoops.cli.main(_health_args(json_mode=True))
    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert captured.err == "error: health evaluation failed over SSH\n"


def test_target_validation_and_private_key_content_stay_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _target(tmp_path)

    def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout=_stdout(), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert "PRIVATE-SECRET-CONTENT" not in djangoops.health.health_project(target).to_json()

    with pytest.raises(ValueError):
        djangoops.deploy.DeploymentTarget.create(
            host="-oProxyCommand=bad",
            user="deploy",
            port=22,
            remote_base="/srv/djangoops/app",
            identity_file=None,
        )
