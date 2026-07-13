from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import random
import re

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.archive_rules import build_cost_info_business_key
from app.collection import create_collection_task
from app.guangdong_pdf_cost_info import GUANGDONG_PDF_CITY_SOURCE_CONFIGS
from app.mianyang_cost_info import (
    MIANYANG_PARSER_VERSION,
    ingest_mianyang_issue,
    list_mianyang_issues,
)
from app.models import CollectionTask
from app.sichuan_pdf_cost_info import (
    DEYANG_PARSER_VERSION,
    SICHUAN_PDF_CITY_SOURCE_CONFIGS,
    ingest_sichuan_pdf_issue,
    list_sichuan_pdf_issues,
)
from app.source_adapter import active_parser_version, archive_business_key_exists, crawl_lag_days, require_data_source
from app.storage import ObjectStore
from app.zigong_cost_info import (
    ZIGONG_PARSER_VERSION,
    ingest_zigong_issue,
    list_zigong_issues,
)

SICHUAN_AUTO_HISTORY_REGION_CODES = (
    "510400",
    "510500",
    "510600",
    "510900",
    "511100",
    "511700",
    "510700",
    "510300",
)
GUANGDONG_PDF_AUTO_HISTORY_REGION_CODES = (
    "440100",
    "440400",
    "440500",
    "440600",
    "441700",
    "441900",
    "442000",
    "445100",
    "445300",
)
SHARED_PDF_CITY_PARSER_VERSIONS = set(SICHUAN_PDF_CITY_SOURCE_CONFIGS.values()) | set(
    GUANGDONG_PDF_CITY_SOURCE_CONFIGS.values()
)


def fanout_sichuan_history_tasks(
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
    as_of_date: str | None = None,
    playwright_list_client=None,
) -> dict[str, object]:
    source = require_data_source(session, source_id, domain_type="cost_info")
    _ensure_auto_history_source(source.region_code)
    config = source.config or {}
    parser_version = active_parser_version(config)
    parser = _active_parser(config, parser_version=parser_version)
    adapter_kind = _adapter_kind(parser_version)
    queue_config = _queue_config(config)
    base_time = base_time or datetime.now(UTC)
    delay_seconds = int(min_delay_seconds if min_delay_seconds is not None else queue_config.get("min_delay_seconds", 3))
    jitter_window = int(jitter_seconds if jitter_seconds is not None else queue_config.get("jitter_seconds", 0) or 0)
    next_scheduled_at = base_time
    rows, fallback_name = _list_rows(
        client=client,
        parser=parser,
        parser_version=parser_version,
        playwright_list_client=playwright_list_client,
    )

    created_count = 0
    skipped_existing_archive_count = 0
    skipped_existing_task_count = 0
    cursor_source_item_key = _row_source_item_key(rows[0]) if rows else None
    cursor_publish_date = _row_publish_date(rows[0]) if rows else None

    for row in rows:
        if max_tasks is not None and created_count >= max_tasks:
            break
        business_key = _business_key_for_row(source_id=source.source_id, region_code=source.region_code, row=row, parser=parser)
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
            operator_id="sichuan-history-backfill-scheduler",
            task_type="crawl_issue",
            trigger_type=mode,
            data_domain="cost_info",
            batch_id=batch_id,
            period_raw=_task_period_raw(_row_title(row)),
            period_start=_period_start_from_title(_row_title(row), parser=parser),
            config_override={
                "source_registry_schema_version": config.get("registry_schema_version", "source_registry.v1"),
                "site_id": config.get("stable", {}).get("site_id"),
                "parser_version": parser_version,
                "queue_mode": mode,
                "adapter_kind": adapter_kind,
                "source_item": _task_source_item(row),
            },
        )
        if fallback_name:
            override = dict(task.config_override or {})
            override["list_client_fallback"] = fallback_name
            task.config_override = override
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
        "adapter_kind": adapter_kind,
        "cursor_source_item_key": cursor_source_item_key,
        "cursor_publish_date": cursor_publish_date,
        "crawl_lag_days": crawl_lag_days(cursor_publish_date, as_of_date=as_of_date),
    }


def run_due_sichuan_history_tasks(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client,
    now: datetime | None = None,
    limit: int = 1,
    retry_delay_seconds: int = 60,
    actor_id: str = "sichuan-history-backfill-worker",
) -> dict[str, int]:
    source = require_data_source(session, source_id, domain_type="cost_info")
    _ensure_auto_history_source(source.region_code)
    config = source.config or {}
    parser_version = active_parser_version(config)
    parser = _active_parser(config, parser_version=parser_version)
    queue_config = _queue_config(config)
    now = now or datetime.now(UTC)
    max_concurrent = int(queue_config.get("max_concurrent_per_host", 1) or 1)
    effective_limit = max(1, min(limit, max_concurrent))

    tasks = list(
        session.scalars(
            select(CollectionTask)
            .where(
                CollectionTask.source_id == source.source_id,
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
        task.status = "running"
        task.started_at = now
        task.attempt += 1
        session.commit()
        try:
            _ingest_task_row(
                session,
                storage,
                source_id=source.source_id,
                client=client,
                row=_task_row(task),
                parser=parser,
                parser_version=parser_version,
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
            task.error_code = "SICHUAN_HISTORY_CRAWL_TASK_FAILED"
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


def _list_rows(*, client, parser: dict, parser_version: str, playwright_list_client) -> tuple[list[dict], str | None]:
    try:
        return _list_rows_with_client(client=client, parser=parser, parser_version=parser_version), None
    except Exception:
        if parser_version != DEYANG_PARSER_VERSION or playwright_list_client is None:
            raise
        return _list_rows_with_client(
            client=playwright_list_client,
            parser=parser,
            parser_version=parser_version,
        ), "playwright"


def _list_rows_with_client(*, client, parser: dict, parser_version: str) -> list[dict]:
    if parser_version in SHARED_PDF_CITY_PARSER_VERSIONS:
        return list_sichuan_pdf_issues(client, parser=parser)
    if parser_version == MIANYANG_PARSER_VERSION:
        return list_mianyang_issues(client, parser=parser)
    if parser_version == ZIGONG_PARSER_VERSION:
        return list_zigong_issues(client, parser=parser)
    raise ValueError(f"SICHUAN_HISTORY_ADAPTER_UNSUPPORTED: {parser_version}")


def _ingest_task_row(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client,
    row: dict,
    parser: dict,
    parser_version: str,
    actor_id: str,
    task_id: str,
):
    if parser_version in SHARED_PDF_CITY_PARSER_VERSIONS:
        return ingest_sichuan_pdf_issue(
            session,
            storage,
            source_id=source_id,
            client=client,
            row=row,
            parser=parser,
            actor_id=actor_id,
            task_id=task_id,
        )
    if parser_version == MIANYANG_PARSER_VERSION:
        return ingest_mianyang_issue(
            session,
            storage,
            source_id=source_id,
            client=client,
            row=row,
            parser=parser,
            actor_id=actor_id,
            task_id=task_id,
        )
    if parser_version == ZIGONG_PARSER_VERSION:
        return ingest_zigong_issue(
            session,
            storage,
            source_id=source_id,
            client=client,
            row=row,
            parser=parser,
            actor_id=actor_id,
            task_id=task_id,
        )
    raise ValueError(f"SICHUAN_HISTORY_ADAPTER_UNSUPPORTED: {parser_version}")


def _active_parser(config: dict, *, parser_version: str) -> dict:
    parser_config = config.get("parser") if isinstance(config.get("parser"), dict) else {}
    parsers = parser_config.get("parsers") if isinstance(parser_config.get("parsers"), dict) else {}
    parser = parsers.get(parser_version)
    if not isinstance(parser, dict):
        raise ValueError("SICHUAN_HISTORY_PARSER_CONFIG_MISSING")
    return parser


def _adapter_kind(parser_version: str) -> str:
    if parser_version in set(SICHUAN_PDF_CITY_SOURCE_CONFIGS.values()):
        return "sichuan_pdf"
    if parser_version in set(GUANGDONG_PDF_CITY_SOURCE_CONFIGS.values()):
        return "guangdong_pdf"
    if parser_version == MIANYANG_PARSER_VERSION:
        return "mianyang_xls"
    if parser_version == ZIGONG_PARSER_VERSION:
        return "zigong_zip"
    raise ValueError(f"SICHUAN_HISTORY_ADAPTER_UNSUPPORTED: {parser_version}")


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


def _ensure_auto_history_source(region_code: str | None) -> None:
    if region_code not in SICHUAN_AUTO_HISTORY_REGION_CODES + GUANGDONG_PDF_AUTO_HISTORY_REGION_CODES:
        raise ValueError(f"SICHUAN_HISTORY_SOURCE_OUT_OF_SCOPE: {region_code}")


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
        raise ValueError("SICHUAN_HISTORY_TASK_SOURCE_ITEM_MISSING")
    return row


def _row_title(row: dict) -> str:
    for key in ("title", "Title", "name", "fileTitle", "periodName"):
        value = row.get(key)
        if value:
            return _clean_title(str(value))
    raise ValueError("SICHUAN_HISTORY_TITLE_MISSING")


def _row_publish_date(row: dict) -> str | None:
    for key in ("publish_date", "publishDate", "inputtime", "pubDate"):
        value = row.get(key)
        if value:
            return _date_from_text(str(value))
    return None


def _row_detail_url(row: dict) -> str | None:
    value = row.get("detail_url") or row.get("detailUrl") or row.get("url")
    return str(value) if value else None


def _row_source_item_key(row: dict) -> str:
    value = row.get("source_item_id") or row.get("id") or row.get("guid") or _row_detail_url(row)
    if not value:
        return _row_title(row)
    return str(value)


def _business_key_for_row(*, source_id: str, region_code: str | None, row: dict, parser: dict) -> str:
    title = _row_title(row)
    return build_cost_info_business_key(
        source_id=source_id,
        region_code=region_code,
        period=_period_start_from_title(title, parser=parser),
        title=title,
    )


def _period_start_from_title(title: str, *, parser: dict) -> str | None:
    period = parser.get("period") if isinstance(parser.get("period"), dict) else {}
    regex = str(period.get("regex") or r"(20\d{2})年(\d{1,2})月")
    match = re.search(regex, title)
    if not match:
        return None
    return f"{int(match.group(1)):04d}-{_month_number(match.group(2)):02d}"


def _month_number(raw_month: str) -> int:
    text = raw_month.strip()
    if text.isdigit():
        return int(text)
    chinese_months = {
        "一": 1,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
        "十一": 11,
        "十二": 12,
    }
    if text in chinese_months:
        return chinese_months[text]
    raise ValueError(f"INVALID_PERIOD_MONTH: {raw_month}")


def _date_from_text(text: str) -> str | None:
    match = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", text)
    if not match:
        return text[:10] if re.match(r"20\d{2}-\d{2}-\d{2}", text[:10]) else None
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def _clean_title(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip()


def _batch_id(source_id: str, source_item_key: str) -> str:
    digest = hashlib.sha1(source_item_key.encode("utf-8")).hexdigest()[:16]
    return f"cost_info:{source_id}:schistory:{digest}"
