from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
import re
from typing import Protocol
from urllib.parse import urljoin

from sqlalchemy.orm import Session

from app.archive_rules import build_cost_info_business_key
from app.source_adapter import SourceRegistryHttpClient, archive_business_key_exists, require_data_source
from app.source_registry import CostInfoAttachment, CostInfoDiscoveredItem, ingest_cost_info_registry_item
from app.storage import ObjectStore

GUIZHOU_BASE_URL = "http://www.gzszj.com"
GUIZHOU_ENTRY_URL = (
    "http://www.gzszj.com/Home/Policies/c2a45b5e-fb3e-43c6-a77c-000000000046"
    "?tid=c2a45b5e-fb3e-43c6-a77c-000000004601"
)
GUIZHOU_LIST_API_URL = "http://www.gzszj.com/Home/GetPoliciesListBy"
GUIZHOU_COST_INFO_POLICY_GUID = "c2a45b5e-fb3e-43c6-a77c-000000004601"
GUIZHOU_COST_INFO_PARSER_VERSION = "guizhou.gzszj-association-issue-pdf-list.v1"
GUIZHOU_TITLE_REGEX = r"^贵州省建设工程造价信息\s*(?P<year>20\d{2})年第(?P<issue>\d{1,2})期$"
GUIZHOU_ATTACHMENT_PREFIX = "/Upload/File/"


class GuizhouCostInfoClient(Protocol):
    def post_form_json(self, url: str, data: dict[str, object]) -> dict:
        ...

    def get_bytes(self, url: str) -> tuple[bytes, str | None]:
        ...


def guizhou_cost_info_source_config() -> dict:
    parser = {
        "scope": "guizhou_gzszj_association_issue_pdf_list",
        "list_url": GUIZHOU_ENTRY_URL,
        "list_api_url": GUIZHOU_LIST_API_URL,
        "list_guid": GUIZHOU_COST_INFO_POLICY_GUID,
        "list_strategy": "ajax_form_json",
        "page_size": 20,
        "period": {
            "kind": "issue_based",
            "source": "title",
            "regex": GUIZHOU_TITLE_REGEX,
        },
        "title_regex": GUIZHOU_TITLE_REGEX,
        "attachments": {
            "allowed": ["pdf"],
            "required_any": ["pdf"],
            "source": "PoliciesAttachmentDTOS",
        },
    }
    return {
        "registry_schema_version": "source_registry.v1",
        "stable": {
            "site_id": "cost_info.gz.gzszj.association",
            "domain_type": "cost_info",
            "region_code": "520000",
            "coverage_region_code": "520000",
            "producer": "贵州省建设工程造价管理协会",
            "publisher": "贵州省建设工程造价管理协会",
            "publisher_scope": "province",
            "publisher_region_code": "520000",
            "publisher_name": "贵州省建设工程造价管理协会",
            "publisher_type": "industry_association",
            "period_kind": "issue_based",
            "entry_url": GUIZHOU_ENTRY_URL,
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
            "active_parser_version": GUIZHOU_COST_INFO_PARSER_VERSION,
            "parsers": {
                GUIZHOU_COST_INFO_PARSER_VERSION: parser,
            },
        },
        "coverage_expectation": {
            "target_regions": [
                {
                    "region_code": "520000",
                    "region_name": "贵州省",
                    "target_level": "province",
                    "requires_city_source": False,
                    "source_completeness_status": "province_source_only",
                    "source_audit_status": "association_main_source",
                    "coverage_note": "贵州省建设工程造价管理协会官方《贵州省建设工程造价信息》主刊源",
                }
            ]
        },
        "ops": {
            "enabled": True,
            "crawl_strategy_tier": "tier2_ajax_form_json",
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


class UrllibGuizhouCostInfoClient(SourceRegistryHttpClient):
    def __init__(self, *, timeout: int = 60, min_interval_seconds: float = 5, jitter_seconds: float = 3) -> None:
        super().__init__(
            referer=GUIZHOU_ENTRY_URL,
            timeout=timeout,
            min_interval_seconds=min_interval_seconds,
            jitter_seconds=jitter_seconds,
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
        )


def list_guizhou_cost_info_issues(
    client: GuizhouCostInfoClient,
    *,
    parser: dict,
    page: int = 1,
    page_size: int | None = None,
) -> list[dict[str, object]]:
    payload = client.post_form_json(
        str(parser.get("list_api_url") or GUIZHOU_LIST_API_URL),
        {
            "title": "",
            "content": "",
            "guid": parser.get("list_guid") or GUIZHOU_COST_INFO_POLICY_GUID,
            "page": page,
            "pagesize": page_size or int(parser.get("page_size") or 20),
            "rnd": 0,
        },
    )
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw_row in payload.get("Rows") or []:
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


def ingest_guizhou_cost_info_issue(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: GuizhouCostInfoClient,
    row: dict,
    actor_id: str = "guizhou-cost-info-crawler",
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
        raise ValueError("GUIZHOU_COST_INFO_ATTACHMENTS_MISSING")

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
            "publisher_type": stable.get("publisher_type") or "industry_association",
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


def run_guizhou_cost_info_incremental(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: GuizhouCostInfoClient | None = None,
    max_pages: int = 1,
    page_size: int = 20,
    max_items: int | None = 5,
    actor_id: str = "guizhou-cost-info-crawler",
) -> dict[str, object]:
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or guizhou_cost_info_source_config()
    parser = config["parser"]["parsers"][GUIZHOU_COST_INFO_PARSER_VERSION]
    active_client = client or UrllibGuizhouCostInfoClient()
    ingested = 0
    skipped_existing = 0
    cursor_source_item_key: str | None = None
    for page in range(1, max_pages + 1):
        rows = list_guizhou_cost_info_issues(active_client, parser=parser, page=page, page_size=page_size)
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
            ingest_guizhou_cost_info_issue(
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
    title = str(row.get("Name") or "").strip()
    match = re.match(GUIZHOU_TITLE_REGEX, title)
    if not match:
        return None
    attachments = _attachments_from_list_row(row)
    if not attachments:
        return None
    policy_id = str(row.get("ID") or "").strip()
    if not policy_id:
        return None
    year = int(match.group("year"))
    issue_no = int(match.group("issue"))
    return {
        "source_item_key": f"PoliciesDetail/{policy_id}",
        "title": title,
        "period": f"{year}年第{issue_no}期",
        "period_year": year,
        "period_issue_no": issue_no,
        "publish_date": _aspnet_date(row.get("EntryDate")),
        "detail_url": urljoin(GUIZHOU_BASE_URL, f"/Home/PoliciesDetail/{policy_id}"),
        "attachments": attachments,
    }


def _attachments_from_list_row(row: dict) -> list[dict[str, str]]:
    attachments: list[dict[str, str]] = []
    for attachment in row.get("PoliciesAttachmentDTOS") or []:
        name = str(attachment.get("Name") or "").strip()
        file_url = str(attachment.get("FileUrl") or "").strip()
        if not name.lower().endswith(".pdf") or not file_url:
            continue
        attachments.append(
            {
                "file_name": name,
                "url": urljoin(GUIZHOU_BASE_URL, f"{GUIZHOU_ATTACHMENT_PREFIX}{file_url}"),
                "discovery_method": "list_api_attachment",
            }
        )
    return attachments


def _row_attachments(row: dict) -> list[dict[str, object]]:
    return list(row.get("attachments") or [])


def _aspnet_date(value: object) -> str | None:
    text = str(value or "")
    match = re.search(r"/Date\((\d+)\)/", text)
    if not match:
        return None
    published = datetime.fromtimestamp(int(match.group(1)) / 1000, timezone(timedelta(hours=8)))
    return published.date().isoformat()


def _ordered_unique(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
