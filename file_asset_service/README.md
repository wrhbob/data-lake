# File Asset Service v0.1

This is the standalone L0 file asset service for the first KPI Cloud refactor slice.

## Scope

- Catalog tables: `file_asset`, `ingest_event`, `file_processing`, `file_relation`.
- Object storage: MinIO-compatible S3 buckets `cost-raw`, `cost-extract`, `cost-report`.
- Ingestion rule: compute `sha256` first, then dedupe by `(tenant_code, sha256)`.
- `ingest_event` records each collection event and intentionally does not store `tenant_code`; tenant is derived from `file_asset`.
- Processor outputs, including unzip child files, must call the same registration path as external uploads.
- The first executable processor is `unzip`; `pdf_extract`, `xls_parse`, and `info_price_parse` are catalog tasks for the next slice.

## Local Test

```bash
python3 -m venv /tmp/file-asset-service-venv
/tmp/file-asset-service-venv/bin/python -m pip install -e '.[test]'
/tmp/file-asset-service-venv/bin/python -m pytest -q
```

## Runtime topology

The NAS hosts the only shared PostgreSQL instance and the data lake object
storage. Office and home computers run application code only; they must use
explicit NAS credentials and never start a local database.

```bash
cp ../.env.example ../.env
# Fill in the NAS PostgreSQL and object-storage credentials in ../.env.
python serve.py
```

`serve.py` exits before startup when the database URL or object-storage
credentials are missing, or when the database URL is not PostgreSQL. To run
only the application container after creating `../.env`, use:

```bash
docker compose up --build console
```

## API Smoke

Upload a zip or information-price file:

```bash
curl -F tenant_code=tenant_a \
  -F source_type=info_price \
  -F batch_id=batch-001 \
  -F file=@/path/to/info-price.zip \
  http://127.0.0.1:8100/api/file-assets/ingest
```

Run a derived unzip task from the returned `processing_ids`:

```bash
curl -X POST http://127.0.0.1:8100/api/file-processing/{processing_id}/run
```

The runner reads the parent object from MinIO, extracts regular zip members, and registers each child through the same `(tenant_code, sha256)` dedupe path. Re-running ingestion for the same zip records a new `ingest_event` but does not duplicate `file_asset` or object storage entries.

## T2 data_source / collection_task

`data_source` defines where files come from. `source_scope` controls where assets land:

- `platform_public` writes files to the reserved `platform_public` tenant asset pool.
- `tenant_private` writes files to the customer tenant asset pool.

`managed_by` records who operates the source:

- `platform` for platform-operated public sources and platform-operated tenant sources.
- `tenant` for customer-operated tenant sources.

`collection_task` is one execution of a source. It derives `asset_tenant_code` from its source and records the operator with `operator_type`.

Example platform public source:

```bash
curl -X POST http://127.0.0.1:8100/api/data-sources \
  -H 'Content-Type: application/json' \
  -d '{
    "source_scope": "platform_public",
    "managed_by": "platform",
    "source_type": "info_price",
    "connector_type": "http_site",
    "name": "广州建设工程信息价",
    "data_domain": "info_price"
  }'
```

Example tenant-operated external source:

```bash
curl -X POST http://127.0.0.1:8100/api/data-sources \
  -H 'Content-Type: application/json' \
  -d '{
    "source_scope": "tenant_private",
    "tenant_code": "tenant_a",
    "managed_by": "tenant",
    "source_type": "info_price",
    "connector_type": "http_site",
    "name": "客户自定义信息价网站",
    "data_domain": "info_price"
  }'
```

Task-linked ingest preserves the existing rule that `ingest_event` does not store `tenant_code`; tenant is derived from `file_asset`.
