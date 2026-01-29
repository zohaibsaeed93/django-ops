from __future__ import annotations

import asyncio
import os

import uvicorn

from controlplane.config import GatewayConfig
from controlplane.runtime import register_gateway
from controlplane.server import build_server


async def serve() -> None:
    grpc_server, gateway = build_server(GatewayConfig.from_env())
    register_gateway(gateway, asyncio.get_running_loop())
    await grpc_server.start()
    config = uvicorn.Config(
        "controlplane.asgi:application",
        host=os.environ.get("DJANGOOPS_WEB_HOST", "127.0.0.1"),
        port=int(os.environ.get("DJANGOOPS_WEB_PORT", "8000")),
        log_level="info",
    )
    http = uvicorn.Server(config)
    try:
        await http.serve()
    finally:
        await grpc_server.stop(5)


def main() -> None:
    asyncio.run(serve())
