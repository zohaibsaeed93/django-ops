"""Behavior tests for deterministic Phase 0 Docker Compose generation."""

from __future__ import annotations

import os
import pathlib

import djangoops.cli
import djangoops.compose
import djangoops.config
import pytest
import yaml


def _config_text(
    *,
    django_module: str = "config",
    postgres: bool = True,
    redis: bool = True,
    celery: bool = True,
    celery_beat: bool = True,
) -> str:
    values = {
        "schema_version": 1,
        "project": {"name": "sample-app"},
        "django": {"module": django_module},
        "services": {
            "postgres": postgres,
            "redis": redis,
            "celery": celery,
            "celery_beat": celery_beat,
        },
    }
    return yaml.safe_dump(values, sort_keys=False)


def _run_in(path: pathlib.Path, argv: list[str]) -> int:
    original = pathlib.Path.cwd()
    os.chdir(path)
    try:
        return djangoops.cli.main(argv)
    finally:
        os.chdir(original)


def test_compose_generates_default_full_stack(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "djangoops.yaml").write_text(_config_text(), encoding="utf-8")

    exit_code = _run_in(tmp_path, ["compose"])

    target = tmp_path / "docker-compose.yml"
    assert exit_code == 0
    document = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert set(document["services"]) == {"web", "postgres", "redis", "celery", "celery_beat"}
    assert document["services"]["postgres"]["image"] == "postgres:16"
    assert document["services"]["redis"]["image"] == "redis:7-alpine"
    assert "ports" not in document["services"]["postgres"]
    assert "ports" not in document["services"]["redis"]
    assert "ports" not in document["services"]["web"]
    assert document["services"]["web"]["expose"] == ["8000"]
    assert set(document["volumes"]) == {"postgres_data", "redis_data"}
    assert document["services"]["postgres"]["restart"] == "unless-stopped"
    assert document["services"]["redis"]["restart"] == "unless-stopped"
    assert "healthcheck" in document["services"]["postgres"]
    assert "healthcheck" in document["services"]["redis"]
    assert "version" not in document
    assert str(pathlib.Path("docker-compose.yml")) in capsys.readouterr().out


def test_compose_uses_configured_django_module_in_application_commands(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "djangoops.yaml").write_text(
        _config_text(django_module="projectconfig"),
        encoding="utf-8",
    )

    assert _run_in(tmp_path, ["compose"]) == 0

    document = yaml.safe_load((tmp_path / "docker-compose.yml").read_text(encoding="utf-8"))
    assert document["services"]["web"]["command"] == [
        "gunicorn",
        "projectconfig.wsgi:application",
        "--bind",
        "0.0.0.0:8000",
    ]
    assert document["services"]["celery"]["command"][:3] == ["celery", "-A", "projectconfig"]
    assert document["services"]["celery_beat"]["command"][:3] == [
        "celery",
        "-A",
        "projectconfig",
    ]


def test_compose_accepts_legacy_settings_module_input(tmp_path: pathlib.Path) -> None:
    (tmp_path / "djangoops.yaml").write_text(
        _config_text(django_module="config.settings"),
        encoding="utf-8",
    )

    assert _run_in(tmp_path, ["compose"]) == 0

    document = yaml.safe_load((tmp_path / "docker-compose.yml").read_text(encoding="utf-8"))
    assert document["services"]["web"]["command"][1] == "config.wsgi:application"
    assert document["services"]["celery"]["command"][:3] == ["celery", "-A", "config"]


def test_compose_omits_disabled_services_and_their_volumes(tmp_path: pathlib.Path) -> None:
    (tmp_path / "djangoops.yaml").write_text(
        _config_text(postgres=False, redis=False, celery=False, celery_beat=True),
        encoding="utf-8",
    )

    assert _run_in(tmp_path, ["compose"]) == 0

    document = yaml.safe_load((tmp_path / "docker-compose.yml").read_text(encoding="utf-8"))
    assert set(document["services"]) == {"web", "celery_beat"}
    assert "volumes" not in document
    assert "depends_on" not in document["services"]["web"]
    assert "depends_on" not in document["services"]["celery_beat"]


def test_compose_render_is_deterministic() -> None:
    config = djangoops.config.DjangoOpsConfig.create("sample-app", "config")

    first = djangoops.compose.render_compose(config)
    second = djangoops.compose.render_compose(config)

    assert first == second
    assert yaml.safe_load(first) == yaml.safe_load(second)


def test_compose_contains_only_secret_references_not_secret_values() -> None:
    config = djangoops.config.DjangoOpsConfig.create("sample-app", "config")
    rendered = djangoops.compose.render_compose(config)

    assert "super-secret-value" not in rendered
    assert "SECRET_KEY:" not in rendered
    assert "AWS_SECRET_ACCESS_KEY:" not in rendered
    assert "SSH_PRIVATE_KEY:" not in rendered
    assert "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set}" in rendered
    assert ".env" in rendered


def test_compose_refuses_existing_output_without_mutating_it(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "djangoops.yaml").write_text(_config_text(), encoding="utf-8")
    target = tmp_path / "docker-compose.yml"
    original_bytes = b"keep: this exactly\n"
    target.write_bytes(original_bytes)

    exit_code = _run_in(tmp_path, ["compose"])

    assert exit_code == 2
    assert target.read_bytes() == original_bytes
    assert "refusing to overwrite" in capsys.readouterr().err


@pytest.mark.parametrize(
    "malformed",
    [
        "not: [valid",
        "schema_version: 999\nproject: {}\ndjango: {}\nservices: {}\n",
        "[]\n",
    ],
)
def test_compose_rejects_malformed_config_without_output(
    tmp_path: pathlib.Path,
    malformed: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "djangoops.yaml").write_text(malformed, encoding="utf-8")

    exit_code = _run_in(tmp_path, ["compose"])

    assert exit_code == 2
    assert not (tmp_path / "docker-compose.yml").exists()
    assert "error:" in capsys.readouterr().err


def test_compose_missing_config_does_not_create_output(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = _run_in(tmp_path, ["compose"])

    assert exit_code == 2
    assert not (tmp_path / "docker-compose.yml").exists()
    assert "djangoops.yaml does not exist" in capsys.readouterr().err


def test_compose_removes_partial_new_file_when_write_fails(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "djangoops.yaml").write_text(_config_text(), encoding="utf-8")
    target = tmp_path / "docker-compose.yml"

    def fail_fsync(_fd: int) -> None:
        raise OSError("simulated write failure")

    monkeypatch.setattr(os, "fsync", fail_fsync)

    with pytest.raises(OSError, match="simulated write failure"):
        djangoops.compose.generate_compose(tmp_path / "djangoops.yaml", target)

    assert not target.exists()


def test_compose_supports_explicit_config_and_output_paths(tmp_path: pathlib.Path) -> None:
    config_path = tmp_path / "custom.yaml"
    output_path = tmp_path / "stack.yaml"
    config_path.write_text(_config_text(), encoding="utf-8")

    assert (
        _run_in(
            tmp_path,
            ["compose", "--config", str(config_path), "--output", str(output_path)],
        )
        == 0
    )
    assert output_path.exists()
    assert not (tmp_path / "docker-compose.yml").exists()
