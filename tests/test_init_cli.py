"""Behavior tests for the Phase 0 `djangoops init` command."""

from __future__ import annotations

import os
import pathlib

import djangoops.cli
import djangoops.config
import pytest

EXPECTED_CONFIG = """schema_version: 1
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


def test_init_creates_deterministic_round_trippable_config(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    original = pathlib.Path.cwd()
    os.chdir(tmp_path)
    try:
        exit_code = djangoops.cli.main(
            [
                "init",
                "--project-name",
                "sample-app",
                "--django-module",
                "config.settings",
            ]
        )
    finally:
        os.chdir(original)

    target = tmp_path / "djangoops.yaml"
    assert exit_code == 0
    assert target.read_text(encoding="utf-8") == EXPECTED_CONFIG
    parsed = djangoops.config.parse_config(target.read_text(encoding="utf-8"))
    assert parsed == djangoops.config.DjangoOpsConfig.create("sample-app", "config.settings")
    assert djangoops.config.render_config(parsed) == EXPECTED_CONFIG
    assert str(target) in capsys.readouterr().out


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
def test_init_rejects_invalid_inputs_without_creating_file(
    tmp_path: pathlib.Path,
    project_name: str,
    django_module: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    original = pathlib.Path.cwd()
    os.chdir(tmp_path)
    try:
        exit_code = djangoops.cli.main(
            ["init", "--project-name", project_name, "--django-module", django_module]
        )
    finally:
        os.chdir(original)

    assert exit_code == 2
    assert not (tmp_path / "djangoops.yaml").exists()
    assert "error:" in capsys.readouterr().err


def test_init_refuses_existing_file_without_mutating_it(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "djangoops.yaml"
    original_bytes = b"keep: this exactly\n"
    target.write_bytes(original_bytes)
    original = pathlib.Path.cwd()
    os.chdir(tmp_path)
    try:
        exit_code = djangoops.cli.main(
            ["init", "--project-name", "sample", "--django-module", "config"]
        )
    finally:
        os.chdir(original)

    assert exit_code == 2
    assert target.read_bytes() == original_bytes
    assert "refusing to overwrite" in capsys.readouterr().err


def test_init_defaults_project_name_to_current_directory(tmp_path: pathlib.Path) -> None:
    project_dir = tmp_path / "my_project"
    project_dir.mkdir()
    original = pathlib.Path.cwd()
    os.chdir(project_dir)
    try:
        exit_code = djangoops.cli.main(["init", "--django-module", "config"])
    finally:
        os.chdir(original)

    assert exit_code == 0
    config_text = (project_dir / "djangoops.yaml").read_text(encoding="utf-8")
    assert djangoops.config.parse_config(config_text).project.name == "my_project"
