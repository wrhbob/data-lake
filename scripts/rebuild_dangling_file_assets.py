"""Rebuild file_asset records for dangling archive_file references.

Root cause: 2026-07-13 manifest_rebuild created archive + archive_file +
ingest_event records but skipped file_asset creation.

This script re-downloads from source_url, uploads to MinIO, and creates
the missing file_asset rows, reusing the existing file_id from ingest_event.
"""

import hashlib
import logging
import mimetypes
import os
import sys
import time
from pathlib import Path

import boto3
import requests
from botocore.config import Config as BotoConfig
from sqlalchemy import create_engine, text

# ── Config ──────────────────────────────────────────────────────────
DATABASE_URL = os.environ.get(
    "FILE_ASSET_DATABASE_URL",
    "postgresql+psycopg://postgres:Evykdht3tGQksTbe@172.16.20.26:5433/postgres",
)
S3_ENDPOINT = os.environ.get("FILE_ASSET_S3_ENDPOINT_URL", "http://172.16.20.26:9000")
S3_ACCESS_KEY = os.environ.get("FILE_ASSET_S3_ACCESS_KEY_ID", "admin")
S3_SECRET_KEY = os.environ.get("FILE_ASSET_S3_SECRET_ACCESS_KEY", "Admin@123456")
S3_BUCKET = os.environ.get("FILE_ASSET_RAW_BUCKET", "cost-raw")
TENANT_CODE = "platform_public"
BATCH_SIZE = int(os.environ.get("REBUILD_BATCH_SIZE", "50"))
DRY_RUN = os.environ.get("REBUILD_DRY_RUN", "0") == "1"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("rebuild")


def make_s3():
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        config=BotoConfig(signature_version="s3v4"),
        region_name="us-east-1",
    )


def make_engine():
    return create_engine(DATABASE_URL)


def compute_sha256(file_path: str) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def object_key(sha256: str, ext: str) -> str:
    """objects/{hash[0:2]}/{hash[0:4]}/{full_hash}.{ext}"""
    return f"objects/{sha256[:2]}/{sha256[:4]}/{sha256}.{ext.lower()}"


def ext_from_name(filename: str) -> str:
    return Path(filename).suffix.lower()


def mime_from_name(filename: str) -> str:
    mime, _ = mimetypes.guess_type(filename)
    return mime or "application/octet-stream"


def fetch_dangling_ids(conn, offset: int = 0, limit: int | None = None):
    """Yield (file_id, original_name, source_url) for dangling records."""
    sql = """
        SELECT ie.file_id, ie.original_name, ie.source_url
        FROM ingest_event ie
        WHERE ie.file_id IN (
            SELECT af.file_id FROM archive_file af
            WHERE af.file_id IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM file_asset fa WHERE fa.file_id = af.file_id)
        )
        AND ie.source_url IS NOT NULL
        AND ie.source_url != ''
        ORDER BY ie.ingested_at
    """
    if limit is not None:
        sql += f" LIMIT {limit}"
    if offset:
        sql += f" OFFSET {offset}"
    result = conn.execute(text(sql))
    return result.fetchall()


def file_asset_exists(conn, file_id: str) -> bool:
    result = conn.execute(
        text("SELECT 1 FROM file_asset WHERE file_id = :fid"),
        {"fid": file_id},
    )
    return result.scalar() is not None


def insert_file_asset(conn, *, file_id, sha256, file_name, file_ext, file_size, mime_type):
    try:
        conn.execute(
            text(
                """
                INSERT INTO file_asset
                    (file_id, tenant_code, bucket, object_key, version, sha256, file_name, file_ext, mime_type, file_size, lifecycle, created_at)
                VALUES
                    (:file_id, :tenant_code, :bucket, :object_key, 'v1', :sha256, :file_name, :file_ext, :mime_type, :file_size, 'active', NOW())
                """
            ),
            {
                "file_id": file_id,
                "tenant_code": TENANT_CODE,
                "bucket": S3_BUCKET,
                "object_key": object_key(sha256, file_ext),
                "sha256": sha256,
                "file_name": file_name,
                "file_ext": file_ext,
                "mime_type": mime_type,
                "file_size": file_size,
            },
        )
    except Exception:
        conn.rollback()
        # Try relink if duplicate
        existing = find_existing_by_sha256(conn, sha256)
        if existing:
            relink_archive_file(conn, file_id, existing)
            return  # handled by relink
        raise


def find_existing_by_sha256(conn, sha256: str) -> str | None:
    result = conn.execute(
        text("SELECT file_id FROM file_asset WHERE sha256 = :sha256 AND tenant_code = :tc"),
        {"sha256": sha256, "tc": TENANT_CODE},
    )
    row = result.fetchone()
    return row[0] if row else None


def relink_archive_file(conn, old_file_id: str, new_file_id: str):
    conn.execute(
        text("UPDATE archive_file SET file_id = :new WHERE file_id = :old"),
        {"new": new_file_id, "old": old_file_id},
    )
    conn.commit()


def process_one(s3, conn, file_id: str, original_name: str, source_url: str) -> dict:
    """Download, hash, upload, insert. Returns result dict."""
    result = {"file_id": file_id, "name": original_name, "url": source_url}

    if file_asset_exists(conn, file_id):
        result["status"] = "skip"
        result["reason"] = "already_exists"
        return result

    ext = ext_from_name(original_name)
    mime_type = mime_from_name(original_name)

    # Download to temp file
    tmp_path = None
    try:
        resp = requests.get(
            source_url,
            timeout=120,
            stream=True,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        )
        resp.raise_for_status()

        # Validate content type — skip HTML error pages
        ct = (resp.headers.get("Content-Type") or "").lower()
        if ct.startswith("text/html"):
            result["status"] = "fail"
            result["reason"] = f"bad_content_type: {ct}"
            log.warning("[%s] SKIP HTML response: %s", file_id[:8], source_url[:100])
            return result

        # Use temp file with correct extension
        import tempfile

        fd, tmp_path = tempfile.mkstemp(suffix=ext)
        os.close(fd)

        file_size = 0
        with open(tmp_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    file_size += len(chunk)

        # Skip tiny files (likely error responses)
        if file_size < 1024 and not ext.lower() in ('.txt', '.csv', '.html'):
            result["status"] = "fail"
            result["reason"] = f"file_too_small: {file_size}B"
            return result

        sha256 = compute_sha256(tmp_path)
        key = object_key(sha256, ext)

        if DRY_RUN:
            result["status"] = "dry_run"
            result["sha256"] = sha256
            result["size"] = file_size
            return result

        # Check for duplicate content
        existing_fid = find_existing_by_sha256(conn, sha256)
        if existing_fid:
            relink_archive_file(conn, file_id, existing_fid)
            result["status"] = "ok"
            result["sha256"] = sha256
            result["size"] = file_size
            result["relinked_to"] = existing_fid
            return result

        # Upload to MinIO
        s3.upload_file(
            tmp_path,
            S3_BUCKET,
            key,
            ExtraArgs={"ContentType": mime_type},
        )

        # Insert file_asset
        insert_file_asset(
            conn,
            file_id=file_id,
            sha256=sha256,
            file_name=original_name,
            file_ext=ext,
            file_size=file_size,
            mime_type=mime_type,
        )
        conn.commit()

        result["status"] = "ok"
        result["sha256"] = sha256
        result["size"] = file_size
        return result

    except requests.RequestException as e:
        result["status"] = "fail"
        result["reason"] = f"download: {e}"
        return result
    except Exception as e:
        conn.rollback()
        result["status"] = "fail"
        result["reason"] = str(e)[:200]
        return result
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def fetch_all_dangling(conn):
    """Fetch all dangling (file_id, original_name, source_url) upfront."""
    result = conn.execute(text("""
        SELECT ie.file_id, ie.original_name, ie.source_url
        FROM ingest_event ie
        WHERE ie.file_id IN (
            SELECT af.file_id FROM archive_file af
            WHERE af.file_id IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM file_asset fa WHERE fa.file_id = af.file_id)
        )
        AND ie.source_url IS NOT NULL
        AND ie.source_url != ''
        ORDER BY ie.ingested_at
    """))
    return result.fetchall()


def main():
    engine = make_engine()
    s3 = make_s3()

    # Verify MinIO connectivity
    try:
        s3.head_bucket(Bucket=S3_BUCKET)
        log.info("MinIO connected, bucket=%s", S3_BUCKET)
    except Exception as e:
        log.error("MinIO unreachable: %s", e)
        sys.exit(1)

    with engine.connect() as conn:
        all_rows = fetch_all_dangling(conn)
    total = len(all_rows)
    log.info("Dangling records to rebuild: %d (DRY_RUN=%s)", total, DRY_RUN)
    if total == 0:
        log.info("Nothing to rebuild.")
        return

    stats = {"ok": 0, "fail": 0, "skip": 0, "dry_run": 0}
    start_time = time.monotonic()
    processed = 0

    for file_id, original_name, source_url in all_rows:
        with engine.connect() as conn:
            # Re-check existence (may have been fixed by previous iteration)
            if file_asset_exists(conn, file_id):
                stats["skip"] += 1
                processed += 1
                continue

            result = process_one(s3, conn, file_id, original_name, source_url)
        stats[result["status"]] = stats.get(result["status"], 0) + 1
        processed += 1

        if result["status"] == "ok":
            log.info(
                "[%d/%d] OK  %s  sha256=%s  size=%d",
                processed, total, original_name,
                result.get("sha256", "?"), result.get("size", 0),
            )
        elif result["status"] == "fail":
            log.warning(
                "[%d/%d] FAIL  %s  reason=%s",
                processed, total, original_name,
                result.get("reason", "?"),
            )

        if processed % 50 == 0:
            elapsed = time.monotonic() - start_time
            log.info(
                "Progress: %d/%d  ok=%d fail=%d skip=%d  elapsed=%.0fs",
                processed, total, stats["ok"], stats["fail"], stats["skip"], elapsed,
            )

    log.info("DONE  ok=%d fail=%d skip=%d dry_run=%d", stats["ok"], stats["fail"], stats["skip"], stats["dry_run"])


if __name__ == "__main__":
    main()
