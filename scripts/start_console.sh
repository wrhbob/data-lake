#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_DIR="$ROOT_DIR/file_asset_service"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing env file: $ENV_FILE"
  echo "copy .env.example to .env and fill NAS credentials first"
  exit 1
fi

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

cd "$SERVICE_DIR"

PYTHON="${PYTHON:-$SERVICE_DIR/.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  echo "python venv not found. run ./scripts/install.sh first"
  exit 1
fi

HOST="${FILE_ASSET_HOST:-127.0.0.1}"
PORT="${FILE_ASSET_PORT:-8010}"

echo "crawler console: http://$HOST:$PORT/crawler"
exec "$PYTHON" -m uvicorn app.main:create_app --factory --host "$HOST" --port "$PORT"

