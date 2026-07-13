from __future__ import annotations

from datetime import UTC, datetime
from html import unescape
import re
from typing import Protocol
from urllib import request
from urllib.parse import unquote, urlencode, urljoin, urlsplit, urlunsplit

from sqlalchemy.orm import Session

from app.archive_rules import build_cost_info_business_key
from app.source_adapter import SourceRegistryHttpClient, archive_business_key_exists, require_data_source, safe_url
from app.source_registry import CostInfoAttachment, CostInfoDiscoveredItem, ingest_cost_info_registry_item
from app.storage import ObjectStore

NANJING_BASE_URL = "https://www.njszj.cn"
NANJING_ENTRY_URL = (
    f"{NANJING_BASE_URL}/ZJWEB/StdCategory.aspx?CategoryID=f55f3e95-167d-4993-b811-3d5327f9ee78"
)
NANJING_COST_INFO_PARSER_VERSION = "nanjing.material-market-info-webforms-monthly-pdf.v1"
NANJING_ORIGINAL_TITLE_REGEX = (
    r"^南京市(?P<year_cn>[二兩两〇零一二三四五六七八九]{4})年"
    r"(?P<month_cn>十一|十二|十|[一二三四五六七八九])月建设工程材料市场信息价格$"
)
NANJING_NORMALIZED_TITLE_REGEX = r"南京市(20\d{2})年(0?[1-9]|1[0-2])月建设工程材料市场信息价格"


class NanjingCostInfoClient(Protocol):
    def get_text(self, url: str) -> str:
        ...

    def post_form(self, url: str, data: dict[str, object]) -> str:
        ...

    def get_bytes(self, url: str) -> tuple[bytes, str | None]:
        ...


def nanjing_cost_info_source_config() -> dict:
    parser = {
        "scope": "nanjing_material_market_info_webforms_monthly_pdf",
        "adapter_kind": "nanjing_pdf",
        "list_url": NANJING_ENTRY_URL,
        "list_strategy": "aspnet_webforms_stateful_gridview",
        "detail_strategy": "message_show_pdf_anchor",
        "period": {
            "kind": "monthly",
            "source": "listed_title",
            "regex": NANJING_NORMALIZED_TITLE_REGEX,
        },
        "title_regex": NANJING_ORIGINAL_TITLE_REGEX,
        "normalized_title_regex": NANJING_NORMALIZED_TITLE_REGEX,
        "pagination": {
            "initial_batch_pages": 1,
            "max_pages_observed": 5,
            "page_size_observed": 20,
            "postback_target": "_ctl0$ContentPlaceHolder1$GridViewStanderd",
            "postback_argument_template": "Page${page}",
            "requires_viewstate": True,
        },
        "http": {
            "requires_browser_headers": True,
            "allowed_headers": ["User-Agent", "Accept", "Accept-Language", "Referer"],
            "boundary_note": "公开页面仅补常规浏览器请求头；不绕登录/验证码/权限控制。",
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
            "site_id": "cost_info.js.nanjing.material_market_info",
            "domain_type": "cost_info",
            "region_code": "320100",
            "coverage_region_code": "320100",
            "producer": "南京市建设工程造价监督站",
            "publisher": "南京市建设工程造价监督站",
            "publisher_scope": "city",
            "publisher_region_code": "320100",
            "publisher_name": "南京市建设工程造价监督站",
            "publisher_type": "cost_station_public_institution",
            "period_kind": "monthly",
            "entry_url": NANJING_ENTRY_URL,
            "periodicity": {"unit": "month", "period_key_format": "YYYY-MM"},
        },
        "price_coordinates": {
            "price_source_type": "info_price",
            "price_kind": "guidance",
            "price_kind_basis": "PDF总说明: 仅作为编制工程概预算及招标控制价的计价参考，并非政府定价。",
            "tax_type": None,
        },
        "source_shape": {
            "publication_mode": "DETAIL_PAGE_ATTACHMENT",
            "source_attachment_mode": "pdf_attachment",
            "parsability": "text_pdf",
        },
        "parser": {
            "active_parser_version": NANJING_COST_INFO_PARSER_VERSION,
            "parsers": {
                NANJING_COST_INFO_PARSER_VERSION: parser,
            },
        },
        "coverage_expectation": {
            "target_regions": [
                {
                    "region_code": "320100",
                    "region_name": "南京市",
                    "target_level": "city",
                    "requires_city_source": False,
                    "source_completeness_status": "city_source_only",
                    "source_audit_status": "official_cost_station_monthly_pdf_source",
                    "coverage_note": "南京造价监督站 WebForms 列表；本轮首批只采首屏近期月度 PDF，历史分页回填另刀处理。",
                }
            ]
        },
        "ops": {
            "enabled": True,
            "crawl_strategy_tier": "tier1_aspnet_webforms_stateful_gridview",
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


class UrllibNanjingCostInfoClient(SourceRegistryHttpClient):
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
            referer=NANJING_ENTRY_URL,
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

    def post_form(self, url: str, data: dict[str, object]) -> str:
        self._throttle()
        body = urlencode(data, doseq=True).encode("utf-8")
        req = request.Request(
            safe_url(url),
            data=body,
            headers=self._headers(content_type="application/x-www-form-urlencoded"),
            method="POST",
        )
        with request.urlopen(req, timeout=self.timeout, context=self._ssl_context) as response:
            return response.read().decode("utf-8", errors="replace")


def list_nanjing_cost_info_issues(
    client: NanjingCostInfoClient,
    *,
    parser: dict,
    max_pages: int | None = 1,
) -> list[dict[str, object]]:
    html = client.get_text(str(parser.get("list_url") or NANJING_ENTRY_URL))
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


def ingest_nanjing_cost_info_issue(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: NanjingCostInfoClient,
    row: dict,
    actor_id: str = "nanjing-cost-info-crawler",
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
        raise ValueError("NANJING_COST_INFO_ATTACHMENTS_MISSING")

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
            "source_title": row["listed_title"],
            "period_year": row["period_year"],
            "period_month": row["period_month"],
            "period_kind": stable.get("period_kind") or "monthly",
            "price_kind": price_coordinates.get("price_kind") or "guidance",
            "price_kind_basis": price_coordinates.get("price_kind_basis"),
            "price_source_type": price_coordinates.get("price_source_type") or "info_price",
            "tax_type": price_coordinates.get("tax_type"),
            "coverage_region_code": stable.get("coverage_region_code") or source.region_code,
            "publisher_type": stable.get("publisher_type") or "cost_station_public_institution",
            "source_attachment_mode": source_shape.get("source_attachment_mode") or "pdf_attachment",
            "parsability": source_shape.get("parsability") or "text_pdf",
            "publication_mode": source_shape.get("publication_mode") or "DETAIL_PAGE_ATTACHMENT",
            "webforms_viewstate_present": bool((row.get("webforms") or {}).get("viewstate")),
            "webforms_eventvalidation_present": bool((row.get("webforms") or {}).get("eventvalidation")),
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


def run_nanjing_cost_info_incremental(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: NanjingCostInfoClient | None = None,
    max_pages: int = 1,
    max_items: int | None = 5,
    actor_id: str = "nanjing-cost-info-crawler",
) -> dict[str, object]:
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or nanjing_cost_info_source_config()
    parser = config["parser"]["parsers"][NANJING_COST_INFO_PARSER_VERSION]
    active_client = client or UrllibNanjingCostInfoClient()
    rows = list_nanjing_cost_info_issues(active_client, parser=parser, max_pages=max_pages)
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
        ingest_nanjing_cost_info_issue(
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
    for match in re.finditer(r"<tr\b[^>]*>(?P<body>.*?)</tr>", html, flags=re.IGNORECASE | re.DOTALL):
        row = _normalize_list_row(match.group("body"), parser=parser, webforms=webforms)
        if row is not None:
            rows.append(row)
    return rows


def _normalize_list_row(row_html: str, *, parser: dict, webforms: dict[str, str | None]) -> dict[str, object] | None:
    link_match = re.search(
        r"<a\b(?P<attrs>[^>]*href=(?P<quote>['\"])(?P<href>[^'\"]*MessageShow\.aspx\?Id=(?P<id>[^'\"]+))(?P=quote)[^>]*)>(?P<text>.*?)</a>",
        row_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not link_match:
        return None
    href = unescape(link_match.group("href")).strip()
    if href.lower().startswith("javascript:"):
        return None
    listed_title = _normalize_visible_text(link_match.group("text"))
    title_match = re.search(str(parser.get("title_regex") or NANJING_ORIGINAL_TITLE_REGEX), listed_title)
    if not title_match:
        return None
    year = _chinese_year_to_int(title_match.group("year_cn"))
    month = _chinese_month_to_int(title_match.group("month_cn"))
    detail_url = _normalize_nanjing_url(urljoin(NANJING_ENTRY_URL, href))
    source_item_key = _source_item_key_from_detail_url(detail_url)
    publish_date_match = re.search(r"\[(?P<date>20\d{2}-\d{2}-\d{2})\]", _html_to_text(row_html))
    period = f"{year:04d}-{month:02d}"
    return {
        "source_item_key": source_item_key,
        "title": f"南京市{year}年{month}月建设工程材料市场信息价格",
        "listed_title": listed_title,
        "period": period,
        "period_year": year,
        "period_month": month,
        "publish_date": publish_date_match.group("date") if publish_date_match else None,
        "detail_url": detail_url,
        "webforms": dict(webforms),
    }


def _extract_detail_pdf_attachments(html: str, detail_url: str) -> list[dict[str, object]]:
    attachments: list[dict[str, object]] = []
    seen: set[str] = set()
    for match in re.finditer(
        r"<a\b(?P<attrs>[^>]*href=(?P<quote>['\"])(?P<href>[^'\"]+?\.pdf)(?P=quote)[^>]*)>(?P<text>.*?)</a>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        href = unescape(match.group("href")).strip()
        url = _normalize_nanjing_url(urljoin(detail_url, href))
        if url in seen:
            continue
        seen.add(url)
        text = _normalize_visible_text(match.group("text"))
        file_name = text if text.lower().endswith(".pdf") else unquote(urlsplit(url).path.rsplit("/", 1)[-1])
        attachments.append(
            {
                "file_name": file_name,
                "url": url,
                "discovery_method": "detail_page_pdf_anchor",
            }
        )
    return attachments


def _post_page(client: NanjingCostInfoClient, form_fields: dict[str, str], *, next_page: int, parser: dict) -> str:
    data: dict[str, object] = dict(form_fields)
    pagination = parser.get("pagination") if isinstance(parser.get("pagination"), dict) else {}
    target = str(pagination.get("postback_target") or "_ctl0$ContentPlaceHolder1$GridViewStanderd")
    data["__EVENTTARGET"] = target
    data["__EVENTARGUMENT"] = f"Page${next_page}"
    return client.post_form(NANJING_ENTRY_URL, data)


def _has_postback_page(html: str, *, next_page: int, parser: dict) -> bool:
    pagination = parser.get("pagination") if isinstance(parser.get("pagination"), dict) else {}
    target = re.escape(str(pagination.get("postback_target") or "_ctl0$ContentPlaceHolder1$GridViewStanderd"))
    return bool(re.search(rf"__doPostBack\(['\"]{target}['\"],['\"]Page\${next_page}['\"]\)", html))


def _extract_form_fields(html: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in re.finditer(r"<input\b(?P<attrs>[^>]*)>", html, flags=re.IGNORECASE | re.DOTALL):
        name = _attr(match.group("attrs"), "name")
        if not name:
            continue
        fields[name] = _attr(match.group("attrs"), "value") or ""
    return fields


def _webforms_state(html: str) -> dict[str, str | None]:
    fields = _extract_form_fields(html)
    return {
        "viewstate": fields.get("__VIEWSTATE"),
        "eventvalidation": fields.get("__EVENTVALIDATION"),
    }


def _attr(attrs: str, name: str) -> str | None:
    match = re.search(rf"""{name}\s*=\s*(?P<quote>["'])(?P<value>.*?)(?P=quote)""", attrs, flags=re.IGNORECASE)
    if match:
        return unescape(match.group("value"))
    match = re.search(rf"{name}\s*=\s*(?P<value>[^\s>]+)", attrs, flags=re.IGNORECASE)
    if match:
        return unescape(match.group("value"))
    return None


def _source_item_key_from_detail_url(detail_url: str) -> str:
    match = re.search(r"[?&]Id=(?P<id>[^&]+)", detail_url, flags=re.IGNORECASE)
    if not match:
        return unquote(urlsplit(detail_url).path.lstrip("/"))
    return unquote(match.group("id"))


def _normalize_nanjing_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.netloc.lower() == "www.njszj.cn" and parts.scheme == "http":
        parts = parts._replace(scheme="https")
    return urlunsplit(parts)


def _normalize_visible_text(html: str) -> str:
    return _normalize_text(re.sub(r"<[^>]+>", "", html))


def _html_to_text(html: str) -> str:
    return _normalize_text(re.sub(r"<[^>]+>", " ", html))


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _chinese_year_to_int(raw: str) -> int:
    digits = {"〇": "0", "零": "0", "一": "1", "二": "2", "兩": "2", "两": "2", "三": "3", "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9"}
    try:
        return int("".join(digits[ch] for ch in raw.strip()))
    except KeyError as exc:
        raise ValueError(f"INVALID_CHINESE_YEAR: {raw}") from exc


def _chinese_month_to_int(raw: str) -> int:
    months = {
        "一": 1,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
        "十一": 11,
        "十二": 12,
    }
    try:
        return months[raw.strip()]
    except KeyError as exc:
        raise ValueError(f"INVALID_CHINESE_MONTH: {raw}") from exc


def _ordered_unique(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
