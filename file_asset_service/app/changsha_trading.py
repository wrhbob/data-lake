"""长沙公共资源交易服务平台工程建设公告适配器。

平台的公开交易页是 SPA，但列表、公告详情和附件均由无需登录的
``/tradeApi`` 提供。日期查询参数在实测中不能可靠地约束结束日期，
所以历史任务按发布时间倒序翻页，并在客户端做时间窗口过滤。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html import escape
import json
import re
from typing import Protocol
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from app.models import Archive, AuditLog, utcnow
from app.normalization import normalize_key_text
from app.source_adapter import (
    SourceRegistryHttpClient,
    SourceRegistryReadTimeout,
    archive_business_key_exists,
    count_opaque_attachments,
    require_data_source,
    run_incremental_channel_crawl,
)
from app.storage import ObjectStore
from app.trading_registry import TradingAttachment, TradingDiscoveredItem, ingest_trading_registry_item, trading_exchange_source_config

CHANGSHA_TRADING_BASE_URL = "https://changsha.hnsggzy.com"
CHANGSHA_TRADING_ENTRY_URL = f"{CHANGSHA_TRADING_BASE_URL}/#/sy/fastTrack?value=gcjskj"
CHANGSHA_TRADING_API_URL = f"{CHANGSHA_TRADING_BASE_URL}/tradeApi"
CHANGSHA_TRADING_PARSER_VERSION = "changsha.hnsggzy.jsgc.v1"
CHANGSHA_REGION_CODE = "430100"
# The platform's ``CHONGXIN_ZHAOBIAO_NOTICE`` code is misleading: in the
# sampled records its detail title is actually a 流标/终止公告.  Keep this
# tender channel strictly to notices whose official detail type is 招标公告.
CHANGSHA_TENDER_NOTICE_CODES = ("ZHAOBIAO_NOTICE",)
CHANGSHA_OPAQUE_CONTAINER_EXTENSIONS = [".zip", ".rar", ".7z", ".cdz"]


class ChangshaTradingClient(Protocol):
    def get_json(self, path: str, params: dict[str, object] | None = None) -> dict:
        ...

    def get_bytes(self, url: str) -> tuple[bytes, str | None]:
        ...


@dataclass(frozen=True)
class ChangshaNoticeSummary:
    source_item_key: str
    bid_section_id: str
    title: str
    publish_date: str | None
    publish_time: str | None
    notice_type_code: str
    notice_type_raw: str
    channel_id: str
    notice_family: str
    is_public: str | None
    region_raw: str | None
    resource_state: str | None


@dataclass(frozen=True)
class ChangshaAttachment:
    file_name: str
    url: str
    attach_guid: str
    client_type: str | None = None


class ChangshaAttachmentDownloadError(RuntimeError):
    def __init__(
        self,
        *,
        source_item_key: str,
        title: str,
        attachment: ChangshaAttachment,
        error_code: str,
        reason: str,
    ) -> None:
        self.source_item_key = source_item_key
        self.title = title
        self.attachment = attachment
        self.error_code = error_code
        self.reason = reason
        super().__init__(f"{error_code}: {attachment.file_name}: {reason}")


class UrllibChangshaTradingClient(SourceRegistryHttpClient):
    def __init__(
        self,
        *,
        timeout: int = 60,
        min_interval_seconds: float = 0,
        jitter_seconds: float = 0,
        attachment_read_timeout_seconds: float = 45,
        attachment_total_timeout_seconds: float = 600,
    ) -> None:
        super().__init__(
            referer=CHANGSHA_TRADING_ENTRY_URL,
            timeout=timeout,
            min_interval_seconds=min_interval_seconds,
            jitter_seconds=jitter_seconds,
            read_timeout_seconds=attachment_read_timeout_seconds,
            read_total_timeout_seconds=attachment_total_timeout_seconds,
        )

    def get_json(self, path: str, params: dict[str, object] | None = None) -> dict:
        url = _api_url(path, params)
        return json.loads(self.get_text(url))


def changsha_trading_source_config() -> dict[str, object]:
    config = trading_exchange_source_config(
        site_id="trading.changsha.hnsggzy.jsgc",
        publisher_name="长沙市公共资源交易服务平台",
        entry_url=CHANGSHA_TRADING_ENTRY_URL,
        region_code=CHANGSHA_REGION_CODE,
        parser_version=CHANGSHA_TRADING_PARSER_VERSION,
        publisher_type="exchange_center",
        source_priority="official",
        crawl_strategy_tier="tier2_public_json_api",
        channels=[
            {
                "channel_id": "tender_notice",
                "notice_family": "tender",
                "notice_type_raw": "招标公告",
                "notice_type_codes": list(CHANGSHA_TENDER_NOTICE_CODES),
                "entry_url": CHANGSHA_TRADING_ENTRY_URL,
            }
        ],
    )
    parser = config["parser"]["parsers"][CHANGSHA_TRADING_PARSER_VERSION]
    parser["scope"] = "public_json_list_detail_and_attachments"
    parser["list_api"] = "/constructionTender/listByFile"
    parser["detail_apis"] = {
        "project": "/constructionTender/getBySectionId",
        "notice": "/constructionNotice/getBySectionId",
        "attachments": "/attach/proxy/ListFile",
    }
    parser["pagination"] = {"mode": "page", "page_field": "current", "page_size_field": "size", "max_page_size": 100}
    parser["history_boundary"] = {
        "mode": "local_publish_date_window",
        "reason": "official_api_end_date_filter_is_not_reliable",
    }
    parser["attachment_rules"]["opaque_container_extensions"] = CHANGSHA_OPAQUE_CONTAINER_EXTENSIONS
    parser["red_lines"] = [
        "do_not_decompress_container",
        "do_not_parse_priced_source",
        "do_not_group_project_code",
        "gated_metadata_only",
    ]
    config["ops"]["queue"] = {
        "max_concurrent_per_host": 1,
        "min_delay_seconds": 1.2,
        "jitter_seconds": 0.4,
        "retry_backoff": "exponential",
        "page_size": 100,
    }
    return config


def discover_changsha_trading_notices(
    client: ChangshaTradingClient,
    *,
    notice_type_codes: list[str] | tuple[str, ...],
    notice_type_raw: str,
    channel_id: str,
    notice_family: str,
    page: int = 1,
    page_size: int = 100,
    start_date: str | date | None = None,
    end_date: str | date | None = None,
) -> list[ChangshaNoticeSummary]:
    response = client.get_json(
        "/constructionTender/listByFile",
        {
            "current": page,
            "size": min(max(1, page_size), 100),
            "regionCode": CHANGSHA_REGION_CODE,
            "tenderProjectType": "CONSTRUCTION",
        },
    )
    data = response.get("data") if isinstance(response, dict) else None
    records = data.get("records") if isinstance(data, dict) else []
    if not isinstance(records, list):
        raise ValueError("CHANGSHA_TRADING_LIST_RECORDS_MISSING")

    accepted_types = {str(value) for value in notice_type_codes}
    summaries: list[ChangshaNoticeSummary] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        bid_section_id = _text(record, "bidSectionId")
        notice_type_code = _text(record, "noticeType")
        publish_time = _text(record, "noticeSendTime")
        publish_date = _date_prefix(publish_time)
        if not bid_section_id or not notice_type_code or notice_type_code not in accepted_types:
            continue
        if not _within_date_window(publish_date, start_date=start_date, end_date=end_date):
            continue
        summaries.append(
            ChangshaNoticeSummary(
                source_item_key=_summary_key(bid_section_id, notice_type_code, publish_time),
                bid_section_id=bid_section_id,
                title=_text(record, "tenderProjectName", "bidSectionName") or bid_section_id,
                publish_date=publish_date,
                publish_time=publish_time,
                notice_type_code=notice_type_code,
                notice_type_raw=notice_type_raw,
                channel_id=channel_id,
                notice_family=notice_family,
                is_public=_text(record, "isPublic"),
                region_raw=_text(record, "name", "regionCode"),
                resource_state=_text(record, "resourceState"),
            )
        )
    return summaries


def ingest_changsha_trading_notice(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: ChangshaTradingClient,
    summary: ChangshaNoticeSummary,
    actor_id: str = "changsha-trading-crawler",
) -> Archive:
    source = require_data_source(session, source_id, domain_type="trading")
    notice_response = client.get_json("/constructionNotice/getBySectionId", {"sectionId": summary.bid_section_id})
    project_response = client.get_json("/constructionTender/getBySectionId", {"sectionId": summary.bid_section_id})
    notice = _matching_notice(notice_response, summary)
    project = _project_detail(project_response, summary.bid_section_id)
    title = _text(notice, "noticeName") or summary.title
    publish_time = _text(notice, "noticeSendTime") or summary.publish_time
    publish_date = _date_prefix(publish_time) or summary.publish_date
    notice_html = _notice_snapshot(title, _text(notice, "noticeContent") or "")
    notice_guid = _notice_guid(notice, summary.bid_section_id)
    discovered_attachments = _discover_attachments(client, notice_guid, summary)

    attachments = [
        TradingAttachment(
            file_name=f"{notice_guid or summary.bid_section_id}.html",
            content=notice_html.encode("utf-8"),
            url=_detail_page_url(summary.bid_section_id),
            content_type="text/html; charset=utf-8",
            file_role="web_snapshot",
            metadata={"source_preview": "tradeApi.constructionNotice.noticeContent"},
        )
    ]
    for attachment in discovered_attachments:
        try:
            content, content_type = client.get_bytes(attachment.url)
        except Exception as exc:  # noqa: BLE001 - persist the source-side failure against this notice.
            raise ChangshaAttachmentDownloadError(
                source_item_key=summary.source_item_key,
                title=title,
                attachment=attachment,
                error_code=_changsha_attachment_error_code(exc),
                reason=str(exc),
            ) from exc
        if not content or _looks_like_download_error(content):
            raise ChangshaAttachmentDownloadError(
                source_item_key=summary.source_item_key,
                title=title,
                attachment=attachment,
                error_code="CHANGSHA_ATTACHMENT_DOWNLOAD_FAILED",
                reason="empty or non-file response from official download endpoint",
            )
        attachments.append(
            TradingAttachment(
                file_name=attachment.file_name,
                content=content,
                url=attachment.url,
                content_type=content_type,
                file_role=_changsha_attachment_file_role(attachment),
                metadata={"attach_guid": attachment.attach_guid, "client_type": attachment.client_type},
            )
        )

    construction_tender = project.get("constructionTender") if isinstance(project.get("constructionTender"), dict) else {}
    construction_project = project.get("constructionProject") if isinstance(project.get("constructionProject"), dict) else {}
    section = _section_detail(project, summary.bid_section_id)
    item = TradingDiscoveredItem(
        source_item_key=summary.source_item_key,
        title=title,
        publish_date=publish_date,
        detail_url=_detail_page_url(summary.bid_section_id),
        channel_id=summary.channel_id,
        notice_family=summary.notice_family,
        notice_type_raw=summary.notice_type_raw,
        project_code_raw=_text(construction_project, "projectCode", "investProjectCode"),
        tender_code_raw=_text(construction_tender, "tenderProjectCode"),
        section_no_raw=_text(section, "bidSectionNo", "bidSectionCode"),
        bid_subject_role_raw=_text(construction_tender, "tenderProjectType"),
        tenderer_raw=_text(construction_tender, "tendererName", "ownerName"),
        agency_raw=_text(construction_tender, "tenderAgencyName"),
        amount_raw=_text(section, "tenderControlPrice", "contractReckonPrice") or _text(construction_tender, "projectBudget"),
        exchange_name="长沙市公共资源交易服务平台",
        column_path_raw="交易公开 / 工程建设 / 招标公告",
        project_name_raw=_text(construction_tender, "tenderProjectName") or _text(construction_project, "projectName") or summary.title,
        engineering_type_raw=_text(construction_tender, "tenderProjectType"),
        discovered_at=_fetched_at_from_publish_date(publish_date),
        fetched_at=_fetched_at_from_publish_date(publish_date),
        attachments=attachments,
        metadata={
            "source_item_key": summary.source_item_key,
            "bid_section_id": summary.bid_section_id,
            "notice_guid": notice_guid,
            "notice_type_code": summary.notice_type_code,
            "resource_state": summary.resource_state,
            "region_raw": _text(construction_project, "regionCode") or summary.region_raw,
            "attachment_count": len(discovered_attachments),
            "opaque_container_count": count_opaque_attachments(discovered_attachments, CHANGSHA_OPAQUE_CONTAINER_EXTENSIONS),
            "attachment_discovery": "tradeApi.attach.proxy.ListFile",
        },
        business_key=_changsha_business_key(source_id=source.source_id, source_item_key=summary.source_item_key),
    )
    return ingest_trading_registry_item(session, storage, source_id=source.source_id, item=item, actor_id=actor_id)


def run_changsha_trading_channels(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: ChangshaTradingClient,
    channel_ids: list[str] | None = None,
    max_pages: int = 1,
    page_size: int = 100,
    max_items_per_channel: int | None = None,
    actor_id: str = "changsha-trading-crawler",
    as_of_date: str | date | None = None,
    start_date: str | date | None = None,
    end_date: str | date | None = None,
    stop_on_existing: bool = True,
) -> dict[str, object]:
    source = require_data_source(session, source_id, domain_type="trading")
    config = source.config or changsha_trading_source_config()
    parser = config["parser"]["parsers"][CHANGSHA_TRADING_PARSER_VERSION]

    def discover_page(channel: dict[str, object], page: int, requested_page_size: int) -> list[ChangshaNoticeSummary]:
        return discover_changsha_trading_notices(
            client,
            notice_type_codes=tuple(str(value) for value in channel["notice_type_codes"]),
            notice_type_raw=str(channel["notice_type_raw"]),
            channel_id=str(channel["channel_id"]),
            notice_family=str(channel["notice_family"]),
            page=page,
            page_size=requested_page_size,
            start_date=start_date,
            end_date=end_date,
        )

    return run_incremental_channel_crawl(
        source_id=source.source_id,
        channels=parser["channels"],
        selected_channel_ids=channel_ids,
        discover_page=discover_page,
        business_key_for=lambda summary: _changsha_business_key(source_id=source.source_id, source_item_key=summary.source_item_key),
        archive_exists=lambda business_key: archive_business_key_exists(
            session,
            tenant_code=source.asset_tenant_code,
            domain_type="trading",
            business_key=business_key,
        ),
        ingest=lambda summary: ingest_changsha_trading_notice(
            session,
            storage,
            source_id=source.source_id,
            client=client,
            summary=summary,
            actor_id=actor_id,
        ),
        on_ingest_error=lambda summary, exc: _record_changsha_ingest_failure(
            session, source_id=source.source_id, actor_id=actor_id, summary=summary, exc=exc
        ),
        max_pages=max_pages,
        page_size=min(max(1, page_size), 100),
        max_items_per_channel=max_items_per_channel,
        as_of_date=as_of_date,
        stop_on_existing=stop_on_existing,
    )


def _api_url(path: str, params: dict[str, object] | None = None) -> str:
    url = f"{CHANGSHA_TRADING_API_URL}/{path.lstrip('/')}"
    values = {key: value for key, value in (params or {}).items() if value is not None and value != ""}
    return f"{url}?{urlencode(values, doseq=True)}" if values else url


def _detail_page_url(bid_section_id: str) -> str:
    return f"{CHANGSHA_TRADING_BASE_URL}/#/resources/transactionDetail/CONSTRUCTION?bidSectionId={bid_section_id}"


def _summary_key(bid_section_id: str, notice_type_code: str, publish_time: str | None) -> str:
    return f"{bid_section_id}|{notice_type_code}|{publish_time or ''}"


def _matching_notice(response: dict, summary: ChangshaNoticeSummary) -> dict:
    data = response.get("data") if isinstance(response, dict) else None
    notices = data.get("noticeList") if isinstance(data, dict) else []
    if not isinstance(notices, list):
        raise ValueError("CHANGSHA_NOTICE_DETAIL_MISSING")
    matches = [
        notice
        for notice in notices
        if isinstance(notice, dict)
        and _text(notice, "bulletinType") == summary.notice_type_code
        and (not summary.publish_time or _text(notice, "noticeSendTime") == summary.publish_time)
    ]
    if not matches:
        matches = [notice for notice in notices if isinstance(notice, dict) and _text(notice, "bulletinType") == summary.notice_type_code]
    if not matches:
        raise ValueError("CHANGSHA_NOTICE_DETAIL_MISSING")
    return max(matches, key=lambda notice: _text(notice, "noticeSendTime") or "")


def _project_detail(response: dict, bid_section_id: str) -> dict:
    data = response.get("data") if isinstance(response, dict) else None
    if not isinstance(data, dict):
        return {}
    detail = dict(data)
    detail["selected_section"] = _section_detail(detail, bid_section_id)
    return detail


def _section_detail(detail: dict, bid_section_id: str) -> dict:
    sections = detail.get("constructionSectionList") if isinstance(detail.get("constructionSectionList"), list) else []
    for section in sections:
        if isinstance(section, dict) and _text(section, "id") == bid_section_id:
            return section
    return sections[0] if sections and isinstance(sections[0], dict) else {}


def _notice_guid(notice: dict, bid_section_id: str) -> str:
    notice_id = _text(notice, "id") or ""
    section_id = _text(notice, "constructionSectionId") or bid_section_id
    if notice_id and section_id and notice_id.endswith(section_id):
        return notice_id[: -len(section_id)]
    return notice_id


def _discover_attachments(client: ChangshaTradingClient, notice_guid: str, summary: ChangshaNoticeSummary) -> list[ChangshaAttachment]:
    if not notice_guid:
        return []
    response = client.get_json("/attach/proxy/ListFile", {"gonggaoguid": notice_guid})
    rows = response.get("data") if isinstance(response, dict) else []
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise ValueError("CHANGSHA_ATTACHMENT_LIST_INVALID")
    attachments: list[ChangshaAttachment] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        attach_guid = _text(row, "AttachGuid", "attachGuid")
        file_name = _text(row, "attachfilename", "attachFileName")
        if not attach_guid or not file_name:
            continue
        params: dict[str, object]
        if str(summary.is_public) == "0":
            params = {
                "attachGuid": attach_guid,
                "attachFileName": file_name,
                "transactionType": "CONSTRUCTION",
                "isPublic": "0",
            }
        else:
            params = {
                "fileId": attach_guid,
                "region": CHANGSHA_REGION_CODE,
                "transactionType": "CONSTRUCTION",
            }
        attachments.append(
            ChangshaAttachment(
                file_name=file_name,
                url=_api_url("/attach/proxy/downloadCommon", params),
                attach_guid=attach_guid,
                client_type=_text(row, "clienttype", "clientType"),
            )
        )
    return attachments


def _notice_snapshot(title: str, content: str) -> str:
    return (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        f"<title>{escape(title)}</title></head><body>{content}</body></html>"
    )


def _within_date_window(value: str | None, *, start_date: str | date | None, end_date: str | date | None) -> bool:
    if value is None:
        return start_date is None and end_date is None
    current = date.fromisoformat(value)
    start = _as_date(start_date)
    end = _as_date(end_date)
    return (start is None or current >= start) and (end is None or current <= end)


def _as_date(value: str | date | None) -> date | None:
    if value is None:
        return None
    return value if isinstance(value, date) else date.fromisoformat(value)


def _date_prefix(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"20\d{2}-\d{2}-\d{2}", value)
    return match.group(0) if match else None


def _text(payload: dict, *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _changsha_attachment_file_role(attachment: ChangshaAttachment) -> str | None:
    name = attachment.file_name.lower()
    if any(keyword in attachment.file_name for keyword in ("地勘", "勘察", "岩土")):
        return "geological"
    if any(keyword in attachment.file_name for keyword in ("工程图", "施工图", "设计图", "图纸", "效果图")):
        return "drawing"
    if name.endswith((".cdz", ".zbx", ".cjz", ".cos", ".qtfx")) or any(
        keyword in attachment.file_name for keyword in ("工程量清单", "清单包", "招标控制价", "最高投标限价", "控制价")
    ):
        return "qingdan_package"
    if any(keyword in attachment.file_name for keyword in ("招标公告", "招标文件", "任务书", "技术要求", "合同")):
        return "tender_doc"
    return None


def _looks_like_download_error(content: bytes) -> bool:
    if len(content) > 2048:
        return False
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    status = value.get("status") if isinstance(value, dict) else None
    return isinstance(status, dict) and str(status.get("state") or "").lower() == "error"


def _changsha_attachment_error_code(exc: Exception) -> str:
    if isinstance(exc, (TimeoutError, SourceRegistryReadTimeout)):
        return "CHANGSHA_ATTACHMENT_DOWNLOAD_TIMEOUT"
    return "CHANGSHA_ATTACHMENT_DOWNLOAD_FAILED"


def _record_changsha_ingest_failure(
    session: Session,
    *,
    source_id: str,
    actor_id: str,
    summary: ChangshaNoticeSummary,
    exc: Exception,
) -> dict[str, object]:
    session.rollback()
    failed_at = utcnow().isoformat()
    if isinstance(exc, ChangshaAttachmentDownloadError):
        payload = {
            "source_item_key": exc.source_item_key,
            "title": exc.title,
            "attachment_url": exc.attachment.url,
            "file_name": exc.attachment.file_name,
            "error": exc.reason,
            "failed_at": failed_at,
            "error_kind": "attachment_timeout" if exc.error_code == "CHANGSHA_ATTACHMENT_DOWNLOAD_TIMEOUT" else "attachment_download_failed",
        }
        error_code = exc.error_code
        action = "TRADING_ATTACHMENT_DOWNLOAD_FAILED"
    else:
        payload = {
            "source_item_key": summary.source_item_key,
            "title": summary.title,
            "error": str(exc),
            "failed_at": failed_at,
            "error_kind": "notice_ingest_failed",
        }
        error_code = "CHANGSHA_NOTICE_INGEST_FAILED"
        action = "TRADING_NOTICE_INGEST_FAILED"
    session.add(
        AuditLog(
            actor_type="system",
            actor_id=actor_id,
            action=action,
            target_type="data_source",
            target_id=source_id,
            error_code=error_code,
            before_payload=None,
            after_payload=payload,
        )
    )
    session.commit()
    return {"error_code": error_code, **payload}


def _changsha_business_key(*, source_id: str, source_item_key: str) -> str:
    return f"trading:{source_id}:notice:{normalize_key_text(source_item_key)}"


def _fetched_at_from_publish_date(publish_date: str | None) -> str | None:
    return f"{publish_date}T00:00:00+08:00" if publish_date else None
