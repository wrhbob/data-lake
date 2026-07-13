from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.archive_rules import build_cost_info_business_key
from app.collection import create_collection_task
from app.models import Archive, CollectionTask
from app.source_adapter import SourceRegistryHttpClient, archive_business_key_exists, crawl_lag_days, require_data_source
from app.source_registry import CostInfoAttachment, CostInfoDiscoveredItem, ingest_cost_info_registry_item
from app.storage import ObjectStore

CHONGQING_ENTRY_URL = "http://www.cqsgczjxx.org/Pages/CQZJW/priceInformation.aspx"
CHONGQING_DOWNLOAD_BASE_URL = "http://manage.cqsgczjxx.org/_UploadFile/"
CHONGQING_COST_INFO_PARSER_VERSION = "cqsgczjxx.pdf-list.v1"
QUERY_INFO_PRICE_ENDPOINT = "/Service/MaterialPriceQuerySvr.svrx/QueryInfoPrice"
PDF_LIST_ENDPOINT = "/Service/FileDownInfoMgeSvr.assx/QueryFileDownInfoList"
PDF_YEARS_ENDPOINT = "/Service/FileDownInfoMgeSvr.svrx/QueryPdfYear?type=downpdf"


class ChongqingCostInfoClient(Protocol):
    def post_form_json(self, url: str, payload: dict[str, str]) -> dict:
        ...

    def get_bytes(self, url: str) -> tuple[bytes, str | None]:
        ...


class UrllibChongqingCostInfoClient(SourceRegistryHttpClient):
    def __init__(self, *, cookie: str | None = None, timeout: int = 30):
        super().__init__(referer=CHONGQING_ENTRY_URL, timeout=timeout, cookie=cookie)


def _format_cookie_header(cookies: list[dict]) -> str:
    """Render playwright cookie dicts as an HTTP ``Cookie`` header value."""
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies if c.get("name"))


def _browser_cookies(
    entry_url: str,
    *,
    timeout_seconds: int,
    settle_ms: int,
    playwright_starter=None,
) -> list[dict]:
    """Drive a headless chromium to ``entry_url`` and return its cookies.

    ``playwright_starter`` is injectable so tests can avoid launching a real
    browser; it mirrors ``playwright.sync_api.sync_playwright`` (a callable
    returning a context manager that yields an object exposing
    ``chromium.launch``). Replaces the old mac/linux-only
    ``~/.codex/skills/playwright/scripts/playwright_cli.sh`` subprocess.
    """
    from playwright.sync_api import sync_playwright

    starter = playwright_starter if playwright_starter is not None else sync_playwright
    with starter() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context()
            page = context.new_page()
            page.goto(entry_url, timeout=timeout_seconds * 1000, wait_until="domcontentloaded")
            page.wait_for_timeout(settle_ms)  # let the JS challenge resolve and set cookies
            return context.cookies()
        finally:
            browser.close()


def bootstrap_chongqing_cookie_with_playwright(
    *,
    entry_url: str = CHONGQING_ENTRY_URL,
    timeout_seconds: int = 30,
    settle_ms: int = 3000,
    playwright_starter=None,
) -> str:
    """Solve the Chongqing anti-bot challenge in a real browser and return the
    session cookie header for the urllib HTTP client.

    Cross-platform (playwright's in-process chromium). Raises
    ``CHONGQING_CHALLENGE_COOKIE_MISSING`` if the site sets no cookies.
    """
    header = _format_cookie_header(
        _browser_cookies(
            entry_url,
            timeout_seconds=timeout_seconds,
            settle_ms=settle_ms,
            playwright_starter=playwright_starter,
        )
    )
    if not header:
        raise ValueError("CHONGQING_CHALLENGE_COOKIE_MISSING")
    return header


def chongqing_cost_info_source_config() -> dict:
    return {
        "registry_schema_version": "source_registry.v1",
        "stable": {
            "site_id": "cost_info.cq.cqsgczjxx",
            "domain_type": "cost_info",
            "region_code": "500000",
            "publisher_name": "重庆市建设工程造价信息网",
            "publisher_type": "cost_station_public_institution",
            "entry_url": CHONGQING_ENTRY_URL,
            "periodicity": {"unit": "month", "period_key_format": "YYYY-MM"},
            "source_priority": "official",
        },
        "parser": {
            "active_parser_version": CHONGQING_COST_INFO_PARSER_VERSION,
            "parsers": {
                CHONGQING_COST_INFO_PARSER_VERSION: {
                    "adapter_kind": "chongqing_pdf",
                    "scope": "pdf_file_list_only",
                    "years_endpoint": f"http://www.cqsgczjxx.org{PDF_YEARS_ENDPOINT}",
                    "file_list_endpoint": f"http://www.cqsgczjxx.org{PDF_LIST_ENDPOINT}",
                    "download_base_url": CHONGQING_DOWNLOAD_BASE_URL,
                    "period": {
                        "source": "list_title",
                        "regex": r"(20\d{2})年第?([一二三四五六七八九十\d]{1,3})期",
                    },
                    "total_issue": {"source": "list_title", "regex": r"总第(\d+)期"},
                    "attachments": {"allowed": ["pdf"], "missing_excel_policy": "single_format_fallback"},
                    "prohibited_endpoints": [QUERY_INFO_PRICE_ENDPOINT],
                }
            },
        },
        "ops": {
            "enabled": True,
            "crawl_strategy_tier": "tier2_dynamic_html",
            "session_bootstrap": "playwright_cookie_then_http",
            "schedule": {"frequency": "monthly"},
            "queue": {
                "max_concurrent_per_host": 1,
                "global_priority": 20,
                "min_delay_seconds": 3,
                "jitter_seconds": 2,
                "retry_backoff": "exponential",
                "dead_letter_handoff": "017_manual_recovery",
            },
            "gated": False,
            "alert_thresholds": {"missing_period_count": 1},
        },
    }


def ingest_latest_chongqing_pdf_issue(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: ChongqingCostInfoClient,
    year: str,
    actor_id: str = "chongqing-cost-info-crawler",
) -> Archive:
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or chongqing_cost_info_source_config()
    parser = config["parser"]["parsers"][CHONGQING_COST_INFO_PARSER_VERSION]
    rows = list_chongqing_pdf_issues(client, parser=parser)
    if not rows:
        raise ValueError(f"CHONGQING_PDF_ISSUE_NOT_FOUND: {year}")

    row = next((candidate for candidate in rows if _row_title(candidate).startswith(str(year))), rows[0])
    return ingest_chongqing_pdf_row(
        session,
        storage,
        source_id=source_id,
        client=client,
        row=row,
        parser=parser,
        actor_id=actor_id,
    )


def run_chongqing_pdf_incremental(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: ChongqingCostInfoClient,
    actor_id: str = "chongqing-cost-info-crawler",
    max_items: int | None = None,
    as_of_date: str | None = None,
    stop_on_existing: bool = True,
) -> dict[str, object]:
    source = require_data_source(session, source_id, domain_type="cost_info")
    parser = (source.config or chongqing_cost_info_source_config())["parser"]["parsers"][CHONGQING_COST_INFO_PARSER_VERSION]
    rows = list_chongqing_pdf_issues(client, parser=parser)
    ingested_count = 0
    skipped_existing_count = 0
    stopped_on_existing = False
    cursor_source_item_key = _row_source_item_key(rows[0]) if rows else None
    cursor_publish_date = _row_publish_date(rows[0]) if rows else None
    missing_total_issue_numbers = _missing_total_issue_numbers(rows)

    for row in rows:
        business_key = _business_key_for_row(source_id=source.source_id, region_code=source.region_code, row=row)
        if archive_business_key_exists(
            session,
            tenant_code=source.asset_tenant_code,
            domain_type="cost_info",
            business_key=business_key,
        ):
            skipped_existing_count += 1
            if stop_on_existing:
                stopped_on_existing = True
                break
            continue
        ingest_chongqing_pdf_row(
            session,
            storage,
            source_id=source_id,
            client=client,
            row=row,
            parser=parser,
            actor_id=actor_id,
        )
        ingested_count += 1
        if max_items is not None and ingested_count >= max_items:
            break

    return {
        "source_id": source_id,
        "listed_count": len(rows),
        "ingested_count": ingested_count,
        "skipped_existing_count": skipped_existing_count,
        "stopped_on_existing": stopped_on_existing,
        "cursor_source_item_key": cursor_source_item_key,
        "cursor_publish_date": cursor_publish_date,
        "crawl_lag_days": crawl_lag_days(cursor_publish_date, as_of_date=as_of_date),
        "missing_total_issue_numbers": missing_total_issue_numbers,
    }


def fanout_chongqing_pdf_tasks(
    session: Session,
    *,
    source_id: str,
    rows: list[dict],
    mode: str,
    base_time: datetime | None = None,
    min_delay_seconds: int | None = None,
    max_tasks: int | None = None,
) -> dict[str, object]:
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or chongqing_cost_info_source_config()
    parser_version = config["parser"]["active_parser_version"]
    queue_config = _queue_config(config)
    base_time = base_time or datetime.now(UTC)
    delay_seconds = min_delay_seconds
    if delay_seconds is None:
        delay_seconds = int(queue_config.get("min_delay_seconds", 3))

    created_count = 0
    skipped_existing_archive_count = 0
    skipped_existing_task_count = 0
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
        batch_id = f"cost_info:{source.source_id}:cqpdf:{source_item_key}"
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
            operator_id="chongqing-cost-info-scheduler",
            task_type="crawl_issue",
            trigger_type=mode,
            data_domain="cost_info",
            batch_id=batch_id,
            period_raw=_row_title(row),
            period_start=_period_start_from_title(_row_title(row)),
            config_override={
                "source_registry_schema_version": config.get("registry_schema_version", "source_registry.v1"),
                "site_id": config.get("stable", {}).get("site_id"),
                "parser_version": parser_version,
                "queue_mode": mode,
                "source_item": _task_source_item(row),
            },
        )
        task.priority = _task_priority(mode)
        task.scheduled_at = base_time + timedelta(seconds=created_count * delay_seconds)
        session.commit()
        created_count += 1

    return {
        "source_id": source_id,
        "listed_count": len(rows),
        "created_count": created_count,
        "skipped_existing_archive_count": skipped_existing_archive_count,
        "skipped_existing_task_count": skipped_existing_task_count,
    }


def run_due_chongqing_pdf_tasks(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: ChongqingCostInfoClient,
    now: datetime | None = None,
    limit: int = 1,
    retry_delay_seconds: int = 60,
    actor_id: str = "chongqing-cost-info-worker",
) -> dict[str, int]:
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or chongqing_cost_info_source_config()
    parser = config["parser"]["parsers"][CHONGQING_COST_INFO_PARSER_VERSION]
    now = now or datetime.now(UTC)

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
            .limit(max(1, limit))
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
            ingest_chongqing_pdf_row(
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
            task.error_code = "CHONGQING_CRAWL_TASK_FAILED"
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
                task.scheduled_at = now + timedelta(seconds=retry_delay_seconds)
                retry_count += 1
            session.commit()

    return {
        "leased_count": len(tasks),
        "done_count": done_count,
        "retry_count": retry_count,
        "dead_letter_count": dead_letter_count,
    }


def list_chongqing_pdf_issues(
    client: ChongqingCostInfoClient,
    *,
    parser: dict,
) -> list[dict]:
    payload = {"type": "DownPdf", "keyWord": "", "orderBy": "", "isontopNum": "2", "pageIndex": "0", "pageSize": "-1"}
    return _response_rows(client.post_form_json(parser["file_list_endpoint"], payload))


def ingest_chongqing_pdf_row(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: ChongqingCostInfoClient,
    row: dict,
    parser: dict,
    actor_id: str,
    task_id: str | None = None,
) -> Archive:
    title = _row_title(row)
    file_name, download_url = _pdf_attachment_from_file_path(_row_file_path(row), parser["download_base_url"])
    content, content_type = client.get_bytes(download_url)
    if not content:
        raise ValueError("CHONGQING_PDF_EMPTY")

    item = CostInfoDiscoveredItem(
        title=title,
        publish_date=_row_publish_date(row),
        detail_url=CHONGQING_ENTRY_URL,
        discovered_at=_row_publish_datetime(row),
        fetched_at=_row_publish_datetime(row),
        attachments=[
            CostInfoAttachment(
                file_name=file_name,
                content=content,
                url=download_url,
                content_type=content_type or "application/pdf",
            )
        ],
        metadata={
            "listed_title": title,
            "total_issue_no": _total_issue_no(title),
            "price_source_type": "info_price",
            "tax_type": None,
            "source_attachment_mode": "pdf_only",
            "extractability_hint": "low_pdf_only",
            "source_file_guid": _row_source_item_key(row),
            "source_file_size": _row_file_size(row),
            "publish_date_raw": _row_publish_datetime(row),
        },
    )
    return ingest_cost_info_registry_item(
        session,
        storage,
        source_id=source_id,
        item=item,
        actor_id=actor_id,
        task_id=task_id,
    )


def _response_rows(response: dict) -> list[dict]:
    if isinstance(response, list):
        return [row for row in response if isinstance(row, dict)]
    rows = response.get("rows")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    data = response.get("data")
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    items = response.get("_Items")
    if isinstance(items, list):
        return [row for row in items if isinstance(row, dict)]
    return []


def _queue_config(config: dict) -> dict:
    ops = config.get("ops") if isinstance(config.get("ops"), dict) else {}
    queue = ops.get("queue") if isinstance(ops.get("queue"), dict) else {}
    return queue


def _task_priority(mode: str) -> int:
    return 10 if mode == "incremental" else 20


def _task_source_item(row: dict) -> dict[str, object]:
    return {
        "guid": _row_source_item_key(row),
        "fileTitle": _row_title(row),
        "fileName": row.get("fileName"),
        "fileSize": _row_file_size(row),
        "filePath": _row_file_path(row),
        "pubDate": _row_publish_datetime(row),
    }


def _task_row(task: CollectionTask) -> dict:
    override = task.config_override or {}
    row = override.get("source_item")
    if not isinstance(row, dict):
        raise ValueError("CHONGQING_TASK_SOURCE_ITEM_MISSING")
    return row


def _row_title(row: dict) -> str:
    for key in ("title", "Title", "name", "fileTitle", "periodName"):
        value = row.get(key)
        if value:
            return str(value)
    raise ValueError("CHONGQING_PDF_TITLE_MISSING")


def _row_file_path(row: dict) -> str:
    for key in ("filePath", "FilePath", "path", "file_path"):
        value = row.get(key)
        if value:
            return str(value)
    raise ValueError("CHONGQING_PDF_FILE_PATH_MISSING")


def _row_source_item_key(row: dict) -> str | None:
    value = row.get("guid") or row.get("Guid") or row.get("id") or row.get("ID")
    return str(value) if value else None


def _row_file_size(row: dict) -> str | None:
    value = row.get("fileSize") or row.get("FileSize")
    return str(value) if value is not None else None


def _row_publish_datetime(row: dict) -> str | None:
    value = row.get("pubDate") or row.get("publishDate") or row.get("PublishDate")
    return str(value) if value else None


def _row_publish_date(row: dict) -> str | None:
    value = _row_publish_datetime(row)
    return value[:10] if value and len(value) >= 10 else None


def _pdf_attachment_from_file_path(file_path: str, download_base_url: str) -> tuple[str, str]:
    parts = [part for part in file_path.split("|") if part]
    pdf_storage_name = next((part for part in parts if part.lower().endswith(".pdf")), None)
    if not pdf_storage_name:
        raise ValueError("CHONGQING_PDF_STORAGE_NAME_MISSING")
    display_name = next((part for part in parts if part.lower().endswith(".pdf") and part != pdf_storage_name), None)
    file_name = display_name or pdf_storage_name
    return file_name, f"{download_base_url.rstrip('/')}/{pdf_storage_name}"


def _total_issue_no(title: str) -> int | None:
    match = re.search(r"总第(\d+)期", title)
    if not match:
        return None
    return int(match.group(1))


def _period_start_from_title(title: str) -> str | None:
    match = re.search(r"(20\d{2})年第?([一二三四五六七八九十\d]{1,3})期", title)
    if not match:
        return None
    return f"{match.group(1)}-{_issue_month(match.group(2)):02d}"


def _issue_month(raw: str) -> int:
    if raw.isdigit():
        return int(raw)
    values = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12}
    if raw in values:
        return values[raw]
    raise ValueError(f"CHONGQING_PERIOD_MONTH_INVALID: {raw}")


def _business_key_for_row(*, source_id: str, region_code: str | None, row: dict) -> str:
    title = _row_title(row)
    return build_cost_info_business_key(
        source_id=source_id,
        region_code=region_code,
        period=_period_start_from_title(title),
        title=title,
    )


def _missing_total_issue_numbers(rows: list[dict]) -> list[int]:
    numbers = sorted(number for row in rows if (number := _total_issue_no(_row_title(row))) is not None)
    if len(numbers) < 2:
        return []
    present = set(numbers)
    return [number for number in range(numbers[0], numbers[-1] + 1) if number not in present]
