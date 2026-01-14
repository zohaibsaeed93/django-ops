"""Behavior tests for the Phase 0 `djangoops init` command."""

from __future__ import annotations

import os
import pathlib

import djangoops.cli
import djangoops.config
import pytest

BASE_INIT = [
    "init",
    "--project-name",
    "sample-app",
    "--django-module",
    "config.settings",
    "--hostname",
    "app.example.com",
    "--acme-email",
    "ops@example.com",
    "--storage-endpoint-url",
    "https://objects.example.com",
    "--storage-region",
    "eu-west-1",
    "--static-bucket",
    "sample-static",
    "--media-bucket",
    "sample-media",
]

EXPECTED_CONFIG = """schema_version: 2
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

PREVIOUS_SCHEMA_1_CONFIG = """schema_version: 1
project:
  name: sample-app
django:
  module: config.settings
services:
  postgres: true
  redis: true
  celery: true
  celery_beat: true
"""


def _run_in(path: pathlib.Path, argv: list[str]) -> int:
    original = pathlib.Path.cwd()
    os.chdir(path)
    try:
        return djangoops.cli.main(argv)
    finally:
        os.chdir(original)


def test_init_creates_deterministic_round_trippable_config(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = _run_in(tmp_path, BASE_INIT)
    target = tmp_path / "djangoops.yaml"
    assert exit_code == 0
    assert target.read_text(encoding="utf-8") == EXPECTED_CONFIG
    parsed = djangoops.config.parse_config(target.read_text(encoding="utf-8"))
    assert parsed.tls.hostname == "app.example.com"
    assert parsed.storage.static_bucket == "sample-static"
    assert djangoops.config.render_config(parsed) == EXPECTED_CONFIG
    assert str(target) in capsys.readouterr().out


def test_previous_schema_1_config_is_rejected_with_upgrade_guidance() -> None:
    with pytest.raises(ValueError, match=r"schema_version 1.*recreate or upgrade.*TLS and S3"):
        djangoops.config.parse_config(PREVIOUS_SCHEMA_1_CONFIG)


@pytest.mark.parametrize(
    ("project_name", "django_module"),
    [
        ("", "config"),
        ("../escape", "config"),
        ("bad\nname", "config"),
        ("sample", ""),
        ("sample", "../settings"),
        ("sample", "config;rm"),
    ],
)
def test_init_rejects_invalid_project_and_module_inputs_without_creating_file(
    tmp_path: pathlib.Path,
    project_name: str,
    django_module: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv = list(BASE_INIT)
    argv[argv.index("--project-name") + 1] = project_name
    argv[argv.index("--django-module") + 1] = django_module
    assert _run_in(tmp_path, argv) == 2
    assert not (tmp_path / "djangoops.yaml").exists()
    assert "error:" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--hostname", "localhost"),
        ("--hostname", "https://app.example.com"),
        ("--acme-email", "not-an-email"),
        ("--storage-endpoint-url", "ftp://objects.example.com"),
        ("--storage-endpoint-url", "https://user:secret@objects.example.com"),
        ("--storage-endpoint-url", "https://objects.example.com/private"),
        ("--storage-region", "bad region"),
        ("--static-bucket", "Bad_Bucket"),
        ("--media-bucket", "a"),
    ],
)
def test_init_rejects_invalid_network_and_storage_inputs_without_creating_file(
    tmp_path: pathlib.Path,
    flag: str,
    value: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv = list(BASE_INIT)
    index = argv.index(flag) + 1
    argv[index] = value
    assert _run_in(tmp_path, argv) == 2
    assert not (tmp_path / "djangoops.yaml").exists()
    assert "error:" in capsys.readouterr().err


def test_config_contains_no_storage_credentials() -> None:
    config = djangoops.config.DjangoOpsConfig.create(
        "sample-app",
        "config",
        "app.example.com",
        "ops@example.com",
        "https://objects.example.com",
        None,
        "sample-static",
        "sample-media",
    )
    rendered = djangoops.config.render_config(config)
    assert "access_key" not in rendered.lower()
    assert "secret" not in rendered.lower()
    assert "AWS_ACCESS_KEY_ID" not in rendered


def test_init_refuses_existing_file_without_mutating_it(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "djangoops.yaml"
    original_bytes = b"keep: this exactly\n"
    target.write_bytes(original_bytes)
    assert _run_in(tmp_path, BASE_INIT) == 2
    assert target.read_bytes() == original_bytes
    assert "refusing to overwrite" in capsys.readouterr().err


def test_init_defaults_project_name_to_current_directory(tmp_path: pathlib.Path) -> None:
    project_dir = tmp_path / "my_project"
    project_dir.mkdir()
    argv = [value for value in BASE_INIT if value not in {"--project-name", "sample-app"}]
    assert _run_in(project_dir, argv) == 0
    config_text = (project_dir / "djangoops.yaml").read_text(encoding="utf-8")
    assert djangoops.config.parse_config(config_text).project.name == "my_project"
