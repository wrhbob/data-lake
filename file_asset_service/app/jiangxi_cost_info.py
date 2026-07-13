from __future__ import annotations

from datetime import UTC, datetime
import json
import os
import re
from typing import Protocol
from urllib.parse import unquote, urljoin, urlsplit

from sqlalchemy.orm import Session

from app.archive_rules import build_cost_info_business_key
from app.source_adapter import SourceRegistryHttpClient, archive_business_key_exists, require_data_source
from app.source_registry import CostInfoAttachment, CostInfoDiscoveredItem, ingest_cost_info_registry_item
from app.storage import ObjectStore

JIANGXI_BASE_URL = "http://zjt.jiangxi.gov.cn"
JIANGXI_LIST_URL = f"{JIANGXI_BASE_URL}/jxszfhcxjst/gqcyc/pc/list.html"
JIANGXI_QUERY_LIST_URL = f"{JIANGXI_BASE_URL}/queryList"
JIANGXI_COST_INFO_PARSER_VERSION = "jiangxi.zjt-material-reference-issue-pdf-query-list.v1"
JIANGXI_TITLE_REGEX = r"江西省材料价格参考信息(?P<period>(?P<year>20\d{2})年第(?P<issue>\d{1,2})期)"


class JiangxiCostInfoClient(Protocol):
    def post_form_json(self, url: str, data: dict[str, object]) -> dict:
        ...

    def get_bytes(self, url: str) -> tuple[bytes, str | None]:
        ...


def jiangxi_cost_info_source_config() -> dict:
    parser = {
        "scope": "jiangxi_zjt_material_reference_issue_pdf_query_list",
        "list_url": JIANGXI_LIST_URL,
        "list_api_url": JIANGXI_QUERY_LIST_URL,
        "list_strategy": "ucap_query_list_article_files",
        "website_code": "jxszfhcxjst",
        "channel_code": "gqcyc",
        "page_size": 100,
        "period": {
            "kind": "issue_based",
            "source": "title",
            "regex": JIANGXI_TITLE_REGEX,
        },
        "title_regex": JIANGXI_TITLE_REGEX,
        "title_required_keywords": ["江西省材料价格参考信息"],
        "excluded_channels": [
            "江西省各设区市建筑人工机械设备信息参考价",
            "工程案例指标分析",
        ],
        "attachments": {
            "allowed": ["pdf"],
            "required_any": ["pdf"],
            "source": "query_list_article_files",
        },
    }
    return {
        "registry_schema_version": "source_registry.v1",
        "stable": {
            "site_id": "cost_info.jx.zjt.material_reference",
            "domain_type": "cost_info",
            "region_code": "360000",
            "coverage_region_code": "360000",
            "producer": "江西省城镇发展服务中心",
            "publisher": "江西省城镇发展服务中心",
            "publisher_scope": "province",
            "publisher_region_code": "360000",
            "publisher_name": "江西省城镇发展服务中心",
            "publisher_type": "cost_station_public_institution",
            "period_kind": "issue_based",
            "entry_url": JIANGXI_LIST_URL,
            "periodicity": {"unit": "issue", "period_key_format": "YYYY年第N期"},
        },
        "price_coordinates": {
            "price_source_type": "info_price",
            "price_kind": "guidance",
            "tax_type": None,
        },
        "source_shape": {
            "publication_mode": "DIRECT_PDF",
            "source_attachment_mode": "pdf_only",
            "parsability": "text_pdf",
        },
        "parser": {
            "active_parser_version": JIANGXI_COST_INFO_PARSER_VERSION,
            "parsers": {
                JIANGXI_COST_INFO_PARSER_VERSION: parser,
            },
        },
        "coverage_expectation": {
            "target_regions": [
                {
                    "region_code": "360000",
                    "region_name": "江西省",
                    "target_level": "province",
                    "requires_city_source": False,
                    "source_completeness_status": "province_source_only",
                    "source_audit_status": "official_cost_station_material_reference_source",
                    "coverage_note": "江西省住建厅工程造价专题“各期常用材料价格参考信息”栏目；本源仅采《江西省材料价格参考信息》PDF，人工机械设备参考价和案例指标流不混入。",
                }
            ]
        },
        "ops": {
            "enabled": True,
            "crawl_strategy_tier": "tier2_ucap_query_list_pdf",
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


class UrllibJiangxiCostInfoClient(SourceRegistryHttpClient):
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
            referer=JIANGXI_LIST_URL,
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


def list_jiangxi_cost_info_issues(
    client: JiangxiCostInfoClient,
    *,
    parser: dict,
    page: int = 1,
    page_size: int | None = None,
) -> list[dict[str, object]]:
    payload = client.post_form_json(
        str(parser.get("list_api_url") or JIANGXI_QUERY_LIST_URL),
        {
            "current": page,
            "pageSize": page_size or int(parser.get("page_size") or 100),
            "webSiteCode": str(parser.get("website_code") or "jxszfhcxjst"),
            "channelCode": str(parser.get("channel_code") or "gqcyc"),
        },
    )
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for result in data.get("results") or []:
        source = result.get("source") if isinstance(result, dict) else None
        row = _normalize_query_result(source if isinstance(source, dict) else result)
        if row is None:
            continue
        key = str(row["source_item_key"])
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    rows.sort(key=lambda row: (int(row["period_year"]), int(row["period_issue_no"])), reverse=True)
    return rows


def ingest_jiangxi_cost_info_issue(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: JiangxiCostInfoClient,
    row: dict,
    actor_id: str = "jiangxi-cost-info-crawler",
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
        raise ValueError("JIANGXI_COST_INFO_ATTACHMENTS_MISSING")

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
            "listed_title": row["title"],
            "period_kind": stable.get("period_kind") or "issue_based",
            "price_kind": price_coordinates.get("price_kind") or "guidance",
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


def run_jiangxi_cost_info_incremental(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: JiangxiCostInfoClient | None = None,
    max_pages: int = 1,
    page_size: int = 100,
    max_items: int | None = 5,
    actor_id: str = "jiangxi-cost-info-crawler",
) -> dict[str, object]:
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or jiangxi_cost_info_source_config()
    parser = config["parser"]["parsers"][JIANGXI_COST_INFO_PARSER_VERSION]
    active_client = client or UrllibJiangxiCostInfoClient()
    ingested = 0
    skipped_existing = 0
    cursor_source_item_key: str | None = None
    for page in range(1, max_pages + 1):
        rows = list_jiangxi_cost_info_issues(active_client, parser=parser, page=page, page_size=page_size)
        if not rows:
            break
        for row in rows:
            if cursor_source_item_key is None:
                cursor_source_item_key = str(row["source_item_key"])
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
            ingest_jiangxi_cost_info_issue(
                session,
                storage,
                source_id=source_id,
                client=active_client,
                row=row,
                actor_id=actor_id,
            )
            ingested += 1
            if max_items is not None and ingested >= max_items:
                return {
                    "source_id": source_id,
                    "ingested_count": ingested,
                    "skipped_existing_count": skipped_existing,
                    "cursor_source_item_key": cursor_source_item_key,
                }
    return {
        "source_id": source_id,
        "ingested_count": ingested,
        "skipped_existing_count": skipped_existing,
        "cursor_source_item_key": cursor_source_item_key,
    }


def _normalize_query_result(source: dict | None) -> dict[str, object] | None:
    if not isinstance(source, dict):
        return None
    row_id = str(source.get("id") or "").strip()
    title = _normalize_text(source.get("title"))
    match = re.fullmatch(JIANGXI_TITLE_REGEX, title)
    if not row_id or not match:
        return None
    attachments = _article_file_attachments(source.get("articleFiles"))
    if not attachments:
        return None
    issue_no = int(match.group("issue"))
    return {
        "source_item_key": f"gqcyc/{row_id}",
        "title": title,
        "period": _compact_period(match.group("period")),
        "period_year": int(match.group("year")),
        "period_issue_no": issue_no,
        "publish_date": _date_from_pub_date(source.get("pubDate")),
        "detail_url": _detail_url(source),
        "content_source": _normalize_text(source.get("contentSource")),
        "attachments": attachments,
    }


def _article_file_attachments(value: object) -> list[dict[str, str]]:
    files = _json_list(value)
    attachments: list[dict[str, str]] = []
    seen: set[str] = set()
    for file in files:
        if not isinstance(file, dict):
            continue
        raw_url = str(file.get("url") or file.get("filePath") or "").strip()
        if not raw_url or not urlsplit(raw_url).path.lower().endswith(".pdf"):
            continue
        url = urljoin(JIANGXI_BASE_URL, raw_url)
        if url in seen:
            continue
        seen.add(url)
        file_name = _normalize_text(file.get("fileName")) or _file_name_from_url(url)
        attachments.append(
            {
                "file_name": file_name,
                "url": url,
                "discovery_method": "query_list_article_files",
            }
        )
    return attachments


def _detail_url(source: dict) -> str:
    urls = _json_dict(source.get("urls"))
    pc_url = str(urls.get("pc") or "").strip()
    return urljoin(JIANGXI_BASE_URL, pc_url) if pc_url else JIANGXI_LIST_URL


def _json_list(value: object) -> list:
    if isinstance(value, list):
        return value
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return []
    return loaded if isinstance(loaded, list) else []


def _json_dict(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _date_from_pub_date(value: object) -> str | None:
    match = re.match(r"(?P<date>20\d{2}-\d{2}-\d{2})", str(value or "").strip())
    return match.group("date") if match else None


def _normalize_text(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _compact_period(period: str) -> str:
    return re.sub(r"\s+", "", period)


def _file_name_from_url(url: str) -> str:
    name = unquote(os.path.basename(urlsplit(url).path))
    return name if name else "江西省材料价格参考信息.pdf"


def _row_attachments(row: dict) -> list[dict[str, object]]:
    return list(row.get("attachments") or [])


def _ordered_unique(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
