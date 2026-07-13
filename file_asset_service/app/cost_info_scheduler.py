from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_session_factory, init_db
from app.models import CollectionTask, DataSource

DEFAULT_DOMAIN = "cost_info"
DEFAULT_TASK_TYPE = "crawl_incremental"


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def source_site_id(source: DataSource) -> str:
    config = source.config or {}
    stable = config.get("stable") or {}
    return stable.get("site_id") or source.source_id


def active_parser_version(source: DataSource) -> str | None:
    parser = (source.config or {}).get("parser") or {}
    version = parser.get("active_parser_version")
    return str(version) if version else None


def adapter_kind(source: DataSource) -> str | None:
    parser = (source.config or {}).get("parser") or {}
    version = parser.get("active_parser_version")
    parsers = parser.get("parsers") or {}
    if not version:
        return None
    adapter = (parsers.get(version) or {}).get("adapter_kind")
    return str(adapter) if adapter else None


def config_digest(config: dict) -> str:
    payload = json.dumps(config or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def latest_collection_task(
    session: Session,
    *,
    source_id: str,
    task_type: str = DEFAULT_TASK_TYPE,
    data_domain: str = DEFAULT_DOMAIN,
) -> CollectionTask | None:
    statement = (
        select(CollectionTask)
        .where(
            CollectionTask.source_id == source_id,
            CollectionTask.task_type == task_type,
            CollectionTask.data_domain == data_domain,
        )
        .order_by(CollectionTask.created_at.desc())
        .limit(20)
    )
    tasks = list(session.scalars(statement).all())
    if not tasks:
        return None
    return max(tasks, key=lambda task: ensure_aware_utc(task.scheduled_at or task.created_at))


def is_due(source: DataSource, session: Session, *, now: datetime | None = None, force: bool = False) -> bool:
    if force:
        return True

    policy = source.schedule_policy or {}
    frequency = policy.get("frequency", "daily")
    current = ensure_aware_utc(now or utcnow())
    last_task = latest_collection_task(
        session,
        source_id=source.source_id,
        task_type=DEFAULT_TASK_TYPE,
        data_domain=DEFAULT_DOMAIN,
    )
    if last_task is None:
        return True

    last_at = ensure_aware_utc(last_task.scheduled_at or last_task.created_at)
    if frequency == "daily":
        timezone_name = policy.get("timezone") or "Asia/Shanghai"
        tz = ZoneInfo(timezone_name)
        return last_at.astimezone(tz).date() < current.astimezone(tz).date()
    if frequency == "weekly":
        return (current - last_at).days >= 7
    return False


def list_sources(
    session: Session,
    *,
    data_domain: str = DEFAULT_DOMAIN,
    source_id: str | None = None,
    site_id: str | None = None,
) -> list[DataSource]:
    statement = select(DataSource).where(DataSource.data_domain == data_domain).order_by(DataSource.created_at.asc())
    if source_id:
        statement = statement.where(DataSource.source_id == source_id)
    sources = list(session.scalars(statement).all())
    if site_id:
        sources = [source for source in sources if source_site_id(source) == site_id]
    return sources


def has_pending_or_running_task(session: Session, source: DataSource, *, data_domain: str = DEFAULT_DOMAIN) -> bool:
    statement = (
        select(CollectionTask.task_id)
        .where(
            CollectionTask.source_id == source.source_id,
            CollectionTask.data_domain == data_domain,
            CollectionTask.task_type == DEFAULT_TASK_TYPE,
            CollectionTask.status.in_(["pending", "running"]),
        )
        .limit(1)
    )
    return session.execute(statement).first() is not None


def task_config_override(source: DataSource) -> dict:
    return {
        "site_id": source_site_id(source),
        "adapter_kind": adapter_kind(source),
        "active_parser_version": active_parser_version(source),
        "config_digest": config_digest(source.config or {}),
    }


def create_task_if_not_exists(
    session: Session,
    source: DataSource,
    *,
    now: datetime | None = None,
    trigger: str = "scheduled",
    data_domain: str = DEFAULT_DOMAIN,
) -> bool:
    if has_pending_or_running_task(session, source, data_domain=data_domain):
        return False

    scheduled_at = ensure_aware_utc(now or utcnow())
    batch_ts = scheduled_at.strftime("%Y%m%d%H%M%S")
    task = CollectionTask(
        source_id=source.source_id,
        asset_tenant_code=source.asset_tenant_code,
        operator_type="system",
        task_type=DEFAULT_TASK_TYPE,
        trigger_type=trigger,
        batch_id=f"cost_info:{source_site_id(source)}:{batch_ts}:{uuid4().hex[:4]}",
        data_domain=data_domain,
        status="pending",
        scheduled_at=scheduled_at,
        attempt=0,
        max_attempts=int((source.schedule_policy or {}).get("max_attempts", 3)),
        config_override=task_config_override(source),
        created_by="cost_info_scheduler",
    )
    session.add(task)
    session.commit()
    return True


def run_scheduler(
    session: Session,
    *,
    now: datetime | None = None,
    trigger: str = "scheduled",
    data_domain: str = DEFAULT_DOMAIN,
    source_id: str | None = None,
    site_id: str | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> dict:
    current = ensure_aware_utc(now or utcnow())
    sources = list_sources(session, data_domain=data_domain, source_id=source_id, site_id=site_id)
    active_sources = [source for source in sources if source.status == "active"]
    disabled_sources = [source for source in sources if source.status != "active"]

    report = {
        "run_type": "scheduler",
        "data_domain": data_domain,
        "trigger": trigger,
        "run_started_at": current.isoformat(),
        "source_seen": len(active_sources),
        "source_due": 0,
        "task_created": 0,
        "task_skipped_pending": 0,
        "task_skipped_disabled": len(disabled_sources),
        "dry_run": dry_run,
        "health_status": "healthy",
        "sources": [],
    }

    for source in active_sources:
        policy = source.schedule_policy or {}
        if policy.get("enabled") is not True:
            continue
        if not is_due(source, session, now=current, force=force):
            continue

        report["source_due"] += 1
        source_report = {
            "source_id": source.source_id,
            "site_id": source_site_id(source),
            "adapter_kind": adapter_kind(source),
            "action": "dry_run" if dry_run else "pending",
        }
        if dry_run:
            report["sources"].append(source_report)
            continue

        if create_task_if_not_exists(session, source, now=current, trigger=trigger, data_domain=data_domain):
            report["task_created"] += 1
            source_report["action"] = "created"
        else:
            report["task_skipped_pending"] += 1
            source_report["action"] = "skipped_pending"
        report["sources"].append(source_report)

    report["run_finished_at"] = ensure_aware_utc(now or utcnow()).isoformat()
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Schedule due cost_info crawler tasks.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--now", action="store_true", help="Accepted for CLI readability; each run is immediate.")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--force", action="store_true")
    run.add_argument("--source-id")
    run.add_argument("--site-id")
    run.add_argument("--domain", default=DEFAULT_DOMAIN)
    run.add_argument("--trigger", default="manual")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command != "run":
        raise ValueError(f"unsupported command: {args.command}")

    init_db()
    SessionFactory = get_session_factory()
    with SessionFactory() as session:
        report = run_scheduler(
            session,
            trigger=args.trigger,
            data_domain=args.domain,
            source_id=args.source_id,
            site_id=args.site_id,
            dry_run=args.dry_run,
            force=args.force,
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
