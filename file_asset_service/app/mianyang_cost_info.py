from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
import os
from typing import Protocol
from urllib.parse import unquote, urljoin, urlsplit

from sqlalchemy.orm import Session

from app.archive_rules import build_cost_info_business_key
from app.source_adapter import archive_business_key_exists, crawl_lag_days, file_extension, require_data_source
from app.source_registry import CostInfoAttachment, CostInfoDiscoveredItem, ingest_cost_info_registry_item
from app.storage import ObjectStore

MIANYANG_ENTRY_URL = "https://zjw.my.gov.cn/myszjj/c101133/list.shtml"
MIANYANG_PARSER_VERSION = "mianyang.xls-list.v1"


class MianyangCostInfoClient(Protocol):
    def get_text(self, url: str) -> str:
        ...

    def get_bytes(self, url: str) -> tuple[bytes, str | None]:
        ...


@dataclass(frozen=True)
class MianyangIssueRow:
    title: str
    publish_date: str | None
    detail_url: str

    def to_dict(self) -> dict[str, str | None]:
        return {
            "title": self.title,
            "publish_date": self.publish_date,
            "detail_url": self.detail_url,
        }


def mianyang_cost_info_source_config() -> dict:
    return {
        "registry_schema_version": "source_registry.v1",
        "stable": {
            "site_id": "cost_info.sc.mianyang",
            "domain_type": "cost_info",
            "region_code": "510700",
            "coverage_region_code": "510700-市区",
            "producer": "绵阳市住房和城乡建设委员会",
            "publisher": "绵阳市住房和城乡建设委员会",
            "publisher_scope": "city",
            "publisher_region_code": "510700",
            "publisher_name": "绵阳市住房和城乡建设委员会",
            "publisher_type": "official_housing_urban_rural_development",
            "entry_url": MIANYANG_ENTRY_URL,
            "periodicity": {"unit": "month", "period_key_format": "YYYY-MM"},
        },
        "price_coordinates": {
            "price_source_type": "info_price",
            "tax_type": None,
        },
        "source_shape": {
            "publication_mode": "DIRECT_WEB",
            "source_attachment_mode": "xls_attachment",
            "parsability": "structured",
        },
        "parser": {
            "active_parser_version": MIANYANG_PARSER_VERSION,
            "parsers": {
                MIANYANG_PARSER_VERSION: {
                    "scope": "static_list_detail_attachment_only",
                    "list_url": MIANYANG_ENTRY_URL,
                    "period": {
                        "source": "title",
                        "regex": r"(20\d{2})年(\d{1,2})月",
                    },
                    "attachments": {
                        "allowed": ["xls", "xlsx"],
                        "required_any": ["xls", "xlsx"],
                    },
                }
            },
        },
        "ops": {
            "enabled": True,
            "crawl_strategy_tier": "tier1_static_html",
            "schedule": {"frequency": "monthly"},
            "queue": {
                "max_concurrent_per_host": 1,
                "global_priority": 20,
                "min_delay_seconds": 3,
                "jitter_seconds": 2,
                "retry_backoff": "exponential",
                "dead_letter_handoff": "017_manual_recovery",
            },
            "gated": False,
            "alert_thresholds": {"missing_period_count": 1},
        },
    }


def list_mianyang_issues(client: MianyangCostInfoClient, *, parser: dict) -> list[dict[str, str | None]]:
    html = client.get_text(parser["list_url"])
    parser_state = _MianyangListParser(base_url=parser["list_url"])
    parser_state.feed(html)
    return [row.to_dict() for row in parser_state.rows]


def ingest_mianyang_issue(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: MianyangCostInfoClient,
    row: dict,
    parser: dict,
    actor_id: str = "mianyang-cost-info-crawler",
    task_id: str | None = None,
):
    detail_url = _row_detail_url(row)
    detail_html = client.get_text(detail_url)
    attachments = _attachments_from_detail(detail_html, detail_url=detail_url)
    if not attachments:
        raise ValueError("MIANYANG_ATTACHMENTS_MISSING")

    downloaded = []
    for file_name, download_url in attachments:
        content, content_type = client.get_bytes(download_url)
        if not content:
            raise ValueError(f"MIANYANG_ATTACHMENT_EMPTY: {file_name}")
        downloaded.append(
            CostInfoAttachment(
                file_name=file_name,
                content=content,
                url=download_url,
                content_type=content_type,
            )
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
            "price_source_type": "info_price",
            "tax_type": None,
            "coverage_region_code": "510700-市区",
            "source_attachment_mode": "xls_attachment",
            "parsability": "structured",
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


def run_mianyang_incremental(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: MianyangCostInfoClient,
    actor_id: str = "mianyang-cost-info-crawler",
    max_items: int | None = None,
    as_of_date: str | None = None,
    stop_on_existing: bool = True,
) -> dict[str, object]:
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or mianyang_cost_info_source_config()
    parser = config["parser"]["parsers"][MIANYANG_PARSER_VERSION]
    rows = list_mianyang_issues(client, parser=parser)
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
        ingest_mianyang_issue(
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


class _MianyangListParser(HTMLParser):
    def __init__(self, *, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.rows: list[MianyangIssueRow] = []
        self._in_li = False
        self._capture_date = False
        self._date_parts: list[str] = []
        self._current_href: str | None = None
        self._current_title: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if tag == "li":
            self._in_li = True
            self._capture_date = False
            self._date_parts = []
            self._current_href = None
            self._current_title = None
            return
        if not self._in_li:
            return
        if tag == "span" and "date" in (attr.get("class") or ""):
            self._capture_date = True
            return
        if tag == "a":
            href = attr.get("href")
            title = attr.get("title")
            if href and title and "材料价格信息" in title:
                self._current_href = href
                self._current_title = title.strip()

    def handle_data(self, data: str) -> None:
        if self._capture_date:
            stripped = data.strip()
            if stripped:
                self._date_parts.append(stripped)

    def handle_endtag(self, tag: str) -> None:
        if tag == "span" and self._capture_date:
            self._capture_date = False
            return
        if tag == "li" and self._in_li:
            if self._current_href and self._current_title:
                self.rows.append(
                    MianyangIssueRow(
                        title=self._current_title,
                        publish_date="".join(self._date_parts).strip() or None,
                        detail_url=urljoin(self.base_url, self._current_href),
                    )
                )
            self._in_li = False


class _AttachmentParser(HTMLParser):
    def __init__(self, *, detail_url: str):
        super().__init__(convert_charrefs=True)
        self.detail_url = detail_url
        self.attachments: list[tuple[str, str]] = []
        self._seen: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if not href:
            return
        ext = file_extension(href)
        if ext not in {".xls", ".xlsx"}:
            return
        url = urljoin(self.detail_url, href)
        if url in self._seen:
            return
        self._seen.add(url)
        self.attachments.append((_file_name_from_url(url), url))


def _attachments_from_detail(html: str, *, detail_url: str) -> list[tuple[str, str]]:
    parser = _AttachmentParser(detail_url=detail_url)
    parser.feed(html)
    return parser.attachments


def _file_name_from_url(url: str) -> str:
    return unquote(os.path.basename(urlsplit(url).path))


def _row_title(row: dict) -> str:
    value = row.get("title") or row.get("Title") or row.get("name")
    if not value:
        raise ValueError("MIANYANG_TITLE_MISSING")
    return str(value)


def _row_publish_date(row: dict) -> str | None:
    value = row.get("publish_date") or row.get("publishDate")
    return str(value) if value else None


def _row_detail_url(row: dict) -> str:
    value = row.get("detail_url") or row.get("detailUrl") or row.get("url")
    if not value:
        raise ValueError("MIANYANG_DETAIL_URL_MISSING")
    return str(value)


def _row_source_item_key(row: dict) -> str:
    return _row_detail_url(row)


def _period_start_from_title(title: str) -> str | None:
    import re

    match = re.search(r"(20\d{2})年(\d{1,2})月", title)
    if not match:
        return None
    return f"{match.group(1)}-{int(match.group(2)):02d}"


def _business_key_for_row(*, source_id: str, region_code: str | None, row: dict) -> str:
    title = _row_title(row)
    return build_cost_info_business_key(
        source_id=source_id,
        region_code=region_code,
        period=_period_start_from_title(title),
        title=title,
    )
