"""Sichuan provincial public-resource engineering tender announcement crawler.

The Sichuan provincial transaction site exposes one public JSON search API for
the provincial level and every prefecture-level city / autonomous prefecture.
Each source code is deliberately modelled as a separate channel: a busy city
must not advance the incremental cursor for another city.

The primary archive is the public HTML announcement.  Attachments are mounted
as durable pending links and fetched separately, because several cities use
large or proprietary files hosted by their own download services.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from html import escape, unescape
from html.parser import HTMLParser
import json
import re
from typing import Protocol
from urllib.parse import urljoin, urlsplit

from sqlalchemy.orm import Session

from app.models import Archive, AuditLog, utcnow
from app.normalization import normalize_key_text
from app.source_adapter import (
    SourceRegistryHttpClient,
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

SICHUAN_TRADING_BASE_URL = "https://ggzyjy.sc.gov.cn"
SICHUAN_TRADING_ENTRY_URL = f"{SICHUAN_TRADING_BASE_URL}/jyxx/002001/transactionInfo.html"
SICHUAN_TRADING_SEARCH_API_URL = (
    f"{SICHUAN_TRADING_BASE_URL}/inteligentsearch/rest/esinteligentsearch/getFullTextDataNew"
)
SICHUAN_TRADING_PARSER_VERSION = "scggzy.jsgc.v2"
SICHUAN_TENDER_NOTICE_CATEGORY = "002001001"
SICHUAN_OPAQUE_CONTAINER_EXTENSIONS = [".zbid", ".zip", ".rar", ".7z", ".cdz", ".cqzf"]
SICHUAN_INCREMENTAL_LOOKBACK_DAYS = 10
SICHUAN_NOTICE_MIN_TEXT_LENGTH = 80


@dataclass(frozen=True)
class SichuanTradingSourceScope:
    source_code: str
    city_name: str
    region_code: str


# These are the official values rendered by the provincial engineering
# transaction search page.  S001 is retained for province-level projects.
SICHUAN_TRADING_SOURCE_SCOPES: tuple[SichuanTradingSourceScope, ...] = (
    SichuanTradingSourceScope("S001", "四川省", "510000"),
    SichuanTradingSourceScope("S002", "成都市", "510100"),
    SichuanTradingSourceScope("S003", "德阳市", "510600"),
    SichuanTradingSourceScope("S004", "绵阳市", "510700"),
    SichuanTradingSourceScope("S005", "内江市", "511000"),
    SichuanTradingSourceScope("S006", "乐山市", "511100"),
    SichuanTradingSourceScope("S007", "广元市", "510800"),
    SichuanTradingSourceScope("S008", "眉山市", "511400"),
    SichuanTradingSourceScope("S009", "自贡市", "510300"),
    SichuanTradingSourceScope("S010", "雅安市", "511800"),
    SichuanTradingSourceScope("S011", "宜宾市", "511500"),
    SichuanTradingSourceScope("S012", "攀枝花市", "510400"),
    SichuanTradingSourceScope("S013", "泸州市", "510500"),
    SichuanTradingSourceScope("S014", "遂宁市", "510900"),
    SichuanTradingSourceScope("S015", "广安市", "511600"),
    SichuanTradingSourceScope("S016", "南充市", "511300"),
    SichuanTradingSourceScope("S017", "达州市", "511700"),
    SichuanTradingSourceScope("S018", "资阳市", "512000"),
    SichuanTradingSourceScope("S019", "巴中市", "511900"),
    SichuanTradingSourceScope("S020", "阿坝州", "513200"),
    SichuanTradingSourceScope("S021", "甘孜州", "513300"),
    SichuanTradingSourceScope("S022", "凉山州", "513400"),
)
SICHUAN_SOURCE_SCOPE_BY_CODE = {scope.source_code: scope for scope in SICHUAN_TRADING_SOURCE_SCOPES}


class SichuanTradingClient(Protocol):
    def post_json(self, url: str, payload: dict[str, object]) -> dict:
        ...

    def get_text(self, url: str) -> str:
        ...

    def get_bytes(self, url: str) -> tuple[bytes, str | None]:
        ...


@dataclass(frozen=True)
class SichuanNoticeSummary:
    source_item_key: str
    detail_url: str
    title: str
    publish_date: str | None
    publish_time: str | None
    category_num: str
    source_code: str
    city_name: str
    city_region_code: str
    exchange_name: str | None
    content: str
    notice_type_raw: str
    channel_id: str
    notice_family: str


@dataclass(frozen=True)
class SichuanTradingAttachment:
    file_name: str
    url: str
    source_label: str | None = None
    source_data_url: str | None = None


class _ElementTextCollector(HTMLParser):
    """Collect text from a named element while retaining nested markup."""

    def __init__(self, target_id: str) -> None:
        super().__init__(convert_charrefs=True)
        self.target_id = target_id
        self.depth = 0
        self.fragments: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.depth:
            self.depth += 1
            return
        if dict(attrs).get("id") == self.target_id:
            self.depth = 1

    def handle_endtag(self, tag: str) -> None:
        if self.depth:
            self.depth -= 1

    def handle_data(self, data: str) -> None:
        if self.depth:
            self.fragments.append(data)


class UrllibSichuanTradingClient(SourceRegistryHttpClient):
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
            referer=SICHUAN_TRADING_ENTRY_URL,
            timeout=timeout,
            min_interval_seconds=min_interval_seconds,
            jitter_seconds=jitter_seconds,
            read_timeout_seconds=attachment_read_timeout_seconds,
            read_total_timeout_seconds=attachment_total_timeout_seconds,
        )


def sichuan_trading_source_config() -> dict[str, object]:
    channels = [
        {
            "channel_id": _channel_id(scope.source_code),
            "notice_family": "tender",
            "notice_type_raw": "招标公告",
            "category_num": SICHUAN_TENDER_NOTICE_CATEGORY,
            "source_code": scope.source_code,
            "city_name": scope.city_name,
            "city_region_code": scope.region_code,
            "entry_url": SICHUAN_TRADING_ENTRY_URL,
        }
        for scope in SICHUAN_TRADING_SOURCE_SCOPES
    ]
    config = trading_exchange_source_config(
        site_id="trading.scggzy.jsgc",
        publisher_name="四川省公共资源交易信息网（工程建设）",
        entry_url=SICHUAN_TRADING_ENTRY_URL,
        region_code="510000",
        parser_version=SICHUAN_TRADING_PARSER_VERSION,
        publisher_type="exchange_center",
        source_priority="official",
        crawl_strategy_tier="tier2_public_json_search_api",
        channels=channels,
    )
    stable = config["stable"]
    stable["province"] = "四川"
    stable["city"] = None

    parser = config["parser"]["parsers"][SICHUAN_TRADING_PARSER_VERSION]
    parser["scope"] = "provincewide_engineering_tender_notice_by_trading_source_code"
    parser["search_api_url"] = SICHUAN_TRADING_SEARCH_API_URL
    parser["detail_url_field"] = "linkurl"
    parser["pagination"] = {"mode": "offset", "offset_field": "pn", "page_size_field": "rn", "page_size": 12}
    parser["source_filter"] = {"field": "tradingsourcevalue", "mode": "equal"}
    parser["source_scopes"] = [
        {"source_code": scope.source_code, "city_name": scope.city_name, "region_code": scope.region_code}
        for scope in SICHUAN_TRADING_SOURCE_SCOPES
    ]
    # The official search endpoint returns the newest notices only when its
    # own two-field sort contract is used.  Keep a small date overlap too, so
    # every scheduled run can find late-indexed notices without drifting into
    # the legacy archive catalogue.
    parser["sort"] = {"ordernum": "0", "webdate": "0"}
    parser["incremental_lookback_days"] = SICHUAN_INCREMENTAL_LOOKBACK_DAYS
    parser["notice_body_policy"] = {
        "require_meaningful_body": True,
        "minimum_text_length": SICHUAN_NOTICE_MIN_TEXT_LENGTH,
        "fallback_order": ["portal_detail_html", "origin_url", "official_search_api_content"],
        "reject_placeholder_bodies": ["见公告", "详见公告", "暂无内容"],
    }
    parser["attachment_discovery"] = "detail_html.public_download_links"
    parser["attachment_rules"]["opaque_container_extensions"] = SICHUAN_OPAQUE_CONTAINER_EXTENSIONS
    parser["red_lines"] = [
        "do_not_decompress_container",
        "do_not_parse_priced_source",
        "do_not_group_project_code",
        "attachment_link_pending_fetch",
    ]

    config["ops"]["schedule"] = {
        "frequency": "daily",
        "timezone": "Asia/Shanghai",
        "scan_times": ["09:25", "13:25", "17:25"],
    }
    config["ops"]["queue"] = {
        "max_concurrent_per_host": 1,
        "min_delay_seconds": 0.8,
        "jitter_seconds": 0.3,
        "retry_backoff": "exponential",
        "max_pages_per_run": 2,
        "page_size": 12,
        "max_items_per_channel": 12,
    }
    # Verification intentionally exercises the four cities explored before
    # rollout, while the active loop scans every official source code.
    config["ops"]["verification"] = {
        "channel_ids": [_channel_id(code) for code in ("S003", "S004", "S011", "S013")],
        "max_pages": 1,
        "page_size": 1,
        "max_items_per_channel": 1,
    }
    return config


def discover_sichuan_trading_notices(
    client: SichuanTradingClient,
    *,
    category_num: str,
    source_code: str,
    notice_type_raw: str,
    channel_id: str,
    notice_family: str,
    offset: int = 0,
    page_size: int = 12,
    start_date: str | date | None = None,
    end_date: str | date | None = None,
) -> list[SichuanNoticeSummary]:
    scope = _source_scope(source_code)
    response = client.post_json(
        SICHUAN_TRADING_SEARCH_API_URL,
        _search_payload(
            category_num=category_num,
            source_code=scope.source_code,
            offset=offset,
            page_size=page_size,
            start_date=start_date,
            end_date=end_date,
        ),
    )
    result = _search_result(response)
    records = result.get("records") if isinstance(result, dict) else []
    if not isinstance(records, list):
        raise ValueError("SICHUAN_TRADING_SEARCH_RECORDS_MISSING")

    summaries: list[SichuanNoticeSummary] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        record_source_code = _text(record, "tradingsourcevalue") or scope.source_code
        # The endpoint should honour the source-code predicate.  Keep the
        # local guard so a broadened or malformed response cannot cross city
        # channels and hide a later city's incremental cursor.
        if record_source_code != scope.source_code:
            continue
        detail_path = _text(record, "linkurl")
        if not detail_path:
            continue
        record_category = _text(record, "categorynum") or category_num
        detail_url = urljoin(SICHUAN_TRADING_BASE_URL, detail_path)
        item_key = _text(record, "id") or f"{record_category}:{detail_path}"
        publish_time = _text(record, "webdate", "infodate")
        summaries.append(
            SichuanNoticeSummary(
                source_item_key=item_key,
                detail_url=detail_url,
                title=_text(record, "title", "titlenew") or item_key,
                publish_date=_date_prefix(publish_time),
                publish_time=publish_time,
                category_num=record_category,
                source_code=scope.source_code,
                city_name=scope.city_name,
                city_region_code=scope.region_code,
                exchange_name=_text(record, "zhuanzai"),
                content=_text(record, "content") or "",
                notice_type_raw=notice_type_raw,
                channel_id=channel_id,
                notice_family=notice_family,
            )
        )
    return sorted(summaries, key=lambda value: (value.publish_time or "", value.source_item_key), reverse=True)


def ingest_sichuan_trading_notice(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: SichuanTradingClient,
    summary: SichuanNoticeSummary,
    actor_id: str = "sichuan-trading-crawler",
) -> Archive:
    source = require_data_source(session, source_id, domain_type="trading")
    detail_html, resolved_detail_url, detail_resolution, notice_body_length, detail_fetch_error = _resolve_notice_document(
        client,
        summary=summary,
    )

    title = _detail_title(detail_html) or summary.title
    publish_date = _date_prefix(_detail_text(detail_html, "date")) or summary.publish_date
    attachments = _discover_attachments(detail_html, detail_url=resolved_detail_url, title=title)
    mounted_attachments: list[TradingAttachment] = [
        TradingAttachment(
            file_name=f"{_safe_file_stem(summary.source_item_key)}.html",
            content=detail_html.encode("utf-8"),
            url=resolved_detail_url,
            content_type="text/html; charset=utf-8",
            file_role="web_snapshot",
            metadata={"source_preview": detail_resolution},
        )
    ]
    for attachment in attachments:
        mounted_attachments.append(
            TradingAttachment(
                file_name=attachment.file_name,
                content=None,
                url=attachment.url,
                file_role=_sichuan_attachment_file_role(attachment),
                fetch_status=FETCH_STATUS_PENDING,
                metadata={
                    "access_status": "PUBLIC_PENDING_FETCH",
                    "source_attachment_label": attachment.source_label,
                    "source_data_url": attachment.source_data_url,
                    "city_source_code": summary.source_code,
                },
            )
        )

    item = TradingDiscoveredItem(
        source_item_key=summary.source_item_key,
        title=title,
        publish_date=publish_date,
        detail_url=resolved_detail_url,
        channel_id=summary.channel_id,
        notice_family=summary.notice_family,
        notice_type_raw=summary.notice_type_raw,
        region_code=summary.city_region_code,
        project_code_raw=_labeled_text(detail_html, "项目编号", "项目代码"),
        tender_code_raw=_labeled_text(detail_html, "招标编号", "招标项目编号"),
        section_no_raw=_labeled_text(detail_html, "标段编号", "标段名称"),
        tenderer_raw=_phrase_text(detail_html, "招标人为", "项目业主为", "建设单位为")
        or _labeled_text(detail_html, "招标人", "项目业主", "建设单位"),
        agency_raw=_phrase_text(detail_html, "招标代理机构是", "招标代理机构为", "代理机构为")
        or _labeled_text(detail_html, "招标代理机构", "代理机构"),
        amount_raw=_labeled_text(detail_html, "招标控制价", "控制价", "合同估算价", "投资额"),
        exchange_name=summary.exchange_name or "四川省公共资源交易信息网",
        column_path_raw=f"交易信息 / 工程建设 / 招标公告 / {summary.city_name}",
        project_name_raw=title,
        discovered_at=_fetched_at_from_publish_date(publish_date),
        fetched_at=_fetched_at_from_publish_date(publish_date),
        attachments=mounted_attachments,
        metadata={
            "source_item_key": summary.source_item_key,
            "category_num": summary.category_num,
            "trading_source_code": summary.source_code,
            "city_name": summary.city_name,
            "city_region_code": summary.city_region_code,
            "exchange_name": summary.exchange_name,
            "publish_time": summary.publish_time,
            "attachment_count": len(attachments),
            "opaque_container_count": count_opaque_attachments(attachments, SICHUAN_OPAQUE_CONTAINER_EXTENSIONS),
            "attachment_discovery": "official_detail_html.public_download_links",
            "portal_detail_url": summary.detail_url,
            "resolved_detail_url": resolved_detail_url,
            "detail_resolution": detail_resolution,
            "notice_body_length": notice_body_length,
            "detail_fetch_error": detail_fetch_error,
        },
        business_key=_sichuan_business_key(source_id=source.source_id, source_item_key=summary.source_item_key),
    )
    return ingest_trading_registry_item(session, storage, source_id=source.source_id, item=item, actor_id=actor_id)


def run_sichuan_trading_channels(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: SichuanTradingClient,
    channel_ids: list[str] | None = None,
    max_pages: int = 1,
    page_size: int = 12,
    max_items_per_channel: int | None = None,
    actor_id: str = "sichuan-trading-crawler",
    as_of_date: str | date | None = None,
    start_date: str | date | None = None,
    end_date: str | date | None = None,
    stop_on_existing: bool = True,
) -> dict[str, object]:
    source = require_data_source(session, source_id, domain_type="trading")
    config = source.config or sichuan_trading_source_config()
    parser = config["parser"]["parsers"][SICHUAN_TRADING_PARSER_VERSION]
    effective_start_date, effective_end_date = _sichuan_discovery_date_window(
        start_date=start_date,
        end_date=end_date,
        as_of_date=as_of_date,
        lookback_days=int(parser.get("incremental_lookback_days") or SICHUAN_INCREMENTAL_LOOKBACK_DAYS),
    )

    def discover_page(channel: dict[str, object], page: int, requested_page_size: int) -> list[SichuanNoticeSummary]:
        return discover_sichuan_trading_notices(
            client,
            category_num=str(channel["category_num"]),
            source_code=str(channel["source_code"]),
            notice_type_raw=str(channel["notice_type_raw"]),
            channel_id=str(channel["channel_id"]),
            notice_family=str(channel["notice_family"]),
            offset=(page - 1) * min(max(1, requested_page_size), 12),
            page_size=requested_page_size,
            start_date=effective_start_date,
            end_date=effective_end_date,
        )

    return run_incremental_channel_crawl(
        source_id=source.source_id,
        channels=parser["channels"],
        selected_channel_ids=channel_ids,
        discover_page=discover_page,
        business_key_for=lambda summary: _sichuan_business_key(
            source_id=source.source_id, source_item_key=summary.source_item_key
        ),
        archive_exists=lambda business_key: archive_business_key_exists(
            session,
            tenant_code=source.asset_tenant_code,
            domain_type="trading",
            business_key=business_key,
        ),
        ingest=lambda summary: ingest_sichuan_trading_notice(
            session,
            storage,
            source_id=source.source_id,
            client=client,
            summary=summary,
            actor_id=actor_id,
        ),
        on_ingest_error=lambda summary, exc: _record_sichuan_ingest_failure(
            session, source_id=source.source_id, actor_id=actor_id, summary=summary, exc=exc
        ),
        max_pages=max(1, max_pages),
        page_size=min(max(1, page_size), 12),
        max_items_per_channel=max_items_per_channel,
        as_of_date=as_of_date,
        stop_on_existing=stop_on_existing,
    )


def run_sichuan_pending_attachment_fetches(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: SichuanTradingClient | None = None,
    limit: int = 1,
    actor_id: str = "sichuan-trading-offpeak-fetch",
) -> dict[str, object]:
    fetch_client = client or UrllibSichuanTradingClient(
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


def _channel_id(source_code: str) -> str:
    return f"tender_notice_{source_code.lower()}"


def _source_scope(source_code: str) -> SichuanTradingSourceScope:
    scope = SICHUAN_SOURCE_SCOPE_BY_CODE.get(str(source_code).upper())
    if scope is None:
        raise ValueError(f"SICHUAN_TRADING_SOURCE_CODE_UNKNOWN: {source_code}")
    return scope


def _search_payload(
    *,
    category_num: str,
    source_code: str,
    offset: int,
    page_size: int,
    start_date: str | date | None,
    end_date: str | date | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "token": "",
        "pn": max(0, offset),
        "rn": min(max(1, page_size), 12),
        "sdt": "",
        "edt": "",
        "wd": "",
        "inc_wd": "",
        "exc_wd": "",
        "fields": "",
        "cnum": "001",
        # This is the portal's documented contract.  Extra sort fields cause
        # its legacy index to return early-2010s records before current ones.
        "sort": '{"ordernum":"0","webdate":"0"}',
        "ssort": "",
        "cl": 10000,
        "terminal": "",
        "highlights": "",
        "statistics": None,
        "unionCondition": None,
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
            },
            {
                "fieldName": "tradingsourcevalue",
                "equal": source_code,
                "notEqual": None,
                "equalList": None,
                "notEqualList": None,
            },
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


def _search_result(response: dict) -> dict:
    content = response.get("content") if isinstance(response, dict) else None
    if isinstance(content, str) and content.lstrip().startswith("{"):
        response = json.loads(content)
    result = response.get("result") if isinstance(response, dict) else None
    return result if isinstance(result, dict) else {}


def _sichuan_discovery_date_window(
    *,
    start_date: str | date | None,
    end_date: str | date | None,
    as_of_date: str | date | None,
    lookback_days: int,
) -> tuple[str | date | None, str | date | None]:
    """Use an overlap window for normal polling; preserve explicit backfills."""

    if start_date is not None or end_date is not None:
        return start_date, end_date
    end = _coerce_date(as_of_date) or date.today()
    start = end - timedelta(days=max(1, lookback_days))
    return start, end


def _resolve_notice_document(
    client: SichuanTradingClient,
    *,
    summary: SichuanNoticeSummary,
) -> tuple[str, str, str, int, str | None]:
    """Resolve a real notice body, never retaining the portal's shell page."""

    detail_html = ""
    detail_fetch_error: str | None = None
    try:
        detail_html = client.get_text(summary.detail_url)
    except Exception as exc:  # noqa: BLE001 - preserve the official API fallback below.
        detail_fetch_error = str(exc)

    if detail_html.strip():
        portal_body = _notice_body_text(detail_html)
        if _meaningful_notice_body(portal_body):
            return detail_html, summary.detail_url, "official_portal_detail_html", len(portal_body), None

        origin_url = _origin_url(detail_html, detail_url=summary.detail_url)
        if origin_url:
            try:
                origin_html = client.get_text(origin_url)
            except Exception as exc:  # noqa: BLE001 - the API body remains an official fallback.
                detail_fetch_error = str(exc)
            else:
                origin_body = _notice_body_text(origin_html)
                if _meaningful_notice_body(origin_body):
                    return origin_html, origin_url, "official_origin_url_html", len(origin_body), None

    # The search endpoint is itself an official public API and often contains
    # the complete notice even where the legacy detail endpoint only says
    # "见公告".  Preserve that genuine body in a small, auditable HTML envelope.
    if _meaningful_notice_body(summary.content):
        snapshot = _search_api_content_snapshot(summary)
        resolution = (
            "official_search_api_content_after_detail_fetch_error"
            if detail_fetch_error
            else "official_search_api_content"
        )
        return snapshot, summary.detail_url, resolution, len(_clean_html(summary.content)), detail_fetch_error

    if detail_fetch_error:
        raise ValueError(f"SICHUAN_TRADING_DETAIL_FETCH_FAILED: {detail_fetch_error}")
    if not detail_html.strip():
        raise ValueError("SICHUAN_TRADING_DETAIL_EMPTY")
    raise ValueError("SICHUAN_TRADING_NOTICE_BODY_UNAVAILABLE")


def _notice_body_text(html: str) -> str:
    normalized_html = html.replace("<![CDATA[", "").replace("]]>", "")
    for element_id in ("newsText", "articleContent", "content", "mainContent"):
        collector = _ElementTextCollector(element_id)
        collector.feed(normalized_html)
        collector.close()
        body = _clean_html(" ".join(collector.fragments))
        if body:
            return body
    article = re.search(r"<article\b[^>]*>(.*?)</article>", html, flags=re.I | re.S)
    return _clean_html(article.group(1)) if article else ""


def _meaningful_notice_body(value: str) -> bool:
    normalized = re.sub(r"\s+", "", _clean_html(value))
    if normalized in {"", "见公告", "详见公告", "暂无内容", "详情见附件"}:
        return False
    if "{{" in normalized or "}}" in normalized:
        return False
    if len(normalized) < SICHUAN_NOTICE_MIN_TEXT_LENGTH:
        return False
    return bool(re.search(r"招标|投标|工程|项目|采购", normalized))


def _origin_url(html: str, *, detail_url: str) -> str | None:
    for match in re.finditer(r"<a\b(?P<attrs>[^>]*)>", html, flags=re.I | re.S):
        attrs = _html_attributes(match.group("attrs"))
        if attrs.get("id") != "originurl_a":
            continue
        raw_url = unescape(attrs.get("data-value", "")).strip()
        if not raw_url or raw_url == "0":
            return None
        candidate = urljoin(detail_url, raw_url)
        return candidate if urlsplit(candidate).scheme in {"http", "https"} else None
    return None


def _search_api_content_snapshot(summary: SichuanNoticeSummary) -> str:
    body = escape(_clean_html(summary.content)).replace("\n", "<br />\n")
    title = escape(summary.title)
    published_at = escape(summary.publish_time or summary.publish_date or "")
    source_url = escape(summary.detail_url, quote=True)
    return (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\" />"
        f"<title>{title}</title></head><body><article data-source=\"official-search-api\">"
        f"<h1>{title}</h1><p>发布时间：{published_at}</p><p>原详情页：<a href=\"{source_url}\">{source_url}</a></p>"
        f"<section id=\"newsText\">{body}</section></article></body></html>"
    )


def _discover_attachments(html: str, *, detail_url: str, title: str) -> list[SichuanTradingAttachment]:
    attachments: list[SichuanTradingAttachment] = []
    seen_urls: set[str] = set()
    for match in re.finditer(r"<a\b(?P<attrs>[^>]*)>(?P<label>.*?)</a>", html, flags=re.I | re.S):
        attrs = _html_attributes(match.group("attrs"))
        href = unescape(attrs.get("href", "")).strip()
        data_url = unescape(attrs.get("data-url", "")).strip()
        css_class = attrs.get("class", "").lower()
        raw_url = data_url if _usable_attachment_url(data_url) else href
        if not _usable_attachment_url(raw_url):
            continue
        if "{{" in raw_url or "{{" in match.group("label"):
            continue
        candidate = raw_url.lower()
        if not (
            "attachurl" in css_class
            or any(token in candidate for token in ("download", "attach", "filetransfer", "/document/"))
            or re.search(r"\.(?:pdf|docx?|xlsx?|zip|rar|7z|zbid|cdz|cqzf)(?:[?#]|$)", candidate)
        ):
            continue
        url = urljoin(detail_url, raw_url)
        if url in seen_urls:
            continue
        seen_urls.add(url)
        label = _clean_html(match.group("label"))
        file_name = label if label and label not in {"附件", "下载"} else _fallback_attachment_name(title, url, len(attachments) + 1)
        attachments.append(
            SichuanTradingAttachment(
                file_name=file_name,
                url=url,
                source_label=label or None,
                source_data_url=data_url or None,
            )
        )
    return attachments


def _html_attributes(value: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for name, _quote, raw_value in re.findall(r"([\w:-]+)\s*=\s*(['\"])(.*?)\2", value, flags=re.S):
        attrs[name.lower()] = raw_value
    return attrs


def _usable_attachment_url(value: str) -> bool:
    normalized = value.strip().lower()
    return bool(normalized) and not normalized.startswith(("javascript:", "#", "mailto:"))


def _fallback_attachment_name(title: str, url: str, ordinal: int) -> str:
    path = urlsplit(url).path.rsplit("/", maxsplit=1)[-1]
    if path and "." in path and len(path) <= 180:
        return path
    return f"{_safe_file_stem(title)}-附件{ordinal}"


def _detail_title(html: str) -> str | None:
    match = re.search(r"<h[1-6][^>]*\bid=['\"]title['\"][^>]*>(.*?)</h[1-6]>", html, flags=re.I | re.S)
    return _clean_html(match.group(1)) if match else None


def _detail_text(html: str, element_id: str) -> str | None:
    match = re.search(
        rf"<[^>]+\bid=['\"]{re.escape(element_id)}['\"][^>]*>(.*?)</[^>]+>", html, flags=re.I | re.S
    )
    return _clean_html(match.group(1)) if match else None


def _labeled_text(html: str, *labels: str) -> str | None:
    text = _clean_html(html)
    for label in labels:
        match = re.search(rf"{re.escape(label)}\s*[:：]\s*(.+?)(?=\s+[\u4e00-\u9fffA-Za-z]{{2,20}}\s*[:：]|$)", text)
        if match:
            value = _clean_l0_value(match.group(1))
            if value:
                return value
    return None


def _phrase_text(html: str, *phrases: str) -> str | None:
    text = _clean_html(html)
    for phrase in phrases:
        match = re.search(rf"{re.escape(phrase)}\s*(.+?)(?=。|，|,|；|;|$)", text)
        if match:
            value = _clean_l0_value(match.group(1))
            if value:
                return value
    return None


def _clean_l0_value(value: str) -> str | None:
    cleaned = _clean_html(value).strip(" \t\r\n。；;，,")
    return cleaned if cleaned and cleaned not in {"/", "／", "无"} else None


def _clean_html(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(value))).strip()


def _sichuan_attachment_file_role(attachment: SichuanTradingAttachment) -> str | None:
    name = attachment.file_name.lower()
    if any(word in attachment.file_name for word in ("地勘", "勘察", "岩土")):
        return "geological"
    if any(word in attachment.file_name for word in ("工程图", "施工图", "设计图", "图纸", "效果图")):
        return "drawing"
    if name.endswith((".zbid", ".cdz", ".cqzf", ".zbx", ".cjz", ".cos", ".qtfx")) or any(
        word in attachment.file_name for word in ("工程量清单", "清单包", "招标控制价", "最高投标限价", "控制价")
    ):
        return "qingdan_package"
    if any(word in attachment.file_name for word in ("招标公告", "招标文件", "任务书", "技术要求", "合同")):
        return "tender_doc"
    return None


def _record_sichuan_ingest_failure(
    session: Session,
    *,
    source_id: str,
    actor_id: str,
    summary: SichuanNoticeSummary,
    exc: Exception,
) -> dict[str, object]:
    session.rollback()
    payload = {
        "source_item_key": summary.source_item_key,
        "title": summary.title,
        "source_code": summary.source_code,
        "city_name": summary.city_name,
        "error": str(exc),
        "failed_at": utcnow().isoformat(),
        "error_kind": "notice_ingest_failed",
    }
    session.add(
        AuditLog(
            actor_type="system",
            actor_id=actor_id,
            action="TRADING_NOTICE_INGEST_FAILED",
            target_type="data_source",
            target_id=source_id,
            error_code="SICHUAN_NOTICE_INGEST_FAILED",
            before_payload=None,
            after_payload=payload,
        )
    )
    session.commit()
    return {"error_code": "SICHUAN_NOTICE_INGEST_FAILED", **payload}


def _sichuan_business_key(*, source_id: str, source_item_key: str) -> str:
    return f"trading:{source_id}:notice:{normalize_key_text(source_item_key)}"


def _date_input(value: str | date | None) -> str:
    if value is None:
        return ""
    return value.isoformat() if isinstance(value, date) else str(value)


def _coerce_date(value: str | date | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


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


def _safe_file_stem(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", value).strip("._")
    return cleaned[:160] or "sichuan-trading-notice"


def _fetched_at_from_publish_date(publish_date: str | None) -> str | None:
    return f"{publish_date}T00:00:00+08:00" if publish_date else None
