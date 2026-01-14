"""Behavior tests for deterministic Phase 0 Docker Compose generation."""

from __future__ import annotations

import os
import pathlib
from typing import Any

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
        "tls": {"hostname": "app.example.com", "acme_email": "ops@example.com"},
        "storage": {
            "endpoint_url": "https://objects.example.com",
            "region": "eu-west-1",
            "static_bucket": "sample-static",
            "media_bucket": "sample-media",
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


def _read_compose(tmp_path: pathlib.Path) -> dict[str, Any]:
    document = yaml.safe_load((tmp_path / "docker-compose.yml").read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise AssertionError("generated Compose document must be a mapping")
    return document


def test_compose_generates_https_ingress_and_full_stack(tmp_path: pathlib.Path) -> None:
    (tmp_path / "djangoops.yaml").write_text(_config_text(), encoding="utf-8")
    assert _run_in(tmp_path, ["compose"]) == 0
    document = _read_compose(tmp_path)
    services = document["services"]
    assert isinstance(services, dict)
    assert set(services) == {
        "traefik",
        "web",
        "postgres",
        "redis",
        "celery",
        "celery_beat",
    }
    traefik = services["traefik"]
    assert traefik["image"] == "traefik:v3.1.7"
    assert traefik["ports"] == ["80:80", "443:443"]
    assert "/var/run/docker.sock:/var/run/docker.sock:ro" in traefik["volumes"]
    assert "traefik_acme:/letsencrypt" in traefik["volumes"]
    commands = traefik["command"]
    assert "--providers.docker.exposedbydefault=false" in commands
    assert "--entrypoints.web.http.redirections.entrypoint.to=websecure" in commands
    assert "--certificatesresolvers.letsencrypt.acme.email=ops@example.com" in commands
    assert "--certificatesresolvers.letsencrypt.acme.storage=/letsencrypt/acme.json" in commands
    web = services["web"]
    assert "ports" not in web
    assert web["expose"] == ["8000"]
    assert "traefik.http.routers.web.rule=Host(`app.example.com`)" in web["labels"]
    assert "traefik.http.routers.web.tls.certresolver=letsencrypt" in web["labels"]
    assert set(document["volumes"]) == {"traefik_acme", "postgres_data", "redis_data"}
    assert "ports" not in services["postgres"]
    assert "ports" not in services["redis"]


def test_compose_renders_s3_runtime_contract_without_credentials() -> None:
    config = djangoops.config.DjangoOpsConfig.create(
        "sample-app",
        "config",
        "app.example.com",
        "ops@example.com",
        "https://objects.example.com",
        "eu-west-1",
        "sample-static",
        "sample-media",
    )
    rendered = djangoops.compose.render_compose(config)
    document = yaml.safe_load(rendered)
    env = document["services"]["web"]["environment"]
    assert env["DJANGOOPS_S3_ENDPOINT_URL"] == "https://objects.example.com"
    assert env["DJANGOOPS_S3_STATIC_BUCKET"] == "sample-static"
    assert env["DJANGOOPS_S3_MEDIA_BUCKET"] == "sample-media"
    assert env["AWS_REGION"] == "eu-west-1"
    assert env["AWS_ACCESS_KEY_ID"] == ("${AWS_ACCESS_KEY_ID:?AWS_ACCESS_KEY_ID must be set}")
    assert env["AWS_SECRET_ACCESS_KEY"] == (
        "${AWS_SECRET_ACCESS_KEY:?AWS_SECRET_ACCESS_KEY must be set}"
    )
    assert "super-secret-value" not in rendered


def test_compose_uses_configured_django_module_in_application_commands(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "djangoops.yaml").write_text(
        _config_text(django_module="projectconfig"),
        encoding="utf-8",
    )
    assert _run_in(tmp_path, ["compose"]) == 0
    document = _read_compose(tmp_path)
    services = document["services"]
    assert isinstance(services, dict)
    assert services["web"]["command"][1] == "projectconfig.wsgi:application"
    assert services["celery"]["command"][:3] == ["celery", "-A", "projectconfig"]


def test_compose_omits_disabled_app_services_but_keeps_ingress(
    tmp_path: pathlib.Path,
) -> None:
    (tmp_path / "djangoops.yaml").write_text(
        _config_text(postgres=False, redis=False, celery=False, celery_beat=True),
        encoding="utf-8",
    )
    assert _run_in(tmp_path, ["compose"]) == 0
    document = _read_compose(tmp_path)
    services = document["services"]
    assert isinstance(services, dict)
    assert set(services) == {"traefik", "web", "celery_beat"}
    assert set(document["volumes"]) == {"traefik_acme"}
    assert "depends_on" not in services["web"]


def test_compose_render_is_deterministic() -> None:
    config = djangoops.config.DjangoOpsConfig.create("sample-app", "config")
    assert djangoops.compose.render_compose(config) == djangoops.compose.render_compose(config)


def test_compose_refuses_existing_output_without_mutating_it(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "djangoops.yaml").write_text(_config_text(), encoding="utf-8")
    target = tmp_path / "docker-compose.yml"
    target.write_bytes(b"keep: this exactly\n")
    assert _run_in(tmp_path, ["compose"]) == 2
    assert target.read_bytes() == b"keep: this exactly\n"
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
) -> None:
    (tmp_path / "djangoops.yaml").write_text(malformed, encoding="utf-8")
    assert _run_in(tmp_path, ["compose"]) == 2
    assert not (tmp_path / "docker-compose.yml").exists()


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
