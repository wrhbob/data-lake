from __future__ import annotations

from datetime import UTC, datetime
from html import unescape
import os
import re
from typing import Protocol
from urllib.parse import unquote, urljoin, urlsplit

from sqlalchemy.orm import Session

from app.archive_rules import build_cost_info_business_key
from app.source_adapter import SourceRegistryHttpClient, archive_business_key_exists, require_data_source
from app.source_registry import CostInfoAttachment, CostInfoDiscoveredItem, ingest_cost_info_registry_item
from app.storage import ObjectStore

HAINAN_BASE_URL = "https://zjt.hainan.gov.cn"
HAINAN_LIST_URL = f"{HAINAN_BASE_URL}/szjt/dejgxx/dezlist.shtml"
HAINAN_COST_INFO_PARSER_VERSION = "hainan.zjt-guidance-pdf-list.v1"
HAINAN_MONTHLY_TITLE_REGEX = r"(?P<year>20\d{2})年(?P<month>0?[1-9]|1[0-2])月.*海南省建设工程.*市场参考价"


class HainanCostInfoClient(Protocol):
    def get_text(self, url: str) -> str:
        ...

    def get_bytes(self, url: str) -> tuple[bytes, str | None]:
        ...


def hainan_cost_info_source_config() -> dict:
    parser = {
        "scope": "hainan_zjt_guidance_pdf_list",
        "list_url": HAINAN_LIST_URL,
        "list_strategy": "static_html_paginated_detail_pdf",
        "detail_strategy": "ucap_content_pdf_anchor",
        "period": {
            "kind": "monthly",
            "source": "title",
            "regex": HAINAN_MONTHLY_TITLE_REGEX,
        },
        "title_regex": HAINAN_MONTHLY_TITLE_REGEX,
        "title_required_keywords": ["海南省建设工程", "市场参考价"],
        "pagination": {
            "url_template": f"{HAINAN_BASE_URL}/szjt/dejgxx/dezlist_{{page}}.shtml",
            "max_pages": 10,
        },
        "attachments": {
            "allowed": ["pdf"],
            "required_any": ["pdf"],
            "source": "detail_page_pdf_anchor",
        },
    }
    return {
        "registry_schema_version": "source_registry.v1",
        "stable": {
            "site_id": "cost_info.hi.zjt.guidance",
            "domain_type": "cost_info",
            "region_code": "460000",
            "coverage_region_code": "460000",
            "producer": "海南省住房和城乡建设厅",
            "publisher": "海南省住房和城乡建设厅",
            "publisher_scope": "province",
            "publisher_region_code": "460000",
            "publisher_name": "海南省住房和城乡建设厅",
            "publisher_type": "official_housing_urban_rural_development",
            "period_kind": "monthly",
            "entry_url": HAINAN_LIST_URL,
            "periodicity": {"unit": "month", "period_key_format": "YYYY-MM"},
        },
        "price_coordinates": {
            "price_source_type": "info_price",
            "price_kind": "guidance",
            "tax_type": None,
        },
        "source_shape": {
            "publication_mode": "DETAIL_PAGE_ATTACHMENT",
            "source_attachment_mode": "pdf_attachment",
            "parsability": "text_pdf",
        },
        "parser": {
            "active_parser_version": HAINAN_COST_INFO_PARSER_VERSION,
            "parsers": {
                HAINAN_COST_INFO_PARSER_VERSION: parser,
            },
        },
        "coverage_expectation": {
            "target_regions": [
                {
                    "region_code": "460000",
                    "region_name": "海南省",
                    "target_level": "province",
                    "requires_city_source": False,
                    "source_completeness_status": "province_source_only",
                    "source_audit_status": "official_guidance_source",
                    "coverage_note": "海南住建厅价格信息栏目标题为市场参考价，但编制说明自证为信息价；按 guidance 入湖。",
                }
            ]
        },
        "ops": {
            "enabled": True,
            "crawl_strategy_tier": "tier1_static_html_detail_pdf",
            "schedule": {"frequency": "monthly"},
            "queue": {
                "max_concurrent_per_host": 1,
                "global_priority": 30,
                "min_delay_seconds": 5,
                "jitter_seconds": 3,
                "retry_backoff": "exponential",
                "dead_letter_handoff": "017_manual_recovery",
            },
            "gated": False,
            "alert_thresholds": {"missing_period_count": 1},
            "http": {
                "user_agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                )
            },
        },
    }


class UrllibHainanCostInfoClient(SourceRegistryHttpClient):
    def __init__(
        self,
        *,
        timeout: int = 60,
        min_interval_seconds: float = 5,
        jitter_seconds: float = 3,
        read_timeout_seconds: float | None = 60,
        read_total_timeout_seconds: float | None = 180,
    ) -> None:
        super().__init__(
            referer=HAINAN_LIST_URL,
            timeout=timeout,
            min_interval_seconds=min_interval_seconds,
            jitter_seconds=jitter_seconds,
            read_timeout_seconds=read_timeout_seconds,
            read_total_timeout_seconds=read_total_timeout_seconds,
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
        )


def list_hainan_cost_info_issues(
    client: HainanCostInfoClient,
    *,
    parser: dict,
    page: int = 1,
) -> list[dict[str, object]]:
    list_url = _list_url_for_page(parser, page)
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw_row in _extract_anchor_rows(client.get_text(list_url)):
        row = _normalize_list_row(raw_row)
        if row is None:
            continue
        key = str(row["source_item_key"])
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    rows.sort(key=lambda row: str(row["period"]), reverse=True)
    return rows


def ingest_hainan_cost_info_issue(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: HainanCostInfoClient,
    row: dict,
    actor_id: str = "hainan-cost-info-crawler",
    task_id: str | None = None,
):
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or {}
    stable = config.get("stable") if isinstance(config.get("stable"), dict) else {}
    price_coordinates = config.get("price_coordinates") if isinstance(config.get("price_coordinates"), dict) else {}
    source_shape = config.get("source_shape") if isinstance(config.get("source_shape"), dict) else {}
    detail_html = client.get_text(str(row["detail_url"]))
    detail_attachments = _extract_detail_pdf_attachments(detail_html, str(row["detail_url"]))
    downloaded: list[CostInfoAttachment] = []
    for attachment in detail_attachments:
        content, content_type = client.get_bytes(str(attachment["url"]))
        if not content:
            continue
        downloaded.append(
            CostInfoAttachment(
                file_name=str(attachment["file_name"]),
                content=content,
                url=str(attachment["url"]),
                content_type=content_type,
            )
        )
    if not downloaded:
        raise ValueError("HAINAN_COST_INFO_ATTACHMENTS_MISSING")

    item = CostInfoDiscoveredItem(
        title=str(row["title"]),
        publish_date=str(row["publish_date"]) if row.get("publish_date") else None,
        detail_url=str(row["detail_url"]),
        discovered_at=str(row["publish_date"]) if row.get("publish_date") else None,
        fetched_at=datetime.now(UTC).isoformat(),
        attachments=downloaded,
        metadata={
            "source_item_key": row["source_item_key"],
            "source_item_id": row["source_item_key"],
            "listed_title": row["title"],
            "period_kind": stable.get("period_kind") or "monthly",
            "price_kind": price_coordinates.get("price_kind") or "guidance",
            "price_source_type": price_coordinates.get("price_source_type") or "info_price",
            "tax_type": price_coordinates.get("tax_type"),
            "coverage_region_code": stable.get("coverage_region_code") or source.region_code,
            "publisher_type": stable.get("publisher_type") or "official_housing_urban_rural_development",
            "source_attachment_mode": source_shape.get("source_attachment_mode") or "pdf_attachment",
            "parsability": source_shape.get("parsability") or "text_pdf",
            "publication_mode": source_shape.get("publication_mode") or "DETAIL_PAGE_ATTACHMENT",
            "attachment_discovery_methods": _ordered_unique(
                str(attachment.get("discovery_method") or "unknown") for attachment in detail_attachments
            ),
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


def run_hainan_cost_info_incremental(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: HainanCostInfoClient | None = None,
    max_pages: int = 1,
    max_items: int | None = 3,
    actor_id: str = "hainan-cost-info-crawler",
) -> dict[str, object]:
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or hainan_cost_info_source_config()
    parser = config["parser"]["parsers"][HAINAN_COST_INFO_PARSER_VERSION]
    active_client = client or UrllibHainanCostInfoClient()
    ingested = 0
    skipped_existing = 0
    cursor_source_item_key: str | None = None
    for page in range(1, max_pages + 1):
        rows = list_hainan_cost_info_issues(active_client, parser=parser, page=page)
        if not rows:
            break
        for row in rows:
            if cursor_source_item_key is None:
                cursor_source_item_key = str(row["source_item_key"])
            business_key = build_cost_info_business_key(
                source_id=source.source_id,
                region_code=source.region_code,
                period=str(row["period"]),
                title=str(row["title"]),
            )
            if archive_business_key_exists(
                session,
                tenant_code=source.asset_tenant_code,
                domain_type="cost_info",
                business_key=business_key,
            ):
                skipped_existing += 1
                continue
            ingest_hainan_cost_info_issue(
                session,
                storage,
                source_id=source_id,
                client=active_client,
                row=row,
                actor_id=actor_id,
            )
            ingested += 1
            if max_items is not None and ingested >= max_items:
                return {
                    "source_id": source_id,
                    "ingested_count": ingested,
                    "skipped_existing_count": skipped_existing,
                    "cursor_source_item_key": cursor_source_item_key,
                }
    return {
        "source_id": source_id,
        "ingested_count": ingested,
        "skipped_existing_count": skipped_existing,
        "cursor_source_item_key": cursor_source_item_key,
    }


def _list_url_for_page(parser: dict, page: int) -> str:
    if page <= 1:
        return str(parser["list_url"])
    pagination = parser.get("pagination") if isinstance(parser.get("pagination"), dict) else {}
    template = str(pagination.get("url_template") or "")
    if not template:
        return str(parser["list_url"])
    return template.format(page=page)


def _extract_anchor_rows(html: str) -> list[dict[str, str | None]]:
    rows: list[dict[str, str | None]] = []
    segments = list(re.finditer(r"<li\b[^>]*>(?P<body>.*?)</li>", html, flags=re.IGNORECASE | re.DOTALL))
    segments.extend(re.finditer(r"<tr\b[^>]*>(?P<body>.*?)</tr>", html, flags=re.IGNORECASE | re.DOTALL))
    for row_match in segments:
        row_html = row_match.group("body")
        anchor_match = re.search(
            r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>",
            row_html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not anchor_match:
            continue
        href = _attr(anchor_match.group("attrs"), "href")
        if not href:
            continue
        title = _attr(anchor_match.group("attrs"), "title") or _strip_tags(anchor_match.group("body"))
        date_match = re.search(r"20\d{2}-\d{2}-\d{2}", row_html)
        rows.append(
            {
                "href": unescape(href).strip(),
                "title": _normalize_text(title),
                "publish_date": date_match.group(0) if date_match else None,
            }
        )
    return rows


def _normalize_list_row(row: dict[str, str | None]) -> dict[str, object] | None:
    title = _normalize_text(str(row.get("title") or ""))
    if not all(keyword in title for keyword in ("海南省建设工程", "市场参考价")):
        return None
    match = re.search(HAINAN_MONTHLY_TITLE_REGEX, title)
    if not match:
        return None
    href = str(row.get("href") or "").strip()
    if not href or href.startswith("javascript:"):
        return None
    detail_url = urljoin(HAINAN_LIST_URL, href)
    month = int(match.group("month"))
    period = f"{int(match.group('year')):04d}-{month:02d}"
    return {
        "source_item_key": _source_item_key_from_url(detail_url),
        "title": title,
        "period": period,
        "period_year": int(match.group("year")),
        "period_month": month,
        "publish_date": row.get("publish_date"),
        "detail_url": detail_url,
    }


def _extract_detail_pdf_attachments(html: str, detail_url: str) -> list[dict[str, str]]:
    attachments: list[dict[str, str]] = []
    seen: set[str] = set()
    for anchor_match in re.finditer(
        r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        href = _attr(anchor_match.group("attrs"), "href")
        if not href or ".pdf" not in href.lower():
            continue
        url = urljoin(detail_url, unescape(href).strip())
        if url in seen:
            continue
        seen.add(url)
        label = _normalize_text(_strip_tags(anchor_match.group("body")))
        file_name = _file_name_from_url(url, fallback=f"{label or '海南市场参考价'}.pdf")
        attachments.append(
            {
                "url": url,
                "file_name": file_name,
                "discovery_method": "a",
            }
        )
    return attachments


def _source_item_key_from_url(url: str) -> str:
    parsed = urlsplit(url)
    return unquote(parsed.path.lstrip("/"))


def _file_name_from_url(url: str, *, fallback: str) -> str:
    name = unquote(os.path.basename(urlsplit(url).path))
    if name and "." in name:
        return name
    fallback = _normalize_text(fallback).replace("/", "-")
    return fallback if fallback.lower().endswith(".pdf") else f"{fallback}.pdf"


def _attr(attrs: str, name: str) -> str | None:
    match = re.search(rf"""{name}\s*=\s*(['"])(?P<value>.*?)\1""", attrs, flags=re.IGNORECASE | re.DOTALL)
    return unescape(match.group("value")) if match else None


def _strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", unescape(text)).strip()


def _ordered_unique(values) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered
