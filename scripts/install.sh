#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_DIR="$ROOT_DIR/file_asset_service"

cd "$SERVICE_DIR"

if command -v uv >/dev/null 2>&1; then
  if [[ ! -d .venv ]]; then
    uv venv .venv
  fi
  uv pip install -e '.[test]'
else
  if [[ ! -d .venv ]]; then
    python3 -m venv .venv
  fi
  ./.venv/bin/python -m pip install --upgrade pip
  ./.venv/bin/python -m pip install -e '.[test]'
fi

./.venv/bin/python -m compileall -q app
./.venv/bin/python -m app.parse_manifest --help >/dev/null

echo "install ok: $SERVICE_DIR/.venv"
