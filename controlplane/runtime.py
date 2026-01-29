from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from controlplane.gateway import AgentGateway

_gateway: AgentGateway | None = None
_loop: asyncio.AbstractEventLoop | None = None


def register_gateway(gateway: AgentGateway, loop: asyncio.AbstractEventLoop) -> None:
    global _gateway, _loop
    _gateway = gateway
    _loop = loop


def gateway_runtime() -> tuple[AgentGateway, asyncio.AbstractEventLoop]:
    if _gateway is None or _loop is None:
        raise RuntimeError("Phase 1 gateway runtime is unavailable")
    return _gateway, _loop
