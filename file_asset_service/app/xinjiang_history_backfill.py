from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import random
from collections.abc import Sequence

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.collection import create_collection_task
from app.models import CollectionTask
from app.source_adapter import active_parser_version, archive_business_key_exists, crawl_lag_days, require_data_source
from app.storage import ObjectStore
from app.xinjiang_cost_info import (
    XINJIANG_ASPNET_AJAX_AREA_PARSER_VERSION,
    XINJIANG_AREA_SOURCE_DEFS,
    _business_key_for_row,
    _period_start_from_title,
    _row_publish_date,
    _row_source_item_key,
    _row_title,
    ingest_xinjiang_area_issue,
    list_xinjiang_area_issues,
)

XINJIANG_HISTORY_REGION_CODES = tuple(source.region_code for source in XINJIANG_AREA_SOURCE_DEFS.values())
XINJIANG_HISTORY_DEFAULT_START_PERIOD = "2024-01"
XINJIANG_HISTORY_DEFAULT_END_PERIOD = "2026-12"


def list_xinjiang_history_issues(
    client,
    *,
    parser: dict,
    page_size: int = 20,
    max_pages: int | None = None,
    start_period: str | None = XINJIANG_HISTORY_DEFAULT_START_PERIOD,
    end_period: str | None = XINJIANG_HISTORY_DEFAULT_END_PERIOD,
) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    page = 1
    while True:
        issue_list = list_xinjiang_area_issues(client, parser=parser, page=page, page_size=page_size)
        for row in issue_list.rows:
            source_item_key = _row_source_item_key(row)
            if source_item_key in seen:
                continue
            seen.add(source_item_key)
            if _row_within_period_window(row, start_period=start_period, end_period=end_period):
                rows.append(row)
        if max_pages is not None and page >= max_pages:
            break
        if not issue_list.rows:
            break
        if page * page_size >= issue_list.total:
            break
        page += 1
    return rows


def fanout_xinjiang_history_tasks(
    session: Session,
    *,
    source_id: str,
    client,
    mode: str = "history_backfill",
    base_time: datetime | None = None,
    min_delay_seconds: int | None = None,
    jitter_seconds: int | None = None,
    jitter_func=None,
    max_tasks: int | None = None,
    start_period: str | None = XINJIANG_HISTORY_DEFAULT_START_PERIOD,
    end_period: str | None = XINJIANG_HISTORY_DEFAULT_END_PERIOD,
    page_size: int = 20,
    max_pages: int | None = None,
    as_of_date: str | None = None,
) -> dict[str, object]:
    source = require_data_source(session, source_id, domain_type="cost_info")
    _ensure_xinjiang_history_source(source.region_code)
    config = source.config or {}
    parser_version = active_parser_version(config)
    if parser_version != XINJIANG_ASPNET_AJAX_AREA_PARSER_VERSION:
        raise ValueError(f"XINJIANG_HISTORY_ADAPTER_UNSUPPORTED: {parser_version}")
    parser = _active_parser(config, parser_version=parser_version)
    queue_config = _queue_config(config)
    delay_seconds = int(min_delay_seconds if min_delay_seconds is not None else queue_config.get("min_delay_seconds", 5))
    jitter_window = int(jitter_seconds if jitter_seconds is not None else queue_config.get("jitter_seconds", 0) or 0)
    next_scheduled_at = base_time or datetime.now(UTC)

    rows = list_xinjiang_history_issues(
        client,
        parser=parser,
        page_size=page_size,
        max_pages=max_pages,
        start_period=start_period,
        end_period=end_period,
    )
    created_count = 0
    skipped_existing_archive_count = 0
    skipped_existing_task_count = 0
    cursor_source_item_key = _row_source_item_key(rows[0]) if rows else None
    cursor_publish_date = _row_publish_date(rows[0]) if rows else None

    for row in rows:
        if max_tasks is not None and created_count >= max_tasks:
            break
        business_key = _business_key_for_row(source_id=source.source_id, region_code=source.region_code, row=row)
        if archive_business_key_exists(
            session,
            tenant_code=source.asset_tenant_code,
            domain_type="cost_info",
            business_key=business_key,
        ):
            skipped_existing_archive_count += 1
            continue

        source_item_key = _row_source_item_key(row)
        batch_id = _batch_id(source.source_id, source_item_key)
        existing_task = session.scalar(
            select(CollectionTask).where(
                CollectionTask.source_id == source.source_id,
                CollectionTask.batch_id == batch_id,
            )
        )
        if existing_task is not None:
            skipped_existing_task_count += 1
            continue

        task = create_collection_task(
            session,
            source_id=source.source_id,
            operator_type="system",
            operator_id="xinjiang-history-backfill-scheduler",
            task_type="crawl_issue",
            trigger_type=mode,
            data_domain="cost_info",
            batch_id=batch_id,
            period_raw=_task_period_raw(_row_title(row)),
            period_start=_period_start_from_title(_row_title(row)),
            config_override={
                "source_registry_schema_version": config.get("registry_schema_version", "source_registry.v1"),
                "site_id": config.get("stable", {}).get("site_id"),
                "parser_version": parser_version,
                "queue_mode": mode,
                "adapter_kind": "aspnet_ajax_area",
                "source_item": _task_source_item(row),
            },
        )
        task.priority = _task_priority(mode)
        task.scheduled_at = next_scheduled_at
        session.commit()
        created_count += 1
        next_scheduled_at = next_scheduled_at + timedelta(
            seconds=delay_seconds + _jitter_delay(jitter_window, jitter_func=jitter_func)
        )

    return {
        "source_id": source_id,
        "region_code": source.region_code,
        "listed_count": len(rows),
        "created_count": created_count,
        "skipped_existing_archive_count": skipped_existing_archive_count,
        "skipped_existing_task_count": skipped_existing_task_count,
        "adapter_kind": "aspnet_ajax_area",
        "cursor_source_item_key": cursor_source_item_key,
        "cursor_publish_date": cursor_publish_date,
        "crawl_lag_days": crawl_lag_days(cursor_publish_date, as_of_date=as_of_date),
    }


def run_due_xinjiang_history_tasks(
    session: Session,
    storage: ObjectStore,
    *,
    source_ids: Sequence[str],
    client,
    now: datetime | None = None,
    limit: int = 1,
    retry_delay_seconds: int = 60,
    actor_id: str = "xinjiang-history-backfill-worker",
) -> dict[str, int]:
    if not source_ids:
        return {"leased_count": 0, "done_count": 0, "retry_count": 0, "dead_letter_count": 0}
    now = now or datetime.now(UTC)
    effective_limit = max(1, min(limit, 1))
    tasks = list(
        session.scalars(
            select(CollectionTask)
            .where(
                CollectionTask.source_id.in_(list(source_ids)),
                CollectionTask.task_type == "crawl_issue",
                CollectionTask.status == "pending",
                or_(CollectionTask.scheduled_at.is_(None), CollectionTask.scheduled_at <= now),
            )
            .order_by(CollectionTask.priority, CollectionTask.scheduled_at, CollectionTask.created_at)
            .limit(effective_limit)
        ).all()
    )

    done_count = 0
    retry_count = 0
    dead_letter_count = 0
    for task in tasks:
        source = require_data_source(session, task.source_id, domain_type="cost_info")
        _ensure_xinjiang_history_source(source.region_code)
        config = source.config or {}
        parser_version = active_parser_version(config)
        parser = _active_parser(config, parser_version=parser_version)
        queue_config = _queue_config(config)

        task.status = "running"
        task.started_at = now
        task.attempt += 1
        session.commit()
        try:
            ingest_xinjiang_area_issue(
                session,
                storage,
                source_id=source.source_id,
                client=client,
                row=_task_row(task),
                parser=parser,
                actor_id=actor_id,
                task_id=task.task_id,
            )
            task = session.get(CollectionTask, task.task_id)
            task.status = "done"
            task.finished_at = now
            task.error_code = None
            task.error_message = None
            session.commit()
            done_count += 1
        except Exception as exc:
            session.rollback()
            task = session.get(CollectionTask, task.task_id)
            task.failed_count += 1
            task.error_code = "XINJIANG_HISTORY_CRAWL_TASK_FAILED"
            task.error_message = str(exc)[:500]
            if task.attempt >= task.max_attempts:
                task.status = "failed"
                task.finished_at = now
                override = dict(task.config_override or {})
                override["manual_recovery"] = {
                    "required": True,
                    "spec": "017",
                    "reason": task.error_code,
                }
                task.config_override = override
                dead_letter_count += 1
            else:
                task.status = "pending"
                task.scheduled_at = now + timedelta(
                    seconds=_retry_delay_seconds(
                        retry_delay_seconds,
                        attempt=task.attempt,
                        queue_config=queue_config,
                    )
                )
                retry_count += 1
            session.commit()

    return {
        "leased_count": len(tasks),
        "done_count": done_count,
        "retry_count": retry_count,
        "dead_letter_count": dead_letter_count,
    }


def _active_parser(config: dict, *, parser_version: str) -> dict:
    parser_config = config.get("parser") if isinstance(config.get("parser"), dict) else {}
    parsers = parser_config.get("parsers") if isinstance(parser_config.get("parsers"), dict) else {}
    parser = parsers.get(parser_version)
    if not isinstance(parser, dict):
        raise ValueError("XINJIANG_HISTORY_PARSER_CONFIG_MISSING")
    return parser


def _queue_config(config: dict) -> dict:
    ops = config.get("ops") if isinstance(config.get("ops"), dict) else {}
    queue = ops.get("queue") if isinstance(ops.get("queue"), dict) else {}
    return queue


def _task_priority(mode: str) -> int:
    return 10 if mode == "incremental" else 20


def _jitter_delay(jitter_seconds: int, *, jitter_func) -> int:
    if jitter_seconds <= 0:
        return 0
    if jitter_func is not None:
        return int(jitter_func(jitter_seconds))
    return random.randint(0, jitter_seconds)


def _retry_delay_seconds(base_delay_seconds: int, *, attempt: int, queue_config: dict) -> int:
    if queue_config.get("retry_backoff") != "exponential":
        return base_delay_seconds
    exponent = max(attempt - 1, 0)
    return base_delay_seconds * (2**exponent)


def _ensure_xinjiang_history_source(region_code: str | None) -> None:
    if region_code not in XINJIANG_HISTORY_REGION_CODES:
        raise ValueError(f"XINJIANG_HISTORY_SOURCE_OUT_OF_SCOPE: {region_code}")


def _task_source_item(row: dict) -> dict[str, object]:
    return {key: value for key, value in row.items() if isinstance(key, str)}


def _task_period_raw(title: str, *, limit: int = 64) -> str:
    if len(title) <= limit:
        return title
    return f"{title[: max(limit - 3, 0)]}..."


def _task_row(task: CollectionTask) -> dict:
    override = task.config_override or {}
    row = override.get("source_item")
    if not isinstance(row, dict):
        raise ValueError("XINJIANG_HISTORY_TASK_SOURCE_ITEM_MISSING")
    return row


def _row_within_period_window(row: dict, *, start_period: str | None, end_period: str | None) -> bool:
    period = _period_start_from_title(_row_title(row))
    if period is None:
        return False
    if start_period and period < start_period:
        return False
    if end_period and period > end_period:
        return False
    return True


def _batch_id(source_id: str, source_item_key: str) -> str:
    digest = hashlib.sha1(source_item_key.encode("utf-8")).hexdigest()[:16]
    return f"cost_info:{source_id}:xjhistory:{digest}"
