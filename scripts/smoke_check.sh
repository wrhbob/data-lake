#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_DIR="$ROOT_DIR/file_asset_service"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

cd "$SERVICE_DIR"

PYTHON="${PYTHON:-$SERVICE_DIR/.venv/bin/python}"
if [[ ! -x "$PYTHON" ]]; then
  echo "python venv not found. run ./scripts/install.sh first"
  exit 1
fi

"$PYTHON" - <<'PY'
from pathlib import Path
from app.config import get_settings
from app.database import get_engine

settings = get_settings()
print("database_url:", settings.database_url.split("@")[-1])
print("s3_endpoint_url:", settings.s3_endpoint_url)
print("raw_bucket:", settings.raw_bucket)
print("manifest_exists:", Path("../manifests/latest/parse_manifest_cost_info.csv").exists())

engine = get_engine()
with engine.connect() as connection:
    result = connection.exec_driver_sql("select 1").scalar()
print("db_select_1:", result)
PY

echo "smoke check ok"

