from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cost_info_scheduler import adapter_kind, is_due, source_site_id
from app.models import CollectionTask, DataSource

DATA_DOMAIN = "cost_info"
SOURCE_TYPE = "info_price"
CONNECTOR_TYPE = "source_registry"
WORKER_TASK_TYPES = {"crawl_incremental", "crawl_issue"}
MANIFEST_ISSUE_FIELDS = [
    "object_key",
    "sha256",
    "file_names",
    "original_names",
    "resolved_regions",
    "region_codes",
    "period_starts",
    "period_raws",
    "source_urls",
    "missing_fields",
]


def utcnow() -> datetime:
    return datetime.now(UTC)


def _task_sort_key(task: CollectionTask) -> datetime:
    return task.created_at or task.scheduled_at or datetime.min.replace(tzinfo=UTC)


def _task_counts(tasks: list[CollectionTask]) -> dict[str, int]:
    return {
        "pending": sum(1 for task in tasks if task.status == "pending"),
        "running": sum(1 for task in tasks if task.status == "running"),
        "done": sum(1 for task in tasks if task.status == "done"),
        "failed": sum(1 for task in tasks if task.status == "failed"),
    }


def _source_tasks(session: Session, source_id: str) -> list[CollectionTask]:
    statement = (
        select(CollectionTask)
        .where(CollectionTask.source_id == source_id, CollectionTask.data_domain == DATA_DOMAIN)
        .order_by(CollectionTask.created_at.desc())
    )
    return list(session.scalars(statement).all())


def list_crawler_sources(session: Session, *, now: datetime | None = None) -> list[dict[str, object]]:
    current = now or utcnow()
    statement = (
        select(DataSource)
        .where(
            DataSource.data_domain == DATA_DOMAIN,
            DataSource.source_type == SOURCE_TYPE,
            DataSource.connector_type == CONNECTOR_TYPE,
        )
        .order_by(DataSource.province.asc(), DataSource.city.asc(), DataSource.created_at.asc())
    )
    rows: list[dict[str, object]] = []
    for source in session.scalars(statement).all():
        tasks = _source_tasks(session, source.source_id)
        counts = _task_counts(tasks)
        latest = max(tasks, key=_task_sort_key) if tasks else None
        policy = source.schedule_policy or {}
        current_adapter = adapter_kind(source)
        worker_pending = sum(
            1
            for task in tasks
            if task.status == "pending" and task.task_type in WORKER_TASK_TYPES
        )
        legacy_pending = sum(
            1
            for task in tasks
            if task.status == "pending" and task.task_type not in WORKER_TASK_TYPES
        )
        rows.append(
            {
                "source_id": source.source_id,
                "site_id": source_site_id(source),
                "name": source.name,
                "status": source.status,
                "province": source.province,
                "city": source.city,
                "region_code": source.region_code,
                "adapter_kind": current_adapter,
                "schedule_enabled": policy.get("enabled") is True,
                "frequency": policy.get("frequency"),
                "is_due": (
                    source.status == "active"
                    and policy.get("enabled") is True
                    and is_due(source, session, now=current)
                ),
                "pending_count": worker_pending,
                "worker_pending_count": worker_pending,
                "legacy_pending_count": legacy_pending,
                "running_count": counts["running"],
                "done_count": counts["done"],
                "failed_count": counts["failed"],
                "last_task_status": latest.status if latest else None,
                "last_task_type": latest.task_type if latest else None,
                "last_task_finished_at": latest.finished_at.isoformat() if latest and latest.finished_at else None,
                "last_error_code": latest.error_code if latest else None,
                "can_run_worker": source.status == "active" and current_adapter is not None and worker_pending > 0,
            }
        )
    return rows


def list_crawler_tasks(
    session: Session,
    *,
    limit: int = 80,
    source_id: str | None = None,
    status: str | None = None,
) -> list[dict[str, object]]:
    statement = (
        select(CollectionTask, DataSource)
        .join(DataSource, CollectionTask.source_id == DataSource.source_id)
        .where(CollectionTask.data_domain == DATA_DOMAIN)
        .order_by(CollectionTask.created_at.desc())
        .limit(max(1, min(limit, 200)))
    )
    if source_id:
        statement = statement.where(CollectionTask.source_id == source_id)
    if status:
        statement = statement.where(CollectionTask.status == status)

    rows = []
    for task, source in session.execute(statement).all():
        override = task.config_override or {}
        rows.append(
            {
                "task_id": task.task_id,
                "source_id": task.source_id,
                "site_id": override.get("site_id") or source_site_id(source),
                "source_name": source.name,
                "adapter_kind": override.get("adapter_kind") or adapter_kind(source),
                "task_type": task.task_type,
                "trigger_type": task.trigger_type,
                "status": task.status,
                "scheduled_at": task.scheduled_at.isoformat() if task.scheduled_at else None,
                "started_at": task.started_at.isoformat() if task.started_at else None,
                "finished_at": task.finished_at.isoformat() if task.finished_at else None,
                "worker_id": task.worker_id,
                "lease_expires_at": task.lease_expires_at.isoformat() if task.lease_expires_at else None,
                "heartbeat_at": task.heartbeat_at.isoformat() if task.heartbeat_at else None,
                "attempt": task.attempt,
                "max_attempts": task.max_attempts,
                "discovered_count": task.discovered_count,
                "new_file_count": task.new_file_count,
                "duplicate_file_count": task.duplicate_file_count,
                "failed_count": task.failed_count,
                "error_code": task.error_code,
            }
        )
    return rows


def summarize_parse_manifest(manifest_path: str | Path | None) -> dict[str, object]:
    path = _resolve_manifest_path(manifest_path)
    summary = _manifest_summary_shell(path)
    if path is None or not path.exists():
        return summary

    row_count = 0
    ready_count = 0
    not_ready_count = 0
    schema_version = None
    missing_field_counts: dict[str, int] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            row_count += 1
            if schema_version is None:
                schema_version = _clean_cell(row.get("manifest_schema_version")) or None
            if _is_parse_ready(row):
                ready_count += 1
            else:
                not_ready_count += 1
                for field in _missing_fields(row):
                    missing_field_counts[field] = missing_field_counts.get(field, 0) + 1

    summary.update(
        {
            "schema_version": schema_version,
            "row_count": row_count,
            "ready_count": ready_count,
            "not_ready_count": not_ready_count,
            "ready_rate": ready_count / row_count if row_count else None,
            "missing_field_counts": missing_field_counts,
        }
    )
    return summary


def list_parse_manifest_issues(manifest_path: str | Path | None, *, limit: int = 50) -> dict[str, object]:
    path = _resolve_manifest_path(manifest_path)
    capped_limit = max(0, min(limit, 500))
    response = {
        "configured": path is not None,
        "exists": path.exists() if path is not None else False,
        "path": str(path) if path is not None else None,
        "limit": capped_limit,
        "issue_count": 0,
        "issues": [],
    }
    if path is None or not path.exists():
        return response

    issues: list[dict[str, str]] = []
    issue_count = 0
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if _is_parse_ready(row):
                continue
            issue_count += 1
            if len(issues) < capped_limit:
                issues.append({field: _clean_cell(row.get(field)) for field in MANIFEST_ISSUE_FIELDS})
    response["issue_count"] = issue_count
    response["issues"] = issues
    return response


def _resolve_manifest_path(manifest_path: str | Path | None) -> Path | None:
    if manifest_path is None:
        return None
    text = str(manifest_path).strip()
    return Path(text) if text else None


def _manifest_summary_shell(path: Path | None) -> dict[str, object]:
    exists = path.exists() if path is not None else False
    stat = path.stat() if exists and path is not None else None
    return {
        "configured": path is not None,
        "exists": exists,
        "path": str(path) if path is not None else None,
        "file_name": path.name if path is not None else None,
        "updated_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat() if stat is not None else None,
        "byte_size": stat.st_size if stat is not None else None,
        "schema_version": None,
        "row_count": 0,
        "ready_count": 0,
        "not_ready_count": 0,
        "ready_rate": None,
        "missing_field_counts": {},
    }


def _is_parse_ready(row: dict[str, str | None]) -> bool:
    return _clean_cell(row.get("parse_ready")).lower() == "true"


def _missing_fields(row: dict[str, str | None]) -> list[str]:
    return [field.strip() for field in _clean_cell(row.get("missing_fields")).split("|") if field.strip()]


def _clean_cell(value: object | None) -> str:
    return "" if value is None else str(value).strip()
