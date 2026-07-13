from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
import re
from urllib.parse import unquote, urljoin, urlsplit

from sqlalchemy.orm import object_session

from app.adapters.base_cost_info_adapter import DiscoveredIssue
from app.archive_rules import build_cost_info_business_key
from app.source_adapter import file_extension
from app.source_registry import CostInfoAttachment, CostInfoDiscoveredItem, ingest_cost_info_registry_item


@dataclass(frozen=True)
class Anchor:
    href: str
    text: str
    attrs: dict[str, str]


class StaticListDetailPackageAdapter:
    adapter_kind = "static_list_detail_package"

    def __init__(self) -> None:
        self._client = None
        self._rows_by_key: dict[str, dict[str, object]] = {}

    def discover(self, source, task, client) -> list[DiscoveredIssue]:
        parser = _active_parser(source)
        list_url = str(parser.get("list_url") or _stable(source).get("entry_url") or source.url or source.base_url)
        html = client.get_text(list_url)
        rows = _extract_list_rows(html, list_url, parser)
        rows = rows[: _max_items(task, source, len(rows))]
        self._client = client
        self._rows_by_key = {str(row["source_item_key"]): row for row in rows}
        return [
            DiscoveredIssue(
                source_item_key=str(row["source_item_key"]),
                title=str(row["title"]),
                publish_date=str(row["publish_date"]) if row.get("publish_date") else None,
                period_raw=str(row["title"]),
                detail_url=str(row["detail_url"]),
                attachment_urls=[str(row["detail_url"])] if _is_direct_attachment(source, parser) else [],
            )
            for row in rows
        ]

    def ingest(self, source, task, issue: DiscoveredIssue, storage) -> None:
        row = self._rows_by_key.get(issue.source_item_key) or _row_from_issue(issue)
        parser = _active_parser(source)
        detail_url = str(row["detail_url"])
        if _is_direct_attachment(source, parser):
            detail_html = ""
            detail_attachments = [_direct_attachment_from_row(row, parser)]
        else:
            detail_html = self._client.get_text(detail_url)
            detail_attachments = _extract_detail_attachments(detail_html, detail_url, parser)
        downloaded: list[CostInfoAttachment] = []
        for attachment in detail_attachments:
            content, content_type = self._client.get_bytes(str(attachment["url"]))
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
            raise ValueError("STATIC_LIST_DETAIL_PACKAGE_ATTACHMENTS_MISSING")

        config = source.config or {}
        stable = _stable(source)
        source_shape = config.get("source_shape") if isinstance(config.get("source_shape"), dict) else {}
        price_coordinates = config.get("price_coordinates") if isinstance(config.get("price_coordinates"), dict) else {}
        publish_date = _extract_detail_publish_date(detail_html) or row.get("publish_date")
        item = CostInfoDiscoveredItem(
            title=str(row["title"]),
            publish_date=str(publish_date) if publish_date else None,
            detail_url=detail_url,
            discovered_at=str(row["publish_date"]) if row.get("publish_date") else None,
            fetched_at=datetime.now(UTC).isoformat(),
            attachments=downloaded,
            metadata={
                "source_item_key": row["source_item_key"],
                "source_item_id": row["source_item_key"],
                "listed_title": row["title"],
                "source_title": row["title"],
                "price_source_type": price_coordinates.get("price_source_type") or "info_price",
                "tax_type": price_coordinates.get("tax_type"),
                "coverage_region_code": stable.get("coverage_region_code") or source.region_code,
                "publisher": stable.get("publisher") or stable.get("publisher_name") or source.name,
                "publisher_scope": stable.get("publisher_scope"),
                "publisher_type": stable.get("publisher_type"),
                "publisher_region_code": stable.get("publisher_region_code") or source.region_code,
                "source_attachment_mode": source_shape.get("source_attachment_mode") or "zip_package",
                "parsability": source_shape.get("parsability") or "opaque_package",
                "publication_mode": source_shape.get("publication_mode") or "DETAIL_PAGE_ATTACHMENT",
                "attachment_discovery_methods": _ordered_unique(
                    str(attachment.get("discovery_method") or "unknown") for attachment in detail_attachments
                ),
                "opaque_package": True,
            },
        )
        ingest_cost_info_registry_item(
            _session_for(source, task),
            storage,
            source_id=source.source_id,
            item=item,
            actor_id="cost-info-worker:static_list_detail_package",
            task_id=task.task_id,
        )

    def business_key(self, source, issue: DiscoveredIssue) -> str:
        period = _period_start(issue.title, _active_parser(source))
        return build_cost_info_business_key(
            source_id=source.source_id,
            region_code=source.region_code,
            period=period or "",
            title=issue.title,
        )


def _active_parser(source) -> dict:
    config = source.config or {}
    parser_config = config.get("parser") or {}
    active_version = parser_config.get("active_parser_version")
    parser = (parser_config.get("parsers") or {}).get(active_version)
    if not isinstance(parser, dict):
        raise ValueError("STATIC_LIST_DETAIL_PACKAGE_PARSER_CONFIG_MISSING")
    return parser


def _stable(source) -> dict:
    config = source.config or {}
    stable = config.get("stable")
    return stable if isinstance(stable, dict) else {}


def _is_direct_attachment(source, parser: dict) -> bool:
    config = source.config or {}
    source_shape = config.get("source_shape") if isinstance(config.get("source_shape"), dict) else {}
    publication_mode = parser.get("publication_mode") or source_shape.get("publication_mode")
    return str(publication_mode or "").upper() == "DIRECT_ATTACHMENT"


def _session_for(source, task):
    session = object_session(task) or object_session(source)
    if session is None:
        raise ValueError("SQLALCHEMY_SESSION_REQUIRED")
    return session


def _max_items(task, source, row_count: int) -> int:
    override = task.config_override if isinstance(task.config_override, dict) else {}
    policy = source.schedule_policy if isinstance(source.schedule_policy, dict) else {}
    raw_value = override.get("max_items_per_run") or policy.get("max_items_per_run") or row_count
    return max(0, int(raw_value))


def _extract_list_rows(html: str, list_url: str, parser: dict) -> list[dict[str, object]]:
    selector = str(parser.get("list_item_selector") or "a")
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for chunk in _list_chunks(html):
        publish_date = _extract_publish_date(chunk)
        for anchor in _extract_anchors(chunk):
            if not _matches_selector(anchor, selector):
                continue
            title = _anchor_title(anchor)
            if not title or _period_start(title, parser) is None:
                continue
            detail_url = urljoin(list_url, anchor.href)
            source_item_key = _article_id(detail_url)
            if not source_item_key or source_item_key in seen:
                continue
            seen.add(source_item_key)
            rows.append(
                {
                    "source_item_key": source_item_key,
                    "title": title,
                    "publish_date": publish_date,
                    "detail_url": detail_url,
                }
            )
    rows.sort(key=lambda row: _period_start(str(row["title"]), parser) or "", reverse=True)
    return rows


def _list_chunks(html: str) -> list[str]:
    chunks = [
        match.group("li")
        for match in re.finditer(r"<li\b[^>]*>(?P<li>.*?)</li>", html, flags=re.IGNORECASE | re.DOTALL)
    ]
    return chunks or [html]


def _extract_detail_attachments(html: str, detail_url: str, parser: dict) -> list[dict[str, object]]:
    selector = str(parser.get("detail_attachment_selector") or "a")
    allowed_extensions = _allowed_extensions(parser)
    attachments: list[dict[str, object]] = []
    seen: set[str] = set()
    for anchor in _extract_anchors(html):
        if not _matches_selector(anchor, selector):
            continue
        url = urljoin(detail_url, anchor.href)
        file_name = _package_file_name(anchor, url, allowed_extensions)
        if not file_name:
            continue
        key = url
        if key in seen:
            continue
        seen.add(key)
        attachments.append(
            {
                "file_name": file_name,
                "url": url,
                "discovery_method": _attachment_discovery_method(file_name),
            }
        )
    return attachments


def _direct_attachment_from_row(row: dict[str, object], parser: dict) -> dict[str, object]:
    url = str(row["detail_url"])
    file_name = _direct_attachment_file_name(str(row["title"]), url, _allowed_extensions(parser))
    if not file_name:
        raise ValueError("STATIC_LIST_DIRECT_ATTACHMENT_FILE_NAME_MISSING")
    return {
        "file_name": file_name,
        "url": url,
        "discovery_method": "list_page_pdf_anchor" if file_extension(file_name).lower() == ".pdf" else "list_page_package_anchor",
    }


def _direct_attachment_file_name(title: str, url: str, allowed_extensions: set[str]) -> str | None:
    visible = _normalize_visible_text(title)
    if file_extension(visible).lower() in allowed_extensions:
        return visible
    path_name = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    ext = file_extension(path_name).lower()
    if ext in allowed_extensions:
        return f"{visible}{ext}" if visible else path_name
    return None


def _attachment_discovery_method(file_name: str) -> str:
    if file_extension(file_name).lower() == ".pdf":
        return "detail_page_pdf_anchor"
    return "detail_page_package_anchor"


def _allowed_extensions(parser: dict) -> set[str]:
    attachments = parser.get("attachments") if isinstance(parser.get("attachments"), dict) else {}
    allowed = attachments.get("allowed") or ["zip", "rar", "7z"]
    return {f".{str(value).lower().lstrip('.')}" for value in allowed}


def _package_file_name(anchor: Anchor, url: str, allowed_extensions: set[str]) -> str | None:
    visible = _normalize_visible_text(anchor.text)
    if file_extension(visible).lower() in allowed_extensions:
        return visible
    path_name = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    if file_extension(path_name).lower() in allowed_extensions:
        return path_name
    return None


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.anchors: list[Anchor] = []
        self._attrs: dict[str, str] | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        self._attrs = {key.lower(): value or "" for key, value in attrs}
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._attrs is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._attrs is None:
            return
        href = self._attrs.get("href", "").strip()
        if href and not href.lower().startswith("javascript:"):
            self.anchors.append(Anchor(href=href, text="".join(self._text), attrs=self._attrs))
        self._attrs = None
        self._text = []


def _extract_anchors(html: str) -> list[Anchor]:
    parser = _AnchorParser()
    parser.feed(html)
    return parser.anchors


def _matches_selector(anchor: Anchor, selector: str) -> bool:
    conditions = re.findall(
        r"\[(?P<attr>[\w-]+)\s*(?P<op>\*=|\$=|\^=|=)\s*(?P<quote>['\"])(?P<value>.*?)(?P=quote)\]",
        selector,
    )
    for attr, op, _quote, expected in conditions:
        candidate = _selector_attr_value(anchor, attr.lower())
        if op == "*=" and expected not in candidate:
            return False
        if op == "$=" and not candidate.lower().endswith(expected.lower()):
            return False
        if op == "^=" and not candidate.lower().startswith(expected.lower()):
            return False
        if op == "=" and candidate != expected:
            return False
    return True


def _selector_attr_value(anchor: Anchor, attr: str) -> str:
    if attr == "title":
        return anchor.attrs.get("title") or _normalize_visible_text(anchor.text)
    if attr == "href":
        return anchor.href
    return anchor.attrs.get(attr, "")


def _anchor_title(anchor: Anchor) -> str:
    return _normalize_visible_text(anchor.attrs.get("title") or anchor.text)


def _period_start(title: str, parser: dict) -> str | None:
    period = parser.get("period") if isinstance(parser.get("period"), dict) else {}
    regex = str(period.get("regex") or r"(20\d{2})年(\d{1,2})月")
    match = re.search(regex, title)
    if not match:
        return None
    groups = match.groupdict()
    year = groups.get("year") or match.group(1)
    month = groups.get("month") or groups.get("issue") or match.group(2)
    return f"{int(year):04d}-{_month_number(str(month)):02d}"


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


def _extract_publish_date(html: str) -> str | None:
    patterns = [
        r"<meta\b[^>]*name=['\"]?PubDate['\"]?[^>]*content=['\"](?P<value>20\d{2}-\d{1,2}-\d{1,2})",
        r"(?P<value>20\d{2}-\d{1,2}-\d{1,2})",
        r"(?P<year>20\d{2})年\s*(?P<month>\d{1,2})月\s*(?P<day>\d{1,2})(?:日|号)",
    ]
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if not match:
            continue
        if "value" in match.groupdict() and match.group("value"):
            year, month, day = match.group("value").split("-")
            return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
        return f"{int(match.group('year')):04d}-{int(match.group('month')):02d}-{int(match.group('day')):02d}"
    return None


def _extract_detail_publish_date(html: str) -> str | None:
    patterns = [
        r"<meta\b[^>]*name=['\"]?PubDate['\"]?[^>]*content=['\"](?P<value>20\d{2}-\d{1,2}-\d{1,2})",
        r"(?:日期|发布时间|发布日期)\s*[：:]\s*(?P<value>20\d{2}-\d{1,2}-\d{1,2})",
        r"(?:日期|发布时间|发布日期)\s*[：:]\s*(?P<year>20\d{2})年\s*(?P<month>\d{1,2})月\s*(?P<day>\d{1,2})(?:日|号)",
    ]
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if not match:
            continue
        if "value" in match.groupdict() and match.group("value"):
            year, month, day = match.group("value").split("-")
            return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
        return f"{int(match.group('year')):04d}-{int(match.group('month')):02d}-{int(match.group('day')):02d}"
    return None


def _article_id(detail_url: str) -> str:
    match = re.search(r"/t20\d{6}_(?P<id>\d+)\.html?", detail_url, flags=re.IGNORECASE)
    if match:
        return match.group("id")
    path_name = urlsplit(detail_url).path.rsplit("/", 1)[-1]
    return path_name.rsplit(".", 1)[0]


def _row_from_issue(issue: DiscoveredIssue) -> dict[str, object]:
    return {
        "source_item_key": issue.source_item_key,
        "title": issue.title,
        "publish_date": issue.publish_date,
        "detail_url": issue.detail_url,
    }


def _normalize_visible_text(html: object) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", unescape(str(html)))).strip()


def _ordered_unique(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
