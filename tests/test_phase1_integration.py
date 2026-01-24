from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import grpc
import pytest

from controlplane.gateway import AgentGateway
from controlplane.protocol import agent_pb2_grpc

_TOKEN = "synthetic-phase1-auth-token"
_COMPOSE_FILE = "ops/compose.prod.yml"


def test_phase1_agent_tls_diagnostics_cancel_and_reconnect(tmp_path: Path) -> None:
    if shutil.which("go") is None or shutil.which("openssl") is None:
        pytest.skip("Phase 1 integration requires Go and OpenSSL")
    asyncio.run(_exercise_phase1(tmp_path))


async def _exercise_phase1(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[1]
    agent_binary = tmp_path / "djangoops-agent"
    subprocess.run(
        ["go", "build", "-o", str(agent_binary), "./cmd/djangoops-agent"],
        cwd=repo / "agent",
        check=True,
        capture_output=True,
        text=True,
    )
    ca, cert, key, wrong_ca = _certificates(tmp_path)
    project = _fixture_project(tmp_path)
    gateway = AgentGateway(
        {
            "agent-1": _TOKEN,
            "bad-auth": "expected-token",
            "bad-protocol": _TOKEN,
            "bad-ca": _TOKEN,
        }
    )
    server, port = await _start_server(gateway, cert, key)
    processes: list[subprocess.Popen[str]] = []
    try:
        good = _start_agent(agent_binary, project, ca, port, "agent-1", _TOKEN)
        processes.append(good)
        await gateway.wait_for_agent("agent-1", timeout=8)
        await asyncio.sleep(0.3)
        assert gateway.heartbeat_age("agent-1") < 2.0

        frames = [frame async for frame in gateway.diagnostics("agent-1", "job-healthy", ".")]
        sequences = [_sequence(frame) for frame in frames]
        assert sequences == list(range(1, len(sequences) + 1))
        result = frames[-1].result
        assert result.status == "healthy"
        checks = {check.name: check for check in result.checks}
        assert checks["active_release"].status == "pass"
        assert checks["compose_services"].status == "pass"
        assert checks["django_check_deploy"].status == "pass"
        assert checks["database"].detail == "connected backend=postgresql"
        assert checks["pending_migrations"].status == "pass"
        assert checks["versions"].detail == "django=5.1.7 python=3.12.9"
        assert checks["celery"].status == "pass"
        assert checks["celery_beat"].status == "pass"

        slow_marker = project / ".slow-diagnostics"
        slow_marker.write_text("slow", encoding="utf-8")
        cancel_task = asyncio.create_task(
            _collect(gateway.diagnostics("agent-1", "job-cancel", "."))
        )
        pid_file = project / ".diagnostic-pid"
        await _wait_for_file(pid_file)
        assert await gateway.cancel("job-cancel") is True
        assert await gateway.cancel("job-cancel") is True
        canceled_frames = await asyncio.wait_for(cancel_task, timeout=8)
        assert canceled_frames[-1].result.status == "canceled"
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        await asyncio.sleep(0.2)
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        assert await gateway.cancel("job-cancel") is False
        slow_marker.unlink()

        await server.stop(0)
        await _wait_until_disconnected(gateway, "agent-1")
        server, rebound = await _start_server(gateway, cert, key, port)
        assert rebound == port
        await gateway.wait_for_agent("agent-1", timeout=8)
        reconnect_frames = [
            frame async for frame in gateway.diagnostics("agent-1", "job-after-reconnect", ".")
        ]
        assert reconnect_frames[-1].result.status == "healthy"

        bad_auth = _start_agent(
            agent_binary, project, ca, port, "bad-auth", "wrong-synthetic-token"
        )
        bad_protocol = _start_agent(
            agent_binary, project, ca, port, "bad-protocol", _TOKEN, protocol_major="2"
        )
        bad_ca = _start_agent(agent_binary, project, wrong_ca, port, "bad-ca", _TOKEN)
        processes.extend([bad_auth, bad_protocol, bad_ca])
        await asyncio.sleep(1.5)
        assert not gateway.is_connected("bad-auth")
        assert not gateway.is_connected("bad-protocol")
        assert not gateway.is_connected("bad-ca")
    finally:
        await server.stop(0)
        for process in processes:
            output = _terminate(process)
            assert _TOKEN not in output
            assert "wrong-synthetic-token" not in output


async def _start_server(
    gateway: AgentGateway, cert: Path, key: Path, port: int = 0
) -> tuple[Any, int]:
    server = grpc.aio.server(options=(("grpc.max_receive_message_length", 1024 * 1024),))
    agent_pb2_grpc.add_AgentControlServicer_to_server(gateway, server)
    credentials = grpc.ssl_server_credentials([(key.read_bytes(), cert.read_bytes())])
    bound = server.add_secure_port(f"127.0.0.1:{port}", credentials)
    assert bound != 0
    await server.start()
    return server, bound


def _start_agent(
    binary: Path,
    project: Path,
    ca: Path,
    port: int,
    agent_id: str,
    token: str,
    *,
    protocol_major: str = "1",
) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env.update(
        {
            "DJANGOOPS_CONTROLPLANE_ENDPOINT": f"127.0.0.1:{port}",
            "DJANGOOPS_CONTROLPLANE_SERVER_NAME": "localhost",
            "DJANGOOPS_CONTROLPLANE_CA_FILE": str(ca),
            "DJANGOOPS_AGENT_ID": agent_id,
            "DJANGOOPS_AGENT_AUTH_TOKEN": token,
            "DJANGOOPS_PROJECT_ROOT": str(project),
            "DJANGOOPS_COMPOSE_FILE": _COMPOSE_FILE,
            "DJANGOOPS_PROTOCOL_MAJOR": protocol_major,
            "DJANGOOPS_AGENT_HEARTBEAT": "200ms",
            "DJANGOOPS_TEST_SLOW_FILE": str(project / ".slow-diagnostics"),
            "DJANGOOPS_TEST_PID_FILE": str(project / ".diagnostic-pid"),
            "DJANGOOPS_EXPECTED_COMPOSE_FILE": str(project / _COMPOSE_FILE),
            "PATH": str(project / "bin") + os.pathsep + env["PATH"],
        }
    )
    return subprocess.Popen(
        [str(binary)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _fixture_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    compose = project / _COMPOSE_FILE
    compose.parent.mkdir(parents=True)
    compose.write_text("services: {}\n", encoding="utf-8")
    bin_dir = project / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(
        """#!/bin/sh
case "$*" in
  *"-f $DJANGOOPS_EXPECTED_COMPOSE_FILE "*) ;;
  *) exit 42 ;;
esac
case "$*" in
  *"config --services"*) printf 'web\\npostgres\\nredis\\ncelery\\ncelery_beat\\n' ;;
  *"ps --services"*) printf 'web\\npostgres\\nredis\\ncelery\\ncelery_beat\\n' ;;
  *"check --deploy"*)
    if [ -f "$DJANGOOPS_TEST_SLOW_FILE" ]; then
      echo $$ > "$DJANGOOPS_TEST_PID_FILE"
      sleep 30
    fi
    ;;
  *"connection.ensure_connection"*) printf 'postgresql\\n' ;;
  *"migrate --check"*) exit 0 ;;
  *"django.get_version"*) printf '5.1.7|3.12.9\\n' ;;
  *) exit 0 ;;
esac
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    return project


def _certificates(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    ca_key = tmp_path / "ca.key"
    ca = tmp_path / "ca.pem"
    server_key = tmp_path / "server.key"
    server_csr = tmp_path / "server.csr"
    server_cert = tmp_path / "server.pem"
    ext = tmp_path / "server.ext"
    wrong_key = tmp_path / "wrong-ca.key"
    wrong_ca = tmp_path / "wrong-ca.pem"
    _openssl(
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        ca_key,
        "-out",
        ca,
        "-days",
        "1",
        "-subj",
        "/CN=DjangoOps Test CA",
    )
    _openssl(
        "req",
        "-new",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        server_key,
        "-out",
        server_csr,
        "-subj",
        "/CN=localhost",
    )
    ext.write_text("subjectAltName=DNS:localhost,IP:127.0.0.1\n", encoding="utf-8")
    _openssl(
        "x509",
        "-req",
        "-in",
        server_csr,
        "-CA",
        ca,
        "-CAkey",
        ca_key,
        "-CAcreateserial",
        "-out",
        server_cert,
        "-days",
        "1",
        "-sha256",
        "-extfile",
        ext,
    )
    _openssl(
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        wrong_key,
        "-out",
        wrong_ca,
        "-days",
        "1",
        "-subj",
        "/CN=Untrusted Test CA",
    )
    return ca, server_cert, server_key, wrong_ca


def _openssl(*args: str | Path) -> None:
    subprocess.run(
        ["openssl", *(str(arg) for arg in args)],
        check=True,
        capture_output=True,
        text=True,
    )


def _sequence(frame: Any) -> int:
    if frame.HasField("progress"):
        return int(frame.progress.sequence)
    return int(frame.result.sequence)


async def _collect(iterator: AsyncIterator[Any]) -> list[Any]:
    return [frame async for frame in iterator]


async def _wait_for_file(path: Path, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if path.exists():
            return
        await asyncio.sleep(0.05)
    raise TimeoutError(f"file not created: {path}")


async def _wait_until_disconnected(
    gateway: AgentGateway, agent_id: str, timeout: float = 5.0
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if not gateway.is_connected(agent_id):
            return
        await asyncio.sleep(0.05)
    raise TimeoutError(f"agent did not disconnect: {agent_id}")


def _terminate(process: subprocess.Popen[str]) -> str:
    if process.poll() is None:
        process.send_signal(signal.SIGTERM)
    try:
        stdout, _ = process.communicate(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, _ = process.communicate(timeout=3)
    return stdout or ""
