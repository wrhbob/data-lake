from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html import unescape
from html.parser import HTMLParser
import re
import ssl
from typing import Protocol
from urllib.parse import parse_qs, urljoin, urlsplit

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
    safe_url,
)
from app.storage import ObjectStore
from app.trading_registry import (
    TradingAttachment,
    TradingDiscoveredItem,
    ingest_trading_registry_item,
    trading_exchange_source_config,
)

CHENGDU_TRADING_ENTRY_URL = "https://www.cdggzy.com/sitenew/notice/List.aspx?cid=000400010001"
CHENGDU_JSGC_LIST_URL = "https://www.cdggzy.com/sitenew/notice/JSGC/List.aspx"
CHENGDU_TRADING_PARSER_VERSION = "cdggzy.jsgc.v1"
CHENGDU_OPAQUE_CONTAINER_EXTENSIONS = [".cdz", ".zip", ".rar", ".7z"]


class ChengduTradingClient(Protocol):
    def get_text(self, url: str) -> str:
        ...

    def post_form(self, url: str, data: dict[str, str]) -> str:
        ...

    def get_bytes(self, url: str) -> tuple[bytes, str | None]:
        ...


@dataclass(frozen=True)
class ChengduNoticeSummary:
    source_item_key: str
    title: str
    detail_url: str
    publish_date: str | None
    region_raw: str | None
    status_raw: str | None
    notice_type_raw: str
    channel_id: str
    notice_family: str
    displaytypeval: str


@dataclass(frozen=True)
class ChengduAttachment:
    file_name: str
    url: str
    category_raw: str | None = None


class ChengduAttachmentDownloadError(RuntimeError):
    def __init__(
        self,
        *,
        source_item_key: str,
        title: str,
        attachment: ChengduAttachment,
        error_code: str,
        reason: str,
    ):
        self.source_item_key = source_item_key
        self.title = title
        self.attachment = attachment
        self.error_code = error_code
        self.reason = reason
        super().__init__(f"{error_code}: {attachment.file_name}: {reason}")


class UrllibChengduTradingClient(SourceRegistryHttpClient):
    def __init__(
        self,
        *,
        timeout: int = 60,
        min_interval_seconds: float = 0,
        jitter_seconds: float = 0,
        attachment_read_timeout_seconds: float = 30,
        attachment_total_timeout_seconds: float = 300,
    ):
        super().__init__(
            referer=CHENGDU_JSGC_LIST_URL,
            timeout=timeout,
            tls_minimum_version=ssl.TLSVersion.TLSv1_2,
            tls_ciphers="DEFAULT:@SECLEVEL=1",
            min_interval_seconds=min_interval_seconds,
            jitter_seconds=jitter_seconds,
            read_timeout_seconds=attachment_read_timeout_seconds,
            read_total_timeout_seconds=attachment_total_timeout_seconds,
        )


def chengdu_trading_source_config() -> dict[str, object]:
    config = trading_exchange_source_config(
        site_id="trading.cdggzy.jsgc",
        publisher_name="成都市公共资源交易服务中心",
        entry_url=CHENGDU_TRADING_ENTRY_URL,
        region_code="510100",
        parser_version=CHENGDU_TRADING_PARSER_VERSION,
        publisher_type="exchange_center",
        source_priority="official",
        crawl_strategy_tier="tier1_aspx_form_post",
        channels=[
            {
                "channel_id": "tender_plan",
                "notice_family": "tender_plan",
                "notice_type_raw": "招标计划",
                "entry_url": CHENGDU_JSGC_LIST_URL,
                "displaytypeval": "19",
            },
            {
                "channel_id": "tender_notice",
                "notice_family": "tender",
                "notice_type_raw": "招标公告",
                "entry_url": CHENGDU_JSGC_LIST_URL,
                "displaytypeval": "1",
            },
            {
                "channel_id": "change_notice",
                "notice_family": "change",
                "notice_type_raw": "变更公告",
                "entry_url": CHENGDU_JSGC_LIST_URL,
                "displaytypeval": "4",
            },
            {
                "channel_id": "award_result",
                "notice_family": "award",
                "notice_type_raw": "中标结果公布",
                "entry_url": CHENGDU_JSGC_LIST_URL,
                "displaytypeval": "18",
            },
            {
                "channel_id": "tender_file_pre_disclosure",
                "notice_family": "tender_pre_disclosure",
                "notice_type_raw": "招标文件提前公示",
                "entry_url": CHENGDU_JSGC_LIST_URL,
                "displaytypeval": "23",
            },
            {
                "channel_id": "evaluation_result_public",
                "notice_family": "award",
                "notice_type_raw": "评标结果公开",
                "entry_url": CHENGDU_JSGC_LIST_URL,
                "displaytypeval": "2",
            },
            {
                "channel_id": "evaluation_result_notice",
                "notice_family": "award",
                "notice_type_raw": "评标结果公示",
                "entry_url": CHENGDU_JSGC_LIST_URL,
                "displaytypeval": "12",
            },
            {
                "channel_id": "termination_notice",
                "notice_family": "tombstone",
                "notice_type_raw": "流标或终止公告",
                "entry_url": CHENGDU_JSGC_LIST_URL,
                "displaytypeval": "14",
            },
        ],
    )
    parser = config["parser"]["parsers"][CHENGDU_TRADING_PARSER_VERSION]
    parser["list_url"] = CHENGDU_JSGC_LIST_URL
    parser["form_post"] = {
        "displaytype_field": "displaytypeval",
        "page_field": "hidCrunt",
        "page_size_field": "hidLimit",
        "reload_field": "hidReload",
    }
    parser["attachment_rules"]["opaque_container_extensions"] = CHENGDU_OPAQUE_CONTAINER_EXTENSIONS
    parser["red_lines"] = [
        "do_not_decompress_container",
        "do_not_parse_priced_source",
        "do_not_group_project_code",
        "gated_metadata_only",
    ]
    config["ops"]["tls"] = {"openssl_ciphers": "DEFAULT:@SECLEVEL=1"}
    return config


def discover_chengdu_trading_notices(
    client: ChengduTradingClient,
    *,
    notice_type_value: str,
    notice_type_raw: str,
    channel_id: str,
    notice_family: str,
    page: int = 1,
    page_size: int = 10,
    start_date: str | date | None = None,
    end_date: str | date | None = None,
) -> list[ChengduNoticeSummary]:
    list_html = client.get_text(CHENGDU_JSGC_LIST_URL)
    form = _hidden_inputs(list_html)
    start_date_value = _date_input(start_date)
    end_date_value = _date_input(end_date)
    form.update(
        {
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "txt_keyword": "",
            "displaytypeval": notice_type_value,
            "displaystateval": "0",
            "dealaddressval": "0",
            "divpudate": "5" if start_date_value or end_date_value else "0",
            "inputTime1": start_date_value,
            "inputTime2": end_date_value,
            "hidCrunt": str(page),
            "hidLimit": str(page_size),
            "hidReload": "true",
            "btnGcSearch": "查询",
        }
    )
    filtered_html = client.post_form(CHENGDU_JSGC_LIST_URL, form)
    return _parse_notice_rows(
        filtered_html,
        notice_type_value=notice_type_value,
        notice_type_raw=notice_type_raw,
        channel_id=channel_id,
        notice_family=notice_family,
    )


def ingest_chengdu_trading_notice(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: ChengduTradingClient,
    summary: ChengduNoticeSummary,
    actor_id: str = "chengdu-trading-crawler",
) -> Archive:
    source = require_data_source(session, source_id, domain_type="trading")

    detail_html = client.get_text(summary.detail_url)
    detail = _parse_notice_detail(detail_html, summary=summary)
    title = detail["title"]
    publish_date = detail["publish_date"]
    source_item_key = detail["source_item_key"]
    fetched_at = _fetched_at_from_publish_date(detail["publish_date"])
    attachments = [
        TradingAttachment(
            file_name=f"{detail['source_item_key']}.html",
            content=detail_html.encode("utf-8"),
            url=summary.detail_url,
            content_type="text/html; charset=utf-8",
            file_role="web_snapshot",
        )
    ]
    for attachment in detail["attachments"]:
        try:
            content, content_type = client.get_bytes(attachment.url)
        except Exception as exc:
            raise ChengduAttachmentDownloadError(
                source_item_key=source_item_key,
                title=title,
                attachment=attachment,
                error_code=_chengdu_attachment_error_code(exc),
                reason=str(exc),
            ) from exc
        if not content:
            raise ChengduAttachmentDownloadError(
                source_item_key=source_item_key,
                title=title,
                attachment=attachment,
                error_code="CHENGDU_ATTACHMENT_EMPTY",
                reason=f"empty attachment: {attachment.url}",
            )
        attachments.append(
            TradingAttachment(
                file_name=attachment.file_name,
                content=content,
                url=attachment.url,
                content_type=content_type,
                file_role=_chengdu_attachment_file_role(attachment),
                metadata={"source_attachment_label": attachment.category_raw} if attachment.category_raw else {},
            )
        )

    item = TradingDiscoveredItem(
        source_item_key=source_item_key,
        title=title,
        publish_date=publish_date,
        detail_url=summary.detail_url,
        channel_id=summary.channel_id,
        notice_family=summary.notice_family,
        notice_type_raw=summary.notice_type_raw,
        project_code_raw=detail.get("project_code_raw"),
        tender_code_raw=detail.get("tender_code_raw"),
        section_no_raw=detail.get("section_no_raw"),
        bid_subject_role_raw=detail.get("bid_subject_role_raw"),
        tenderer_raw=detail.get("tenderer_raw"),
        agency_raw=detail.get("agency_raw"),
        amount_raw=detail.get("amount_raw"),
        exchange_name="成都市公共资源交易服务中心",
        column_path_raw="交易公告 / 工程建设",
        project_name_raw=detail.get("project_name_raw"),
        discovered_at=fetched_at,
        fetched_at=fetched_at,
        attachments=attachments,
        metadata={
            "source_item_key": source_item_key,
            "region_raw": summary.region_raw,
            "status_raw": summary.status_raw,
            "displaytypeval": summary.displaytypeval,
            "attachment_count": len(detail["attachments"]),
            "opaque_container_count": _opaque_container_count(detail["attachments"]),
            "content_source": detail.get("content_source"),
        },
        business_key=_chengdu_business_key(
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


def run_chengdu_trading_channels(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: ChengduTradingClient,
    channel_ids: list[str] | None = None,
    max_pages: int = 1,
    page_size: int = 10,
    max_items_per_channel: int | None = None,
    actor_id: str = "chengdu-trading-crawler",
    as_of_date: str | date | None = None,
    start_date: str | date | None = None,
    end_date: str | date | None = None,
    stop_on_existing: bool = True,
) -> dict[str, object]:
    source = require_data_source(session, source_id, domain_type="trading")
    resolved_source_id = source.source_id
    config = source.config or chengdu_trading_source_config()
    parser = config["parser"]["parsers"][CHENGDU_TRADING_PARSER_VERSION]

    def discover_page(channel: dict[str, object], page: int, page_size: int) -> list[ChengduNoticeSummary]:
        return discover_chengdu_trading_notices(
            client,
            notice_type_value=str(channel["displaytypeval"]),
            notice_type_raw=str(channel["notice_type_raw"]),
            channel_id=str(channel["channel_id"]),
            notice_family=str(channel["notice_family"]),
            page=page,
            page_size=page_size,
            start_date=start_date,
            end_date=end_date,
        )

    def business_key_for(summary: ChengduNoticeSummary) -> str:
        return _chengdu_business_key(
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
        ingest=lambda summary: ingest_chengdu_trading_notice(
            session,
            storage,
            source_id=resolved_source_id,
            client=client,
            summary=summary,
            actor_id=actor_id,
        ),
        on_ingest_error=lambda summary, exc: _record_chengdu_ingest_failure(
            session,
            source_id=resolved_source_id,
            actor_id=actor_id,
            summary=summary,
            exc=exc,
        ),
        max_pages=max_pages,
        page_size=page_size,
        max_items_per_channel=max_items_per_channel,
        as_of_date=as_of_date,
        stop_on_existing=stop_on_existing,
    )


def _chengdu_business_key(
    *,
    source_id: str,
    source_item_key: str,
) -> str:
    return f"trading:{source_id}:notice:{normalize_key_text(source_item_key)}"


def _date_input(value: str | date | None) -> str:
    if value is None:
        return ""
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _chengdu_attachment_error_code(exc: Exception) -> str:
    if isinstance(exc, (TimeoutError, SourceRegistryReadTimeout)):
        return "CHENGDU_ATTACHMENT_DOWNLOAD_TIMEOUT"
    return "CHENGDU_ATTACHMENT_DOWNLOAD_FAILED"


def _record_chengdu_ingest_failure(
    session: Session,
    *,
    source_id: str,
    actor_id: str,
    summary: ChengduNoticeSummary,
    exc: Exception,
) -> dict[str, object]:
    # A failed SQL flush leaves the session unusable until rollback.  The
    # channel runner deliberately handles individual notice failures, so it
    # must restore the session before writing the audit event and continuing.
    session.rollback()
    failed_at = utcnow().isoformat()
    if isinstance(exc, ChengduAttachmentDownloadError):
        payload = {
            "source_item_key": exc.source_item_key,
            "title": exc.title,
            "attachment_url": exc.attachment.url,
            "file_name": exc.attachment.file_name,
            "error": exc.reason,
            "failed_at": failed_at,
            "error_kind": "attachment_timeout"
            if exc.error_code == "CHENGDU_ATTACHMENT_DOWNLOAD_TIMEOUT"
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
            error_code="CHENGDU_NOTICE_INGEST_FAILED",
            before_payload=None,
            after_payload=payload,
        )
    )
    session.flush()
    return {"error_code": "CHENGDU_NOTICE_INGEST_FAILED", **payload}


class _InputParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inputs: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "input":
            return
        payload = {key: value or "" for key, value in attrs}
        name = payload.get("name")
        if name:
            self.inputs[name] = payload.get("value", "")


def _hidden_inputs(html: str) -> dict[str, str]:
    parser = _InputParser()
    parser.feed(html)
    return dict(parser.inputs)


def _parse_notice_rows(
    html: str,
    *,
    notice_type_value: str,
    notice_type_raw: str,
    channel_id: str,
    notice_family: str,
) -> list[ChengduNoticeSummary]:
    rows: list[ChengduNoticeSummary] = []
    for raw_row in html.split('<div class="list-row">')[1:]:
        row = raw_row.split('<div class="list-row">', 1)[0]
        link = re.search(r'<a[^>]+href="([^"]*NoticeContent\.aspx\?id=[^"]+)"[^>]*>(.*?)</a>', row, flags=re.S)
        if link is None:
            continue
        detail_url = unescape(link.group(1)).strip()
        title = _clean_html(link.group(2))
        publish_match = re.search(r'(20\d{2}-\d{2}-\d{2})', row)
        region_match = re.search(r'【([^】]+)】', row)
        status = _extract_status(row)
        rows.append(
            ChengduNoticeSummary(
                source_item_key=_source_item_key_from_url(detail_url),
                title=title,
                detail_url=detail_url,
                publish_date=publish_match.group(1) if publish_match else None,
                region_raw=region_match.group(1) if region_match else None,
                status_raw=status,
                notice_type_raw=notice_type_raw,
                channel_id=channel_id,
                notice_family=notice_family,
                displaytypeval=notice_type_value,
            )
        )
    return rows


def _parse_notice_detail(html: str, *, summary: ChengduNoticeSummary) -> dict[str, object]:
    title = _meta_content(html, "ArticleTitle") or summary.title
    pub_date = _meta_content(html, "PubDate")
    publish_date = pub_date[:10] if pub_date else summary.publish_date
    source_item_key = _hidden_value(html, "ContentPlaceHolder1_hidNoticeid") or summary.source_item_key
    section_no_raw = _labeled_text(html, "标段名称", "标段编号")
    project_name_raw = _labeled_text(html, "项目名称") or _project_name_from_section(section_no_raw, title)
    bid_subject_role_raw = (
        _labeled_text(html, "标的角色", "标段类型", "招标类别")
        or _bid_subject_role_from_text(section_no_raw, title)
    )
    return {
        "title": title,
        "publish_date": publish_date,
        "source_item_key": source_item_key,
        "content_source": _meta_content(html, "ContentSource"),
        "project_name_raw": project_name_raw,
        "project_code_raw": _labeled_text(html, "项目编号", "项目代码"),
        "tender_code_raw": _labeled_text(html, "招标编号", "招标项目编号"),
        "section_no_raw": section_no_raw,
        "bid_subject_role_raw": bid_subject_role_raw,
        "tenderer_raw": _phrase_text(html, "招标人为", "项目业主为", "建设单位为")
        or _labeled_text(html, "招标人", "建设单位", "项目业主"),
        "agency_raw": _phrase_text(html, "招标代理机构是", "招标代理机构为", "代理机构为")
        or _labeled_text(html, "招标代理机构", "代理机构"),
        "amount_raw": _labeled_text(html, "招标控制价", "控制价", "中标价", "合同估算价"),
        "attachments": _parse_attachments(html),
    }


def _parse_attachments(html: str) -> list[ChengduAttachment]:
    attachments: list[ChengduAttachment] = []
    for block in re.findall(r'<div class="file-item">(.*?)</div>', html, flags=re.S):
        url_match = re.search(r"DownloadFile\('([^']+)'\)", block)
        name_match = re.search(r'class="file-name">(.*?)</a>', block, flags=re.S)
        if url_match is None or name_match is None:
            continue
        category_match = re.search(r"\[([^\]]+)\]", block)
        attachments.append(
            ChengduAttachment(
                file_name=_clean_html(name_match.group(1)),
                url=unescape(url_match.group(1)).strip(),
                category_raw=_clean_html(category_match.group(1)) if category_match else None,
            )
        )
    return attachments


def _meta_content(html: str, name: str) -> str | None:
    match = re.search(rf'<meta[^>]+name="{re.escape(name)}"[^>]+content="([^"]*)"', html, flags=re.I)
    if match is None:
        return None
    return unescape(match.group(1)).strip()


def _hidden_value(html: str, element_id: str) -> str | None:
    match = re.search(rf'<input[^>]+id="{re.escape(element_id)}"[^>]+value="([^"]*)"', html, flags=re.I)
    if match is None:
        return None
    return unescape(match.group(1)).strip()


def _source_item_key_from_url(url: str) -> str:
    parsed = urlsplit(url)
    values = parse_qs(parsed.query).get("id")
    if values and values[0]:
        return values[0]
    raise ValueError(f"CHENGDU_NOTICE_ID_MISSING: {url}")


def _clean_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    text = re.sub(r"\s+", " ", unescape(text))
    return text.strip()


def _extract_status(row_html: str) -> str | None:
    text = _clean_html(row_html)
    for status in ("待开标", "已开标", "流标", "终止"):
        if status in text:
            return status
    return None


def _labeled_text(html: str, *labels: str) -> str | None:
    for label in labels:
        label_pattern = rf"{re.escape(label)}(?:[（(][^）)]*[）)])?"
        raw_match = re.search(
            rf"(?:^|[>\r\n])\s*(?:\d+(?:\.\d+)*[.．、]?\s*)?{label_pattern}\s*[:：]\s*([^<\r\n]+)",
            html,
        )
        if raw_match is not None:
            value = _clean_l0_value(raw_match.group(1))
            if value:
                return value
    text = _clean_html(html)
    for label in labels:
        label_pattern = rf"{re.escape(label)}(?:[（(][^）)]*[）)])?"
        pattern = (
            rf"(?:^|\s)(?:\d+(?:\.\d+)*[.．、]?\s*)?{label_pattern}\s*[:：]\s*"
            rf"(.+?)(?=\s+[^\s:：]{{2,20}}(?:[（(][^）)]*[）)])?\s*[:：]|$)"
        )
        match = re.search(pattern, text)
        if match is None:
            continue
        value = _clean_l0_value(match.group(1))
        return value or None
    return None


def _phrase_text(html: str, *phrases: str) -> str | None:
    text = _clean_html(html)
    for phrase in phrases:
        match = re.search(rf"{re.escape(phrase)}\s*(.+?)(?=。|，|,|；|;|$)", text)
        if match is None:
            continue
        value = _clean_l0_value(match.group(1))
        if value:
            return value
    return None


def _project_name_from_section(section_no_raw: str | None, title: str | None) -> str | None:
    source = _clean_l0_value(section_no_raw or title)
    if not source:
        return None
    value = re.sub(r"/?标段$", "", source)
    value = re.sub(r"(勘察设计|勘察|设计|施工|监理|造价咨询|造价|审计)$", "", value)
    value = re.sub(r"(招标公告|中标结果公布|评标结果公示|评标结果公开|变更公告)$", "", value)
    return _clean_l0_value(value)


def _bid_subject_role_from_text(*values: str | None) -> str | None:
    text = " ".join(value for value in values if value)
    for role in ("勘察设计", "造价咨询", "全过程咨询", "勘察", "设计", "施工", "监理", "造价", "审计"):
        if role in text:
            return role
    return None


def _clean_l0_value(value: str | None) -> str | None:
    text = _clean_html(value or "")
    text = text.strip(" \t\r\n。；;，,")
    if text in {"", "/", "／", "无"}:
        return None
    return text


def _chengdu_attachment_file_role(attachment: ChengduAttachment) -> str | None:
    name = attachment.file_name.lower()
    category = attachment.category_raw or ""
    if name.endswith(".cdz") or any(
        keyword in category or keyword in attachment.file_name
        for keyword in ("工程量清单", "清单包", "招标控制价", "控制价")
    ):
        return "qingdan_package"
    if any(
        keyword in category or keyword in attachment.file_name
        for keyword in ("设计任务书", "任务书", "招标文件", "招标资料", "投标文件格式", "合同附件", "技术要求")
    ):
        return "tender_doc"
    if any(keyword in category or keyword in attachment.file_name for keyword in ("地勘", "勘察", "岩土")):
        return "geological"
    if any(keyword in category or keyword in attachment.file_name for keyword in ("工程图", "施工图", "设计图", "图纸")):
        return "drawing"
    return None


def _opaque_container_count(attachments: list[ChengduAttachment]) -> int:
    return count_opaque_attachments(attachments, CHENGDU_OPAQUE_CONTAINER_EXTENSIONS)


def _fetched_at_from_publish_date(publish_date: str | None) -> str | None:
    if not publish_date:
        return None
    return f"{publish_date}T00:00:00+08:00"


def _safe_url(url: str) -> str:
    return safe_url(url)
