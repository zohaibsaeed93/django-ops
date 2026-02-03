from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import grpc

from controlplane.gateway import AgentGateway
from controlplane.protocol import agent_pb2_grpc

TOKEN = "phase3-kind-synthetic-token"


def run(
    *args: str, cwd: Path | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=check, capture_output=True, text=True)


def openssl(*args: str | Path) -> None:
    run("openssl", *(str(arg) for arg in args))


def certificates(root: Path) -> tuple[Path, Path, Path]:
    ca_key, ca = root / "ca.key", root / "ca.pem"
    key = root / "server.key"
    csr = root / "server.csr"
    cert = root / "server.pem"
    ext = root / "server.ext"
    openssl(
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
        "/CN=DjangoOps Phase3 CA",
    )
    openssl(
        "req",
        "-new",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        key,
        "-out",
        csr,
        "-subj",
        "/CN=localhost",
    )
    ext.write_text("subjectAltName=DNS:localhost,IP:127.0.0.1\n", encoding="utf-8")
    openssl(
        "x509",
        "-req",
        "-in",
        csr,
        "-CA",
        ca,
        "-CAkey",
        ca_key,
        "-CAcreateserial",
        "-out",
        cert,
        "-days",
        "1",
        "-sha256",
        "-extfile",
        ext,
    )
    return ca, cert, key


async def start_server(gateway: AgentGateway, cert: Path, key: Path) -> tuple[Any, int]:
    server = grpc.aio.server(options=(("grpc.max_receive_message_length", 1024 * 1024),))
    agent_pb2_grpc.add_AgentControlServicer_to_server(gateway, server)
    creds = grpc.ssl_server_credentials([(key.read_bytes(), cert.read_bytes())])
    port = server.add_secure_port("127.0.0.1:0", creds)
    assert port
    await server.start()
    return server, port


def start_agent(
    binary: Path,
    repo: Path,
    ca: Path,
    port: int,
    credential_root: Path,
) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env.update(
        {
            "DJANGOOPS_CONTROLPLANE_ENDPOINT": f"127.0.0.1:{port}",
            "DJANGOOPS_CONTROLPLANE_SERVER_NAME": "localhost",
            "DJANGOOPS_CONTROLPLANE_CA_FILE": str(ca),
            "DJANGOOPS_AGENT_ID": "agent-kind",
            "DJANGOOPS_AGENT_AUTH_TOKEN": TOKEN,
            "DJANGOOPS_PROJECT_ROOT": str(repo),
            "DJANGOOPS_COMPOSE_FILE": "docker-compose.yml",
            "DJANGOOPS_AGENT_HEARTBEAT": "200ms",
            "DJANGOOPS_KUBERNETES_CREDENTIAL_ROOT": str(credential_root),
        }
    )
    return subprocess.Popen(
        [str(binary)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def stop_agent(process: subprocess.Popen[str]) -> str:
    if process.poll() is None:
        process.send_signal(signal.SIGTERM)
    try:
        output, _ = process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        output, _ = process.communicate(timeout=3)
    assert TOKEN not in (output or "")
    return output or ""


async def wait_for_agent_disconnect(
    gateway: AgentGateway, agent_id: str = "agent-kind", timeout: float = 10.0
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if not gateway.is_connected(agent_id):
            return
        await asyncio.sleep(0.05)
    raise TimeoutError(f"agent {agent_id!r} did not disconnect")


async def collect(iterator: Any) -> list[Any]:
    return [frame async for frame in iterator]


def sequence(frame: Any) -> int:
    if frame.HasField("kubernetes_progress"):
        return int(frame.kubernetes_progress.sequence)
    return int(frame.kubernetes_result.sequence)


def release_call(
    gateway: AgentGateway,
    *,
    job_id: str,
    image: str,
    values_json: str,
    release_name: str = "djangoops",
    rollback_policy: str = "block-after-migration",
    previous_revision: int = 0,
    migration_timeout: int = 60,
    rollout_timeout: int = 60,
    mode: str = "release",
    credential_ref: str = "file:kind-ci",
) -> Any:
    return gateway.kubernetes_release(
        agent_id="agent-kind",
        job_id=job_id,
        namespace="phase3-ci",
        release_name=release_name,
        chart_path="charts/djangoops",
        image=image,
        values_json=values_json,
        credential_ref=credential_ref,
        migration_timeout_seconds=migration_timeout,
        rollout_timeout_seconds=rollout_timeout,
        rollback_policy=rollback_policy,
        mode=mode,
        previous_revision=previous_revision,
    )


def helm_revision(release_name: str) -> int:
    output = run(
        "helm",
        "get",
        "metadata",
        release_name,
        "-n",
        "phase3-ci",
        "-o",
        "json",
    ).stdout
    return int(json.loads(output)["revision"])


async def exercise() -> None:
    repo = Path(__file__).resolve().parents[1]
    state_file = repo / ".djangoops-agent-kubernetes.json"
    state_file.unlink(missing_ok=True)
    image = os.environ["PHASE3_IMAGE_DIGEST"]
    source_kubeconfig = Path(os.environ["PHASE3_KUBECONFIG"])
    with tempfile.TemporaryDirectory(prefix="djangoops-phase3-") as raw:
        root = Path(raw)
        binary = root / "djangoops-agent"
        run(
            "go",
            "build",
            "-o",
            str(binary),
            "./cmd/djangoops-agent",
            cwd=repo / "agent",
        )
        ca, cert, key = certificates(root)
        credential_root = root / "credentials"
        credential_root.mkdir()
        kubeconfig = credential_root / "kind-ci"
        shutil.copyfile(source_kubeconfig, kubeconfig)
        kubeconfig.chmod(0o600)
        gateway = AgentGateway({"agent-kind": TOKEN})
        server, port = await start_server(gateway, cert, key)
        process = start_agent(binary, repo, ca, port, credential_root)
        try:
            await gateway.wait_for_agent("agent-kind", timeout=10)
            assert "kubernetes_release_v1" in gateway.capabilities("agent-kind")
            bad = await collect(
                release_call(
                    gateway,
                    job_id="kind-bad-credential",
                    image=image,
                    values_json="{}",
                    credential_ref="file:missing",
                    mode="validate",
                )
            )
            assert bad[-1].kubernetes_result.error_code == "CLUSTER_CREDENTIAL_UNAVAILABLE"
            validation = await collect(
                release_call(
                    gateway,
                    job_id="kind-validate",
                    image=image,
                    values_json="{}",
                    mode="validate",
                )
            )
            assert validation[-1].kubernetes_result.status == "succeeded"

            values = (
                '{"environmentSecretName":"djangoops-runtime",'
                '"migrationCompatibility":"safe","webReplicas":1}'
            )
            frames = await collect(
                release_call(
                    gateway,
                    job_id="kind-release-1",
                    image=image,
                    values_json=values,
                    rollback_policy="allow",
                    migration_timeout=180,
                    rollout_timeout=180,
                )
            )
            assert [sequence(frame) for frame in frames] == list(range(1, len(frames) + 1))
            result = frames[-1].kubernetes_result
            assert result.status == "succeeded", (result.status, result.error_code)
            assert result.helm_revision >= 1
            good_revision = int(result.helm_revision)
            ready = run(
                "kubectl",
                "get",
                "deployment/djangoops-web",
                "-n",
                "phase3-ci",
                "-o",
                "jsonpath={.status.readyReplicas}",
            ).stdout
            assert int(ready or "0") >= 1

            migration_failure = await collect(
                release_call(
                    gateway,
                    job_id="kind-migration-failure",
                    image=image,
                    release_name="djangoops-migfail",
                    values_json='{"environmentSecretName":"missing-runtime","webReplicas":1}',
                    migration_timeout=20,
                    rollout_timeout=20,
                )
            )
            assert migration_failure[-1].kubernetes_result.error_code == "MIGRATION_OR_APPLY_FAILED"
            missing_deploy = run(
                "kubectl",
                "get",
                "deployment/djangoops-migfail-web",
                "-n",
                "phase3-ci",
                check=False,
            )
            assert missing_deploy.returncode != 0

            blocked_values = (
                '{"environmentSecretName":"djangoops-runtime","migrationCompatibility":"unknown",'
                '"webReplicas":1,"health":{"path":"/missing","port":8000}}'
            )
            blocked = await collect(
                release_call(
                    gateway,
                    job_id="kind-rollback-blocked",
                    image=image,
                    values_json=blocked_values,
                    previous_revision=good_revision,
                    rollout_timeout=20,
                )
            )
            blocked_result = blocked[-1].kubernetes_result
            assert blocked_result.status == "rollback_blocked"
            assert blocked_result.error_code == "ROLLOUT_FAILED_DB_COMPATIBILITY_UNKNOWN"

            safe = await collect(
                release_call(
                    gateway,
                    job_id="kind-safe-rollback",
                    image=image,
                    values_json=blocked_values.replace("unknown", "safe"),
                    rollback_policy="allow",
                    previous_revision=good_revision,
                    rollout_timeout=20,
                )
            )
            safe_result = safe[-1].kubernetes_result
            assert safe_result.status == "rolled_back", (
                safe_result.status,
                safe_result.error_code,
            )
            assert int(safe_result.helm_revision) == good_revision
            restored = run(
                "kubectl",
                "get",
                "deployment/djangoops-web",
                "-n",
                "phase3-ci",
                "-o",
                "jsonpath={.status.readyReplicas}",
            ).stdout
            assert int(restored or "0") >= 1

            cancel_values = (
                '{"environmentSecretName":"djangoops-runtime","webReplicas":1,'
                '"migration":{"command":["sh","-c","sleep 120"],"activeDeadlineSeconds":180}}'
            )
            cancel_frames: list[Any] = []
            async for frame in release_call(
                gateway,
                job_id="kind-cancel-inflight",
                image=image,
                release_name="djangoops-cancel",
                values_json=cancel_values,
                migration_timeout=180,
                rollout_timeout=30,
            ):
                cancel_frames.append(frame)
                if (
                    frame.HasField("kubernetes_progress")
                    and frame.kubernetes_progress.phase == "migrate-apply"
                ):
                    assert await gateway.cancel("kind-cancel-inflight")
            assert cancel_frames[-1].kubernetes_result.status == "cancelled"

            before_restart_revision = helm_revision("djangoops")
            stop_agent(process)
            await wait_for_agent_disconnect(gateway)
            process = start_agent(binary, repo, ca, port, credential_root)
            await gateway.wait_for_agent("agent-kind", timeout=10)
            replay = await collect(
                release_call(
                    gateway,
                    job_id="kind-release-1",
                    image=image,
                    values_json=values,
                    rollback_policy="allow",
                    migration_timeout=180,
                    rollout_timeout=180,
                )
            )
            replay_result = replay[-1].kubernetes_result
            assert replay_result.status == "succeeded", (
                replay_result.status,
                replay_result.error_code,
            )
            assert int(replay_result.helm_revision) == good_revision
            assert helm_revision("djangoops") == before_restart_revision

            uncertain_values = (
                '{"environmentSecretName":"djangoops-runtime","webReplicas":1,'
                '"migration":{"command":["sh","-c","sleep 120"],"activeDeadlineSeconds":180}}'
            )
            disconnected_frames: list[Any] = []
            killed = False
            async for frame in release_call(
                gateway,
                job_id="kind-restart-inflight",
                image=image,
                release_name="djangoops-restart",
                values_json=uncertain_values,
                migration_timeout=180,
                rollout_timeout=30,
            ):
                disconnected_frames.append(frame)
                if (
                    not killed
                    and frame.HasField("kubernetes_progress")
                    and frame.kubernetes_progress.phase == "migrate-apply"
                ):
                    process.kill()
                    process.communicate(timeout=3)
                    await wait_for_agent_disconnect(gateway)
                    killed = True
            assert killed
            disconnect_result = disconnected_frames[-1].kubernetes_result
            assert disconnect_result.error_code == "AGENT_DISCONNECTED"

            process = start_agent(binary, repo, ca, port, credential_root)
            await gateway.wait_for_agent("agent-kind", timeout=10)
            retry = await collect(
                release_call(
                    gateway,
                    job_id="kind-restart-inflight",
                    image=image,
                    release_name="djangoops-restart",
                    values_json=uncertain_values,
                    migration_timeout=180,
                    rollout_timeout=30,
                )
            )
            retry_result = retry[-1].kubernetes_result
            assert retry_result.error_code == "RECOVERY_REQUIRED", (
                retry_result.status,
                retry_result.error_code,
            )
            history = run(
                "helm",
                "history",
                "djangoops-restart",
                "-n",
                "phase3-ci",
                "-o",
                "json",
                check=False,
            )
            if history.returncode == 0:
                assert len(json.loads(history.stdout)) <= 1
        finally:
            await server.stop(0)
            stop_agent(process)
            state_file.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(exercise())
