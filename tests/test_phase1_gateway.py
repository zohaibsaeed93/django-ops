from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from controlplane.config import GatewayConfig
from controlplane.gateway import (
    AgentGateway,
    AgentSession,
    _validate_job_id,
    _validate_release_root,
)
from controlplane.protocol import agent_pb2


def test_gateway_rejects_unsafe_job_and_release_identifiers() -> None:
    with pytest.raises(ValueError):
        _validate_job_id("../../job")
    with pytest.raises(ValueError):
        _validate_release_root("../other-project")
    with pytest.raises(ValueError):
        _validate_release_root("/srv/other-project")


def test_gateway_config_reads_tokens_only_from_runtime_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cert = tmp_path / "server.pem"
    key = tmp_path / "server.key"
    cert.write_text("certificate", encoding="utf-8")
    key.write_text("key", encoding="utf-8")
    monkeypatch.setenv("DJANGOOPS_CONTROLPLANE_TLS_CERT", str(cert))
    monkeypatch.setenv("DJANGOOPS_CONTROLPLANE_TLS_KEY", str(key))
    monkeypatch.setenv(
        "DJANGOOPS_AGENT_TOKENS_JSON", json.dumps({"agent-1": "synthetic-runtime-token"})
    )
    config = GatewayConfig.from_env()
    assert config.agent_tokens == {"agent-1": "synthetic-runtime-token"}


def test_gateway_bounds_inflight_work_and_completed_retention() -> None:
    asyncio.run(_exercise_gateway_limits())


async def _exercise_gateway_limits() -> None:
    gateway = AgentGateway({"agent-1": "synthetic"}, max_completed_jobs=2, max_inflight_per_agent=1)
    session = AgentSession("agent-1", ("django_diagnostics_v1",), 1, 0)
    gateway._sessions["agent-1"] = session
    assert session.outgoing.maxsize > 0

    first = gateway.diagnostics("agent-1", "job-1", ".")
    pending: asyncio.Future[Any] = asyncio.ensure_future(anext(first))
    await asyncio.sleep(0)
    assert "job-1" in gateway._jobs
    assert gateway._jobs["job-1"].queue.maxsize > 0

    second = gateway.diagnostics("agent-1", "job-2", ".")
    with pytest.raises(RuntimeError, match="capacity"):
        await anext(second)

    state = gateway._jobs["job-1"]
    await state.queue.put(
        agent_pb2.AgentFrame(
            result=agent_pb2.DiagnosticsResult(job_id="job-1", sequence=1, status="healthy")
        )
    )
    frame = await pending
    assert frame.result.status == "healthy"
    with pytest.raises(StopAsyncIteration):
        await anext(first)
    assert "job-1" in gateway._completed_jobs

    gateway._remember_completed("job-2")
    gateway._remember_completed("job-3")
    assert gateway._completed_jobs == {"job-2", "job-3"}
    assert len(gateway._completed_order) == 2

    diagnostics_reuse = gateway.diagnostics("agent-1", "job-2", ".")
    with pytest.raises(ValueError, match="already been used"):
        await anext(diagnostics_reuse)

    _, replay_state = await gateway._open_job(
        "agent-1", "job-2", "kubernetes", allow_completed_reuse=True
    )
    assert replay_state.kind == "kubernetes"
    gateway._jobs.pop("job-2")
