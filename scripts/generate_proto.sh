#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROTO_DIR="$ROOT/proto/djangoops/agent/v1"
PY_OUT="$ROOT/controlplane/generated"
GO_OUT="$ROOT/agent/gen/agentv1"

mkdir -p "$PY_OUT" "$GO_OUT"
python -m grpc_tools.protoc \
  -I "$PROTO_DIR" \
  --python_out="$PY_OUT" \
  --grpc_python_out="$PY_OUT" \
  --go_out="$GO_OUT" --go_opt=paths=source_relative \
  --go-grpc_out="$GO_OUT" --go-grpc_opt=paths=source_relative \
  "$PROTO_DIR/agent.proto"
