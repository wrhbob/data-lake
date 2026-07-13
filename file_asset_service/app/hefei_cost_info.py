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

HEFEI_BASE_URL = "http://112.124.14.202:8009"
HEFEI_ENTRY_URL = f"{HEFEI_BASE_URL}/PeriodicalPublish/index.aspx"
HEFEI_COST_INFO_PARSER_VERSION = "hefei.periodical-aspnet-monthly-pdf-list.v1"
HEFEI_JOURNAL_NAME = "合肥建设工程市场价格信息"
HEFEI_MONTHLY_TITLE_REGEX = r"(20\d{2})年(0?[1-9]|1[0-2])月合肥建设工程市场价格信息"


class HefeiCostInfoClient(Protocol):
    def get_text(self, url: str) -> str:
        ...

    def post_form(self, url: str, data: dict[str, object]) -> str:
        ...

    def get_bytes(self, url: str) -> tuple[bytes, str | None]:
        ...


def hefei_cost_info_source_config() -> dict:
    parser = {
        "adapter_kind": "hefei_pdf",
        "scope": "hefei_periodical_aspnet_monthly_pdf_list",
        "list_url": HEFEI_ENTRY_URL,
        "list_strategy": "aspnet_webforms_year_paginated_pdf_link",
        "regular_issue_type": "正刊",
        "excluded_issue_types": ["市场价格信息（副刊）", "副刊"],
        "period": {
            "kind": "monthly",
            "source": "issue_card",
            "regex": HEFEI_MONTHLY_TITLE_REGEX,
        },
        "title_regex": HEFEI_MONTHLY_TITLE_REGEX,
        "semantic_basis": [
            "PDF编制说明写明属于合肥市城乡建设局公共服务事项，由合肥市建筑市场监督管理处实施。",
            "PDF编制说明写明可作为投资估算、设计概算、施工图预算、最高投标限价编制依据。",
        ],
        "pagination": {"max_pages_per_year": 3, "page_size_observed": 6},
        "attachments": {
            "allowed": ["pdf"],
            "required_any": ["pdf"],
            "source": "issue_card_pdf_anchor",
        },
    }
    return {
        "registry_schema_version": "source_registry.v1",
        "stable": {
            "site_id": "cost_info.ah.hefei.price_info",
            "domain_type": "cost_info",
            "region_code": "340100",
            "coverage_region_code": "340100",
            "producer": "合肥市建筑市场监督管理处",
            "publisher": "合肥市建筑市场监督管理处",
            "publisher_scope": "city",
            "publisher_region_code": "340100",
            "publisher_name": "合肥市建筑市场监督管理处",
            "publisher_type": "cost_station_public_institution",
            "period_kind": "monthly",
            "entry_url": HEFEI_ENTRY_URL,
            "periodicity": {"unit": "month", "period_key_format": "YYYY-MM"},
        },
        "price_coordinates": {
            "price_source_type": "info_price",
            "price_kind": "guidance",
            "price_kind_basis": (
                "PDF编制说明: 可作为合肥市行政区域内建筑工程和市政公用工程投资估算、设计概算、"
                "施工图预算、最高投标限价的编制依据；标题含市场价格但实质为计价信息价。"
            ),
            "tax_type": None,
        },
        "source_shape": {
            "publication_mode": "DIRECT_PDF",
            "source_attachment_mode": "pdf_only",
            "parsability": "text_pdf",
        },
        "parser": {
            "active_parser_version": HEFEI_COST_INFO_PARSER_VERSION,
            "parsers": {
                HEFEI_COST_INFO_PARSER_VERSION: parser,
            },
        },
        "coverage_expectation": {
            "target_regions": [
                {
                    "region_code": "340100",
                    "region_name": "合肥市",
                    "target_level": "city",
                    "requires_city_source": False,
                    "source_completeness_status": "city_source_only",
                    "source_audit_status": "city_public_service_monthly_pdf_source",
                    "coverage_note": (
                        "合肥《合肥建设工程市场价格信息》WebForms 期刊源；仅采正刊 PDF，副刊不混入。"
                    ),
                }
            ]
        },
        "ops": {
            "enabled": True,
            "crawl_strategy_tier": "tier1_aspnet_webforms_pdf_list",
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


class UrllibHefeiCostInfoClient(SourceRegistryHttpClient):
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
            referer=HEFEI_ENTRY_URL,
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


def list_hefei_cost_info_issues(
    client: HefeiCostInfoClient,
    *,
    parser: dict,
    years: list[int] | None = None,
    max_pages_per_year: int | None = None,
) -> list[dict[str, object]]:
    initial_html = client.get_text(str(parser.get("list_url") or HEFEI_ENTRY_URL))
    target_years = years or _extract_regular_years(initial_html)
    if not target_years:
        target_years = [datetime.now(UTC).year]
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    base_form = _extract_form_fields(initial_html)
    page_limit = max_pages_per_year
    if page_limit is None:
        pagination = parser.get("pagination") if isinstance(parser.get("pagination"), dict) else {}
        page_limit = int(pagination.get("max_pages_per_year") or 3)

    for year in target_years:
        html = _post_year_page(client, base_form, int(year), page=1)
        page = 1
        while True:
            for row in _extract_issue_rows(html, parser=parser):
                key = str(row["source_item_key"])
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)
            max_page = _max_page(html)
            if page >= max_page or page >= page_limit:
                break
            page += 1
            html = _post_existing_year_page(client, _extract_form_fields(html), year=int(year), page=page)

    rows.sort(key=lambda row: (int(row["period_year"]), int(row["period_month"])), reverse=True)
    return rows


def ingest_hefei_cost_info_issue(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: HefeiCostInfoClient,
    row: dict,
    actor_id: str = "hefei-cost-info-crawler",
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
        raise ValueError("HEFEI_COST_INFO_ATTACHMENTS_MISSING")

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
            "listed_title": row["listed_title"],
            "issue_type": row["issue_type"],
            "period_year": row["period_year"],
            "period_month": row["period_month"],
            "period_kind": stable.get("period_kind") or "monthly",
            "price_kind": price_coordinates.get("price_kind") or "guidance",
            "price_kind_basis": price_coordinates.get("price_kind_basis"),
            "price_source_type": price_coordinates.get("price_source_type") or "info_price",
            "tax_type": price_coordinates.get("tax_type"),
            "coverage_region_code": stable.get("coverage_region_code") or source.region_code,
            "publisher_type": stable.get("publisher_type") or "cost_station_public_institution",
            "source_attachment_mode": source_shape.get("source_attachment_mode") or "pdf_only",
            "parsability": source_shape.get("parsability") or "text_pdf",
            "publication_mode": source_shape.get("publication_mode") or "DIRECT_PDF",
            "attachment_discovery_methods": _ordered_unique(
                str(attachment.get("discovery_method") or "unknown") for attachment in _row_attachments(row)
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


def run_hefei_cost_info_incremental(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: HefeiCostInfoClient | None = None,
    years: list[int] | None = None,
    max_pages_per_year: int | None = 1,
    max_items: int | None = 5,
    actor_id: str = "hefei-cost-info-crawler",
) -> dict[str, object]:
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or hefei_cost_info_source_config()
    parser = config["parser"]["parsers"][HEFEI_COST_INFO_PARSER_VERSION]
    active_client = client or UrllibHefeiCostInfoClient()
    rows = list_hefei_cost_info_issues(
        active_client,
        parser=parser,
        years=years,
        max_pages_per_year=max_pages_per_year,
    )
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
        ingest_hefei_cost_info_issue(
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


def _post_year_page(client: HefeiCostInfoClient, base_form: dict[str, str], year: int, *, page: int) -> str:
    data = _with_required_hidden_fields(base_form)
    data.update({"hidn_OP": "ZKNF", "hidn_ID": str(year), "hidn_Search": ""})
    if page > 1:
        data.update({"PageNumber1$hCurrPage": str(page), "PageNumber1$txtPage": str(page), "PageNumber1$btnGo": "转到"})
    return client.post_form(HEFEI_ENTRY_URL, data)


def _post_existing_year_page(client: HefeiCostInfoClient, page_form: dict[str, str], *, year: int, page: int) -> str:
    data = _with_required_hidden_fields(page_form)
    data.update(
        {
            "hidn_ID": str(year),
            "PageNumber1$hCurrPage": str(page),
            "PageNumber1$txtPage": str(page),
            "PageNumber1$btnGo": "转到",
        }
    )
    return client.post_form(HEFEI_ENTRY_URL, data)


def _extract_issue_rows(html: str, *, parser: dict) -> list[dict[str, object]]:
    regular_issue_type = str(parser.get("regular_issue_type") or "正刊")
    rows: list[dict[str, object]] = []
    for match in re.finditer(r"<li\b[^>]*>(?P<body>.*?)</li>", html, flags=re.IGNORECASE | re.DOTALL):
        row = _normalize_issue_card(match.group("body"), regular_issue_type=regular_issue_type)
        if row is not None:
            rows.append(row)
    return rows


def _normalize_issue_card(card_html: str, *, regular_issue_type: str) -> dict[str, object] | None:
    open_match = re.search(
        r"toOpen\('(?P<reader>[^']*)','(?P<year>20\d{2})','(?P<month>\d{1,2})月','(?P<issue_type>[^']+)'\)",
        card_html,
        flags=re.IGNORECASE,
    )
    pdf_match = re.search(
        r"<a\b[^>]*href=(?P<quote>['\"])(?P<href>[^'\"]+?\.pdf)(?P=quote)[^>]*>\s*下载本期价格信息\s*</a>",
        card_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not open_match or not pdf_match:
        return None
    issue_type = _normalize_text(open_match.group("issue_type"))
    if issue_type != regular_issue_type:
        return None
    year = int(open_match.group("year"))
    month = int(open_match.group("month"))
    pdf_url = urljoin(HEFEI_ENTRY_URL, unescape(pdf_match.group("href")).strip())
    source_item_key = unquote(urlsplit(pdf_url).path.lstrip("/"))
    period = f"{year:04d}-{month:02d}"
    listed_title = f"{year}年{month}月刊"
    title = f"{year}年{month}月{HEFEI_JOURNAL_NAME}"
    return {
        "source_item_key": source_item_key,
        "title": title,
        "listed_title": listed_title,
        "issue_type": issue_type,
        "period": period,
        "period_year": year,
        "period_month": month,
        "publish_date": _publish_date_from_pdf_path(pdf_url),
        "detail_url": HEFEI_ENTRY_URL,
        "reader_url": urljoin(HEFEI_ENTRY_URL, unescape(open_match.group("reader")).strip()),
        "attachments": [
            {
                "file_name": f"{HEFEI_JOURNAL_NAME}{year}年{month}月.pdf",
                "url": pdf_url,
                "discovery_method": "aspnet_year_issue_pdf_link",
            }
        ],
    }


def _extract_regular_years(html: str) -> list[int]:
    years: list[int] = []
    seen: set[int] = set()
    for match in re.finditer(r"toZKNF\('(?P<year>20\d{2})'\)", html):
        year = int(match.group("year"))
        if year in seen:
            continue
        seen.add(year)
        years.append(year)
    years.sort(reverse=True)
    return years


def _extract_form_fields(html: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in re.finditer(r"<input\b(?P<attrs>[^>]*)>", html, flags=re.IGNORECASE | re.DOTALL):
        name = _attr(match.group("attrs"), "name")
        if not name:
            continue
        fields[name] = _attr(match.group("attrs"), "value") or ""
    return fields


def _with_required_hidden_fields(fields: dict[str, str]) -> dict[str, object]:
    data: dict[str, object] = dict(fields)
    for name in [
        "hidn_OP",
        "hidn_ID",
        "hidn_Search",
        "hidnMac",
        "hidnCPU",
        "hidnHD",
        "hidn_LJ",
        "hidn_NF",
        "hidn_YF",
        "hidn_QKLX",
    ]:
        data.setdefault(name, "")
    return data


def _max_page(html: str) -> int:
    match = re.search(r'id=["\']PageNumber1_lblMaxPage["\']>(?P<page>\d+)<', html)
    if not match:
        return 1
    return max(1, int(match.group("page")))


def _publish_date_from_pdf_path(url: str) -> str | None:
    match = re.search(r"/updatePDF/(?P<year>20\d{2})/(?P<month>\d{1,2})/(?P<day>\d{1,2})/", urlsplit(url).path)
    if not match:
        return None
    return f"{int(match.group('year')):04d}-{int(match.group('month')):02d}-{int(match.group('day')):02d}"


def _row_attachments(row: dict) -> list[dict[str, object]]:
    return list(row.get("attachments") or [])


def _attr(attrs: str, name: str) -> str | None:
    match = re.search(rf"""{name}\s*=\s*(?P<quote>["'])(?P<value>.*?)(?P=quote)""", attrs, flags=re.IGNORECASE)
    if match:
        return unescape(match.group("value"))
    match = re.search(rf"{name}\s*=\s*(?P<value>[^\s>]+)", attrs, flags=re.IGNORECASE)
    if match:
        return unescape(match.group("value"))
    return None


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", unescape(text)).strip()


def _ordered_unique(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
