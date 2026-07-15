from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Archive, ArchiveFile, FileAsset
from app.storage import ObjectStore


AUDITABLE_ARCHIVE_STATUSES = {"collected", "archived", "ready_for_governance"}


def audit_file_asset_objects(
    session: Session,
    storage: ObjectStore,
    *,
    domain_type: str | None = None,
    region_code: str | None = None,
    limit: int | None = None,
    issue_limit: int = 100,
) -> dict[str, object]:
    archive_scoped = domain_type is not None or region_code is not None
    if archive_scoped:
        statement = (
            select(
                FileAsset,
                Archive.title.label("archive_title"),
                Archive.region_code.label("archive_region_code"),
            )
            .join(ArchiveFile, ArchiveFile.file_id == FileAsset.file_id)
            .join(Archive, Archive.archive_id == ArchiveFile.archive_id)
            .where(Archive.status.in_(AUDITABLE_ARCHIVE_STATUSES))
        )
        if domain_type is not None:
            statement = statement.where(Archive.domain_type == domain_type)
        if region_code is not None:
            statement = statement.where(Archive.region_code == region_code)
    else:
        statement = select(FileAsset)

    statement = statement.order_by(FileAsset.created_at.desc(), FileAsset.file_id.asc())
    if limit is not None:
        statement = statement.limit(limit)

    counters = {
        "checked_count": 0,
        "ok_count": 0,
        "missing_count": 0,
        "size_mismatch_count": 0,
        "error_count": 0,
        "orphan_reference_count": 0,
        "orphan_archive_count": 0,
        "expected_bytes": 0,
        "available_bytes": 0,
    }
    issues: list[dict[str, Any]] = []

    orphan_statement = (
        select(ArchiveFile, Archive)
        .join(Archive, Archive.archive_id == ArchiveFile.archive_id)
        .outerjoin(FileAsset, ArchiveFile.file_id == FileAsset.file_id)
        .where(
            Archive.is_withdrawn.is_(False),
            ArchiveFile.file_id.is_not(None),
            FileAsset.file_id.is_(None),
        )
        .order_by(Archive.created_at.desc(), ArchiveFile.archive_file_id.asc())
    )
    if archive_scoped:
        orphan_statement = orphan_statement.where(Archive.status.in_(AUDITABLE_ARCHIVE_STATUSES))
        if domain_type is not None:
            orphan_statement = orphan_statement.where(Archive.domain_type == domain_type)
        if region_code is not None:
            orphan_statement = orphan_statement.where(Archive.region_code == region_code)

    orphan_archive_ids: set[str] = set()
    for mounted, archive in session.execute(orphan_statement).all():
        counters["orphan_reference_count"] += 1
        orphan_archive_ids.add(archive.archive_id)
        _append_orphan_issue(issues, issue_limit, mounted=mounted, archive=archive)
    counters["orphan_archive_count"] = len(orphan_archive_ids)

    for row in session.execute(statement).all():
        row_mapping = row._mapping
        asset: FileAsset = row[0]
        archive_title = row_mapping.get("archive_title") if archive_scoped else None
        archive_region_code = row_mapping.get("archive_region_code") if archive_scoped else None
        expected_size = int(asset.file_size)

        counters["checked_count"] += 1
        counters["expected_bytes"] += expected_size

        try:
            stat = storage.stat_object(asset.bucket, asset.object_key)
        except KeyError:
            counters["missing_count"] += 1
            _append_issue(
                issues,
                issue_limit,
                status="missing",
                asset=asset,
                expected_size=expected_size,
                actual_size=None,
                archive_title=archive_title,
                region_code=archive_region_code,
            )
            continue
        except Exception as exc:
            counters["error_count"] += 1
            _append_issue(
                issues,
                issue_limit,
                status="error",
                asset=asset,
                expected_size=expected_size,
                actual_size=None,
                archive_title=archive_title,
                region_code=archive_region_code,
                error_message=str(exc),
            )
            continue

        actual_size = int(stat.byte_size)
        counters["available_bytes"] += actual_size
        if actual_size == expected_size:
            counters["ok_count"] += 1
            continue

        counters["size_mismatch_count"] += 1
        _append_issue(
            issues,
            issue_limit,
            status="size_mismatch",
            asset=asset,
            expected_size=expected_size,
            actual_size=actual_size,
            archive_title=archive_title,
            region_code=archive_region_code,
        )

    return {**counters, "issues": issues}


def _append_orphan_issue(
    issues: list[dict[str, Any]],
    issue_limit: int,
    *,
    mounted: ArchiveFile,
    archive: Archive,
) -> None:
    if len(issues) >= max(issue_limit, 0):
        return
    issues.append(
        {
            "status": "orphan_file_reference",
            "archive_id": archive.archive_id,
            "archive_title": archive.title,
            "region_code": archive.region_code,
            "archive_file_id": mounted.archive_file_id,
            "file_id": mounted.file_id,
            "file_name": mounted.display_name,
        }
    )


def _append_issue(
    issues: list[dict[str, Any]],
    issue_limit: int,
    *,
    status: str,
    asset: FileAsset,
    expected_size: int,
    actual_size: int | None,
    archive_title: str | None,
    region_code: str | None,
    error_message: str | None = None,
) -> None:
    if len(issues) >= max(issue_limit, 0):
        return
    issue: dict[str, Any] = {
        "status": status,
        "file_id": asset.file_id,
        "file_name": asset.file_name,
        "bucket": asset.bucket,
        "object_key": asset.object_key,
        "expected_size": expected_size,
        "actual_size": actual_size,
    }
    if archive_title is not None:
        issue["archive_title"] = archive_title
    if region_code is not None:
        issue["region_code"] = region_code
    if error_message is not None:
        issue["error_message"] = error_message
    issues.append(issue)
