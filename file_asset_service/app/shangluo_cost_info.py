from __future__ import annotations

from datetime import UTC, datetime
from html import unescape
import re
from typing import Protocol
from urllib.parse import urljoin

from sqlalchemy.orm import Session

from app.archive_rules import build_cost_info_business_key
from app.source_adapter import SourceRegistryHttpClient, archive_business_key_exists, require_data_source
from app.source_registry import CostInfoAttachment, CostInfoDiscoveredItem, ingest_cost_info_registry_item
from app.storage import ObjectStore

SHANGLUO_BASE_URL = "https://www.shangluo.gov.cn"
SHANGLUO_LIST_URL = f"{SHANGLUO_BASE_URL}/zjj/index/tzgg.htm"
SHANGLUO_COST_INFO_PARSER_VERSION = "shangluo.zjj-visual-sitebuilder-cost-journal-pdf.v1"
SHANGLUO_TITLE_REGEX = r"商洛工程造价管理信息（(?P<period>(?P<year>20\d{2})年第(?P<issue>\d{1,2})期)）"


class ShangluoCostInfoClient(Protocol):
    def get_text(self, url: str) -> str:
        ...

    def get_bytes(self, url: str) -> tuple[bytes, str | None]:
        ...


def shangluo_cost_info_source_config() -> dict:
    parser = {
        "adapter_kind": "shangluo_pdf",
        "scope": "shangluo_zjj_visual_sitebuilder_cost_journal_pdf",
        "list_url": SHANGLUO_LIST_URL,
        "list_strategy": "visual_sitebuilder_paginated_notice_list",
        "detail_strategy": "visual_sitebuilder_virtual_attach_pdf",
        "page_count_observed": 7,
        "max_pages_per_run": 2,
        "period": {
            "kind": "issue_based",
            "source": "title",
            "regex": SHANGLUO_TITLE_REGEX,
        },
        "title_regex": SHANGLUO_TITLE_REGEX,
        "title_keywords_all": ["商洛工程造价管理信息"],
        "excluded_title_keywords": ["公租房", "保障性租赁住房", "名单公示", "物业服务"],
        "semantic_basis": [
            "商洛市住建局通知公告栏目连续发布《商洛工程造价管理信息》PDF。",
            "该 PDF 为工程造价管理信息综合期刊，包含信息价/造价管理内容；未文档级分离价格类型。",
            "本 adapter 只做 L0 原 PDF 归档，不解析 PDF 内部价格表。",
        ],
        "attachments": {
            "allowed": ["pdf"],
            "required_any": ["pdf"],
            "source": "detail_page_virtual_attach_pdf_anchor",
        },
    }
    return {
        "registry_schema_version": "source_registry.v1",
        "stable": {
            "site_id": "cost_info.sn.shangluo.zjj.cost_journal",
            "domain_type": "cost_info",
            "region_code": "611000",
            "coverage_region_code": "611000",
            "province": "陕西省",
            "city": "商洛市",
            "producer": "商洛市住房和城乡建设局",
            "publisher": "商洛市住房和城乡建设局",
            "publisher_scope": "city",
            "publisher_region_code": "611000",
            "publisher_name": "商洛市住房和城乡建设局",
            "publisher_type": "official_housing_urban_rural_development",
            "period_kind": "issue_based",
            "entry_url": SHANGLUO_LIST_URL,
            "periodicity": {"unit": "issue", "period_key_format": "YYYY年第N期"},
        },
        "price_coordinates": {
            "price_source_type": "info_price",
            "price_kind": "unspecified",
            "price_kind_basis": (
                "《商洛工程造价管理信息》为综合期刊，可能包含信息价、造价管理等内容；"
                "未文档级分离，L0 不读内容拆分，价格行分类后置。"
            ),
            "tax_type": None,
        },
        "source_shape": {
            "publication_mode": "DETAIL_PAGE_ATTACHMENT",
            "source_attachment_mode": "pdf_attachment",
            "parsability": "mixed_text_pdf",
        },
        "parser": {
            "active_parser_version": SHANGLUO_COST_INFO_PARSER_VERSION,
            "parsers": {
                SHANGLUO_COST_INFO_PARSER_VERSION: parser,
            },
        },
        "coverage_expectation": {
            "target_regions": [
                {
                    "region_code": "611000",
                    "region_name": "商洛市",
                    "target_level": "city",
                    "requires_city_source": False,
                    "source_completeness_status": "city_source_only",
                    "source_audit_status": "official_city_zjj_issue_based_cost_journal_pdf",
                    "coverage_note": "商洛市住建局通知公告栏目；本源仅采《商洛工程造价管理信息》期号制 PDF，公租房等相邻公告不混入。",
                }
            ]
        },
        "ops": {
            "enabled": True,
            "crawl_strategy_tier": "tier1_static_detail_pdf",
            "schedule": {"frequency": "weekly"},
            "queue": {
                "max_concurrent_per_host": 1,
                "global_priority": 30,
                "min_delay_seconds": 5,
                "jitter_seconds": 3,
                "max_items_per_run": 5,
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


class UrllibShangluoCostInfoClient(SourceRegistryHttpClient):
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
            referer=SHANGLUO_LIST_URL,
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


def list_shangluo_cost_info_issues(
    client: ShangluoCostInfoClient,
    *,
    parser: dict,
    max_pages: int | None = None,
    max_items: int | None = 5,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    page_limit = max_pages if max_pages is not None else int(parser.get("max_pages_per_run") or 1)
    for page in range(1, page_limit + 1):
        page_url = _page_url(page, parser=parser)
        html = client.get_text(page_url)
        page_rows = _extract_list_rows(html)
        if not page_rows:
            break
        for raw in page_rows:
            row = _normalize_list_row(raw, parser=parser, page_url=page_url)
            if row is None:
                continue
            key = str(row["source_item_key"])
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    rows.sort(key=lambda row: (int(row["period_year"]), int(row["period_issue_no"])), reverse=True)
    if max_items is not None:
        return rows[:max_items]
    return rows


def ingest_shangluo_cost_info_issue(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: ShangluoCostInfoClient,
    row: dict,
    actor_id: str = "shangluo-cost-info-crawler",
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
                content_type=content_type or "application/pdf",
            )
        )
    if not downloaded:
        raise ValueError("SHANGLUO_COST_INFO_ATTACHMENTS_MISSING")

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
            "source_title": row["source_title"],
            "listed_title": row["source_title"],
            "period": row["period"],
            "period_raw": row["period"],
            "period_year": row["period_year"],
            "period_issue_no": row["period_issue_no"],
            "period_kind": stable.get("period_kind") or "issue_based",
            "price_kind": price_coordinates.get("price_kind") or "unspecified",
            "price_kind_basis": price_coordinates.get("price_kind_basis"),
            "price_source_type": price_coordinates.get("price_source_type") or "info_price",
            "tax_type": price_coordinates.get("tax_type"),
            "coverage_region_code": stable.get("coverage_region_code") or source.region_code,
            "producer": stable.get("producer"),
            "publisher": stable.get("publisher"),
            "publisher_scope": stable.get("publisher_scope") or "city",
            "publisher_type": stable.get("publisher_type") or "official_housing_urban_rural_development",
            "publisher_region_code": stable.get("publisher_region_code") or source.region_code,
            "source_attachment_mode": source_shape.get("source_attachment_mode") or "pdf_attachment",
            "parsability": source_shape.get("parsability") or "mixed_text_pdf",
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


def run_shangluo_cost_info_incremental(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: ShangluoCostInfoClient | None = None,
    max_pages: int | None = None,
    max_items: int | None = 5,
    actor_id: str = "shangluo-cost-info-crawler",
) -> dict[str, object]:
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or shangluo_cost_info_source_config()
    parser = config["parser"]["parsers"][SHANGLUO_COST_INFO_PARSER_VERSION]
    active_client = client or UrllibShangluoCostInfoClient()
    rows = list_shangluo_cost_info_issues(active_client, parser=parser, max_pages=max_pages, max_items=max_items)
    ingested = 0
    skipped_existing = 0
    cursor_source_item_key = str(rows[0]["source_item_key"]) if rows else None
    for row in rows:
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
        ingest_shangluo_cost_info_issue(
            session,
            storage,
            source_id=source_id,
            client=active_client,
            row=row,
            actor_id=actor_id,
        )
        ingested += 1
        if max_items is not None and ingested >= max_items:
            break
    return {
        "source_id": source_id,
        "listed_count": len(rows),
        "ingested_count": ingested,
        "skipped_existing_count": skipped_existing,
        "cursor_source_item_key": cursor_source_item_key,
    }


def _page_url(page: int, *, parser: dict) -> str:
    list_url = str(parser.get("list_url") or SHANGLUO_LIST_URL)
    if page <= 1:
        return list_url
    page_count = int(parser.get("page_count_observed") or 1)
    if page_count <= 1:
        return list_url
    page_suffix = max(page_count - page + 1, 1)
    return urljoin(list_url, f"tzgg/{page_suffix}.htm")


def _extract_list_rows(html: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    pattern = (
        r"<li\b[^>]*>\s*"
        r"<span\b[^>]*>(?P<date>20\d{2}-\d{1,2}-\d{1,2})</span>\s*"
        r"<a\b(?P<attrs>[^>]*href=(?P<hquote>['\"])(?P<href>[^'\"]+)(?P=hquote)[^>]*)>"
        r"(?P<body>.*?)</a>"
    )
    for match in re.finditer(pattern, html, flags=re.IGNORECASE | re.DOTALL):
        rows.append(
            {
                "href": unescape(match.group("href")).strip(),
                "title": _title_from_anchor(match.group("attrs"), match.group("body")),
                "publish_date": _normalize_text(match.group("date")),
            }
        )
    return rows


def _title_from_anchor(attrs: str, body: str) -> str:
    title_match = re.search(r"title=(?P<quote>['\"])(?P<title>.*?)(?P=quote)", attrs, flags=re.IGNORECASE | re.DOTALL)
    if title_match:
        title = _normalize_text(title_match.group("title"))
        if title:
            return title
    return _normalize_visible_text(body)


def _normalize_list_row(raw: dict[str, str], *, parser: dict, page_url: str) -> dict[str, object] | None:
    source_title = raw["title"]
    if not _looks_like_shangluo_issue(source_title, parser=parser):
        return None
    period = _extract_issue_period(source_title, parser=parser)
    if period is None:
        return None
    year, issue_no = period
    detail_url = urljoin(page_url, raw["href"])
    source_item_key = _article_id(detail_url)
    if not source_item_key:
        return None
    return {
        "source_item_key": source_item_key,
        "title": source_title,
        "source_title": source_title,
        "period": f"{year}年第{issue_no}期",
        "period_year": year,
        "period_issue_no": issue_no,
        "publish_date": raw["publish_date"],
        "detail_url": detail_url,
    }


def _looks_like_shangluo_issue(title: str, *, parser: dict) -> bool:
    normalized = _normalize_text(title)
    if any(keyword in normalized for keyword in parser.get("excluded_title_keywords") or []):
        return False
    if not all(keyword in normalized for keyword in parser.get("title_keywords_all") or []):
        return False
    return _extract_issue_period(normalized, parser=parser) is not None


def _extract_issue_period(title: str, *, parser: dict) -> tuple[int, int] | None:
    regex = str(parser.get("title_regex") or SHANGLUO_TITLE_REGEX)
    match = re.search(regex, _normalize_text(title))
    if not match:
        return None
    return int(match.group("year")), int(match.group("issue"))


def _article_id(detail_url: str) -> str | None:
    match = re.search(r"/info/\d+/(?P<id>\d+)\.htm", detail_url)
    return match.group("id") if match else None


def _extract_detail_pdf_attachments(html: str, detail_url: str) -> list[dict[str, object]]:
    attachments: list[dict[str, object]] = []
    seen_urls: set[str] = set()
    pattern = (
        r"<a\b(?P<attrs>[^>]*href=(?P<quote>['\"])(?P<href>[^'\"]*"
        r"(?:\.pdf|[?&]e=\.pdf)[^'\"]*)(?P=quote)[^>]*)>(?P<body>.*?)</a>"
    )
    for match in re.finditer(pattern, html, flags=re.IGNORECASE | re.DOTALL):
        href = unescape(match.group("href")).strip()
        url = urljoin(detail_url, href)
        if url in seen_urls:
            continue
        seen_urls.add(url)
        attachments.append(
            {
                "file_name": _pdf_file_name(match.group("attrs"), match.group("body"), href),
                "url": url,
                "discovery_method": "detail_page_virtual_attach_pdf_anchor",
            }
        )
    return attachments


def _pdf_file_name(anchor_attrs: str, anchor_body: str, href: str) -> str:
    for attr in ("download", "title"):
        match = re.search(rf"{attr}=(?P<quote>['\"])(?P<value>[^'\"]+\.pdf)(?P=quote)", anchor_attrs, flags=re.I)
        if match:
            return unescape(match.group("value")).strip()
    visible = _normalize_visible_text(anchor_body)
    if visible.lower().endswith(".pdf"):
        return visible
    path_match = re.search(r"/([^/?#]+\.pdf)(?:[?#]|$)", href, flags=re.IGNORECASE)
    if path_match:
        return unescape(path_match.group(1)).strip()
    return "商洛工程造价管理信息.pdf"


def _normalize_visible_text(html: str) -> str:
    return _normalize_text(re.sub(r"<[^>]+>", "", html))


def _normalize_text(text: object) -> str:
    return re.sub(r"\s+", " ", unescape(str(text))).strip()


def _ordered_unique(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
