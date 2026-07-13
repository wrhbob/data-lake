from __future__ import annotations

from datetime import UTC, datetime
import json
import os
import re
from typing import Protocol
from urllib.parse import unquote, urljoin, urlsplit

from sqlalchemy.orm import Session

from app.archive_rules import build_cost_info_business_key
from app.source_adapter import archive_business_key_exists, crawl_lag_days, file_extension, require_data_source
from app.source_registry import CostInfoAttachment, CostInfoDiscoveredItem, ingest_cost_info_registry_item
from app.storage import ObjectStore

ZIGONG_ENTRY_URL = "https://www.zg.gov.cn/zgsrmzf/c00145/pc/list.html"
ZIGONG_BASE_URL = "https://www.zg.gov.cn"
ZIGONG_PARSER_VERSION = "zigong.zip-list.v1"


class ZigongCostInfoClient(Protocol):
    def get_text(self, url: str) -> str:
        ...

    def get_bytes(self, url: str) -> tuple[bytes, str | None]:
        ...


def zigong_cost_info_source_config() -> dict:
    return {
        "registry_schema_version": "source_registry.v1",
        "stable": {
            "site_id": "cost_info.sc.zigong",
            "domain_type": "cost_info",
            "region_code": "510300",
            "coverage_region_code": "510300",
            "producer": "自贡市住房和城乡建设局",
            "publisher": "自贡市人民政府",
            "publisher_scope": "city",
            "publisher_region_code": "510300",
            "publisher_name": "自贡市人民政府",
            "publisher_type": "official_government_portal",
            "entry_url": ZIGONG_ENTRY_URL,
            "periodicity": {"unit": "month", "period_key_format": "YYYY-MM"},
        },
        "price_coordinates": {
            "price_source_type": "info_price",
            "tax_type": None,
        },
        "source_shape": {
            "publication_mode": "DIRECT_WEB",
            "source_attachment_mode": "zip_package",
            "parsability": "image_based",
        },
        "parser": {
            "active_parser_version": ZIGONG_PARSER_VERSION,
            "parsers": {
                ZIGONG_PARSER_VERSION: {
                    "scope": "embedded_listdata_package_only",
                    "list_url": ZIGONG_ENTRY_URL,
                    "period": {
                        "source": "title",
                        "regex": r"(20\d{2})年(\d{1,2})月",
                    },
                    "attachments": {
                        "allowed": ["zip", "rar"],
                        "opaque": True,
                    },
                }
            },
        },
        "ops": {
            "enabled": True,
            "crawl_strategy_tier": "tier1_embedded_json",
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


def list_zigong_issues(client: ZigongCostInfoClient, *, parser: dict) -> list[dict[str, object]]:
    html = client.get_text(parser["list_url"])
    rows = []
    for article in _extract_article_list(html):
        title = str(article.get("title") or "")
        if "材料价格信息" not in title:
            continue
        attachments = _attachments_from_article(article)
        if not attachments:
            continue
        rows.append(
            {
                "title": title,
                "publish_date": _publish_date(article.get("pubDate")),
                "detail_url": _detail_url(article),
                "source_item_id": str(article.get("id") or ""),
                "attachments": attachments,
            }
        )
    return rows


def ingest_zigong_issue(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: ZigongCostInfoClient,
    row: dict,
    parser: dict,
    actor_id: str = "zigong-cost-info-crawler",
    task_id: str | None = None,
):
    attachments = row.get("attachments")
    if not isinstance(attachments, list) or not attachments:
        raise ValueError("ZIGONG_ATTACHMENTS_MISSING")

    downloaded = []
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        file_name = str(attachment["file_name"])
        download_url = str(attachment["url"])
        content, content_type = client.get_bytes(download_url)
        if not content:
            raise ValueError(f"ZIGONG_ATTACHMENT_EMPTY: {file_name}")
        downloaded.append(
            CostInfoAttachment(
                file_name=file_name,
                content=content,
                url=download_url,
                content_type=content_type,
            )
        )
    if not downloaded:
        raise ValueError("ZIGONG_ATTACHMENTS_MISSING")

    item = CostInfoDiscoveredItem(
        title=_row_title(row),
        publish_date=_row_publish_date(row),
        detail_url=_row_detail_url(row),
        discovered_at=_row_publish_date(row),
        fetched_at=datetime.now(UTC).isoformat(),
        attachments=downloaded,
        metadata={
            "listed_title": _row_title(row),
            "source_item_id": _row_source_item_key(row),
            "price_source_type": "info_price",
            "tax_type": None,
            "source_attachment_mode": "zip_package",
            "parsability": "image_based",
            "opaque_package": True,
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


def run_zigong_incremental(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: ZigongCostInfoClient,
    actor_id: str = "zigong-cost-info-crawler",
    max_items: int | None = None,
    as_of_date: str | None = None,
    stop_on_existing: bool = True,
) -> dict[str, object]:
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or zigong_cost_info_source_config()
    parser = config["parser"]["parsers"][ZIGONG_PARSER_VERSION]
    rows = list_zigong_issues(client, parser=parser)
    ingested_count = 0
    skipped_existing_count = 0
    stopped_on_existing = False
    cursor_source_item_key = _row_source_item_key(rows[0]) if rows else None
    cursor_publish_date = _row_publish_date(rows[0]) if rows else None

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
        ingest_zigong_issue(
            session,
            storage,
            source_id=source.source_id,
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
    }


def _extract_article_list(html: str) -> list[dict]:
    marker = "articleList"
    marker_index = html.find(marker)
    if marker_index < 0:
        return []
    start = html.find("[", marker_index)
    if start < 0:
        return []
    end = _matching_bracket(html, start)
    if end is None:
        return []
    payload = html[start : end + 1]
    parsed = json.loads(payload)
    return [row for row in parsed if isinstance(row, dict)]


def _matching_bracket(text: str, start: int) -> int | None:
    depth = 0
    in_string = False
    quote = ""
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            continue
        if char in {"'", '"'}:
            in_string = True
            quote = char
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return index
    return None


def _attachments_from_article(article: dict) -> list[dict[str, object]]:
    files = article.get("articleFiles")
    if not isinstance(files, list):
        return []
    attachments: list[dict[str, object]] = []
    for file_info in files:
        if not isinstance(file_info, dict):
            continue
        file_name = str(file_info.get("fileName") or "")
        file_path = str(file_info.get("filePath") or "")
        if file_extension(file_name or file_path) not in {".zip", ".rar"}:
            continue
        if "价格走势" in file_name:
            continue
        attachments.append(
            {
                "file_name": file_name or _file_name_from_url(file_path),
                "url": urljoin(str(file_info.get("domainName") or ZIGONG_BASE_URL), file_path),
                "file_id": str(file_info.get("fileId") or ""),
                "total_size": file_info.get("totalSize") or None,
            }
        )
    return attachments


def _detail_url(article: dict) -> str:
    urls = article.get("urls")
    if isinstance(urls, str) and urls.strip():
        try:
            parsed = json.loads(urls)
            pc_url = parsed.get("pc") if isinstance(parsed, dict) else None
            if pc_url:
                return urljoin(ZIGONG_BASE_URL, str(pc_url))
        except json.JSONDecodeError:
            pass
    return urljoin(ZIGONG_BASE_URL, f"/zgsrmzf/c00145/pc/content/content_{article.get('id')}.html")


def _publish_date(value: object) -> str | None:
    if not value:
        return None
    return str(value)[:10]


def _file_name_from_url(url: str) -> str:
    return unquote(os.path.basename(urlsplit(url).path))


def _row_title(row: dict) -> str:
    value = row.get("title")
    if not value:
        raise ValueError("ZIGONG_TITLE_MISSING")
    return str(value)


def _row_publish_date(row: dict) -> str | None:
    value = row.get("publish_date") or row.get("publishDate")
    return str(value) if value else None


def _row_detail_url(row: dict) -> str:
    value = row.get("detail_url") or row.get("detailUrl")
    if not value:
        raise ValueError("ZIGONG_DETAIL_URL_MISSING")
    return str(value)


def _row_source_item_key(row: dict) -> str:
    value = row.get("source_item_id") or row.get("id")
    if not value:
        raise ValueError("ZIGONG_SOURCE_ITEM_ID_MISSING")
    return str(value)


def _period_start_from_title(title: str) -> str | None:
    match = re.search(r"(20\d{2})年(\d{1,2})月", title)
    if not match:
        return None
    return f"{match.group(1)}-{int(match.group(2)):02d}"


def _business_key_for_row(*, source_id: str, region_code: str | None, row: dict) -> str:
    title = _row_title(row)
    return build_cost_info_business_key(
        source_id=source_id,
        region_code=region_code,
        period=_period_start_from_title(title),
        title=title,
    )
