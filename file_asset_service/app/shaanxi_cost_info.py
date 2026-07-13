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

SHAANXI_BASE_URL = "https://js.shaanxi.gov.cn"
SHAANXI_LIST_URL = f"{SHAANXI_BASE_URL}/sy/yw/zjglfw/zjxx/"
SHAANXI_COST_INFO_PARSER_VERSION = "shaanxi.zjt-province-material-info-price-monthly-pdf.v1"
SHAANXI_TITLE_REGEX = r"陕西工程造价信息(20\d{2})年(0?[1-9]|1[0-2])月材料信息价"
SHAANXI_SOURCE_TITLE_REGEX = r"^(20\d{2})年(0?[1-9]|1[0-2])月材料信息价$"


class ShaanxiCostInfoClient(Protocol):
    def get_text(self, url: str) -> str:
        ...

    def get_bytes(self, url: str) -> tuple[bytes, str | None]:
        ...


def shaanxi_cost_info_source_config() -> dict:
    parser = {
        "scope": "shaanxi_zjt_province_material_info_price_monthly_pdf",
        "list_url": SHAANXI_LIST_URL,
        "list_strategy": "static_html_paginated_article_list",
        "detail_strategy": "article_detail_pdf_anchor",
        "page_count_observed": 5,
        "period": {
            "kind": "monthly",
            "source": "normalized_title",
            "regex": SHAANXI_TITLE_REGEX,
        },
        "source_title_regex": SHAANXI_SOURCE_TITLE_REGEX,
        "title_regex": SHAANXI_TITLE_REGEX,
        "excluded_title_prefixes": ["设区市造价信息"],
        "semantic_basis": [
            "PDF总说明写明《陕西工程造价信息》材料信息价由陕西省建设工程造价服务中心按月发布。",
            "总说明写明材料信息价可用于编制工程概预算、最高投标限价等，具备 guidance 口径。",
            "同一 PDF 同时刊载生产、销售企业报价；未文档级分离，L0 不把整本伪标为 guidance。",
        ],
        "attachments": {
            "allowed": ["pdf"],
            "required_any": ["pdf"],
            "source": "detail_page_pdf_anchor",
        },
    }
    return {
        "registry_schema_version": "source_registry.v1",
        "stable": {
            "site_id": "cost_info.sn.province.material_info_price",
            "domain_type": "cost_info",
            "region_code": "610000",
            "coverage_region_code": "610000",
            "producer": "陕西省建设工程造价服务中心",
            "publisher": "陕西省建设工程造价服务中心",
            "publisher_scope": "province",
            "publisher_region_code": "610000",
            "publisher_name": "陕西省建设工程造价服务中心",
            "publisher_type": "cost_station_public_institution",
            "period_kind": "monthly",
            "entry_url": SHAANXI_LIST_URL,
            "periodicity": {"unit": "month", "period_key_format": "YYYY-MM"},
        },
        "price_coordinates": {
            "price_source_type": "info_price",
            "price_kind": "unspecified",
            "price_kind_basis": (
                "《陕西工程造价信息》材料信息价可用于工程概预算、最高投标限价，"
                "但同一 PDF 同时含生产、销售企业报价/厂商报价；未文档级分离，"
                "L0 不读内容拆分，价格行分类后置。"
            ),
            "tax_type": None,
        },
        "source_shape": {
            "publication_mode": "DETAIL_PAGE_ATTACHMENT",
            "source_attachment_mode": "pdf_attachment",
            "parsability": "mixed_text_pdf",
        },
        "parser": {
            "active_parser_version": SHAANXI_COST_INFO_PARSER_VERSION,
            "parsers": {
                SHAANXI_COST_INFO_PARSER_VERSION: parser,
            },
        },
        "coverage_expectation": {
            "target_regions": [
                {
                    "region_code": "610000",
                    "region_name": "陕西省",
                    "target_level": "province",
                    "requires_city_source": False,
                    "source_completeness_status": "province_source_only",
                    "source_audit_status": "official_cost_station_monthly_material_info_price",
                    "coverage_note": "陕西住建厅造价信息栏目混合省刊与设区市期刊；本源仅采省造价中心裸标题“YYYY年M月材料信息价”PDF。",
                }
            ]
        },
        "ops": {
            "enabled": True,
            "crawl_strategy_tier": "tier1_static_paginated_detail_pdf",
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


class UrllibShaanxiCostInfoClient(SourceRegistryHttpClient):
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
            referer=SHAANXI_LIST_URL,
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


def list_shaanxi_cost_info_issues(
    client: ShaanxiCostInfoClient,
    *,
    parser: dict,
    max_pages: int | None = 5,
    max_items: int | None = 5,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    page_limit = max_pages if max_pages is not None else int(parser.get("page_count_observed") or 5)
    for page in range(1, page_limit + 1):
        html = client.get_text(_page_url(page, parser=parser))
        page_rows = _extract_list_rows(html)
        if not page_rows:
            break
        for raw in page_rows:
            row = _normalize_list_row(raw, parser=parser)
            if row is None:
                continue
            key = str(row["source_item_key"])
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    rows.sort(key=lambda row: (int(row["period_year"]), int(row["period_month"])), reverse=True)
    if max_items is not None:
        return rows[:max_items]
    return rows


def ingest_shaanxi_cost_info_issue(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: ShaanxiCostInfoClient,
    row: dict,
    actor_id: str = "shaanxi-cost-info-crawler",
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
        raise ValueError("SHAANXI_COST_INFO_ATTACHMENTS_MISSING")

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
            "period_year": row["period_year"],
            "period_month": row["period_month"],
            "period_kind": stable.get("period_kind") or "monthly",
            "price_kind": price_coordinates.get("price_kind") or "unspecified",
            "price_kind_basis": price_coordinates.get("price_kind_basis"),
            "price_source_type": price_coordinates.get("price_source_type") or "info_price",
            "tax_type": price_coordinates.get("tax_type"),
            "coverage_region_code": stable.get("coverage_region_code") or source.region_code,
            "publisher_type": stable.get("publisher_type") or "cost_station_public_institution",
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


def run_shaanxi_cost_info_incremental(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: ShaanxiCostInfoClient | None = None,
    max_pages: int | None = 5,
    max_items: int | None = 5,
    actor_id: str = "shaanxi-cost-info-crawler",
) -> dict[str, object]:
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or shaanxi_cost_info_source_config()
    parser = config["parser"]["parsers"][SHAANXI_COST_INFO_PARSER_VERSION]
    active_client = client or UrllibShaanxiCostInfoClient()
    rows = list_shaanxi_cost_info_issues(active_client, parser=parser, max_pages=max_pages, max_items=max_items)
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
        ingest_shaanxi_cost_info_issue(
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
    list_url = str(parser.get("list_url") or SHAANXI_LIST_URL)
    if page <= 1:
        return list_url
    return urljoin(list_url, f"index_{page - 1}.html")


def _extract_list_rows(html: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    pattern = (
        r"<li\b[^>]*class=(?P<cquote>['\"])[^'\"]*clearfix[^'\"]*(?P=cquote)[^>]*>\s*"
        r"<a\b[^>]*href=(?P<hquote>['\"])(?P<href>[^'\"]+)(?P=hquote)[^>]*"
        r"title=(?P<tquote>['\"])(?P<title>.*?)(?P=tquote)[^>]*>.*?</a>\s*"
        r"<span[^>]*>(?P<date>[^<]+)</span>"
    )
    for match in re.finditer(pattern, html, flags=re.IGNORECASE | re.DOTALL):
        rows.append(
            {
                "href": unescape(match.group("href")).strip(),
                "title": _normalize_text(match.group("title")),
                "publish_date": _normalize_text(match.group("date")),
            }
        )
    return rows


def _normalize_list_row(raw: dict[str, str], *, parser: dict) -> dict[str, object] | None:
    source_title = raw["title"]
    if any(source_title.startswith(prefix) for prefix in parser.get("excluded_title_prefixes") or []):
        return None
    match = re.search(str(parser.get("source_title_regex") or SHAANXI_SOURCE_TITLE_REGEX), source_title)
    if not match:
        return None
    year = int(match.group(1))
    month = int(match.group(2))
    detail_url = urljoin(SHAANXI_LIST_URL, raw["href"])
    source_item_key = _article_id(detail_url)
    if not source_item_key:
        return None
    return {
        "source_item_key": source_item_key,
        "title": f"陕西工程造价信息{year}年{month}月材料信息价",
        "source_title": source_title,
        "period": f"{year:04d}-{month:02d}",
        "period_year": year,
        "period_month": month,
        "publish_date": raw["publish_date"],
        "detail_url": detail_url,
    }


def _article_id(detail_url: str) -> str | None:
    match = re.search(r"t\d+_(\d+)\.html", detail_url)
    return match.group(1) if match else None


def _extract_detail_pdf_attachments(html: str, detail_url: str) -> list[dict[str, object]]:
    attachments: list[dict[str, object]] = []
    for match in re.finditer(
        r"<a\b(?P<attrs>[^>]*href=(?P<quote>['\"])(?P<href>[^'\"]+\.pdf(?:\?[^'\"]*)?)(?P=quote)[^>]*)>(?P<body>.*?)</a>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        attrs = match.group("attrs")
        if "appendix" not in attrs and "download" not in attrs:
            continue
        href = unescape(match.group("href")).strip()
        file_name = _pdf_file_name(attrs, match.group("body"), href)
        if not file_name:
            continue
        attachments.append(
            {
                "file_name": file_name,
                "url": urljoin(detail_url, href),
                "discovery_method": "detail_page_pdf_anchor",
            }
        )
    return attachments


def _pdf_file_name(anchor_attrs: str, anchor_body: str, href: str) -> str | None:
    for attr in ("download", "title"):
        match = re.search(rf"{attr}=(?P<quote>['\"])(?P<value>[^'\"]+\.pdf)(?P=quote)", anchor_attrs, flags=re.I)
        if match:
            return unescape(match.group("value")).strip()
    visible = _normalize_visible_text(anchor_body)
    if visible.lower().endswith(".pdf"):
        return visible
    match = re.search(r"/([^/]+\.pdf)(?:\?|$)", href, flags=re.IGNORECASE)
    return match.group(1) if match else None


def _normalize_visible_text(html: str) -> str:
    return _normalize_text(re.sub(r"<[^>]+>", "", html))


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _ordered_unique(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
