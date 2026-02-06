"""Bounded Phase 0 operation identifiers shared with workload telemetry."""

from __future__ import annotations

import re
import secrets

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def operation_id(kind: str) -> str:
    prefix = _SAFE.sub("_", kind)[:16] or "operation"
    return f"{prefix}-{secrets.token_hex(12)}"[:64]
