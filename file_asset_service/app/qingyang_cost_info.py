from __future__ import annotations

from datetime import UTC, datetime
from html import unescape
import re
from typing import Protocol
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from sqlalchemy.orm import Session

from app.archive_rules import build_cost_info_business_key
from app.source_adapter import SourceRegistryHttpClient, archive_business_key_exists, require_data_source
from app.source_registry import CostInfoAttachment, CostInfoDiscoveredItem, ingest_cost_info_registry_item
from app.storage import ObjectStore

QINGYANG_BASE_URL = "https://zhujianju.zgqingyang.gov.cn"
QINGYANG_LIST_URL = f"{QINGYANG_BASE_URL}/zwgk/bmwj/zcwj"
QINGYANG_COST_INFO_PARSER_VERSION = "qingyang.zjj-static-issue-based-price-info-pdf.v1"
QINGYANG_PERIOD_REGEX = r"(?P<period>(?P<year>20\d{2}|二0二\d|二〇二\d)年第?(?P<issue>\d{1,2}|[一二三四五六七八九十]+)期)"


class QingyangCostInfoClient(Protocol):
    def get_text(self, url: str) -> str:
        ...

    def get_bytes(self, url: str) -> tuple[bytes, str | None]:
        ...


def qingyang_cost_info_source_config() -> dict:
    parser = {
        "adapter_kind": "qingyang_pdf",
        "scope": "qingyang_zjj_static_issue_based_price_info_pdf",
        "list_url": QINGYANG_LIST_URL,
        "list_strategy": "static_paginated_list_detail_pdf_anchor",
        "detail_strategy": "article_detail_pdf_anchor_or_data_powerurl",
        "period": {
            "kind": "issue_based",
            "source": "normalized_title",
            "regex": QINGYANG_PERIOD_REGEX,
        },
        "title_regex": QINGYANG_PERIOD_REGEX,
        "title_keywords_all": ["庆阳市", "信息价"],
        "title_keywords_any": ["材料信息价", "机械租赁信息价", "人工市场信息价"],
        "excluded_title_keywords": ["职称", "资质", "公租房", "合同备案", "报装"],
        "attachments": {
            "allowed": ["pdf"],
            "required_any": ["pdf"],
            "sources": ["detail_page_pdf_anchor", "detail_page_pdf_data_powerurl"],
        },
        "semantic_basis": [
            "庆阳市住建局部门文件政策文件栏目连续发布建设工程材料信息价、机械租赁信息价、人工市场信息价。",
            "详情页以 PDF 作为原件，部分新页在文件下载区暴露 PDF，部分旧页通过正文图片 data-powerurl 指向 PDF。",
            "本 adapter 只做 L0 原 PDF 归档，不解析 PDF 内部价格表。",
        ],
    }
    return {
        "registry_schema_version": "source_registry.v1",
        "stable": {
            "site_id": "cost_info.gs.qingyang.zjj.price_info",
            "domain_type": "cost_info",
            "region_code": "621000",
            "coverage_region_code": "621000",
            "province": "甘肃省",
            "city": "庆阳市",
            "producer": "庆阳市住房和城乡建设局",
            "publisher": "庆阳市住房和城乡建设局",
            "publisher_scope": "city",
            "publisher_region_code": "621000",
            "publisher_name": "庆阳市住房和城乡建设局",
            "publisher_type": "official_housing_urban_rural_development",
            "period_kind": "issue_based",
            "entry_url": QINGYANG_LIST_URL,
            "periodicity": {"unit": "issue", "period_key_format": "YYYY年第N期"},
        },
        "price_coordinates": {
            "price_source_type": "info_price",
            "price_kind": "guidance",
            "price_kind_basis": "部门文件连续公布建设工程材料信息价、机械租赁信息价、人工市场信息价。",
            "tax_type": None,
        },
        "source_shape": {
            "publication_mode": "DETAIL_PAGE_ATTACHMENT",
            "source_attachment_mode": "pdf_attachment",
            "parsability": "text_pdf",
        },
        "parser": {
            "active_parser_version": QINGYANG_COST_INFO_PARSER_VERSION,
            "parsers": {
                QINGYANG_COST_INFO_PARSER_VERSION: parser,
            },
        },
        "coverage_expectation": {
            "target_regions": [
                {
                    "region_code": "621000",
                    "region_name": "庆阳市",
                    "target_level": "city",
                    "requires_city_source": False,
                    "source_completeness_status": "city_source_only",
                    "source_audit_status": "official_city_zjj_issue_based_price_info_pdf",
                    "coverage_note": "庆阳市住建局部门文件栏目；采建设工程材料信息价、机械租赁信息价、人工市场信息价 PDF，职称、资质、公租房等相邻文件不混入。",
                }
            ]
        },
        "ops": {
            "enabled": True,
            "crawl_strategy_tier": "tier1_static_detail_pdf",
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


class UrllibQingyangCostInfoClient(SourceRegistryHttpClient):
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
            referer=QINGYANG_LIST_URL,
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


def list_qingyang_cost_info_issues(
    client: QingyangCostInfoClient,
    *,
    parser: dict,
    max_items: int | None = 5,
) -> list[dict[str, object]]:
    html = client.get_text(str(parser.get("list_url") or QINGYANG_LIST_URL))
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
    rows.sort(key=lambda row: (int(row["period_year"]), int(row["period_issue_no"]), str(row["publish_date"])), reverse=True)
    if max_items is not None:
        return rows[:max_items]
    return rows


def ingest_qingyang_cost_info_issue(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: QingyangCostInfoClient,
    row: dict,
    actor_id: str = "qingyang-cost-info-crawler",
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
        raise ValueError("QINGYANG_COST_INFO_ATTACHMENTS_MISSING")

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
            "price_subtype": row.get("price_subtype"),
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


def run_qingyang_cost_info_incremental(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: QingyangCostInfoClient | None = None,
    max_items: int | None = 5,
    actor_id: str = "qingyang-cost-info-crawler",
) -> dict[str, object]:
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or qingyang_cost_info_source_config()
    parser = config["parser"]["parsers"][QINGYANG_COST_INFO_PARSER_VERSION]
    active_client = client or UrllibQingyangCostInfoClient()
    rows = list_qingyang_cost_info_issues(active_client, parser=parser, max_items=max_items)
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
        ingest_qingyang_cost_info_issue(
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
            r"<a\b(?P<attrs>[^>]*href=(?P<hquote>['\"])(?P<href>[^'\"]+)(?P=hquote)[^>]*)>"
            r"(?P<body>.*?)</a>",
            li_html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not anchor:
            continue
        date = re.search(
            r"<span\b[^>]*class=(?P<squote>['\"])[^'\"]*\bdateRight\b[^'\"]*(?P=squote)[^>]*>(?P<date>.*?)</span>",
            li_html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not date:
            continue
        rows.append(
            {
                "href": unescape(anchor.group("href")).strip(),
                "title": _title_from_anchor(anchor.group("attrs"), anchor.group("body")),
                "publish_date": _date_from_text(date.group("date")),
            }
        )
    return rows


def _title_from_anchor(attrs: str, body: str) -> str:
    title_match = re.search(r"title=(?P<quote>['\"])(?P<title>.*?)(?P=quote)", attrs, flags=re.IGNORECASE | re.DOTALL)
    if title_match:
        title = _normalize_text(title_match.group("title"))
        title = re.sub(r"^标题：", "", title).strip()
        title = re.split(r"\s+点击数：|\s+发表时间：", title, maxsplit=1)[0].strip()
        if title:
            return title
    return _normalize_visible_text(body)


def _normalize_list_row(raw: dict[str, str], *, parser: dict) -> dict[str, object] | None:
    source_title = raw["title"]
    if not _looks_like_qingyang_issue(source_title, parser=parser):
        return None
    period = _extract_issue_period(source_title)
    if period is None:
        return None
    year, issue_no = period
    detail_url = urljoin(QINGYANG_BASE_URL, raw["href"])
    source_item_key = _article_id(detail_url)
    if not source_item_key:
        return None
    price_subtype = _price_subtype(source_title)
    normalized_suffix = (
        "建设工程人工市场信息价"
        if price_subtype == "labor"
        else "建设工程材料信息价和机械租赁信息价"
    )
    return {
        "source_item_key": source_item_key,
        "title": f"庆阳市{year}年第{issue_no}期{normalized_suffix}",
        "source_title": source_title,
        "period": f"{year}年第{issue_no}期",
        "period_year": year,
        "period_issue_no": issue_no,
        "price_subtype": price_subtype,
        "publish_date": raw["publish_date"],
        "detail_url": detail_url,
    }


def _looks_like_qingyang_issue(title: str, *, parser: dict) -> bool:
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
    match = re.search(QINGYANG_PERIOD_REGEX, normalized)
    if not match:
        return None
    return _year_number(match.group("year")), _issue_number(match.group("issue"))


def _year_number(raw_year: str) -> int:
    if raw_year.isdigit():
        return int(raw_year)
    normalized = raw_year.replace("〇", "0").replace("零", "0")
    digits = {"二": "2", "0": "0", "一": "1", "三": "3", "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9"}
    value = "".join(digits.get(char, char) for char in normalized)
    if value.isdigit():
        return int(value)
    raise ValueError(f"INVALID_QINGYANG_YEAR: {raw_year}")


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
    raise ValueError(f"INVALID_QINGYANG_ISSUE: {raw_issue}")


def _price_subtype(title: str) -> str:
    return "labor" if "人工市场信息价" in title else "material_and_machine"


def _article_id(detail_url: str) -> str | None:
    match = re.search(r"/content_(?P<id>\d+)", detail_url, flags=re.IGNORECASE)
    return match.group("id") if match else None


def _extract_detail_pdf_attachments(html: str, detail_url: str) -> list[dict[str, object]]:
    attachments: list[dict[str, object]] = []
    seen_urls: set[str] = set()
    for match in re.finditer(
        r"<a\b(?P<attrs>[^>]*href=(?P<quote>['\"])(?P<href>[^'\"]+\.pdf)(?P=quote)[^>]*)>(?P<body>.*?)</a>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        href = unescape(match.group("href")).strip()
        url = _safe_pdf_url(urljoin(detail_url, href))
        if url in seen_urls:
            continue
        seen_urls.add(url)
        attachments.append(
            {
                "file_name": _pdf_file_name(match.group("body"), href),
                "url": url,
                "discovery_method": "detail_page_pdf_anchor",
            }
        )
    if attachments:
        return attachments
    for match in re.finditer(
        r"data-powerurl=(?P<quote>['\"])(?P<href>[^'\"]+\.pdf)(?P=quote)",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        href = unescape(match.group("href")).strip()
        url = _safe_pdf_url(urljoin(detail_url, href))
        if url in seen_urls:
            continue
        seen_urls.add(url)
        attachments.append(
            {
                "file_name": _pdf_file_name("", href),
                "url": url,
                "discovery_method": "detail_page_pdf_data_powerurl",
            }
        )
    return attachments


def _pdf_file_name(anchor_body: str, href: str) -> str:
    visible = _normalize_visible_text(anchor_body)
    if visible.lower().endswith(".pdf"):
        return visible
    path_name = unquote(urlsplit(href).path.rsplit("/", 1)[-1])
    if path_name.lower().endswith(".pdf"):
        return path_name
    return "庆阳市建设工程信息价.pdf"


def _safe_pdf_url(url: str) -> str:
    parsed = urlsplit(url)
    safe_path = quote(unquote(parsed.path), safe="/%")
    return urlunsplit((parsed.scheme, parsed.netloc, safe_path, parsed.query, parsed.fragment))


def _date_from_text(text: str) -> str:
    match = re.search(r"(20\d{2}-\d{1,2}-\d{1,2})", _normalize_visible_text(text))
    return match.group(1) if match else ""


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
