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

QINGHAI_BASE_URL = "http://zjt.qinghai.gov.cn"
QINGHAI_STATIC_LIST_URL = f"{QINGHAI_BASE_URL}/html/132/List.html"
QINGHAI_DYNAMIC_LIST_URL_TEMPLATE = (
    f"{QINGHAI_BASE_URL}/webaspx/view_list.aspx?portalid=1&lmid=132&pages={{page}}"
)
QINGHAI_COST_INFO_PARSER_VERSION = "qinghai.zjt-aspnet-issue-pdf-list.v1"
QINGHAI_MAIN_JOURNAL = "青海工程造价管理信息"
QINGHAI_MARKET_JOURNAL = "青海建设工程市场价格信息"
QINGHAI_TITLE_REGEX = (
    r"(?P<period>(?P<year>20\d{2})年第(?P<issue>\d{1,2})(?:[—–-](?P<issue_end>\d{1,2}))?期)"
    r"《青海工程造价管理信息》"
)


class QinghaiCostInfoClient(Protocol):
    def get_text(self, url: str) -> str:
        ...

    def get_bytes(self, url: str) -> tuple[bytes, str | None]:
        ...


def qinghai_cost_info_source_config() -> dict:
    parser = {
        "scope": "qinghai_zjt_aspnet_issue_pdf_list",
        "list_url": QINGHAI_STATIC_LIST_URL,
        "dynamic_list_url_template": QINGHAI_DYNAMIC_LIST_URL_TEMPLATE,
        "list_strategy": "aspnet_static_or_dynamic_html",
        "period": {
            "kind": "issue_based",
            "source": "title",
            "regex": QINGHAI_TITLE_REGEX,
        },
        "title_regex": QINGHAI_TITLE_REGEX,
        "excluded_title_regexes": [
            rf"《{QINGHAI_MARKET_JOURNAL}》",
            r"通知|通报|公示|统计调查|费用标准|计价",
        ],
        "attachments": {
            "allowed": ["pdf"],
            "required_any": ["pdf"],
            "source": "list_anchor_href",
        },
    }
    return {
        "registry_schema_version": "source_registry.v1",
        "stable": {
            "site_id": "cost_info.qh.zjt.main",
            "domain_type": "cost_info",
            "region_code": "630000",
            "coverage_region_code": "630000",
            "producer": "青海省住房和城乡建设厅",
            "publisher": "青海省住房和城乡建设厅",
            "publisher_scope": "province",
            "publisher_region_code": "630000",
            "publisher_name": "青海省住房和城乡建设厅",
            "publisher_type": "official_housing_urban_rural_development",
            "period_kind": "issue_based",
            "entry_url": QINGHAI_STATIC_LIST_URL,
            "periodicity": {"unit": "issue", "period_key_format": "YYYY年第N期"},
        },
        "price_coordinates": {
            "price_source_type": "info_price",
            "price_kind": "guidance",
            "tax_type": None,
        },
        "source_shape": {
            "publication_mode": "DIRECT_PDF",
            "source_attachment_mode": "pdf_only",
            "parsability": "text_pdf",
        },
        "parser": {
            "active_parser_version": QINGHAI_COST_INFO_PARSER_VERSION,
            "parsers": {
                QINGHAI_COST_INFO_PARSER_VERSION: parser,
            },
        },
        "coverage_expectation": {
            "target_regions": [
                {
                    "region_code": "630000",
                    "region_name": "青海省",
                    "target_level": "province",
                    "requires_city_source": False,
                    "source_completeness_status": "province_source_only",
                    "source_audit_status": "official_main_source",
                    "coverage_note": "青海省住建厅造价信息栏目《青海工程造价管理信息》主刊源；同栏目市场价格刊物本轮过滤。",
                }
            ]
        },
        "ops": {
            "enabled": True,
            "crawl_strategy_tier": "tier1_aspnet_static_pdf_list",
            "schedule": {"frequency": "issue"},
            "queue": {
                "max_concurrent_per_host": 1,
                "global_priority": 30,
                "min_delay_seconds": 5,
                "jitter_seconds": 3,
                "retry_backoff": "exponential",
                "dead_letter_handoff": "017_manual_recovery",
            },
            "gated": False,
            "alert_thresholds": {"missing_issue_count": 1},
            "http": {
                "user_agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                )
            },
        },
    }


class UrllibQinghaiCostInfoClient(SourceRegistryHttpClient):
    def __init__(
        self,
        *,
        timeout: int = 60,
        min_interval_seconds: float = 5,
        jitter_seconds: float = 3,
        read_timeout_seconds: float | None = 60,
        read_total_timeout_seconds: float | None = 300,
    ) -> None:
        super().__init__(
            referer=QINGHAI_STATIC_LIST_URL,
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


def list_qinghai_cost_info_issues(
    client: QinghaiCostInfoClient,
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
    rows.sort(
        key=lambda row: (
            int(row["period_year"]),
            int(row["period_issue_no"]),
            int(row.get("period_issue_end_no") or row["period_issue_no"]),
        ),
        reverse=True,
    )
    return rows


def ingest_qinghai_cost_info_issue(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: QinghaiCostInfoClient,
    row: dict,
    actor_id: str = "qinghai-cost-info-crawler",
    task_id: str | None = None,
):
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or {}
    stable = config.get("stable") if isinstance(config.get("stable"), dict) else {}
    price_coordinates = config.get("price_coordinates") if isinstance(config.get("price_coordinates"), dict) else {}
    source_shape = config.get("source_shape") if isinstance(config.get("source_shape"), dict) else {}
    downloaded: list[CostInfoAttachment] = []
    for attachment in _row_attachments(row):
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
        raise ValueError("QINGHAI_COST_INFO_ATTACHMENTS_MISSING")

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
            "period_kind": stable.get("period_kind") or "issue_based",
            "price_kind": price_coordinates.get("price_kind") or "guidance",
            "price_source_type": price_coordinates.get("price_source_type") or "info_price",
            "tax_type": price_coordinates.get("tax_type"),
            "coverage_region_code": stable.get("coverage_region_code") or source.region_code,
            "publisher_type": stable.get("publisher_type") or "official_housing_urban_rural_development",
            "source_attachment_mode": source_shape.get("source_attachment_mode") or "pdf_only",
            "parsability": source_shape.get("parsability") or "text_pdf",
            "publication_mode": source_shape.get("publication_mode") or "DIRECT_PDF",
            "attachment_discovery_methods": _ordered_unique(
                str(attachment.get("discovery_method") or "unknown") for attachment in _row_attachments(row)
            ),
        },
    )
    if row.get("period_issue_end_no") is not None:
        item.metadata["period_issue_end_no"] = row["period_issue_end_no"]
    return ingest_cost_info_registry_item(
        session,
        storage,
        source_id=source_id,
        item=item,
        actor_id=actor_id,
        task_id=task_id,
    )


def run_qinghai_cost_info_incremental(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: QinghaiCostInfoClient | None = None,
    max_pages: int = 1,
    max_items: int | None = 3,
    actor_id: str = "qinghai-cost-info-crawler",
) -> dict[str, object]:
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or qinghai_cost_info_source_config()
    parser = config["parser"]["parsers"][QINGHAI_COST_INFO_PARSER_VERSION]
    active_client = client or UrllibQinghaiCostInfoClient()
    ingested = 0
    skipped_existing = 0
    cursor_source_item_key: str | None = None
    for page in range(1, max_pages + 1):
        rows = list_qinghai_cost_info_issues(active_client, parser=parser, page=page)
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
            ingest_qinghai_cost_info_issue(
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


def _extract_anchor_rows(html: str) -> list[dict[str, str | None]]:
    rows: list[dict[str, str | None]] = []
    segments = list(re.finditer(r"<tr\b[^>]*>(?P<body>.*?)</tr>", html, flags=re.IGNORECASE | re.DOTALL))
    segments.extend(re.finditer(r"<li\b[^>]*>(?P<body>.*?)</li>", html, flags=re.IGNORECASE | re.DOTALL))
    for row_match in segments:
        row_html = row_match.group("body")
        anchor_match = re.search(
            r"<a\b(?P<attrs>[^>]*)>(?P<body>.*?)</a>",
            row_html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not anchor_match:
            continue
        attrs = anchor_match.group("attrs")
        href = _attr(attrs, "href")
        if not href:
            continue
        title = _attr(attrs, "title") or _strip_tags(anchor_match.group("body"))
        date_match = re.search(r"20\d{2}-\d{2}-\d{2}", row_html)
        rows.append(
            {
                "href": unescape(href).strip(),
                "title": unescape(title).strip(),
                "publish_date": date_match.group(0) if date_match else None,
            }
        )
    return rows


def _list_url_for_page(parser: dict, page: int) -> str:
    if page <= 1:
        return str(parser.get("list_url") or QINGHAI_STATIC_LIST_URL)
    return urljoin(str(parser.get("list_url") or QINGHAI_STATIC_LIST_URL), f"List-{page - 1}.html")


def _normalize_list_row(row: dict[str, str | None]) -> dict[str, object] | None:
    title = str(row.get("title") or "").strip()
    match = re.search(QINGHAI_TITLE_REGEX, title)
    if not match:
        return None
    href = str(row.get("href") or "").strip()
    if not _is_pdf_url(href):
        return None
    attachment_url = urljoin(QINGHAI_BASE_URL, href)
    file_name = _file_name_from_url(attachment_url)
    issue_no = int(match.group("issue"))
    issue_end_no = int(match.group("issue_end")) if match.group("issue_end") else None
    return {
        "source_item_key": _source_item_key_from_url(attachment_url),
        "title": title,
        "period": _compact_period(match.group("period")),
        "period_year": int(match.group("year")),
        "period_issue_no": issue_no,
        "period_issue_end_no": issue_end_no,
        "publish_date": row.get("publish_date"),
        "detail_url": attachment_url,
        "attachments": [
            {
                "file_name": file_name,
                "url": attachment_url,
                "discovery_method": "list_direct_pdf",
            }
        ],
    }


def _attr(attrs: str, name: str) -> str | None:
    match = re.search(rf"""{name}\s*=\s*(?P<quote>["'])(?P<value>.*?)(?P=quote)""", attrs, flags=re.IGNORECASE)
    if match:
        return match.group("value")
    match = re.search(rf"{name}\s*=\s*(?P<value>[^\s>]+)", attrs, flags=re.IGNORECASE)
    if match:
        return match.group("value")
    return None


def _strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html).strip()


def _compact_period(period: str) -> str:
    return re.sub(r"\s+", "", period)


def _is_pdf_url(url: str) -> bool:
    return urlsplit(url).path.lower().endswith(".pdf")


def _file_name_from_url(url: str) -> str:
    return unquote(os.path.basename(urlsplit(url).path))


def _source_item_key_from_url(url: str) -> str:
    file_name = _file_name_from_url(url)
    if file_name:
        return f"files/{file_name}"
    return urlsplit(url).path.lstrip("/")


def _row_attachments(row: dict) -> list[dict[str, object]]:
    return list(row.get("attachments") or [])


def _ordered_unique(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
