from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


def test_installed_wheel_runs_controlplane_outside_checkout(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    source = tmp_path / "source"
    source.mkdir()
    for filename in ("pyproject.toml", "README.md"):
        shutil.copyfile(repo / filename, source / filename)
    for directory in ("cli", "controlplane"):
        shutil.copytree(
            repo / directory,
            source / directory,
            ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"),
        )
    built = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "from setuptools.build_meta import build_wheel; build_wheel('wheelhouse')",
        ],
        cwd=source,
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    installed = tmp_path / "installed"
    with zipfile.ZipFile(next((source / "wheelhouse").glob("*.whl"))) as wheel:
        wheel.extractall(installed)
    verified = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            """
import os
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
os.environ['DJANGO_SETTINGS_MODULE'] = 'controlplane.settings'
import controlplane
assert Path(controlplane.__file__).is_relative_to(Path(sys.argv[1]))
import django
from django.conf import settings
settings.DATABASES['default']['NAME'] = ':memory:'
django.setup()
from controlplane.webserver import main
from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.test import Client
from controlplane.models import Project
call_command('check')
call_command('migrate', verbosity=0)
assert Project.objects.count() == 0
client = Client()
assert client.get('/accounts/login/').status_code == 200
user = get_user_model().objects.create_user('wheel-test')
client.force_login(user)
assert client.get('/').status_code == 200
""",
            str(installed),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert verified.returncode == 0, verified.stdout + verified.stderr
