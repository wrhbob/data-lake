from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import os
import re
from typing import Protocol
from urllib.parse import unquote, urljoin, urlsplit

from sqlalchemy.orm import Session

from app.archive_rules import build_cost_info_business_key
from app.models import Archive
from app.source_adapter import SourceRegistryHttpClient, archive_business_key_exists, crawl_lag_days, file_extension, require_data_source
from app.source_registry import CostInfoAttachment, CostInfoDiscoveredItem, ingest_cost_info_registry_item
from app.storage import ObjectStore

XINJIANG_BASE_URL = "https://www.xjzj.com"
XINJIANG_PRICE_INFO_GUID = "c8df326e-cf0e-494d-b643-bd45cdb32773"
XINJIANG_PRICE_INFO_CATEGORY_ID = "C2A45B5E-FB3E-43C6-A77C-000000000456"
XINJIANG_POLICIES_LIST_ENDPOINT = f"{XINJIANG_BASE_URL}/Home/GetPoliciesListBy"
XINJIANG_ASPNET_AJAX_AREA_PARSER_VERSION = "xjzj.aspnet-ajax-area.v1"
XINJIANG_URUMQI_PARSER_VERSION = XINJIANG_ASPNET_AJAX_AREA_PARSER_VERSION


def _xinjiang_entry_url(areaid: str) -> str:
    return (
        f"{XINJIANG_BASE_URL}/Home/Policies/{XINJIANG_PRICE_INFO_CATEGORY_ID}"
        f"?areaid={areaid}&tid={XINJIANG_PRICE_INFO_GUID}"
    )


class XinjiangCostInfoClient(Protocol):
    def post_form_json(self, url: str, payload: dict[str, str]) -> dict:
        ...

    def get_text(self, url: str) -> str:
        ...

    def get_bytes(self, url: str) -> tuple[bytes, str | None]:
        ...


class UrllibXinjiangCostInfoClient(SourceRegistryHttpClient):
    def __init__(
        self,
        *,
        timeout: int = 30,
        cookie: str | None = None,
        referer: str | None = None,
        min_interval_seconds: float = 5,
        jitter_seconds: float = 5,
    ):
        super().__init__(
            referer=referer or XINJIANG_URUMQI_ENTRY_URL,
            timeout=timeout,
            cookie=cookie,
            min_interval_seconds=min_interval_seconds,
            jitter_seconds=jitter_seconds,
        )


@dataclass(frozen=True)
class XinjiangIssueList:
    rows: list[dict]
    total: int


@dataclass(frozen=True)
class XinjiangAreaSource:
    key: str
    areaid: str
    city_name: str
    region_code: str
    producer: str
    title_keywords: tuple[str, ...] = ()


XINJIANG_AREA_SOURCE_DEFS: dict[str, XinjiangAreaSource] = {
    "yili": XinjiangAreaSource("yili", "1", "伊犁哈萨克自治州", "654000", "伊犁哈萨克自治州建设工程造价管理机构", ("伊犁",)),
    "urumqi": XinjiangAreaSource("urumqi", "2", "乌鲁木齐市", "650100", "乌鲁木齐建筑市场运行服务中心", ("乌鲁木齐",)),
    "changji": XinjiangAreaSource("changji", "3", "昌吉回族自治州", "652300", "昌吉回族自治州建设工程造价管理机构", ("昌吉",)),
    "karamay": XinjiangAreaSource("karamay", "4", "克拉玛依市", "650200", "克拉玛依市建设工程造价管理机构", ("克拉玛依",)),
    "shihezi": XinjiangAreaSource("shihezi", "5", "石河子市", "659001", "石河子市建设工程造价管理机构", ("石河子",)),
    "tacheng": XinjiangAreaSource("tacheng", "7", "塔城地区", "654200", "塔城地区建设工程造价管理机构", ("塔城",)),
    "altay": XinjiangAreaSource("altay", "8", "阿勒泰地区", "654300", "阿勒泰地区建设工程造价管理机构", ("阿勒泰",)),
    "hami": XinjiangAreaSource("hami", "9", "哈密市", "650500", "哈密市建设工程造价管理机构", ("哈密",)),
    "bayinguoleng": XinjiangAreaSource("bayinguoleng", "10", "巴音郭楞蒙古自治州", "652800", "巴音郭楞蒙古自治州建设工程造价管理机构", ("巴州", "巴音郭楞", "库尔勒")),
    "akesu": XinjiangAreaSource("akesu", "11", "阿克苏地区", "652900", "阿克苏地区建设工程造价管理机构", ("阿克苏",)),
    "kashi": XinjiangAreaSource("kashi", "12", "喀什地区", "653100", "喀什地区建设工程造价管理机构", ("喀什",)),
    "wujiaqu": XinjiangAreaSource("wujiaqu", "13", "五家渠市", "659004", "五家渠市建设工程造价管理机构", ("五家渠",)),
    "boertala": XinjiangAreaSource("boertala", "14", "博尔塔拉蒙古自治州", "652700", "博尔塔拉蒙古自治州建设工程造价管理机构", ("博州", "博尔塔拉")),
    "kezilesu": XinjiangAreaSource("kezilesu", "15", "克孜勒苏柯尔克孜自治州", "653000", "克孜勒苏柯尔克孜自治州建设工程造价管理机构", ("克州", "克孜勒苏")),
    "hetian": XinjiangAreaSource("hetian", "16", "和田地区", "653200", "和田地区建设工程造价管理机构", ("和田",)),
    "turpan": XinjiangAreaSource("turpan", "17", "吐鲁番市", "650400", "吐鲁番市建设工程造价管理机构", ("吐鲁番",)),
}

XINJIANG_URUMQI_AREA_ID = XINJIANG_AREA_SOURCE_DEFS["urumqi"].areaid
XINJIANG_URUMQI_ENTRY_URL = _xinjiang_entry_url(XINJIANG_URUMQI_AREA_ID)
XINJIANG_AREA_SOURCE_CONFIGS = {key: XINJIANG_ASPNET_AJAX_AREA_PARSER_VERSION for key in XINJIANG_AREA_SOURCE_DEFS}


def xinjiang_area_cost_info_source_config(key: str) -> dict:
    source = XINJIANG_AREA_SOURCE_DEFS[key]
    entry_url = _xinjiang_entry_url(source.areaid)
    return {
        "registry_schema_version": "source_registry.v1",
        "stable": {
            "site_id": f"cost_info.xj.{source.key}",
            "domain_type": "cost_info",
            "region_code": source.region_code,
            "coverage_region_code": source.region_code,
            "producer": source.producer,
            "publisher": "新疆工程造价信息网",
            "publisher_scope": "city",
            "publisher_region_code": source.region_code,
            "publisher_name": "新疆工程造价信息网",
            "publisher_type": "official_cost_info_site",
            "entry_url": entry_url,
            "periodicity": {"unit": "month", "period_key_format": "YYYY-MM"},
        },
        "price_coordinates": {
            "price_source_type": "info_price",
            "tax_type": None,
        },
        "source_shape": {
            "publication_mode": "DIRECT_WEB",
            "source_attachment_mode": "mixed",
            "parsability": "structured",
        },
        "coverage_expectation": {
            "target_regions": [
                {
                    "region_code": source.region_code,
                    "region_name": source.city_name,
                    "target_level": "city",
                    "requires_city_source": True,
                    "source_completeness_status": "pending_source_audit",
                    "source_audit_status": "aspnet_ajax_area_verified",
                    "source_audit_note": "xjzj.com统一ASP.NET AJAX平台，按areaid切换地区；共享策略aspnet_ajax_area",
                    "city_source_url": entry_url,
                    "coverage_note": "新疆工程造价信息网统一平台公开附件源；FileProcessing=0，不解析xlsx/doc正文。",
                }
            ]
        },
        "parser": {
            "active_parser_version": XINJIANG_ASPNET_AJAX_AREA_PARSER_VERSION,
            "parsers": {
                XINJIANG_ASPNET_AJAX_AREA_PARSER_VERSION: {
                    "adapter_kind": "aspnet_ajax_area",
                    "scope": "aspnet_ajax_area_list_detail_attachment_only",
                    "list_strategy": "aspnet_ajax_area",
                    "list_endpoint": XINJIANG_POLICIES_LIST_ENDPOINT,
                    "detail_url_template": f"{XINJIANG_BASE_URL}/Home/PoliciesDetail/{{id}}",
                    "guid": XINJIANG_PRICE_INFO_GUID,
                    "areaid": source.areaid,
                    "aspnet_ajax_area": {
                        "endpoint": XINJIANG_POLICIES_LIST_ENDPOINT,
                        "guid": XINJIANG_PRICE_INFO_GUID,
                        "areaid": source.areaid,
                        "rows_path": "Rows",
                        "total_path": "Total",
                    },
                    "period": {
                        "source": "title",
                        "regex": r"(20\d{2})年(\d{1,2})月",
                    },
                    "title_keywords": list(source.title_keywords),
                    "attachments": {
                        "allowed": ["pdf", "doc", "docx", "xls", "xlsx"],
                        "required_any": ["pdf", "doc", "docx", "xls", "xlsx"],
                    },
                }
            },
        },
        "ops": {
            "enabled": True,
            "crawl_strategy_tier": "tier1_aspnet_ajax_area",
            "schedule": {"frequency": "monthly"},
            "queue": {
                "max_concurrent_per_host": 1,
                "global_priority": 20,
                "min_delay_seconds": 5,
                "jitter_seconds": 5,
                "retry_backoff": "exponential",
                "dead_letter_handoff": "017_manual_recovery",
            },
            "gated": False,
            "alert_thresholds": {"missing_period_count": 1},
        },
    }


def xinjiang_urumqi_cost_info_source_config() -> dict:
    return xinjiang_area_cost_info_source_config("urumqi")


def list_xinjiang_area_issues(
    client: XinjiangCostInfoClient,
    *,
    parser: dict,
    page: int = 1,
    page_size: int = 20,
) -> XinjiangIssueList:
    response = client.post_form_json(
        parser["list_endpoint"],
        {
            "title": "",
            "content": "",
            "guid": str(parser["guid"]),
            "areaid": str(parser["areaid"]),
            "page": str(page),
            "pagesize": str(page_size),
            "rnd": "0",
        },
    )
    rows = response.get("Rows") or response.get("rows") or []
    if not isinstance(rows, list):
        rows = []
    rows = _filter_rows_by_title_keywords([row for row in rows if isinstance(row, dict)], parser)
    total = response.get("Total") or response.get("total") or len(rows)
    return XinjiangIssueList(rows=rows, total=int(total))


def list_xinjiang_urumqi_issues(
    client: XinjiangCostInfoClient,
    *,
    parser: dict,
    page: int = 1,
    page_size: int = 20,
) -> XinjiangIssueList:
    return list_xinjiang_area_issues(client, parser=parser, page=page, page_size=page_size)


def _filter_rows_by_title_keywords(rows: list[dict], parser: dict) -> list[dict]:
    keywords = [str(keyword).strip() for keyword in parser.get("title_keywords") or [] if str(keyword).strip()]
    exclude_keywords = [str(keyword).strip() for keyword in parser.get("exclude_keywords") or [] if str(keyword).strip()]
    if not keywords and not exclude_keywords:
        return rows
    filtered: list[dict] = []
    for row in rows:
        title = _row_title_or_empty(row)
        if keywords and not any(keyword in title for keyword in keywords):
            continue
        if exclude_keywords and any(keyword in title for keyword in exclude_keywords):
            continue
        filtered.append(row)
    return filtered


def ingest_xinjiang_area_issue(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: XinjiangCostInfoClient,
    row: dict,
    parser: dict,
    actor_id: str = "xjzj-aspnet-ajax-area-crawler",
    task_id: str | None = None,
) -> Archive:
    detail_url = _detail_url(row, parser)
    detail_html = client.get_text(detail_url)
    attachments = _attachments_from_detail(detail_html)
    if not attachments:
        raise ValueError("XINJIANG_AREA_ATTACHMENTS_MISSING")

    downloaded = []
    for file_name, download_url in attachments:
        content, content_type = client.get_bytes(download_url)
        if not content:
            raise ValueError(f"XINJIANG_AREA_ATTACHMENT_EMPTY: {file_name}")
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
        discovered_at=_row_publish_datetime(row),
        fetched_at=datetime.now(UTC).isoformat(),
        attachments=downloaded,
        metadata={
            "listed_title": _row_title(row),
            "source_item_id": _row_source_item_key(row),
            "publish_date_raw": _row_publish_datetime(row),
            "price_source_type": "info_price",
            "tax_type": None,
            "source_attachment_mode": "mixed",
            "parsability": "structured",
            "attachment_discovery_methods": ["LookFile"],
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


def ingest_xinjiang_urumqi_issue(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: XinjiangCostInfoClient,
    row: dict,
    parser: dict,
    actor_id: str = "xinjiang-urumqi-cost-info-crawler",
    task_id: str | None = None,
):
    return ingest_xinjiang_area_issue(
        session,
        storage,
        source_id=source_id,
        client=client,
        row=row,
        parser=parser,
        actor_id=actor_id,
        task_id=task_id,
    )


def run_xinjiang_area_incremental(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: XinjiangCostInfoClient,
    actor_id: str = "xjzj-aspnet-ajax-area-crawler",
    max_items: int | None = None,
    as_of_date: str | None = None,
    stop_on_existing: bool = True,
) -> dict[str, object]:
    source = require_data_source(session, source_id, domain_type="cost_info")
    config = source.config or xinjiang_urumqi_cost_info_source_config()
    parser_version = str(config["parser"]["active_parser_version"])
    parser = config["parser"]["parsers"][parser_version]
    issue_list = list_xinjiang_area_issues(client, parser=parser, page=1, page_size=20)
    ingested_count = 0
    skipped_existing_count = 0
    stopped_on_existing = False
    cursor_source_item_key = _row_source_item_key(issue_list.rows[0]) if issue_list.rows else None
    cursor_publish_date = _row_publish_date(issue_list.rows[0]) if issue_list.rows else None

    for row in issue_list.rows:
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
        ingest_xinjiang_area_issue(
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
        "listed_count": issue_list.total,
        "ingested_count": ingested_count,
        "skipped_existing_count": skipped_existing_count,
        "stopped_on_existing": stopped_on_existing,
        "cursor_source_item_key": cursor_source_item_key,
        "cursor_publish_date": cursor_publish_date,
        "crawl_lag_days": crawl_lag_days(cursor_publish_date, as_of_date=as_of_date),
    }


def run_xinjiang_urumqi_incremental(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    client: XinjiangCostInfoClient,
    actor_id: str = "xinjiang-urumqi-cost-info-crawler",
    max_items: int | None = None,
    as_of_date: str | None = None,
    stop_on_existing: bool = True,
) -> dict[str, object]:
    return run_xinjiang_area_incremental(
        session,
        storage,
        source_id=source_id,
        client=client,
        actor_id=actor_id,
        max_items=max_items,
        as_of_date=as_of_date,
        stop_on_existing=stop_on_existing,
    )


def _detail_url(row: dict, parser: dict) -> str:
    return str(parser["detail_url_template"]).format(id=_row_source_item_key(row))


def _attachments_from_detail(html: str) -> list[tuple[str, str]]:
    seen: set[str] = set()
    attachments: list[tuple[str, str]] = []
    for match in re.finditer(r"LookFile\(['\"]([^'\"]+)['\"]\)", html):
        raw_path = match.group(1).strip()
        ext = file_extension(raw_path)
        if ext not in {".doc", ".docx", ".xls", ".xlsx"}:
            continue
        url = urljoin(XINJIANG_BASE_URL, raw_path)
        if url in seen:
            continue
        seen.add(url)
        attachments.append((_file_name_from_url(url), url))
    return attachments


def _file_name_from_url(url: str) -> str:
    name = os.path.basename(urlsplit(url).path)
    return unquote(name)


def _row_title(row: dict) -> str:
    for key in ("Name", "name", "Title", "title"):
        value = row.get(key)
        if value:
            return str(value)
    raise ValueError("XINJIANG_AREA_TITLE_MISSING")


def _row_title_or_empty(row: dict) -> str:
    try:
        return _row_title(row)
    except ValueError:
        return ""


def _row_source_item_key(row: dict) -> str:
    for key in ("ID", "id", "Guid", "guid"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value)
    raise ValueError("XINJIANG_AREA_SOURCE_ITEM_ID_MISSING")


def _row_publish_datetime(row: dict) -> str | None:
    value = row.get("ReleaseDate") or row.get("releaseDate") or row.get("publishDate")
    if not value:
        return None
    text = str(value)
    match = re.search(r"/Date\((-?\d+)\)/", text)
    if match:
        return datetime.fromtimestamp(int(match.group(1)) / 1000, UTC).isoformat()
    return text


def _row_publish_date(row: dict) -> str | None:
    value = _row_publish_datetime(row)
    return value[:10] if value and len(value) >= 10 else None


def _period_start_from_title(title: str) -> str | None:
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
