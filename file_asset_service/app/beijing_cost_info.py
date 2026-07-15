from __future__ import annotations

from datetime import UTC, datetime
from html.parser import HTMLParser
import os
import re
from typing import Protocol
from urllib.parse import unquote, urljoin, urlsplit

from sqlalchemy.orm import Session

from app.archive_rules import build_cost_info_business_key
from app.normalization import normalize_key_text
from app.sichuan_pdf_cost_info import discover_cost_info_attachments
from app.source_adapter import (
    SourceRegistryHttpClient,
    archive_business_key_exists,
    crawl_lag_days,
    normalize_extension,
    require_data_source,
)
from app.source_registry import CostInfoAttachment, CostInfoDiscoveredItem, ingest_cost_info_registry_item
from app.storage import ObjectStore

BEIJING_ENTRY_URL = "https://zjw.beijing.gov.cn/bjjs/gczj14/zjxx/index.shtml"
BEIJING_MORE_URL = "https://zjw.beijing.gov.cn/bjjs/gczj14/zjxx/gczjxxj64/index.shtml"
BEIJING_COST_INFO_PARSER_VERSION = "beijing.zjw-main-pdf-list.v1"
BEIJING_TITLE_REGEX = r"^(20\d{2})年(0?[1-9]|1[0-2])月北京工程造价信息$"
BEIJING_EXCLUDE_KEYWORDS = (
    "市场参考价",
    "厂家参考",
    "绿色建材",
    "房屋修缮",
    "协会信息价",
    "京津冀",
)


class BeijingCostInfoClient(Protocol):
    def get_text(self, url: str) -> str:
        ...

    def get_bytes(self, url: str) -> tuple[bytes, str | None]:
        ...


def beijing_cost_info_source_config() -> dict:
    parser = {
        "adapter_kind": "beijing_pdf",
        "scope": "beijing_zjw_main_pdf_list",
        "list_url": BEIJING_ENTRY_URL,
        "list_urls": [BEIJING_ENTRY_URL, BEIJING_MORE_URL],
        "list_strategy": "static_html",
        "period": {
            "source": "title",
            "regex": BEIJING_TITLE_REGEX,
        },
        "title_regex": BEIJING_TITLE_REGEX,
        "exclude_keywords": list(BEIJING_EXCLUDE_KEYWORDS),
        "min_period": "2026-01",
        "period_required": True,
        "attachments": {
            "allowed": ["pdf"],
            "probe_methods": ["a", "iframe", "embed", "object", "js_url"],
            "required_any": ["pdf"],
        },
    }
    return {
        "registry_schema_version": "source_registry.v1",
        "stable": {
            "site_id": "cost_info.bj.zjw.main",
            "domain_type": "cost_info",
            "region_code": "110000",
            "coverage_region_code": "110000",
            "producer": "北京市住房和城乡建设委员会",
            "publisher": "北京市住房和城乡建设委员会",
            "publisher_scope": "province",
            "publisher_region_code": "110000",
            "publisher_name": "北京市住房和城乡建设委员会",
            "publisher_type": "official_housing_urban_rural_development",
            "entry_url": BEIJING_ENTRY_URL,
            "periodicity": {"unit": "month", "period_key_format": "YYYY-MM"},
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
            "active_parser_version": BEIJING_COST_INFO_PARSER_VERSION,
            "parsers": {
                BEIJING_COST_INFO_PARSER_VERSION: parser,
            },
        },
        "coverage_expectation": {
            "target_regions": [
                {
                    "region_code": "110000",
                    "region_name": "北京市",
                    "target_level": "province",
                    "requires_city_source": False,
                    "source_completeness_status": "province_source_only",
                    "source_audit_status": "official_main_source",
                    "coverage_note": "北京市住建委官方《北京工程造价信息》主刊源",
                }
            ]
        },
        "ops": {
            "enabled": True,
            "crawl_strategy_tier": "tier1_static_html",
            "schedule": {"frequency": "monthly"},
            "queue": {
                "max_concurrent_per_host": 1,
                "global_priority": 20,
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


class UrllibBeijingCostInfoClient(SourceRegistryHttpClient):
    def __init__(self, *, timeout: int = 60, min_interval_seconds: float = 5, jitter_seconds: float = 3) -> None:
        super().__init__(
            referer=BEIJING_ENTRY_URL,
            timeout=timeout,
            min_interval_seconds=min_interval_seconds,
            jitter_seconds=jitter_seconds,
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
        )


def list_beijing_cost_info_issues(client: BeijingCostInfoClient, *, parser: dict) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    seen_url: set[str] = set()
    seen_period_title: set[tuple[str, str]] = set()
    pending_urls = _list_urls(parser)
    queued_urls = set(pending_urls)
    requested_periods = _requested_periods_for_list(parser)
    history_page_limit = _history_page_limit(parser)
    pagination_url_count = 0

    while pending_urls:
        list_url = pending_urls.pop(0)
        if list_url in seen_url:
            continue
        seen_url.add(list_url)
        html = client.get_text(list_url)
        parser_state = _BeijingIssueListParser(base_url=list_url, parser=parser)
        parser_state.feed(html)
        for row in parser_state.rows:
            url_key = _row_source_item_key(row)
            if url_key in seen_url:
                continue
            pt_key = _row_period_title_key(row, parser)
            if pt_key is not None and pt_key in seen_period_title:
                continue
            seen_url.add(url_key)
            if pt_key is not None:
                seen_period_title.add(pt_key)
            rows.append(row)

        # The official list's first page currently ends at 2024-07.  Follow
        # its own static EasySite pagination only for an explicit history
        # request; routine monthly scans remain a two-page fast path.
        if history_page_limit:
            for page_url in _official_pagination_urls(html, base_url=list_url):
                if page_url in seen_url or page_url in queued_urls:
                    continue
                if pagination_url_count >= history_page_limit:
                    break
                pending_urls.append(page_url)
                queued_urls.add(page_url)
                pagination_url_count += 1

        if requested_periods and requested_periods.issubset(
            {str(row.get("period")) for row in rows if row.get("period")}
        ):
            break
    return rows


def ingest_beijing_cost_info_issue(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: BeijingCostInfoClient,
    row: dict,
    parser: dict,
    actor_id: str = "beijing-cost-info-crawler",
    task_id: str | None = None,
):
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or {}
    stable = config.get("stable") if isinstance(config.get("stable"), dict) else {}
    price_coordinates = config.get("price_coordinates") if isinstance(config.get("price_coordinates"), dict) else {}
    source_shape = config.get("source_shape") if isinstance(config.get("source_shape"), dict) else {}
    title = _row_title(row)
    detail_url = _row_detail_url(row)
    attachment_infos = _row_attachments(row)
    downloaded: list[CostInfoAttachment] = []
    discovery_methods: list[str] = []

    if not attachment_infos:
        content, content_type = client.get_bytes(detail_url)
        if _looks_like_pdf(content, content_type):
            downloaded.append(
                CostInfoAttachment(
                    file_name=f"{title}.pdf",
                    content=content,
                    url=detail_url,
                    content_type=content_type,
                )
            )
            discovery_methods.append("detail_response_pdf")
        else:
            detail_html = content.decode("utf-8", errors="replace")
            attachment_infos = discover_cost_info_attachments(
                detail_html,
                detail_url=detail_url,
                allowed_extensions=_allowed_extensions(parser),
            )
            if content_type:
                row["detail_content_type"] = content_type

    for attachment in attachment_infos:
        download_url = str(attachment["url"])
        file_name = str(attachment.get("file_name") or _file_name_from_url(download_url, fallback=f"{title}.pdf"))
        content, content_type = client.get_bytes(download_url)
        if not content:
            continue
        downloaded.append(
            CostInfoAttachment(
                file_name=file_name,
                content=content,
                url=download_url,
                content_type=content_type,
            )
        )
        discovery_methods.append(str(attachment.get("discovery_method") or "unknown"))

    if not downloaded:
        raise ValueError("BEIJING_COST_INFO_ATTACHMENTS_MISSING")

    item = CostInfoDiscoveredItem(
        title=title,
        publish_date=_row_publish_date(row),
        detail_url=detail_url,
        discovered_at=_row_publish_date(row),
        fetched_at=datetime.now(UTC).isoformat(),
        attachments=downloaded,
        metadata={
            "listed_title": title,
            "source_item_id": _row_source_item_key(row),
            "price_source_type": price_coordinates.get("price_source_type") or "info_price",
            "tax_type": price_coordinates.get("tax_type"),
            "coverage_region_code": stable.get("coverage_region_code") or source.region_code,
            "source_attachment_mode": source_shape.get("source_attachment_mode") or "pdf_only",
            "parsability": source_shape.get("parsability") or "text_pdf",
            "publication_mode": source_shape.get("publication_mode") or "DIRECT_PDF",
            "attachment_discovery_methods": _ordered_unique(discovery_methods),
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


def run_beijing_cost_info_incremental(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: BeijingCostInfoClient,
    actor_id: str = "beijing-cost-info-crawler",
    max_items: int | None = None,
    as_of_date: str | None = None,
    stop_on_existing: bool = True,
) -> dict[str, object]:
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or {}
    active_version = str((config.get("parser") or {}).get("active_parser_version") or "")
    parser = (config.get("parser") or {}).get("parsers", {}).get(active_version)
    if not isinstance(parser, dict):
        raise ValueError("BEIJING_COST_INFO_PARSER_CONFIG_MISSING")
    rows = list_beijing_cost_info_issues(client, parser=parser)
    ingested_count = 0
    skipped_existing_count = 0
    stopped_on_existing = False
    cursor_source_item_key = _row_source_item_key(rows[0]) if rows else None
    cursor_publish_date = _row_publish_date(rows[0]) if rows else None

    for row in rows:
        business_key = _business_key_for_row(source_id=source.source_id, region_code=source.region_code, row=row)
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
        ingest_beijing_cost_info_issue(
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


class _BeijingIssueListParser(HTMLParser):
    VOID_TAGS = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }

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
        return tag == "div" and bool(classes & {"list_div", "list-item", "news-item"})

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
            title = _clean_title(anchor["title"])
            period = _period_from_title(title, parser=self.parser)
            if period is None or _period_before_min(period, self.parser):
                continue
            url = urljoin(self.base_url, anchor["href"])
            row: dict[str, object] = {
                "title": title,
                "period": period,
                "publish_date": publish_date,
                "detail_url": url,
                "source_item_id": url,
            }
            if _url_extension(url) in _allowed_extensions(self.parser):
                row["attachments"] = [
                    {
                        "file_name": f"{title}.pdf",
                        "url": url,
                        "discovery_method": "list_direct",
                    }
                ]
            self.rows.append(row)
            return


def _list_urls(parser: dict) -> list[str]:
    value = parser.get("list_urls")
    if isinstance(value, list) and value:
        return [str(item) for item in value if item]
    return [str(parser.get("list_url") or BEIJING_ENTRY_URL)]


def _requested_periods_for_list(parser: dict) -> set[str]:
    values = parser.get("_requested_periods")
    if not isinstance(values, (list, tuple, set)):
        return set()
    return {str(value) for value in values if value}


def _history_page_limit(parser: dict) -> int:
    if not parser.get("_history_pagination"):
        return 0
    pagination = parser.get("pagination") if isinstance(parser.get("pagination"), dict) else {}
    raw_limit = parser.get("_history_page_limit") or pagination.get("max_pages") or 8
    try:
        return max(1, min(int(raw_limit), 100))
    except (TypeError, ValueError):
        return 8


def _official_pagination_urls(html: str, *, base_url: str) -> list[str]:
    """Extract same-site EasySite static-page links from an official list page."""

    base = urlsplit(base_url)
    urls: list[str] = []
    for raw_url in re.findall(r"queryArticleByCondition\(\s*this\s*,\s*['\"]([^'\"]+)['\"]\s*\)", html):
        candidate = urljoin(base_url, raw_url)
        parsed = urlsplit(candidate)
        if parsed.scheme not in {"http", "https"} or parsed.netloc != base.netloc:
            continue
        if "/gczj14/zjxx/gczjxxj64/" not in parsed.path:
            continue
        if candidate not in urls:
            urls.append(candidate)
    return urls


def _allowed_extensions(parser: dict) -> set[str]:
    attachments = parser.get("attachments") if isinstance(parser.get("attachments"), dict) else {}
    allowed = attachments.get("allowed") or ["pdf"]
    return {normalize_extension(str(ext)) for ext in allowed}


def _row_title(row: dict) -> str:
    value = row.get("title")
    if not value:
        raise ValueError("BEIJING_COST_INFO_TITLE_MISSING")
    return _clean_title(str(value))


def _row_publish_date(row: dict) -> str | None:
    value = row.get("publish_date")
    return _date_from_text(str(value)) if value else None


def _row_detail_url(row: dict) -> str:
    value = row.get("detail_url") or row.get("url")
    if not value:
        raise ValueError("BEIJING_COST_INFO_DETAIL_URL_MISSING")
    return str(value)


def _row_source_item_key(row: dict) -> str:
    return str(row.get("source_item_id") or _row_detail_url(row))


def _row_period_title_key(row: dict, parser: dict) -> tuple[str, str] | None:
    period = str(row.get("period") or _period_from_title(_row_title(row), parser=parser) or "")
    title = _normalize_title_for_dedup(_row_title(row) or "")
    if not period or not title:
        return None
    return (period, title)


def _normalize_title_for_dedup(title: str) -> str:
    t = re.sub(r"(\d{4})年(\d)月", r"\1年0\2月", title)
    return normalize_key_text(t)


def _row_attachments(row: dict) -> list[dict[str, object]]:
    attachments = row.get("attachments")
    if not isinstance(attachments, list):
        return []
    return [item for item in attachments if isinstance(item, dict) and item.get("url")]


def _business_key_for_row(*, source_id: str, region_code: str | None, row: dict) -> str:
    title = _row_title(row)
    return build_cost_info_business_key(
        source_id=source_id,
        region_code=region_code,
        period=str(row.get("period") or _period_from_title(title, parser={"title_regex": BEIJING_TITLE_REGEX}) or ""),
        title=title,
    )


def _period_from_title(title: str, *, parser: dict) -> str | None:
    if any(keyword and keyword in title for keyword in parser.get("exclude_keywords", [])):
        return None
    regex = str(parser.get("title_regex") or (parser.get("period") or {}).get("regex") or BEIJING_TITLE_REGEX)
    match = re.search(regex, title)
    if not match:
        return None
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}"


def _period_before_min(period: str, parser: dict) -> bool:
    min_period = str(parser.get("min_period") or "")
    return bool(min_period and period < min_period)


def _clean_title(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip()


def _date_from_text(text: str) -> str | None:
    match = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", text)
    if not match:
        return None
    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def _looks_like_pdf(content: bytes, content_type: str | None) -> bool:
    return bool((content_type and "pdf" in content_type.lower()) or content.startswith(b"%PDF"))


def _url_extension(url: str) -> str:
    return normalize_extension(os.path.splitext(unquote(urlsplit(url).path))[1])


def _file_name_from_url(url: str, *, fallback: str) -> str:
    name = os.path.basename(unquote(urlsplit(url).path)).strip()
    return name or fallback


def _ordered_unique(values) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered
