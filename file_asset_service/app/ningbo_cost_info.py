from __future__ import annotations

from datetime import UTC, datetime
from html import unescape
import http.cookiejar
import re
from typing import Protocol
from urllib import request
from urllib.parse import unquote, urlencode, urljoin, urlsplit

from sqlalchemy.orm import Session

from app.archive_rules import build_cost_info_business_key
from app.source_adapter import (
    SourceRegistryHttpClient,
    archive_business_key_exists,
    read_response_bytes_with_deadline,
    require_data_source,
    safe_url,
)
from app.source_registry import CostInfoAttachment, CostInfoDiscoveredItem, ingest_cost_info_registry_item
from app.storage import ObjectStore

NINGBO_BASE_URL = "https://www.nbzj.net"
NINGBO_ENTRY_URL = f"{NINGBO_BASE_URL}/Book/ElectronicJournalList.aspx?CategoryId=193"
NINGBO_COST_INFO_PARSER_VERSION = "ningbo.comprehensive-journal-webforms-monthly-pdf.v1"
NINGBO_TITLE_REGEX = r"^(20\d{2})年(0?[1-9]|1[0-2])月刊综合版$"
NINGBO_NORMALIZED_TITLE_REGEX = r"宁波造价信息(20\d{2})年(0?[1-9]|1[0-2])月刊综合版"
NINGBO_DOWNLOAD_TARGET = "ctl00$ContentPlaceContent$lbtnDownLoad"


class NingboCostInfoClient(Protocol):
    def get_text(self, url: str) -> str:
        ...

    def post_form(self, url: str, data: dict[str, object]) -> str:
        ...

    def post_form_bytes(self, url: str, data: dict[str, object]) -> tuple[bytes, str | None, str | None]:
        ...


def ningbo_cost_info_source_config() -> dict:
    parser = {
        "adapter_kind": "ningbo_pdf",
        "scope": "ningbo_comprehensive_journal_webforms_monthly_pdf",
        "list_url": NINGBO_ENTRY_URL,
        "list_strategy": "aspnet_webforms_journal_list",
        "detail_strategy": "electronic_journal_view",
        "download_strategy": "detail_page_webforms_postback",
        "period": {
            "kind": "monthly",
            "source": "title",
            "regex": NINGBO_NORMALIZED_TITLE_REGEX,
        },
        "title_regex": NINGBO_TITLE_REGEX,
        "normalized_title_regex": NINGBO_NORMALIZED_TITLE_REGEX,
        "price_kind_note": "综合版整本月刊未文档级分离；材料信息价、人工参考价、指数、租赁价格等混排。",
        "pagination": {
            "initial_batch_pages": 1,
            "max_pages_observed": 4,
            "page_size_observed": 12,
            "postback_target": "ctl00$ContentPlaceContent$Pager",
            "requires_viewstate": True,
        },
        "download": {
            "postback_target": NINGBO_DOWNLOAD_TARGET,
            "requires_detail_viewstate": True,
        },
        "attachments": {
            "allowed": ["pdf"],
            "required_any": ["pdf"],
            "source": "detail_page_webforms_postback",
        },
    }
    return {
        "registry_schema_version": "source_registry.v1",
        "stable": {
            "site_id": "cost_info.zj.ningbo.comprehensive_journal",
            "domain_type": "cost_info",
            "region_code": "330200",
            "coverage_region_code": "330200",
            "producer": "宁波市建筑市场管理服务总站（宁波市建设工程造价管理服务总站）",
            "publisher": "宁波市建筑市场管理服务总站（宁波市建设工程造价管理服务总站）",
            "publisher_scope": "city",
            "publisher_region_code": "330200",
            "publisher_name": "宁波市建筑市场管理服务总站（宁波市建设工程造价管理服务总站）",
            "publisher_type": "cost_station_public_institution",
            "period_kind": "monthly",
            "entry_url": NINGBO_ENTRY_URL,
            "periodicity": {"unit": "month", "period_key_format": "YYYY-MM"},
        },
        "price_coordinates": {
            "price_source_type": "info_price",
            "price_kind": "unspecified",
            "price_kind_basis": (
                "综合版为整本月刊，含材料信息价、人工参考价、价格指数、租赁价格、政策文章等；"
                "未文档级分离，L0 不读内容拆分，价格行分类后置。"
            ),
            "tax_type": None,
        },
        "source_shape": {
            "publication_mode": "DETAIL_PAGE_POSTBACK_DOWNLOAD",
            "source_attachment_mode": "pdf_attachment",
            "parsability": "text_pdf",
        },
        "parser": {
            "active_parser_version": NINGBO_COST_INFO_PARSER_VERSION,
            "parsers": {
                NINGBO_COST_INFO_PARSER_VERSION: parser,
            },
        },
        "coverage_expectation": {
            "target_regions": [
                {
                    "region_code": "330200",
                    "region_name": "宁波市",
                    "target_level": "city",
                    "requires_city_source": False,
                    "source_completeness_status": "city_source_only",
                    "source_audit_status": "official_cost_station_monthly_comprehensive_journal",
                    "coverage_note": "宁波造价信息综合版 WebForms 月刊；本轮首批只采首屏近期月刊，历史分页回填另刀处理。",
                }
            ]
        },
        "ops": {
            "enabled": True,
            "crawl_strategy_tier": "tier1_aspnet_webforms_journal_postback_download",
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


class UrllibNingboCostInfoClient(SourceRegistryHttpClient):
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
            referer=NINGBO_ENTRY_URL,
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
        self._opener = request.build_opener(request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def get_text(self, url: str) -> str:
        self._throttle()
        req = request.Request(safe_url(url), headers=self._headers(), method="GET")
        with self._opener.open(req, timeout=self.timeout) as response:
            return response.read().decode("utf-8", errors="replace")

    def post_form(self, url: str, data: dict[str, object]) -> str:
        content, _content_type, _content_disposition = self.post_form_bytes(url, data)
        return content.decode("utf-8", errors="replace")

    def post_form_bytes(self, url: str, data: dict[str, object]) -> tuple[bytes, str | None, str | None]:
        self._throttle()
        body = urlencode(data, doseq=True).encode("utf-8")
        req = request.Request(
            safe_url(url),
            data=body,
            headers=self._headers(content_type="application/x-www-form-urlencoded"),
            method="POST",
        )
        with self._opener.open(req, timeout=self.timeout) as response:
            return (
                read_response_bytes_with_deadline(
                    response,
                    total_timeout_seconds=self.read_total_timeout_seconds,
                    chunk_size=self.read_chunk_size,
                ),
                response.headers.get("Content-Type"),
                response.headers.get("Content-Disposition"),
            )


def list_ningbo_cost_info_issues(
    client: NingboCostInfoClient,
    *,
    parser: dict,
    max_pages: int | None = 1,
) -> list[dict[str, object]]:
    html = client.get_text(str(parser.get("list_url") or NINGBO_ENTRY_URL))
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    page = 1
    while True:
        for row in _extract_list_rows(html, parser=parser):
            key = str(row["source_item_key"])
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
        if max_pages is not None and page >= max_pages:
            break
        next_page = page + 1
        if not _has_postback_page(html, next_page=next_page, parser=parser):
            break
        html = _post_page(client, _extract_form_fields(html), next_page=next_page, parser=parser)
        page = next_page

    rows.sort(key=lambda row: str(row["period"]), reverse=True)
    return rows


def ingest_ningbo_cost_info_issue(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: NingboCostInfoClient,
    row: dict,
    actor_id: str = "ningbo-cost-info-crawler",
    task_id: str | None = None,
):
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or {}
    stable = config.get("stable") if isinstance(config.get("stable"), dict) else {}
    price_coordinates = config.get("price_coordinates") if isinstance(config.get("price_coordinates"), dict) else {}
    source_shape = config.get("source_shape") if isinstance(config.get("source_shape"), dict) else {}
    detail_url = str(row["detail_url"])
    detail_html = client.get_text(detail_url)
    detail_title = _extract_detail_title(detail_html) or str(row["listed_title"])
    form_fields = _extract_form_fields(detail_html)
    data: dict[str, object] = dict(form_fields)
    data["__EVENTTARGET"] = NINGBO_DOWNLOAD_TARGET
    data["__EVENTARGUMENT"] = ""
    content, content_type, content_disposition = client.post_form_bytes(detail_url, data)
    if not content:
        raise ValueError("NINGBO_COST_INFO_ATTACHMENT_MISSING")

    file_name = _file_name_from_content_disposition(content_disposition) or f"{detail_title}.pdf"
    item = CostInfoDiscoveredItem(
        title=str(row["title"]),
        publish_date=str(row["publish_date"]) if row.get("publish_date") else None,
        detail_url=detail_url,
        discovered_at=str(row["publish_date"]) if row.get("publish_date") else None,
        fetched_at=datetime.now(UTC).isoformat(),
        attachments=[
            CostInfoAttachment(
                file_name=file_name,
                content=content,
                url=detail_url,
                content_type=content_type,
            )
        ],
        metadata={
            "source_item_key": row["source_item_key"],
            "source_item_id": row["source_item_key"],
            "listed_title": row["listed_title"],
            "source_title": row["listed_title"],
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
            "parsability": source_shape.get("parsability") or "text_pdf",
            "publication_mode": source_shape.get("publication_mode") or "DETAIL_PAGE_POSTBACK_DOWNLOAD",
            "webforms_viewstate_present": bool(form_fields.get("__VIEWSTATE")),
            "webforms_eventvalidation_present": bool(form_fields.get("__EVENTVALIDATION")),
            "download_postback_target": NINGBO_DOWNLOAD_TARGET,
            "attachment_discovery_methods": ["webforms_download_postback"],
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


def run_ningbo_cost_info_incremental(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: NingboCostInfoClient | None = None,
    max_pages: int = 1,
    max_items: int | None = 5,
    actor_id: str = "ningbo-cost-info-crawler",
) -> dict[str, object]:
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or ningbo_cost_info_source_config()
    parser = config["parser"]["parsers"][NINGBO_COST_INFO_PARSER_VERSION]
    active_client = client or UrllibNingboCostInfoClient()
    rows = list_ningbo_cost_info_issues(active_client, parser=parser, max_pages=max_pages)
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
        ingest_ningbo_cost_info_issue(
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


def _extract_list_rows(html: str, *, parser: dict) -> list[dict[str, object]]:
    webforms = _webforms_state(html)
    rows: list[dict[str, object]] = []
    for match in re.finditer(
        r"<a\b(?P<attrs>[^>]*href=(?P<quote>['\"])(?P<href>ElectronicJournalView\.aspx\?ContentId=(?P<id>\d+))(?P=quote)[^>]*)>"
        r"(?P<body>.*?)</a>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        row = _normalize_list_item(match, parser=parser, webforms=webforms)
        if row is not None:
            rows.append(row)
    return rows


def _normalize_list_item(match: re.Match[str], *, parser: dict, webforms: dict[str, str | None]) -> dict[str, object] | None:
    listed_title = _extract_list_item_title(match.group("body"))
    title_match = re.search(str(parser.get("title_regex") or NINGBO_TITLE_REGEX), listed_title)
    if not title_match:
        return None
    year = int(title_match.group(1))
    month = int(title_match.group(2))
    detail_url = urljoin(NINGBO_ENTRY_URL, unescape(match.group("href")).strip())
    source_item_key = str(match.group("id"))
    period = f"{year:04d}-{month:02d}"
    return {
        "source_item_key": source_item_key,
        "title": f"宁波造价信息{year}年{month}月刊综合版",
        "listed_title": listed_title,
        "period": period,
        "period_year": year,
        "period_month": month,
        "publish_date": None,
        "detail_url": detail_url,
        "webforms": dict(webforms),
    }


def _extract_list_item_title(body_html: str) -> str:
    strong = re.search(r"<h3\b[^>]*>\s*<strong>(?P<title>.*?)</strong>", body_html, flags=re.IGNORECASE | re.DOTALL)
    if strong:
        return _normalize_visible_text(strong.group("title"))
    alt = re.search(r"<img\b[^>]*\balt=(?P<quote>['\"])(?P<title>.*?)(?P=quote)", body_html, flags=re.IGNORECASE | re.DOTALL)
    if alt:
        return _normalize_text(alt.group("title"))
    return _normalize_visible_text(body_html)


def _post_page(client: NingboCostInfoClient, form_fields: dict[str, str], *, next_page: int, parser: dict) -> str:
    data: dict[str, object] = dict(form_fields)
    pagination = parser.get("pagination") if isinstance(parser.get("pagination"), dict) else {}
    target = str(pagination.get("postback_target") or "ctl00$ContentPlaceContent$Pager")
    data["__EVENTTARGET"] = target
    data["__EVENTARGUMENT"] = str(next_page)
    return client.post_form(NINGBO_ENTRY_URL, data)


def _has_postback_page(html: str, *, next_page: int, parser: dict) -> bool:
    pagination = parser.get("pagination") if isinstance(parser.get("pagination"), dict) else {}
    target = re.escape(str(pagination.get("postback_target") or "ctl00$ContentPlaceContent$Pager"))
    return bool(re.search(rf"__doPostBack\(['\"]{target}['\"],['\"]{next_page}['\"]\)", html))


def _extract_form_fields(html: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in re.finditer(r"<input\b(?P<attrs>[^>]*)>", html, flags=re.IGNORECASE | re.DOTALL):
        name = _attr(match.group("attrs"), "name")
        if not name:
            continue
        fields[name] = _attr(match.group("attrs"), "value") or ""
    for match in re.finditer(
        r"<select\b(?P<attrs>[^>]*)>(?P<body>.*?)</select>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        name = _attr(match.group("attrs"), "name")
        if not name:
            continue
        fields[name] = _selected_option_value(match.group("body"))
    return fields


def _selected_option_value(select_body: str) -> str:
    selected = re.search(r"<option\b(?P<attrs>[^>]*\bselected(?:\s*=\s*['\"][^'\"]*['\"])?[^>]*)>", select_body, flags=re.IGNORECASE)
    if selected:
        return _attr(selected.group("attrs"), "value") or _normalize_visible_text(selected.group(0))
    first = re.search(r"<option\b(?P<attrs>[^>]*)>", select_body, flags=re.IGNORECASE)
    if first:
        return _attr(first.group("attrs"), "value") or ""
    return ""


def _webforms_state(html: str) -> dict[str, str | None]:
    fields = _extract_form_fields(html)
    return {
        "viewstate": fields.get("__VIEWSTATE"),
        "eventvalidation": fields.get("__EVENTVALIDATION"),
    }


def _extract_detail_title(html: str) -> str | None:
    match = re.search(r"<div\b[^>]*class=(?P<quote>['\"])[^'\"]*\bz_title\b[^'\"]*(?P=quote)[^>]*>(?P<title>.*?)</div>", html, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return _normalize_visible_text(match.group("title"))


def _file_name_from_content_disposition(content_disposition: str | None) -> str | None:
    if not content_disposition:
        return None
    match = re.search(r"filename\*?=(?:UTF-8''|)(?P<quote>['\"]?)(?P<name>[^;'\"\r\n]+)(?P=quote)", content_disposition, flags=re.IGNORECASE)
    if not match:
        return None
    return unquote(match.group("name"))


def _attr(attrs: str, name: str) -> str | None:
    match = re.search(rf"""{name}\s*=\s*(?P<quote>["'])(?P<value>.*?)(?P=quote)""", attrs, flags=re.IGNORECASE | re.DOTALL)
    if match:
        return unescape(match.group("value"))
    match = re.search(rf"{name}\s*=\s*(?P<value>[^\s>]+)", attrs, flags=re.IGNORECASE)
    if match:
        return unescape(match.group("value"))
    return None


def _normalize_visible_text(html: str) -> str:
    return _normalize_text(re.sub(r"<[^>]+>", "", html))


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(text)).strip()
