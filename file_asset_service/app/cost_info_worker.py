from __future__ import annotations

import argparse
import json
import os
import socket
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.adapters import get_adapter
from app.adapters.base_cost_info_adapter import AdapterResult, DiscoveredIssue
from app.crawler_platform.cost_info_http_client import make_http_client
from app.crawler_platform.red_line_checker import check_file_processing_red_lines
from app.crawler_platform.run_report import WorkerReport, finalize_worker_report
from app.database import get_session_factory, init_db
from app.info_price_coverage import ACTIVE_ARCHIVE_STATUSES, _archive_period
from app.models import Archive, CollectionTask, DataSource
from app.storage import ObjectStore, get_object_store

TASK_TYPES = ("crawl_incremental", "crawl_issue")
DATA_DOMAIN = "cost_info"
EARLY_STOP_DUPLICATE_THRESHOLD = 3


def utcnow() -> datetime:
    return datetime.now(UTC)


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def source_site_id(source: DataSource) -> str:
    return ((source.config or {}).get("stable") or {}).get("site_id") or source.source_id


def source_adapter_kind(source: DataSource) -> str:
    parser = (source.config or {}).get("parser") or {}
    active_version = parser.get("active_parser_version")
    parsers = parser.get("parsers") or {}
    adapter_kind = (parsers.get(active_version) or {}).get("adapter_kind")
    if not adapter_kind:
        raise ValueError(f"SOURCE_ADAPTER_KIND_REQUIRED: {source.source_id}")
    return str(adapter_kind)


def _build_business_key(source: DataSource, issue: DiscoveredIssue, adapter=None) -> str:
    if adapter is not None and hasattr(adapter, "business_key"):
        return adapter.business_key(source, issue)
    return f"cost_info:{source_site_id(source)}:notice:{issue.source_item_key}"


def _archive_exists(business_key: str, db: Session, source: DataSource) -> bool:
    return (
        db.scalar(
            select(Archive.archive_id).where(
                Archive.tenant_code == source.asset_tenant_code,
                Archive.domain_type == DATA_DOMAIN,
                Archive.business_key == business_key,
                Archive.version == 1,
            )
        )
        is not None
    )


def _due(task: CollectionTask, now: datetime) -> bool:
    if task.scheduled_at is None:
        return True
    return ensure_aware_utc(task.scheduled_at) <= now


def _apply_worker_row_lock(statement, *, dialect_name: str):
    if dialect_name != "postgresql":
        return statement
    return statement.with_for_update(skip_locked=True, of=CollectionTask)


def _select_pending_tasks(
    db: Session,
    *,
    limit: int,
    now: datetime,
    source_id: str | None = None,
    site_id: str | None = None,
    adapter_kind: str | None = None,
    batch_id: str | None = None,
    include_expired_running: bool = False,
    lock_rows: bool = False,
) -> list[tuple[CollectionTask, DataSource]]:
    status_filter = CollectionTask.status == "pending"
    if include_expired_running:
        status_filter = or_(
            CollectionTask.status == "pending",
            and_(
                CollectionTask.status == "running",
                CollectionTask.lease_expires_at.is_not(None),
                CollectionTask.lease_expires_at <= now,
            ),
        )
    statement = (
        select(CollectionTask, DataSource)
        .join(DataSource, CollectionTask.source_id == DataSource.source_id)
        .where(
            status_filter,
            CollectionTask.task_type.in_(TASK_TYPES),
            CollectionTask.data_domain == DATA_DOMAIN,
            DataSource.status == "active",
        )
        .order_by(CollectionTask.scheduled_at.asc(), CollectionTask.created_at.asc())
    )
    if source_id:
        statement = statement.where(CollectionTask.source_id == source_id)
    if batch_id:
        statement = statement.where(CollectionTask.batch_id == batch_id)
    if lock_rows:
        statement = _apply_worker_row_lock(statement, dialect_name=db.get_bind().dialect.name)
    rows = list(db.execute(statement).all())

    selected: list[tuple[CollectionTask, DataSource]] = []
    for task, source in rows:
        if len(selected) >= limit:
            break
        if site_id and source_site_id(source) != site_id:
            continue
        if adapter_kind and source_adapter_kind(source) != adapter_kind:
            continue
        if not _due(task, now):
            continue
        selected.append((task, source))
    return selected


def _lease_pending_tasks(
    db: Session,
    *,
    limit: int,
    now: datetime,
    worker_id: str,
    lease_seconds: int,
    source_id: str | None = None,
    site_id: str | None = None,
    adapter_kind: str | None = None,
    batch_id: str | None = None,
) -> list[tuple[CollectionTask, DataSource]]:
    lease_until = now + timedelta(seconds=max(60, lease_seconds))
    selected = _select_pending_tasks(
        db,
        limit=limit,
        now=now,
        source_id=source_id,
        site_id=site_id,
        adapter_kind=adapter_kind,
        batch_id=batch_id,
        include_expired_running=True,
        lock_rows=True,
    )
    for task, _source in selected:
        task.status = "running"
        task.started_at = now
        task.finished_at = None
        task.worker_id = worker_id
        task.lease_expires_at = lease_until
        task.heartbeat_at = now
        task.updated_at = now
    db.commit()
    return selected


def _lease_task(task: CollectionTask, db: Session, now: datetime) -> None:
    task.status = "running"
    task.started_at = now
    db.commit()


def _run_one_task(
    task: CollectionTask,
    source: DataSource,
    db: Session,
    *,
    dry_run: bool,
    storage: ObjectStore,
) -> AdapterResult:
    adapter_kind = source_adapter_kind(source)
    adapter = get_adapter(adapter_kind)
    client = make_http_client(source)
    issues = adapter.discover(source, task, client)

    result = AdapterResult(discovered_count=len(issues))
    consecutive_duplicates = 0
    policy = source.schedule_policy or {}

    for issue in issues:
        business_key = _build_business_key(source, issue, adapter)
        if _archive_exists(business_key, db, source):
            result.duplicate_count += 1
            consecutive_duplicates += 1
            if (
                policy.get("early_stop_duplicate", True)
                and consecutive_duplicates >= EARLY_STOP_DUPLICATE_THRESHOLD
            ):
                result.early_stopped_on_duplicate = True
                break
            continue

        consecutive_duplicates = 0
        if dry_run:
            result.skipped_count += 1
            continue

        try:
            adapter.ingest(source, task, issue, storage)
        except Exception as exc:
            result.failed_count += 1
            result.failed_items.append(
                {
                    "source_item_key": issue.source_item_key,
                    "error": str(exc)[:400],
                }
            )
            continue
        result.ingested_count += 1

    return result


def _complete_task(task: CollectionTask, result: AdapterResult, db: Session, now: datetime) -> None:
    task.status = "done"
    task.finished_at = now
    task.discovered_count = result.discovered_count
    task.downloaded_count = result.ingested_count
    task.new_file_count = result.ingested_count
    task.duplicate_file_count = result.duplicate_count
    task.failed_count = result.failed_count
    task.processing_created_count = 0
    task.error_code = None
    task.error_message = None
    task.lease_expires_at = None
    task.heartbeat_at = now
    task.cursor_after = {
        "cursor_source_item_key": result.cursor_source_item_key,
        "cursor_publish_date": result.cursor_publish_date,
        "early_stopped_on_duplicate": result.early_stopped_on_duplicate,
    }
    db.commit()


def _task_period(task: CollectionTask) -> str | None:
    if task.period_start:
        return task.period_start
    override = task.config_override if isinstance(task.config_override, dict) else {}
    coverage_backfill = override.get("coverage_backfill") if isinstance(override, dict) else {}
    if isinstance(coverage_backfill, dict):
        period = coverage_backfill.get("period")
        if period:
            return str(period)
    return None


def _task_period_already_covered(task: CollectionTask, source: DataSource, db: Session) -> str | None:
    period = _task_period(task)
    if not period:
        return None
    statement = select(Archive).where(
        Archive.domain_type == DATA_DOMAIN,
        Archive.source_id == source.source_id,
        Archive.tenant_code == source.asset_tenant_code,
        Archive.status.in_(ACTIVE_ARCHIVE_STATUSES),
        Archive.is_current.is_(True),
        Archive.is_withdrawn.is_(False),
    )
    for archive in db.scalars(statement).all():
        if _archive_period(archive) == period:
            return period
    return None


def _complete_already_covered_task(task: CollectionTask, db: Session, now: datetime, *, period: str) -> None:
    task.status = "done"
    task.finished_at = now
    task.discovered_count = 0
    task.downloaded_count = 0
    task.new_file_count = 0
    task.duplicate_file_count = 1
    task.failed_count = 0
    task.processing_created_count = 0
    task.error_code = None
    task.error_message = None
    task.lease_expires_at = None
    task.heartbeat_at = now
    task.cursor_after = {
        "skipped_already_covered": True,
        "period": period,
    }
    db.commit()


def _classify_error(exc: Exception) -> str:
    message = str(exc).lower()
    if "timeout" in message:
        return "DOWNLOAD_TIMEOUT"
    if isinstance(exc, ConnectionError):
        return "HOST_UNREACHABLE"
    if isinstance(exc, ValueError):
        return "PARSE_ERROR"
    return "COST_INFO_CRAWL_TASK_FAILED"


def _recent_consecutive_failed_task_count(db: Session, source_id: str) -> int:
    statement = (
        select(CollectionTask)
        .where(
            CollectionTask.source_id == source_id,
            CollectionTask.data_domain == DATA_DOMAIN,
            CollectionTask.task_type.in_(TASK_TYPES),
        )
        .order_by(CollectionTask.created_at.desc())
        .limit(20)
    )
    count = 0
    for task in db.scalars(statement).all():
        if task.status != "failed":
            break
        count += 1
    return count


def _handle_task_failure(
    task: CollectionTask,
    source: DataSource,
    exc: Exception,
    db: Session,
    now: datetime,
) -> tuple[str, bool]:
    task.attempt = (task.attempt or 0) + 1
    task.error_code = _classify_error(exc)
    override = dict(task.config_override or {})
    override["last_error"] = {
        "exception_type": type(exc).__name__,
        "message": str(exc)[:500],
        "ts": now.isoformat(),
    }
    task.config_override = override

    max_attempts = task.max_attempts or 3
    if task.attempt < max_attempts:
        delay_seconds = min(60 * (2**task.attempt), 14400)
        task.status = "pending"
        task.scheduled_at = now + timedelta(seconds=delay_seconds)
        task.finished_at = now
        task.lease_expires_at = None
        task.heartbeat_at = now
        db.commit()
        return "retry", False

    task.status = "failed"
    task.error_code = "MAX_ATTEMPTS_EXCEEDED"
    task.finished_at = now
    task.lease_expires_at = None
    task.heartbeat_at = now
    override = dict(task.config_override or {})
    override["manual_recovery"] = {
        "required": True,
        "spec": "017",
        "reason": task.error_code,
        "failed_at": now.isoformat(),
    }
    task.config_override = override
    db.commit()

    manual_recovery = False
    if _recent_consecutive_failed_task_count(db, source.source_id) >= 5:
        source.status = "manual_recovery"
        db.commit()
        manual_recovery = True
    return "dead_letter", manual_recovery


def run_worker(
    db: Session,
    limit: int = 10,
    source_id: str | None = None,
    site_id: str | None = None,
    adapter_kind: str | None = None,
    batch_id: str | None = None,
    dry_run: bool = False,
    *,
    trigger: str = "manual",
    worker_id: str | None = None,
    lease_seconds: int = 14400,
    now: datetime | None = None,
    storage: ObjectStore | None = None,
) -> WorkerReport:
    started_at = ensure_aware_utc(now or utcnow())
    worker_identity = worker_id or default_worker_id()
    report = WorkerReport(
        run_id=f"run_{started_at.strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:8]}",
        trigger=trigger,
        run_started_at=started_at.isoformat(),
        worker_id=worker_identity,
    )
    if dry_run:
        task_rows = _select_pending_tasks(
            db,
            limit=limit,
            now=started_at,
            source_id=source_id,
            site_id=site_id,
            adapter_kind=adapter_kind,
            batch_id=batch_id,
        )
    else:
        task_rows = _lease_pending_tasks(
            db,
            limit=limit,
            now=started_at,
            source_id=source_id,
            site_id=site_id,
            adapter_kind=adapter_kind,
            batch_id=batch_id,
            worker_id=worker_identity,
            lease_seconds=lease_seconds,
        )
    report.leased_count = len(task_rows)
    leased_task_ids = [task.task_id for task, _source in task_rows]
    object_store = storage or get_object_store()

    for task, source in task_rows:
        adapter = source_adapter_kind(source)
        covered_period = _task_period_already_covered(task, source, db)
        if covered_period:
            if not dry_run:
                _complete_already_covered_task(task, db, started_at, period=covered_period)
            report.done_count += 1
            report.duplicate_count += 1
            report.per_source.append(
                {
                    "source_id": source.source_id,
                    "site_id": source_site_id(source),
                    "adapter_kind": adapter,
                    "status": "dry_run_skipped_covered" if dry_run else "skipped_covered",
                    "period": covered_period,
                    "archive_created": 0,
                    "duplicates": 1,
                    "failed": 0,
                    "early_stopped": False,
                }
            )
            continue
        try:
            result = _run_one_task(task, source, db, dry_run=dry_run, storage=object_store)
        except Exception as exc:
            status, manual_recovery = _handle_task_failure(task, source, exc, db, started_at)
            if status == "retry":
                report.retry_count += 1
            else:
                report.dead_letter_count += 1
            if manual_recovery:
                report.manual_recovery_count += 1
            report.per_source.append(
                {
                    "source_id": source.source_id,
                    "site_id": source_site_id(source),
                    "adapter_kind": adapter,
                    "status": status,
                    "error_code": task.error_code,
                    "attempt": task.attempt,
                    "max_attempts": task.max_attempts,
                }
            )
            continue

        if not dry_run:
            _complete_task(task, result, db, started_at)
        report.done_count += 1
        report.archive_created_count += result.ingested_count
        report.duplicate_count += result.duplicate_count
        report.failed_count += result.failed_count
        report.per_source.append(
            {
                "source_id": source.source_id,
                "site_id": source_site_id(source),
                "adapter_kind": adapter,
                "status": "dry_run" if dry_run else "done",
                "archive_created": result.ingested_count,
                "duplicates": result.duplicate_count,
                "failed": result.failed_count,
                "early_stopped": result.early_stopped_on_duplicate,
            }
        )

    finished_at = ensure_aware_utc(now or utcnow())
    report.run_finished_at = finished_at.isoformat()
    report.duration_seconds = int((finished_at - started_at).total_seconds())
    red_line_check = check_file_processing_red_lines(leased_task_ids, db)
    report.file_processing_rows_created = red_line_check.rows_created
    report.file_processing_passes = red_line_check.passes
    report.red_line_violations = red_line_check.violations
    finalize_worker_report(report, finished_at=finished_at)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run pending cost_info crawler tasks.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--limit", type=int, default=10)
    run.add_argument("--source-id")
    run.add_argument("--site-id")
    run.add_argument("--adapter-kind")
    run.add_argument("--batch-id")
    run.add_argument("--worker-id")
    run.add_argument("--lease-seconds", type=int, default=14400)
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--trigger", default="manual")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "run":
        raise ValueError(f"unsupported command: {args.command}")
    init_db()
    SessionFactory = get_session_factory()
    with SessionFactory() as session:
        report = run_worker(
            session,
            limit=args.limit,
            source_id=args.source_id,
            site_id=args.site_id,
            adapter_kind=args.adapter_kind,
            batch_id=args.batch_id,
            dry_run=args.dry_run,
            trigger=args.trigger,
            worker_id=args.worker_id,
            lease_seconds=args.lease_seconds,
        )
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
