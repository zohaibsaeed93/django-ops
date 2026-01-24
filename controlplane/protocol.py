from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

_GENERATED = Path(__file__).with_name("generated")
if str(_GENERATED) not in sys.path:
    sys.path.insert(0, str(_GENERATED))

import agent_pb2 as agent_pb2  # type: ignore[import-not-found]  # noqa: E402
import agent_pb2_grpc as agent_pb2_grpc  # type: ignore[import-not-found]  # noqa: E402


def bindings() -> tuple[ModuleType, ModuleType]:
    return agent_pb2, agent_pb2_grpc
