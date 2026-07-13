from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html import unescape
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
from app.trading_registry import (
    FETCH_STATUS_PENDING,
    TradingAttachment,
    TradingDiscoveredItem,
    ingest_trading_registry_item,
    run_pending_trading_fetches,
    trading_exchange_source_config,
)

CHONGQING_TRADING_ENTRY_URL = "https://www.cqggzy.com/trade/014001?categoryNum=014001001&pageNum=1"
CHONGQING_TRADING_BASE_URL = "https://www.cqggzy.com"
CHONGQING_SEARCH_API_URL = "https://www.cqggzy.com/api/v2/search-engine-page"
CHONGQING_DOWNLOAD_URL = (
    "https://ggzydl.cqggzy.com/CQTPBidder/jsgcztbmis2/pages/zbfilelingqu_hy/"
    "cQZBFileDownAttachAction.action"
)
CHONGQING_TRADING_PARSER_VERSION = "cqggzy.jsgc.v1"
CHONGQING_OPAQUE_CONTAINER_EXTENSIONS = [".cqzf", ".zip", ".rar", ".7z", ".cdz"]


class ChongqingTradingClient(Protocol):
    def post_json(self, url: str, payload: dict[str, object]) -> dict:
        ...

    def get_json(self, url: str) -> dict:
        ...

    def get_bytes(self, url: str) -> tuple[bytes, str | None]:
        ...


@dataclass(frozen=True)
class ChongqingNoticeSummary:
    source_item_key: str
    title: str
    detail_url: str
    category_num: str
    publish_date: str | None
    region_raw: str | None
    project_code_raw: str | None
    tenderer_raw: str | None
    notice_type_raw: str
    channel_id: str
    notice_family: str
    category_type_raw: str | None = None


@dataclass(frozen=True)
class ChongqingAttachment:
    file_name: str
    url: str
    file_code: str
    attach_guid: str
    client_guid: str


class ChongqingAttachmentDownloadError(RuntimeError):
    def __init__(
        self,
        *,
        source_item_key: str,
        title: str,
        attachment: ChongqingAttachment,
        error_code: str,
        reason: str,
    ):
        self.source_item_key = source_item_key
        self.title = title
        self.attachment = attachment
        self.error_code = error_code
        self.reason = reason
        super().__init__(f"{error_code}: {attachment.file_name}: {reason}")


class UrllibChongqingTradingClient(SourceRegistryHttpClient):
    def __init__(
        self,
        *,
        referer: str = CHONGQING_TRADING_ENTRY_URL,
        timeout: int = 60,
        min_interval_seconds: float = 0,
        jitter_seconds: float = 0,
        attachment_read_timeout_seconds: float = 30,
        attachment_total_timeout_seconds: float = 300,
    ):
        super().__init__(
            referer=referer,
            timeout=timeout,
            min_interval_seconds=min_interval_seconds,
            jitter_seconds=jitter_seconds,
            read_timeout_seconds=attachment_read_timeout_seconds,
            read_total_timeout_seconds=attachment_total_timeout_seconds,
        )

    def get_json(self, url: str) -> dict:
        return json.loads(self.get_text(url))


def run_chongqing_pending_attachment_fetches(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: ChongqingTradingClient | None = None,
    limit: int = 1,
    actor_id: str = "chongqing-trading-offpeak-fetch",
) -> dict[str, object]:
    fetch_client = client or UrllibChongqingTradingClient(
        referer=f"{CHONGQING_TRADING_BASE_URL}/",
        timeout=1200,
        attachment_read_timeout_seconds=1200,
        attachment_total_timeout_seconds=1200,
    )
    return run_pending_trading_fetches(
        session,
        storage,
        source_id=source_id,
        client=fetch_client,
        limit=limit,
        actor_id=actor_id,
    )


def chongqing_trading_source_config() -> dict[str, object]:
    config = trading_exchange_source_config(
        site_id="trading.cqggzy.jsgc",
        publisher_name="重庆市公共资源交易中心",
        entry_url=CHONGQING_TRADING_ENTRY_URL,
        region_code="500000",
        parser_version=CHONGQING_TRADING_PARSER_VERSION,
        publisher_type="exchange_center",
        source_priority="official",
        crawl_strategy_tier="tier2_json_search_api",
        channels=[
            {
                "channel_id": "tender_plan",
                "notice_family": "tender_plan",
                "notice_type_raw": "招标计划",
                "category_num": "014001019",
                "entry_url": CHONGQING_TRADING_ENTRY_URL,
            },
            {
                "channel_id": "tender_notice",
                "notice_family": "tender",
                "notice_type_raw": "招标公告",
                "category_num": "014001001",
                "entry_url": CHONGQING_TRADING_ENTRY_URL,
            },
            {
                "channel_id": "invitation_notice",
                "notice_family": "tender",
                "notice_type_raw": "邀标信息",
                "category_num": "014001014",
                "entry_url": CHONGQING_TRADING_ENTRY_URL,
            },
            {
                "channel_id": "change_notice",
                "notice_family": "change",
                "notice_type_raw": "答疑补遗",
                "category_num": "014001002",
                "entry_url": CHONGQING_TRADING_ENTRY_URL,
            },
            {
                "channel_id": "award_candidate",
                "notice_family": "award_candidate",
                "notice_type_raw": "中标候选人公示",
                "category_num": "014001003",
                "entry_url": CHONGQING_TRADING_ENTRY_URL,
            },
            {
                "channel_id": "award_result",
                "notice_family": "award",
                "notice_type_raw": "中标结果公示",
                "category_num": "014001004",
                "entry_url": CHONGQING_TRADING_ENTRY_URL,
            },
        ],
    )
    parser = config["parser"]["parsers"][CHONGQING_TRADING_PARSER_VERSION]
    parser["scope"] = "json_search_api_detail_info_content"
    parser["search_api_url"] = CHONGQING_SEARCH_API_URL
    parser["pagination"] = {"mode": "offset", "offset_field": "pn", "page_size_field": "rn"}
    parser["detail_api"] = "/api/page/{infoid}?categoryNum={categorynum}"
    parser["attachment_discovery"] = "infoContent.downloadAttach"
    parser["download_url"] = CHONGQING_DOWNLOAD_URL
    parser["attachment_rules"]["opaque_container_extensions"] = CHONGQING_OPAQUE_CONTAINER_EXTENSIONS
    parser["red_lines"] = [
        "do_not_decompress_container",
        "do_not_parse_priced_source",
        "do_not_group_project_code",
        "gated_metadata_only",
    ]
    config["ops"]["queue"] = {
        "max_concurrent_per_host": 1,
        "min_delay_seconds": 3,
        "jitter_seconds": 2,
        "retry_backoff": "exponential",
    }
    return config


def discover_chongqing_trading_notices(
    client: ChongqingTradingClient,
    *,
    category_num: str,
    notice_type_raw: str,
    channel_id: str,
    notice_family: str,
    offset: int = 0,
    page_size: int = 10,
    start_date: str | date | None = None,
    end_date: str | date | None = None,
) -> list[ChongqingNoticeSummary]:
    response = client.post_json(
        CHONGQING_SEARCH_API_URL,
        _search_payload(
            category_num=category_num,
            offset=offset,
            page_size=page_size,
            start_date=start_date,
            end_date=end_date,
        ),
    )
    records = ((_unwrap_search_response(response).get("result") or {}).get("records")) or []
    summaries: list[ChongqingNoticeSummary] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        source_item_key = _first_text(record, "infoid", "infoId")
        record_category_num = _first_text(record, "categorynum", "categoryNum") or category_num
        if not source_item_key:
            continue
        title = _first_text(record, "title", "titlenew") or source_item_key
        summaries.append(
            ChongqingNoticeSummary(
                source_item_key=source_item_key,
                title=title,
                detail_url=_detail_page_url(source_item_key, record_category_num),
                category_num=record_category_num,
                publish_date=_date_prefix(_first_text(record, "pubinwebdate", "webdate", "infodate", "infoDate")),
                region_raw=_first_text(record, "infoc", "infoC"),
                project_code_raw=_first_text(record, "infod", "infoD", "projectno", "projectNo"),
                tenderer_raw=_first_text(record, "companyname", "companyName"),
                notice_type_raw=notice_type_raw,
                channel_id=channel_id,
                notice_family=notice_family,
                category_type_raw=_first_text(record, "categorytype", "categorytype2", "categoryType", "categoryType2"),
            )
        )
    return summaries


def ingest_chongqing_trading_notice(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: ChongqingTradingClient,
    summary: ChongqingNoticeSummary,
    actor_id: str = "chongqing-trading-crawler",
) -> Archive:
    source = require_data_source(session, source_id, domain_type="trading")

    detail_url = _detail_api_url(summary.source_item_key, summary.category_num)
    detail_response = client.get_json(detail_url)
    detail = _parse_notice_detail(detail_response, summary=summary)
    title = str(detail["title"])
    publish_date = detail.get("publish_date")
    source_item_key = str(detail["source_item_key"])
    fetched_at = _fetched_at_from_publish_date(str(publish_date) if publish_date else None)
    discovered_attachments = detail["attachments"]

    attachments = [
        TradingAttachment(
            file_name=f"{source_item_key}.html",
            content=str(detail.get("info_content") or "").encode("utf-8"),
            url=summary.detail_url,
            content_type="text/html; charset=utf-8",
            file_role="web_snapshot",
            metadata={"source_preview": "api_page.infoContent"},
        )
    ]
    for attachment in discovered_attachments:
        file_role = _chongqing_attachment_file_role(attachment)
        if _should_defer_chongqing_attachment(attachment, file_role):
            attachments.append(
                TradingAttachment(
                    file_name=attachment.file_name,
                    content=None,
                    url=attachment.url,
                    content_type=None,
                    file_role=file_role,
                    fetch_status=FETCH_STATUS_PENDING,
                    metadata={
                        "attach_guid": attachment.attach_guid,
                        "file_code": attachment.file_code,
                        "client_guid": attachment.client_guid,
                        "access_status": "PENDING_FETCH",
                        "defer_reason": "large_attachment_offpeak",
                    },
                )
            )
            continue
        try:
            content, content_type = client.get_bytes(attachment.url)
        except Exception as exc:
            raise ChongqingAttachmentDownloadError(
                source_item_key=source_item_key,
                title=title,
                attachment=attachment,
                error_code=_chongqing_attachment_error_code(exc),
                reason=str(exc),
            ) from exc
        if not content:
            raise ChongqingAttachmentDownloadError(
                source_item_key=source_item_key,
                title=title,
                attachment=attachment,
                error_code="CHONGQING_ATTACHMENT_EMPTY",
                reason=f"empty attachment: {attachment.url}",
            )
        attachments.append(
            TradingAttachment(
                file_name=attachment.file_name,
                content=content,
                url=attachment.url,
                content_type=content_type,
                file_role=file_role,
                metadata={
                    "attach_guid": attachment.attach_guid,
                    "file_code": attachment.file_code,
                    "client_guid": attachment.client_guid,
                },
            )
        )

    item = TradingDiscoveredItem(
        source_item_key=source_item_key,
        title=title,
        publish_date=str(publish_date) if publish_date else None,
        detail_url=summary.detail_url,
        channel_id=summary.channel_id,
        notice_family=summary.notice_family,
        notice_type_raw=summary.notice_type_raw,
        project_code_raw=detail.get("project_code_raw"),
        tender_code_raw=detail.get("tender_code_raw"),
        section_no_raw=detail.get("section_no_raw"),
        tenderer_raw=detail.get("tenderer_raw"),
        agency_raw=detail.get("agency_raw"),
        amount_raw=detail.get("amount_raw"),
        exchange_name="重庆市公共资源交易中心",
        column_path_raw=f"工程招投标 / {summary.notice_type_raw}",
        project_name_raw=detail.get("project_name_raw"),
        discovered_at=fetched_at,
        fetched_at=fetched_at,
        attachments=attachments,
        metadata={
            "source_item_key": source_item_key,
            "region_raw": detail.get("region_raw") or summary.region_raw,
            "category_num": detail.get("category_num") or summary.category_num,
            "attachment_count": len(discovered_attachments),
            "qingdan_package_count": sum(
                1 for attachment in discovered_attachments if _chongqing_attachment_file_role(attachment) == "qingdan_package"
            ),
            "opaque_container_count": _opaque_container_count(discovered_attachments),
            "attachment_discovery": "infoContent.downloadAttach",
        },
        business_key=_chongqing_business_key(
            source_id=source.source_id,
            source_item_key=source_item_key,
        ),
    )
    return ingest_trading_registry_item(
        session,
        storage,
        source_id=source.source_id,
        item=item,
        actor_id=actor_id,
    )


def run_chongqing_trading_channels(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: ChongqingTradingClient,
    channel_ids: list[str] | None = None,
    max_pages: int = 1,
    page_size: int = 10,
    max_items_per_channel: int | None = None,
    actor_id: str = "chongqing-trading-crawler",
    as_of_date: str | date | None = None,
    start_date: str | date | None = None,
    end_date: str | date | None = None,
) -> dict[str, object]:
    source = require_data_source(session, source_id, domain_type="trading")
    config = source.config or chongqing_trading_source_config()
    parser = config["parser"]["parsers"][CHONGQING_TRADING_PARSER_VERSION]

    def discover_page(channel: dict[str, object], page: int, page_size: int) -> list[ChongqingNoticeSummary]:
        return discover_chongqing_trading_notices(
            client,
            category_num=str(channel["category_num"]),
            notice_type_raw=str(channel["notice_type_raw"]),
            channel_id=str(channel["channel_id"]),
            notice_family=str(channel["notice_family"]),
            offset=(page - 1) * page_size,
            page_size=page_size,
            start_date=start_date,
            end_date=end_date,
        )

    def business_key_for(summary: ChongqingNoticeSummary) -> str:
        return _chongqing_business_key(
            source_id=source.source_id,
            source_item_key=summary.source_item_key,
        )

    return run_incremental_channel_crawl(
        source_id=source.source_id,
        channels=parser["channels"],
        selected_channel_ids=channel_ids,
        discover_page=discover_page,
        business_key_for=business_key_for,
        archive_exists=lambda business_key: archive_business_key_exists(
            session,
            tenant_code=source.asset_tenant_code,
            domain_type="trading",
            business_key=business_key,
        ),
        ingest=lambda summary: ingest_chongqing_trading_notice(
            session,
            storage,
            source_id=source.source_id,
            client=client,
            summary=summary,
            actor_id=actor_id,
        ),
        on_ingest_error=lambda summary, exc: _record_chongqing_ingest_failure(
            session,
            source_id=source.source_id,
            actor_id=actor_id,
            summary=summary,
            exc=exc,
        ),
        max_pages=max_pages,
        page_size=page_size,
        max_items_per_channel=max_items_per_channel,
        as_of_date=as_of_date,
    )


def _search_payload(
    *,
    category_num: str,
    offset: int,
    page_size: int,
    start_date: str | date | None,
    end_date: str | date | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "token": "",
        "pn": offset,
        "rn": page_size,
        "sdt": "",
        "edt": "",
        "wd": "",
        "inc_wd": "",
        "exc_wd": "",
        "fields": "",
        "cnum": "001",
        "sort": '{"istop":"0","ordernum":"0","webdate":"0","newid":"0"}',
        "ssort": "",
        "cl": 10000,
        "terminal": "",
        "highlights": "",
        "statistics": None,
        "unionCondition": [],
        "accuracy": "",
        "noParticiple": "1",
        "searchRange": None,
        "noWd": True,
        "condition": [
            {
                "fieldName": "categorynum",
                "equal": category_num,
                "notEqual": None,
                "equalList": None,
                "notEqualList": None,
                "isLike": True,
                "likeType": 2,
                "noWd": True,
            }
        ],
    }
    start = _date_input(start_date)
    end = _date_input(end_date)
    if start or end:
        payload["time"] = [
            {
                "fieldName": "webdate",
                "startTime": f"{start or end} 00:00:00",
                "endTime": f"{end or start} 23:59:59",
            }
        ]
    return payload


def _unwrap_search_response(response: dict) -> dict:
    content = response.get("content")
    if isinstance(content, str) and content.strip().startswith("{"):
        return json.loads(content)
    return response


def _parse_notice_detail(response: dict, *, summary: ChongqingNoticeSummary) -> dict[str, object]:
    current = ((response.get("result") or {}).get("currentRecord")) or response.get("currentRecord") or response.get("data") or {}
    if not isinstance(current, dict):
        current = {}
    title = _first_text(current, "title", "infoTitle") or summary.title
    publish_date = _date_prefix(_first_text(current, "infoDate", "webdate", "pubinwebdate")) or summary.publish_date
    source_item_key = _first_text(current, "infoId", "infoid") or summary.source_item_key
    info_content = _first_text(current, "infoContent", "infocontent") or ""
    return {
        "title": title,
        "publish_date": publish_date,
        "source_item_key": source_item_key,
        "category_num": _first_text(current, "categoryNum", "categorynum") or summary.category_num,
        "region_raw": _first_text(current, "infoC", "infoc") or summary.region_raw,
        "project_name_raw": _first_text(current, "projectName", "xiangMuName") or title,
        "project_code_raw": _first_text(current, "infoD", "infod", "projectNo", "projectno") or summary.project_code_raw,
        "tender_code_raw": _first_text(current, "cqBianHao", "biaoDuanNo", "tenderCode"),
        "section_no_raw": _first_text(current, "biaoDuanName", "sectionName"),
        "tenderer_raw": _first_text(current, "zhaoBiaoRen", "zhaoBiaoRenName", "companyName", "companyname")
        or summary.tenderer_raw,
        "agency_raw": _first_text(current, "daiLiDanWei", "daiLiName", "agentName", "agencyName"),
        "amount_raw": _first_text(current, "touZiE", "heTongGuSuanJia", "amount"),
        "info_content": info_content,
        "attachments": _parse_download_attach_attachments(info_content),
    }


def _parse_download_attach_attachments(html: str) -> list[ChongqingAttachment]:
    attachments: list[ChongqingAttachment] = []
    seen: set[tuple[str, str, str]] = set()
    tag_pattern = re.compile(
        r"<(?P<tag>a|span|button)\b(?P<attrs>[^>]*)downloadAttach\((?P<args>[^)]*)\)(?P<after>[^>]*)>"
        r"(?P<label>.*?)</(?P=tag)>",
        flags=re.I | re.S,
    )
    for match in tag_pattern.finditer(html):
        call = _parse_download_attach_args(match.group("args"))
        if call is None or call in seen:
            continue
        seen.add(call)
        attachments.append(_attachment_from_call(file_name=_clean_html(match.group("label")), call=call))

    for call in _download_attach_call_pattern().findall(html):
        parsed = tuple(call)
        if parsed in seen:
            continue
        seen.add(parsed)
        attachments.append(_attachment_from_call(file_name=f"{parsed[0]}.{parsed[1]}", call=parsed))
    return attachments


def _download_attach_call_pattern():
    return re.compile(r"downloadAttach\('([^']+)'\s*,\s*'([^']+)'\s*,\s*'([^']+)'\)")


def _parse_download_attach_args(args: str) -> tuple[str, str, str] | None:
    match = re.search(r"'([^']+)'\s*,\s*'([^']+)'\s*,\s*'([^']+)'", args)
    if match is None:
        return None
    return match.group(1), match.group(2), match.group(3)


def _attachment_from_call(*, file_name: str, call: tuple[str, str, str]) -> ChongqingAttachment:
    attach_guid, file_code, client_guid = call
    query = urlencode(
        {
            "cmd": "download",
            "AttachGuid": attach_guid,
            "FileCode": file_code,
            "ClientGuid": client_guid,
        }
    )
    return ChongqingAttachment(
        file_name=file_name or attach_guid,
        url=f"{CHONGQING_DOWNLOAD_URL}?{query}",
        file_code=file_code,
        attach_guid=attach_guid,
        client_guid=client_guid,
    )


def _chongqing_attachment_file_role(attachment: ChongqingAttachment) -> str | None:
    name = attachment.file_name.lower()
    if attachment.file_code == "CQ012":
        return "drawing"
    if any(keyword in attachment.file_name for keyword in ("地勘", "勘察", "岩土")):
        return "geological"
    if any(keyword in attachment.file_name for keyword in ("工程图", "施工图", "设计图", "设计成果", "图纸", "效果图")):
        return "drawing"
    if name.endswith(".cqzf") or any(
        keyword in attachment.file_name for keyword in ("工程量清单", "限价清单", "空白清单", "清单包", "招标控制价", "控制价")
    ):
        return "qingdan_package"
    if any(
        keyword in attachment.file_name
        for keyword in ("招标公告", "招标文件", "招标书", "比选文件", "技术文件", "技术标准", "评标办法")
    ):
        return "tender_doc"
    return None


def _should_defer_chongqing_attachment(attachment: ChongqingAttachment, file_role: str | None) -> bool:
    if file_role in {"drawing", "geological"}:
        return True
    name = attachment.file_name.lower()
    if name.endswith((".zip", ".rar", ".7z")) and any(
        keyword in attachment.file_name for keyword in ("图纸", "施工图", "设计成果", "地勘", "勘察")
    ):
        return True
    return False


def _record_chongqing_ingest_failure(
    session: Session,
    *,
    source_id: str,
    actor_id: str,
    summary: ChongqingNoticeSummary,
    exc: Exception,
) -> dict[str, object]:
    failed_at = utcnow().isoformat()
    if isinstance(exc, ChongqingAttachmentDownloadError):
        payload = {
            "source_item_key": exc.source_item_key,
            "title": exc.title,
            "attachment_url": exc.attachment.url,
            "file_name": exc.attachment.file_name,
            "error": exc.reason,
            "failed_at": failed_at,
            "error_kind": "attachment_timeout"
            if exc.error_code == "CHONGQING_ATTACHMENT_DOWNLOAD_TIMEOUT"
            else "attachment_download_failed",
        }
        session.add(
            AuditLog(
                actor_type="system",
                actor_id=actor_id,
                action="TRADING_ATTACHMENT_DOWNLOAD_FAILED",
                target_type="data_source",
                target_id=source_id,
                error_code=exc.error_code,
                before_payload=None,
                after_payload=payload,
            )
        )
        session.flush()
        session.commit()
        return {"error_code": exc.error_code, **payload}

    payload = {
        "source_item_key": summary.source_item_key,
        "title": summary.title,
        "error": str(exc),
        "failed_at": failed_at,
        "error_kind": "notice_ingest_failed",
    }
    session.add(
        AuditLog(
            actor_type="system",
            actor_id=actor_id,
            action="TRADING_NOTICE_INGEST_FAILED",
            target_type="data_source",
            target_id=source_id,
            error_code="CHONGQING_NOTICE_INGEST_FAILED",
            before_payload=None,
            after_payload=payload,
        )
    )
    session.flush()
    session.commit()
    return {"error_code": "CHONGQING_NOTICE_INGEST_FAILED", **payload}


def _chongqing_attachment_error_code(exc: Exception) -> str:
    if isinstance(exc, (TimeoutError, SourceRegistryReadTimeout)):
        return "CHONGQING_ATTACHMENT_DOWNLOAD_TIMEOUT"
    return "CHONGQING_ATTACHMENT_DOWNLOAD_FAILED"


def _chongqing_business_key(*, source_id: str, source_item_key: str) -> str:
    return f"trading:{source_id}:notice:{normalize_key_text(source_item_key)}"


def _detail_page_url(infoid: str, category_num: str) -> str:
    return f"{CHONGQING_TRADING_BASE_URL}/trade/014001/{infoid}?categoryNum={category_num}"


def _detail_api_url(infoid: str, category_num: str) -> str:
    return f"{CHONGQING_TRADING_BASE_URL}/api/page/{infoid}?categoryNum={category_num}"


def _date_input(value: str | date | None) -> str:
    if value is None:
        return ""
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _date_prefix(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"20\d{2}-\d{2}-\d{2}", value)
    return match.group(0) if match else None


def _first_text(payload: dict, *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _clean_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    text = re.sub(r"\s+", " ", unescape(text))
    return text.strip()


def _opaque_container_count(attachments: list[ChongqingAttachment]) -> int:
    return count_opaque_attachments(attachments, CHONGQING_OPAQUE_CONTAINER_EXTENSIONS)


def _fetched_at_from_publish_date(publish_date: str | None) -> str | None:
    if not publish_date:
        return None
    return f"{publish_date}T00:00:00+08:00"
