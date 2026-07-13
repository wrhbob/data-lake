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

LANZHOU_BASE_URL = "https://zjj.lanzhou.gov.cn"
LANZHOU_LIST_URL = f"{LANZHOU_BASE_URL}/col/col13792/index.html"
LANZHOU_COST_INFO_PARSER_VERSION = "lanzhou.zjj-hanweb-issue-based-mixed-journal-pdf.v1"
LANZHOU_PERIOD_REGEX = r"(?P<period>(?P<year>20\d{2})年第(?P<issue>\d{1,2})期)"


class LanzhouCostInfoClient(Protocol):
    def get_text(self, url: str) -> str:
        ...

    def get_bytes(self, url: str) -> tuple[bytes, str | None]:
        ...


def lanzhou_cost_info_source_config() -> dict:
    parser = {
        "scope": "lanzhou_zjj_hanweb_issue_based_mixed_journal_pdf",
        "list_url": LANZHOU_LIST_URL,
        "list_strategy": "hanweb_jpage_static_datastore",
        "detail_strategy": "article_detail_pdf_anchor",
        "period": {
            "kind": "issue_based",
            "source": "normalized_title",
            "regex": LANZHOU_PERIOD_REGEX,
        },
        "title_keywords_any": ["兰州建设工程造价信息", "兰州建设工程信息价格", "兰州建设工程价格信息", "兰州市建设工程价格信息"],
        "excluded_title_keywords": ["综合材料预算指导价格", "住宅专项维修资金", "购房补贴", "档案", "施工图", "物业"],
        "attachments": {
            "allowed": ["pdf"],
            "required_any": ["pdf"],
            "source": "detail_page_pdf_anchor",
        },
        "semantic_basis": [
            "PDF封面写明《兰州建设工程造价信息》由兰州市建设工程造价站主办。",
            "2026年第1期目录同时含价格信息、市场参考价格、造价指数、社会平均成本和政策文件。",
            "综合期刊未在文档级分离价格类型，L0 不拆 PDF，不把整刊伪标为 guidance。",
        ],
    }
    return {
        "registry_schema_version": "source_registry.v1",
        "stable": {
            "site_id": "cost_info.gs.lanzhou.cost_station_journal",
            "domain_type": "cost_info",
            "region_code": "620100",
            "coverage_region_code": "620100",
            "producer": "兰州市建设工程造价站",
            "publisher": "兰州市建设工程造价站",
            "publisher_scope": "city",
            "publisher_region_code": "620100",
            "publisher_name": "兰州市建设工程造价站",
            "publisher_type": "cost_station_public_institution",
            "period_kind": "issue_based",
            "entry_url": LANZHOU_LIST_URL,
            "periodicity": {"unit": "issue", "period_key_format": "YYYY年第N期"},
        },
        "price_coordinates": {
            "price_source_type": "info_price",
            "price_kind": "unspecified",
            "price_kind_basis": (
                "兰州建设工程造价信息为综合期刊，目录含价格信息、市场参考价格、造价指数、"
                "社会平均成本和政策文件；未文档级分离，L0 不读内容拆分，价格行分类后置。"
            ),
            "tax_type": None,
        },
        "source_shape": {
            "publication_mode": "DETAIL_PAGE_ATTACHMENT",
            "source_attachment_mode": "pdf_attachment",
            "parsability": "mixed_text_pdf",
        },
        "parser": {
            "active_parser_version": LANZHOU_COST_INFO_PARSER_VERSION,
            "parsers": {
                LANZHOU_COST_INFO_PARSER_VERSION: parser,
            },
        },
        "coverage_expectation": {
            "target_regions": [
                {
                    "region_code": "620100",
                    "region_name": "兰州市",
                    "target_level": "city",
                    "requires_city_source": False,
                    "source_completeness_status": "city_source_only",
                    "source_audit_status": "official_cost_station_issue_based_mixed_journal",
                    "coverage_note": "兰州市住房和城乡建设局在线下载栏目混合下载流；仅采《兰州建设工程造价/价格信息》期号制综合 PDF，住房补贴、档案、施工图等下载项不混入。",
                }
            ]
        },
        "ops": {
            "enabled": True,
            "crawl_strategy_tier": "tier1_hanweb_static_detail_pdf",
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


class UrllibLanzhouCostInfoClient(SourceRegistryHttpClient):
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
            referer=LANZHOU_LIST_URL,
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


def list_lanzhou_cost_info_issues(
    client: LanzhouCostInfoClient,
    *,
    parser: dict,
    max_items: int | None = 5,
) -> list[dict[str, object]]:
    html = client.get_text(str(parser.get("list_url") or LANZHOU_LIST_URL))
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


def ingest_lanzhou_cost_info_issue(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: LanzhouCostInfoClient,
    row: dict,
    actor_id: str = "lanzhou-cost-info-crawler",
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
        raise ValueError("LANZHOU_COST_INFO_ATTACHMENTS_MISSING")

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


def run_lanzhou_cost_info_incremental(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: LanzhouCostInfoClient | None = None,
    max_items: int | None = 5,
    actor_id: str = "lanzhou-cost-info-crawler",
) -> dict[str, object]:
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or lanzhou_cost_info_source_config()
    parser = config["parser"]["parsers"][LANZHOU_COST_INFO_PARSER_VERSION]
    active_client = client or UrllibLanzhouCostInfoClient()
    rows = list_lanzhou_cost_info_issues(active_client, parser=parser, max_items=max_items)
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
        ingest_lanzhou_cost_info_issue(
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
        r"<li><a\b[^>]*href=(?P<hquote>['\"])(?P<href>[^'\"]+)(?P=hquote)"
        r"\s+title=(?P<tquote>['\"])(?P<title>.*?)(?P=tquote)[^>]*>.*?</a><span>(?P<date>[^<]+)</span></li>"
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
    if not _looks_like_lanzhou_journal(source_title, parser=parser):
        return None
    period = _extract_issue_period(source_title)
    if period is None:
        return None
    year, issue_no = period
    detail_url = urljoin(LANZHOU_BASE_URL, raw["href"])
    source_item_key = _article_id(detail_url)
    if not source_item_key:
        return None
    normalized_title = f"{year}年第{issue_no}期兰州建设工程造价信息"
    return {
        "source_item_key": source_item_key,
        "title": normalized_title,
        "source_title": source_title,
        "period": f"{year}年第{issue_no}期",
        "period_year": year,
        "period_issue_no": issue_no,
        "publish_date": raw["publish_date"],
        "detail_url": detail_url,
    }


def _looks_like_lanzhou_journal(title: str, *, parser: dict) -> bool:
    if any(keyword in title for keyword in parser.get("excluded_title_keywords") or []):
        return False
    normalized = re.sub(r"\s+", "", title)
    if not any(keyword in normalized for keyword in parser.get("title_keywords_any") or []):
        return False
    return _extract_issue_period(normalized) is not None


def _extract_issue_period(title: str) -> tuple[int, int] | None:
    normalized = re.sub(r"\s+", "", title)
    patterns = [
        r"(?P<year>20\d{2})年(?:度)?第(?P<issue>\d{1,2}|[一二三四五六七八九十]+)期",
        r"(?P<year>20\d{2})年第(?P<issue>\d{1,2}|[一二三四五六七八九十]+)期",
        r"(?P<year>20\d{2})\.(?P<issue>\d{1,2})兰州建设工程价格信息",
        r"兰州建设工程(?:造价信息|信息价格|价格信息)[（(](?P<year>20\d{2})年第(?P<issue>\d{1,2}|[一二三四五六七八九十]+)期[）)]",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        return int(match.group("year")), _issue_number(match.group("issue"))
    return None


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
    raise ValueError(f"INVALID_LANZHOU_ISSUE: {raw_issue}")


def _article_id(detail_url: str) -> str | None:
    match = re.search(r"art_13792_(\d+)\.html", detail_url)
    return match.group(1) if match else None


def _extract_detail_pdf_attachments(html: str, detail_url: str) -> list[dict[str, object]]:
    attachments: list[dict[str, object]] = []
    for match in re.finditer(
        r"<a\b(?P<attrs>[^>]*href=(?P<quote>['\"])(?P<href>[^'\"]+\.pdf(?:\?[^'\"]*)?|[^'\"]*downfile\.jsp\?[^'\"]+)(?P=quote)[^>]*)>(?P<body>.*?)</a>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        href = unescape(match.group("href")).strip()
        file_name = _pdf_file_name(match.group("body"), href)
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


def _pdf_file_name(anchor_body: str, href: str) -> str | None:
    visible = _normalize_visible_text(anchor_body)
    if visible.lower().endswith(".pdf"):
        return visible
    match = re.search(r"filename=([^&]+\.pdf)", href, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    return None


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
