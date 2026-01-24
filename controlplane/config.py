from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GatewayConfig:
    listen: str
    certificate: Path
    private_key: Path
    agent_tokens: dict[str, str]

    @classmethod
    def from_env(cls) -> GatewayConfig:
        listen = os.environ.get("DJANGOOPS_CONTROLPLANE_LISTEN", "127.0.0.1:8443")
        certificate = _required_path("DJANGOOPS_CONTROLPLANE_TLS_CERT")
        private_key = _required_path("DJANGOOPS_CONTROLPLANE_TLS_KEY")
        raw_tokens = os.environ.get("DJANGOOPS_AGENT_TOKENS_JSON", "")
        if not raw_tokens:
            raise ValueError("DJANGOOPS_AGENT_TOKENS_JSON is required")
        parsed = json.loads(raw_tokens)
        if not isinstance(parsed, dict) or not parsed:
            raise ValueError("agent token configuration must be a non-empty object")
        tokens: dict[str, str] = {}
        for agent_id, token in parsed.items():
            if not isinstance(agent_id, str) or not isinstance(token, str) or not token:
                raise ValueError("agent token entries must be non-empty strings")
            tokens[agent_id] = token
        return cls(listen, certificate, private_key, tokens)


def _required_path(name: str) -> Path:
    value = os.environ.get(name, "")
    if not value:
        raise ValueError(f"{name} is required")
    path = Path(value)
    if not path.is_file():
        raise ValueError(f"{name} must reference an existing file")
    return path
