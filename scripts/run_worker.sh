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

WORKER_ID="${FILE_ASSET_WORKER_ID:-crawler-node-01}"
LEASE_SECONDS="${FILE_ASSET_WORKER_LEASE_SECONDS:-14400}"
LIMIT="${FILE_ASSET_WORKER_LIMIT:-3}"

exec "$PYTHON" -m app.cost_info_worker run \
  --limit "$LIMIT" \
  --worker-id "$WORKER_ID" \
  --lease-seconds "$LEASE_SECONDS" \
  --trigger crawler_node

