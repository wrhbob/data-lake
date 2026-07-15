from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re

from sqlalchemy.orm import Session

from app.archive_rules import metadata_cell
from app.archive_service import create_archive_from_ingest_event
from app.collection import create_collection_task
from app.models import Archive, CollectionTask, DataSource
from app.storage import ObjectStore
from app.assets import register_asset


@dataclass(frozen=True)
class CostInfoAttachment:
    file_name: str
    content: bytes
    url: str
    content_type: str | None = None


@dataclass(frozen=True)
class CostInfoDiscoveredItem:
    title: str
    publish_date: str | None
    detail_url: str
    discovered_at: str | None
    fetched_at: str | None
    attachments: list[CostInfoAttachment]
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class CostInfoPeriodFields:
    period_kind: str
    period: str | None
    period_raw: str | None
    period_start: str | None
    period_year: int | None = None
    period_issue_no: int | None = None
    period_issue_end_no: int | None = None


def _config_digest(config: dict) -> str:
    payload = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _active_parser_version(config: dict) -> str:
    parser = config.get("parser")
    if not isinstance(parser, dict):
        raise ValueError("SOURCE_REGISTRY_PARSER_REQUIRED")
    parser_version = parser.get("active_parser_version")
    if not parser_version:
        raise ValueError("SOURCE_REGISTRY_PARSER_VERSION_REQUIRED")
    return str(parser_version)


def _period_regex(config: dict, parser_version: str) -> str:
    parser = config.get("parser") or {}
    parsers = parser.get("parsers") or {}
    selected = parsers.get(parser_version) or {}
    period = selected.get("period") or {}
    return period.get("regex") or r"(20\d{2})年(\d{1,2})月"


def _period_kind(config: dict, parser_version: str) -> str:
    parser = config.get("parser") or {}
    parsers = parser.get("parsers") or {}
    selected = parsers.get(parser_version) or {}
    period = selected.get("period") or {}
    stable = config.get("stable") if isinstance(config.get("stable"), dict) else {}
    return str(period.get("kind") or stable.get("period_kind") or "monthly")


def _period_fields(title: str, config: dict, parser_version: str) -> CostInfoPeriodFields:
    period_kind = _period_kind(config, parser_version)
    regex = _period_regex(config, parser_version)
    if period_kind == "issue_based":
        return _issue_based_period_fields(title, regex)
    if period_kind == "bimonthly":
        return _bimonthly_period_fields(title, regex)
    period_start = _period_start(title, regex)
    return CostInfoPeriodFields(
        period_kind="monthly",
        period=period_start,
        period_raw=title,
        period_start=period_start,
    )


def _period_start(title: str, regex: str) -> str | None:
    match = re.search(regex, title)
    if not match:
        return None
    year = match.group(1)
    month = _month_number(match.group(2))
    return f"{year}-{month:02d}"


def _issue_based_period_fields(title: str, regex: str) -> CostInfoPeriodFields:
    match = re.search(regex, title)
    if not match:
        return CostInfoPeriodFields(period_kind="issue_based", period=None, period_raw=None, period_start=None)
    groups = match.groupdict()
    year = int(groups.get("year") or match.group(1))
    issue_raw = str(groups.get("issue") or match.group(2))
    issue_numbers = [int(value) for value in re.findall(r"\d{1,2}", issue_raw)]
    if not issue_numbers:
        raise ValueError(f"INVALID_PERIOD_ISSUE: {issue_raw}")
    issue_no = issue_numbers[0]
    issue_end_raw = groups.get("issue_end")
    issue_end_no = int(issue_end_raw) if issue_end_raw else (issue_numbers[1] if len(issue_numbers) > 1 else None)
    period = str(groups.get("period") or "").strip()
    if not period:
        period = f"{year}年第{issue_no}{f'—{issue_end_no}' if issue_end_no is not None else ''}期"
    period = re.sub(r"\s+", "", period)
    return CostInfoPeriodFields(
        period_kind="issue_based",
        period=period,
        period_raw=period,
        period_start=None,
        period_year=year,
        period_issue_no=issue_no,
        period_issue_end_no=issue_end_no,
    )


def _bimonthly_period_fields(title: str, regex: str) -> CostInfoPeriodFields:
    match = re.search(regex, title)
    if not match:
        return CostInfoPeriodFields(period_kind="bimonthly", period=None, period_raw=None, period_start=None)
    groups = match.groupdict()
    year = int(groups.get("year") or match.group(1))
    start_month = _month_number(str(groups.get("start_month") or groups.get("month") or match.group(2)))
    end_month = _month_number(str(groups.get("end_month") or match.group(3)))
    period = str(groups.get("period") or "").strip()
    if not period:
        period = f"{year}年{start_month}-{end_month}月"
    period = re.sub(r"\s+", "", period).replace("—", "-").replace("－", "-").replace("~", "-")
    return CostInfoPeriodFields(
        period_kind="bimonthly",
        period=period,
        period_raw=period,
        period_start=f"{year}-{start_month:02d}",
        period_year=year,
    )


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


def _field_sources(*fields: str, parser_version: str) -> dict[str, dict[str, str]]:
    return {
        field: {
            "source_level": "crawler",
            "tagged_by": parser_version,
            "tagged_at": "2026-06-21T00:00:00+08:00",
        }
        for field in fields
    }


def _metadata_cell(value: object, *, parser_version: str) -> dict[str, object]:
    if isinstance(value, dict) and "source_level" in value:
        return value
    return metadata_cell(value, source_level="crawler", tagged_by=parser_version)


def ingest_cost_info_registry_item(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    item: CostInfoDiscoveredItem,
    actor_id: str = "source_registry",
    task_id: str | None = None,
) -> Archive:
    source = session.get(DataSource, source_id)
    if source is None:
        raise ValueError(f"DATA_SOURCE_NOT_FOUND: {source_id}")
    if source.data_domain != "cost_info":
        raise ValueError(f"SOURCE_DOMAIN_MISMATCH: {source.data_domain}")
    if not item.attachments:
        raise ValueError("COST_INFO_ATTACHMENTS_REQUIRED")

    config = source.config or {}
    stable = config.get("stable") if isinstance(config.get("stable"), dict) else {}
    parser_version = _active_parser_version(config)
    period_fields = _period_fields(item.title, config, parser_version)
    # A source-specific parser may retain an issue-based original period while
    # also supplying a reliable month projection (for example 贵州第5期 = 5月).
    # Prefer that explicit projection over the generic period parser result.
    period_start = item.metadata.get("period_start") or period_fields.period_start
    region_code = source.region_code or stable.get("region_code")
    coverage_region_code = item.metadata.get("coverage_region_code") or stable.get("coverage_region_code") or region_code
    price_coordinates = config.get("price_coordinates") if isinstance(config.get("price_coordinates"), dict) else {}
    source_shape = config.get("source_shape") if isinstance(config.get("source_shape"), dict) else {}
    price_source_type = item.metadata.get("price_source_type") or price_coordinates.get("price_source_type") or "info_price"
    price_kind = item.metadata.get("price_kind") or price_coordinates.get("price_kind") or "unspecified"
    period_kind = item.metadata.get("period_kind") or period_fields.period_kind
    tax_type = item.metadata.get("tax_type") if "tax_type" in item.metadata else price_coordinates.get("tax_type")
    producer = item.metadata.get("producer") or stable.get("producer")
    publisher = item.metadata.get("publisher") or stable.get("publisher") or stable.get("publisher_name") or source.name
    publisher_scope = item.metadata.get("publisher_scope") or stable.get("publisher_scope")
    publisher_type = item.metadata.get("publisher_type") or stable.get("publisher_type")
    publisher_region_code = item.metadata.get("publisher_region_code") or stable.get("publisher_region_code") or region_code
    publisher_name = stable.get("publisher_name") or source.name
    parsability = item.metadata.get("parsability") or source_shape.get("parsability") or "unknown"
    publication_mode = item.metadata.get("publication_mode") or source_shape.get("publication_mode")
    source_attachment_mode = item.metadata.get("source_attachment_mode") or source_shape.get("source_attachment_mode")
    derive_tasks = not (item.metadata.get("opaque_package") is True or source_attachment_mode == "zip_package")

    if task_id is not None:
        task = session.get(CollectionTask, task_id)
        if task is None:
            raise ValueError(f"COLLECTION_TASK_NOT_FOUND: {task_id}")
        if task.source_id != source.source_id:
            raise ValueError("COLLECTION_TASK_SOURCE_MISMATCH")
    else:
        task = create_collection_task(
            session,
            source_id=source.source_id,
            operator_type="system",
            operator_id=actor_id,
            task_type="crawl",
            trigger_type="manual",
            data_domain="cost_info",
            period_raw=period_fields.period_raw or item.title,
            period_start=period_start,
            config_override={
                "source_registry_schema_version": config.get("registry_schema_version", "source_registry.v1"),
                "site_id": stable.get("site_id"),
                "parser_version": parser_version,
                "template_id": None,
                "template_version": None,
                "config_digest": _config_digest(config),
                "config_snapshot": config,
            },
        )

    archive: Archive | None = None
    metadata = {
        "period": _metadata_cell(period_fields.period, parser_version=parser_version),
        "period_raw": _metadata_cell(period_fields.period_raw or item.title, parser_version=parser_version),
        "period_start": _metadata_cell(period_start, parser_version=parser_version),
        "coverage_region_code": _metadata_cell(coverage_region_code, parser_version=parser_version),
        "price_source_type": _metadata_cell(price_source_type, parser_version=parser_version),
        "tax_type": _metadata_cell(tax_type, parser_version=parser_version),
        "producer": _metadata_cell(producer, parser_version=parser_version),
        "publisher": _metadata_cell(publisher, parser_version=parser_version),
        "publisher_scope": _metadata_cell(publisher_scope, parser_version=parser_version),
        "publisher_type": _metadata_cell(publisher_type, parser_version=parser_version),
        "publisher_region_code": _metadata_cell(publisher_region_code, parser_version=parser_version),
        "publisher_name": _metadata_cell(publisher_name, parser_version=parser_version),
        "parsability": _metadata_cell(parsability, parser_version=parser_version),
        "publication_mode": _metadata_cell(publication_mode, parser_version=parser_version),
        "source_attachment_mode": _metadata_cell(source_attachment_mode, parser_version=parser_version),
    }
    if period_fields.period_year is not None:
        metadata["period_year"] = _metadata_cell(period_fields.period_year, parser_version=parser_version)
    if period_fields.period_issue_no is not None:
        metadata["period_issue_no"] = _metadata_cell(period_fields.period_issue_no, parser_version=parser_version)
    if period_fields.period_issue_end_no is not None:
        metadata["period_issue_end_no"] = _metadata_cell(
            period_fields.period_issue_end_no,
            parser_version=parser_version,
        )
    for key, value in item.metadata.items():
        if key in metadata or key in {"price_kind", "period_kind", "source_item_key"}:
            continue
        if isinstance(value, dict) and "source_level" in value:
            metadata[key] = value
        else:
            metadata[key] = metadata_cell(value, source_level="crawler", tagged_by=parser_version)
    field_sources = _field_sources(
        "domain_type",
        "channel_type",
        "title",
        "region_code",
        "publish_date",
        parser_version=parser_version,
    )
    for index, attachment in enumerate(item.attachments, start=1):
        configured_source_item_key = item.metadata.get("source_item_key")
        if configured_source_item_key:
            source_item_key = str(configured_source_item_key)
            if len(item.attachments) > 1:
                source_item_key = f"{source_item_key}:{index}"
        else:
            period_key = period_fields.period or period_start or "unknown"
            source_item_key = f"{source.source_id}:{period_key}:{index}"
        result = register_asset(
            session,
            storage,
            tenant_code=source.asset_tenant_code,
            source_type="info_price",
            batch_id=task.batch_id,
            task_id=task.task_id,
            file_name=attachment.file_name,
            content=attachment.content,
            source_url=attachment.url,
            source_item_key=source_item_key,
            mime_type=attachment.content_type,
            derive_tasks=derive_tasks,
            source_metadata={
                "discovered_at": item.discovered_at,
                "fetched_at": item.fetched_at,
                "detail_url": item.detail_url,
                **item.metadata,
            },
        )
        archive = create_archive_from_ingest_event(
            session,
            event_id=result.ingest_event_id,
            domain_type="cost_info",
            channel_type="crawler",
            price_kind=str(price_kind),
            period_kind=str(period_kind),
            title=item.title,
            region_code=region_code,
            publish_date=item.publish_date,
            visibility_scope="public",
            status="collected",
            metadata=metadata,
            field_sources=field_sources,
            actor_type="system",
            actor_id=actor_id,
        )

    if archive is None:
        raise ValueError("COST_INFO_ARCHIVE_NOT_CREATED")
    return archive
