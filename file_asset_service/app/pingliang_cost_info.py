from __future__ import annotations

from datetime import UTC, datetime
from html import unescape
import json
import re
from typing import Protocol
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit, urlunsplit

from sqlalchemy.orm import Session

from app.archive_rules import build_cost_info_business_key
from app.source_adapter import SourceRegistryHttpClient, archive_business_key_exists, require_data_source
from app.source_registry import CostInfoAttachment, CostInfoDiscoveredItem, ingest_cost_info_registry_item
from app.storage import ObjectStore

PINGLIANG_BASE_URL = "https://zjj.pingliang.gov.cn"
PINGLIANG_LIST_URL = f"{PINGLIANG_BASE_URL}/zfxxgk/fdzdgknr/lzyj/sjbjzc/index.html"
PINGLIANG_LIST_API_URL = f"{PINGLIANG_BASE_URL}/api-gateway/jpaas-publish-server/front/page/build/unit"
PINGLIANG_COST_INFO_PARSER_VERSION = "pingliang.zjj-jpaas-issue-based-material-price-pdf.v1"
PINGLIANG_PERIOD_REGEX = r"(?P<period>(?P<year>20\d{2})年第(?P<issue>\d{1,2}|[一二三四五六七八九十]+)期)"


class PingliangCostInfoClient(Protocol):
    def get_text(self, url: str) -> str:
        ...

    def get_bytes(self, url: str) -> tuple[bytes, str | None]:
        ...


def pingliang_cost_info_source_config() -> dict:
    parser = {
        "adapter_kind": "pingliang_pdf",
        "scope": "pingliang_zjj_jpaas_issue_based_material_price_pdf",
        "list_url": PINGLIANG_LIST_URL,
        "list_api_url": PINGLIANG_LIST_API_URL,
        "list_strategy": "jpaas_unit_list_detail_pdf_anchor",
        "detail_strategy": "article_detail_filtered_pdf_anchor",
        "jpaas": {
            "parseType": "bulidstatic",
            "webId": "3db5818b243e4b4495e9ed9bfe7a7cc3",
            "tplSetId": "2143cc8bdacf4ec68ad1e8cbe22f56a4",
            "pageType": "column",
            "tagId": "信息公开内容2",
            "editType": "null",
            "pageId": "3e29889524c0468f89bbd4e324d86968",
        },
        "page_size": 20,
        "period": {
            "kind": "issue_based",
            "source": "normalized_title",
            "regex": PINGLIANG_PERIOD_REGEX,
        },
        "title_regex": PINGLIANG_PERIOD_REGEX,
        "title_keywords_all": ["平凉市", "发布"],
        "title_keywords_any": ["建设工程造价信息", "建设工程材料信息价格"],
        "excluded_title_keywords": [
            "人工市场信息价",
            "住宅",
            "为民实事",
            "保障性住房",
            "维修资金",
            "招标投标",
            "前期费",
        ],
        "attachments": {
            "allowed": ["pdf"],
            "required_any": ["pdf"],
            "source": "detail_page_filtered_pdf_anchor",
            "title_keywords_any": ["材料信息价格", "材料价格汇总表", "材料信息价"],
            "excluded_title_keywords": ["造价指标", "海绵城市", "参考价格"],
        },
        "semantic_basis": [
            "平凉市住建局市级/本局政策栏目发布建设工程造价信息、建设工程材料信息价格。",
            "详情页附件为当期建设工程材料信息价格汇总表 PDF；造价指标、海绵城市参考价格等相邻 PDF 不纳入本 adapter。",
            "2025 年上/下半年人工市场信息价详情页当前无附件，MVP 不采该 HTML 特例。",
        ],
    }
    return {
        "registry_schema_version": "source_registry.v1",
        "stable": {
            "site_id": "cost_info.gs.pingliang.zjj.price_info",
            "domain_type": "cost_info",
            "region_code": "620800",
            "coverage_region_code": "620800",
            "province": "甘肃省",
            "city": "平凉市",
            "producer": "平凉市住房和城乡建设局",
            "publisher": "平凉市住房和城乡建设局",
            "publisher_scope": "city",
            "publisher_region_code": "620800",
            "publisher_name": "平凉市住房和城乡建设局",
            "publisher_type": "official_housing_urban_rural_development",
            "period_kind": "issue_based",
            "entry_url": PINGLIANG_LIST_URL,
            "periodicity": {"unit": "issue", "period_key_format": "YYYY年第N期"},
        },
        "price_coordinates": {
            "price_source_type": "info_price",
            "price_kind": "guidance",
            "price_kind_basis": "栏目发布建设工程造价信息、建设工程材料信息价格，作为工程计价参考来源。",
            "tax_type": None,
        },
        "source_shape": {
            "publication_mode": "DETAIL_PAGE_ATTACHMENT",
            "source_attachment_mode": "pdf_attachment",
            "parsability": "text_pdf",
        },
        "parser": {
            "active_parser_version": PINGLIANG_COST_INFO_PARSER_VERSION,
            "parsers": {
                PINGLIANG_COST_INFO_PARSER_VERSION: parser,
            },
        },
        "coverage_expectation": {
            "target_regions": [
                {
                    "region_code": "620800",
                    "region_name": "平凉市",
                    "target_level": "city",
                    "requires_city_source": False,
                    "source_completeness_status": "city_source_only",
                    "source_audit_status": "official_city_zjj_issue_based_material_price_pdf",
                    "coverage_note": "平凉市住建局市级/本局政策栏目混合列表；仅采建设工程造价信息、建设工程材料信息价格记录中的材料信息价格 PDF 附件，人工市场信息价无附件特例后续处理。",
                }
            ]
        },
        "ops": {
            "enabled": True,
            "crawl_strategy_tier": "tier2_jpaas_detail_filtered_pdf",
            "schedule": {"frequency": "per_issue"},
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


class UrllibPingliangCostInfoClient(SourceRegistryHttpClient):
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
            referer=PINGLIANG_LIST_URL,
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


def list_pingliang_cost_info_issues(
    client: PingliangCostInfoClient,
    *,
    parser: dict,
    max_items: int | None = 5,
    page_no: int = 1,
) -> list[dict[str, object]]:
    payload = client.get_text(_build_list_api_url(parser, page_no=page_no))
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw in _extract_list_rows(_unit_html(payload)):
        row = _normalize_list_row(raw, parser=parser)
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


def ingest_pingliang_cost_info_issue(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: PingliangCostInfoClient,
    row: dict,
    actor_id: str = "pingliang-cost-info-crawler",
    task_id: str | None = None,
):
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or {}
    stable = config.get("stable") if isinstance(config.get("stable"), dict) else {}
    price_coordinates = config.get("price_coordinates") if isinstance(config.get("price_coordinates"), dict) else {}
    source_shape = config.get("source_shape") if isinstance(config.get("source_shape"), dict) else {}
    parser = config["parser"]["parsers"][PINGLIANG_COST_INFO_PARSER_VERSION]
    detail_html = client.get_text(str(row["detail_url"]))
    detail_attachments = _extract_detail_pdf_attachments(detail_html, str(row["detail_url"]), parser=parser)
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
        raise ValueError("PINGLIANG_COST_INFO_ATTACHMENTS_MISSING")

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


def run_pingliang_cost_info_incremental(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: PingliangCostInfoClient | None = None,
    max_items: int | None = 5,
    actor_id: str = "pingliang-cost-info-crawler",
) -> dict[str, object]:
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or pingliang_cost_info_source_config()
    parser = config["parser"]["parsers"][PINGLIANG_COST_INFO_PARSER_VERSION]
    active_client = client or UrllibPingliangCostInfoClient()
    rows = list_pingliang_cost_info_issues(active_client, parser=parser, max_items=max_items)
    ingested = 0
    skipped_existing = 0
    cursor_source_item_key = str(rows[0]["source_item_key"]) if rows else None
    cursor_publish_date = str(rows[0]["publish_date"]) if rows and rows[0].get("publish_date") else None
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
        ingest_pingliang_cost_info_issue(
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


def _build_list_api_url(parser: dict, *, page_no: int = 1) -> str:
    params = dict(parser.get("jpaas") or {})
    params["pageNo"] = page_no
    params["pageSize"] = int(parser.get("page_size") or 20)
    return f"{parser.get('list_api_url') or PINGLIANG_LIST_API_URL}?{urlencode(params)}"


def _unit_html(payload: str) -> str:
    parsed = json.loads(payload)
    data = parsed.get("data") if isinstance(parsed.get("data"), dict) else {}
    return str(data.get("html") or "")


def _extract_list_rows(html: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    pattern = (
        r"<li\b[^>]*>.*?"
        r"<a\b(?P<attrs>[^>]*href=(?P<hquote>['\"])(?P<href>[^'\"]+)(?P=hquote)[^>]*)>"
        r"(?P<body>.*?)</a>\s*<span[^>]*>(?P<date>[^<]+)</span>"
    )
    for match in re.finditer(pattern, html, flags=re.IGNORECASE | re.DOTALL):
        attrs = match.group("attrs")
        rows.append(
            {
                "href": unescape(match.group("href")).strip(),
                "title": _title_from_anchor(attrs, match.group("body")),
                "publish_date": _normalize_text(match.group("date")),
            }
        )
    return rows


def _title_from_anchor(attrs: str, body: str) -> str:
    title_match = re.search(r"title=(?P<quote>['\"])(?P<title>.*?)(?P=quote)", attrs, flags=re.IGNORECASE | re.DOTALL)
    if title_match:
        return _normalize_text(title_match.group("title"))
    return _normalize_visible_text(body)


def _normalize_list_row(raw: dict[str, str], *, parser: dict) -> dict[str, object] | None:
    source_title = raw["title"]
    if not _looks_like_pingliang_issue(source_title, parser=parser):
        return None
    period = _extract_issue_period(source_title)
    if period is None:
        return None
    year, issue_no = period
    detail_url = urljoin(PINGLIANG_BASE_URL, raw["href"])
    source_item_key = _article_id(detail_url)
    if not source_item_key:
        return None
    return {
        "source_item_key": source_item_key,
        "title": f"平凉市{year}年第{issue_no}期建设工程材料信息价格",
        "source_title": source_title,
        "period": f"{year}年第{issue_no}期",
        "period_year": year,
        "period_issue_no": issue_no,
        "publish_date": raw["publish_date"],
        "detail_url": detail_url,
    }


def _looks_like_pingliang_issue(title: str, *, parser: dict) -> bool:
    normalized = _normalize_text(title)
    if any(keyword in normalized for keyword in parser.get("excluded_title_keywords") or []):
        return False
    if not all(keyword in normalized for keyword in parser.get("title_keywords_all") or []):
        return False
    any_keywords = parser.get("title_keywords_any") or []
    if any_keywords and not any(keyword in normalized for keyword in any_keywords):
        return False
    return _extract_issue_period(normalized) is not None


def _extract_issue_period(title: str) -> tuple[int, int] | None:
    normalized = _normalize_text(title)
    match = re.search(PINGLIANG_PERIOD_REGEX, normalized)
    if not match:
        return None
    return int(match.group("year")), _issue_number(match.group("issue"))


def _issue_number(raw_issue: str) -> int:
    if raw_issue.isdigit():
        return int(raw_issue)
    chinese = {
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
    }
    if raw_issue in chinese:
        return chinese[raw_issue]
    if raw_issue.startswith("十"):
        return 10 + chinese.get(raw_issue[1:], 0)
    if raw_issue.endswith("十"):
        return chinese.get(raw_issue[0], 1) * 10
    if "十" in raw_issue:
        head, tail = raw_issue.split("十", 1)
        return chinese.get(head, 1) * 10 + chinese.get(tail, 0)
    raise ValueError(f"INVALID_PINGLIANG_ISSUE: {raw_issue}")


def _article_id(detail_url: str) -> str | None:
    match = re.search(r"/art_[^/]*?([0-9a-f]{32})\.html", detail_url, flags=re.IGNORECASE)
    return match.group(1) if match else None


def _extract_detail_pdf_attachments(html: str, detail_url: str, *, parser: dict) -> list[dict[str, object]]:
    attachments: list[dict[str, object]] = []
    attachment_config = parser.get("attachments") if isinstance(parser.get("attachments"), dict) else {}
    for match in re.finditer(
        r"<a\b(?P<attrs>[^>]*href=(?P<quote>['\"])(?P<href>[^'\"]+)(?P=quote)[^>]*)>(?P<body>.*?)</a>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        href = unescape(match.group("href")).strip()
        if href.lower().startswith("javascript:"):
            continue
        file_name = _pdf_file_name(match.group("body"), href)
        if not file_name or not file_name.lower().endswith(".pdf"):
            continue
        if not _allowed_attachment_title(file_name, attachment_config):
            continue
        attachments.append(
            {
                "file_name": file_name,
                "url": _normalized_download_url(urljoin(detail_url, href)),
                "discovery_method": "detail_page_filtered_pdf_anchor",
            }
        )
    return attachments


def _allowed_attachment_title(file_name: str, attachment_config: dict) -> bool:
    normalized = _normalize_text(file_name)
    if any(keyword in normalized for keyword in attachment_config.get("excluded_title_keywords") or []):
        return False
    any_keywords = attachment_config.get("title_keywords_any") or []
    return not any_keywords or any(keyword in normalized for keyword in any_keywords)


def _pdf_file_name(anchor_body: str, href: str) -> str | None:
    visible = _normalize_visible_text(anchor_body)
    if visible.lower().endswith(".pdf"):
        return visible
    query_name = _download_query_file_name(href)
    if query_name and query_name.lower().endswith(".pdf"):
        return query_name
    path_name = unquote(urlsplit(href).path.rsplit("/", 1)[-1])
    if path_name.lower().endswith(".pdf"):
        return path_name
    return None


def _download_query_file_name(href: str) -> str | None:
    values = parse_qs(urlsplit(href).query).get("fileName") or []
    if not values:
        return None
    return unquote(values[0]).strip() or None


def _normalized_download_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.path.startswith("/api-gateway/jpaas-web-server/front/document/download"):
        return urlunsplit(("https", "zjj.pingliang.gov.cn", parsed.path, parsed.query, ""))
    return url


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
