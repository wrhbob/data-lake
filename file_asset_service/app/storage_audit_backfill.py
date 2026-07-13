"""Backfill script: scan MinIO buckets for orphaned objects and create
corresponding FileAsset records in the database.

Objects that already have a FileAsset row (matched on ``object_key``) are
skipped. The sha256 hash is extracted from the object key path pattern
``objects/{XX}/{YY}/{sha256_hex}{extension}``.

Usage::

    python -m app.storage_audit_backfill --dry-run
    python -m app.storage_audit_backfill --bucket cost-raw
    python -m app.storage_audit_backfill --prefix objects/ --limit 500
"""

from __future__ import annotations

import argparse
import logging
import mimetypes
import os
import sys
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.config import Config
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_session_factory, init_db
from app.models import FileAsset

logger = logging.getLogger("storage_audit_backfill")

# Ensure mimetypes knows about common extensions that might otherwise
# resolve to None on some platforms.
_mimetypes_init_once = False


def _init_mimetypes() -> None:
    global _mimetypes_init_once
    if _mimetypes_init_once:
        return
    _mimetypes_init_once = True
    for ext, mime in _EXTRA_MIME_MAP.items():
        mimetypes.add_type(mime, ext)


# Additional extension-to-MIME mappings beyond the stdlib defaults.
_EXTRA_MIME_MAP: dict[str, str] = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".rar": "application/vnd.rar",
    ".7z": "application/x-7z-compressed",
    ".gz": "application/gzip",
    ".bz2": "application/x-bzip2",
    ".sqlite": "application/vnd.sqlite3",
    ".db": "application/octet-stream",
    ".jsonl": "application/jsonl",
    ".ndjson": "application/x-ndjson",
    ".parquet": "application/vnd.apache.parquet",
    ".avro": "application/avro",
    ".msg": "application/vnd.ms-outlook",
    ".eml": "message/rfc822",
    ".dwg": "application/acad",
    ".dxf": "application/dxf",
    ".rvt": "application/octet-stream",
    ".ifc": "application/ifc",
    ".gbq": "application/octet-stream",
}

BATCH_SIZE = 200
DEFAULT_TENANT_CODE = "platform_public"
DEFAULT_VERSION = "1"
DEFAULT_LIFECYCLE = "active"

_BUCKETS = ("cost-raw", "cost-extract", "cost-report")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill FileAsset records by scanning MinIO buckets",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Scan only; do not insert records into the database (default: %(default)s).",
    )
    parser.add_argument(
        "--bucket",
        dest="bucket",
        default=None,
        help=(
            "Single bucket to scan. "
            f"If omitted, all configured buckets ({', '.join(_BUCKETS)}) are scanned."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Maximum number of objects to process (0 = unlimited).",
    )
    parser.add_argument(
        "--prefix",
        default=None,
        help="Filter objects to those whose key starts with this prefix.",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# S3 / MinIO helpers
# ---------------------------------------------------------------------------


def build_s3_client() -> Any:
    """Return a boto3 S3 client configured from project settings."""
    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        region_name=settings.s3_region_name,
        config=Config(signature_version="s3v4"),
    )


def resolve_buckets(requested: str | None) -> list[str]:
    """Return the list of buckets to scan based on the CLI argument."""
    settings = get_settings()
    bucket_map = {
        "cost-raw": settings.raw_bucket,
        "cost-extract": settings.extract_bucket,
        "cost-report": settings.report_bucket,
    }
    if requested:
        resolved = bucket_map.get(requested, requested)
        return [resolved]
    return [bucket_map.get(b, b) for b in _BUCKETS]


def _extract_sha256_from_key(key: str) -> str:
    """Extract sha256 hash from an object key like ``objects/1b/25/1b25c4...5979``.

    The key path pattern is ``objects/{XX}/{YY}/{full_sha256_hex}{extension}``.
    We strip the prefix dirs, then use the filename stem as the sha256 hex.
    """
    parts = key.split("/")
    if len(parts) >= 4 and parts[0] == "objects":
        filename = parts[-1]
        sha256_hex = os.path.splitext(filename)[0]
        if len(sha256_hex) == 64 and all(c in "0123456789abcdef" for c in sha256_hex):
            return sha256_hex
    # Fallback: use ETag (MD5) when key doesn't follow the objects/ layout.
    return ""


def _s3_object_to_file_asset(
    bucket: str,
    obj: dict[str, Any],
    *,
    tenant_code: str = DEFAULT_TENANT_CODE,
) -> FileAsset:
    """Build an unsaved FileAsset instance from an S3 object summary."""
    key = obj["Key"]
    file_name = key.rsplit("/", 1)[-1] if "/" in key else key
    file_ext = os.path.splitext(file_name)[1].lstrip(".").lower()
    mime_type = mimetypes.guess_type(file_name)[0]

    raw_etag = str(obj.get("ETag", "")).strip('"')
    sha256 = _extract_sha256_from_key(key) or raw_etag or "unknown"

    last_modified = obj.get("LastModified")
    if isinstance(last_modified, datetime):
        created_at = last_modified
    else:
        created_at = datetime.now(UTC)

    return FileAsset(
        tenant_code=tenant_code,
        bucket=bucket,
        object_key=key,
        version=DEFAULT_VERSION,
        sha256=sha256,
        file_name=file_name,
        file_ext=file_ext,
        mime_type=mime_type,
        file_size=int(obj.get("Size", 0)),
        etag=raw_etag or None,
        lifecycle=DEFAULT_LIFECYCLE,
        created_at=created_at,
    )


def _skip_key(key: str) -> bool:
    """Return True for keys that should never produce a FileAsset record."""
    return key.endswith("/") or key == "" or key.endswith("/.")


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _fetch_existing_object_keys(
    session: Session, tenant_code: str, keys: list[str]
) -> set[str]:
    """Return the set of object_key values that already have a FileAsset row."""
    if not keys:
        return set()
    stmt = select(FileAsset.object_key).where(
        FileAsset.tenant_code == tenant_code,
        FileAsset.object_key.in_(keys),
    )
    return {row[0] for row in session.execute(stmt).all()}


# ---------------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------------


def scan_and_backfill(
    session: Session,
    client: Any,
    *,
    buckets: list[str],
    limit: int = 0,
    prefix: str | None = None,
    dry_run: bool = False,
    tenant_code: str = DEFAULT_TENANT_CODE,
) -> dict[str, int]:
    """Walk every object in *buckets* and upsert missing FileAsset rows.

    Returns a summary dict with keys: scanned, already_present, inserted, errors.
    """
    stats = {"scanned": 0, "already_present": 0, "inserted": 0, "errors": 0}
    pending: list[FileAsset] = []

    def _flush() -> None:
        nonlocal pending
        if not pending:
            return
        if dry_run:
            for asset in pending:
                logger.info(
                    "[dry-run] would insert file_id=%s bucket=%s key=%s "
                    "name=%s size=%s",
                    asset.file_id,
                    asset.bucket,
                    asset.object_key,
                    asset.file_name,
                    asset.file_size,
                )
            stats["inserted"] += len(pending)
            pending.clear()
            return

        # Batch-check which object_keys already exist so we skip them.
        batch_keys = [a.object_key for a in pending]
        existing = _fetch_existing_object_keys(session, tenant_code, batch_keys)
        new_assets = [a for a in pending if a.object_key not in existing]
        already = len(pending) - len(new_assets)
        stats["already_present"] += already
        if already:
            logger.info(
                "Batch: %d already in DB (matched on object_key), "
                "%d new to insert.",
                already,
                len(new_assets),
            )

        if not new_assets:
            pending.clear()
            session.commit()
            return

        try:
            session.add_all(new_assets)
            session.commit()
            inserted = len(new_assets)
            stats["inserted"] += inserted
            logger.info("Batch: committed %d new FileAsset rows.", inserted)
        except Exception:
            session.rollback()
            # Fall back to insert-one-by-one so a single bad row does not
            # kill the whole batch.
            one_by_one_inserts = 0
            for asset in new_assets:
                try:
                    session.add(asset)
                    session.commit()
                    one_by_one_inserts += 1
                except Exception as exc:
                    session.rollback()
                    stats["errors"] += 1
                    logger.error(
                        "Failed to insert file for key=%s bucket=%s: %s",
                        asset.object_key,
                        asset.bucket,
                        exc,
                    )
            stats["inserted"] += one_by_one_inserts
            if one_by_one_inserts:
                logger.info(
                    "Batch fallback: committed %d rows one-by-one.", one_by_one_inserts
                )
        pending.clear()

    try:
        for bucket in buckets:
            logger.info("Scanning bucket: %s", bucket)
            paginator = client.get_paginator("list_objects_v2")
            page_iterator = paginator.paginate(
                Bucket=bucket,
                Prefix=prefix or "",
                PaginationConfig={"PageSize": 1000},
            )
            for page in page_iterator:
                contents = page.get("Contents") or []
                for obj in contents:
                    key = obj.get("Key", "")
                    if _skip_key(key):
                        continue

                    try:
                        asset = _s3_object_to_file_asset(
                            bucket, obj, tenant_code=tenant_code
                        )
                    except Exception as exc:
                        logger.error("Skipping key=%s in bucket=%s: %s", key, bucket, exc)
                        stats["errors"] += 1
                        continue

                    pending.append(asset)
                    stats["scanned"] += 1

                    if len(pending) >= BATCH_SIZE:
                        _flush()

                    if limit and stats["scanned"] >= limit:
                        logger.info("Reached --limit=%d, stopping scan.", limit)
                        break

                if limit and stats["scanned"] >= limit:
                    break
            if limit and stats["scanned"] >= limit:
                break

        _flush()

    except Exception as exc:
        logger.error("Fatal scan error: %s", exc, exc_info=True)
        stats["errors"] += 1

    return stats


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _print_summary(stats: dict[str, int], dry_run: bool) -> None:
    print("\n" + "=" * 58)
    print("  Storage Audit Backfill  --  Summary")
    if dry_run:
        print("  (DRY RUN -- no database changes were made)")
    print("=" * 58)
    print(f"  Total objects scanned    : {stats['scanned']:>6,d}")
    print(f"  Already in database      : {stats['already_present']:>6,d}")
    print(f"  Newly inserted           : {stats['inserted']:>6,d}")
    print(f"  Errors                   : {stats['errors']:>6,d}")
    print("=" * 58)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    _init_mimetypes()

    logger.info("Initializing database ...")
    init_db()

    buckets = resolve_buckets(args.bucket)
    logger.info("Buckets to scan: %s", buckets)
    if args.prefix:
        logger.info("Prefix filter: %s", args.prefix)
    if args.limit:
        logger.info("Limit: %d objects", args.limit)
    if args.dry_run:
        logger.info("DRY RUN mode -- no records will be inserted.")

    client = build_s3_client()
    session_factory = get_session_factory()
    session = session_factory()

    try:
        stats = scan_and_backfill(
            session,
            client,
            buckets=buckets,
            limit=args.limit,
            prefix=args.prefix,
            dry_run=args.dry_run,
        )
    finally:
        session.close()

    _print_summary(stats, dry_run=args.dry_run)

    if stats["errors"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
