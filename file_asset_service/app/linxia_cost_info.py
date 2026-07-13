from __future__ import annotations

from datetime import UTC, datetime
from html import unescape
import json
import re
from typing import Protocol
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlsplit

from sqlalchemy.orm import Session

from app.archive_rules import build_cost_info_business_key
from app.source_adapter import SourceRegistryHttpClient, archive_business_key_exists, require_data_source
from app.source_registry import CostInfoAttachment, CostInfoDiscoveredItem, ingest_cost_info_registry_item
from app.storage import ObjectStore

LINXIA_BASE_URL = "https://www.linxia.gov.cn"
LINXIA_LIST_URL = f"{LINXIA_BASE_URL}/lxz/zwgk/bmxxgkpt/lxzzjj/fdzdgknr/bmwj/index.html"
LINXIA_LIST_API_URL = f"{LINXIA_BASE_URL}/api-gateway/jpaas-publish-server/front/page/build/unit"
LINXIA_COST_INFO_PARSER_VERSION = "linxia.gov-jpaas-bimonthly-material-info-price-xlsx.v1"
LINXIA_PERIOD_REGEX = r"(?P<period>(?P<year>20\d{2})年(?P<start_month>\d{1,2})[-—~－](?P<end_month>\d{1,2})月)"


class LinxiaCostInfoClient(Protocol):
    def get_text(self, url: str) -> str:
        ...

    def get_bytes(self, url: str) -> tuple[bytes, str | None]:
        ...


def linxia_cost_info_source_config() -> dict:
    parser = {
        "scope": "linxia_gov_jpaas_bimonthly_material_info_price_xlsx",
        "adapter_kind": "linxia_xlsx",
        "list_url": LINXIA_LIST_URL,
        "list_api_url": LINXIA_LIST_API_URL,
        "list_strategy": "jpaas_unit_list_detail_xlsx_anchor",
        "detail_strategy": "article_detail_xlsx_anchor",
        "jpaas": {
            "parseType": "bulidstatic",
            "webId": "a9eaec9548d54f9887818db54d9643c3",
            "tplSetId": "d635ce898d484e87a2711b8e8ec2017d",
            "pageType": "column",
            "tagId": "法定主动公开内容第一个栏目list",
            "editType": "null",
            "pageId": "90ff8a93ea0746c792f655cbf01621a2",
        },
        "page_size": 20,
        "period": {
            "kind": "bimonthly",
            "source": "title",
            "regex": LINXIA_PERIOD_REGEX,
        },
        "title_regex": LINXIA_PERIOD_REGEX,
        "title_keywords_all": ["关于发布临夏州", "建设工程"],
        "excluded_title_keywords": ["法治", "生态环境", "责任清单", "安全生产", "物业", "征集", "消防"],
        "attachments": {
            "allowed": ["xlsx"],
            "required_any": ["xlsx"],
            "source": "detail_page_xlsx_anchor",
        },
        "semantic_basis": [
            "临夏州住建局部门文件详情页正文写明发布建设工程一类材料信息价。",
            "正文写明该信息价并非政府定价或者政府指导价，仅作为编制工程概预算、招投标控制价等的计价参考。",
            "附件为 XLSX 原件，按双月周期发布。",
        ],
    }
    return {
        "registry_schema_version": "source_registry.v1",
        "stable": {
            "site_id": "cost_info.gs.linxia.zjj.price_info",
            "domain_type": "cost_info",
            "region_code": "622900",
            "coverage_region_code": "622900",
            "province": "甘肃省",
            "city": "临夏回族自治州",
            "producer": "临夏州住建局造价咨询服务中心",
            "publisher": "临夏州住房和城乡建设局",
            "publisher_scope": "prefecture",
            "publisher_region_code": "622900",
            "publisher_name": "临夏州住房和城乡建设局",
            "publisher_type": "official_housing_urban_rural_development",
            "period_kind": "bimonthly",
            "entry_url": LINXIA_LIST_URL,
            "periodicity": {"unit": "two_months", "period_key_format": "YYYY年M-M月"},
        },
        "price_coordinates": {
            "price_source_type": "info_price",
            "price_kind": "guidance",
            "price_kind_basis": "正文写明并非政府定价或者政府指导价，仅作为编制建设工程概预算、招投标控制价等的计价参考。",
            "tax_type": None,
        },
        "source_shape": {
            "publication_mode": "DETAIL_PAGE_ATTACHMENT",
            "source_attachment_mode": "xlsx_attachment",
            "parsability": "opaque_spreadsheet",
        },
        "parser": {
            "active_parser_version": LINXIA_COST_INFO_PARSER_VERSION,
            "parsers": {
                LINXIA_COST_INFO_PARSER_VERSION: parser,
            },
        },
        "coverage_expectation": {
            "target_regions": [
                {
                    "region_code": "622900",
                    "region_name": "临夏回族自治州",
                    "target_level": "prefecture",
                    "requires_city_source": False,
                    "source_completeness_status": "prefecture_source_only",
                    "source_audit_status": "official_prefecture_zjj_bimonthly_material_info_price_xlsx",
                    "coverage_note": "临夏州住建局部门文件栏目；仅采标题含“关于发布临夏州YYYY年M-M月建设工程”的一类材料信息价 XLSX 附件，法治、环保、物业、安全生产等相邻文件不混入。",
                }
            ]
        },
        "ops": {
            "enabled": True,
            "crawl_strategy_tier": "tier2_jpaas_detail_xlsx",
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


class UrllibLinxiaCostInfoClient(SourceRegistryHttpClient):
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
            referer=LINXIA_LIST_URL,
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


def list_linxia_cost_info_issues(
    client: LinxiaCostInfoClient,
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
    rows.sort(
        key=lambda row: (int(row["period_year"]), int(row["period_start_month"]), int(row["period_end_month"])),
        reverse=True,
    )
    if max_items is not None:
        return rows[:max_items]
    return rows


def ingest_linxia_cost_info_issue(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: LinxiaCostInfoClient,
    row: dict,
    actor_id: str = "linxia-cost-info-crawler",
    task_id: str | None = None,
):
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or {}
    stable = config.get("stable") if isinstance(config.get("stable"), dict) else {}
    price_coordinates = config.get("price_coordinates") if isinstance(config.get("price_coordinates"), dict) else {}
    source_shape = config.get("source_shape") if isinstance(config.get("source_shape"), dict) else {}
    detail_html = client.get_text(str(row["detail_url"]))
    detail_attachments = _extract_detail_xlsx_attachments(detail_html, str(row["detail_url"]))
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
                content_type=content_type or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        )
    if not downloaded:
        raise ValueError("LINXIA_COST_INFO_ATTACHMENTS_MISSING")

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
            "publisher_scope": stable.get("publisher_scope") or "prefecture",
            "publisher_type": stable.get("publisher_type") or "official_housing_urban_rural_development",
            "publisher_region_code": stable.get("publisher_region_code") or source.region_code,
            "source_attachment_mode": source_shape.get("source_attachment_mode") or "xlsx_attachment",
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


def run_linxia_cost_info_incremental(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: LinxiaCostInfoClient | None = None,
    max_items: int | None = 5,
    actor_id: str = "linxia-cost-info-crawler",
) -> dict[str, object]:
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or linxia_cost_info_source_config()
    parser = config["parser"]["parsers"][LINXIA_COST_INFO_PARSER_VERSION]
    active_client = client or UrllibLinxiaCostInfoClient()
    rows = list_linxia_cost_info_issues(active_client, parser=parser, max_items=max_items)
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
        ingest_linxia_cost_info_issue(
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


def _build_list_api_url(parser: dict, *, page_no: int = 1) -> str:
    params = dict(parser.get("jpaas") or {})
    params["pageNo"] = page_no
    params["pageSize"] = int(parser.get("page_size") or 20)
    return f"{parser.get('list_api_url') or LINXIA_LIST_API_URL}?{urlencode(params)}"


def _unit_html(payload: str) -> str:
    parsed = json.loads(payload)
    data = parsed.get("data") if isinstance(parsed.get("data"), dict) else {}
    return str(data.get("html") or "")


def _extract_list_rows(html: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    pattern = (
        r"<li\b[^>]*class=(?P<liquote>['\"])[^'\"]*\bcf\b[^'\"]*(?P=liquote)[^>]*>.*?"
        r"<a\b[^>]*href=(?P<hquote>['\"])(?P<href>[^'\"]+)(?P=hquote)[^>]*>(?P<title>.*?)</a>.*?"
        r"<span\b[^>]*class=(?P<squote>['\"])[^'\"]*\bfr\b[^'\"]*(?P=squote)[^>]*>(?P<date>[^<]+)</span>"
    )
    for match in re.finditer(pattern, html, flags=re.IGNORECASE | re.DOTALL):
        rows.append(
            {
                "href": unescape(match.group("href")).strip(),
                "title": _normalize_visible_text(match.group("title")),
                "publish_date": _normalize_text(match.group("date")),
            }
        )
    return rows


def _normalize_list_row(raw: dict[str, str], *, parser: dict) -> dict[str, object] | None:
    source_title = raw["title"]
    if not _looks_like_linxia_period(source_title, parser=parser):
        return None
    period = _extract_bimonthly_period(source_title)
    if period is None:
        return None
    year, start_month, end_month = period
    detail_url = urljoin(LINXIA_BASE_URL, raw["href"])
    source_item_key = _article_id(detail_url)
    if not source_item_key:
        return None
    return {
        "source_item_key": source_item_key,
        "title": f"临夏州{year}年{start_month}-{end_month}月建设工程一类材料信息价",
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


def _looks_like_linxia_period(title: str, *, parser: dict) -> bool:
    normalized = _normalize_title_for_match(title)
    if any(keyword in normalized for keyword in parser.get("excluded_title_keywords") or []):
        return False
    if not all(keyword in normalized for keyword in parser.get("title_keywords_all") or []):
        return False
    return _extract_bimonthly_period(normalized) is not None


def _extract_bimonthly_period(title: str) -> tuple[int, int, int] | None:
    normalized = _normalize_title_for_match(title)
    match = re.search(LINXIA_PERIOD_REGEX, normalized)
    if not match:
        return None
    return int(match.group("year")), int(match.group("start_month")), int(match.group("end_month"))


def _extract_detail_xlsx_attachments(html: str, detail_url: str) -> list[dict[str, object]]:
    attachments: list[dict[str, object]] = []
    for match in re.finditer(
        r"<a\b(?P<attrs>[^>]*href=(?P<quote>['\"])(?P<href>[^'\"]+)(?P=quote)[^>]*)>(?P<body>.*?)</a>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        href = unescape(match.group("href")).strip()
        if href.lower().startswith("javascript:"):
            continue
        file_name = _spreadsheet_file_name(match.group("body"), href)
        if not file_name or not file_name.lower().endswith((".xls", ".xlsx")):
            continue
        attachments.append(
            {
                "file_name": file_name,
                "url": urljoin(detail_url, href),
                "discovery_method": "detail_page_xlsx_anchor",
            }
        )
    return attachments


def _spreadsheet_file_name(anchor_body: str, href: str) -> str | None:
    visible = _normalize_visible_text(anchor_body)
    if visible.lower().endswith((".xls", ".xlsx")):
        return visible
    query_name = _download_query_file_name(href)
    if query_name:
        return query_name
    path_name = unquote(urlsplit(href).path.rsplit("/", 1)[-1])
    if path_name.lower().endswith((".xls", ".xlsx")):
        return path_name
    return None


def _download_query_file_name(href: str) -> str | None:
    values = parse_qs(urlsplit(href).query).get("fileName") or []
    if not values:
        return None
    return unquote(values[0]).strip() or None


def _article_id(detail_url: str) -> str | None:
    match = re.search(r"/art_[^/]*?([0-9a-f]{32})\.html", detail_url, flags=re.IGNORECASE)
    return match.group(1) if match else None


def _normalize_title_for_match(text: str) -> str:
    normalized = _normalize_text(text).replace("－", "-").replace("~", "—").replace("-", "—")
    return normalized


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
