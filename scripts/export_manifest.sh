#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_DIR="$ROOT_DIR/file_asset_service"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
OUT_DIR="${OUT_DIR:-$ROOT_DIR/manifests/latest}"

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

mkdir -p "$OUT_DIR"

"$PYTHON" -m app.parse_manifest \
  "$OUT_DIR/parse_manifest.csv" \
  --bucket "${FILE_ASSET_RAW_BUCKET:-cost-raw}" \
  --region-map data/national_cost_info_regions.csv

"$PYTHON" -m app.parse_manifest \
  "$OUT_DIR/parse_manifest.jsonl" \
  --format jsonl \
  --bucket "${FILE_ASSET_RAW_BUCKET:-cost-raw}" \
  --region-map data/national_cost_info_regions.csv

"$PYTHON" -m app.parse_manifest \
  "$OUT_DIR/parse_manifest_cost_info.csv" \
  --bucket "${FILE_ASSET_RAW_BUCKET:-cost-raw}" \
  --region-map data/national_cost_info_regions.csv \
  --domain-type cost_info

"$PYTHON" -m app.parse_manifest \
  "$OUT_DIR/parse_manifest_cost_info.jsonl" \
  --format jsonl \
  --bucket "${FILE_ASSET_RAW_BUCKET:-cost-raw}" \
  --region-map data/national_cost_info_regions.csv \
  --domain-type cost_info

echo "manifest exported: $OUT_DIR"

