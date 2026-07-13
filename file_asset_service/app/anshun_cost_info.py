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

ANSHUN_BASE_URL = "http://zfcx.anshun.gov.cn"
ANSHUN_LIST_URL = f"{ANSHUN_BASE_URL}/zwgk/zfxxgk/xxgkml/cxjs/jzgl_84084/"
ANSHUN_COST_INFO_PARSER_VERSION = "anshun.zfcx-static-monthly-material-reference-xls.v1"
ANSHUN_TITLE_REGEX = r"^(20\d{2})年(0?[1-9]|1[0-2])月份安顺市区主要建筑安装材料市场综合参考价$"


class AnshunCostInfoClient(Protocol):
    def get_text(self, url: str) -> str:
        ...

    def get_bytes(self, url: str) -> tuple[bytes, str | None]:
        ...


def anshun_cost_info_source_config() -> dict:
    parser = {
        "scope": "anshun_zfcx_static_monthly_material_reference_xls",
        "list_url": ANSHUN_LIST_URL,
        "list_strategy": "static_html_single_page_article_list",
        "detail_strategy": "article_detail_xls_anchor",
        "period": {
            "kind": "monthly",
            "source": "title",
            "regex": ANSHUN_TITLE_REGEX,
        },
        "title_regex": ANSHUN_TITLE_REGEX,
        "semantic_basis": [
            "安顺市住房和城乡建设局建筑管理栏目按月发布市区主要建筑安装材料市场综合参考价。",
            "详情页元数据 ContentSource 为“市住建局（市场科）”，附件为单一月度材料综合参考价 xls。",
            "本源是官方月度材料信息价价表，未见文档级厂家报价或综合期刊结构；L0 原件入湖不读内容。",
        ],
        "attachments": {
            "allowed": ["xls"],
            "required_any": ["xls"],
            "source": "detail_page_xls_anchor",
        },
    }
    return {
        "registry_schema_version": "source_registry.v1",
        "stable": {
            "site_id": "cost_info.gz.anshun.material_reference_price",
            "domain_type": "cost_info",
            "region_code": "520400",
            "coverage_region_code": "520400",
            "producer": "安顺市住房和城乡建设局",
            "publisher": "安顺市住房和城乡建设局",
            "publisher_scope": "city",
            "publisher_region_code": "520400",
            "publisher_name": "安顺市住房和城乡建设局",
            "publisher_type": "official_housing_urban_rural_development",
            "period_kind": "monthly",
            "entry_url": ANSHUN_LIST_URL,
            "periodicity": {"unit": "month", "period_key_format": "YYYY-MM"},
        },
        "price_coordinates": {
            "price_source_type": "info_price",
            "price_kind": "guidance",
            "price_kind_basis": (
                "安顺市住建局（市场科）按月发布市区主要建筑安装材料市场综合参考价；"
                "这是官方月度材料信息价价表，标题含“市场/参考”但并非厂家报价或混合市场情报。"
            ),
            "tax_type": None,
        },
        "source_shape": {
            "publication_mode": "DETAIL_PAGE_ATTACHMENT",
            "source_attachment_mode": "xls_attachment",
            "parsability": "opaque_spreadsheet",
        },
        "parser": {
            "active_parser_version": ANSHUN_COST_INFO_PARSER_VERSION,
            "parsers": {
                ANSHUN_COST_INFO_PARSER_VERSION: parser,
            },
        },
        "coverage_expectation": {
            "target_regions": [
                {
                    "region_code": "520400",
                    "region_name": "安顺市",
                    "target_level": "city",
                    "requires_city_source": False,
                    "source_completeness_status": "city_source_only",
                    "source_audit_status": "official_city_monthly_xls_source",
                    "coverage_note": "安顺市住房和城乡建设局建筑管理混合列表；仅采“安顺市区主要建筑安装材料市场综合参考价”月度 xls，行政处罚、招投标警示等建筑管理通知不混入。",
                }
            ]
        },
        "ops": {
            "enabled": True,
            "crawl_strategy_tier": "tier1_static_detail_xls",
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
            "alert_thresholds": {"missing_month_count": 1},
            "http": {
                "user_agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                )
            },
        },
    }


class UrllibAnshunCostInfoClient(SourceRegistryHttpClient):
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
            referer=ANSHUN_LIST_URL,
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


def list_anshun_cost_info_issues(
    client: AnshunCostInfoClient,
    *,
    parser: dict,
    max_items: int | None = 5,
) -> list[dict[str, object]]:
    html = client.get_text(str(parser.get("list_url") or ANSHUN_LIST_URL))
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
    rows.sort(key=lambda row: str(row["period"]), reverse=True)
    if max_items is not None:
        return rows[:max_items]
    return rows


def ingest_anshun_cost_info_issue(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: AnshunCostInfoClient,
    row: dict,
    actor_id: str = "anshun-cost-info-crawler",
    task_id: str | None = None,
):
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or {}
    stable = config.get("stable") if isinstance(config.get("stable"), dict) else {}
    price_coordinates = config.get("price_coordinates") if isinstance(config.get("price_coordinates"), dict) else {}
    source_shape = config.get("source_shape") if isinstance(config.get("source_shape"), dict) else {}
    detail_html = client.get_text(str(row["detail_url"]))
    detail_attachments = _extract_detail_xls_attachments(detail_html, str(row["detail_url"]))
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
                content_type=content_type or "application/vnd.ms-excel",
            )
        )
    if not downloaded:
        raise ValueError("ANSHUN_COST_INFO_ATTACHMENTS_MISSING")

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
            "source_title": row["title"],
            "listed_title": row["title"],
            "period_year": row["period_year"],
            "period_month": row["period_month"],
            "period_kind": stable.get("period_kind") or "monthly",
            "price_kind": price_coordinates.get("price_kind") or "guidance",
            "price_kind_basis": price_coordinates.get("price_kind_basis"),
            "price_source_type": price_coordinates.get("price_source_type") or "info_price",
            "tax_type": price_coordinates.get("tax_type"),
            "coverage_region_code": stable.get("coverage_region_code") or source.region_code,
            "publisher_type": stable.get("publisher_type") or "official_housing_urban_rural_development",
            "source_attachment_mode": source_shape.get("source_attachment_mode") or "xls_attachment",
            "parsability": source_shape.get("parsability") or "opaque_spreadsheet",
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


def run_anshun_cost_info_incremental(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: AnshunCostInfoClient | None = None,
    max_items: int | None = 5,
    actor_id: str = "anshun-cost-info-crawler",
) -> dict[str, object]:
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or anshun_cost_info_source_config()
    parser = config["parser"]["parsers"][ANSHUN_COST_INFO_PARSER_VERSION]
    active_client = client or UrllibAnshunCostInfoClient()
    rows = list_anshun_cost_info_issues(active_client, parser=parser, max_items=max_items)
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
        ingest_anshun_cost_info_issue(
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


def _extract_list_rows(html: str) -> list[str]:
    return [
        match.group("body")
        for match in re.finditer(r"<li\b[^>]*>(?P<body>.*?)</li>", html, flags=re.IGNORECASE | re.DOTALL)
    ]


def _normalize_list_row(row_html: str, *, parser: dict) -> dict[str, object] | None:
    link_match = re.search(
        r"<a\b(?P<attrs>[^>]*href=(?P<quote>['\"])(?P<href>[^'\"]+?\.html)(?P=quote)[^>]*)>(?P<text>.*?)</a>",
        row_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not link_match:
        return None
    href = unescape(link_match.group("href")).strip()
    if href.lower().startswith("javascript:"):
        return None
    attrs = link_match.group("attrs")
    title = _normalize_text(_attr(attrs, "title") or _html_to_text(link_match.group("text")))
    match = re.search(str(parser.get("title_regex") or ANSHUN_TITLE_REGEX), title)
    if not match:
        return None
    year = int(match.group(1))
    month = int(match.group(2))
    detail_url = urljoin(ANSHUN_LIST_URL, href)
    publish_date_match = re.search(r"20\d{2}-\d{2}-\d{2}", _html_to_text(row_html))
    return {
        "source_item_key": _source_item_key_from_detail_url(detail_url),
        "title": title,
        "period": f"{year:04d}-{month:02d}",
        "period_year": year,
        "period_month": month,
        "publish_date": publish_date_match.group(0) if publish_date_match else None,
        "detail_url": detail_url,
    }


def _extract_detail_xls_attachments(html: str, detail_url: str) -> list[dict[str, object]]:
    attachments: list[dict[str, object]] = []
    seen: set[str] = set()
    for match in re.finditer(
        r"<a\b(?P<attrs>[^>]*href=(?P<quote>['\"])(?P<href>[^'\"]+?\.xls)(?P=quote)[^>]*)>(?P<text>.*?)</a>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        href = unescape(match.group("href")).strip()
        url = urljoin(detail_url, href)
        if url in seen:
            continue
        seen.add(url)
        attrs = match.group("attrs")
        file_name = _attr(attrs, "download") or _attr(attrs, "title") or _html_to_text(match.group("text"))
        file_name = _normalize_text(file_name)
        if not file_name.lower().endswith(".xls"):
            file_name = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
        attachments.append(
            {
                "file_name": file_name,
                "url": url,
                "discovery_method": "detail_page_xls_anchor",
            }
        )
    return attachments


def _source_item_key_from_detail_url(detail_url: str) -> str:
    match = re.search(r"/t20\d{6}_(?P<id>\d+)\.html", detail_url)
    if match:
        return match.group("id")
    return urlsplit(detail_url).path.rsplit("/", 1)[-1].removesuffix(".html")


def _attr(attrs: str, name: str) -> str | None:
    match = re.search(rf"""{name}\s*=\s*(?P<quote>["'])(?P<value>.*?)(?P=quote)""", attrs, flags=re.IGNORECASE)
    if match:
        return unescape(match.group("value"))
    match = re.search(rf"{name}\s*=\s*(?P<value>[^\s>]+)", attrs, flags=re.IGNORECASE)
    if match:
        return unescape(match.group("value"))
    return None


def _html_to_text(html: str) -> str:
    return _normalize_text(re.sub(r"<[^>]+>", " ", html))


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _ordered_unique(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
