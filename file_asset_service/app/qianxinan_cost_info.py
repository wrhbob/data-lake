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

QIANXINAN_BASE_URL = "https://www.qxn.gov.cn"
QIANXINAN_LIST_URL = (
    f"{QIANXINAN_BASE_URL}/zwgk/zfjg/zzfcxjsj_5135158/"
    "bmxxgkml_5136493/zjxx_5135167/"
)
QIANXINAN_COST_INFO_PARSER_VERSION = "qianxinan.gov-static-issue-mixed-reference-pdf.v1"
QIANXINAN_TITLE_REGEX = r"(?P<period>(?P<year>20\d{2})年第(?P<issue>\d{1,2})期)"
QIANXINAN_SOURCE_TITLE_REGEX = r"^黔西南州建设工程造价信息(?P<period>(?P<year>20\d{2})年第(?P<issue>\d{1,2})期)$"


class QianxinanCostInfoClient(Protocol):
    def get_text(self, url: str) -> str:
        ...

    def get_bytes(self, url: str) -> tuple[bytes, str | None]:
        ...


def qianxinan_cost_info_source_config() -> dict:
    parser = {
        "scope": "qianxinan_gov_static_issue_mixed_reference_pdf",
        "list_url": QIANXINAN_LIST_URL,
        "list_strategy": "static_html_single_page_article_list",
        "detail_strategy": "article_detail_pdf_anchor",
        "period": {
            "kind": "issue_based",
            "source": "title",
            "regex": QIANXINAN_TITLE_REGEX,
        },
        "source_title_regex": QIANXINAN_SOURCE_TITLE_REGEX,
        "semantic_basis": [
            "PDF封面写明《黔西南州建设工程造价信息》由黔西南州住房和城乡建设发展中心发布。",
            "PDF封面/目录写明主要建筑安装材料市场综合参考价，并含政策法规、行业动态、造价信息、附录建材信息。",
            "综合期刊未在文档级分离价格类型，L0 不读内容拆分，不把整本伪标为 guidance。",
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
            "site_id": "cost_info.gz.qianxinan.cost_station_journal",
            "domain_type": "cost_info",
            "region_code": "522300",
            "coverage_region_code": "522300",
            "producer": "黔西南州住房和城乡建设发展中心",
            "publisher": "黔西南州住房和城乡建设发展中心",
            "publisher_scope": "city",
            "publisher_region_code": "522300",
            "publisher_name": "黔西南州住房和城乡建设发展中心",
            "publisher_type": "cost_station_public_institution",
            "period_kind": "issue_based",
            "entry_url": QIANXINAN_LIST_URL,
            "periodicity": {"unit": "issue", "period_key_format": "YYYY年第N期"},
        },
        "price_coordinates": {
            "price_source_type": "info_price",
            "price_kind": "unspecified",
            "price_kind_basis": (
                "黔西南州建设工程造价信息为综合期刊，含主要建筑安装材料市场综合参考价、"
                "政策法规/行业动态、附录建材信息等；未文档级分离，L0 不读内容拆分，价格行分类后置。"
            ),
            "tax_type": None,
        },
        "source_shape": {
            "publication_mode": "DETAIL_PAGE_ATTACHMENT",
            "source_attachment_mode": "pdf_attachment",
            "parsability": "image_pdf",
        },
        "parser": {
            "active_parser_version": QIANXINAN_COST_INFO_PARSER_VERSION,
            "parsers": {
                QIANXINAN_COST_INFO_PARSER_VERSION: parser,
            },
        },
        "coverage_expectation": {
            "target_regions": [
                {
                    "region_code": "522300",
                    "region_name": "黔西南布依族苗族自治州",
                    "target_level": "city",
                    "requires_city_source": False,
                    "source_completeness_status": "city_source_only",
                    "source_audit_status": "official_cost_station_issue_based_mixed_journal",
                    "coverage_note": "黔西南州住建局造价信息栏目；本源仅采《黔西南州建设工程造价信息》期号制 PDF，L0 原件入湖不拆分。",
                }
            ]
        },
        "ops": {
            "enabled": True,
            "crawl_strategy_tier": "tier1_static_detail_pdf",
            "schedule": {"frequency": "issue"},
            "queue": {
                "max_concurrent_per_host": 1,
                "global_priority": 30,
                "min_delay_seconds": 5,
                "jitter_seconds": 3,
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


class UrllibQianxinanCostInfoClient(SourceRegistryHttpClient):
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
            referer=QIANXINAN_LIST_URL,
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


def list_qianxinan_cost_info_issues(
    client: QianxinanCostInfoClient,
    *,
    parser: dict,
    max_items: int | None = 5,
) -> list[dict[str, object]]:
    html = client.get_text(str(parser.get("list_url") or QIANXINAN_LIST_URL))
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
    rows.sort(key=lambda row: (int(row["period_year"]), int(row["period_issue_no"])), reverse=True)
    if max_items is not None:
        return rows[:max_items]
    return rows


def ingest_qianxinan_cost_info_issue(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: QianxinanCostInfoClient,
    row: dict,
    actor_id: str = "qianxinan-cost-info-crawler",
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
        raise ValueError("QIANXINAN_COST_INFO_ATTACHMENTS_MISSING")

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
            "period_issue_no": row["period_issue_no"],
            "period_kind": stable.get("period_kind") or "issue_based",
            "price_kind": price_coordinates.get("price_kind") or "unspecified",
            "price_kind_basis": price_coordinates.get("price_kind_basis"),
            "price_source_type": price_coordinates.get("price_source_type") or "info_price",
            "tax_type": price_coordinates.get("tax_type"),
            "coverage_region_code": stable.get("coverage_region_code") or source.region_code,
            "publisher_type": stable.get("publisher_type") or "cost_station_public_institution",
            "source_attachment_mode": source_shape.get("source_attachment_mode") or "pdf_attachment",
            "parsability": source_shape.get("parsability") or "image_pdf",
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


def run_qianxinan_cost_info_incremental(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: QianxinanCostInfoClient | None = None,
    max_items: int | None = 5,
    actor_id: str = "qianxinan-cost-info-crawler",
) -> dict[str, object]:
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or qianxinan_cost_info_source_config()
    parser = config["parser"]["parsers"][QIANXINAN_COST_INFO_PARSER_VERSION]
    active_client = client or UrllibQianxinanCostInfoClient()
    rows = list_qianxinan_cost_info_issues(active_client, parser=parser, max_items=max_items)
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
        ingest_qianxinan_cost_info_issue(
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


def _extract_list_rows(html: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    pattern = (
        r"<li>\s*<span>\[(?P<date>\d{4}年\d{2}月\d{2}日)\]</span>.*?"
        r"<a\b[^>]*href=(?P<hquote>['\"])(?P<href>[^'\"]+)(?P=hquote)[^>]*"
        r"title=(?P<tquote>['\"])(?P<title>.*?)(?P=tquote)[^>]*>.*?</a>"
    )
    for match in re.finditer(pattern, html, flags=re.IGNORECASE | re.DOTALL):
        rows.append(
            {
                "href": unescape(match.group("href")).strip(),
                "title": _normalize_text(match.group("title")),
                "publish_date": _normalize_date(match.group("date")),
            }
        )
    return rows


def _normalize_list_row(raw: dict[str, str], *, parser: dict) -> dict[str, object] | None:
    source_title = raw["title"]
    match = re.search(str(parser.get("source_title_regex") or QIANXINAN_SOURCE_TITLE_REGEX), source_title)
    if not match:
        return None
    year = int(match.group("year"))
    issue_no = int(match.group("issue"))
    period = f"{year}年第{issue_no}期"
    detail_url = urljoin(QIANXINAN_LIST_URL, raw["href"])
    source_item_key = _article_id(detail_url)
    if not source_item_key:
        return None
    return {
        "source_item_key": source_item_key,
        "title": source_title,
        "source_title": source_title,
        "period": period,
        "period_year": year,
        "period_issue_no": issue_no,
        "publish_date": raw["publish_date"],
        "detail_url": detail_url,
    }


def _normalize_date(date_text: str) -> str:
    match = re.search(r"(\d{4})年(\d{2})月(\d{2})日", date_text)
    if not match:
        return _normalize_text(date_text)
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


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
