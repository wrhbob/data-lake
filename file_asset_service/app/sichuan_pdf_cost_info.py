from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
import json
import os
import re
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlsplit

from sqlalchemy.orm import Session

from app.archive_rules import build_cost_info_business_key
from app.source_adapter import archive_business_key_exists, crawl_lag_days, normalize_extension, require_data_source
from app.source_registry import CostInfoAttachment, CostInfoDiscoveredItem, ingest_cost_info_registry_item
from app.storage import ObjectStore

PANZHIHUA_PARSER_VERSION = "sichuan.panzhihua-pdf-list.v1"
LUZHOU_PARSER_VERSION = "sichuan.luzhou-pdf-list.v1"
DEYANG_PARSER_VERSION = "sichuan.deyang-pdf-list.v1"
SUINING_PARSER_VERSION = "sichuan.suining-pdf-list.v1"
LESHAN_PARSER_VERSION = "sichuan.leshan-pdf-list.v1"
DAZHOU_PARSER_VERSION = "sichuan.dazhou-ajax-iframe-pdf.v1"

PDF_PERIOD_REGEX = r"(20\d{2})年(?:第)?([一二三四五六七八九十]{1,3}|\d{1,2})(?:期|月)"
PDF_ALLOWED_EXTENSIONS = ["pdf"]
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class SichuanPdfCostInfoClient(Protocol):
    def get_text(self, url: str) -> str:
        ...

    def post_form_json(self, url: str, data: dict[str, object]) -> dict:
        ...

    def post_json(self, url: str, payload: dict[str, object]) -> dict:
        ...

    def get_bytes(self, url: str) -> tuple[bytes, str | None]:
        ...


@dataclass(frozen=True)
class SichuanPdfCitySource:
    key: str
    city_name: str
    region_code: str
    publisher: str
    entry_url: str
    parser_version: str
    title_keywords: tuple[str, ...]
    province_key: str = "sc"
    exclude_keywords: tuple[str, ...] = ()
    tax_type: str | None = None
    parsability: str = "text_pdf"
    source_attachment_mode: str = "pdf_only"
    allowed_extensions: tuple[str, ...] = tuple(PDF_ALLOWED_EXTENSIONS)
    list_strategy: str = "static_html"
    ajax_url: str | None = None
    ajax_form_data: dict[str, object] | None = None
    json_payload: dict[str, object] | None = None
    pagination_url_template: str | None = None
    max_pages: int = 1
    queue_min_delay_seconds: int = 3
    queue_jitter_seconds: int = 2
    user_agent: str | None = None


SICHUAN_PDF_CITY_SOURCE_DEFS: dict[str, SichuanPdfCitySource] = {
    "panzhihua": SichuanPdfCitySource(
        key="panzhihua",
        city_name="攀枝花市",
        region_code="510400",
        publisher="攀枝花市住房和城乡建设局",
        entry_url="http://zjj.panzhihua.gov.cn/ztzl/gczjxx/",
        parser_version=PANZHIHUA_PARSER_VERSION,
        title_keywords=("信息价", "材料价格"),
        exclude_keywords=("人工费调整", "清单计价定额"),
        tax_type="tax_exclusive",
    ),
    "luzhou": SichuanPdfCitySource(
        key="luzhou",
        city_name="泸州市",
        region_code="510500",
        publisher="泸州市住房和城乡建设局",
        entry_url="https://zjj.luzhou.gov.cn/hyfw/gczjxx/",
        parser_version=LUZHOU_PARSER_VERSION,
        title_keywords=("信息价", "材料价格", "工程造价信息"),
        parsability="mixed",
        source_attachment_mode="mixed_file_attachment",
        allowed_extensions=("pdf", "xls", "xlsx"),
    ),
    "deyang": SichuanPdfCitySource(
        key="deyang",
        city_name="德阳市",
        region_code="510600",
        publisher="德阳市人民政府",
        entry_url="https://www.deyang.gov.cn/info/iList.jsp?node_id=GKdyszf&cat_id=25723",
        parser_version=DEYANG_PARSER_VERSION,
        title_keywords=("建筑材料市场价格信息", "市场价格信息", "信息价", "材料价格", "工程造价信息"),
        pagination_url_template="https://www.deyang.gov.cn/info/iList.jsp?node_id=GKdyszf&site_id=CMSdeyang&cur_page={page}&cat_id=25723",
        max_pages=14,
        queue_min_delay_seconds=5,
        queue_jitter_seconds=5,
        user_agent=BROWSER_USER_AGENT,
    ),
    "suining": SichuanPdfCitySource(
        key="suining",
        city_name="遂宁市",
        region_code="510900",
        publisher="遂宁市住房和城乡建设局",
        entry_url="https://snjsj.suining.gov.cn/xinwen/list/58b7d65c2700de24f4d639bdf982a0b1.html",
        parser_version=SUINING_PARSER_VERSION,
        title_keywords=("建材价格", "信息价", "材料价格"),
        exclude_keywords=("厂商报价",),
    ),
    "leshan": SichuanPdfCitySource(
        key="leshan",
        city_name="乐山市",
        region_code="511100",
        publisher="乐山市住房和城乡建设局",
        entry_url="https://www.leshan.gov.cn/lsswszf/lssjzclscxxj/92337845/index.html",
        parser_version=LESHAN_PARSER_VERSION,
        title_keywords=("市场信息价", "信息价", "材料价格"),
        exclude_keywords=("厂商报价", "参考价"),
    ),
    "dazhou": SichuanPdfCitySource(
        key="dazhou",
        city_name="达州市",
        region_code="511700",
        publisher="达州市住房和城乡建设局",
        entry_url="http://zjj.dazhou.gov.cn/news-list-zaojiaxinxi.html",
        parser_version=DAZHOU_PARSER_VERSION,
        title_keywords=("造价信息", "信息价", "材料价格"),
        list_strategy="ajax_post_json",
        ajax_url="http://zjj.dazhou.gov.cn/api-ajax_list-1.html",
        ajax_form_data={
            "ajax_type[]": ["14_news", "51", "14", "news", "Y-m-d", "40", "15", "0"],
            "ajax_type[8]": "",
            "is_ds": "1",
        },
    ),
}

SICHUAN_PDF_CITY_SOURCE_CONFIGS = {key: source.parser_version for key, source in SICHUAN_PDF_CITY_SOURCE_DEFS.items()}


def panzhihua_cost_info_source_config() -> dict:
    return _source_config(SICHUAN_PDF_CITY_SOURCE_DEFS["panzhihua"])


def luzhou_cost_info_source_config() -> dict:
    return _source_config(SICHUAN_PDF_CITY_SOURCE_DEFS["luzhou"])


def deyang_cost_info_source_config() -> dict:
    return _source_config(SICHUAN_PDF_CITY_SOURCE_DEFS["deyang"])


def suining_cost_info_source_config() -> dict:
    return _source_config(SICHUAN_PDF_CITY_SOURCE_DEFS["suining"])


def leshan_cost_info_source_config() -> dict:
    return _source_config(SICHUAN_PDF_CITY_SOURCE_DEFS["leshan"])


def dazhou_cost_info_source_config() -> dict:
    return _source_config(SICHUAN_PDF_CITY_SOURCE_DEFS["dazhou"])


def _source_config(source: SichuanPdfCitySource) -> dict:
    parser = {
        "adapter_kind": "sichuan_pdf",
        "scope": "list_detail_multi_attachment_probe",
        "list_url": source.entry_url,
        "list_strategy": source.list_strategy,
        "period": {
            "source": "title",
            "regex": PDF_PERIOD_REGEX,
        },
        "title_keywords": list(source.title_keywords),
        "exclude_keywords": list(source.exclude_keywords),
        "period_required": True,
        "attachments": {
            "allowed": list(source.allowed_extensions),
            "probe_methods": ["a", "iframe", "embed", "object", "js_url", "ajax_field"],
            "required_any": list(source.allowed_extensions),
        },
    }
    if source.list_strategy == "ajax_post_json":
        parser["ajax"] = {
            "url": source.ajax_url,
            "form_data": source.ajax_form_data or {},
            "rows_path": "data",
        }
    if source.list_strategy == "gd_gkmlpt_api":
        parser["gkmlpt"] = {
            "api_url": source.ajax_url,
        }
    if source.list_strategy == "spa_json_file_api":
        parser["spa_json"] = {
            "api_url": source.ajax_url,
            "payload": source.json_payload or source.ajax_form_data or {},
            "download_url_template": "/download/{encoded_file}",
        }
    if source.list_strategy == "static_html" and source.pagination_url_template and source.max_pages > 1:
        parser["pagination"] = {
            "url_template": source.pagination_url_template,
            "max_pages": source.max_pages,
            "stop_on_error_after_rows": source.user_agent is not None,
        }

    return {
        "registry_schema_version": "source_registry.v1",
        "stable": {
            "site_id": f"cost_info.{source.province_key}.{source.key}",
            "domain_type": "cost_info",
            "region_code": source.region_code,
            "coverage_region_code": source.region_code,
            "producer": source.publisher,
            "publisher": source.publisher,
            "publisher_scope": "city",
            "publisher_region_code": source.region_code,
            "publisher_name": source.publisher,
            "publisher_type": "official_housing_urban_rural_development",
            "entry_url": source.entry_url,
            "periodicity": {"unit": "month", "period_key_format": "YYYY-MM"},
        },
        "price_coordinates": {
            "price_source_type": "info_price",
            "tax_type": source.tax_type,
        },
        "source_shape": {
            "publication_mode": "DIRECT_WEB",
            "source_attachment_mode": source.source_attachment_mode,
            "parsability": source.parsability,
        },
        "parser": {
            "active_parser_version": source.parser_version,
            "parsers": {
                source.parser_version: parser,
            },
        },
        "ops": {
            "enabled": True,
            "crawl_strategy_tier": "tier1_static_html"
            if source.list_strategy == "static_html"
            else "tier1_ajax_html",
            "schedule": {"frequency": "monthly"},
            "queue": {
                "max_concurrent_per_host": 1,
                "global_priority": 20,
                "min_delay_seconds": source.queue_min_delay_seconds,
                "jitter_seconds": source.queue_jitter_seconds,
                "retry_backoff": "exponential",
                "dead_letter_handoff": "017_manual_recovery",
            },
            "gated": False,
            "alert_thresholds": {"missing_period_count": 1},
            "http": {"user_agent": source.user_agent} if source.user_agent else {},
        },
    }


def list_sichuan_pdf_issues(client: SichuanPdfCostInfoClient, *, parser: dict) -> list[dict[str, object]]:
    if parser.get("list_strategy") == "ajax_post_json":
        ajax = parser.get("ajax") if isinstance(parser.get("ajax"), dict) else {}
        ajax_url = str(ajax.get("url") or "")
        payload = client.post_form_json(ajax_url, dict(ajax.get("form_data") or {}))
        return _rows_from_ajax_payload(payload, parser=parser, base_url=ajax_url)
    if parser.get("list_strategy") == "gd_gkmlpt_api":
        gkmlpt = parser.get("gkmlpt") if isinstance(parser.get("gkmlpt"), dict) else {}
        api_url = str(gkmlpt.get("api_url") or "")
        payload = json.loads(client.get_text(api_url))
        return _rows_from_gd_gkmlpt_payload(payload, parser=parser, base_url=api_url)
    if parser.get("list_strategy") == "spa_json_file_api":
        spa_json = parser.get("spa_json") if isinstance(parser.get("spa_json"), dict) else {}
        api_url = str(spa_json.get("api_url") or "")
        payload = client.post_json(api_url, dict(spa_json.get("payload") or {}))
        return _rows_from_spa_json_file_api_payload(payload, parser=parser, base_url=api_url)

    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for list_url in _static_list_urls(parser):
        try:
            html = client.get_text(list_url)
        except (HTTPError, URLError, TimeoutError):
            if rows and _pagination_stop_on_error_after_rows(parser):
                break
            raise
        parser_state = _SichuanIssueListParser(base_url=list_url, parser=parser)
        parser_state.feed(html)
        for row in parser_state.rows:
            key = _row_source_item_key(row)
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    return rows


def _static_list_urls(parser: dict) -> list[str]:
    list_url = str(parser["list_url"])
    pagination = parser.get("pagination") if isinstance(parser.get("pagination"), dict) else {}
    url_template = str(pagination.get("url_template") or "")
    max_pages = int(pagination.get("max_pages") or 1)
    if not url_template or max_pages <= 1:
        return [list_url]
    return [list_url, *[url_template.format(page=page) for page in range(2, max_pages + 1)]]


def _pagination_stop_on_error_after_rows(parser: dict) -> bool:
    pagination = parser.get("pagination") if isinstance(parser.get("pagination"), dict) else {}
    return bool(pagination.get("stop_on_error_after_rows"))


def ingest_sichuan_pdf_issue(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: SichuanPdfCostInfoClient,
    row: dict,
    parser: dict,
    actor_id: str = "sichuan-pdf-cost-info-crawler",
    task_id: str | None = None,
):
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or {}
    stable = config.get("stable") if isinstance(config.get("stable"), dict) else {}
    price_coordinates = config.get("price_coordinates") if isinstance(config.get("price_coordinates"), dict) else {}
    source_shape = config.get("source_shape") if isinstance(config.get("source_shape"), dict) else {}
    detail_url = _row_detail_url(row)
    attachments = _row_attachments(row)
    if not attachments:
        detail_html = client.get_text(detail_url)
        attachments = discover_cost_info_attachments(
            detail_html,
            detail_url=detail_url,
            allowed_extensions=_allowed_extensions(parser),
        )
    if not attachments and _url_extension(detail_url) in _allowed_extensions(parser):
        attachments = [_attachment_from_url(detail_url, discovery_method="detail_url_direct")]
    if not attachments:
        raise ValueError("SICHUAN_PDF_ATTACHMENTS_MISSING")

    downloaded = []
    download_errors = []
    for attachment in attachments:
        download_url = str(attachment["url"])
        file_name = str(attachment["file_name"])
        try:
            content, content_type = client.get_bytes(download_url)
        except Exception as exc:
            download_errors.append(f"{download_url}: {type(exc).__name__}")
            continue
        if not content:
            download_errors.append(f"{download_url}: empty")
            continue
        downloaded.append(
            CostInfoAttachment(
                file_name=file_name,
                content=content,
                url=download_url,
                content_type=content_type,
            )
        )
    if not downloaded:
        raise ValueError(f"SICHUAN_PDF_ATTACHMENTS_UNAVAILABLE: {download_errors}")

    discovery_methods = _ordered_unique(str(item.get("discovery_method") or "unknown") for item in attachments)
    attachment_mode, parsability = _shape_from_downloaded_attachments(
        downloaded,
        fallback_mode=source_shape.get("source_attachment_mode") or "pdf_only",
        fallback_parsability=source_shape.get("parsability") or "text_pdf",
    )
    item = CostInfoDiscoveredItem(
        title=_row_title(row),
        publish_date=_row_publish_date(row),
        detail_url=detail_url,
        discovered_at=_row_publish_date(row),
        fetched_at=datetime.now(UTC).isoformat(),
        attachments=downloaded,
        metadata={
            "listed_title": _row_title(row),
            "source_item_id": _row_source_item_key(row),
            "price_source_type": price_coordinates.get("price_source_type") or "info_price",
            "tax_type": price_coordinates.get("tax_type"),
            "coverage_region_code": stable.get("coverage_region_code") or source.region_code,
            "source_attachment_mode": attachment_mode,
            "parsability": parsability,
            "attachment_discovery_methods": discovery_methods,
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


def run_sichuan_pdf_incremental(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: SichuanPdfCostInfoClient,
    actor_id: str = "sichuan-pdf-cost-info-crawler",
    max_items: int | None = None,
    as_of_date: str | None = None,
    stop_on_existing: bool = True,
) -> dict[str, object]:
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or {}
    active_version = str((config.get("parser") or {}).get("active_parser_version") or "")
    parser = (config.get("parser") or {}).get("parsers", {}).get(active_version)
    if not isinstance(parser, dict):
        raise ValueError("SICHUAN_PDF_PARSER_CONFIG_MISSING")
    rows = list_sichuan_pdf_issues(client, parser=parser)
    ingested_count = 0
    skipped_existing_count = 0
    stopped_on_existing = False
    cursor_source_item_key = _row_source_item_key(rows[0]) if rows else None
    cursor_publish_date = _row_publish_date(rows[0]) if rows else None

    for row in rows:
        business_key = _business_key_for_row(source_id=source.source_id, region_code=source.region_code, row=row, parser=parser)
        if archive_business_key_exists(
            session,
            tenant_code=source.asset_tenant_code,
            domain_type="cost_info",
            business_key=business_key,
        ):
            skipped_existing_count += 1
            if stop_on_existing:
                stopped_on_existing = True
                break
            continue
        ingest_sichuan_pdf_issue(
            session,
            storage,
            source_id=source.source_id,
            client=client,
            row=row,
            parser=parser,
            actor_id=actor_id,
        )
        ingested_count += 1
        if max_items is not None and ingested_count >= max_items:
            break

    return {
        "source_id": source_id,
        "listed_count": len(rows),
        "ingested_count": ingested_count,
        "skipped_existing_count": skipped_existing_count,
        "stopped_on_existing": stopped_on_existing,
        "cursor_source_item_key": cursor_source_item_key,
        "cursor_publish_date": cursor_publish_date,
        "crawl_lag_days": crawl_lag_days(cursor_publish_date, as_of_date=as_of_date),
    }


class _SichuanIssueListParser(HTMLParser):
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, *, base_url: str, parser: dict):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.parser = parser
        self.rows: list[dict[str, object]] = []
        self._row_tag: str | None = None
        self._row_depth = 0
        self._row_text_parts: list[str] = []
        self._anchors: list[dict[str, str]] = []
        self._current_anchor: dict[str, object] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if self._row_tag is None:
            if self._is_row_start(tag, attr):
                self._start_row(tag)
            return

        if tag not in self.VOID_TAGS:
            self._row_depth += 1
        if tag == "a":
            href = attr.get("href")
            if not href:
                return
            self._current_anchor = {
                "href": href,
                "title": attr.get("title") or "",
                "text_parts": [],
            }

    def handle_data(self, data: str) -> None:
        if self._row_tag is not None:
            self._row_text_parts.append(data)
        if self._current_anchor is not None:
            self._current_anchor["text_parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._current_anchor is not None:
            title = str(self._current_anchor["title"] or "").strip()
            text = "".join(self._current_anchor["text_parts"]).strip()
            self._anchors.append(
                {
                    "href": str(self._current_anchor["href"]),
                    "title": title or text,
                }
            )
            self._current_anchor = None
        if self._row_tag is None or tag in self.VOID_TAGS:
            return
        self._row_depth = max(self._row_depth - 1, 0)
        if self._row_depth == 0:
            self._append_row_from_container()
            self._row_tag = None

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def _is_row_start(self, tag: str, attr: dict[str, str | None]) -> bool:
        if tag in {"li", "tr"}:
            return True
        classes = {part.strip() for part in str(attr.get("class") or "").split()}
        return tag == "div" and "list_div" in classes

    def _start_row(self, tag: str) -> None:
        self._row_tag = tag
        self._row_depth = 1
        self._row_text_parts = []
        self._anchors = []
        self._current_anchor = None

    def _append_row_from_container(self) -> None:
        row_text = " ".join(part.strip() for part in self._row_text_parts if part.strip())
        publish_date = _date_from_text(row_text)
        for anchor in self._anchors:
            title = _clean_issue_title(anchor["title"])
            if not _title_matches(title, self.parser):
                continue
            url = urljoin(self.base_url, anchor["href"])
            row: dict[str, object] = {
                "title": title,
                "publish_date": publish_date,
                "detail_url": url,
                "source_item_id": url,
            }
            if _url_extension(url) in _allowed_extensions(self.parser):
                row["attachments"] = [_attachment_from_url(url, discovery_method="list_direct")]
            self.rows.append(row)
            return


class _AttachmentProbeParser(HTMLParser):
    ATTRIBUTE_PROBES = {
        "a": ("href",),
        "iframe": ("src",),
        "embed": ("src",),
        "object": ("data",),
    }

    def __init__(self, *, detail_url: str, allowed_extensions: set[str]):
        super().__init__(convert_charrefs=True)
        self.detail_url = detail_url
        self.allowed_extensions = allowed_extensions
        self.attachments: list[dict[str, str]] = []
        self._seen: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        probes = self.ATTRIBUTE_PROBES.get(tag)
        if not probes:
            return
        attr = dict(attrs)
        for name in probes:
            value = attr.get(name)
            if value:
                self.add_candidate(value, discovery_method=tag)

    def add_candidate(self, value: str, *, discovery_method: str) -> None:
        url = urljoin(self.detail_url, unescape(value.strip()))
        if _url_extension(url) not in self.allowed_extensions:
            return
        if url in self._seen:
            return
        self._seen.add(url)
        self.attachments.append(
            {
                "file_name": _file_name_from_url(url),
                "url": url,
                "discovery_method": discovery_method,
            }
        )


def discover_cost_info_attachments(
    html: str,
    *,
    detail_url: str,
    allowed_extensions: list[str] | set[str] | tuple[str, ...],
) -> list[dict[str, str]]:
    normalized_allowed = {normalize_extension(ext) for ext in allowed_extensions}
    parser = _AttachmentProbeParser(detail_url=detail_url, allowed_extensions=normalized_allowed)
    parser.feed(html)
    for value in _js_file_url_candidates(_script_blocks(html), allowed_extensions=normalized_allowed):
        parser.add_candidate(value, discovery_method="js_url")
    return parser.attachments


def _rows_from_ajax_payload(payload: dict, *, parser: dict, base_url: str) -> list[dict[str, object]]:
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    rows: list[dict[str, object]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = _clean_issue_title(str(item.get("title") or ""))
        if not _title_matches(title, parser):
            continue
        url = str(item.get("url") or item.get("outlink") or "").strip()
        if not url:
            continue
        detail_url = urljoin(base_url, url)
        rows.append(
            {
                "title": title,
                "publish_date": _date_from_text(str(item.get("inputtime") or item.get("publish_date") or "")),
                "detail_url": detail_url,
                "source_item_id": str(item.get("id") or item.get("contentid") or detail_url),
            }
        )
    return rows


def _rows_from_gd_gkmlpt_payload(payload: dict, *, parser: dict, base_url: str) -> list[dict[str, object]]:
    data = payload.get("articles")
    if not isinstance(data, list):
        return []
    rows: list[dict[str, object]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        title = _clean_issue_title(str(item.get("title") or ""))
        if not _title_matches(title, parser):
            continue
        url = str(item.get("url") or item.get("post_url") or "").strip()
        if not url:
            post_id = item.get("id")
            if post_id:
                url = _gd_gkmlpt_detail_url(base_url, int(post_id))
        if not url:
            continue
        detail_url = urljoin(base_url, url)
        rows.append(
            {
                "title": title,
                "publish_date": _date_from_gd_gkmlpt_item(item),
                "detail_url": detail_url,
                "source_item_id": str(item.get("id") or detail_url),
            }
        )
    return rows


def _rows_from_spa_json_file_api_payload(payload: dict, *, parser: dict, base_url: str) -> list[dict[str, object]]:
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    rows: list[dict[str, object]] = []
    spa_json = parser.get("spa_json") if isinstance(parser.get("spa_json"), dict) else {}
    download_template = str(spa_json.get("download_url_template") or "/download/{encoded_file}")
    detail_url = str(parser.get("detail_url") or parser.get("list_url") or base_url)
    for item in data:
        if not isinstance(item, dict):
            continue
        title = _clean_issue_title(str(item.get("name") or item.get("title") or ""))
        if not _title_matches(title, parser):
            continue
        attachments = []
        for encoded_file in _spa_encoded_files(item):
            file_meta = _decode_spa_encoded_file(encoded_file)
            file_name = str(file_meta.get("fileName") or title)
            if _url_extension(file_name) not in _allowed_extensions(parser):
                continue
            attachments.append(
                {
                    "file_name": file_name,
                    "url": urljoin(base_url, download_template.format(encoded_file=encoded_file)),
                    "discovery_method": "spa_json_file_api",
                }
            )
        row: dict[str, object] = {
            "title": title,
            "publish_date": _date_from_text(str(item.get("publishDate") or item.get("createTime") or "")),
            "detail_url": detail_url,
            "source_item_id": str(item.get("id") or item.get("name") or detail_url),
        }
        if attachments:
            row["attachments"] = attachments
        rows.append(row)
    return rows


def _spa_encoded_files(item: dict[str, object]) -> list[str]:
    raw = item.get("file")
    if not raw:
        return []
    return [part for part in str(raw).split("|") if part]


def _decode_spa_encoded_file(encoded_file: str) -> dict[str, object]:
    padded = encoded_file + ("=" * ((4 - len(encoded_file) % 4) % 4))
    try:
        decoded = base64.urlsafe_b64decode(padded).decode("utf-8")
        data = json.loads(decoded)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _title_matches(title: str, parser: dict) -> bool:
    if not title:
        return False
    if parser.get("period_required", True) and _period_start_from_title(title, parser=parser) is None:
        return False
    if any(keyword and keyword in title for keyword in parser.get("exclude_keywords", [])):
        return False
    keywords = [str(keyword) for keyword in parser.get("title_keywords", []) if keyword]
    return not keywords or any(keyword in title for keyword in keywords)


def _clean_issue_title(title: str) -> str:
    text = re.sub(r"\s+", " ", title).strip()
    match = re.search(r"标题[:：]\s*(.*?)(?:点击数[:：]|发表时间[:：]|$)", text)
    if match:
        return match.group(1).strip()
    return text


def _js_file_url_candidates(html: str, *, allowed_extensions: set[str]) -> list[str]:
    extension_pattern = "|".join(re.escape(ext.lstrip(".")) for ext in sorted(allowed_extensions))
    if not extension_pattern:
        return []
    quoted = re.compile(rf"""["']([^"']+?\.(?:{extension_pattern})(?:\?[^"']*)?)["']""", re.IGNORECASE)
    return [unescape(match.group(1)) for match in quoted.finditer(html)]


def _script_blocks(html: str) -> str:
    scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", html, flags=re.IGNORECASE | re.DOTALL)
    return "\n".join(scripts)


def _allowed_extensions(parser: dict) -> set[str]:
    attachments = parser.get("attachments") if isinstance(parser.get("attachments"), dict) else {}
    allowed = attachments.get("allowed") or PDF_ALLOWED_EXTENSIONS
    return {normalize_extension(str(ext)) for ext in allowed}


def _shape_from_downloaded_attachments(
    attachments: list[CostInfoAttachment],
    *,
    fallback_mode: str,
    fallback_parsability: str,
) -> tuple[str, str]:
    extensions = {normalize_extension(os.path.splitext(attachment.file_name)[1]) for attachment in attachments}
    if extensions and extensions <= {".pdf"}:
        return "pdf_only", "text_pdf"
    if extensions and extensions <= {".xls", ".xlsx"}:
        return "xls_attachment", "structured"
    if extensions & {".xls", ".xlsx"}:
        return "mixed_file_attachment", "mixed"
    return fallback_mode, fallback_parsability


def _attachment_from_url(url: str, *, discovery_method: str) -> dict[str, str]:
    return {
        "file_name": _file_name_from_url(url),
        "url": url,
        "discovery_method": discovery_method,
    }


def _row_attachments(row: dict) -> list[dict[str, object]]:
    attachments = row.get("attachments")
    if not isinstance(attachments, list):
        return []
    return [item for item in attachments if isinstance(item, dict) and item.get("url")]


def _row_title(row: dict) -> str:
    value = row.get("title") or row.get("Title") or row.get("name")
    if not value:
        raise ValueError("SICHUAN_PDF_TITLE_MISSING")
    return _clean_issue_title(str(value))


def _row_publish_date(row: dict) -> str | None:
    value = row.get("publish_date") or row.get("publishDate") or row.get("inputtime")
    return _date_from_text(str(value)) if value else None


def _row_detail_url(row: dict) -> str:
    value = row.get("detail_url") or row.get("detailUrl") or row.get("url")
    if not value:
        raise ValueError("SICHUAN_PDF_DETAIL_URL_MISSING")
    return str(value)


def _row_source_item_key(row: dict) -> str:
    value = row.get("source_item_id") or row.get("id") or _row_detail_url(row)
    return str(value)


def _business_key_for_row(*, source_id: str, region_code: str | None, row: dict, parser: dict) -> str:
    title = _row_title(row)
    return build_cost_info_business_key(
        source_id=source_id,
        region_code=region_code,
        period=_period_start_from_title(title, parser=parser),
        title=title,
    )


def _period_start_from_title(title: str, *, parser: dict) -> str | None:
    period = parser.get("period") if isinstance(parser.get("period"), dict) else {}
    regex = str(period.get("regex") or PDF_PERIOD_REGEX)
    match = re.search(regex, title)
    if not match:
        return None
    return f"{match.group(1)}-{_month_number(match.group(2)):02d}"


def _month_number(raw_month: str) -> int:
    text = raw_month.strip()
    if text.isdigit():
        return int(text)
    chinese_months = {
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
        "十一": 11,
        "十二": 12,
    }
    if text in chinese_months:
        return chinese_months[text]
    raise ValueError(f"INVALID_PERIOD_MONTH: {raw_month}")


def _date_from_text(text: str) -> str | None:
    match = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", text)
    if not match:
        return None
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def _date_from_gd_gkmlpt_item(item: dict[str, object]) -> str | None:
    for key in ("created_at", "publish_date", "date"):
        value = item.get(key)
        if isinstance(value, str):
            date = _date_from_text(value)
            if date:
                return date
        if isinstance(value, (int, float)) and value:
            return datetime.fromtimestamp(int(value), UTC).date().isoformat()
    return None


def _gd_gkmlpt_detail_url(base_url: str, post_id: int) -> str:
    split = urlsplit(base_url)
    prefix = split.path.split("/gkmlpt/api/", 1)[0]
    return f"{split.scheme}://{split.netloc}{prefix}/gkmlpt/content/{post_id // 1_000_000}/{post_id // 1_000}/post_{post_id}.html"


def _url_extension(url: str) -> str:
    return normalize_extension(os.path.splitext(urlsplit(url).path)[1])


def _file_name_from_url(url: str) -> str:
    path = urlsplit(url).path
    file_name = unquote(os.path.basename(path))
    return file_name or "attachment.pdf"


def _ordered_unique(values) -> list[str]:
    result = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
