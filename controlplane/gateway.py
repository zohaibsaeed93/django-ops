from __future__ import annotations

import asyncio
import hmac
import re
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

import grpc

from controlplane.protocol import agent_pb2, agent_pb2_grpc

_PROTOCOL_MAJOR = 1
_DIAGNOSTIC_CAPABILITY = "django_diagnostics_v1"
_KUBERNETES_CAPABILITY = "kubernetes_release_v1"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_DEFAULT_MAX_COMPLETED_JOBS = 256
_DEFAULT_MAX_INFLIGHT_PER_AGENT = 4
_MAX_JOB_EVENTS = 32
_MAX_OUTGOING_FRAMES = 32


@dataclass
class JobState:
    agent_id: str
    kind: str
    queue: asyncio.Queue[Any] = field(
        default_factory=lambda: asyncio.Queue(maxsize=_MAX_JOB_EVENTS)
    )
    next_sequence: int = 1
    cancel_sent: bool = False


@dataclass
class AgentSession:
    agent_id: str
    capabilities: tuple[str, ...]
    protocol_major: int
    protocol_minor: int
    outgoing: asyncio.Queue[Any | None] = field(
        default_factory=lambda: asyncio.Queue(maxsize=_MAX_OUTGOING_FRAMES)
    )
    last_heartbeat: float = field(default_factory=time.monotonic)


class AgentGateway(agent_pb2_grpc.AgentControlServicer):  # type: ignore[misc]
    def __init__(
        self,
        agent_tokens: dict[str, str],
        *,
        max_completed_jobs: int = _DEFAULT_MAX_COMPLETED_JOBS,
        max_inflight_per_agent: int = _DEFAULT_MAX_INFLIGHT_PER_AGENT,
    ) -> None:
        if max_completed_jobs < 1 or max_inflight_per_agent < 1:
            raise ValueError("gateway resource limits must be positive")
        self._agent_tokens = dict(agent_tokens)
        self._sessions: dict[str, AgentSession] = {}
        self._jobs: dict[str, JobState] = {}
        self._completed_jobs: set[str] = set()
        self._completed_order: deque[str] = deque()
        self._max_completed_jobs = max_completed_jobs
        self._max_inflight_per_agent = max_inflight_per_agent
        self._lock = asyncio.Lock()

    async def Connect(self, request_iterator: Any, context: Any) -> AsyncIterator[Any]:  # noqa: N802
        try:
            first = await anext(request_iterator)
        except StopAsyncIteration:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "agent hello is required")
            return
        if not first.HasField("hello"):
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "first frame must be agent hello")
            return
        hello = first.hello
        if not _ID_RE.fullmatch(hello.agent_id):
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "invalid agent identity")
            return
        if hello.protocol.major != _PROTOCOL_MAJOR:
            await context.abort(grpc.StatusCode.FAILED_PRECONDITION, "unsupported protocol version")
            return
        if _DIAGNOSTIC_CAPABILITY not in set(hello.capabilities):
            await context.abort(
                grpc.StatusCode.FAILED_PRECONDITION, "diagnostics capability required"
            )
            return
        expected = self._agent_tokens.get(hello.agent_id)
        if expected is None or not hmac.compare_digest(expected, hello.auth_token):
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, "agent authentication failed")
            return
        session = AgentSession(
            hello.agent_id, tuple(hello.capabilities), hello.protocol.major, hello.protocol.minor
        )
        async with self._lock:
            if hello.agent_id in self._sessions:
                await context.abort(
                    grpc.StatusCode.ALREADY_EXISTS, "agent identity already connected"
                )
                return
            self._sessions[hello.agent_id] = session
        consumer = asyncio.create_task(self._consume(session, request_iterator))
        try:
            while True:
                frame = await session.outgoing.get()
                if frame is None:
                    break
                yield frame
        finally:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
            await self._disconnect(session)

    async def _consume(self, session: AgentSession, request_iterator: Any) -> None:
        try:
            async for frame in request_iterator:
                if frame.HasField("heartbeat"):
                    session.last_heartbeat = time.monotonic()
                elif frame.HasField("progress"):
                    await self._accept_event(
                        session, frame.progress.job_id, frame.progress.sequence, frame
                    )
                elif frame.HasField("result"):
                    await self._accept_event(
                        session, frame.result.job_id, frame.result.sequence, frame
                    )
                elif frame.HasField("kubernetes_progress"):
                    event = frame.kubernetes_progress
                    await self._accept_event(session, event.job_id, event.sequence, frame)
                elif frame.HasField("kubernetes_result"):
                    event = frame.kubernetes_result
                    await self._accept_event(session, event.job_id, event.sequence, frame)
        finally:
            await session.outgoing.put(None)

    async def _accept_event(
        self, session: AgentSession, job_id: str, sequence: int, frame: Any
    ) -> None:
        state = self._jobs.get(job_id)
        if state is None or state.agent_id != session.agent_id:
            return
        if sequence != state.next_sequence:
            frame = self._failure_frame(state, job_id, state.next_sequence, "STREAM_ORDER")
            await state.queue.put(frame)
            await session.outgoing.put(
                agent_pb2.ControlFrame(cancel=agent_pb2.CancelJob(job_id=job_id))
            )
            return
        state.next_sequence += 1
        await state.queue.put(frame)

    def _failure_frame(self, state: JobState, job_id: str, sequence: int, code: str) -> Any:
        if state.kind == "kubernetes":
            return agent_pb2.AgentFrame(
                kubernetes_result=agent_pb2.KubernetesReleaseResult(
                    job_id=job_id, sequence=sequence, status="failed", error_code=code
                )
            )
        return agent_pb2.AgentFrame(
            result=agent_pb2.DiagnosticsResult(
                job_id=job_id,
                sequence=sequence,
                status="failed",
                checks=[
                    agent_pb2.DiagnosticCheck(
                        name="control_channel", status="fail", detail=code.lower()
                    )
                ],
            )
        )

    async def _disconnect(self, session: AgentSession) -> None:
        async with self._lock:
            if self._sessions.get(session.agent_id) is session:
                self._sessions.pop(session.agent_id, None)
        for job_id, state in list(self._jobs.items()):
            if state.agent_id == session.agent_id:
                await state.queue.put(
                    self._failure_frame(state, job_id, state.next_sequence, "AGENT_DISCONNECTED")
                )

    async def wait_for_agent(self, agent_id: str, timeout: float = 10.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if agent_id in self._sessions:
                return
            await asyncio.sleep(0.05)
        raise TimeoutError(f"agent {agent_id!r} did not connect")

    def is_connected(self, agent_id: str) -> bool:
        return agent_id in self._sessions

    def heartbeat_age(self, agent_id: str) -> float:
        session = self._sessions.get(agent_id)
        if session is None:
            raise LookupError("agent is not connected")
        return max(0.0, time.monotonic() - session.last_heartbeat)

    def capabilities(self, agent_id: str) -> tuple[str, ...]:
        session = self._sessions.get(agent_id)
        if session is None:
            raise LookupError("agent is not connected")
        return session.capabilities

    def protocol_version(self, agent_id: str) -> tuple[int, int]:
        session = self._sessions.get(agent_id)
        if session is None:
            raise LookupError("agent is not connected")
        return session.protocol_major, session.protocol_minor

    async def _open_job(
        self,
        agent_id: str,
        job_id: str,
        kind: str,
        *,
        allow_completed_reuse: bool = False,
    ) -> tuple[AgentSession, JobState]:
        _validate_job_id(job_id)
        async with self._lock:
            if job_id in self._jobs:
                raise ValueError("job_id is already active")
            if job_id in self._completed_jobs and not allow_completed_reuse:
                raise ValueError("job_id has already been used")
            session = self._sessions.get(agent_id)
            if session is None:
                raise LookupError("agent is not connected")
            inflight = sum(1 for state in self._jobs.values() if state.agent_id == agent_id)
            if inflight >= self._max_inflight_per_agent:
                raise RuntimeError("agent operation capacity reached")
            state = JobState(agent_id, kind)
            self._jobs[job_id] = state
            return session, state

    async def _events(self, job_id: str, state: JobState) -> AsyncIterator[Any]:
        try:
            while True:
                frame = await state.queue.get()
                yield frame
                if frame.HasField("result") or frame.HasField("kubernetes_result"):
                    break
        finally:
            async with self._lock:
                self._jobs.pop(job_id, None)
                self._remember_completed(job_id)

    async def diagnostics(
        self, agent_id: str, job_id: str, release_root: str
    ) -> AsyncIterator[Any]:
        _validate_release_root(release_root)
        session, state = await self._open_job(agent_id, job_id, "diagnostic")
        await session.outgoing.put(
            agent_pb2.ControlFrame(
                diagnostics=agent_pb2.DiagnosticsRequest(job_id=job_id, release_root=release_root)
            )
        )
        async for frame in self._events(job_id, state):
            yield frame

    async def kubernetes_release(
        self,
        *,
        agent_id: str,
        job_id: str,
        namespace: str,
        release_name: str,
        chart_path: str,
        image: str,
        values_json: str,
        credential_ref: str,
        migration_timeout_seconds: int,
        rollout_timeout_seconds: int,
        rollback_policy: str,
        mode: str,
        previous_revision: int,
    ) -> AsyncIterator[Any]:
        session, state = await self._open_job(
            agent_id, job_id, "kubernetes", allow_completed_reuse=True
        )
        if _KUBERNETES_CAPABILITY not in session.capabilities:
            async with self._lock:
                self._jobs.pop(job_id, None)
            raise LookupError("agent does not advertise Kubernetes release capability")
        request = agent_pb2.KubernetesReleaseRequest(
            job_id=job_id,
            namespace=namespace,
            release_name=release_name,
            chart_path=chart_path,
            image=image,
            values_json=values_json,
            credential_ref=credential_ref,
            migration_timeout_seconds=migration_timeout_seconds,
            rollout_timeout_seconds=rollout_timeout_seconds,
            rollback_policy=rollback_policy,
            mode=mode,
            previous_revision=previous_revision,
        )
        await session.outgoing.put(agent_pb2.ControlFrame(kubernetes_release=request))
        async for frame in self._events(job_id, state):
            yield frame

    def _remember_completed(self, job_id: str) -> None:
        if job_id in self._completed_jobs:
            return
        if len(self._completed_order) >= self._max_completed_jobs:
            self._completed_jobs.discard(self._completed_order.popleft())
        self._completed_order.append(job_id)
        self._completed_jobs.add(job_id)

    async def cancel(self, job_id: str) -> bool:
        state = self._jobs.get(job_id)
        if state is None:
            return False
        if state.cancel_sent:
            return True
        session = self._sessions.get(state.agent_id)
        if session is None:
            return False
        state.cancel_sent = True
        await session.outgoing.put(
            agent_pb2.ControlFrame(cancel=agent_pb2.CancelJob(job_id=job_id))
        )
        return True


def _validate_job_id(job_id: str) -> None:
    if not _ID_RE.fullmatch(job_id):
        raise ValueError("job_id must be a safe 1-64 character identifier")


def _validate_release_root(release_root: str) -> None:
    if not release_root or len(release_root) > 256 or "\\" in release_root:
        raise ValueError("release_root must be a bounded relative path")
    path = PurePosixPath(release_root)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("release_root may not escape the configured project root")
