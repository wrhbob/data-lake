from __future__ import annotations

from datetime import UTC, datetime
import os
import re
from typing import Protocol
from urllib.parse import quote, urljoin, urlsplit

from sqlalchemy.orm import Session

from app.archive_rules import build_cost_info_business_key
from app.source_adapter import SourceRegistryHttpClient, archive_business_key_exists, require_data_source
from app.source_registry import CostInfoAttachment, CostInfoDiscoveredItem, ingest_cost_info_registry_item
from app.storage import ObjectStore

HUBEI_BASE_URL = "http://bzcljg.hbcic.net.cn:8091"
HUBEI_ENTRY_URL = f"{HUBEI_BASE_URL}/gcms-busi/#/materialMessageInquire/periodical?type=self"
HUBEI_JOURNAL_LIST_API_URL = f"{HUBEI_BASE_URL}/material-monitor/bJournal/journalList"
HUBEI_JOURNAL_YEAR_API_URL = f"{HUBEI_BASE_URL}/material-monitor/bJournal/journalYear"
HUBEI_JOURNAL_CITY_API_URL = f"{HUBEI_BASE_URL}/material-monitor/bJournal/queryJournalCity?journalType=1"
HUBEI_COST_INFO_PARSER_VERSION = "hubei.hbcic-periodical-issue-pdf-list.v1"
HUBEI_MAIN_JOURNAL_NAME = "湖北工程造价管理"
HUBEI_TITLE_REGEX = r"(?P<period>(?P<year>20\d{2})年第(?P<issue>\d{1,2})期)《湖北工程造价管理》"


class HubeiCostInfoClient(Protocol):
    def post_json(self, url: str, payload: dict[str, object]) -> dict:
        ...

    def get_bytes(self, url: str) -> tuple[bytes, str | None]:
        ...


def hubei_cost_info_source_config() -> dict:
    parser = {
        "scope": "hubei_hbcic_periodical_issue_pdf_list",
        "adapter_kind": "hubei_pdf",
        "list_url": HUBEI_ENTRY_URL,
        "list_api_url": HUBEI_JOURNAL_LIST_API_URL,
        "year_api_url": HUBEI_JOURNAL_YEAR_API_URL,
        "city_api_url": HUBEI_JOURNAL_CITY_API_URL,
        "list_strategy": "spa_json_post_journal_list",
        "journal_type": 1,
        "page_size": 100,
        "period": {
            "kind": "issue_based",
            "source": "pushYear_pushPeriod",
            "regex": HUBEI_TITLE_REGEX,
        },
        "title_regex": HUBEI_TITLE_REGEX,
        "title_required_keywords": [HUBEI_MAIN_JOURNAL_NAME],
        "attachments": {
            "allowed": ["pdf"],
            "required_any": ["pdf"],
            "source": "journalUrl",
        },
    }
    return {
        "registry_schema_version": "source_registry.v1",
        "stable": {
            "site_id": "cost_info.hb.hbcic.periodical",
            "domain_type": "cost_info",
            "region_code": "420000",
            "coverage_region_code": "420000",
            "producer": "湖北省建设工程标准定额管理总站",
            "publisher": "湖北省建设工程标准定额管理总站",
            "publisher_scope": "province",
            "publisher_region_code": "420000",
            "publisher_name": "湖北省建设工程标准定额管理总站",
            "publisher_type": "cost_station_public_institution",
            "period_kind": "issue_based",
            "entry_url": HUBEI_ENTRY_URL,
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
            "active_parser_version": HUBEI_COST_INFO_PARSER_VERSION,
            "parsers": {
                HUBEI_COST_INFO_PARSER_VERSION: parser,
            },
        },
        "coverage_expectation": {
            "target_regions": [
                {
                    "region_code": "420000",
                    "region_name": "湖北省",
                    "target_level": "province",
                    "requires_city_source": False,
                    "source_completeness_status": "province_source_only",
                    "source_audit_status": "official_cost_station_periodical_source",
                    "coverage_note": "湖北造价业务管理系统《湖北工程造价管理》期刊源；列表公开 PDF 原件，按期号制 guidance 入湖。",
                }
            ]
        },
        "ops": {
            "enabled": True,
            "crawl_strategy_tier": "tier2_spa_json_pdf_list",
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


class UrllibHubeiCostInfoClient(SourceRegistryHttpClient):
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
            referer=HUBEI_ENTRY_URL,
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


def list_hubei_cost_info_issues(
    client: HubeiCostInfoClient,
    *,
    parser: dict,
    page: int = 1,
    page_size: int | None = None,
) -> list[dict[str, object]]:
    payload = client.post_json(
        str(parser.get("list_api_url") or HUBEI_JOURNAL_LIST_API_URL),
        {
            "pushYear": "",
            "region": "",
            "pageSize": page_size or int(parser.get("page_size") or 100),
            "pageNum": page,
            "journalType": int(parser.get("journal_type") or 1),
        },
    )
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw_row in data.get("records") or []:
        row = _normalize_list_row(raw_row)
        if row is None:
            continue
        key = str(row["source_item_key"])
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    rows.sort(key=lambda row: (int(row["period_year"]), int(row["period_issue_no"])), reverse=True)
    return rows


def ingest_hubei_cost_info_issue(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: HubeiCostInfoClient,
    row: dict,
    actor_id: str = "hubei-cost-info-crawler",
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
        raise ValueError("HUBEI_COST_INFO_ATTACHMENTS_MISSING")

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


def run_hubei_cost_info_incremental(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: HubeiCostInfoClient | None = None,
    max_pages: int = 1,
    page_size: int = 100,
    max_items: int | None = 5,
    actor_id: str = "hubei-cost-info-crawler",
) -> dict[str, object]:
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or hubei_cost_info_source_config()
    parser = config["parser"]["parsers"][HUBEI_COST_INFO_PARSER_VERSION]
    active_client = client or UrllibHubeiCostInfoClient()
    ingested = 0
    skipped_existing = 0
    cursor_source_item_key: str | None = None
    for page in range(1, max_pages + 1):
        rows = list_hubei_cost_info_issues(active_client, parser=parser, page=page, page_size=page_size)
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
            ingest_hubei_cost_info_issue(
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


def _normalize_list_row(row: dict) -> dict[str, object] | None:
    row_id = str(row.get("id") or "").strip()
    if not row_id:
        return None
    if str(row.get("region") or "").strip() != "420000":
        return None
    if int(row.get("journalType") or 0) != 1:
        return None
    journal_name = _normalize_space(row.get("journalName"))
    if journal_name != HUBEI_MAIN_JOURNAL_NAME:
        return None
    year_text = str(row.get("pushYear") or "").strip()
    period_text = _normalize_space(row.get("pushPeriod"))
    if not re.fullmatch(r"20\d{2}", year_text) or not re.fullmatch(r"第\d{1,2}期", period_text):
        return None
    pdf_path = str(row.get("journalUrl") or "").strip()
    if not pdf_path.lower().endswith(".pdf"):
        return None
    issue_no = int(re.search(r"\d{1,2}", period_text).group(0))  # type: ignore[union-attr]
    title = f"{year_text}年{period_text}《{HUBEI_MAIN_JOURNAL_NAME}》"
    return {
        "source_item_key": f"bJournal/{row_id}",
        "title": title,
        "listed_title": journal_name,
        "period": f"{year_text}年{period_text}",
        "period_year": int(year_text),
        "period_issue_no": issue_no,
        "publish_date": _date_from_datetime(row.get("createTime")),
        "detail_url": HUBEI_ENTRY_URL,
        "attachments": [
            {
                "file_name": _file_name_from_path(pdf_path),
                "url": _download_url(pdf_path),
                "discovery_method": "journal_list_api",
            }
        ],
    }


def _download_url(remote_file: str) -> str:
    return f"{HUBEI_BASE_URL}/basic/o/file/download-img?remoteFile={quote(remote_file, safe='/')}"


def _file_name_from_path(path: str) -> str:
    name = os.path.basename(urlsplit(path).path)
    return name if name else "湖北工程造价管理.pdf"


def _row_attachments(row: dict) -> list[dict[str, object]]:
    return list(row.get("attachments") or [])


def _date_from_datetime(value: object) -> str | None:
    text = str(value or "").strip()
    match = re.match(r"(?P<date>20\d{2}-\d{2}-\d{2})", text)
    return match.group("date") if match else None


def _normalize_space(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _ordered_unique(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
