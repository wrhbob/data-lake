"""Database-backed task + loop runtime for trading announcement archives.

The trading crawlers already know how to parse their official sites.  This
module supplies the production runtime around them: source-aware scheduling,
leased task execution, cursor/state updates, and a small verification mode for
newly registered sources.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from datetime import UTC, date, datetime, time as clock_time, timedelta
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.changsha_trading import UrllibChangshaTradingClient, run_changsha_trading_channels
from app.chengdu_trading import UrllibChengduTradingClient, run_chengdu_trading_channels
from app.chongqing_trading import UrllibChongqingTradingClient, run_chongqing_trading_channels
from app.sichuan_trading import UrllibSichuanTradingClient, run_sichuan_trading_channels
from app.database import get_session_factory, init_db
from app.models import CollectionTask, DataSource, SourceCrawlState
from app.storage import ObjectStore, get_object_store
from app.trading_config_factories import restore_all_source_configs
from app.trading_source_registry import (
    DATA_DOMAIN,
    list_trading_sources,
    set_trading_source_active,
)

TASK_TYPE = "trading_incremental"
HISTORY_TASK_TYPE = "trading_history_backfill"
TASK_TYPES = (TASK_TYPE, HISTORY_TASK_TYPE)
SERVICE_DIR = Path(__file__).resolve().parent.parent
ROOT = SERVICE_DIR.parent


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def source_site_id(source: DataSource) -> str:
    stable = (source.config or {}).get("stable") or {}
    return str(stable.get("site_id") or source.source_id)


def _parse_scan_times(policy: dict) -> tuple[clock_time, ...]:
    raw = policy.get("scan_times") or ["09:10", "15:10"]
    if isinstance(raw, str):
        raw = [raw]
    values: set[clock_time] = set()
    for value in raw if isinstance(raw, (list, tuple)) else []:
        try:
            hour, minute = str(value).strip().split(":", maxsplit=1)
            values.add(clock_time(hour=int(hour), minute=int(minute)))
        except (TypeError, ValueError):
            continue
    return tuple(sorted(values)) or (clock_time(hour=9, minute=10), clock_time(hour=15, minute=10))


def _parse_scan_days(policy: dict) -> tuple[int, ...]:
    raw = policy.get("scan_days") or [1, 3, 5, 7, 10, 15]
    if isinstance(raw, int):
        raw = [raw]
    values = {int(value) for value in raw if str(value).strip().isdigit() and 1 <= int(value) <= 31}
    return tuple(sorted(values)) or (1, 3, 5, 7, 10, 15)


def next_scan_at(source: DataSource, *, after: datetime) -> datetime:
    """Return the next source-owned calendar slot, in UTC."""

    policy = source.schedule_policy or {}
    frequency = str(policy.get("frequency") or "daily").lower()
    timezone = ZoneInfo(str(policy.get("timezone") or "Asia/Shanghai"))
    local_after = ensure_aware_utc(after).astimezone(timezone)

    if frequency == "monthly":
        year, month = local_after.year, local_after.month
        for _ in range(14):
            candidates: list[datetime] = []
            for day in _parse_scan_days(policy):
                for scan_time in _parse_scan_times(policy):
                    try:
                        candidate = datetime(year, month, day, scan_time.hour, scan_time.minute, tzinfo=timezone)
                    except ValueError:
                        continue
                    if candidate > local_after:
                        candidates.append(candidate)
            if candidates:
                return min(candidates).astimezone(UTC)
            month += 1
            if month > 12:
                year += 1
                month = 1
        raise ValueError(f"TRADING_SCAN_WINDOW_UNRESOLVABLE: {source.source_id}")

    if frequency == "weekly":
        return (local_after + timedelta(days=7)).astimezone(UTC)

    # Event-driven announcements are scanned at explicit daily slots.
    for day_offset in range(8):
        candidate_day = local_after.date() + timedelta(days=day_offset)
        for scan_time in _parse_scan_times(policy):
            candidate = datetime.combine(candidate_day, scan_time, tzinfo=timezone)
            if candidate > local_after:
                return candidate.astimezone(UTC)
    raise ValueError(f"TRADING_DAILY_SCAN_WINDOW_UNRESOLVABLE: {source.source_id}")


def _is_due(source: DataSource, session: Session, *, now: datetime, force: bool) -> bool:
    if force:
        return True
    state = session.get(SourceCrawlState, source.source_id)
    if state is None or state.next_scan_at is None:
        return True
    return ensure_aware_utc(state.next_scan_at) <= now


def _has_pending_or_running_task(session: Session, source_id: str) -> bool:
    statement = (
        select(CollectionTask.task_id)
        .where(
            CollectionTask.source_id == source_id,
            CollectionTask.data_domain == DATA_DOMAIN,
            CollectionTask.task_type.in_(TASK_TYPES),
            CollectionTask.status.in_(["pending", "running"]),
        )
        .limit(1)
    )
    return session.execute(statement).first() is not None


def _task_plan(
    source: DataSource,
    *,
    verify: bool,
    channel_ids: list[str] | None,
    max_pages: int | None,
    page_size: int | None,
    max_items_per_channel: int | None,
    start_date: str | None,
    end_date: str | None,
) -> dict[str, Any]:
    if bool(start_date) != bool(end_date):
        raise ValueError("TRADING_HISTORY_DATE_RANGE_REQUIRED")
    if start_date and end_date:
        try:
            start = date.fromisoformat(start_date)
            end = date.fromisoformat(end_date)
        except ValueError as exc:
            raise ValueError("TRADING_HISTORY_DATE_FORMAT_INVALID") from exc
        if start > end:
            raise ValueError("TRADING_HISTORY_DATE_RANGE_INVALID")
    history_backfill = bool(start_date)
    policy = source.schedule_policy or {}
    verification = policy.get("verification") if isinstance(policy.get("verification"), dict) else {}
    return {
        "site_id": source_site_id(source),
        "verification_run": verify,
        "history_backfill": history_backfill,
        "start_date": start_date,
        "end_date": end_date,
        "stop_on_existing": not history_backfill,
        "channel_ids": channel_ids if channel_ids is not None else (
            list(verification.get("channel_ids") or []) if verify else None
        ),
        "max_pages": int(
            max_pages
            if max_pages is not None
            else (20 if history_backfill else (verification.get("max_pages", 1) if verify else policy.get("max_pages_per_run", 2)))
        ),
        "page_size": int(
            page_size
            if page_size is not None
            else (50 if history_backfill else (verification.get("page_size", 1) if verify else policy.get("page_size", 10)))
        ),
        "max_items_per_channel": (
            max_items_per_channel
            if history_backfill
            else int(
                max_items_per_channel
                if max_items_per_channel is not None
                else (verification.get("max_items_per_channel", 1) if verify else policy.get("max_items_per_channel", 20))
            )
        ),
    }


def _task_trigger_type(trigger: str) -> str:
    """Fit the human-facing trigger label into ``collection_task.trigger_type``."""

    normalized = str(trigger or "scheduled").strip()
    return normalized[:32] or "scheduled"


def run_trading_scheduler(
    session: Session,
    *,
    now: datetime | None = None,
    trigger: str = "scheduled",
    source_id: str | None = None,
    site_id: str | None = None,
    dry_run: bool = False,
    force: bool = False,
    verify: bool = False,
    channel_ids: list[str] | None = None,
    max_pages: int | None = None,
    page_size: int | None = None,
    max_items_per_channel: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    current = ensure_aware_utc(now or utcnow())
    sources = list_trading_sources(session)
    if source_id:
        sources = [source for source in sources if source.source_id == source_id]
    if site_id:
        sources = [source for source in sources if source_site_id(source) == site_id]

    report: dict[str, Any] = {
        "run_type": "trading_scheduler",
        "data_domain": DATA_DOMAIN,
        "trigger": trigger,
        "run_started_at": current.isoformat(),
        "verification_run": verify,
        "source_seen": len(sources),
        "source_due": 0,
        "task_created": 0,
        "task_skipped_pending": 0,
        "task_skipped_disabled": 0,
        "task_skipped_unverified": 0,
        "dry_run": dry_run,
        "sources": [],
    }
    for source in sources:
        policy = source.schedule_policy or {}
        allowed_statuses = {"active"}
        if verify:
            allowed_statuses.add("pending_verify")
        if source.status not in allowed_statuses:
            if source.status == "pending_verify":
                report["task_skipped_unverified"] += 1
            else:
                report["task_skipped_disabled"] += 1
            continue
        if policy.get("enabled") is not True:
            report["task_skipped_disabled"] += 1
            continue
        if not _is_due(source, session, now=current, force=force):
            continue

        report["source_due"] += 1
        plan = _task_plan(
            source,
            verify=verify,
            channel_ids=channel_ids,
            max_pages=max_pages,
            page_size=page_size,
            max_items_per_channel=max_items_per_channel,
            start_date=start_date,
            end_date=end_date,
        )
        source_report = {
            "source_id": source.source_id,
            "site_id": source_site_id(source),
            "status": source.status,
            "plan": plan,
            "action": "dry_run" if dry_run else "pending",
            "next_scan_at": None,
        }
        if dry_run:
            report["sources"].append(source_report)
            continue
        if _has_pending_or_running_task(session, source.source_id):
            report["task_skipped_pending"] += 1
            source_report["action"] = "skipped_pending"
            report["sources"].append(source_report)
            continue

        batch_ts = current.strftime("%Y%m%d%H%M%S")
        task = CollectionTask(
            source_id=source.source_id,
            asset_tenant_code=source.asset_tenant_code,
            operator_type="system",
            task_type=HISTORY_TASK_TYPE if plan["history_backfill"] else TASK_TYPE,
            trigger_type=_task_trigger_type(trigger),
            batch_id=f"trading:{source_site_id(source)}:{batch_ts}:{uuid4().hex[:4]}",
            data_domain=DATA_DOMAIN,
            status="pending",
            scheduled_at=current,
            max_attempts=max(1, int(policy.get("max_attempts") or 3)),
            period_raw=f"{plan['start_date']}..{plan['end_date']}" if plan["history_backfill"] else None,
            period_start=plan["start_date"][:7] if plan["history_backfill"] else None,
            period_end=plan["end_date"][:7] if plan["history_backfill"] else None,
            config_override={"trading_crawl": plan},
            created_by="trading_scheduler",
        )
        session.add(task)
        state = session.get(SourceCrawlState, source.source_id)
        if state is None:
            state = SourceCrawlState(source_id=source.source_id, data_domain=DATA_DOMAIN)
            session.add(state)
        state.last_scheduled_at = current
        state.next_scan_at = next_scan_at(source, after=current)
        state.updated_at = current
        session.commit()
        report["task_created"] += 1
        source_report["action"] = "created"
        source_report["next_scan_at"] = state.next_scan_at.isoformat()
        report["sources"].append(source_report)

    report["run_finished_at"] = ensure_aware_utc(now or utcnow()).isoformat()
    return report


def _due(task: CollectionTask, now: datetime) -> bool:
    return task.scheduled_at is None or ensure_aware_utc(task.scheduled_at) <= now


def _verification_task(task: CollectionTask) -> bool:
    override = task.config_override if isinstance(task.config_override, dict) else {}
    plan = override.get("trading_crawl") if isinstance(override.get("trading_crawl"), dict) else {}
    return plan.get("verification_run") is True


def _select_pending_tasks(
    session: Session,
    *,
    limit: int,
    now: datetime,
    source_id: str | None = None,
    site_id: str | None = None,
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
            CollectionTask.data_domain == DATA_DOMAIN,
            CollectionTask.task_type.in_(TASK_TYPES),
        )
        .order_by(CollectionTask.priority.asc(), CollectionTask.scheduled_at.asc(), CollectionTask.created_at.asc())
    )
    if source_id:
        statement = statement.where(CollectionTask.source_id == source_id)
    if lock_rows and session.get_bind().dialect.name == "postgresql":
        statement = statement.with_for_update(skip_locked=True, of=CollectionTask)

    selected: list[tuple[CollectionTask, DataSource]] = []
    for task, source in session.execute(statement).all():
        if len(selected) >= limit or not _due(task, now):
            continue
        if site_id and source_site_id(source) != site_id:
            continue
        if source.status != "active" and not (source.status == "pending_verify" and _verification_task(task)):
            continue
        selected.append((task, source))
    return selected


def _lease_pending_tasks(
    session: Session,
    *,
    limit: int,
    now: datetime,
    worker_id: str,
    lease_seconds: int,
    source_id: str | None,
    site_id: str | None,
) -> list[tuple[CollectionTask, DataSource]]:
    selected = _select_pending_tasks(
        session,
        limit=limit,
        now=now,
        source_id=source_id,
        site_id=site_id,
        include_expired_running=True,
        lock_rows=True,
    )
    lease_until = now + timedelta(seconds=max(60, lease_seconds))
    for task, _source in selected:
        task.status = "running"
        task.started_at = now
        task.finished_at = None
        task.worker_id = worker_id
        task.lease_expires_at = lease_until
        task.heartbeat_at = now
        task.updated_at = now
        state = session.get(SourceCrawlState, task.source_id)
        if state is not None:
            state.last_started_at = now
            state.updated_at = now
    session.commit()
    return selected


def _rate_limit(source: DataSource) -> tuple[float, float]:
    policy = source.schedule_policy or {}
    rate = policy.get("rate_limit") if isinstance(policy.get("rate_limit"), dict) else {}
    return float(rate.get("min_delay_seconds") or 0), float(rate.get("jitter_seconds") or 0)


def _default_client(source: DataSource) -> object:
    min_interval, jitter = _rate_limit(source)
    site_id = source_site_id(source)
    if site_id == "trading.changsha.hnsggzy.jsgc":
        return UrllibChangshaTradingClient(min_interval_seconds=min_interval, jitter_seconds=jitter)
    if site_id == "trading.cdggzy.jsgc":
        return UrllibChengduTradingClient(min_interval_seconds=min_interval, jitter_seconds=jitter)
    if site_id == "trading.cqggzy.jsgc":
        return UrllibChongqingTradingClient(min_interval_seconds=min_interval, jitter_seconds=jitter)
    if site_id == "trading.scggzy.jsgc":
        return UrllibSichuanTradingClient(min_interval_seconds=min_interval, jitter_seconds=jitter)
    raise ValueError(f"TRADING_CRAWLER_NOT_REGISTERED: {site_id}")


def _task_plan_from_task(task: CollectionTask) -> dict[str, Any]:
    override = task.config_override if isinstance(task.config_override, dict) else {}
    plan = override.get("trading_crawl") if isinstance(override.get("trading_crawl"), dict) else {}
    return dict(plan)


def _run_task(
    session: Session,
    storage: ObjectStore,
    *,
    task: CollectionTask,
    source: DataSource,
    client_factory: Callable[[DataSource], object] | None = None,
) -> dict[str, Any]:
    plan = _task_plan_from_task(task)
    client = client_factory(source) if client_factory is not None else _default_client(source)
    kwargs = {
        "source_id": source.source_id,
        "client": client,
        "channel_ids": plan.get("channel_ids"),
        "max_pages": max(1, int(plan.get("max_pages") or 1)),
        "page_size": max(1, int(plan.get("page_size") or 1)),
        "max_items_per_channel": (
            max(1, int(plan["max_items_per_channel"]))
            if plan.get("max_items_per_channel") is not None
            else None
        ),
        "start_date": plan.get("start_date"),
        "end_date": plan.get("end_date"),
        "as_of_date": plan.get("end_date"),
        "stop_on_existing": plan.get("stop_on_existing") is not False,
        "actor_id": f"trading-task-loop:{task.task_id}",
    }
    site_id = source_site_id(source)
    if site_id == "trading.changsha.hnsggzy.jsgc":
        return run_changsha_trading_channels(session, storage, **kwargs)
    if site_id == "trading.cdggzy.jsgc":
        return run_chengdu_trading_channels(session, storage, **kwargs)
    if site_id == "trading.cqggzy.jsgc":
        return run_chongqing_trading_channels(session, storage, **kwargs)
    if site_id == "trading.scggzy.jsgc":
        return run_sichuan_trading_channels(session, storage, **kwargs)
    raise ValueError(f"TRADING_CRAWLER_NOT_REGISTERED: {site_id}")


def _cursor_from_result(result: dict[str, Any]) -> tuple[str | None, str | None]:
    for channel in result.get("channels") or []:
        if not isinstance(channel, dict):
            continue
        key = channel.get("cursor_source_item_key")
        if key:
            return str(key), str(channel.get("cursor_publish_date") or "") or None
    return None, None


def _complete_task(
    session: Session,
    *,
    task: CollectionTask,
    source: DataSource,
    result: dict[str, Any],
    now: datetime,
) -> None:
    ingested = int(result.get("ingested_count") or 0)
    duplicate = int(result.get("skipped_existing_count") or 0)
    failed = int(result.get("failed_count") or 0)
    task.status = "done"
    task.finished_at = now
    task.lease_expires_at = None
    task.heartbeat_at = now
    task.discovered_count = ingested + duplicate + failed
    task.downloaded_count = ingested
    task.new_file_count = ingested
    task.duplicate_file_count = duplicate
    task.failed_count = failed
    task.error_code = None
    task.error_message = None
    key, publish_date = _cursor_from_result(result)
    task.cursor_after = {
        "source_item_key": key,
        "publish_date": publish_date,
        "channels": result.get("channels") or [],
    }
    override = dict(task.config_override or {})
    override["trading_result"] = {
        "ingested_count": ingested,
        "skipped_existing_count": duplicate,
        "failed_count": failed,
        "failed_items": list(result.get("failed_items") or [])[:20],
    }
    task.config_override = override

    state = session.get(SourceCrawlState, source.source_id)
    if state is not None:
        state.last_succeeded_at = now
        state.last_seen_item_key = key
        state.last_seen_publish_date = publish_date
        state.cursor = dict(task.cursor_after)
        if ingested:
            state.last_new_item_at = now
            state.consecutive_empty_runs = 0
        else:
            state.consecutive_empty_runs += 1
        state.updated_at = now
    session.commit()

    # Only a clean small verification run promotes a new source.  A partial
    # attachment failure is intentionally kept pending for an operator retry.
    if _verification_task(task) and failed == 0 and source.status == "pending_verify":
        set_trading_source_active(session, source)


def _classify_error(exc: Exception) -> str:
    message = str(exc).lower()
    if "timeout" in message:
        return "TRADING_CRAWL_TIMEOUT"
    if "not_registered" in message:
        return "TRADING_CRAWLER_NOT_REGISTERED"
    if isinstance(exc, ValueError):
        return "TRADING_CRAWL_PARSE_ERROR"
    return "TRADING_CRAWL_TASK_FAILED"


def _retry_or_fail(session: Session, *, task: CollectionTask, exc: Exception, now: datetime) -> str:
    task.attempt = int(task.attempt or 0) + 1
    task.error_code = _classify_error(exc)
    task.error_message = str(exc)[:500]
    override = dict(task.config_override or {})
    override["last_error"] = {
        "exception_type": type(exc).__name__,
        "message": str(exc)[:500],
        "at": now.isoformat(),
    }
    task.config_override = override
    task.finished_at = now
    task.lease_expires_at = None
    task.heartbeat_at = now
    if task.attempt < max(1, int(task.max_attempts or 3)):
        task.status = "pending"
        task.scheduled_at = now + timedelta(seconds=min(60 * (2**task.attempt), 14400))
        result = "retry"
    else:
        task.status = "failed"
        result = "dead_letter"
    session.commit()
    return result


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def run_trading_worker(
    session: Session,
    *,
    limit: int = 10,
    source_id: str | None = None,
    site_id: str | None = None,
    dry_run: bool = False,
    trigger: str = "manual",
    worker_id: str | None = None,
    lease_seconds: int = 14400,
    now: datetime | None = None,
    storage: ObjectStore | None = None,
    client_factory: Callable[[DataSource], object] | None = None,
) -> dict[str, Any]:
    current = ensure_aware_utc(now or utcnow())
    identity = worker_id or default_worker_id()
    if dry_run:
        rows = _select_pending_tasks(
            session,
            limit=max(1, limit),
            now=current,
            source_id=source_id,
            site_id=site_id,
        )
    else:
        rows = _lease_pending_tasks(
            session,
            limit=max(1, limit),
            now=current,
            worker_id=identity,
            lease_seconds=lease_seconds,
            source_id=source_id,
            site_id=site_id,
        )
    report: dict[str, Any] = {
        "run_type": "trading_worker",
        "data_domain": DATA_DOMAIN,
        "trigger": trigger,
        "run_started_at": current.isoformat(),
        "worker_id": identity,
        "dry_run": dry_run,
        "leased_count": len(rows),
        "done_count": 0,
        "retry_count": 0,
        "dead_letter_count": 0,
        "archive_created_count": 0,
        "duplicate_count": 0,
        "failed_count": 0,
        "per_source": [],
    }
    if dry_run:
        for task, source in rows:
            report["per_source"].append(
                {"task_id": task.task_id, "source_id": source.source_id, "site_id": source_site_id(source), "status": "dry_run"}
            )
        report["run_finished_at"] = current.isoformat()
        return report

    object_store = storage or get_object_store()
    for task, source in rows:
        try:
            result = _run_task(
                session,
                object_store,
                task=task,
                source=source,
                client_factory=client_factory,
            )
            _complete_task(session, task=task, source=source, result=result, now=current)
            ingested = int(result.get("ingested_count") or 0)
            duplicate = int(result.get("skipped_existing_count") or 0)
            failed = int(result.get("failed_count") or 0)
            report["done_count"] += 1
            report["archive_created_count"] += ingested
            report["duplicate_count"] += duplicate
            report["failed_count"] += failed
            report["per_source"].append(
                {
                    "task_id": task.task_id,
                    "source_id": source.source_id,
                    "site_id": source_site_id(source),
                    "status": "done",
                    "archive_created": ingested,
                    "duplicates": duplicate,
                    "failed": failed,
                }
            )
        except Exception as exc:  # noqa: BLE001 - preserve a durable retry record per task.
            outcome = _retry_or_fail(session, task=task, exc=exc, now=current)
            report[f"{outcome}_count"] += 1
            report["per_source"].append(
                {
                    "task_id": task.task_id,
                    "source_id": source.source_id,
                    "site_id": source_site_id(source),
                    "status": outcome,
                    "error_code": task.error_code,
                    "error_message": task.error_message,
                }
            )
    report["run_finished_at"] = ensure_aware_utc(now or utcnow()).isoformat()
    return report


def run_trading_loop_once(
    session: Session,
    *,
    now: datetime | None = None,
    worker_limit: int = 10,
    max_worker_cycles: int = 10,
    force: bool = False,
    dry_run: bool = False,
    trigger: str = "trading_task_loop",
    worker_id: str | None = None,
    lease_seconds: int = 14400,
    storage: ObjectStore | None = None,
    source_id: str | None = None,
    site_id: str | None = None,
    verify: bool = False,
    channel_ids: list[str] | None = None,
    max_pages: int | None = None,
    page_size: int | None = None,
    max_items_per_channel: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    client_factory: Callable[[DataSource], object] | None = None,
) -> dict[str, Any]:
    current = ensure_aware_utc(now or utcnow())
    scheduler = run_trading_scheduler(
        session,
        now=current,
        trigger=trigger,
        source_id=source_id,
        site_id=site_id,
        dry_run=dry_run,
        force=force,
        verify=verify,
        channel_ids=channel_ids,
        max_pages=max_pages,
        page_size=page_size,
        max_items_per_channel=max_items_per_channel,
        start_date=start_date,
        end_date=end_date,
    )
    reports: list[dict[str, Any]] = []
    totals = {"leased_count": 0, "done_count": 0, "retry_count": 0, "dead_letter_count": 0, "archive_created_count": 0, "duplicate_count": 0, "failed_count": 0}
    for _ in range(max(1, max_worker_cycles)):
        worker = run_trading_worker(
            session,
            limit=max(1, worker_limit),
            source_id=source_id,
            site_id=site_id,
            dry_run=dry_run,
            trigger=trigger,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            now=current,
            storage=storage,
            client_factory=client_factory,
        )
        reports.append(worker)
        for key in totals:
            totals[key] += int(worker.get(key) or 0)
        if not int(worker.get("leased_count") or 0):
            break
    return {
        "run_type": "trading_task_loop_round",
        "run_at": current.isoformat(),
        "dry_run": dry_run,
        "scheduler": scheduler,
        "worker_cycles": len(reports),
        "worker_summary": totals,
        "worker_reports": reports,
    }


def run_trading_loop(
    session_factory: sessionmaker[Session],
    *,
    interval_seconds: int = 3600,
    max_rounds: int | None = None,
    **round_kwargs: Any,
) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    count = 0
    while max_rounds is None or count < max_rounds:
        with session_factory() as session:
            reports.append(run_trading_loop_once(session, **round_kwargs))
        count += 1
        if max_rounds is not None and count >= max_rounds:
            break
        time.sleep(max(1, interval_seconds))
    return reports


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
    except Exception as exc:  # noqa: BLE001
        print(f"[trading_task_loop] could not load .env: {exc}", flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the task-and-loop trading announcement crawler runtime.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("register", help="Register the bundled Chengdu and Chongqing sources as pending verification.")
    run = subparsers.add_parser("run", help="Schedule and drain one or more trading task-loop rounds.")
    run.add_argument("--once", action="store_true", help="Run exactly one round.")
    run.add_argument("--rounds", type=int)
    run.add_argument("--interval-seconds", type=int, default=3600)
    run.add_argument("--worker-limit", type=int, default=10)
    run.add_argument("--max-worker-cycles", type=int, default=10)
    run.add_argument("--worker-id")
    run.add_argument("--lease-seconds", type=int, default=14400)
    run.add_argument("--source-id")
    run.add_argument("--site-id")
    run.add_argument("--verify", action="store_true", help="Allow a pending_verify source to run a small verification task.")
    run.add_argument("--channel-id", action="append", dest="channel_ids")
    run.add_argument("--max-pages", type=int)
    run.add_argument("--page-size", type=int)
    run.add_argument("--max-items-per-channel", type=int)
    run.add_argument("--start-date", help="History backfill start date, YYYY-MM-DD; must be paired with --end-date.")
    run.add_argument("--end-date", help="History backfill end date, YYYY-MM-DD; must be paired with --start-date.")
    run.add_argument("--force", action="store_true")
    run.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _load_env()
    if str(SERVICE_DIR) not in sys.path:
        sys.path.insert(0, str(SERVICE_DIR))
    init_db()
    session_factory = get_session_factory()
    if args.command == "register":
        with session_factory() as session:
            results = [result.to_dict() for result in restore_all_source_configs(session)]
        print(json.dumps({"registered": results}, ensure_ascii=False, indent=2))
        return 0

    rounds = 1 if args.once else args.rounds
    reports = run_trading_loop(
        session_factory,
        interval_seconds=args.interval_seconds,
        max_rounds=rounds,
        worker_limit=args.worker_limit,
        max_worker_cycles=args.max_worker_cycles,
        worker_id=args.worker_id,
        lease_seconds=args.lease_seconds,
        source_id=args.source_id,
        site_id=args.site_id,
        verify=args.verify,
        channel_ids=args.channel_ids,
        max_pages=args.max_pages,
        page_size=args.page_size,
        max_items_per_channel=args.max_items_per_channel,
        start_date=args.start_date,
        end_date=args.end_date,
        force=args.force,
        dry_run=args.dry_run,
    )
    for report in reports:
        print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
