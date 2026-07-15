from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.archive_rules import (
    ARCHIVE_STATUSES,
    build_cost_info_business_key,
    build_policy_regulation_business_key,
    build_quota_business_key,
    build_standard_atlas_business_key,
    build_trading_business_key,
    metadata_value,
    validate_archive_file_role,
    validate_archive_provenance,
)
from app.file_mirror import FileMirror
from app.models import Archive, ArchiveEvent, ArchiveFile, AuditLog, CrawlLineage, CollectionTask, DataSource, FileAsset, IngestEvent, Outbox

VALID_DOMAIN_TYPES = {"cost_info", "trading", "policy_regulation", "standard_atlas", "quota"}
VALID_CHANNEL_TYPES = {"crawler", "netdisk", "system_api", "manual_upload", "batch_import", "email_import"}
VALID_COLLECTION_METHODS = {"auto", "manual_recovery", "manual_denovo", "legacy_batch_import"}
VALID_VISIBILITY_SCOPES = {"public", "tenant", "private"}
VALID_PRICE_KINDS = {"guidance", "market_reference", "manufacturer_reference", "unspecified"}
VALID_PERIOD_KINDS = {"monthly", "issue_based", "bimonthly"}

STATUS_EVENT_TYPES = {
    "archived": "ARCHIVE_ARCHIVED",
    "quarantined": "ARCHIVE_QUARANTINED",
    "ready_for_governance": "ARCHIVE_READY_FOR_GOVERNANCE",
}
ILLEGAL_STATUS_TRANSITIONS = {
    ("discovered", "archived"),
    ("discovered", "ready_for_governance"),
    ("collecting", "archived"),
    ("quarantined", "archived"),
}
PROVENANCE_WALL_ERRORS = {
    "METADATA_EXTRACTION_FORBIDDEN",
    "FIELD_SOURCE_EXTRACTION_FORBIDDEN",
    "FIELD_SOURCE_MISSING",
}


def _require_json_object(value: dict | None, field_name: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    if "project_group_key" in value:
        raise ValueError("PROJECT_GROUP_KEY_FORBIDDEN")
    return dict(value)


def _parse_lineage_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _parse_archive_date(value: str | date | None) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"INVALID_ARCHIVE_PUBLISH_DATE: {value}") from exc
    raise ValueError(f"INVALID_ARCHIVE_PUBLISH_DATE: {value}")


def _date_to_iso(value: str | date | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _lineage_metadata_datetime(metadata: dict | None, keys: tuple[str, ...]) -> datetime | None:
    if not isinstance(metadata, dict):
        return None
    for key in keys:
        parsed = _parse_lineage_datetime(metadata.get(key))
        if parsed is not None:
            return parsed
    return None


def _lineage_discovered_at(event: IngestEvent) -> datetime | None:
    return _lineage_metadata_datetime(
        event.source_metadata,
        ("discovered_at", "source_discovered_at", "found_at", "listed_at"),
    ) or event.source_modified_at


def _lineage_fetched_at(event: IngestEvent) -> datetime | None:
    return _lineage_metadata_datetime(
        event.source_metadata,
        ("fetched_at", "downloaded_at", "source_fetched_at"),
    ) or event.ingested_at


def _lineage_parser_version(session: Session, event: IngestEvent) -> str | None:
    if not event.task_id:
        return None
    task = session.get(CollectionTask, event.task_id)
    if task is None or not isinstance(task.config_override, dict):
        return None
    return task.config_override.get("parser_version") or task.config_override.get("active_parser_version")


def _is_excel_asset(asset: FileAsset) -> bool:
    return asset.file_ext.lower() in {".xls", ".xlsx"}


def _apply_representation_roles(session: Session, archive_id: str) -> None:
    rows = session.execute(
        select(ArchiveFile, FileAsset)
        .join(FileAsset, ArchiveFile.file_id == FileAsset.file_id)
        .where(ArchiveFile.archive_id == archive_id)
        .order_by(ArchiveFile.sort_order, ArchiveFile.added_at)
    ).all()
    if not rows:
        return
    has_excel = any(_is_excel_asset(asset) for _, asset in rows)
    for index, (mounted, asset) in enumerate(rows):
        if has_excel:
            is_primary = _is_excel_asset(asset)
        else:
            is_primary = index == 0
        mounted.representation_role = "primary" if is_primary else "secondary"
        mounted.is_primary = is_primary


def _archive_has_file(session: Session, *, archive_id: str, file_id: str) -> bool:
    return (
        session.scalar(
            select(ArchiveFile.archive_file_id).where(
                ArchiveFile.archive_id == archive_id,
                ArchiveFile.file_id == file_id,
            )
        )
        is not None
    )


def _validate_archive_fields(
    *,
    domain_type: str | None = None,
    channel_type: str | None = None,
    collection_method: str | None = None,
    price_kind: str | None = None,
    period_kind: str | None = None,
    status: str | None = None,
    visibility_scope: str | None = None,
) -> None:
    if domain_type is not None and domain_type not in VALID_DOMAIN_TYPES:
        raise ValueError(f"UNSUPPORTED_ARCHIVE_DOMAIN: {domain_type}")
    if channel_type is not None and channel_type not in VALID_CHANNEL_TYPES:
        raise ValueError(f"UNSUPPORTED_ARCHIVE_CHANNEL: {channel_type}")
    if collection_method is not None and collection_method not in VALID_COLLECTION_METHODS:
        raise ValueError(f"UNSUPPORTED_COLLECTION_METHOD: {collection_method}")
    if price_kind is not None and price_kind not in VALID_PRICE_KINDS:
        raise ValueError(f"UNSUPPORTED_PRICE_KIND: {price_kind}")
    if domain_type is not None and domain_type != "cost_info" and price_kind not in (None, "unspecified"):
        raise ValueError(f"PRICE_KIND_ONLY_FOR_COST_INFO: {domain_type}")
    if period_kind is not None and period_kind not in VALID_PERIOD_KINDS:
        raise ValueError(f"UNSUPPORTED_PERIOD_KIND: {period_kind}")
    if domain_type is not None and domain_type != "cost_info" and period_kind not in (None, "monthly"):
        raise ValueError(f"PERIOD_KIND_ONLY_FOR_COST_INFO: {domain_type}")
    if status is not None and status not in ARCHIVE_STATUSES:
        raise ValueError(f"UNSUPPORTED_ARCHIVE_STATUS: {status}")
    if visibility_scope is not None and visibility_scope not in VALID_VISIBILITY_SCOPES:
        raise ValueError(f"UNSUPPORTED_ARCHIVE_VISIBILITY: {visibility_scope}")


def _event_for_patch(before_status: str, after_status: str) -> str:
    if before_status != after_status and after_status in STATUS_EVENT_TYPES:
        return STATUS_EVENT_TYPES[after_status]
    return "ARCHIVE_TAGGED"


def _add_event(
    session: Session,
    *,
    archive_id: str,
    event_type: str,
    actor_type: str | None,
    actor_id: str | None,
    before_payload: dict | None = None,
    after_payload: dict | None = None,
) -> ArchiveEvent:
    event = ArchiveEvent(
        archive_id=archive_id,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        before_payload=before_payload,
        after_payload=after_payload,
    )
    session.add(event)
    session.flush()
    session.add(
        Outbox(
            event_id=event.event_id,
            idempotency_key=f"archive_event:{event.event_id}",
        )
    )
    return event


def _serialize_archive_base(archive: Archive) -> dict[str, object]:
    return {
        "archive_id": archive.archive_id,
        "domain_type": archive.domain_type,
        "channel_type": archive.channel_type,
        "collection_method": archive.collection_method,
        "price_kind": archive.price_kind,
        "period_kind": archive.period_kind,
        "business_key": archive.business_key,
        "title": archive.title,
        "region_code": archive.region_code,
        "publish_date": _date_to_iso(archive.publish_date),
        "source_id": archive.source_id,
        "task_id": archive.task_id,
        "batch_id": archive.batch_id,
        "source_item_key": archive.source_item_key,
        "source_url": archive.source_url,
        "tenant_code": archive.tenant_code,
        "visibility_scope": archive.visibility_scope,
        "status": archive.status,
        "version": archive.version,
        "is_current": archive.is_current,
        "is_withdrawn": archive.is_withdrawn,
        "metadata": dict(archive.metadata_payload),
        "metadata_schema_version": archive.metadata_schema_version,
        "field_sources": archive.field_sources,
        "preview_status": archive.preview_status,
        "created_at": archive.created_at.isoformat(),
        "updated_at": archive.updated_at.isoformat(),
    }


def _source_config_cell(value: object) -> dict[str, object]:
    return {
        "value": value,
        "source_level": "source_config",
        "tagged_by": "archive_api:source_config_fallback",
        "tagged_at": "2026-06-25T00:00:00+08:00",
    }


def _apply_source_config_metadata_fallback(row: dict[str, object], source: DataSource | None) -> None:
    if row.get("domain_type") != "cost_info" or source is None:
        return
    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        return
    config = source.config if isinstance(source.config, dict) else {}
    stable = config.get("stable") if isinstance(config.get("stable"), dict) else {}
    fallback_values = {
        "coverage_region_code": stable.get("coverage_region_code") or source.region_code,
        "producer": stable.get("producer"),
        "publisher": stable.get("publisher") or stable.get("publisher_name") or source.name,
        "publisher_scope": stable.get("publisher_scope"),
        "publisher_type": stable.get("publisher_type"),
        "publisher_region_code": stable.get("publisher_region_code") or source.region_code,
        "publisher_name": stable.get("publisher_name") or source.name,
    }
    for key, value in fallback_values.items():
        if key in metadata or value is None:
            continue
        metadata[key] = _source_config_cell(value)


def _archive_field_snapshot(
    *,
    domain_type: str,
    channel_type: str,
    collection_method: str = "auto",
    price_kind: str = "unspecified",
    period_kind: str = "monthly",
    business_key: str,
    title: str,
    region_code: str | None,
    publish_date: str | None,
) -> dict[str, object]:
    return {
        "domain_type": domain_type,
        "channel_type": channel_type,
        "business_key": business_key,
        "title": title,
        "region_code": region_code,
        "publish_date": publish_date,
    }


def _record_rejected_write(
    session: Session,
    *,
    target_key: str,
    error_code: str,
    actor_type: str | None,
    actor_id: str | None,
    payload: dict,
) -> None:
    with Session(bind=session.get_bind()) as audit_session:
        audit_session.add(
            AuditLog(
                actor_type=actor_type,
                actor_id=actor_id,
                action="ARCHIVE_WRITE_REJECTED",
                target_type="archive",
                target_id=target_key,
                error_code=error_code,
                before_payload=None,
                after_payload={"error_code": error_code, "target_key": target_key, "payload": payload},
            )
        )
        audit_session.commit()


def _add_crawl_lineage_for_event(session: Session, *, archive_id: str, event: IngestEvent) -> None:
    session.add(
        CrawlLineage(
            archive_id=archive_id,
            source_id=event.source_id,
            task_id=event.task_id,
            batch_id=event.batch_id,
            source_item_key=event.source_item_key,
            source_url=event.source_url,
            parser_version=_lineage_parser_version(session, event),
            source_metadata=event.source_metadata,
            discovered_at=_lineage_discovered_at(event),
            fetched_at=_lineage_fetched_at(event),
        )
    )


def _validate_or_audit_archive_payload(
    session: Session,
    *,
    archive_fields: dict,
    metadata: dict | None,
    field_sources: dict | None,
    target_key: str,
    actor_type: str | None,
    actor_id: str | None,
) -> tuple[dict, dict]:
    try:
        return validate_archive_provenance(
            archive_fields=archive_fields,
            metadata=metadata,
            field_sources=field_sources,
        )
    except ValueError as exc:
        error_code = str(exc).split(":", 1)[0]
        if error_code in PROVENANCE_WALL_ERRORS:
            _record_rejected_write(
                session,
                target_key=target_key,
                error_code=error_code,
                actor_type=actor_type,
                actor_id=actor_id,
                payload={
                    "archive_fields": archive_fields,
                    "metadata": metadata,
                    "field_sources": field_sources,
                },
            )
        raise


FETCH_STATUS_FETCHED = "FETCHED"
FETCH_STATUS_PENDING = "PENDING_FETCH"


def _serialize_archive_file(
    mounted: ArchiveFile,
    asset: FileAsset | None,
    *,
    archive: Archive | None = None,
    mirror: FileMirror | None = None,
) -> dict[str, object]:
    row = {
        "archive_file_id": mounted.archive_file_id,
        "archive_id": mounted.archive_id,
        "file_id": mounted.file_id,
        "file_role": mounted.file_role,
        "fetch_status": mounted.fetch_status,
        "representation_role": mounted.representation_role,
        "item_id": mounted.item_id,
        "display_name": mounted.display_name,
        "source_url": mounted.source_url,
        "is_primary": mounted.is_primary,
        "sort_order": mounted.sort_order,
        "metadata": mounted.metadata_payload,
        "added_at": mounted.added_at.isoformat(),
        "file_name": asset.file_name if asset else mounted.display_name,
        "file_ext": asset.file_ext if asset else None,
        "mime_type": asset.mime_type if asset else mounted.metadata_payload.get("content_type"),
        "file_size": asset.file_size if asset else mounted.metadata_payload.get("expected_file_size"),
        "bucket": asset.bucket if asset else None,
        "object_key": asset.object_key if asset else None,
    }
    if archive is not None and mirror is not None:
        row.update(mirror.status_for(archive, mounted, asset))
    return row


def _archive_summary_rows(session: Session, archives: list[Archive], *, mirror: FileMirror | None = None) -> list[dict[str, object]]:
    if not archives:
        return []

    archive_ids = [archive.archive_id for archive in archives]
    files_by_archive: dict[str, list[tuple[ArchiveFile, FileAsset | None]]] = {}
    for mounted, asset in session.execute(
        select(ArchiveFile, FileAsset)
        .outerjoin(FileAsset, ArchiveFile.file_id == FileAsset.file_id)
        .where(ArchiveFile.archive_id.in_(archive_ids))
        .order_by(ArchiveFile.archive_id, ArchiveFile.sort_order, ArchiveFile.added_at)
    ).all():
        files_by_archive.setdefault(mounted.archive_id, []).append((mounted, asset))

    source_ids = {archive.source_id for archive in archives if archive.source_id}
    sources_by_id = {
        source.source_id: source
        for source in session.scalars(select(DataSource).where(DataSource.source_id.in_(source_ids))).all()
    } if source_ids else {}

    rows: list[dict[str, object]] = []
    for archive in archives:
        files = files_by_archive.get(archive.archive_id, [])
        primary_file = next((item for item in files if item[0].is_primary), files[0] if files else None)
        row = _serialize_archive_base(archive)
        _apply_source_config_metadata_fallback(row, sources_by_id.get(archive.source_id))
        row["file_count"] = len(files)
        row["priced_source_count"] = sum(1 for mounted, _ in files if mounted.file_role == "priced_source")
        row["primary_file"] = (
            {
                "file_id": primary_file[1].file_id if primary_file[1] else None,
                "file_name": primary_file[1].file_name if primary_file[1] else primary_file[0].display_name,
                "file_role": primary_file[0].file_role,
                **(
                    mirror.status_for(archive, primary_file[0], primary_file[1])
                    if mirror is not None
                    else {}
                ),
            }
            if primary_file
            else None
        )
        rows.append(row)
    return rows


def create_archive(
    session: Session,
    *,
    domain_type: str,
    channel_type: str,
    collection_method: str = "auto",
    price_kind: str = "unspecified",
    period_kind: str = "monthly",
    business_key: str,
    title: str,
    source_id: str,
    tenant_code: str,
    visibility_scope: str,
    status: str = "pending_tag",
    region_code: str | None = None,
    publish_date: str | date | None = None,
    task_id: str | None = None,
    batch_id: str | None = None,
    source_item_key: str | None = None,
    source_url: str | None = None,
    metadata: dict | None = None,
    field_sources: dict | None = None,
    actor_type: str | None = None,
    actor_id: str | None = None,
    allow_fileless_collected: bool = False,
) -> Archive:
    _validate_archive_fields(
        domain_type=domain_type,
        channel_type=channel_type,
        collection_method=collection_method,
        price_kind=price_kind,
        period_kind=period_kind,
        status=status,
        visibility_scope=visibility_scope,
    )
    if session.get(DataSource, source_id) is None:
        raise ValueError(f"DATA_SOURCE_NOT_FOUND: {source_id}")
    if status == "collected" and not allow_fileless_collected:
        raise ValueError("COLLECTED_REQUIRES_FILE")
    parsed_publish_date = _parse_archive_date(publish_date)
    archive_fields = _archive_field_snapshot(
        domain_type=domain_type,
        channel_type=channel_type,
        business_key=business_key,
        title=title,
        region_code=region_code,
        publish_date=_date_to_iso(parsed_publish_date),
    )
    metadata_payload, field_sources_payload = _validate_or_audit_archive_payload(
        session,
        archive_fields=archive_fields,
        metadata=metadata,
        field_sources=field_sources,
        target_key=business_key,
        actor_type=actor_type,
        actor_id=actor_id,
    )
    existing = session.scalar(
        select(Archive).where(
            Archive.tenant_code == tenant_code,
            Archive.domain_type == domain_type,
            Archive.business_key == business_key,
            Archive.version == 1,
        )
    )
    if existing is not None:
        raise ValueError("ARCHIVE_BUSINESS_KEY_EXISTS")
    archive = Archive(
        domain_type=domain_type,
        channel_type=channel_type,
        business_key=business_key,
        title=title,
        region_code=region_code,
        publish_date=parsed_publish_date,
        source_id=source_id,
        task_id=task_id,
        batch_id=batch_id,
        source_item_key=source_item_key,
        source_url=source_url,
        tenant_code=tenant_code,
        visibility_scope=visibility_scope,
        status=status,
        collection_method=collection_method,
        price_kind=price_kind,
        period_kind=period_kind,
        metadata_payload=metadata_payload,
        field_sources=field_sources_payload,
    )
    session.add(archive)
    session.flush()
    _add_event(
        session,
        archive_id=archive.archive_id,
        event_type="ARCHIVE_CREATED",
        actor_type=actor_type,
        actor_id=actor_id,
        after_payload={"status": archive.status},
    )
    session.commit()
    session.refresh(archive)
    return archive


def _build_default_business_key(
    *,
    domain_type: str,
    source_id: str,
    source_item_key: str | None,
    title: str,
    publish_date: str | None,
    metadata: dict,
    event_id: str,
    region_code: str | None = None,
) -> str:
    plain_metadata = {key: metadata_value(value) for key, value in metadata.items()}
    if domain_type == "cost_info":
        return build_cost_info_business_key(
            source_id=source_id,
            region_code=region_code,
            period=plain_metadata.get("period_start") or plain_metadata.get("period_raw"),
            title=title,
        )
    if domain_type == "trading":
        return build_trading_business_key(
            source_id=source_id,
            source_item_key=source_item_key,
            title=title,
            publish_date=publish_date,
            notice_type_raw=plain_metadata.get("notice_type_raw"),
        )
    if domain_type == "quota":
        return build_quota_business_key(
            source_id=source_id,
            title=title,
            region=plain_metadata.get("region_raw") or plain_metadata.get("region"),
            quota_code=plain_metadata.get("quota_code_raw") or plain_metadata.get("quota_code"),
            version=plain_metadata.get("version"),
            effective_date=plain_metadata.get("effective_date"),
            standard_no=plain_metadata.get("standard_no"),
        )
    if domain_type == "policy_regulation":
        return build_policy_regulation_business_key(
            source_id=source_id,
            title=title,
            issuing_authority=plain_metadata.get("issuing_authority_raw") or plain_metadata.get("issuing_authority"),
            document_no=plain_metadata.get("document_no_raw") or plain_metadata.get("document_no"),
            publish_date=publish_date or plain_metadata.get("publish_date_raw"),
        )
    if domain_type == "standard_atlas":
        return build_standard_atlas_business_key(
            source_id=source_id,
            title=title,
            standard_no=plain_metadata.get("standard_no"),
            version=plain_metadata.get("version"),
            publish_date=publish_date,
        )
    return f"{domain_type}:{source_id}:{source_item_key or event_id}"


def create_archive_from_ingest_event_with_flag(
    session: Session,
    *,
    event_id: str,
    domain_type: str,
    channel_type: str,
    collection_method: str = "auto",
    price_kind: str = "unspecified",
    period_kind: str = "monthly",
    title: str,
    visibility_scope: str = "public",
    status: str = "pending_tag",
    business_key: str | None = None,
    region_code: str | None = None,
    publish_date: str | date | None = None,
    metadata: dict | None = None,
    field_sources: dict | None = None,
    actor_type: str | None = None,
    actor_id: str | None = None,
) -> tuple[Archive, bool]:
    event = session.get(IngestEvent, event_id)
    if event is None:
        raise ValueError(f"INGEST_EVENT_NOT_FOUND: {event_id}")
    asset = session.get(FileAsset, event.file_id)
    if asset is None:
        raise ValueError(f"FILE_ASSET_NOT_FOUND: {event.file_id}")
    if event.source_id is None:
        raise ValueError("INGEST_EVENT_SOURCE_REQUIRED")

    parsed_publish_date = _parse_archive_date(publish_date)
    metadata_payload = _require_json_object(metadata, "metadata")
    generated_business_key = business_key is None
    resolved_business_key = business_key or _build_default_business_key(
        domain_type=domain_type,
        source_id=event.source_id,
        source_item_key=event.source_item_key,
        title=title,
        publish_date=_date_to_iso(parsed_publish_date),
        metadata=metadata_payload,
        event_id=event.event_id,
        region_code=region_code,
    )
    field_sources_payload = _require_json_object(field_sources, "field_sources")
    _validate_archive_fields(
        domain_type=domain_type,
        channel_type=channel_type,
        collection_method=collection_method,
        price_kind=price_kind,
        period_kind=period_kind,
        status=status,
        visibility_scope=visibility_scope,
    )
    if generated_business_key and "business_key" not in field_sources_payload:
        source_level = "crawler" if event.source_item_key or event.source_url else "filename"
        field_sources_payload["business_key"] = {
            "source_level": source_level,
            "tagged_by": actor_id or actor_type or "system:ingest",
            "tagged_at": event.ingested_at.isoformat(),
        }
    existing = session.scalar(
        select(Archive).where(
            Archive.tenant_code == asset.tenant_code,
            Archive.domain_type == domain_type,
            Archive.business_key == resolved_business_key,
            Archive.version == 1,
        )
    )
    if existing is not None and domain_type == "cost_info":
        archive_fields = _archive_field_snapshot(
            domain_type=existing.domain_type,
            channel_type=existing.channel_type,
            business_key=existing.business_key,
            title=existing.title,
            region_code=existing.region_code,
            publish_date=_date_to_iso(existing.publish_date),
        )
        _validate_or_audit_archive_payload(
            session,
            archive_fields=archive_fields,
            metadata=metadata_payload,
            field_sources=field_sources_payload,
            target_key=existing.business_key,
            actor_type=actor_type,
            actor_id=actor_id,
        )
        _add_crawl_lineage_for_event(session, archive_id=existing.archive_id, event=event)
        if not _archive_has_file(session, archive_id=existing.archive_id, file_id=asset.file_id):
            attach_file(
                session,
                existing.archive_id,
                asset.file_id,
                file_role=None,
                display_name=event.original_name,
                source_url=event.source_url,
                sort_order=10,
                metadata={"source_event_id": event.event_id},
                actor_type=actor_type,
                actor_id=actor_id,
            )
        session.refresh(existing)
        return existing, True
    archive = create_archive(
        session,
        domain_type=domain_type,
        channel_type=channel_type,
        collection_method=collection_method,
        price_kind=price_kind,
        period_kind=period_kind,
        business_key=resolved_business_key,
        title=title,
        region_code=region_code,
        publish_date=parsed_publish_date,
        source_id=event.source_id,
        task_id=event.task_id,
        batch_id=event.batch_id,
        source_item_key=event.source_item_key,
        source_url=event.source_url,
        tenant_code=asset.tenant_code,
        visibility_scope=visibility_scope,
        status=status,
        metadata=metadata_payload,
        field_sources=field_sources_payload,
        actor_type=actor_type,
        actor_id=actor_id,
        allow_fileless_collected=status == "collected",
    )
    _add_crawl_lineage_for_event(session, archive_id=archive.archive_id, event=event)
    attach_file(
        session,
        archive.archive_id,
        asset.file_id,
        file_role=None,
        display_name=event.original_name,
        source_url=event.source_url,
        is_primary=True,
        sort_order=10,
        metadata={"source_event_id": event.event_id},
        actor_type=actor_type,
        actor_id=actor_id,
    )
    session.refresh(archive)
    return archive, False


def create_archive_from_ingest_event(session: Session, **kwargs: object) -> Archive:
    """Backward-compatible wrapper; drops the attached_to_existing flag."""
    archive, _attached_to_existing = create_archive_from_ingest_event_with_flag(session, **kwargs)
    return archive


def _apply_archive_list_filters(
    statement,
    *,
    domain_type: str | None = None,
    status: str | None = None,
    region_code: str | None = None,
    channel_type: str | None = None,
    tenant_code: str | None = None,
    source_id: str | None = None,
    search: str | None = None,
):
    statement = statement.where(Archive.is_withdrawn.is_(False))
    if domain_type:
        statement = statement.where(Archive.domain_type == domain_type)
    if status:
        statement = statement.where(Archive.status == status)
    if region_code:
        statement = statement.where(Archive.region_code == region_code)
    if channel_type:
        statement = statement.where(Archive.channel_type == channel_type)
    if tenant_code:
        statement = statement.where(Archive.tenant_code == tenant_code)
    if source_id:
        statement = statement.where(Archive.source_id == source_id)
    if search:
        pattern = f"%{search}%"
        statement = statement.where(or_(Archive.title.like(pattern), Archive.business_key.like(pattern)))
    return statement


def count_archives(
    session: Session,
    *,
    domain_type: str | None = None,
    status: str | None = None,
    region_code: str | None = None,
    channel_type: str | None = None,
    tenant_code: str | None = None,
    source_id: str | None = None,
    search: str | None = None,
) -> int:
    _validate_archive_fields(domain_type=domain_type, channel_type=channel_type, status=status)
    statement = _apply_archive_list_filters(
        select(func.count()).select_from(Archive),
        domain_type=domain_type,
        status=status,
        region_code=region_code,
        channel_type=channel_type,
        tenant_code=tenant_code,
        source_id=source_id,
        search=search,
    )
    return int(session.scalar(statement) or 0)


def list_archives(
    session: Session,
    *,
    domain_type: str | None = None,
    status: str | None = None,
    region_code: str | None = None,
    channel_type: str | None = None,
    tenant_code: str | None = None,
    source_id: str | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
    mirror: FileMirror | None = None,
) -> list[dict[str, object]]:
    _validate_archive_fields(domain_type=domain_type, channel_type=channel_type, status=status)
    capped_limit = max(1, min(limit, 500))
    safe_offset = max(0, offset)
    statement = _apply_archive_list_filters(
        select(Archive),
        domain_type=domain_type,
        status=status,
        region_code=region_code,
        channel_type=channel_type,
        tenant_code=tenant_code,
        source_id=source_id,
        search=search,
    )
    # The archive list is a publication feed. Newest source publication must
    # lead, rather than the moment our crawler happened to ingest a record.
    # Explicit NULLS LAST keeps un-dated/manual archives below dated notices.
    statement = (
        statement.order_by(
            Archive.publish_date.desc().nullslast(),
            Archive.created_at.desc(),
            Archive.archive_id.desc(),
        )
        .offset(safe_offset)
        .limit(capped_limit)
    )

    return _archive_summary_rows(session, session.scalars(statement).all(), mirror=mirror)


def get_archive_detail(session: Session, archive_id: str, *, mirror: FileMirror | None = None) -> dict[str, object]:
    archive = session.get(Archive, archive_id)
    if archive is None or archive.is_withdrawn:
        raise ValueError(f"ARCHIVE_NOT_FOUND: {archive_id}")
    detail = _serialize_archive_base(archive)
    _apply_source_config_metadata_fallback(detail, session.get(DataSource, archive.source_id))
    detail["files"] = [
        _serialize_archive_file(mounted, asset, archive=archive, mirror=mirror)
        for mounted, asset in session.execute(
            select(ArchiveFile, FileAsset)
            .outerjoin(FileAsset, ArchiveFile.file_id == FileAsset.file_id)
            .where(ArchiveFile.archive_id == archive.archive_id)
            .order_by(ArchiveFile.sort_order, ArchiveFile.added_at)
        ).all()
    ]
    return detail


def patch_archive(
    session: Session,
    archive_id: str,
    *,
    business_key: str | None = None,
    price_kind: str | None = None,
    period_kind: str | None = None,
    title: str | None = None,
    region_code: str | None = None,
    publish_date: str | date | None = None,
    visibility_scope: str | None = None,
    status: str | None = None,
    metadata: dict | None = None,
    field_sources: dict | None = None,
    actor_type: str | None = None,
    actor_id: str | None = None,
) -> Archive:
    archive = session.get(Archive, archive_id)
    if archive is None:
        raise ValueError(f"ARCHIVE_NOT_FOUND: {archive_id}")
    _validate_archive_fields(
        domain_type=archive.domain_type,
        price_kind=price_kind,
        period_kind=period_kind,
        status=status,
        visibility_scope=visibility_scope,
    )
    if status is not None and (archive.status, status) in ILLEGAL_STATUS_TRANSITIONS:
        raise ValueError(f"ILLEGAL_ARCHIVE_STATUS_TRANSITION: {archive.status}->{status}")
    if archive.status == "quarantined" and status == "archived":
        raise ValueError("QUARANTINED_ARCHIVE_REQUIRES_REPAIR")

    before_payload = {
        "business_key": archive.business_key,
        "price_kind": archive.price_kind,
        "period_kind": archive.period_kind,
        "title": archive.title,
        "region_code": archive.region_code,
        "publish_date": _date_to_iso(archive.publish_date),
        "visibility_scope": archive.visibility_scope,
        "status": archive.status,
        "metadata": dict(archive.metadata_payload),
        "field_sources": dict(archive.field_sources),
    }
    before_status = archive.status

    next_business_key = business_key if business_key is not None else archive.business_key
    next_price_kind = price_kind if price_kind is not None else archive.price_kind
    next_period_kind = period_kind if period_kind is not None else archive.period_kind
    next_title = title if title is not None else archive.title
    next_region_code = region_code if region_code is not None else archive.region_code
    next_publish_date = _parse_archive_date(publish_date) if publish_date is not None else archive.publish_date
    next_visibility_scope = visibility_scope if visibility_scope is not None else archive.visibility_scope
    next_status = status if status is not None else archive.status
    next_metadata = dict(archive.metadata_payload)
    if metadata is not None:
        next_metadata = {**next_metadata, **_require_json_object(metadata, "metadata")}
    next_field_sources = dict(archive.field_sources)
    if field_sources is not None:
        next_field_sources = {**next_field_sources, **_require_json_object(field_sources, "field_sources")}

    if next_business_key != archive.business_key:
        existing = session.scalar(
            select(Archive).where(
                Archive.tenant_code == archive.tenant_code,
                Archive.domain_type == archive.domain_type,
                Archive.business_key == next_business_key,
                Archive.version == archive.version,
                Archive.archive_id != archive.archive_id,
            )
        )
        if existing is not None:
            raise ValueError("ARCHIVE_BUSINESS_KEY_EXISTS")

    archive_fields = _archive_field_snapshot(
        domain_type=archive.domain_type,
        channel_type=archive.channel_type,
        business_key=next_business_key,
        title=next_title,
        region_code=next_region_code,
        publish_date=_date_to_iso(next_publish_date),
    )
    metadata_payload, field_sources_payload = _validate_or_audit_archive_payload(
        session,
        archive_fields=archive_fields,
        metadata=next_metadata,
        field_sources=next_field_sources,
        target_key=next_business_key,
        actor_type=actor_type,
        actor_id=actor_id,
    )

    archive.business_key = next_business_key
    archive.price_kind = next_price_kind
    archive.period_kind = next_period_kind
    archive.title = next_title
    archive.region_code = next_region_code
    archive.publish_date = next_publish_date
    archive.visibility_scope = next_visibility_scope
    archive.status = next_status
    archive.metadata_payload = metadata_payload
    archive.field_sources = field_sources_payload

    after_payload = {
        "business_key": archive.business_key,
        "price_kind": archive.price_kind,
        "period_kind": archive.period_kind,
        "title": archive.title,
        "region_code": archive.region_code,
        "publish_date": _date_to_iso(archive.publish_date),
        "visibility_scope": archive.visibility_scope,
        "status": archive.status,
        "metadata": dict(archive.metadata_payload),
        "field_sources": dict(archive.field_sources),
    }
    event_type = _event_for_patch(before_status, archive.status)
    _add_event(
        session,
        archive_id=archive.archive_id,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        before_payload=before_payload,
        after_payload=after_payload,
    )
    session.add(
        AuditLog(
            actor_type=actor_type,
            actor_id=actor_id,
            action="ARCHIVE_TAGGED",
            target_type="archive",
            target_id=archive.archive_id,
            before_payload=before_payload,
            after_payload=after_payload,
        )
    )
    session.commit()
    session.refresh(archive)
    return archive


def delete_archive(
    session: Session,
    archive_id: str,
    *,
    actor_type: str | None = None,
    actor_id: str | None = None,
) -> dict[str, object]:
    archive = session.get(Archive, archive_id)
    if archive is None:
        raise ValueError(f"ARCHIVE_NOT_FOUND: {archive_id}")
    before_payload = _serialize_archive_base(archive)
    event_ids = session.scalars(select(ArchiveEvent.event_id).where(ArchiveEvent.archive_id == archive_id)).all()
    if event_ids:
        session.execute(delete(Outbox).where(Outbox.event_id.in_(event_ids)))
    session.execute(delete(ArchiveEvent).where(ArchiveEvent.archive_id == archive_id))
    session.execute(delete(CrawlLineage).where(CrawlLineage.archive_id == archive_id))
    session.execute(delete(ArchiveFile).where(ArchiveFile.archive_id == archive_id))
    session.delete(archive)
    session.add(
        AuditLog(
            actor_type=actor_type,
            actor_id=actor_id,
            action="ARCHIVE_DELETED",
            target_type="archive",
            target_id=archive_id,
            before_payload=before_payload,
            after_payload={"deleted": True},
        )
    )
    session.commit()
    return before_payload


def withdraw_archive(
    session: Session,
    archive_id: str,
    *,
    actor_type: str | None = None,
    actor_id: str | None = None,
) -> Archive:
    """Soft-delete an archive while preserving files, lineage, and audit history."""

    archive = session.get(Archive, archive_id)
    if archive is None:
        raise ValueError(f"ARCHIVE_NOT_FOUND: {archive_id}")
    if archive.is_withdrawn:
        raise ValueError(f"ARCHIVE_ALREADY_WITHDRAWN: {archive_id}")

    before_payload = {
        "is_current": archive.is_current,
        "is_withdrawn": archive.is_withdrawn,
        "status": archive.status,
    }
    archive.is_current = False
    archive.is_withdrawn = True
    archive.updated_at = datetime.now(UTC)
    after_payload = {
        "is_current": False,
        "is_withdrawn": True,
        "status": archive.status,
        "storage_deleted": False,
    }
    _add_event(
        session,
        archive_id=archive.archive_id,
        event_type="ARCHIVE_WITHDRAWN",
        actor_type=actor_type,
        actor_id=actor_id,
        before_payload=before_payload,
        after_payload=after_payload,
    )
    session.add(
        AuditLog(
            actor_type=actor_type,
            actor_id=actor_id,
            action="ARCHIVE_WITHDRAWN",
            target_type="archive",
            target_id=archive.archive_id,
            before_payload=before_payload,
            after_payload=after_payload,
        )
    )
    session.commit()
    session.refresh(archive)
    return archive


def attach_file(
    session: Session,
    archive_id: str,
    file_id: str,
    *,
    file_role: str | None = None,
    item_id: str | None = None,
    display_name: str | None = None,
    source_url: str | None = None,
    is_primary: bool = False,
    sort_order: int = 100,
    metadata: dict | None = None,
    actor_type: str | None = None,
    actor_id: str | None = None,
) -> ArchiveFile:
    archive = session.get(Archive, archive_id)
    if archive is None:
        raise ValueError(f"ARCHIVE_NOT_FOUND: {archive_id}")
    asset = session.get(FileAsset, file_id)
    if asset is None:
        raise ValueError(f"FILE_ASSET_NOT_FOUND: {file_id}")
    if asset.tenant_code != archive.tenant_code:
        raise ValueError("ARCHIVE_FILE_TENANT_MISMATCH")

    resolved_role = validate_archive_file_role(
        file_name=asset.file_name,
        requested_role=file_role,
        is_primary=is_primary,
        domain_type=archive.domain_type,
    )
    mounted = ArchiveFile(
        archive_id=archive.archive_id,
        file_id=asset.file_id,
        file_role=resolved_role,
        item_id=item_id,
        display_name=display_name,
        source_url=source_url,
        is_primary=is_primary,
        sort_order=sort_order,
        metadata_payload=_require_json_object(metadata, "metadata"),
    )
    session.add(mounted)
    session.flush()
    _apply_representation_roles(session, archive.archive_id)
    event_type = "ADD_REPRESENTATION" if archive.status == "archived" else "ARCHIVE_FILE_ATTACHED"
    _add_event(
        session,
        archive_id=archive.archive_id,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        before_payload={"version": archive.version, "status": archive.status},
        after_payload={"version": archive.version, "status": archive.status, "file_id": asset.file_id, "file_role": resolved_role},
    )
    if archive.status == "archived":
        session.add(
            AuditLog(
                actor_type=actor_type,
                actor_id=actor_id,
                action="ADD_REPRESENTATION",
                target_type="archive",
                target_id=archive.archive_id,
                before_payload={"version": archive.version},
                after_payload={"version": archive.version, "file_id": asset.file_id, "file_role": resolved_role},
            )
        )
    session.commit()
    session.refresh(mounted)
    return mounted


def attach_pending_file(
    session: Session,
    archive_id: str,
    *,
    file_role: str,
    display_name: str,
    source_url: str,
    item_id: str | None = None,
    sort_order: int = 100,
    metadata: dict | None = None,
    actor_type: str | None = None,
    actor_id: str | None = None,
) -> ArchiveFile:
    archive = session.get(Archive, archive_id)
    if archive is None:
        raise ValueError(f"ARCHIVE_NOT_FOUND: {archive_id}")

    resolved_role = validate_archive_file_role(
        file_name=display_name,
        requested_role=file_role,
        is_primary=False,
        domain_type=archive.domain_type,
    )
    payload = _require_json_object(metadata, "metadata")
    payload.setdefault("access_status", FETCH_STATUS_PENDING)
    mounted = ArchiveFile(
        archive_id=archive.archive_id,
        file_id=None,
        file_role=resolved_role,
        fetch_status=FETCH_STATUS_PENDING,
        item_id=item_id,
        display_name=display_name,
        source_url=source_url,
        is_primary=False,
        sort_order=sort_order,
        metadata_payload=payload,
    )
    session.add(mounted)
    session.flush()
    _add_event(
        session,
        archive_id=archive.archive_id,
        event_type="ARCHIVE_FILE_ATTACHED",
        actor_type=actor_type,
        actor_id=actor_id,
        before_payload={"version": archive.version, "status": archive.status},
        after_payload={
            "version": archive.version,
            "status": archive.status,
            "file_role": resolved_role,
            "display_name": display_name,
            "source_url": source_url,
            "fetch_status": FETCH_STATUS_PENDING,
        },
    )
    session.commit()
    session.refresh(mounted)
    return mounted
