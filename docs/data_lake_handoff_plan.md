# Data Lake Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the data lake and crawled data out of a personal workstation workflow so teammates can read, parse, and continue processing independently.

**Architecture:** NAS owns the durable state: PostgreSQL catalog, MinIO object storage, and fixed manifest exports. Crawler nodes remain stateless workers: they only run code, acquire DB leases, crawl sources, upload raw files to MinIO, and write metadata to PostgreSQL. Teammates consume read-only DB credentials, MinIO read credentials, and manifest files from a stable NAS/MinIO location.

**Tech Stack:** PostgreSQL 16, MinIO S3-compatible object storage, FastAPI crawler console, SQLAlchemy catalog models, CSV/JSONL parse manifests, Docker Compose on NAS.

---

## Current Verified State

- Local crawler console: `http://127.0.0.1:8010/crawler`
- Current MinIO endpoint: `http://www.djtsoft.x3322.net:9000`
- Current local DB connection target: `127.0.0.1:15432`
- Current caveat: `127.0.0.1:15432` appears to be a local tunnel/listener, so teammates must not depend on this workstation address.
- Local NAS mirror path `/Users/tms/nas-crawler-share` is not mounted; it is only an empty local directory.
- Latest manifest exports:
  - `/Users/tms/Desktop/cloudSchool/out/parse_manifest.csv`: 1845 rows, 1843 ready
  - `/Users/tms/Desktop/cloudSchool/out/parse_manifest.jsonl`
  - `/Users/tms/Desktop/cloudSchool/out/parse_manifest_cost_info.csv`: 1293 rows, 1293 ready
  - `/Users/tms/Desktop/cloudSchool/out/parse_manifest_cost_info.jsonl`
- Current central catalog counts:
  - `data_sources`: 437
  - `archives`: 1002
  - `file_assets`: 1852
  - `ingest_events`: 1925
  - `collection_tasks`: 1454
  - `cost_info_archives`: 875

## Target Ownership Boundary

NAS owns:

- PostgreSQL catalog DB.
- MinIO buckets: `cost-raw`, `cost-extract`, `cost-report`.
- Fixed manifest directory or MinIO prefix.
- Read-only accounts for teammates.
- Backups.

Crawler machines own:

- Crawler code checkout.
- `.env.crawler-node` with NAS endpoints.
- Unique `FILE_ASSET_WORKER_ID`.
- No durable database and no authoritative data copy.

Teammates own:

- Parsing and downstream processing code.
- Read-only DB access.
- Read-only MinIO access.
- Output writes to `cost-extract` or a separate agreed bucket/prefix.

## Recommended Data Products For Handoff

Give teammates these stable contracts:

1. `parse_manifest_cost_info.csv`
   - Primary handoff table for information-price parsing.
   - One row per parseable crawled object.
   - Includes object key, hash, original name, region, period, source URL, and readiness fields.

2. `parse_manifest_cost_info.jsonl`
   - Same content, easier for streaming pipelines.

3. PostgreSQL read-only views or direct tables:
   - `archive`
   - `archive_file`
   - `file_asset`
   - `ingest_event`
   - `data_source`
   - `collection_task`

4. MinIO objects:
   - Raw files in `cost-raw`.
   - Parser outputs should go to `cost-extract`.
   - Reports, manifests, and handoff snapshots should go to `cost-report`.

## Task 1: Make NAS The Only Official Runtime

**Files:**
- Existing: `/Users/tms/Desktop/cloudSchool/file_asset_service/deploy/nas/docker-compose.yml`
- Existing: `/Users/tms/Desktop/cloudSchool/file_asset_service/deploy/nas/.env.example`

- [ ] **Step 1: Create NAS deployment directory**

Run on the NAS host:

```bash
mkdir -p /volume1/docker/file_asset_service
mkdir -p /volume1/data_lake/manifests/latest
mkdir -p /volume1/data_lake/handoff
```

- [ ] **Step 2: Copy compose files to NAS**

Run from the workstation:

```bash
rsync -av /Users/tms/Desktop/cloudSchool/file_asset_service/deploy/nas/ nas:/volume1/docker/file_asset_service/
```

- [ ] **Step 3: Configure NAS secrets**

Run on the NAS host:

```bash
cd /volume1/docker/file_asset_service
cp .env.example .env
chmod 600 .env
```

Edit `.env` with production passwords:

```dotenv
FILE_ASSET_POSTGRES_DB=file_asset
FILE_ASSET_POSTGRES_USER=file_asset
FILE_ASSET_POSTGRES_PASSWORD=<strong-password>
FILE_ASSET_POSTGRES_BIND=0.0.0.0:15432

FILE_ASSET_MINIO_ROOT_USER=<admin-user>
FILE_ASSET_MINIO_ROOT_PASSWORD=<strong-minio-password>
FILE_ASSET_MINIO_API_BIND=0.0.0.0:9000
FILE_ASSET_MINIO_CONSOLE_BIND=0.0.0.0:19001
```

- [ ] **Step 4: Start NAS services**

Run on the NAS host:

```bash
cd /volume1/docker/file_asset_service
docker compose up -d
docker compose ps
```

Expected:

```text
postgres   running/healthy
minio      running/healthy
```

## Task 2: Decide Whether To Migrate Or Keep Current DB

**Files:**
- Existing: `/Users/tms/Desktop/cloudSchool/file_asset_service/app/database.py`

- [ ] **Step 1: Identify the real current PostgreSQL host**

Run on the workstation:

```bash
lsof -nP -iTCP:15432 -sTCP:LISTEN
```

If the listener is `ssh`, the workstation is only forwarding a remote DB. In that case, document the real NAS host and give teammates that host instead of `127.0.0.1`.

- [ ] **Step 2: If current DB is local, dump it**

Run on the workstation only if current DB is not already on NAS:

```bash
pg_dump \
  "postgresql://file_asset:<old-password>@127.0.0.1:15432/file_asset" \
  -Fc \
  -f /Users/tms/Desktop/cloudSchool/out/file_asset_catalog_2026-07-01.dump
```

- [ ] **Step 3: Restore to NAS**

Run from a machine that can reach the NAS PostgreSQL:

```bash
createdb "postgresql://file_asset:<new-password>@<nas-host>:15432/file_asset" || true
pg_restore \
  --clean \
  --if-exists \
  --no-owner \
  -d "postgresql://file_asset:<new-password>@<nas-host>:15432/file_asset" \
  /Users/tms/Desktop/cloudSchool/out/file_asset_catalog_2026-07-01.dump
```

- [ ] **Step 4: Run schema migration on NAS DB**

Run from the repo:

```bash
cd /Users/tms/Desktop/cloudSchool/file_asset_service
export FILE_ASSET_DATABASE_URL='postgresql+psycopg://file_asset:<new-password>@<nas-host>:15432/file_asset'
./.venv/bin/python - <<'PY'
from app.database import init_db
init_db()
print("schema initialized")
PY
```

Expected:

```text
schema initialized
```

## Task 3: Create Teammate Read-Only PostgreSQL Access

**Files:**
- No code changes.

- [ ] **Step 1: Create reader role**

Run in PostgreSQL as admin or owner:

```sql
CREATE ROLE file_asset_reader LOGIN PASSWORD '<reader-password>';
GRANT CONNECT ON DATABASE file_asset TO file_asset_reader;
GRANT USAGE ON SCHEMA public TO file_asset_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO file_asset_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO file_asset_reader;
```

- [ ] **Step 2: Verify reader can query**

Run from a teammate machine:

```bash
psql "postgresql://file_asset_reader:<reader-password>@<nas-host>:15432/file_asset" \
  -c "select count(*) as file_assets from file_asset;"
```

Expected:

```text
 file_assets
-------------
        1852
```

The exact count may increase after later crawler runs.

## Task 4: Create Teammate Read-Only MinIO Access

**Files:**
- No code changes.

- [ ] **Step 1: Create a read-only MinIO policy**

Use the MinIO console or `mc admin policy create`. Policy body:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:ListBucket"],
      "Resource": [
        "arn:aws:s3:::cost-raw",
        "arn:aws:s3:::cost-raw/*",
        "arn:aws:s3:::cost-report",
        "arn:aws:s3:::cost-report/*"
      ]
    }
  ]
}
```

- [ ] **Step 2: Create teammate access key**

Create a user such as:

```text
access_key: cost_reader
secret_key: <reader-secret>
policy: cost-readonly
```

- [ ] **Step 3: Verify object read**

Run from a teammate machine:

```bash
mc alias set costlake http://<nas-host>:9000 cost_reader '<reader-secret>'
mc ls costlake/cost-raw | head
mc ls costlake/cost-report/manifests/latest/
```

Expected:

```text
objects are listed without write permission
```

## Task 5: Publish Manifest To A Fixed Shared Location

**Files:**
- Existing output files under `/Users/tms/Desktop/cloudSchool/out/`
- Existing script: `/Users/tms/Desktop/cloudSchool/file_asset_service/app/parse_manifest.py`

- [ ] **Step 1: Regenerate manifests from NAS DB**

Run from the repo:

```bash
cd /Users/tms/Desktop/cloudSchool/file_asset_service
export FILE_ASSET_DATABASE_URL='postgresql+psycopg://file_asset:<password>@<nas-host>:15432/file_asset'

./.venv/bin/python -m app.parse_manifest \
  /Users/tms/Desktop/cloudSchool/out/parse_manifest.csv \
  --bucket cost-raw \
  --region-map data/national_cost_info_regions.csv

./.venv/bin/python -m app.parse_manifest \
  /Users/tms/Desktop/cloudSchool/out/parse_manifest.jsonl \
  --format jsonl \
  --bucket cost-raw \
  --region-map data/national_cost_info_regions.csv

./.venv/bin/python -m app.parse_manifest \
  /Users/tms/Desktop/cloudSchool/out/parse_manifest_cost_info.csv \
  --bucket cost-raw \
  --region-map data/national_cost_info_regions.csv \
  --domain-type cost_info

./.venv/bin/python -m app.parse_manifest \
  /Users/tms/Desktop/cloudSchool/out/parse_manifest_cost_info.jsonl \
  --format jsonl \
  --bucket cost-raw \
  --region-map data/national_cost_info_regions.csv \
  --domain-type cost_info
```

- [ ] **Step 2: Publish to MinIO fixed prefix**

Run with a MinIO admin or writer account:

```bash
mc alias set costlake http://<nas-host>:9000 <writer-access-key> '<writer-secret>'
mc cp /Users/tms/Desktop/cloudSchool/out/parse_manifest.csv costlake/cost-report/manifests/latest/parse_manifest.csv
mc cp /Users/tms/Desktop/cloudSchool/out/parse_manifest.jsonl costlake/cost-report/manifests/latest/parse_manifest.jsonl
mc cp /Users/tms/Desktop/cloudSchool/out/parse_manifest_cost_info.csv costlake/cost-report/manifests/latest/parse_manifest_cost_info.csv
mc cp /Users/tms/Desktop/cloudSchool/out/parse_manifest_cost_info.jsonl costlake/cost-report/manifests/latest/parse_manifest_cost_info.jsonl
```

- [ ] **Step 3: Publish a dated snapshot**

Run:

```bash
SNAPSHOT_DATE=$(date +%F)
mc mirror --overwrite \
  costlake/cost-report/manifests/latest/ \
  costlake/cost-report/manifests/snapshots/$SNAPSHOT_DATE/
```

Expected teammate contract:

```text
s3://cost-report/manifests/latest/parse_manifest_cost_info.csv
s3://cost-report/manifests/latest/parse_manifest_cost_info.jsonl
```

## Task 6: Configure Stateless Crawler Nodes

**Files:**
- Existing: `/Users/tms/Desktop/cloudSchool/file_asset_service/deploy/crawler-node.env.example`

- [ ] **Step 1: Create one env file per crawler machine**

On each crawler machine:

```bash
cd /path/to/file_asset_service
cp deploy/crawler-node.env.example .env.crawler-node
chmod 600 .env.crawler-node
```

Edit:

```dotenv
FILE_ASSET_DATABASE_URL=postgresql+psycopg://file_asset:<password>@<nas-host>:15432/file_asset
FILE_ASSET_S3_ENDPOINT_URL=http://<nas-host>:9000
FILE_ASSET_S3_ACCESS_KEY_ID=<crawler-writer-access-key>
FILE_ASSET_S3_SECRET_ACCESS_KEY=<crawler-writer-secret>
FILE_ASSET_S3_REGION_NAME=us-east-1

FILE_ASSET_RAW_BUCKET=cost-raw
FILE_ASSET_EXTRACT_BUCKET=cost-extract
FILE_ASSET_REPORT_BUCKET=cost-report

FILE_ASSET_WORKER_ID=crawler-node-01
FILE_ASSET_WORKER_LEASE_SECONDS=14400
```

Each machine must use a unique `FILE_ASSET_WORKER_ID`, such as:

```text
crawler-node-01
crawler-node-02
crawler-node-03
```

- [ ] **Step 2: Run worker**

Run:

```bash
set -a
. ./.env.crawler-node
set +a

python -m app.cost_info_worker run \
  --limit 3 \
  --worker-id "$FILE_ASSET_WORKER_ID" \
  --lease-seconds "$FILE_ASSET_WORKER_LEASE_SECONDS" \
  --trigger crawler_node
```

Expected:

```text
leased_count >= 0
health_status = healthy
```

## Task 7: Define Write Rules For Teammate Processing

**Files:**
- No code changes.

- [ ] **Step 1: Raw data is immutable**

Rule:

```text
No teammate process writes to cost-raw.
Only crawlers write raw source files.
```

- [ ] **Step 2: Parsed outputs go to cost-extract**

Recommended prefix:

```text
s3://cost-extract/info_price/<region_code>/<period>/<sha256>/
```

Example:

```text
s3://cost-extract/info_price/110000/2026-06/3f5.../tables.parquet
s3://cost-extract/info_price/110000/2026-06/3f5.../metadata.json
```

- [ ] **Step 3: Reports and public handoff files go to cost-report**

Recommended prefixes:

```text
s3://cost-report/manifests/latest/
s3://cost-report/manifests/snapshots/YYYY-MM-DD/
s3://cost-report/coverage/
s3://cost-report/quality/
```

## Task 8: Acceptance Checklist

**Files:**
- No code changes.

- [ ] **Step 1: Teammate can query catalog**

Run:

```bash
psql "postgresql://file_asset_reader:<reader-password>@<nas-host>:15432/file_asset" \
  -c "select source_url, original_name from ingest_event order by ingested_at desc limit 5;"
```

Expected:

```text
5 recent ingest rows with original names and source URLs
```

- [ ] **Step 2: Teammate can read manifest**

Run:

```bash
mc cp costlake/cost-report/manifests/latest/parse_manifest_cost_info.csv /tmp/parse_manifest_cost_info.csv
python - <<'PY'
import csv
with open('/tmp/parse_manifest_cost_info.csv', newline='', encoding='utf-8') as f:
    rows = list(csv.DictReader(f))
print(len(rows))
print(rows[0]['object_key'])
PY
```

Expected:

```text
row count > 0
object_key is present
```

- [ ] **Step 3: Teammate can download a raw file by manifest object key**

Run:

```bash
OBJECT_KEY=$(python - <<'PY'
import csv
with open('/tmp/parse_manifest_cost_info.csv', newline='', encoding='utf-8') as f:
    row = next(csv.DictReader(f))
print(row['object_key'])
PY
)
mc cp "costlake/cost-raw/$OBJECT_KEY" /tmp/raw_object
ls -lh /tmp/raw_object
```

Expected:

```text
raw object downloaded successfully
```

- [ ] **Step 4: Teammate cannot write to raw bucket**

Run with reader credentials:

```bash
echo test > /tmp/write-test.txt
mc cp /tmp/write-test.txt costlake/cost-raw/write-test.txt
```

Expected:

```text
Access Denied
```

## Operational Notes

- Do not put PostgreSQL data files on SMB/NFS. Use Docker volume or NAS-local block storage.
- Do not use the workstation address `127.0.0.1:15432` in teammate docs.
- Prefer MinIO fixed prefixes over workstation-mounted NAS paths for handoff.
- If a filesystem share is still needed, mount NAS explicitly and publish the same manifest files under `/volume1/data_lake/manifests/latest`.
- Keep raw bucket immutable from non-crawler users.
- Refresh manifests after crawler batches, then publish to `cost-report/manifests/latest`.
- The 165 `pending` tasks currently visible are legacy `crawl` tasks and are not consumed by the new worker. New worker tasks are `crawl_incremental` and `crawl_issue`.

