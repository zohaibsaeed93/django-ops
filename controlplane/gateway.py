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
_CAPABILITY = "django_diagnostics_v1"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_DEFAULT_MAX_COMPLETED_JOBS = 256
_DEFAULT_MAX_INFLIGHT_PER_AGENT = 4
_MAX_JOB_EVENTS = 32
_MAX_OUTGOING_FRAMES = 32


@dataclass
class JobState:
    agent_id: str
    queue: asyncio.Queue[Any] = field(
        default_factory=lambda: asyncio.Queue(maxsize=_MAX_JOB_EVENTS)
    )
    next_sequence: int = 1
    cancel_sent: bool = False


@dataclass
class AgentSession:
    agent_id: str
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
        if _CAPABILITY not in set(hello.capabilities):
            await context.abort(
                grpc.StatusCode.FAILED_PRECONDITION, "diagnostics capability required"
            )
            return
        expected = self._agent_tokens.get(hello.agent_id)
        if expected is None or not hmac.compare_digest(expected, hello.auth_token):
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, "agent authentication failed")
            return

        session = AgentSession(hello.agent_id)
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
        finally:
            await session.outgoing.put(None)

    async def _accept_event(
        self, session: AgentSession, job_id: str, sequence: int, frame: Any
    ) -> None:
        state = self._jobs.get(job_id)
        if state is None or state.agent_id != session.agent_id:
            return
        if sequence != state.next_sequence:
            result = agent_pb2.AgentFrame(
                result=agent_pb2.DiagnosticsResult(
                    job_id=job_id,
                    sequence=state.next_sequence,
                    status="failed",
                    checks=[
                        agent_pb2.DiagnosticCheck(
                            name="stream_order",
                            status="fail",
                            detail="out-of-order diagnostic event rejected",
                        )
                    ],
                )
            )
            await state.queue.put(result)
            await session.outgoing.put(
                agent_pb2.ControlFrame(cancel=agent_pb2.CancelJob(job_id=job_id))
            )
            return
        state.next_sequence += 1
        await state.queue.put(frame)

    async def _disconnect(self, session: AgentSession) -> None:
        async with self._lock:
            if self._sessions.get(session.agent_id) is session:
                self._sessions.pop(session.agent_id, None)
        for job_id, state in list(self._jobs.items()):
            if state.agent_id != session.agent_id:
                continue
            await state.queue.put(
                agent_pb2.AgentFrame(
                    result=agent_pb2.DiagnosticsResult(
                        job_id=job_id,
                        sequence=state.next_sequence,
                        status="disconnected",
                        checks=[
                            agent_pb2.DiagnosticCheck(
                                name="control_channel",
                                status="fail",
                                detail=(
                                    "agent connection lost; diagnostic work canceled fail-closed"
                                ),
                            )
                        ],
                    )
                )
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

    async def diagnostics(
        self, agent_id: str, job_id: str, release_root: str
    ) -> AsyncIterator[Any]:
        _validate_job_id(job_id)
        _validate_release_root(release_root)
        async with self._lock:
            if job_id in self._completed_jobs or job_id in self._jobs:
                raise ValueError("job_id has already been used")
            session = self._sessions.get(agent_id)
            if session is None:
                raise LookupError("agent is not connected")
            inflight = sum(1 for state in self._jobs.values() if state.agent_id == agent_id)
            if inflight >= self._max_inflight_per_agent:
                raise RuntimeError("agent diagnostics capacity reached")
            state = JobState(agent_id)
            self._jobs[job_id] = state
        await session.outgoing.put(
            agent_pb2.ControlFrame(
                diagnostics=agent_pb2.DiagnosticsRequest(job_id=job_id, release_root=release_root)
            )
        )
        try:
            while True:
                frame = await state.queue.get()
                yield frame
                if frame.HasField("result"):
                    break
        finally:
            async with self._lock:
                self._jobs.pop(job_id, None)
                self._remember_completed(job_id)

    def _remember_completed(self, job_id: str) -> None:
        if job_id in self._completed_jobs:
            return
        if len(self._completed_order) >= self._max_completed_jobs:
            evicted = self._completed_order.popleft()
            self._completed_jobs.discard(evicted)
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
