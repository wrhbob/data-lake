# Central DB and crawler node deployment

## NAS host

Run PostgreSQL and MinIO on the NAS host. Keep PostgreSQL data on a local Docker volume on that host; do not put the PostgreSQL data directory on SMB/NFS.

```bash
cd deploy/nas
cp .env.example .env
# edit strong passwords and bind addresses
docker compose up -d
```

Initialize or migrate the catalog schema once from any machine that can reach the NAS PostgreSQL:

```bash
export FILE_ASSET_DATABASE_URL='postgresql+psycopg://file_asset:<password>@<nas-host>:15432/file_asset'
python - <<'PY'
from app.database import init_db
init_db()
print("schema initialized")
PY
```

## Crawler node

Crawler nodes do not need local PostgreSQL. Each node points to the NAS PostgreSQL and NAS MinIO endpoint.

```bash
cp deploy/crawler-node.env.example .env.crawler-node
# edit host, credentials, and unique FILE_ASSET_WORKER_ID per machine
set -a
. ./.env.crawler-node
set +a

python -m app.cost_info_worker run \
  --limit 3 \
  --worker-id "$FILE_ASSET_WORKER_ID" \
  --lease-seconds "$FILE_ASSET_WORKER_LEASE_SECONDS" \
  --trigger crawler_node
```

Worker leasing is database-backed:

- Workers atomically change due tasks from `pending` to `running`.
- PostgreSQL uses `FOR UPDATE SKIP LOCKED`, so parallel crawler nodes do not take the same task.
- `worker_id`, `lease_expires_at`, and `heartbeat_at` are written to `collection_task`.
- Expired `running` leases can be reclaimed by another worker.
