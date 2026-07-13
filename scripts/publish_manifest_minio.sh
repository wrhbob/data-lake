#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
MANIFEST_DIR="${MANIFEST_DIR:-$ROOT_DIR/manifests/latest}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "missing env file: $ENV_FILE"
  echo "copy .env.example to .env and fill NAS credentials first"
  exit 1
fi

if ! command -v mc >/dev/null 2>&1; then
  echo "MinIO Client 'mc' is required"
  exit 1
fi

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

ALIAS="${FILE_ASSET_MC_ALIAS:-costlake}"
ENDPOINT="${FILE_ASSET_S3_ENDPOINT_URL:?FILE_ASSET_S3_ENDPOINT_URL is required}"
ACCESS_KEY="${FILE_ASSET_S3_ACCESS_KEY_ID:?FILE_ASSET_S3_ACCESS_KEY_ID is required}"
SECRET_KEY="${FILE_ASSET_S3_SECRET_ACCESS_KEY:?FILE_ASSET_S3_SECRET_ACCESS_KEY is required}"
REPORT_BUCKET="${FILE_ASSET_REPORT_BUCKET:-cost-report}"

mc alias set "$ALIAS" "$ENDPOINT" "$ACCESS_KEY" "$SECRET_KEY" >/dev/null

mc cp "$MANIFEST_DIR/parse_manifest.csv" "$ALIAS/$REPORT_BUCKET/manifests/latest/parse_manifest.csv"
mc cp "$MANIFEST_DIR/parse_manifest.jsonl" "$ALIAS/$REPORT_BUCKET/manifests/latest/parse_manifest.jsonl"
mc cp "$MANIFEST_DIR/parse_manifest_cost_info.csv" "$ALIAS/$REPORT_BUCKET/manifests/latest/parse_manifest_cost_info.csv"
mc cp "$MANIFEST_DIR/parse_manifest_cost_info.jsonl" "$ALIAS/$REPORT_BUCKET/manifests/latest/parse_manifest_cost_info.jsonl"

SNAPSHOT_DATE="$(date +%F)"
mc mirror --overwrite "$ALIAS/$REPORT_BUCKET/manifests/latest/" "$ALIAS/$REPORT_BUCKET/manifests/snapshots/$SNAPSHOT_DATE/"

echo "manifest published: s3://$REPORT_BUCKET/manifests/latest/"

