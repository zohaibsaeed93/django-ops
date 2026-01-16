"""Behavior tests for Phase 0 Django-aware health diagnostics."""

from __future__ import annotations

import json
import subprocess

import djangoops.cli
import djangoops.health
import pytest

_FAILURE_DETAILS = {
    "compose_services": "expected Compose services are missing, stopped, or unhealthy",
    "django_deploy_check": "Django deployment checks failed",
    "database_connectivity": "Django database connection failed",
    "migrations_current": "Django reports unapplied migrations or migration check failure",
}


def _stdout(*, failed: str | None = None) -> str:
    rows = [
        ("active_release", "pass", "active release pointer is valid"),
        ("compose_services", "pass", "expected Compose services are running and inspectable"),
        ("django_deploy_check", "pass", "Django deployment checks pass"),
        ("database_connectivity", "pass", "Django database connection succeeds"),
        ("migrations_current", "pass", "Django reports no unapplied migrations"),
    ]
    output: list[str] = []
    for name, status, detail in rows:
        if name == failed:
            status = "fail"
            detail = _FAILURE_DETAILS[name]
        output.append(f"DJANGOOPS_HEALTH|{name}|{status}|{detail}")
    return "\n".join(output) + "\n"


def _argv(*extra: str) -> list[str]:
    return [
        "health",
        "--host",
        "vps.example.com",
        "--user",
        "deploy",
        "--port",
        "2222",
        "--remote-base",
        "/srv/djangoops/sample-app",
        *extra,
    ]


def _completed(
    stdout: str,
    *,
    returncode: int = 0,
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_healthy_json_is_one_deterministic_document(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def run(_command: list[str]) -> subprocess.CompletedProcess[str]:
        return _completed(_stdout())

    monkeypatch.setattr(djangoops.health, "_run_command", run)
    assert djangoops.cli.main(_argv("--json")) == 0
    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert captured.err == ""
    assert len(captured.out.splitlines()) == 1
    assert document["schema_version"] == 1
    assert document["status"] == "healthy"
    assert [check["name"] for check in document["checks"]] == [
        "active_release",
        "compose_services",
        "django_deploy_check",
        "database_connectivity",
        "migrations_current",
    ]


@pytest.mark.parametrize(
    "failed",
    [
        "compose_services",
        "django_deploy_check",
        "database_connectivity",
        "migrations_current",
    ],
)
def test_individual_failure_is_unhealthy_and_keeps_complete_report(
    failed: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def run(_command: list[str]) -> subprocess.CompletedProcess[str]:
        return _completed(_stdout(failed=failed))

    monkeypatch.setattr(djangoops.health, "_run_command", run)
    assert djangoops.cli.main(_argv()) == 1
    output = capsys.readouterr().out
    assert f"[FAIL] {failed}" in output
    assert output.count("[PASS]") == 4
    assert "Overall: UNHEALTHY" in output


def test_missing_current_is_clean_unhealthy_report(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    unavailable = "not evaluated because there is no valid active release"
    stdout = "\n".join(
        [
            "DJANGOOPS_HEALTH|active_release|fail|no active release is available",
            f"DJANGOOPS_HEALTH|compose_services|fail|{unavailable}",
            f"DJANGOOPS_HEALTH|django_deploy_check|fail|{unavailable}",
            f"DJANGOOPS_HEALTH|database_connectivity|fail|{unavailable}",
            f"DJANGOOPS_HEALTH|migrations_current|fail|{unavailable}",
        ]
    )

    def run(_command: list[str]) -> subprocess.CompletedProcess[str]:
        return _completed(stdout)

    monkeypatch.setattr(djangoops.health, "_run_command", run)
    assert djangoops.cli.main(_argv()) == 1
    assert "no active release is available" in capsys.readouterr().out


def test_transport_failure_is_distinct_and_does_not_echo_remote_stderr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "POSTGRES_PASSWORD=do-not-print"

    def run(_command: list[str]) -> subprocess.CompletedProcess[str]:
        return _completed("", returncode=255, stderr=secret)

    monkeypatch.setattr(djangoops.health, "_run_command", run)
    assert djangoops.cli.main(_argv("--json")) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "health could not be evaluated over SSH" in captured.err
    assert secret not in captured.err


def test_ssh_command_is_batch_mode_read_only_and_django_aware(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity_file = __file__
    seen: list[str] = []

    def run(command: list[str]) -> subprocess.CompletedProcess[str]:
        seen.extend(command)
        return _completed(_stdout())

    monkeypatch.setattr(djangoops.health, "_run_command", run)
    assert djangoops.cli.main(_argv("--identity-file", identity_file)) == 0
    command = seen[-1]
    assert seen[:4] == ["ssh", "-o", "BatchMode=yes", "-p"]
    assert identity_file in seen
    assert "Behavior tests for Phase 0" not in " ".join(seen)
    assert "manage.py check --deploy" in command
    assert "connection.ensure_connection" in command
    assert "migrate --check --noinput" in command
    assert "docker compose -f docker-compose.yml config --services" in command
    assert "docker compose down" not in command
    assert "migrate --noinput" not in command.replace("migrate --check --noinput", "")


def test_invalid_target_is_usage_failure_without_ssh(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    called = False

    def run(_command: list[str]) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        return _completed(_stdout())

    monkeypatch.setattr(djangoops.health, "_run_command", run)
    argv = _argv()
    argv[argv.index("vps.example.com")] = "bad;host"
    assert djangoops.cli.main(argv) == 2
    assert called is False
    assert "error:" in capsys.readouterr().err


def test_malformed_remote_output_is_evaluation_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def run(_command: list[str]) -> subprocess.CompletedProcess[str]:
        return _completed("DJANGOOPS_HEALTH|active_release|pass|ok\n")

    monkeypatch.setattr(djangoops.health, "_run_command", run)
    assert djangoops.cli.main(_argv()) == 2
    assert "response was malformed" in capsys.readouterr().err


def test_untrusted_remote_detail_is_not_echoed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "DATABASE_URL=postgres://secret"
    forged = _stdout().replace(
        "Django database connection succeeds",
        secret,
    )

    def run(_command: list[str]) -> subprocess.CompletedProcess[str]:
        return _completed(forged)

    monkeypatch.setattr(djangoops.health, "_run_command", run)
    assert djangoops.cli.main(_argv("--json")) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "remote health response was malformed" in captured.err
    assert secret not in captured.err
