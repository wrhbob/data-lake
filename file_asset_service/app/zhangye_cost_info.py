from __future__ import annotations

from datetime import UTC, datetime
from html import unescape
import re
from typing import Protocol
from urllib.parse import unquote, urljoin, urlsplit

from sqlalchemy.orm import Session

from app.archive_rules import build_cost_info_business_key
from app.source_adapter import SourceRegistryHttpClient, archive_business_key_exists, require_data_source
from app.source_registry import CostInfoAttachment, CostInfoDiscoveredItem, ingest_cost_info_registry_item
from app.storage import ObjectStore

ZHANGYE_BASE_URL = "https://www.zhangye.gov.cn"
ZHANGYE_LIST_URL = f"{ZHANGYE_BASE_URL}/jsj/dzdt/tzgg/"
ZHANGYE_COST_INFO_PARSER_VERSION = "zhangye.gov-static-notice-bimonthly-price-info-zip.v1"
ZHANGYE_PERIOD_REGEX = r"(?P<period>(?P<year>20\d{2})年(?P<start_month>\d{1,2})[-—~－](?P<end_month>\d{1,2})月)"


class ZhangyeCostInfoClient(Protocol):
    def get_text(self, url: str) -> str:
        ...

    def get_bytes(self, url: str) -> tuple[bytes, str | None]:
        ...


def zhangye_cost_info_source_config() -> dict:
    parser = {
        "adapter_kind": "zhangye_zip",
        "scope": "zhangye_gov_static_notice_bimonthly_price_info_zip",
        "list_url": ZHANGYE_LIST_URL,
        "list_strategy": "static_list_detail_zip_anchor",
        "detail_strategy": "article_detail_zip_anchor",
        "period": {
            "kind": "bimonthly",
            "source": "normalized_title",
            "regex": ZHANGYE_PERIOD_REGEX,
        },
        "title_regex": ZHANGYE_PERIOD_REGEX,
        "title_keywords_all": ["张掖市", "建设工程", "信息价"],
        "excluded_title_keywords": ["公租房", "安全生产", "消防", "物业", "征集", "资质", "名单"],
        "attachments": {
            "allowed": ["zip"],
            "required_any": ["zip"],
            "source": "detail_page_zip_anchor",
            "opaque": True,
        },
        "semantic_basis": [
            "张掖市住建局通知公告详情页正文写明发布建设工程材料信息价及人工信息价。",
            "正文写明信息价供有关单位参考，作为 L0 信息价来源登记。",
            "附件为 ZIP 原包，内含当期信息价 PDF 文件；本层只归档原包，不解压、不解析价格内容。",
        ],
    }
    return {
        "registry_schema_version": "source_registry.v1",
        "stable": {
            "site_id": "cost_info.gs.zhangye.zjj.price_info",
            "domain_type": "cost_info",
            "region_code": "620700",
            "coverage_region_code": "620700",
            "province": "甘肃省",
            "city": "张掖市",
            "producer": "张掖市住房和城乡建设局",
            "publisher": "张掖市住房和城乡建设局",
            "publisher_scope": "city",
            "publisher_region_code": "620700",
            "publisher_name": "张掖市住房和城乡建设局",
            "publisher_type": "official_housing_urban_rural_development",
            "period_kind": "bimonthly",
            "entry_url": ZHANGYE_LIST_URL,
            "periodicity": {"unit": "two_months", "period_key_format": "YYYY年M-M月"},
        },
        "price_coordinates": {
            "price_source_type": "info_price",
            "price_kind": "guidance",
            "price_kind_basis": "正文写明建设工程材料信息价及人工信息价予以发布，供有关单位参考。",
            "tax_type": None,
        },
        "source_shape": {
            "publication_mode": "DETAIL_PAGE_ATTACHMENT",
            "source_attachment_mode": "zip_package",
            "parsability": "opaque_pdf_package",
        },
        "parser": {
            "active_parser_version": ZHANGYE_COST_INFO_PARSER_VERSION,
            "parsers": {
                ZHANGYE_COST_INFO_PARSER_VERSION: parser,
            },
        },
        "coverage_expectation": {
            "target_regions": [
                {
                    "region_code": "620700",
                    "region_name": "张掖市",
                    "target_level": "city",
                    "requires_city_source": False,
                    "source_completeness_status": "city_source_only",
                    "source_audit_status": "official_city_zjj_bimonthly_price_info_zip",
                    "coverage_note": "张掖市住建局通知公告混合列表；仅采标题含建设工程材料信息价及人工信息价的双月 ZIP 附件，公租房、安全生产、资质名单等相邻通知不混入。",
                }
            ]
        },
        "ops": {
            "enabled": True,
            "crawl_strategy_tier": "tier1_static_detail_zip",
            "schedule": {"frequency": "bimonthly"},
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


class UrllibZhangyeCostInfoClient(SourceRegistryHttpClient):
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
            referer=ZHANGYE_LIST_URL,
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


def list_zhangye_cost_info_issues(
    client: ZhangyeCostInfoClient,
    *,
    parser: dict,
    max_items: int | None = 5,
) -> list[dict[str, object]]:
    html = client.get_text(str(parser.get("list_url") or ZHANGYE_LIST_URL))
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw in _extract_list_rows(html):
        row = _normalize_list_row(raw, parser=parser)
        if row is None:
            continue
        key = str(row["source_item_key"])
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    rows.sort(
        key=lambda row: (int(row["period_year"]), int(row["period_start_month"]), int(row["period_end_month"])),
        reverse=True,
    )
    if max_items is not None:
        return rows[:max_items]
    return rows


def ingest_zhangye_cost_info_issue(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: ZhangyeCostInfoClient,
    row: dict,
    actor_id: str = "zhangye-cost-info-crawler",
    task_id: str | None = None,
):
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or {}
    stable = config.get("stable") if isinstance(config.get("stable"), dict) else {}
    price_coordinates = config.get("price_coordinates") if isinstance(config.get("price_coordinates"), dict) else {}
    source_shape = config.get("source_shape") if isinstance(config.get("source_shape"), dict) else {}
    detail_html = client.get_text(str(row["detail_url"]))
    detail_attachments = _extract_detail_zip_attachments(detail_html, str(row["detail_url"]))
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
                content_type=content_type or "application/zip",
            )
        )
    if not downloaded:
        raise ValueError("ZHANGYE_COST_INFO_ATTACHMENTS_MISSING")

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
            "period_start_month": row["period_start_month"],
            "period_end_month": row["period_end_month"],
            "period_start": row["period_start"],
            "period_end": row["period_end"],
            "period_kind": stable.get("period_kind") or "bimonthly",
            "price_kind": price_coordinates.get("price_kind") or "guidance",
            "price_kind_basis": price_coordinates.get("price_kind_basis"),
            "price_source_type": price_coordinates.get("price_source_type") or "info_price",
            "tax_type": price_coordinates.get("tax_type"),
            "coverage_region_code": stable.get("coverage_region_code") or source.region_code,
            "producer": stable.get("producer"),
            "publisher": stable.get("publisher"),
            "publisher_scope": stable.get("publisher_scope") or "city",
            "publisher_type": stable.get("publisher_type") or "official_housing_urban_rural_development",
            "publisher_region_code": stable.get("publisher_region_code") or source.region_code,
            "source_attachment_mode": source_shape.get("source_attachment_mode") or "zip_package",
            "parsability": source_shape.get("parsability") or "opaque_pdf_package",
            "publication_mode": source_shape.get("publication_mode") or "DETAIL_PAGE_ATTACHMENT",
            "attachment_discovery_methods": _ordered_unique(
                str(attachment.get("discovery_method") or "unknown") for attachment in detail_attachments
            ),
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


def run_zhangye_cost_info_incremental(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: ZhangyeCostInfoClient | None = None,
    max_items: int | None = 5,
    actor_id: str = "zhangye-cost-info-crawler",
) -> dict[str, object]:
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or zhangye_cost_info_source_config()
    parser = config["parser"]["parsers"][ZHANGYE_COST_INFO_PARSER_VERSION]
    active_client = client or UrllibZhangyeCostInfoClient()
    rows = list_zhangye_cost_info_issues(active_client, parser=parser, max_items=max_items)
    ingested = 0
    skipped_existing = 0
    cursor_source_item_key = str(rows[0]["source_item_key"]) if rows else None
    cursor_publish_date = str(rows[0]["publish_date"]) if rows and rows[0].get("publish_date") else None
    for row in rows:
        business_key = build_cost_info_business_key(
            source_id=source.source_id,
            region_code=source.region_code,
            period=str(row.get("period_start") or row.get("period") or ""),
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
        ingest_zhangye_cost_info_issue(
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
        "cursor_publish_date": cursor_publish_date,
    }


def _extract_list_rows(html: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for li_match in re.finditer(r"<li\b[^>]*>(?P<li>.*?)</li>", html, flags=re.IGNORECASE | re.DOTALL):
        li_html = li_match.group("li")
        anchor = re.search(
            r"<a\b[^>]*href=(?P<quote>['\"])(?P<href>[^'\"]+)(?P=quote)[^>]*>(?P<body>.*?)</a>",
            li_html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not anchor:
            continue
        body = anchor.group("body")
        date = _extract_date(body) or _extract_date(li_html)
        title_html = re.sub(r"<span\b[^>]*>.*?</span>", " ", body, flags=re.IGNORECASE | re.DOTALL)
        title = _normalize_visible_text(title_html)
        if not title:
            continue
        rows.append(
            {
                "href": unescape(anchor.group("href")).strip(),
                "title": title,
                "publish_date": date or "",
            }
        )
    return rows


def _normalize_list_row(raw: dict[str, str], *, parser: dict) -> dict[str, object] | None:
    source_title = raw["title"]
    if not _looks_like_zhangye_period(source_title, parser=parser):
        return None
    period = _extract_bimonthly_period(source_title)
    if period is None:
        return None
    year, start_month, end_month = period
    detail_url = urljoin(ZHANGYE_LIST_URL, raw["href"])
    source_item_key = _article_id(detail_url)
    if not source_item_key:
        return None
    return {
        "source_item_key": source_item_key,
        "title": f"张掖市{year}年{start_month}-{end_month}月建设工程材料信息价及人工信息价",
        "source_title": source_title,
        "period": f"{year}年{start_month}-{end_month}月",
        "period_year": year,
        "period_start_month": start_month,
        "period_end_month": end_month,
        "period_start": f"{year}-{start_month:02d}",
        "period_end": f"{year}-{end_month:02d}",
        "publish_date": raw["publish_date"],
        "detail_url": detail_url,
    }


def _looks_like_zhangye_period(title: str, *, parser: dict) -> bool:
    normalized = _normalize_title_for_match(title)
    if any(keyword in normalized for keyword in parser.get("excluded_title_keywords") or []):
        return False
    if not all(keyword in normalized for keyword in parser.get("title_keywords_all") or []):
        return False
    return _extract_bimonthly_period(normalized) is not None


def _extract_bimonthly_period(title: str) -> tuple[int, int, int] | None:
    normalized = _normalize_title_for_match(title)
    match = re.search(ZHANGYE_PERIOD_REGEX, normalized)
    if not match:
        return None
    return int(match.group("year")), int(match.group("start_month")), int(match.group("end_month"))


def _extract_detail_zip_attachments(html: str, detail_url: str) -> list[dict[str, object]]:
    attachments: list[dict[str, object]] = []
    for match in re.finditer(
        r"<a\b(?P<attrs>[^>]*href=(?P<quote>['\"])(?P<href>[^'\"]+)(?P=quote)[^>]*)>(?P<body>.*?)</a>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        href = unescape(match.group("href")).strip()
        if href.lower().startswith("javascript:"):
            continue
        file_name = _package_file_name(match.group("body"), href)
        if not file_name or not file_name.lower().endswith(".zip"):
            continue
        attachments.append(
            {
                "file_name": file_name,
                "url": urljoin(detail_url, href),
                "discovery_method": "detail_page_zip_anchor",
            }
        )
    return attachments


def _package_file_name(anchor_body: str, href: str) -> str | None:
    visible = _normalize_visible_text(anchor_body)
    if visible.lower().endswith(".zip"):
        return visible
    path_name = unquote(urlsplit(href).path.rsplit("/", 1)[-1])
    if path_name.lower().endswith(".zip"):
        return path_name
    return None


def _article_id(detail_url: str) -> str | None:
    match = re.search(r"/t20\d{6}_(?P<id>\d+)\.html", detail_url, flags=re.IGNORECASE)
    if match:
        return match.group("id")
    path_name = urlsplit(detail_url).path.rsplit("/", 1)[-1]
    return path_name.rsplit(".", 1)[0] or None


def _extract_date(html: str) -> str | None:
    match = re.search(r"<span\b[^>]*>\s*(20\d{2}-\d{1,2}-\d{1,2})\s*</span>", html, flags=re.IGNORECASE)
    return _normalize_text(match.group(1)) if match else None


def _normalize_title_for_match(text: str) -> str:
    return _normalize_text(text).replace("－", "-").replace("—", "-").replace("~", "-")


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
