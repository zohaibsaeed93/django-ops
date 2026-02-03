from __future__ import annotations

import asyncio

from controlplane.gateway import AgentGateway
from controlplane.runtime import gateway_runtime as _gateway_runtime


class GatewayRuntimeProxy:
    @property
    def gateway(self) -> AgentGateway | None:
        try:
            gateway, _ = _gateway_runtime()
        except RuntimeError:
            return None
        return gateway

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        try:
            _, loop = _gateway_runtime()
        except RuntimeError:
            return None
        return loop


gateway_runtime = GatewayRuntimeProxy()
