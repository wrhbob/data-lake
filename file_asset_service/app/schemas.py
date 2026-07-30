from pydantic import BaseModel


class IngestExistingArchive(BaseModel):
    archive_id: str
    title: str


class IngestResponse(BaseModel):
    file_id: str
    ingest_event_id: str | None
    bucket: str
    object_key: str
    sha256: str
    duplicated: bool
    processing_ids: list[str]
    existing_archives: list[IngestExistingArchive] = []


class IngestEventResponse(BaseModel):
    event_id: str
    file_id: str
    tenant_code: str
    source_id: str | None = None
    task_id: str | None = None
    source_type: str
    batch_id: str | None
    source_url: str | None
    source_item_key: str | None = None
    source_modified_at: str | None = None
    source_metadata: dict | None = None
    original_name: str
    ingested_at: str


class ProcessingRunResponse(BaseModel):
    processing_id: str
    processor: str
    status: str
    created_file_ids: list[str]
    duplicated_file_ids: list[str]
    message: str | None = None


class DataSourceCreate(BaseModel):
    source_scope: str
    tenant_code: str | None = None
    managed_by: str
    source_type: str
    connector_type: str
    name: str
    base_url: str | None = None
    url: str | None = None
    url_alt: str | None = None
    province: str | None = None
    city: str | None = None
    region_code: str | None = None
    data_domain: str
    auth_secret_ref: str | None = None
    format: str | None = None
    downloadable: bool | None = None
    bucket: str | None = None
    owner: str | None = None
    reviewer: str | None = None
    remark: str | None = None
    frequency: str | None = None
    config: dict | None = None
    schedule_policy: dict | None = None
    status: str | None = None
    created_by: str | None = None


class DataSourceResponse(BaseModel):
    source_id: str
    source_scope: str
    tenant_code: str | None
    asset_tenant_code: str
    managed_by: str
    source_type: str
    connector_type: str
    name: str
    base_url: str | None
    url: str | None
    url_alt: str | None
    province: str | None
    city: str | None
    region_code: str | None
    data_domain: str
    format: str | None
    downloadable: bool | None
    bucket: str | None
    owner: str | None
    reviewer: str | None
    remark: str | None
    frequency: str | None
    config: dict
    schedule_policy: dict | None
    status: str


class CollectionTaskCreate(BaseModel):
    source_id: str
    operator_type: str
    operator_id: str | None = None
    task_type: str
    trigger_type: str
    data_domain: str | None = None
    batch_id: str | None = None
    status: str | None = None
    period_raw: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    period_note: str | None = None
    config_override: dict | None = None
    created_by: str | None = None


class CollectionTaskResponse(BaseModel):
    task_id: str
    source_id: str
    asset_tenant_code: str
    operator_type: str
    operator_id: str | None
    task_type: str
    trigger_type: str
    batch_id: str
    data_domain: str
    status: str
    period_raw: str | None
    period_start: str | None
    period_end: str | None
    period_note: str | None
    worker_id: str | None = None
    lease_expires_at: str | None = None
    heartbeat_at: str | None = None
    config_override: dict | None
    new_file_count: int
    duplicate_file_count: int
    failed_count: int


class CrawlerSchedulerRunRequest(BaseModel):
    dry_run: bool = True
    force: bool = False
    source_id: str | None = None
    site_id: str | None = None
    trigger: str = "manual"


class CrawlerWorkerRunRequest(BaseModel):
    dry_run: bool = True
    limit: int = 10
    source_id: str | None = None
    site_id: str | None = None
    adapter_kind: str | None = None
    batch_id: str | None = None
    worker_id: str | None = None
    lease_seconds: int = 14400
    trigger: str = "manual"


class CrawlerCoverageBackfillRequest(BaseModel):
    region_code: str
    start_period: str
    end_period: str
    source_id: str | None = None
    dry_run: bool = True
    include_covered: bool = False


class CrawlerCampaignCreateRequest(BaseModel):
    source_id: str
    name: str
    start_period: str | None = None
    end_period: str | None = None
    item_limit: int | None = None
    mode: str = "history_backfill"


class CrawlerLoopRunRequest(BaseModel):
    dry_run: bool = True
    force: bool = False
    worker_limit: int = 20
    max_worker_cycles: int = 20
    worker_id: str | None = None
    lease_seconds: int = 14400
    trigger: str = "api_crawler_loop"


class TradingSchedulerRunRequest(BaseModel):
    dry_run: bool = True
    force: bool = False
    verify: bool = False
    source_id: str | None = None
    site_id: str | None = None
    channel_ids: list[str] | None = None
    max_pages: int | None = None
    page_size: int | None = None
    max_items_per_channel: int | None = None
    start_date: str | None = None
    end_date: str | None = None
    trigger: str = "manual"


class TradingWorkerRunRequest(BaseModel):
    dry_run: bool = True
    limit: int = 10
    source_id: str | None = None
    site_id: str | None = None
    worker_id: str | None = None
    lease_seconds: int = 14400
    trigger: str = "manual"


class TradingLoopRunRequest(TradingSchedulerRunRequest):
    worker_limit: int = 10
    max_worker_cycles: int = 10
    worker_id: str | None = None
    lease_seconds: int = 14400
    trigger: str = "api_trading_loop"


class ArchiveCreate(BaseModel):
    domain_type: str
    channel_type: str
    collection_method: str = "auto"
    price_kind: str = "unspecified"
    period_kind: str = "monthly"
    business_key: str
    title: str
    source_id: str
    tenant_code: str
    visibility_scope: str
    status: str = "pending_tag"
    region_code: str | None = None
    publish_date: str | None = None
    task_id: str | None = None
    batch_id: str | None = None
    source_item_key: str | None = None
    source_url: str | None = None
    metadata: dict | None = None
    field_sources: dict | None = None
    actor_type: str | None = None
    actor_id: str | None = None


class ArchivePatch(BaseModel):
    business_key: str | None = None
    price_kind: str | None = None
    period_kind: str | None = None
    title: str | None = None
    region_code: str | None = None
    publish_date: str | None = None
    visibility_scope: str | None = None
    status: str | None = None
    metadata: dict | None = None
    field_sources: dict | None = None
    actor_type: str | None = None
    actor_id: str | None = None


class ArchiveFileAttach(BaseModel):
    file_id: str
    file_role: str | None = None
    item_id: str | None = None
    display_name: str | None = None
    source_url: str | None = None
    is_primary: bool = False
    sort_order: int = 100
    metadata: dict | None = None
    actor_type: str | None = None
    actor_id: str | None = None


class ArchiveFromIngestEventCreate(BaseModel):
    event_id: str
    domain_type: str
    channel_type: str
    collection_method: str = "auto"
    price_kind: str = "unspecified"
    period_kind: str = "monthly"
    title: str
    visibility_scope: str = "public"
    status: str = "pending_tag"
    business_key: str | None = None
    region_code: str | None = None
    publish_date: str | None = None
    metadata: dict | None = None
    field_sources: dict | None = None
    actor_type: str | None = None
    actor_id: str | None = None


class ArchiveFileResponse(BaseModel):
    archive_file_id: str
    archive_id: str
    file_id: str | None = None
    file_role: str
    representation_role: str
    item_id: str | None = None
    display_name: str | None = None
    source_url: str | None = None
    is_primary: bool
    sort_order: int
    metadata: dict
    added_at: str | None = None
    file_name: str | None = None
    file_ext: str | None = None
    mime_type: str | None = None
    file_size: int | None = None
    bucket: str | None = None
    object_key: str | None = None
    mirror_status: str | None = None
    mirror_relative_path: str | None = None
    mirror_path: str | None = None
    mirror_size: int | None = None
    mirror_error: str | None = None
    mirror_checked_at: str | None = None


class ArchivePrimaryFileResponse(BaseModel):
    file_id: str | None = None
    file_name: str
    file_role: str
    mirror_status: str | None = None
    mirror_relative_path: str | None = None
    mirror_path: str | None = None
    mirror_size: int | None = None
    mirror_error: str | None = None
    mirror_checked_at: str | None = None


class ArchiveResponse(BaseModel):
    archive_id: str
    domain_type: str
    channel_type: str
    collection_method: str
    price_kind: str
    period_kind: str
    business_key: str
    title: str
    region_code: str | None = None
    publish_date: str | None = None
    source_id: str
    task_id: str | None = None
    batch_id: str | None = None
    source_item_key: str | None = None
    source_url: str | None = None
    tenant_code: str
    visibility_scope: str
    status: str
    version: int
    is_current: bool
    is_withdrawn: bool
    metadata: dict
    metadata_schema_version: str
    field_sources: dict
    preview_status: str
    created_at: str
    updated_at: str


class ArchiveSummaryResponse(ArchiveResponse):
    file_count: int
    priced_source_count: int
    primary_file: ArchivePrimaryFileResponse | None = None
    # v0.4 Bug#1 修：parse_* 字段从 quota 域扩到通用 ArchiveResponse。
    # 主端点 /api/archives（main.py:1162）走 response_model=list[ArchiveSummaryResponse],
    # 不在 schema 里的字段会被 Pydantic 过滤掉。cost_info 域 parse_status 永远 None,字段冗余但无害。
    parse_status: str | None = None
    parse_phase: str | None = None
    parse_task_id: str | None = None
    parse_started_at: str | None = None
    parse_finished_at: str | None = None
    parse_error_code: str | None = None
    candidate_xlsx_key: str | None = None
    final_xlsx_key: str | None = None


class ArchiveDetailResponse(ArchiveResponse):
    files: list[ArchiveFileResponse]


class ArchiveFromIngestEventResponse(ArchiveDetailResponse):
    attached_to_existing: bool = False
