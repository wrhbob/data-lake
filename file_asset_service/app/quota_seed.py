"""P0-3 seed-batch ingestion for the 四川2025 quota PDFs (SPEC-QA-001 §9.1, P3-D1).

Raw-only: creates DataSource / Blob / FileAsset / IngestEvent through the shared
``register_asset`` path. It NEVER creates Archive / Publication Set (that is P0-4).

Safety contract (P3-D1):
- dry-run is the default; only ``--apply`` writes to storage/DB;
- before writing, the plan (file list + sha256 + target DataSource + expected new count)
  is reported;
- source files are only *copied* in — never moved, overwritten or deleted;
- idempotent: a stable DataSource identity + ``source_item_key = relative path`` means a
  re-run does not create duplicate DataSource / FileAsset / IngestEvent rows.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy import inspect, select
from sqlalchemy.engine import Connectable
from sqlalchemy.orm import Session

from app.assets import compute_sha256, register_asset
from app.collection import create_data_source
from app.database import get_session_factory, init_db
from app.models import DataSource, FileAsset, IngestEvent
from app.storage import ObjectStore, get_object_store

# Repo root holds both file_asset_service/ and quota/; this file lives at
# file_asset_service/app/quota_seed.py -> parents[2] == repo root.
DEFAULT_SEED_ROOT = Path(__file__).resolve().parents[2] / "quota" / "四川2025"

SEED_SOURCE_NAME = "四川2025清单定额-seed"
SEED_SOURCE_TYPE = "quota_registry"
SEED_BATCH_ID = "quota-seed:sichuan-2025"
SEED_REGION_CODE = "510000"
SEED_PROVINCE = "四川省"
SEED_ACTOR = "quota-seed"

# P0-2 schema surface that MUST exist before writing objects when init_db() is skipped.
REQUIRED_QUOTA_TABLES: dict[str, set[str]] = {
    "quota_publication_set": {"publication_set_id", "biz_key", "quota_system_type", "jurisdiction_level"},
    "quota_archive_profile": {"archive_id", "publication_set_id", "document_role"},
    "quota_projection_candidate": {
        "candidate_id",
        "file_id",
        "projection_status",
        "duplicate_of_file_id",
        "suggested_metadata",
        "matched_rules",
        "suggestion_confidence",
    },
    "quota_publication_relation": {
        "relation_id",
        "source_publication_set_id",
        "target_publication_set_id",
        "relation_type",
    },
    "quota_dictionary": {"dict_type", "code"},
}
REQUIRED_ARCHIVE_FILE_COLUMNS = {"page_range", "link_source", "linked_by"}


class QuotaSchemaIncomplete(RuntimeError):
    """Raised when --skip-init is used but the P0-2 quota schema is not fully present."""


def verify_quota_schema(bind: Connectable) -> None:
    """Fail fast (before any object/DB write) if the P0-2 quota schema is incomplete.

    --skip-init skips the MIGRATION step; it must NOT skip schema compatibility checks.
    """
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())
    problems: list[str] = []

    for table, required_columns in REQUIRED_QUOTA_TABLES.items():
        if table not in existing_tables:
            problems.append(f"missing table: {table}")
            continue
        actual = {column["name"] for column in inspector.get_columns(table)}
        missing = required_columns - actual
        if missing:
            problems.append(f"table {table} missing columns: {sorted(missing)}")

    if "archive_file" not in existing_tables:
        problems.append("missing table: archive_file")
    else:
        actual = {column["name"] for column in inspector.get_columns("archive_file")}
        missing = REQUIRED_ARCHIVE_FILE_COLUMNS - actual
        if missing:
            problems.append(f"table archive_file missing P0-2 columns: {sorted(missing)}")

    if "quota_projection_candidate" in existing_tables:
        uniques = inspector.get_unique_constraints("quota_projection_candidate")
        if not any(set(u.get("column_names") or []) == {"file_id"} for u in uniques):
            problems.append("quota_projection_candidate missing UNIQUE(file_id) idempotency constraint")

    if problems:
        raise QuotaSchemaIncomplete(
            "P0-2 quota schema is incomplete; refusing to write. "
            "NOTE: --skip-init skips MIGRATION, NOT schema compatibility checks. "
            "Run init_db() (or migrate_quota_tables) first. Problems: " + "; ".join(problems)
        )


@dataclass(frozen=True)
class SeedFileplan:
    relative_path: str
    file_name: str
    sha256: str
    byte_size: int
    already_ingested: bool
    hash_conflict: bool
    existing_sha256: str | None


def _discover_pdfs(root: Path) -> list[Path]:
    if not root.exists() or not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*.pdf") if p.is_file())


def _relative_path(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def find_seed_source(session: Session) -> DataSource | None:
    return session.scalar(
        select(DataSource).where(
            DataSource.data_domain == "quota",
            DataSource.source_type == SEED_SOURCE_TYPE,
            DataSource.asset_tenant_code == "platform_public",
            DataSource.name == SEED_SOURCE_NAME,
        )
    )


def get_or_create_seed_source(session: Session) -> tuple[DataSource, bool]:
    existing = find_seed_source(session)
    if existing is not None:
        return existing, False
    source = create_data_source(
        session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type=SEED_SOURCE_TYPE,
        connector_type="manual_seed",
        name=SEED_SOURCE_NAME,
        data_domain="quota",
        province=SEED_PROVINCE,
        region_code=SEED_REGION_CODE,
        config={"seed_batch": SEED_BATCH_ID, "note": "四川2025 quota seed batch (P0-3)"},
        created_by=SEED_ACTOR,
    )
    return source, True


def _existing_ingest_sha(session: Session, *, source_id: str | None, source_item_key: str) -> str | None:
    """Return the sha256 already ingested under this source_item_key, or None."""
    if source_id is None:
        return None
    return session.scalar(
        select(FileAsset.sha256)
        .join(IngestEvent, IngestEvent.file_id == FileAsset.file_id)
        .where(IngestEvent.source_id == source_id, IngestEvent.source_item_key == source_item_key)
        .order_by(IngestEvent.ingested_at.asc())
    )


def _already_ingested(session: Session, *, source_id: str | None, source_item_key: str) -> bool:
    if source_id is None:
        return False
    return (
        session.scalar(
            select(IngestEvent.event_id).where(
                IngestEvent.source_id == source_id,
                IngestEvent.source_item_key == source_item_key,
            )
        )
        is not None
    )


def plan_seed(session: Session, root: Path = DEFAULT_SEED_ROOT) -> dict:
    """Read-only: compute the ingestion plan without writing anything."""
    source = find_seed_source(session)
    source_id = source.source_id if source else None
    files: list[SeedFileplan] = []
    for path in _discover_pdfs(root):
        relative = _relative_path(root, path)
        content = path.read_bytes()
        current_sha = compute_sha256(content)
        existing_sha = _existing_ingest_sha(session, source_id=source_id, source_item_key=relative)
        files.append(
            SeedFileplan(
                relative_path=relative,
                file_name=path.name,
                sha256=current_sha,
                byte_size=len(content),
                already_ingested=existing_sha is not None,
                hash_conflict=existing_sha is not None and existing_sha != current_sha,
                existing_sha256=existing_sha,
            )
        )
    expected_new = sum(1 for f in files if not f.already_ingested)
    hash_conflicts = [f.relative_path for f in files if f.hash_conflict]
    return {
        "root": str(root),
        "root_exists": root.exists(),
        "source_name": SEED_SOURCE_NAME,
        "source_exists": source is not None,
        "source_id": source_id,
        "batch_id": SEED_BATCH_ID,
        "file_count": len(files),
        "expected_new": expected_new,
        "hash_conflicts": hash_conflicts,
        "files": [asdict(f) for f in files],
    }


def apply_seed(session: Session, storage: ObjectStore, root: Path = DEFAULT_SEED_ROOT) -> dict:
    """Copy the seed PDFs into storage and register raw FileAsset/IngestEvent rows.

    Only creates DataSource / Blob / FileAsset / IngestEvent. Never Archive.
    """
    # Guard MinIO/DB writes: the quota schema must be present even when init_db is skipped.
    verify_quota_schema(session.get_bind())
    source, source_created = get_or_create_seed_source(session)
    newly_ingested: list[dict] = []
    skipped: list[dict] = []
    conflicts: list[dict] = []
    for path in _discover_pdfs(root):
        relative = _relative_path(root, path)
        content = path.read_bytes()
        current_sha = compute_sha256(content)
        existing_sha = _existing_ingest_sha(session, source_id=source.source_id, source_item_key=relative)
        if existing_sha is not None:
            if existing_sha != current_sha:
                # Content changed under the same stable key: refuse to silently overwrite.
                conflicts.append(
                    {"relative_path": relative, "existing_sha256": existing_sha, "new_sha256": current_sha}
                )
            else:
                skipped.append({"relative_path": relative, "reason": "already_ingested"})
            continue
        result = register_asset(
            session,
            storage,
            tenant_code=source.asset_tenant_code,
            source_type=SEED_SOURCE_TYPE,
            batch_id=SEED_BATCH_ID,
            file_name=path.name,
            content=content,
            source_id=source.source_id,
            source_item_key=relative,
            create_ingest_event=True,
            derive_tasks=False,  # L0 raw-only: no OCR / parse / processing tasks
            source_metadata={"nas_path": relative, "seed_batch": SEED_BATCH_ID, "original_name": path.name},
        )
        newly_ingested.append(
            {
                "relative_path": relative,
                "file_id": result.file_id,
                "sha256": result.sha256,
                "duplicated_blob": result.duplicated,
            }
        )
    return {
        "source_id": source.source_id,
        "source_created": source_created,
        "newly_ingested_count": len(newly_ingested),
        "skipped_count": len(skipped),
        "hash_conflict_count": len(conflicts),
        "newly_ingested": newly_ingested,
        "skipped": skipped,
        "hash_conflicts": conflicts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.quota_seed", description="四川2025 quota seed-batch ingestion (raw-only)")
    parser.add_argument("--root", default=str(DEFAULT_SEED_ROOT), help="directory containing the seed PDFs")
    parser.add_argument("--apply", action="store_true", help="actually copy files into storage (default: dry-run)")
    parser.add_argument(
        "--skip-init",
        action="store_true",
        help="skip init_db(); use when the quota schema is already deployed and an unrelated "
        "legacy migration would otherwise block startup",
    )
    args = parser.parse_args(argv)
    root = Path(args.root)

    if not args.skip_init:
        init_db()
    session_factory = get_session_factory()
    with session_factory() as session:
        plan = plan_seed(session, root)
        print("== SEED PLAN (dry-run) ==")
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        if not args.apply:
            print("\nDry-run only. Re-run with --apply to copy files into storage.")
            return 0
        if not plan["root_exists"] or plan["file_count"] == 0:
            print("\nNo seed files found; nothing to apply.", file=sys.stderr)
            return 2
        storage = get_object_store()
        summary = apply_seed(session, storage, root)
        print("\n== SEED APPLIED ==")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
