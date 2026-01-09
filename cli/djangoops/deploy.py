"""Phase 0 direct-SSH deployment with staged release safety."""

from __future__ import annotations

import io
import os
import re
import shlex
import subprocess
import tarfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Final

from djangoops.compose import COMPOSE_FILENAME, load_config

_HOST_RE: Final = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
_USER_RE: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,31}$")
_RELEASE_RE: Final = re.compile(r"^r[0-9]{8}T[0-9]{6}Z-[0-9]+$")
_EXCLUDED_DIRS: Final = {
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "node_modules",
    "venv",
}
_EXCLUDED_NAMES: Final = {
    ".env",
    ".secrets",
    "id_ed25519",
    "id_rsa",
}
_EXCLUDED_SUFFIXES: Final = {".key", ".pem", ".p12", ".pfx"}


class DeployError(RuntimeError):
    """Expected operational deployment failure safe to show to a user."""


@dataclass(frozen=True, slots=True)
class DeploymentTarget:
    """Validated non-secret SSH target metadata."""

    host: str
    user: str
    port: int
    remote_base: str
    identity_file: Path | None = None

    @classmethod
    def create(
        cls,
        *,
        host: str,
        user: str,
        port: int,
        remote_base: str,
        identity_file: Path | None,
    ) -> DeploymentTarget:
        """Validate target values before any subprocess is executed."""
        if not _HOST_RE.fullmatch(host) or ".." in host or host.startswith("-"):
            raise ValueError("SSH host must be a DNS name or IPv4-style host without shell syntax")
        if not _USER_RE.fullmatch(user) or user.startswith("-"):
            raise ValueError("SSH user contains unsupported characters")
        if not 1 <= port <= 65535:
            raise ValueError("SSH port must be between 1 and 65535")
        _validate_remote_base(remote_base)
        if identity_file is not None and not identity_file.is_file():
            raise ValueError(f"SSH identity file does not exist: {identity_file}")
        return cls(
            host=host,
            user=user,
            port=port,
            remote_base=remote_base,
            identity_file=identity_file,
        )


@dataclass(frozen=True, slots=True)
class DeploymentPlan:
    """Immutable release plan derived from validated local inputs."""

    project_root: Path
    project_name: str
    target: DeploymentTarget
    release_id: str
    compose_file: str = COMPOSE_FILENAME

    @property
    def release_dir(self) -> str:
        return f"{self.target.remote_base}/releases/{self.release_id}"

    @property
    def current_path(self) -> str:
        return f"{self.target.remote_base}/current"

    @property
    def shared_env_path(self) -> str:
        return f"{self.target.remote_base}/shared/.env"

    @property
    def previous_path(self) -> str:
        return f"{self.target.remote_base}/.previous-{self.release_id}"


def deploy_project(
    *,
    project_root: Path,
    config_path: Path,
    compose_path: Path,
    target: DeploymentTarget,
    now: datetime | None = None,
) -> str:
    """Upload, start, and atomically activate one staged release."""
    config = load_config(config_path)
    if not compose_path.is_file():
        raise DeployError(f"{compose_path.name} does not exist; run 'djangoops compose' first")
    root = project_root.resolve()
    try:
        compose_relative = compose_path.resolve().relative_to(root)
        config_path.resolve().relative_to(root)
    except ValueError as exc:
        raise DeployError("config and Compose files must be inside the project directory") from exc

    release_id = _release_id(now)
    plan = DeploymentPlan(
        project_root=root,
        project_name=config.project.name,
        target=target,
        release_id=release_id,
        compose_file=compose_relative.as_posix(),
    )
    archive = _build_archive(root)

    try:
        _upload_release(plan, archive)
    except DeployError:
        _cleanup_release(plan)
        raise

    try:
        _capture_previous_current(plan)
    except DeployError:
        _cleanup_release(plan)
        raise

    try:
        _start_release(plan)
    except DeployError:
        _restore_or_stop_failed_release(plan)
        _cleanup_previous_pointer(plan)
        _cleanup_release(plan)
        raise

    try:
        _activate_release(plan)
    except DeployError:
        _restore_or_stop_failed_release(plan)
        _cleanup_pending_pointer(plan)
        _cleanup_previous_pointer(plan)
        _cleanup_release(plan)
        raise

    _cleanup_previous_pointer(plan)
    return release_id


def _release_id(now: datetime | None) -> str:
    value = now if now is not None else datetime.now(UTC)
    if value.tzinfo is None:
        raise ValueError("release timestamp must be timezone-aware")
    stamp = value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"r{stamp}-{os.getpid()}"


def _build_archive(project_root: Path) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
        for path in _iter_deploy_files(project_root):
            relative = path.relative_to(project_root)
            archive.add(path, arcname=relative.as_posix(), recursive=False)
    return buffer.getvalue()


def _iter_deploy_files(project_root: Path) -> list[Path]:
    selected: list[Path] = []
    for root, dirnames, filenames in os.walk(project_root, followlinks=False):
        root_path = Path(root)
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in _EXCLUDED_DIRS and not (root_path / name).is_symlink()
        )
        for name in sorted(filenames):
            path = root_path / name
            if path.is_symlink() or not path.is_file() or _is_secret_or_local_only(name):
                continue
            selected.append(path)
    return selected


def _is_secret_or_local_only(name: str) -> bool:
    if name in _EXCLUDED_NAMES:
        return True
    if name.startswith(".env.") and name != ".env.example":
        return True
    return Path(name).suffix.lower() in _EXCLUDED_SUFFIXES


def _ssh_argv(target: DeploymentTarget, remote_command: str) -> list[str]:
    argv = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-p",
        str(target.port),
    ]
    if target.identity_file is not None:
        argv.extend(["-i", str(target.identity_file)])
    argv.extend([f"{target.user}@{target.host}", remote_command])
    return argv


def _run_ssh(
    target: DeploymentTarget,
    remote_command: str,
    *,
    input_bytes: bytes | None = None,
) -> None:
    try:
        result = subprocess.run(
            _ssh_argv(target, remote_command),
            input=input_bytes,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise DeployError("unable to execute the system OpenSSH client") from exc
    if result.returncode != 0:
        raise DeployError("remote SSH command failed")


def _upload_release(plan: DeploymentPlan, archive: bytes) -> None:
    release = shlex.quote(plan.release_dir)
    command = f"umask 077; mkdir -p {release} && tar -xzf - -C {release}"
    try:
        _run_ssh(plan.target, command, input_bytes=archive)
    except DeployError as exc:
        raise DeployError("release upload failed over SSH") from exc


def _capture_previous_current(plan: DeploymentPlan) -> None:
    """Persist pre-deploy current state before startup or pointer activation."""
    current = shlex.quote(plan.current_path)
    previous = shlex.quote(plan.previous_path)
    command = (
        "set -eu; "
        f"if test -L {current} || test -d {current}; then "
        f"previous_target=$(readlink -f -- {current}); "
        'test -n "$previous_target"; '
        f'ln -sfn -- "$previous_target" {previous}; '
        "else "
        f"rm -f -- {previous}; "
        "fi"
    )
    try:
        _run_ssh(plan.target, command)
    except DeployError as exc:
        raise DeployError("unable to preserve the pre-deployment current release") from exc


def _start_release(plan: DeploymentPlan) -> None:
    release = shlex.quote(plan.release_dir)
    shared_env = shlex.quote(plan.shared_env_path)
    project = shlex.quote(plan.project_name)
    compose = shlex.quote(plan.compose_file)
    command = (
        "set -eu; "
        f"test -f {shared_env}; "
        f"ln -sfn {shared_env} {release}/.env; "
        f"cd {release}; "
        f"docker compose -p {project} -f {compose} up -d --build"
    )
    try:
        _run_ssh(plan.target, command)
    except DeployError as exc:
        raise DeployError(
            "remote Docker Compose startup failed; previous release restoration was attempted"
        ) from exc


def _activate_release(plan: DeploymentPlan) -> None:
    base = shlex.quote(plan.target.remote_base)
    release = shlex.quote(plan.release_dir)
    current = shlex.quote(plan.current_path)
    pending = shlex.quote(f"{plan.target.remote_base}/.current-{plan.release_id}")
    command = (
        "set -eu; "
        f"mkdir -p {base}/releases {base}/shared; "
        f"ln -sfn {release} {pending}; "
        f"mv -Tf {pending} {current}"
    )
    try:
        _run_ssh(plan.target, command)
    except DeployError as exc:
        raise DeployError("release started but current pointer activation failed") from exc


def _restore_or_stop_failed_release(plan: DeploymentPlan) -> None:
    """Recover from the saved pre-deploy state, never post-failure current state."""
    current = shlex.quote(plan.current_path)
    previous = shlex.quote(plan.previous_path)
    restore = shlex.quote(f"{plan.target.remote_base}/.restore-{plan.release_id}")
    release = shlex.quote(plan.release_dir)
    project = shlex.quote(plan.project_name)
    compose = shlex.quote(plan.compose_file)
    command = (
        "set -eu; "
        f"if test -L {previous}; then "
        f"previous_target=$(readlink -- {previous}); "
        'test -n "$previous_target"; '
        f'ln -sfn -- "$previous_target" {restore}; '
        f"mv -Tf {restore} {current}; "
        'cd "$previous_target"; '
        f"docker compose -p {project} -f {compose} up -d --build; "
        "else "
        f"if test -L {current}; then "
        f"current_target=$(readlink -- {current}); "
        f'if test "$current_target" = {release}; then rm -f -- {current}; fi; '
        "fi; "
        f"cd {release}; docker compose -p {project} -f {compose} down; "
        "fi"
    )
    try:
        _run_ssh(plan.target, command)
    except DeployError:
        # Preserve the original deployment error. No -v/volume prune is ever used;
        # any remaining release directory is still bounded for operator recovery.
        return


def _cleanup_pending_pointer(plan: DeploymentPlan) -> None:
    if not _RELEASE_RE.fullmatch(plan.release_id):
        return
    pending = shlex.quote(f"{plan.target.remote_base}/.current-{plan.release_id}")
    try:
        _run_ssh(plan.target, f"rm -f -- {pending}")
    except DeployError:
        return


def _cleanup_previous_pointer(plan: DeploymentPlan) -> None:
    if not _RELEASE_RE.fullmatch(plan.release_id):
        return
    previous = shlex.quote(plan.previous_path)
    restore = shlex.quote(f"{plan.target.remote_base}/.restore-{plan.release_id}")
    try:
        _run_ssh(plan.target, f"rm -f -- {previous} {restore}")
    except DeployError:
        return


def _cleanup_release(plan: DeploymentPlan) -> None:
    if not _RELEASE_RE.fullmatch(plan.release_id):
        return
    release = shlex.quote(plan.release_dir)
    command = f"rm -rf -- {release}"
    try:
        _run_ssh(plan.target, command)
    except DeployError:
        return


def _validate_remote_base(value: str) -> None:
    if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("remote base directory contains control characters")
    path = PurePosixPath(value)
    if not path.is_absolute():
        raise ValueError("remote base directory must be an absolute POSIX path")
    if any(part in {".", ".."} for part in path.parts):
        raise ValueError("remote base directory must not contain '.' or '..' segments")
    if value == "/":
        raise ValueError("remote base directory must not be the filesystem root")
