from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.archive_rules import infer_archive_file_role, metadata_cell, now_iso
from app.archive_service import create_archive_from_ingest_event
from app.assets import register_asset
from app.collection import create_collection_task
from app.models import Archive
from app.source_adapter import active_parser_version, config_digest, require_data_source
from app.storage import ObjectStore

QUOTA_REGISTRY_RED_LINES = ["do_not_parse_quota_content", "do_not_parse_bill_standard_rows", "do_not_decompress_container"]
QUOTA_FILE_ROLE_RULES = {"quota_db": ["定额"], "bill_standard": ["清单", "gb50500"], "quota_supplement": ["补充定额"], "quota_interpretation": ["定额解释"]}
QUOTA_METADATA_KEYS = {
    "quota_db": {"region_raw", "quota_code_raw", "version", "effective_date", "specialty_raw"},
    "quota_supplement": {"region_raw", "quota_code_raw", "version", "effective_date", "parent_quota_ref"},
    "quota_interpretation": {"region_raw", "quota_code_raw", "version", "effective_date", "parent_quota_ref"},
    "bill_standard": {"standard_no", "version", "effective_date"},
}


@dataclass(frozen=True)
class QuotaDiscoveredFile:
    source_item_key: str; title: str; file_name: str; content: bytes
    source_url: str | None = None; content_type: str | None = None
    region_code: str | None = None; publish_date: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    discovered_at: str | None = None; fetched_at: str | None = None
    source_level: str = "manual"


def quota_source_config(*, site_id: str, publisher_name: str, entry_url: str, region_code: str | None, parser_version: str) -> dict[str, object]:
    parser = {
        "scope": "source_file_identity_only",
        "quota_registry": {"collection_mode": "initial_snapshot_plus_patch_stream", "preferred_source_files": ["xlsx", "pdf"], "file_role_rules": QUOTA_FILE_ROLE_RULES},
        "red_lines": QUOTA_REGISTRY_RED_LINES,
    }
    return {
        "registry_schema_version": "source_registry.v1",
        "stable": {
            "site_id": site_id, "domain_type": "quota", "region_code": region_code, "publisher_name": publisher_name,
            "publisher_type": "quota_registry", "entry_url": entry_url, "issue_period": "versioned_reference",
        },
        "parser": {"active_parser_version": parser_version, "parsers": {parser_version: parser}},
        "ops": {"enabled": True, "crawl_strategy_tier": "tier0_static_snapshot", "schedule_frequency": "manual_snapshot_until_source_verified"},
    }


def ingest_quota_file(
    session: Session,
    storage: ObjectStore,
    *,
    source_id: str,
    item: QuotaDiscoveredFile,
    actor_id: str = "quota-registry",
) -> Archive:
    source = require_data_source(session, source_id, domain_type="quota")
    if not item.content:
        raise ValueError("QUOTA_FILE_EMPTY")

    config = source.config or {}
    stable = config.get("stable") if isinstance(config.get("stable"), dict) else {}
    parser_version = active_parser_version(config)
    file_role = infer_archive_file_role(file_name=item.file_name, domain_type="quota", is_primary=True)
    region_code = item.region_code or source.region_code or stable.get("region_code")

    task = create_collection_task(
        session, source_id=source.source_id, operator_type="system", operator_id=actor_id,
        task_type="snapshot_import", trigger_type="manual", data_domain="quota",
        period_raw=str(item.metadata.get("version") or "") or None, period_start=_period_start(item.metadata.get("effective_date")),
        config_override={
            "source_registry_schema_version": config.get("registry_schema_version", "source_registry.v1"),
            "site_id": stable.get("site_id"), "parser_version": parser_version, "config_digest": config_digest(config),
            "config_snapshot": config, "file_role": file_role,
        },
    )
    result = register_asset(
        session, storage, tenant_code=source.asset_tenant_code, source_type=source.source_type, batch_id=task.batch_id,
        task_id=task.task_id, file_name=item.file_name, content=item.content, source_url=item.source_url,
        source_item_key=item.source_item_key, mime_type=item.content_type, derive_tasks=False,
        source_metadata={"discovered_at": item.discovered_at, "fetched_at": item.fetched_at, "source_item_key": item.source_item_key, "file_role": file_role},
    )
    if result.ingest_event_id is None:
        raise ValueError("QUOTA_INGEST_EVENT_REQUIRED")

    fields = ["domain_type", "channel_type", "business_key", "title"]
    if region_code:
        fields.append("region_code")
    if item.publish_date:
        fields.append("publish_date")
    return create_archive_from_ingest_event(
        session, event_id=result.ingest_event_id, domain_type="quota", channel_type="batch_import",
        collection_method="legacy_batch_import", title=item.title, region_code=str(region_code) if region_code else None,
        publish_date=item.publish_date, visibility_scope="public", status="collected",
        metadata=_metadata_cells(item, file_role=file_role, parser_version=parser_version),
        field_sources=_field_sources(fields, source_level=item.source_level, tagged_by=actor_id),
        actor_type="system", actor_id=actor_id,
    )


def _metadata_cells(item: QuotaDiscoveredFile, *, file_role: str, parser_version: str) -> dict[str, dict[str, object]]:
    grouped_keys = QUOTA_METADATA_KEYS.get(file_role, {"version", "parent_quota_ref"})
    return {
        key: metadata_cell(value, source_level=item.source_level, tagged_by=parser_version)
        for key, value in item.metadata.items()
        if key in grouped_keys and value is not None and value != ""
    }


def _field_sources(fields: list[str], *, source_level: str, tagged_by: str) -> dict[str, dict[str, str]]:
    tagged_at = now_iso()
    return {field: {"source_level": source_level, "tagged_by": tagged_by, "tagged_at": tagged_at} for field in fields}


def _period_start(value: object) -> str | None:
    return value[:7] if isinstance(value, str) and len(value) >= 7 else None
