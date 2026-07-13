from __future__ import annotations

from datetime import UTC, datetime
import json
import re
from typing import Protocol
from urllib.parse import urlencode, urljoin, urlsplit

from sqlalchemy.orm import Session

from app.archive_rules import build_cost_info_business_key
from app.source_adapter import SourceRegistryHttpClient, archive_business_key_exists, require_data_source
from app.source_registry import CostInfoAttachment, CostInfoDiscoveredItem, ingest_cost_info_registry_item
from app.storage import ObjectStore

JINAN_HOST = "http://jnxxj.jngczjxh.com:5020"
JINAN_APP_ROOT = f"{JINAN_HOST}/cj"
JINAN_ENTRY_URL = f"{JINAN_APP_ROOT}/material-wave"
JINAN_API_ROOT = f"{JINAN_APP_ROOT}/api"
JINAN_LAST_PERIOD_URL = f"{JINAN_API_ROOT}/build/period/getLastPeriodId"
JINAN_PERIOD_LIST_URL = f"{JINAN_API_ROOT}/build/period/selectPeriodList"
JINAN_DOWNLOAD_LIST_URL = f"{JINAN_API_ROOT}/build/priceVolatility/queryPublishedPageForPDF"
JINAN_COST_INFO_PARSER_VERSION = "jinan.zjj-spa-api-download-column-monthly-pdf.v1"
JINAN_PERIOD_NAME_REGEX = r"^(20\d{2})年\s*(0?[1-9]|1[0-2])月材料价格信息$"
JINAN_SOURCE_TITLE_REGEX = r"^(20\d{2})年第(0?[1-9]|1[0-2])期济南工程造价信息$"
JINAN_NORMALIZED_TITLE_REGEX = r"^(20\d{2})年(0?[1-9]|1[0-2])月济南工程造价信息$"


class JinanCostInfoClient(Protocol):
    def get_json(self, url: str, params: dict[str, object] | None = None) -> dict:
        ...

    def post_json(self, url: str, payload: dict[str, object]) -> dict:
        ...

    def get_bytes(self, url: str) -> tuple[bytes, str | None]:
        ...


def jinan_cost_info_source_config() -> dict:
    parser = {
        "adapter_kind": "jinan_pdf",
        "scope": "jinan_zjj_spa_api_download_column_monthly_pdf",
        "entry_url": JINAN_ENTRY_URL,
        "list_strategy": "vue_spa_public_api_periods",
        "download_strategy": "public_download_column_pdf_attachment",
        "period_api": {
            "last_period_url": JINAN_LAST_PERIOD_URL,
            "period_list_url": JINAN_PERIOD_LIST_URL,
            "period_name_regex": JINAN_PERIOD_NAME_REGEX,
        },
        "download_api": {
            "url": JINAN_DOWNLOAD_LIST_URL,
            "types": ["8"],
            "page_size": 20,
        },
        "period": {
            "kind": "monthly",
            "source": "normalized_download_title",
            "regex": JINAN_NORMALIZED_TITLE_REGEX,
        },
        "title_regex": JINAN_SOURCE_TITLE_REGEX,
        "normalized_title_regex": JINAN_NORMALIZED_TITLE_REGEX,
        "excluded_download_titles": ["2020人工材料机械汇总表"],
        "semantic_basis": [
            "PDF使用说明写明《济南工程造价信息》供济南市房屋建筑和市政基础设施工程参考使用。",
            "PDF使用说明写明本价格信息仅作为编制工程概预算、最高投标限价等参考使用。",
            "济南市人民政府公开答复指向济南市住房和城乡建设局官网标准造价栏目查询下载。",
        ],
        "attachments": {
            "allowed": ["pdf"],
            "required_any": ["pdf"],
            "source": "download_column_pdf_attachment",
        },
    }
    return {
        "registry_schema_version": "source_registry.v1",
        "stable": {
            "site_id": "cost_info.sd.jinan.zjj.price_info",
            "domain_type": "cost_info",
            "region_code": "370100",
            "coverage_region_code": "370100",
            "producer": "济南市住房和城乡建设局",
            "publisher": "济南市住房和城乡建设局",
            "publisher_scope": "city",
            "publisher_region_code": "370100",
            "publisher_name": "济南市住房和城乡建设局",
            "publisher_type": "official_housing_urban_rural_development",
            "period_kind": "monthly",
            "entry_url": JINAN_ENTRY_URL,
            "periodicity": {"unit": "month", "period_key_format": "YYYY-MM"},
        },
        "price_coordinates": {
            "price_source_type": "info_price",
            "price_kind": "guidance",
            "price_kind_basis": (
                "PDF使用说明: 《济南工程造价信息》发布的市场综合价格信息仅作为编制工程概预算、"
                "最高投标限价等参考使用；标题含市场综合价但实质为计价信息价。"
            ),
            "tax_type": None,
        },
        "source_shape": {
            "publication_mode": "SPA_API_DOWNLOAD_COLUMN_ATTACHMENT",
            "source_attachment_mode": "pdf_attachment",
            "parsability": "text_pdf",
        },
        "parser": {
            "active_parser_version": JINAN_COST_INFO_PARSER_VERSION,
            "parsers": {
                JINAN_COST_INFO_PARSER_VERSION: parser,
            },
        },
        "coverage_expectation": {
            "target_regions": [
                {
                    "region_code": "370100",
                    "region_name": "济南市",
                    "target_level": "city",
                    "requires_city_source": False,
                    "source_completeness_status": "city_source_only",
                    "source_audit_status": "official_city_spa_api_monthly_pdf_source",
                    "coverage_note": "济南市建设工程材料价格信息系统公开 SPA/API 源；仅采下载栏目中《济南工程造价信息》月度 PDF，固定人工材料机械汇总表等附件不混入。",
                }
            ]
        },
        "ops": {
            "enabled": True,
            "crawl_strategy_tier": "tier1_vue_spa_public_api_pdf_attachment",
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


class UrllibJinanCostInfoClient(SourceRegistryHttpClient):
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
            referer=JINAN_ENTRY_URL,
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

    def get_json(self, url: str, params: dict[str, object] | None = None) -> dict:
        query = urlencode(params or {}, doseq=True)
        target = f"{url}?{query}" if query else url
        return json.loads(self.get_text(target))


def list_jinan_cost_info_issues(
    client: JinanCostInfoClient,
    *,
    parser: dict,
    max_periods: int | None = 5,
) -> list[dict[str, object]]:
    last_period_payload = client.get_json(str(parser["period_api"]["last_period_url"]))
    if last_period_payload.get("code") != 0:
        raise ValueError(f"JINAN_LAST_PERIOD_FAILED: {last_period_payload.get('message')}")
    last_period_id = last_period_payload.get("data")
    period_payload = client.get_json(str(parser["period_api"]["period_list_url"]), {"periodId": last_period_id})
    if period_payload.get("code") != 0:
        raise ValueError(f"JINAN_PERIOD_LIST_FAILED: {period_payload.get('message')}")

    periods = []
    for raw_period in period_payload.get("data") or []:
        normalized = _normalize_period(raw_period, parser=parser)
        if normalized is not None:
            periods.append(normalized)
    periods.sort(key=lambda row: str(row["period"]), reverse=True)
    if max_periods is not None:
        periods = periods[:max_periods]

    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for period in periods:
        payload = _download_query_payload(period, parser=parser)
        download_payload = client.post_json(str(parser["download_api"]["url"]), payload)
        if download_payload.get("code") != 0:
            raise ValueError(f"JINAN_DOWNLOAD_LIST_FAILED: {download_payload.get('message')}")
        records = (download_payload.get("data") or {}).get("records") or []
        for record in records:
            row = _normalize_download_record(record, period=period, parser=parser)
            if row is None:
                continue
            key = str(row["source_item_key"])
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    rows.sort(key=lambda row: str(row["period"]), reverse=True)
    return rows


def ingest_jinan_cost_info_issue(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: JinanCostInfoClient,
    row: dict,
    actor_id: str = "jinan-cost-info-crawler",
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
        raise ValueError("JINAN_COST_INFO_ATTACHMENTS_MISSING")

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
            "period_api_id": row["period_api_id"],
            "source_title": row["source_title"],
            "listed_title": row["source_title"],
            "period_year": row["period_year"],
            "period_month": row["period_month"],
            "period_kind": stable.get("period_kind") or "monthly",
            "price_kind": price_coordinates.get("price_kind") or "guidance",
            "price_kind_basis": price_coordinates.get("price_kind_basis"),
            "price_source_type": price_coordinates.get("price_source_type") or "info_price",
            "tax_type": price_coordinates.get("tax_type"),
            "coverage_region_code": stable.get("coverage_region_code") or source.region_code,
            "publisher_type": stable.get("publisher_type") or "official_housing_urban_rural_development",
            "source_attachment_mode": source_shape.get("source_attachment_mode") or "pdf_attachment",
            "parsability": source_shape.get("parsability") or "text_pdf",
            "publication_mode": source_shape.get("publication_mode") or "SPA_API_DOWNLOAD_COLUMN_ATTACHMENT",
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


def run_jinan_cost_info_incremental(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: JinanCostInfoClient | None = None,
    max_periods: int | None = 5,
    max_items: int | None = 5,
    actor_id: str = "jinan-cost-info-crawler",
) -> dict[str, object]:
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or jinan_cost_info_source_config()
    parser = config["parser"]["parsers"][JINAN_COST_INFO_PARSER_VERSION]
    active_client = client or UrllibJinanCostInfoClient()
    rows = list_jinan_cost_info_issues(active_client, parser=parser, max_periods=max_periods)
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
        ingest_jinan_cost_info_issue(
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


def _normalize_period(raw_period: dict, *, parser: dict) -> dict[str, object] | None:
    if str(raw_period.get("statusName") or "") != "已发布":
        return None
    period_name = str(raw_period.get("periodName") or "").strip()
    match = re.search(str(parser["period_api"]["period_name_regex"]), period_name)
    if not match:
        return None
    period_id = raw_period.get("id")
    if period_id is None:
        return None
    year = int(match.group(1))
    month = int(match.group(2))
    return {
        "period_api_id": int(period_id),
        "period_name": period_name,
        "period": f"{year:04d}-{month:02d}",
        "period_year": year,
        "period_month": month,
        "start_time": raw_period.get("startTime"),
    }


def _download_query_payload(period: dict[str, object], *, parser: dict) -> dict[str, object]:
    download_api = parser.get("download_api") if isinstance(parser.get("download_api"), dict) else {}
    period_id = int(period["period_api_id"])
    return {
        "ylPeriodId": period_id,
        "periodId": period_id,
        "current": 1,
        "size": int(download_api.get("page_size") or 20),
        "types": list(download_api.get("types") or ["8"]),
    }


def _normalize_download_record(record: dict, *, period: dict[str, object], parser: dict) -> dict[str, object] | None:
    title = _strip_pdf_suffix(str(record.get("title") or record.get("attachName") or "").strip())
    if title in set(parser.get("excluded_download_titles") or []):
        return None
    match = re.search(str(parser.get("title_regex") or JINAN_SOURCE_TITLE_REGEX), title)
    if not match:
        return None
    year = int(match.group(1))
    month = int(match.group(2))
    if year != int(period["period_year"]) or month != int(period["period_month"]):
        return None
    record_id = record.get("id") or record.get("priceVolatilityAttach")
    attach_url = str(record.get("attachUrl") or "").strip()
    if record_id is None or not attach_url.lower().endswith(".pdf"):
        return None
    normalized_title = f"{year:04d}年{month:02d}月济南工程造价信息"
    file_name = str(record.get("attachName") or f"{title}.pdf").strip()
    if not file_name.lower().endswith(".pdf"):
        file_name = f"{file_name}.pdf"
    publish_date = _date_part(record.get("publishTime"))
    return {
        "source_item_key": str(record_id),
        "title": normalized_title,
        "source_title": title,
        "period": str(period["period"]),
        "period_year": year,
        "period_month": month,
        "period_api_id": period["period_api_id"],
        "publish_date": publish_date,
        "detail_url": f"{JINAN_ENTRY_URL}?periodId={period['period_api_id']}",
        "attachments": [
            {
                "file_name": file_name,
                "url": _absolute_attachment_url(attach_url),
                "discovery_method": "download_column_pdf_attachment",
            }
        ],
    }


def _row_attachments(row: dict) -> list[dict[str, object]]:
    attachments = row.get("attachments")
    if not isinstance(attachments, list):
        return []
    return [dict(attachment) for attachment in attachments if isinstance(attachment, dict)]


def _absolute_attachment_url(raw_url: str) -> str:
    if urlsplit(raw_url).scheme:
        return raw_url
    if raw_url.startswith("/cj/"):
        return f"{JINAN_HOST}{raw_url}"
    if raw_url.startswith("/"):
        return f"{JINAN_APP_ROOT}{raw_url}"
    return urljoin(f"{JINAN_APP_ROOT}/", raw_url)


def _strip_pdf_suffix(title: str) -> str:
    return re.sub(r"\.pdf$", "", title.strip(), flags=re.IGNORECASE)


def _date_part(value: object) -> str | None:
    if value is None:
        return None
    match = re.search(r"20\d{2}-\d{2}-\d{2}", str(value))
    return match.group(0) if match else None


def _ordered_unique(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
