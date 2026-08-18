from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.quota_taxonomy import (
    DOCUMENT_ROLES,
    JURISDICTION_LEVELS,
    LEGAL_STATUSES,
    LINK_SOURCES,
    MATERIAL_TYPES,
    PROJECTION_STATUSES,
    PUBLICATION_RELATION_TYPES,
    QUOTA_METADATA_STATUSES,
    QUOTA_SYSTEM_TYPES,
    sql_in_clause,
)
from app.archive_rules import ARCHIVE_FILE_ROLES  # v0.7 fix: ck_archive_file_role 改用动态 sql_in_clause


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


def archive_json_type():
    return JSON().with_variant(JSONB(), "postgresql")


def ordered_id_type():
    return BigInteger().with_variant(Integer(), "sqlite")


class Base(DeclarativeBase):
    pass


class DataSource(Base):
    __tablename__ = "data_source"
    __table_args__ = (
        CheckConstraint(
            "(source_scope = 'platform_public' and tenant_code is null and asset_tenant_code = 'platform_public') "
            "or "
            "(source_scope = 'tenant_private' and tenant_code is not null and asset_tenant_code = tenant_code)",
            name="ck_data_source_scope_tenant",
        ),
        CheckConstraint("managed_by in ('platform', 'tenant')", name="ck_data_source_managed_by"),
        UniqueConstraint(
            "source_scope",
            "asset_tenant_code",
            "source_type",
            "province",
            "city",
            "name",
            name="uq_data_source_identity_region",
        ),
        UniqueConstraint("source_id", "asset_tenant_code", name="uq_data_source_tenant_pair"),
    )

    source_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_scope: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    tenant_code: Mapped[str | None] = mapped_column(String(64), index=True)
    asset_tenant_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    managed_by: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    connector_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_url: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    url_alt: Mapped[str | None] = mapped_column(Text)
    province: Mapped[str | None] = mapped_column(String(64), index=True)
    city: Mapped[str | None] = mapped_column(String(64), index=True)
    region_code: Mapped[str | None] = mapped_column(String(32), index=True)
    data_domain: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    auth_secret_ref: Mapped[str | None] = mapped_column(Text)
    format: Mapped[str | None] = mapped_column(String(64), index=True)
    downloadable: Mapped[bool | None] = mapped_column(Boolean, index=True)
    bucket: Mapped[str | None] = mapped_column(String(32), index=True)
    owner: Mapped[str | None] = mapped_column(String(64), index=True)
    reviewer: Mapped[str | None] = mapped_column(String(64), index=True)
    remark: Mapped[str | None] = mapped_column(Text)
    frequency: Mapped[str | None] = mapped_column(String(32), index=True)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    schedule_policy: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)
    created_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tasks: Mapped[list["CollectionTask"]] = relationship(back_populates="data_source")
    ingest_events: Mapped[list["IngestEvent"]] = relationship(back_populates="data_source")


class CollectionTask(Base):
    __tablename__ = "collection_task"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_id", "asset_tenant_code"],
            ["data_source.source_id", "data_source.asset_tenant_code"],
            name="fk_collection_task_source_tenant",
        ),
        CheckConstraint(
            "operator_type in ('platform_ops', 'tenant_user', 'system')",
            name="ck_collection_task_operator_type",
        ),
        UniqueConstraint("source_id", "batch_id", name="uq_collection_task_source_batch"),
        # Cell-level idempotency: at most one pending/running task per
        # (coverage_region_code, period, domain). Backs POST /lake/collect-tasks.
        Index(
            "uq_collection_task_cell_active",
            "coverage_region_code",
            "period_start",
            "data_domain",
            unique=True,
            postgresql_where=text(
                "status IN ('pending', 'running') AND coverage_region_code IS NOT NULL"
            ),
            sqlite_where=text("status IN ('pending', 'running') AND coverage_region_code IS NOT NULL"),
        ),
    )

    task_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    asset_tenant_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    operator_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    operator_id: Mapped[str | None] = mapped_column(String(128))
    task_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    batch_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    data_domain: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[str | None] = mapped_column(String(128), index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    cursor_before: Mapped[dict | None] = mapped_column(JSON)
    cursor_after: Mapped[dict | None] = mapped_column(JSON)
    config_override: Mapped[dict | None] = mapped_column(JSON)
    period_raw: Mapped[str | None] = mapped_column(String(64), index=True)
    period_start: Mapped[str | None] = mapped_column(String(7), index=True)
    period_end: Mapped[str | None] = mapped_column(String(7), index=True)
    period_note: Mapped[str | None] = mapped_column(String(64), index=True)
    # Coverage cell the task targets (set for crawl_issue / cell-backfill tasks).
    # Paired with period_start + data_domain via uq_collection_task_cell_active.
    coverage_region_code: Mapped[str | None] = mapped_column(String(32), index=True)
    discovered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    downloaded_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processing_created_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_of_task_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("collection_task.task_id"))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    data_source: Mapped[DataSource] = relationship(back_populates="tasks")
    retry_of: Mapped["CollectionTask | None"] = relationship(remote_side=[task_id])
    ingest_events: Mapped[list["IngestEvent"]] = relationship(back_populates="collection_task")


class SourceCrawlState(Base):
    """Mutable scheduler state kept separate from a source's static policy.

    ``DataSource.schedule_policy`` is configuration that may be imported again
    from the source registry.  Keeping next-run/cursor information here means a
    configuration refresh can never accidentally reset a production crawl loop.
    """

    __tablename__ = "source_crawl_state"

    source_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("data_source.source_id"), primary_key=True
    )
    data_domain: Mapped[str] = mapped_column(String(64), nullable=False, default="cost_info", index=True)
    next_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_new_item_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_item_key: Mapped[str | None] = mapped_column(Text)
    last_seen_publish_date: Mapped[str | None] = mapped_column(String(32))
    consecutive_empty_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cursor: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class CrawlCampaign(Base):
    """An auditable, source-scoped historical collection run."""

    __tablename__ = "crawl_campaign"
    __table_args__ = (
        CheckConstraint(
            "mode in ('history_backfill', 'reconcile')",
            name="ck_crawl_campaign_mode",
        ),
        CheckConstraint(
            "status in ('pending', 'running', 'completed', 'completed_with_errors', 'failed', 'cancelled')",
            name="ck_crawl_campaign_status",
        ),
    )

    campaign_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(String(36), ForeignKey("data_source.source_id"), nullable=False, index=True)
    data_domain: Mapped[str] = mapped_column(String(64), nullable=False, default="cost_info", index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="history_backfill", index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    start_period: Mapped[str | None] = mapped_column(String(7), index=True)
    end_period: Mapped[str | None] = mapped_column(String(7), index=True)
    as_of_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parser_version: Mapped[str | None] = mapped_column(String(128))
    config_digest: Mapped[str | None] = mapped_column(String(80))
    item_limit: Mapped[int | None] = mapped_column(Integer)
    discovered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(128))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class CrawlItem(Base):
    """Durable inventory of announcements encountered by a campaign."""

    __tablename__ = "crawl_item"
    __table_args__ = (
        UniqueConstraint("campaign_id", "source_item_key", name="uq_crawl_item_campaign_source_key"),
        Index("ix_crawl_item_campaign_status", "campaign_id", "status"),
    )

    item_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("crawl_campaign.campaign_id"), nullable=False, index=True
    )
    source_id: Mapped[str] = mapped_column(String(36), ForeignKey("data_source.source_id"), nullable=False, index=True)
    source_item_key: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    detail_url: Mapped[str | None] = mapped_column(Text)
    publish_date: Mapped[str | None] = mapped_column(String(32), index=True)
    period_start: Mapped[str | None] = mapped_column(String(7), index=True)
    attachment_fingerprint: Mapped[str | None] = mapped_column(String(80), index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="discovered", index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("collection_task.task_id"), index=True)
    last_error: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Blob(Base):
    __tablename__ = "blob"
    __table_args__ = (
        UniqueConstraint("blob_hash", name="uq_blob_hash"),
        UniqueConstraint("blob_storage_key", name="uq_blob_storage_key"),
    )

    blob_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    blob_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    storage_bucket: Mapped[str] = mapped_column(String(128), nullable=False)
    blob_storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(128))
    etag: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    file_assets: Mapped[list["FileAsset"]] = relationship(
        back_populates="blob",
        foreign_keys="FileAsset.blob_hash",
    )


class FileAsset(Base):
    __tablename__ = "file_asset"
    __table_args__ = (
        UniqueConstraint("tenant_code", "sha256", name="uq_file_asset_tenant_sha256"),
    )

    file_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    bucket: Mapped[str] = mapped_column(String(128), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False, default="v1")
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    blob_hash: Mapped[str | None] = mapped_column(String(64), ForeignKey("blob.blob_hash"), index=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_ext: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    mime_type: Mapped[str | None] = mapped_column(String(128))
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    etag: Mapped[str | None] = mapped_column(String(128))
    parent_file_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("file_asset.file_id"),
        nullable=True,
        index=True,
    )
    lifecycle: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    blob: Mapped["Blob | None"] = relationship(back_populates="file_assets", foreign_keys=[blob_hash])
    parent: Mapped["FileAsset | None"] = relationship(remote_side=[file_id])
    ingest_events: Mapped[list["IngestEvent"]] = relationship(back_populates="file_asset")
    processing_tasks: Mapped[list["FileProcessing"]] = relationship(back_populates="file_asset")
    relations: Mapped[list["FileRelation"]] = relationship(back_populates="file_asset")


class IngestEvent(Base):
    __tablename__ = "ingest_event"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    file_id: Mapped[str] = mapped_column(String(36), ForeignKey("file_asset.file_id"), nullable=False, index=True)
    source_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("data_source.source_id"), index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("collection_task.task_id"), index=True)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    batch_id: Mapped[str | None] = mapped_column(String(128), index=True)
    source_url: Mapped[str | None] = mapped_column(Text)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_item_key: Mapped[str | None] = mapped_column(Text)
    source_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_metadata: Mapped[dict | None] = mapped_column("metadata", JSON)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    file_asset: Mapped[FileAsset] = relationship(back_populates="ingest_events")
    data_source: Mapped[DataSource | None] = relationship(back_populates="ingest_events")
    collection_task: Mapped[CollectionTask | None] = relationship(back_populates="ingest_events")


class FileProcessing(Base):
    __tablename__ = "file_processing"

    processing_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    file_id: Mapped[str] = mapped_column(String(36), ForeignKey("file_asset.file_id"), nullable=False, index=True)
    processor: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_bucket: Mapped[str | None] = mapped_column(String(128))
    output_key: Mapped[str | None] = mapped_column(String(512))
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    file_asset: Mapped[FileAsset] = relationship(back_populates="processing_tasks")


class FileRelation(Base):
    __tablename__ = "file_relation"
    __table_args__ = (
        UniqueConstraint("file_id", "rel_type", "rel_id", name="uq_file_relation_target"),
    )

    relation_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    file_id: Mapped[str] = mapped_column(String(36), ForeignKey("file_asset.file_id"), nullable=False, index=True)
    rel_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    rel_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    file_asset: Mapped[FileAsset] = relationship(back_populates="relations")


class Archive(Base):
    __tablename__ = "archive"
    __table_args__ = (
        CheckConstraint(
            "domain_type in ('cost_info', 'trading', 'policy_regulation', 'standard_atlas', 'quota')",
            name="ck_archive_domain_type",
        ),
        CheckConstraint(
            "channel_type in ('crawler', 'netdisk', 'system_api', 'manual_upload', 'batch_import', 'email_import')",
            name="ck_archive_channel_type",
        ),
        CheckConstraint(
            "collection_method in ('auto', 'manual_recovery', 'manual_denovo', 'legacy_batch_import')",
            name="ck_archive_collection_method",
        ),
        CheckConstraint(
            "price_kind in ('guidance', 'market_reference', 'manufacturer_reference', 'unspecified')",
            name="ck_archive_price_kind",
        ),
        CheckConstraint(
            "period_kind in ('monthly', 'issue_based', 'bimonthly')",
            name="ck_archive_period_kind",
        ),
        CheckConstraint(
            "visibility_scope in ('public', 'tenant', 'private')",
            name="ck_archive_visibility_scope",
        ),
        CheckConstraint(
            "status in ("
            "'discovered', 'collecting', 'collect_failed', 'quarantined', "
            "'collected', 'pending_tag', 'archived', 'ready_for_governance'"
            ")",
            name="ck_archive_status",
        ),
        UniqueConstraint(
            "tenant_code",
            "domain_type",
            "business_key",
            "version",
            name="uq_archive_business_version",
        ),
    )

    archive_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    domain_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    collection_method: Mapped[str] = mapped_column(String(32), nullable=False, default="auto", index=True)
    price_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="unspecified", index=True)
    period_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="monthly", index=True)
    business_key: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    region_code: Mapped[str | None] = mapped_column(String(32), index=True)
    publish_date: Mapped[date | None] = mapped_column(Date, index=True)
    # Denormalised coverage cell key (populated from metadata at write time; backfilled).
    # Promotes the matrix's read-time normalisation to first-class columns so the
    # v_coverage_gap view can classify cells in SQL and status-writeback is O(index).
    coverage_region_code: Mapped[str | None] = mapped_column(String(32), index=True)
    coverage_period: Mapped[str | None] = mapped_column(String(7), index=True)
    source_id: Mapped[str] = mapped_column(String(36), ForeignKey("data_source.source_id"), nullable=False, index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("collection_task.task_id"), index=True)
    batch_id: Mapped[str | None] = mapped_column(String(128), index=True)
    source_item_key: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    tenant_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    visibility_scope: Mapped[str] = mapped_column(String(32), nullable=False, default="public", index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending_tag", index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_withdrawn: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    metadata_payload: Mapped[dict] = mapped_column("metadata", archive_json_type(), nullable=False, default=dict)
    metadata_schema_version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1")
    field_sources: Mapped[dict] = mapped_column(archive_json_type(), nullable=False, default=dict)
    preview_status: Mapped[str] = mapped_column(String(32), nullable=False, default="none", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    # --- quota_parser integration (v0.3, 2026-07-28) ---
    # 见 quota/INTEGRATION_PLAN.md §2.2：parse_status 独立于 status（避免污染 cost_info 域
    # ck_archive_status CheckConstraint）。quota 档案解析子状态机用 parse_status 字段，
    # 状态枚举：`pending / parsing / parsed / qa_passed / usable / failed_user /
    # failed_permanent / transient`（无 CheckConstraint，自由写入，由 adapter 层校验）。
    # 其余 12 列与 Manifest（quota-parser-result/v1）字段一一对齐（web-frontend SPEC §6.1）。
    # parse_* 13 字段（合并 main 类型语义 + info 字段精度）
    # 项 1：CheckConstraint 保留 main 动态 sql_in_clause(28 role)
    # 项 2：字段长度采用 info 版本（String(64)/Text/显式 nullable=True），向上兼容
    # 项 3：parse_warnings 保留 main 的 list（语义清晰：警告数组）
    parse_status: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    parse_profile: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parse_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parse_phase: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parse_parser_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    parse_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parse_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    parse_metrics: Mapped[dict | None] = mapped_column(archive_json_type(), nullable=True)
    parse_warnings: Mapped[list | None] = mapped_column(archive_json_type(), nullable=True)
    parse_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    parse_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    candidate_xlsx_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_xlsx_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    files: Mapped[list["ArchiveFile"]] = relationship(back_populates="archive", cascade="all, delete-orphan")
    data_source: Mapped[DataSource] = relationship()
    collection_task: Mapped[CollectionTask | None] = relationship()


class ArchiveFile(Base):
    __tablename__ = "archive_file"
    __table_args__ = (
        # v0.7 fix: 改成动态 sql_in_clause, 自动跟随 archive_rules.ARCHIVE_FILE_ROLES.
        # 历史硬编码 24 个 role 漏了 v0.5 新增的 4 个 parse_* (parse_markdown/parse_html/
        # parse_candidate_xlsx/parse_final_xlsx), 导致 quota_parser worker 的
        # register_parse_artifact 写入 parse_markdown 等产物时 CheckViolation.
        # 现在所有 28 个 role 单一来源 = ARCHIVE_FILE_ROLES, 不会再分叉.
        CheckConstraint(
            sql_in_clause("file_role", ARCHIVE_FILE_ROLES),
            name="ck_archive_file_role",
        ),
        CheckConstraint("representation_role in ('primary', 'secondary')", name="ck_archive_representation_role"),
        CheckConstraint(sql_in_clause("link_source", LINK_SOURCES), name="ck_archive_file_link_source"),
        UniqueConstraint("archive_id", "file_id", "file_role", "page_range", name="uq_archive_file_role"),
        # NOTE: SPEC-QA-001 revision #5 asks for a "one primary file per archive"
        # guard. It is NOT enforced as a global DB constraint here because existing
        # cost_info archives legitimately carry multiple is_primary=true rows
        # (multi-Excel representations via _apply_representation_roles). A global
        # partial unique index would break those domains (§13.1). The invariant is
        # therefore enforced quota-scoped at the application layer in P0-4.
    )

    archive_file_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    archive_id: Mapped[str] = mapped_column(String(36), ForeignKey("archive.archive_id"), nullable=False, index=True)
    file_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("file_asset.file_id"), nullable=True, index=True)
    file_role: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    fetch_status: Mapped[str] = mapped_column(String(32), nullable=False, default="FETCHED", index=True)
    representation_role: Mapped[str] = mapped_column(String(32), nullable=False, default="primary", index=True)
    item_id: Mapped[str | None] = mapped_column(String(64), index=True)
    display_name: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    metadata_payload: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    # SPEC-QA-001 §6.5: page_range NOT NULL DEFAULT '' so it can safely join the
    # uniqueness key (NULLs would let duplicates slip past on PG/SQLite).
    page_range: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    link_source: Mapped[str] = mapped_column(String(32), nullable=False, default="import", index=True)
    linked_by: Mapped[str | None] = mapped_column(String(128))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    archive: Mapped[Archive] = relationship(back_populates="files")
    file_asset: Mapped[FileAsset] = relationship()


class ArchiveEvent(Base):
    __tablename__ = "archive_event"
    __table_args__ = (
        CheckConstraint(
            "event_type in ("
            "'ARCHIVE_CREATED', 'ARCHIVE_FILE_ATTACHED', 'ARCHIVE_TAGGED', "
            "'ARCHIVE_ARCHIVED', 'ARCHIVE_WITHDRAWN', 'ARCHIVE_QUARANTINED', "
            "'ARCHIVE_READY_FOR_GOVERNANCE', 'ADD_REPRESENTATION'"
            ")",
            name="ck_archive_event_type",
        ),
    )

    event_id: Mapped[int] = mapped_column(ordered_id_type(), primary_key=True, autoincrement=True)
    archive_id: Mapped[str] = mapped_column(String(36), ForeignKey("archive.archive_id"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_type: Mapped[str | None] = mapped_column(String(32), index=True)
    actor_id: Mapped[str | None] = mapped_column(String(128), index=True)
    before_payload: Mapped[dict | None] = mapped_column(JSON)
    after_payload: Mapped[dict | None] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    archive: Mapped[Archive] = relationship()


# ── v0.5 新增（INTEGRATION_PLAN §1.2.2 / DB_SCHEMA.md §3.9） ──
# 解析任务队列表 — web 端点 INSERT,worker 进程 SELECT ... FOR UPDATE SKIP LOCKED 抢单
# sweeper 扫 last_heartbeat_at 标记超时 job
class QuotaParseJob(Base):
    __tablename__ = "quota_parse_job"
    __table_args__ = (
        CheckConstraint(
            "status in ('queued','running','done','failed','cancelled')",
            name="ck_quota_parse_job_status",
        ),
        CheckConstraint(
            "profile in ('sichuan','chongqing')",
            name="ck_quota_parse_job_profile",
        ),
        CheckConstraint(
            "attempt >= 0 AND attempt <= max_attempts",
            name="ck_quota_parse_job_attempt",
        ),
    )

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    archive_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("archive.archive_id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    profile: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", index=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    worker_pid: Mapped[int | None] = mapped_column(Integer)
    worker_hostname: Mapped[str | None] = mapped_column(String(128))
    enqueued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # v0.5 新增（sweeper / 进度条）：
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    chunks_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunks_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # end
    error_code: Mapped[str | None] = mapped_column(String(32))
    error_message: Mapped[str | None] = mapped_column(Text)
    parse_task_id: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[str | None] = mapped_column(String(128))
    metadata_payload: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)

    archive: Mapped["Archive"] = relationship()


class Outbox(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_outbox_idempotency_key"),
        CheckConstraint("status in ('pending', 'acked', 'dead')", name="ck_outbox_status"),
    )

    outbox_id: Mapped[int] = mapped_column(ordered_id_type(), primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ordered_id_type(), ForeignKey("archive_event.event_id"), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    event: Mapped[ArchiveEvent] = relationship()


class AuditLog(Base):
    __tablename__ = "audit_log"

    audit_id: Mapped[int] = mapped_column(ordered_id_type(), primary_key=True, autoincrement=True)
    actor_type: Mapped[str | None] = mapped_column(String(32), index=True)
    actor_id: Mapped[str | None] = mapped_column(String(128), index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    error_code: Mapped[str | None] = mapped_column(String(64), index=True)
    before_payload: Mapped[dict | None] = mapped_column(JSON)
    after_payload: Mapped[dict | None] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class CrawlLineage(Base):
    __tablename__ = "crawl_lineage"

    lineage_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    archive_id: Mapped[str] = mapped_column(String(36), ForeignKey("archive.archive_id"), nullable=False, index=True)
    source_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("data_source.source_id"), index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("collection_task.task_id"), index=True)
    batch_id: Mapped[str | None] = mapped_column(String(128), index=True)
    source_item_key: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    parser_version: Mapped[str | None] = mapped_column(String(128), index=True)
    source_metadata: Mapped[dict | None] = mapped_column(JSON)
    discovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    archive: Mapped[Archive] = relationship()
    data_source: Mapped[DataSource | None] = relationship()
    collection_task: Mapped[CollectionTask | None] = relationship()


class CityPeriodScheme(Base):
    """Per-region publication rhythm (feature 3 · 月度节拍).

    One row per (region_code, domain_type). ``expected_publish_day`` is the day-of-month
    the period is expected to land; it drives monthly task generation and the overdue
    webhook. Initial values are data-inferred then folded into the D-③ manual verification
    checklist (``verified`` flips to true once a human confirms).
    """

    __tablename__ = "city_period_scheme"
    __table_args__ = (
        UniqueConstraint("region_code", "domain_type", name="uq_city_period_scheme_region_domain"),
        CheckConstraint(
            "expected_publish_day is null or (expected_publish_day between 1 and 31)",
            name="ck_city_period_scheme_publish_day",
        ),
        CheckConstraint("cadence in ('monthly', 'bimonthly')", name="ck_city_period_scheme_cadence"),
    )

    scheme_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    region_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    domain_type: Mapped[str] = mapped_column(String(64), nullable=False, default="cost_info", index=True)
    expected_publish_day: Mapped[int | None] = mapped_column(Integer)
    cadence: Mapped[str] = mapped_column(String(16), nullable=False, default="monthly")
    period_kind: Mapped[str | None] = mapped_column(String(16))
    source_kind: Mapped[str | None] = mapped_column(String(32))
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    inferred_from: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class QuotaPublicationSet(Base):
    """资料体系 / Publication Set (SPEC-QA-001 §6.3)."""

    __tablename__ = "quota_publication_set"
    __table_args__ = (
        UniqueConstraint("biz_key", name="uq_quota_publication_set_biz_key"),
        CheckConstraint(sql_in_clause("material_type", MATERIAL_TYPES), name="ck_quota_publication_set_material_type"),
        CheckConstraint(
            "quota_system_type is null or " + sql_in_clause("quota_system_type", QUOTA_SYSTEM_TYPES),
            name="ck_quota_publication_set_system_type",
        ),
        CheckConstraint(
            sql_in_clause("jurisdiction_level", JURISDICTION_LEVELS),
            name="ck_quota_publication_set_jurisdiction_level",
        ),
        CheckConstraint(sql_in_clause("legal_status", LEGAL_STATUSES), name="ck_quota_publication_set_legal_status"),
        CheckConstraint(
            sql_in_clause("metadata_status", QUOTA_METADATA_STATUSES),
            name="ck_quota_publication_set_metadata_status",
        ),
        CheckConstraint(
            "expected_volume_count is null or expected_volume_count >= 0",
            name="ck_quota_publication_set_expected_volume",
        ),
        CheckConstraint(
            "quota_system_type <> 'industry_specialty' or industry_sector_code is not null",
            name="ck_quota_publication_set_specialty_requires_sector",
        ),
        CheckConstraint(
            "quota_system_type <> 'construction_regional' or industry_sector_code is null",
            name="ck_quota_publication_set_regional_no_sector",
        ),
        Index(
            "ix_quota_publication_set_sys_juris_year",
            "quota_system_type",
            "jurisdiction_code",
            "edition_year",
        ),
        Index(
            "ix_quota_publication_set_sys_sector_year",
            "quota_system_type",
            "industry_sector_code",
            "edition_year",
        ),
    )

    publication_set_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    biz_key: Mapped[str] = mapped_column(Text, nullable=False)
    publication_family_code: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    material_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    quota_system_type: Mapped[str | None] = mapped_column(String(32), index=True)
    # v0.9 (2026-08-18): book_category 是 PG 生成列, 自动从 material_type + quota_system_type 派生。
    # 单一字段表达 3 桶分类: boq_standard / construction_quota / industry_quota (其他 material_type 留 NULL).
    # SQL: 见 scripts/migration_2026_08_18_book_category.sql
    book_category: Mapped[str | None] = mapped_column(
        String(32),
        Computed(
            "CASE "
            "WHEN material_type = 'boq_standard' THEN 'boq_standard' "
            "WHEN quota_system_type = 'construction_regional' THEN 'construction_quota' "
            "WHEN quota_system_type = 'industry_specialty' THEN 'industry_quota' "
            "ELSE NULL "
            "END",
            persisted=True,
        ),
        index=True,
    )
    jurisdiction_level: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    jurisdiction_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    industry_sector_code: Mapped[str | None] = mapped_column(String(64), index=True)
    issuer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    standard_or_quota_code: Mapped[str | None] = mapped_column(String(128))
    edition_label: Mapped[str] = mapped_column(String(64), nullable=False)
    edition_year: Mapped[int | None] = mapped_column(Integer, index=True)
    publish_date: Mapped[date | None] = mapped_column(Date)
    effective_date: Mapped[date | None] = mapped_column(Date)
    repeal_date: Mapped[date | None] = mapped_column(Date)
    legal_status: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown", index=True)
    expected_volume_count: Mapped[int | None] = mapped_column(Integer)
    metadata_status: Mapped[str] = mapped_column(String(16), nullable=False, default="missing", index=True)
    tenant_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    visibility_scope: Mapped[str] = mapped_column(String(32), nullable=False, default="public", index=True)
    created_by: Mapped[str | None] = mapped_column(String(128))
    updated_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class QuotaArchiveProfile(Base):
    """清单定额档案扩展 / Quota Archive Profile (SPEC-QA-001 §6.4), 1:1 with Archive."""

    __tablename__ = "quota_archive_profile"
    __table_args__ = (
        CheckConstraint(sql_in_clause("document_role", DOCUMENT_ROLES), name="ck_quota_archive_profile_document_role"),
        CheckConstraint(
            sql_in_clause("metadata_status", QUOTA_METADATA_STATUSES),
            name="ck_quota_archive_profile_metadata_status",
        ),
        CheckConstraint("completeness_score between 0 and 100", name="ck_quota_archive_profile_completeness"),
        Index("ix_quota_archive_profile_set_discipline", "publication_set_id", "discipline_code"),
    )

    archive_id: Mapped[str] = mapped_column(String(36), ForeignKey("archive.archive_id"), primary_key=True)
    publication_set_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("quota_publication_set.publication_set_id"), nullable=False, index=True
    )
    document_role: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    discipline_code: Mapped[str | None] = mapped_column(String(64), index=True)
    volume_code: Mapped[str | None] = mapped_column(String(64))
    volume_title: Mapped[str | None] = mapped_column(Text)
    part_no: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="zh-CN")
    metadata_status: Mapped[str] = mapped_column(String(16), nullable=False, default="missing", index=True)
    completeness_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completeness_blockers: Mapped[list] = mapped_column(archive_json_type(), nullable=False, default=list)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    archive: Mapped["Archive"] = relationship()
    publication_set: Mapped["QuotaPublicationSet"] = relationship()


class QuotaProjectionCandidate(Base):
    """待归档候选 / Projection Candidate (SPEC-QA-001 §6.6)."""

    __tablename__ = "quota_projection_candidate"
    __table_args__ = (
        UniqueConstraint("file_id", name="uq_quota_projection_candidate_file"),
        CheckConstraint(sql_in_clause("projection_status", PROJECTION_STATUSES), name="ck_quota_projection_candidate_status"),
        CheckConstraint(
            "suggestion_confidence is null or (suggestion_confidence >= 0 and suggestion_confidence <= 1)",
            name="ck_quota_projection_candidate_confidence",
        ),
    )

    candidate_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    file_id: Mapped[str] = mapped_column(String(36), ForeignKey("file_asset.file_id"), nullable=False)
    projection_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    suggested_publication_set_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("quota_publication_set.publication_set_id"), index=True
    )
    suggested_archive_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("archive.archive_id"), index=True)
    suggested_metadata: Mapped[dict] = mapped_column(archive_json_type(), nullable=False, default=dict)
    suggestion_confidence: Mapped[float | None] = mapped_column(Float)
    matched_rules: Mapped[list] = mapped_column(archive_json_type(), nullable=False, default=list)
    duplicate_of_file_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("file_asset.file_id"), index=True)
    resolved_archive_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("archive.archive_id"), index=True)
    resolution_note: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[str | None] = mapped_column(String(128))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class QuotaPublicationRelation(Base):
    """资料体系关系 / Publication Relation (SPEC-QA-001 §6.7)."""

    __tablename__ = "quota_publication_relation"
    __table_args__ = (
        CheckConstraint(sql_in_clause("relation_type", PUBLICATION_RELATION_TYPES), name="ck_quota_publication_relation_type"),
        CheckConstraint(
            "source_publication_set_id <> target_publication_set_id",
            name="ck_quota_publication_relation_not_self",
        ),
        UniqueConstraint(
            "source_publication_set_id",
            "target_publication_set_id",
            "relation_type",
            name="uq_quota_publication_relation_triple",
        ),
    )

    relation_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_publication_set_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("quota_publication_set.publication_set_id"), nullable=False, index=True
    )
    target_publication_set_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("quota_publication_set.publication_set_id"), nullable=False, index=True
    )
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    evidence: Mapped[str | None] = mapped_column(Text)
    source_ref: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class QuotaDictionary(Base):
    """受控字典 / Controlled Dictionary (SPEC-QA-001 §7 · D1). Open vocab."""

    __tablename__ = "quota_dictionary"
    __table_args__ = (
        UniqueConstraint("dict_type", "code", name="uq_quota_dictionary_type_code"),
    )

    dict_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dict_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class AdministrativeDivision(Base):
    """公共行政区划 / Administrative Divisions (GB/T 2260).

    Layer-0 shared dictionary; used by quota, cost_info, trading and other domains.
    """

    __tablename__ = "administrative_division"

    code: Mapped[str] = mapped_column(String(6), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    parent_code: Mapped[str] = mapped_column(
        String(6), ForeignKey("administrative_division.code"), nullable=True, index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="GB/T2260-2024")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
