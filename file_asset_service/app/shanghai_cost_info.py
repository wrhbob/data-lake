from __future__ import annotations

from datetime import UTC, datetime
import re
from typing import Protocol
from urllib import request
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from app.archive_rules import build_cost_info_business_key
from app.source_adapter import SourceRegistryHttpClient, archive_business_key_exists, require_data_source, safe_url
from app.source_registry import CostInfoAttachment, CostInfoDiscoveredItem, ingest_cost_info_registry_item
from app.storage import ObjectStore

SHANGHAI_BASE_URL = "https://ciac.zjw.sh.gov.cn"
SHANGHAI_ENTRY_URL = f"{SHANGHAI_BASE_URL}/JGBXMGCZJInterWeb/pc/#/hyxxHynr?bmCode=003002"
SHANGHAI_INTERWEB_BASE_URL = f"{SHANGHAI_BASE_URL}/JGBXMGCZJInterWeb/interWeb"
SHANGHAI_LIST_URL = f"{SHANGHAI_INTERWEB_BASE_URL}/hyxx/getHyxxList"
SHANGHAI_DOWNLOAD_URL = f"{SHANGHAI_INTERWEB_BASE_URL}/currently/bdFileDownload"
SHANGHAI_INFO_PRICE_CATEGORY_ID = "D34B56ED-CCB1-4D18-8D8D-F71BE69363AC"
SHANGHAI_COST_INFO_PARSER_VERSION = "shanghai.ciac-info-price-monthly-excel.v1"
SHANGHAI_TITLE_REGEX = r"(?P<year>20\d{2})年(?P<month>\d{1,2})月信息价"


class ShanghaiCostInfoClient(Protocol):
    def post_form_json(self, url: str, data: dict[str, object]) -> dict:
        ...

    def post_form_bytes(self, url: str, data: dict[str, object]) -> tuple[bytes, str | None]:
        ...


def shanghai_cost_info_source_config() -> dict:
    parser = {
        "scope": "shanghai_ciac_hyxx_003002_info_price_excel",
        "adapter_kind": "shanghai_excel",
        "entry_url": SHANGHAI_ENTRY_URL,
        "list_api_url": SHANGHAI_LIST_URL,
        "download_api_url": SHANGHAI_DOWNLOAD_URL,
        "list_strategy": "vue_interweb_hyxx_post_form",
        "category_code": "003002",
        "category_id": SHANGHAI_INFO_PRICE_CATEGORY_ID,
        "page_size": 20,
        "period": {
            "kind": "monthly",
            "source": "title",
            "regex": SHANGHAI_TITLE_REGEX,
        },
        "title_regex": SHANGHAI_TITLE_REGEX,
        "title_required_keywords": ["信息价"],
        "excluded_title_keywords": ["行情分析", "价格指数", "劳务信息价", "租赁", "季度"],
        "attachments": {
            "allowed": ["xls"],
            "required_any": ["xls"],
            "source": "hyxx_list_wjid",
        },
    }
    return {
        "registry_schema_version": "source_registry.v1",
        "stable": {
            "site_id": "cost_info.sh.ciac.info_price",
            "domain_type": "cost_info",
            "region_code": "310000",
            "coverage_region_code": "310000",
            "producer": "上海市建筑建材业市场管理总站",
            "publisher": "上海市建筑建材业市场管理总站",
            "publisher_scope": "province",
            "publisher_region_code": "310000",
            "publisher_name": "上海市建筑建材业市场管理总站",
            "publisher_type": "cost_station_public_institution",
            "period_kind": "monthly",
            "entry_url": SHANGHAI_ENTRY_URL,
            "periodicity": {"unit": "month", "period_key_format": "YYYY-MM"},
        },
        "price_coordinates": {
            "price_source_type": "info_price",
            "price_kind": "guidance",
            "tax_type": None,
        },
        "source_shape": {
            "publication_mode": "DIRECT_EXCEL_DOWNLOAD",
            "source_attachment_mode": "excel_only",
            "parsability": "opaque_spreadsheet",
        },
        "parser": {
            "active_parser_version": SHANGHAI_COST_INFO_PARSER_VERSION,
            "parsers": {
                SHANGHAI_COST_INFO_PARSER_VERSION: parser,
            },
        },
        "coverage_expectation": {
            "target_regions": [
                {
                    "region_code": "310000",
                    "region_name": "上海市",
                    "target_level": "province",
                    "requires_city_source": False,
                    "source_completeness_status": "province_source_only",
                    "source_audit_status": "official_cost_station_info_price_source",
                    "coverage_note": "上海市建设市场信息服务平台“人工，材料，机械信息价”栏目；仅采 003002 月度信息价 Excel 原件，行情分析、指数、劳务价等相邻栏目不混入。",
                }
            ]
        },
        "ops": {
            "enabled": True,
            "crawl_strategy_tier": "tier2_vue_interweb_excel",
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


class UrllibShanghaiCostInfoClient(SourceRegistryHttpClient):
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
            referer=SHANGHAI_ENTRY_URL,
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

    def post_form_bytes(self, url: str, data: dict[str, object]) -> tuple[bytes, str | None]:
        self._throttle()
        body = urlencode(data, doseq=True).encode("utf-8")
        req = request.Request(
            safe_url(url),
            data=body,
            headers=self._headers(content_type="application/x-www-form-urlencoded; charset=UTF-8", form_ajax=True),
            method="POST",
        )
        with request.urlopen(req, timeout=self.timeout, context=self._ssl_context) as response:
            return response.read(), response.headers.get("Content-Type")


def list_shanghai_cost_info_issues(
    client: ShanghaiCostInfoClient,
    *,
    parser: dict,
    page: int = 1,
    page_size: int | None = None,
) -> list[dict[str, object]]:
    payload = client.post_form_json(
        str(parser.get("list_api_url") or SHANGHAI_LIST_URL),
        {
            "zdId": str(parser.get("category_id") or SHANGHAI_INFO_PRICE_CATEGORY_ID),
            "pageNum": page,
            "pageSize": page_size or int(parser.get("page_size") or 20),
            "bt": "",
            "fbsjStart": "",
            "fbsjEnd": "",
        },
    )
    if int(payload.get("code") or 200) != 200:
        raise ValueError(f"SHANGHAI_COST_INFO_LIST_FAILED: {payload.get('msg')}")
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw_row in payload.get("rows") or []:
        row = _normalize_row(raw_row, parser=parser)
        if row is None:
            continue
        key = str(row["source_item_key"])
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    rows.sort(key=lambda row: (int(row["period_year"]), int(row["period_month"])), reverse=True)
    return rows


def ingest_shanghai_cost_info_issue(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: ShanghaiCostInfoClient,
    row: dict,
    actor_id: str = "shanghai-cost-info-crawler",
    task_id: str | None = None,
):
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or {}
    stable = config.get("stable") if isinstance(config.get("stable"), dict) else {}
    price_coordinates = config.get("price_coordinates") if isinstance(config.get("price_coordinates"), dict) else {}
    source_shape = config.get("source_shape") if isinstance(config.get("source_shape"), dict) else {}
    downloaded: list[CostInfoAttachment] = []
    for attachment in _row_attachments(row):
        content, content_type = client.post_form_bytes(
            SHANGHAI_DOWNLOAD_URL,
            {"id": str(attachment["file_id"])},
        )
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
        raise ValueError("SHANGHAI_COST_INFO_ATTACHMENTS_MISSING")

    item = CostInfoDiscoveredItem(
        title=str(row["title"]),
        publish_date=str(row["publish_date"]) if row.get("publish_date") else None,
        detail_url=str(row["detail_url"]),
        discovered_at=str(row["publish_date"]) if row.get("publish_date") else None,
        fetched_at=datetime.now(UTC).isoformat(),
        attachments=downloaded,
        metadata={
            "source_item_key": row["source_item_key"],
            "source_item_id": row["source_item_id"],
            "listed_title": row["title"],
            "period_kind": stable.get("period_kind") or "monthly",
            "period_year": row["period_year"],
            "period_month": row["period_month"],
            "price_kind": price_coordinates.get("price_kind") or "guidance",
            "price_source_type": price_coordinates.get("price_source_type") or "info_price",
            "tax_type": price_coordinates.get("tax_type"),
            "coverage_region_code": stable.get("coverage_region_code") or source.region_code,
            "publisher_type": stable.get("publisher_type") or "cost_station_public_institution",
            "source_attachment_mode": source_shape.get("source_attachment_mode") or "excel_only",
            "parsability": source_shape.get("parsability") or "opaque_spreadsheet",
            "publication_mode": source_shape.get("publication_mode") or "DIRECT_EXCEL_DOWNLOAD",
            "attachment_discovery_methods": _ordered_unique(
                str(attachment.get("discovery_method") or "unknown") for attachment in _row_attachments(row)
            ),
            "source_category_code": row.get("category_code"),
            "source_file_id": row.get("file_id"),
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


def run_shanghai_cost_info_incremental(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: ShanghaiCostInfoClient | None = None,
    max_pages: int = 1,
    page_size: int = 20,
    max_items: int | None = 5,
    actor_id: str = "shanghai-cost-info-crawler",
) -> dict[str, object]:
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or {}
    parser_config = (config.get("parser") or {}).get("parsers") or {}
    parser = parser_config.get(SHANGHAI_COST_INFO_PARSER_VERSION) or shanghai_cost_info_source_config()["parser"]["parsers"][
        SHANGHAI_COST_INFO_PARSER_VERSION
    ]
    crawler = client or UrllibShanghaiCostInfoClient()
    ingested = 0
    skipped_existing = 0
    scanned = 0
    for page in range(1, max_pages + 1):
        rows = list_shanghai_cost_info_issues(crawler, parser=parser, page=page, page_size=page_size)
        if not rows:
            break
        for row in rows:
            scanned += 1
            business_key = build_cost_info_business_key(
                source_id=source.source_id,
                region_code=source.region_code,
                period=str(row.get("period") or ""),
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
            ingest_shanghai_cost_info_issue(
                session,
                storage,
                source_id=source_id,
                client=crawler,
                row=row,
                actor_id=actor_id,
            )
            ingested += 1
            if max_items is not None and ingested >= max_items:
                return {
                    "source_id": source_id,
                    "scanned_count": scanned,
                    "ingested_count": ingested,
                    "skipped_existing_count": skipped_existing,
                }
    return {
        "source_id": source_id,
        "scanned_count": scanned,
        "ingested_count": ingested,
        "skipped_existing_count": skipped_existing,
    }


def _normalize_row(raw_row: object, *, parser: dict) -> dict[str, object] | None:
    if not isinstance(raw_row, dict):
        return None
    category_code = str(raw_row.get("xxlb") or "")
    if category_code != str(parser.get("category_code") or "003002"):
        return None
    if raw_row.get("ycwj"):
        return None
    file_id = str(raw_row.get("wjid") or "").strip()
    if not file_id:
        return None
    title = str(raw_row.get("xxbt") or raw_row.get("bt") or "").strip()
    match = re.fullmatch(str(parser.get("title_regex") or SHANGHAI_TITLE_REGEX), title)
    if not match:
        return None
    year = int(match.group("year"))
    month = int(match.group("month"))
    row_id = str(raw_row.get("id") or "").strip()
    if not row_id:
        return None
    period = f"{year}-{month:02d}"
    publish_date = _publish_date(raw_row)
    file_name = f"{title}.xls"
    download_url = f"{SHANGHAI_DOWNLOAD_URL}?id={file_id}"
    return {
        "source_item_key": f"{category_code}/{row_id}",
        "source_item_id": row_id,
        "title": title,
        "period": period,
        "period_year": year,
        "period_month": month,
        "publish_date": publish_date,
        "detail_url": str(parser.get("entry_url") or SHANGHAI_ENTRY_URL),
        "category_code": category_code,
        "file_id": file_id,
        "attachments": [
            {
                "file_id": file_id,
                "file_name": file_name,
                "url": download_url,
                "discovery_method": "hyxx_list_wjid",
            }
        ],
    }


def _publish_date(raw_row: dict) -> str | None:
    value = raw_row.get("fbsj") or raw_row.get("changetime") or raw_row.get("timeflag")
    if not value:
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    match = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", text)
    if match:
        return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    return None


def _row_attachments(row: dict) -> list[dict[str, object]]:
    attachments = row.get("attachments")
    return list(attachments) if isinstance(attachments, list) else []


def _ordered_unique(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
