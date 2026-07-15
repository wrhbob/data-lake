from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.archive_rules import metadata_cell, now_iso
from app.archive_service import attach_file, attach_pending_file, create_archive_from_ingest_event
from app.assets import register_asset
from app.collection import create_collection_task
from app.models import Archive, ArchiveFile, DataSource, utcnow
from app.storage import ObjectStore

FETCH_STATUS_FETCHED = "FETCHED"
FETCH_STATUS_PENDING = "PENDING_FETCH"
FETCH_STATUS_FAILED = "FAILED"

TRADING_PRICED_SOURCE_EXTENSIONS = [".zbx", ".cjz", ".cos", ".qtfx"]
TRADING_REGISTRY_RED_LINES = [
    "do_not_parse_priced_source",
    "do_not_group_project_code",
    "gated_metadata_only",
]
TRADING_L1_HOOK_METADATA_KEYS = {
    "project_name_raw",
    "project_code_raw",
    "tender_code_raw",
    "section_no_raw",
    "bid_subject_role_raw",
    "tenderer_raw",
    "agency_raw",
    "amount_raw",
}


@dataclass(frozen=True)
class TradingAttachment:
    file_name: str
    content: bytes | None
    url: str
    content_type: str | None = None
    gated: bool = False
    gated_reason: str | None = None
    file_role: str | None = None
    fetch_status: str = FETCH_STATUS_FETCHED
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class TradingDiscoveredItem:
    source_item_key: str
    title: str
    publish_date: str | None
    detail_url: str
    channel_id: str
    notice_family: str
    notice_type_raw: str
    # A province-wide source may emit records from several administrative
    # regions.  Preserve the item-level code instead of forcing every archive
    # to inherit the source's provincial code.
    region_code: str | None = None
    project_code_raw: str | None = None
    tender_code_raw: str | None = None
    section_no_raw: str | None = None
    bid_subject_role_raw: str | None = None
    tenderer_raw: str | None = None
    agency_raw: str | None = None
    amount_raw: str | None = None
    exchange_name: str | None = None
    column_path_raw: str | None = None
    project_name_raw: str | None = None
    engineering_type_raw: str | None = None
    discovered_at: str | None = None
    fetched_at: str | None = None
    attachments: list[TradingAttachment] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)
    business_key: str | None = None


class PendingFetchClient(Protocol):
    def get_bytes(self, url: str) -> tuple[bytes, str | None]:
        ...


def trading_exchange_source_config(
    *,
    site_id: str,
    publisher_name: str,
    entry_url: str,
    region_code: str,
    parser_version: str,
    channels: list[dict[str, object]],
    source_priority: str = "official",
    publisher_type: str = "exchange_center",
    crawl_strategy_tier: str = "tier1_static_html",
) -> dict[str, object]:
    return {
        "registry_schema_version": "source_registry.v1",
        "stable": {
            "site_id": site_id,
            "domain_type": "trading",
            "region_code": region_code,
            "publisher_name": publisher_name,
            "publisher_type": publisher_type,
            "entry_url": entry_url,
            "issue_period": "event_driven",
            "source_priority": source_priority,
        },
        "parser": {
            "active_parser_version": parser_version,
            "parsers": {
                parser_version: {
                    "scope": "list_detail_html_only",
                    "channels": channels,
                    "field_rules": {
                        "notice_type_raw": {"source": "list_or_detail_text"},
                        "project_name_raw": {"source": "list_or_detail_text"},
                        "project_code_raw": {"source": "list_or_detail_text"},
                        "tender_code_raw": {"source": "list_or_detail_text"},
                        "section_no_raw": {"source": "list_or_detail_text"},
                        "bid_subject_role_raw": {"source": "list_or_detail_text"},
                        "tenderer_raw": {"source": "list_or_detail_text"},
                        "agency_raw": {"source": "list_or_detail_text"},
                        "amount_raw": {"source": "list_or_detail_text"},
                        "publish_date": {"source": "list_or_detail_text"},
                    },
                    "attachment_rules": {
                        "public_depth": 1,
                        "gated_policy": "metadata_only",
                        "priced_source_extensions": TRADING_PRICED_SOURCE_EXTENSIONS,
                    },
                    "source_priority": source_priority,
                    "red_lines": TRADING_REGISTRY_RED_LINES,
                }
            },
        },
        "ops": {
            "enabled": True,
            "crawl_strategy_tier": crawl_strategy_tier,
            "incremental_cursor": {"field": "source_item_key", "stop_on_existing_business_key": True},
            "schedule_frequency": "manual_until_verified",
        },
    }


def ingest_trading_registry_item(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    item: TradingDiscoveredItem,
    actor_id: str = "source_registry",
) -> Archive:
    source = session.get(DataSource, source_id)
    if source is None:
        raise ValueError(f"DATA_SOURCE_NOT_FOUND: {source_id}")
    if source.data_domain != "trading":
        raise ValueError(f"SOURCE_DOMAIN_MISMATCH: {source.data_domain}")

    fetched_attachments = [
        attachment
        for attachment in item.attachments
        if not attachment.gated and attachment.fetch_status == FETCH_STATUS_FETCHED and attachment.content is not None
    ]
    pending_attachments = [
        attachment
        for attachment in item.attachments
        if not attachment.gated and attachment.fetch_status == FETCH_STATUS_PENDING and attachment.content is None
    ]
    if not fetched_attachments:
        raise ValueError("TRADING_PUBLIC_ATTACHMENT_REQUIRED")

    config = source.config or {}
    stable = config.get("stable") if isinstance(config.get("stable"), dict) else {}
    parser_version = _active_parser_version(config)
    source_priority = str(stable.get("source_priority") or "official")
    region_code = item.region_code or source.region_code or stable.get("region_code")

    task = create_collection_task(
        session,
        source_id=source.source_id,
        operator_type="system",
        operator_id=actor_id,
        task_type="crawl",
        trigger_type="manual",
        data_domain="trading",
        period_raw=item.publish_date,
        config_override={
            "source_registry_schema_version": config.get("registry_schema_version", "source_registry.v1"),
            "site_id": stable.get("site_id"),
            "parser_version": parser_version,
            "template_id": None,
            "template_version": None,
            "config_digest": _config_digest(config),
            "config_snapshot": config,
            "source_priority": source_priority,
            "channel_id": item.channel_id,
            "notice_family": item.notice_family,
        },
    )

    metadata = _metadata_for_item(item, parser_version=parser_version, source_priority=source_priority)
    field_sources = _field_sources(
        "domain_type",
        "channel_type",
        "business_key",
        "title",
        "region_code",
        "publish_date",
        parser_version=parser_version,
    )

    archive: Archive | None = None
    mounted_file_roles: set[tuple[str, str]] = set()
    sort_index = 0
    for index, attachment in enumerate(fetched_attachments, start=1):
        sort_index = index
        result = register_asset(
            session,
            storage,
            tenant_code=source.asset_tenant_code,
            source_type=source.source_type,
            batch_id=task.batch_id,
            task_id=task.task_id,
            file_name=attachment.file_name,
            content=attachment.content or b"",
            source_url=attachment.url,
            source_item_key=item.source_item_key,
            mime_type=attachment.content_type,
            derive_tasks=False,
            source_metadata=_source_metadata_for_item(item, attachment, source_priority=source_priority),
        )
        if result.ingest_event_id is None:
            raise ValueError("TRADING_INGEST_EVENT_REQUIRED")

        if archive is None:
            archive = create_archive_from_ingest_event(
                session,
                event_id=result.ingest_event_id,
                domain_type="trading",
                channel_type="crawler",
                title=item.title,
                business_key=item.business_key,
                region_code=str(region_code) if region_code else None,
                publish_date=item.publish_date,
                visibility_scope="public",
                status="collected",
                metadata=metadata,
                field_sources=field_sources,
                actor_type="system",
                actor_id=actor_id,
            )
        else:
            file_role = attachment.file_role
            # ``None`` intentionally keeps archive-service role inference
            # (for example, .cjz becomes priced_source).  It remains a stable
            # deduplication key for repeated source attachments.
            mount_key = (result.file_id, file_role or "__inferred__")
            # Official notices occasionally repeat the same downloadable
            # object in their attachment HTML.  One archive may only mount a
            # given raw file once for each role; the duplicate still has a
            # durable ingestion event but must not create a second mount.
            if mount_key in mounted_file_roles:
                continue
            attach_file(
                session,
                archive.archive_id,
                result.file_id,
                file_role=file_role,
                display_name=attachment.file_name,
                source_url=attachment.url,
                is_primary=False,
                sort_order=index * 10,
                metadata={**attachment.metadata, "source_event_id": result.ingest_event_id},
                actor_type="system",
                actor_id=actor_id,
            )
            mounted_file_roles.add(mount_key)

    if archive is None:
        raise ValueError("TRADING_ARCHIVE_NOT_CREATED")
    for pending_index, attachment in enumerate(pending_attachments, start=sort_index + 1):
        attach_pending_file(
            session,
            archive.archive_id,
            file_role=attachment.file_role or "attachment",
            display_name=attachment.file_name,
            source_url=attachment.url,
            sort_order=pending_index * 10,
            metadata={
                **attachment.metadata,
                "content_type": attachment.content_type,
                "fetch_status": FETCH_STATUS_PENDING,
            },
            actor_type="system",
            actor_id=actor_id,
        )
    # Asset registration has already updated the file counters on this
    # item-level task.  It is a completed ingestion record, not a second queue
    # entry for the task-loop worker to pick up later.
    task.status = "done"
    task.finished_at = utcnow()
    task.discovered_count = 1
    task.error_code = None
    task.error_message = None
    session.commit()
    return archive


def fetch_pending_trading_archive_file(
    session: Session,
    storage: ObjectStore,
    *,
    archive_file_id: str,
    client: PendingFetchClient,
    actor_id: str = "trading-offpeak-fetch",
) -> ArchiveFile:
    mounted = session.get(ArchiveFile, archive_file_id)
    if mounted is None:
        raise ValueError(f"ARCHIVE_FILE_NOT_FOUND: {archive_file_id}")
    if mounted.fetch_status != FETCH_STATUS_PENDING:
        raise ValueError(f"ARCHIVE_FILE_NOT_PENDING_FETCH: {archive_file_id}")
    if mounted.file_id is not None:
        raise ValueError(f"ARCHIVE_FILE_ALREADY_FETCHED: {archive_file_id}")
    archive = session.get(Archive, mounted.archive_id)
    if archive is None:
        raise ValueError(f"ARCHIVE_NOT_FOUND: {mounted.archive_id}")
    source = session.get(DataSource, archive.source_id)
    if source is None:
        raise ValueError(f"DATA_SOURCE_NOT_FOUND: {archive.source_id}")
    if not mounted.source_url:
        raise ValueError(f"ARCHIVE_FILE_SOURCE_URL_REQUIRED: {archive_file_id}")

    content, content_type = client.get_bytes(mounted.source_url)
    task = create_collection_task(
        session,
        source_id=source.source_id,
        operator_type="system",
        operator_id=actor_id,
        task_type="crawl",
        trigger_type="manual",
        data_domain="trading",
        period_raw=archive.publish_date.isoformat() if archive.publish_date else None,
        config_override={
            "fetch_mode": "offpeak",
            "archive_file_id": mounted.archive_file_id,
            "file_role": mounted.file_role,
            "source_item_key": archive.source_item_key,
        },
    )
    result = register_asset(
        session,
        storage,
        tenant_code=archive.tenant_code,
        source_type=source.source_type,
        batch_id=task.batch_id,
        task_id=task.task_id,
        file_name=mounted.display_name or mounted.archive_file_id,
        content=content,
        source_url=mounted.source_url,
        source_item_key=archive.source_item_key,
        mime_type=content_type,
        derive_tasks=False,
        source_metadata={
            "detail_url": archive.source_url,
            "attachment_url": mounted.source_url,
            "source_item_key": archive.source_item_key,
            "file_role": mounted.file_role,
            "fetch_mode": "offpeak",
        },
    )
    if result.ingest_event_id is None:
        raise ValueError("TRADING_INGEST_EVENT_REQUIRED")
    mounted.file_id = result.file_id
    mounted.fetch_status = FETCH_STATUS_FETCHED
    mounted.metadata_payload = {
        **(mounted.metadata_payload or {}),
        "access_status": "PUBLIC",
        "fetch_status": FETCH_STATUS_FETCHED,
        "fetch_mode": "offpeak",
        "source_event_id": result.ingest_event_id,
        "fetched_at": now_iso(),
        "content_type": content_type,
    }
    # As above, the task created for an off-peak file is an auditable lineage
    # record and has completed synchronously inside this function.
    task.status = "done"
    task.finished_at = utcnow()
    task.discovered_count = 1
    task.error_code = None
    task.error_message = None
    session.commit()
    session.refresh(mounted)
    return mounted


def run_pending_trading_fetches(
    session: Session,
    storage: ObjectStore,
    *,
    client: PendingFetchClient,
    source_id: str | None = None,
    limit: int = 10,
    actor_id: str = "trading-offpeak-fetch",
    include_failed: bool = False,
    scan_limit: int = 500,
) -> dict[str, object]:
    capped_limit = max(1, min(int(limit), 100))
    capped_scan_limit = max(capped_limit, min(max(1, int(scan_limit)), 1000))
    statement = (
        select(ArchiveFile)
        .join(Archive, ArchiveFile.archive_id == Archive.archive_id)
        .where(Archive.domain_type == "trading")
        .where(ArchiveFile.fetch_status == FETCH_STATUS_PENDING)
        .order_by(Archive.publish_date, Archive.created_at, ArchiveFile.sort_order)
        .limit(capped_scan_limit)
    )
    if source_id:
        statement = statement.where(Archive.source_id == source_id)

    candidates = list(session.scalars(statement).all())
    pending_files: list[ArchiveFile] = []
    skipped_failed_items: list[dict[str, object]] = []
    for mounted in candidates:
        if not include_failed and _has_pending_fetch_failure(mounted):
            skipped_failed_items.append(_pending_fetch_item_payload(mounted))
            continue
        pending_files.append(mounted)
        if len(pending_files) >= capped_limit:
            break

    fetched_items: list[dict[str, object]] = []
    failed_items: list[dict[str, object]] = []
    for mounted in pending_files:
        try:
            fetched = fetch_pending_trading_archive_file(
                session,
                storage,
                archive_file_id=mounted.archive_file_id,
                client=client,
                actor_id=actor_id,
            )
            fetched_items.append(
                {
                    "archive_file_id": fetched.archive_file_id,
                    "archive_id": fetched.archive_id,
                    "file_id": fetched.file_id,
                    "display_name": fetched.display_name,
                    "file_role": fetched.file_role,
                }
            )
        except Exception as exc:
            session.rollback()
            marked = _record_pending_fetch_failure(
                session,
                archive_file_id=mounted.archive_file_id,
                error=str(exc),
                actor_id=actor_id,
            )
            failed_items.append(
                {
                    "archive_file_id": mounted.archive_file_id,
                    "archive_id": mounted.archive_id,
                    "display_name": mounted.display_name,
                    "file_role": mounted.file_role,
                    "error": str(exc),
                    "fetch_attempts": (marked.metadata_payload or {}).get("fetch_attempts") if marked else None,
                }
            )
    return {
        "seen_count": len(pending_files),
        "fetched_count": len(fetched_items),
        "failed_count": len(failed_items),
        "skipped_failed_count": len(skipped_failed_items),
        "fetched_items": fetched_items,
        "failed_items": failed_items,
        "skipped_failed_items": skipped_failed_items,
    }


def _has_pending_fetch_failure(mounted: ArchiveFile) -> bool:
    metadata = mounted.metadata_payload or {}
    return bool(metadata.get("fetch_attempts") or metadata.get("last_fetch_error"))


def _pending_fetch_item_payload(mounted: ArchiveFile) -> dict[str, object]:
    metadata = mounted.metadata_payload or {}
    return {
        "archive_file_id": mounted.archive_file_id,
        "archive_id": mounted.archive_id,
        "display_name": mounted.display_name,
        "file_role": mounted.file_role,
        "fetch_attempts": metadata.get("fetch_attempts", 0),
        "last_fetch_error": metadata.get("last_fetch_error"),
    }


def _record_pending_fetch_failure(
    session: Session,
    *,
    archive_file_id: str,
    error: str,
    actor_id: str,
) -> ArchiveFile | None:
    mounted = session.get(ArchiveFile, archive_file_id)
    if mounted is None or mounted.fetch_status != FETCH_STATUS_PENDING or mounted.file_id is not None:
        return None

    metadata = dict(mounted.metadata_payload or {})
    previous_attempts = metadata.get("fetch_attempts", 0)
    try:
        fetch_attempts = int(previous_attempts)
    except (TypeError, ValueError):
        fetch_attempts = 0
    mounted.metadata_payload = {
        **metadata,
        "access_status": metadata.get("access_status", "PENDING_FETCH"),
        "fetch_status": FETCH_STATUS_PENDING,
        "fetch_attempts": fetch_attempts + 1,
        "last_fetch_error": error,
        "last_fetch_actor_id": actor_id,
        "last_fetch_attempted_at": now_iso(),
    }
    session.commit()
    session.refresh(mounted)
    return mounted


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


def _field_sources(*fields: str, parser_version: str) -> dict[str, dict[str, str]]:
    tagged_at = now_iso()
    return {
        field: {
            "source_level": "crawler",
            "tagged_by": parser_version,
            "tagged_at": tagged_at,
        }
        for field in fields
    }


def _metadata_for_item(
    item: TradingDiscoveredItem,
    *,
    parser_version: str,
    source_priority: str,
) -> dict[str, dict[str, object]]:
    raw_values = {
        "notice_type_raw": item.notice_type_raw,
        "project_code_raw": item.project_code_raw,
        "tender_code_raw": item.tender_code_raw,
        "section_no_raw": item.section_no_raw,
        "bid_subject_role_raw": item.bid_subject_role_raw,
        "tenderer_raw": item.tenderer_raw,
        "agency_raw": item.agency_raw,
        "amount_raw": item.amount_raw,
        "exchange_name": item.exchange_name,
        "column_path_raw": item.column_path_raw,
        "project_name_raw": item.project_name_raw,
        "engineering_type_raw": item.engineering_type_raw,
        "channel_id": item.channel_id,
        "notice_family": item.notice_family,
        "source_priority": source_priority,
        "detail_url": item.detail_url,
    }
    metadata = {
        key: metadata_cell(value, source_level="crawler", tagged_by=parser_version)
        for key, value in raw_values.items()
        if key in TRADING_L1_HOOK_METADATA_KEYS or (value is not None and value != "")
    }

    gated = [
        {
            **attachment.metadata,
            "file_name": attachment.file_name,
            "url": attachment.url,
            "access_status": "LOCKED",
            "gated_reason": attachment.gated_reason,
            "file_role": attachment.file_role,
        }
        for attachment in item.attachments
        if attachment.gated
    ]
    if gated:
        metadata["gated_attachment_count"] = metadata_cell(len(gated), source_level="crawler", tagged_by=parser_version)
        metadata["gated_attachments"] = metadata_cell(gated, source_level="crawler", tagged_by=parser_version)

    for key, value in item.metadata.items():
        if key in metadata:
            continue
        if isinstance(value, dict) and "source_level" in value:
            metadata[key] = value
        else:
            metadata[key] = metadata_cell(value, source_level="crawler", tagged_by=parser_version)
    return metadata


def _source_metadata_for_item(
    item: TradingDiscoveredItem,
    attachment: TradingAttachment,
    *,
    source_priority: str,
) -> dict[str, object]:
    return {
        "discovered_at": item.discovered_at,
        "fetched_at": item.fetched_at,
        "detail_url": item.detail_url,
        "attachment_url": attachment.url,
        "channel_id": item.channel_id,
        "notice_family": item.notice_family,
        "notice_type_raw": item.notice_type_raw,
        "project_code_raw": item.project_code_raw,
        "tender_code_raw": item.tender_code_raw,
        "section_no_raw": item.section_no_raw,
        "bid_subject_role_raw": item.bid_subject_role_raw,
        "tenderer_raw": item.tenderer_raw,
        "agency_raw": item.agency_raw,
        "amount_raw": item.amount_raw,
        "project_name_raw": item.project_name_raw,
        "source_priority": source_priority,
    }
