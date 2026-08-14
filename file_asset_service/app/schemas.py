from datetime import datetime

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
    # ===== 信息价 PDF → xlsx 解析（2026-08-10 apply 自 info：parse_* 字段提到基类）=====
    # 主端点 /api/archives（main.py:1162）走 response_model=list[ArchiveSummaryResponse],
    # 不在 schema 里的字段会被 Pydantic 过滤掉。cost_info 域 parse_status 永远 None,
    # 字段冗余但无害。parse_supported 决定前端 ▶️ 解析按钮是否启用。
    # parse_warnings 类型：list（与 models.py / archive_service.py 项 3 决策一致）
    parse_supported: bool = False
    parse_status: str | None = None
    parse_task_id: str | None = None
    parse_profile: str | None = None
    parse_phase: str | None = None
    parse_parser_version: str | None = None
    parse_error_code: str | None = None
    parse_error_message: str | None = None
    parse_metrics: dict | None = None
    parse_warnings: list = []
    parse_started_at: str | None = None
    parse_finished_at: str | None = None
    candidate_xlsx_key: str | None = None
    final_xlsx_key: str | None = None


class ArchiveSummaryResponse(ArchiveResponse):
    file_count: int
    priced_source_count: int
    primary_file: ArchivePrimaryFileResponse | None = None
    # parse_* 字段已统一在 ArchiveResponse 基类声明（apply 自 info）
    # 2026-08-10 改：把 parse_* 字段从 quota 域子类提到通用 ArchiveResponse 基类，
    # 避免 ArchiveSummaryResponse / ArchiveDetailResponse 重复声明，且与
    # _serialize_archive_base (archive_service.py) 透出字段保持一致。


class ArchiveDetailResponse(ArchiveResponse):
    files: list[ArchiveFileResponse]
    # parse_* 字段已统一在 ArchiveResponse 基类声明（apply 自 info）
    # v0.8: 详情页需要 parse_status / parse_warnings / parse_error_message / parse_parser_version,
    # _serialize_archive_base 已写入,基类统一声明后所有子类自动继承,无需重复。


class ArchiveFromIngestEventResponse(ArchiveDetailResponse):
    attached_to_existing: bool = False


# ===== 信息价 PDF → xlsx 解析（2026-08-10 apply 自 info：6 个新 Pydantic 类）=====


class ParseSubmitRequest(BaseModel):
    """POST /api/archives/{id}/parse 请求体。

    year/period 通常后端从 PDF 文件名自动推断,前端可手动覆盖。
    city 默认走 detect_city(filename → title → region_code) 三级 fallback,
    不接受前端传入（前端只准覆盖年份+期数）。
    """

    year: int
    period: str


class ParseSubmitResponse(BaseModel):
    """POST /api/archives/{id}/parse 响应体。"""

    task_id: str
    archive_id: str
    parse_status: str  # queued / running / succeeded / failed / cancelled
    estimated_duration_seconds: str  # 经验值,前端展示 "预计 ~30s"


class ParseCancelResponse(BaseModel):
    """POST /api/archives/{id}/parse-cancel 响应体。

    cancelled = True 表示已发终止信号(进程正在收拾),并非真正"已终止"。
    already_terminal = True 表示任务已完成(succeeded/failed/cancelled),无需再 cancel。
    """

    cancelled: bool
    already_terminal: bool


class ParseDeleteResponse(BaseModel):
    """DELETE /api/archives/{id}/parse-output 响应体。"""

    deleted: bool
    archive_id: str


class ParseOutputResponse(BaseModel):
    """GET /api/archives/{id}/parse-output 响应体(presigned URL,7天有效)。"""

    bucket: str
    object_key: str
    download_url: str
    expires_in: int
    parse_finished_at: datetime | None


class ParseStreamEvent(BaseModel):
    """GET /api/archives/{id}/parse-stream 的 SSE event 帧。

    客户端 EventSource 按 seq 增量追加到日志区,按 status 切换 UI 状态。
    dropped_lines > 0 时前端显示 '前 N 行已截断' 提示(缓冲区 5000 行封顶)。
    """

    seq: int
    status: str  # running / succeeded / failed / cancelled
    line: str | None = None
    final_xlsx_key: str | None = None
    parse_error_code: str | None = None
    parse_error_message: str | None = None
    row_count: int | None = None
    duration_seconds: float | None = None
    truncated: bool = False
    dropped_lines: int = 0
