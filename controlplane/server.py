from __future__ import annotations

import asyncio

import grpc

from controlplane.config import GatewayConfig
from controlplane.gateway import AgentGateway
from controlplane.protocol import agent_pb2_grpc


async def serve(config: GatewayConfig) -> None:
    server = grpc.aio.server(options=(("grpc.max_receive_message_length", 1024 * 1024),))
    gateway = AgentGateway(config.agent_tokens)
    agent_pb2_grpc.add_AgentControlServicer_to_server(gateway, server)
    credentials = grpc.ssl_server_credentials(
        [(config.private_key.read_bytes(), config.certificate.read_bytes())]
    )
    bound = server.add_secure_port(config.listen, credentials)
    if bound == 0:
        raise RuntimeError("control-plane TLS listener could not bind")
    await server.start()
    await server.wait_for_termination()


def main() -> None:
    asyncio.run(serve(GatewayConfig.from_env()))


if __name__ == "__main__":
    main()
