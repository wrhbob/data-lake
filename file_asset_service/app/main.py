from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from io import BytesIO
import json
import os
from pathlib import Path
from typing import Annotated
from urllib.parse import quote
from zipfile import BadZipFile, ZipFile

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from app.archive_rules import build_quota_business_key
from app.archive_service import (
    _archive_summary_rows,
    attach_file as attach_archive_file,
    count_archives,
    create_archive,
    create_archive_from_ingest_event,
    create_archive_from_ingest_event_with_flag,
    delete_archive,
    get_archive_detail,
    list_archives,
    patch_archive,
    withdraw_archive,
)
from app.basic_auth import BasicAuthMiddleware
from app.assets import register_asset
from app.collection import create_collection_task, create_data_source, list_collection_tasks, list_data_sources
def _run_quota_filtered_listing(
    session: Session,
    *,
    domain_type: str | None,
    status: str | None,
    region_code: str | None,
    channel_type: str | None,
    tenant_code: str | None,
    source_id: str | None,
    search: str | None,
    primary: str | None,
    jurisdiction_code: str | None,
    industry_sector_code: str | None,
    edition_year: int | None,
    edition_label: str | None,
    discipline_code: str | None,
    limit: int,
    offset: int,
    mirror: FileMirror | None,
) -> tuple[list[dict[str, object]], int]:
    """Quota-domain listing with the 6 chip-side filters the generic endpoint
    was missing. Mirrors quota_api.list_quota_archives (quota_api.py:285) but
    routes results through _archive_summary_rows so primary_file is preserved
    for inline preview.

    Activation rule: any of the 6 quota params must be supplied AND domain_type
    must equal 'quota' (or be None — caller checks). Pure generic listings stay
    on the legacy code path so behavior is identical for non-quota callers.

    v0.9.2 (2026-08-18): primary 三类改用 book_category 单字段 (boq_standard /
      construction_quota / industry_quota) — 与 quota_api /facets /archives
      一致, 走 QuotaPublicationSet.book_category GENERATED 列。前端 quota-ui.js
      同步已 commit。历史值 construction_regional / industry_specialty 已废弃,
      此处不再回退支持。
    """
    base = (
        select(Archive, QuotaArchiveProfile, QuotaPublicationSet)
        .join(
            QuotaArchiveProfile,
            QuotaArchiveProfile.archive_id == Archive.archive_id,
            isouter=True,
        )
        .join(
            QuotaPublicationSet,
            QuotaPublicationSet.publication_set_id == QuotaArchiveProfile.publication_set_id,
            isouter=True,
        )
        .where(Archive.is_withdrawn.is_(False))
    )
    if domain_type:
        base = base.where(Archive.domain_type == domain_type)
    if status:
        base = base.where(Archive.status == status)
    if region_code:
        base = base.where(Archive.region_code == region_code)
    if channel_type:
        base = base.where(Archive.channel_type == channel_type)
    if tenant_code:
        base = base.where(Archive.tenant_code == tenant_code)
    if source_id:
        base = base.where(Archive.source_id == source_id)
    if search:
        pattern = f"%{search}%"
        base = base.where(
            or_(Archive.title.ilike(pattern), Archive.business_key.ilike(pattern))
        )

    # ── quota-specific chip filters ─────────────────────────────
    # v0.9.2 (2026-08-18): primary 改用 book_category 单字段 (QuotaPublicationSet.book_category
    #   是 GENERATED ALWAYS AS STORED 列, 见 scripts/migration_2026_08_18_book_category.sql),
    #   与 quota_api /facets /archives?primary= 同源, 不再走 quota_system_type/material_type 双层。
    if primary == "construction_quota":
        base = base.where(QuotaPublicationSet.book_category == "construction_quota")
    elif primary == "industry_quota":
        base = base.where(QuotaPublicationSet.book_category == "industry_quota")
    elif primary == "boq_standard":
        base = base.where(QuotaPublicationSet.book_category == "boq_standard")
    if jurisdiction_code:
        base = base.where(QuotaPublicationSet.jurisdiction_code == jurisdiction_code)
    if industry_sector_code:
        base = base.where(QuotaPublicationSet.industry_sector_code == industry_sector_code)
    if edition_year is not None:
        base = base.where(QuotaPublicationSet.edition_year == edition_year)
    if edition_label:
        base = base.where(QuotaPublicationSet.edition_label == edition_label)
    if discipline_code:
        base = base.where(QuotaArchiveProfile.discipline_code == discipline_code)

    # Count (mirror the same WHERE; do not order/offset/limit)
    count_stmt = select(func.count()).select_from(Archive).where(Archive.is_withdrawn.is_(False))
    if domain_type:
        count_stmt = count_stmt.where(Archive.domain_type == domain_type)
    if status:
        count_stmt = count_stmt.where(Archive.status == status)
    if region_code:
        count_stmt = count_stmt.where(Archive.region_code == region_code)
    if channel_type:
        count_stmt = count_stmt.where(Archive.channel_type == channel_type)
    if tenant_code:
        count_stmt = count_stmt.where(Archive.tenant_code == tenant_code)
    if source_id:
        count_stmt = count_stmt.where(Archive.source_id == source_id)
    if search:
        pattern = f"%{search}%"
        count_stmt = count_stmt.where(
            or_(Archive.title.ilike(pattern), Archive.business_key.ilike(pattern))
        )
    # quota filter contributions need a join to be meaningful for the count
    if primary or jurisdiction_code or industry_sector_code or edition_year is not None or edition_label:
        count_stmt = count_stmt.join(
            QuotaArchiveProfile,
            QuotaArchiveProfile.archive_id == Archive.archive_id,
            isouter=True,
        ).join(
            QuotaPublicationSet,
            QuotaPublicationSet.publication_set_id == QuotaArchiveProfile.publication_set_id,
            isouter=True,
        )
        # v0.9.2: 同 base 分支, 用 book_category 单字段 (与 quota_api 保持一致)。
        if primary == "construction_quota":
            count_stmt = count_stmt.where(QuotaPublicationSet.book_category == "construction_quota")
        elif primary == "industry_quota":
            count_stmt = count_stmt.where(QuotaPublicationSet.book_category == "industry_quota")
        elif primary == "boq_standard":
            count_stmt = count_stmt.where(QuotaPublicationSet.book_category == "boq_standard")
        if jurisdiction_code:
            count_stmt = count_stmt.where(QuotaPublicationSet.jurisdiction_code == jurisdiction_code)
        if industry_sector_code:
            count_stmt = count_stmt.where(QuotaPublicationSet.industry_sector_code == industry_sector_code)
        if edition_year is not None:
            count_stmt = count_stmt.where(QuotaPublicationSet.edition_year == edition_year)
        if edition_label:
            count_stmt = count_stmt.where(QuotaPublicationSet.edition_label == edition_label)
    if discipline_code:
        # needs the profile join if not already present
        if not (primary or jurisdiction_code or industry_sector_code or edition_year is not None or edition_label):
            count_stmt = count_stmt.join(
                QuotaArchiveProfile,
                QuotaArchiveProfile.archive_id == Archive.archive_id,
                isouter=True,
            )
        count_stmt = count_stmt.where(QuotaArchiveProfile.discipline_code == discipline_code)

    total_count = int(session.scalar(count_stmt) or 0)

    # Paginated, ordered list
    capped_limit = max(1, min(limit, 500))
    safe_offset = max(0, offset)
    paginated = (
        base.order_by(
            Archive.publish_date.desc().nullslast(),
            Archive.created_at.desc(),
            Archive.archive_id.desc(),
        )
        .offset(safe_offset)
        .limit(capped_limit)
    )
    rows = session.execute(paginated).all()
    archives = [r[0] for r in rows]
    items = _archive_summary_rows(session, archives, mirror=mirror)
    return items, total_count


from app.config import get_settings
from app.coverage_backfill import run_coverage_backfill
from app.crawl_campaign import campaign_to_dict, create_campaign, list_campaigns
from app.crawler_loop import run_loop_once
from app.cost_info_scheduler import run_scheduler
from app.cost_info_worker import run_worker
from app.crawler_dashboard import list_crawler_sources, list_crawler_tasks, list_parse_manifest_issues, summarize_parse_manifest
from app.trading_config_factories import restore_all_source_configs
from app.trading_source_registry import list_trading_sources
from app.trading_task_loop import run_trading_loop_once, run_trading_scheduler, run_trading_worker
from app.database import get_db_session, init_db
from app.file_mirror import FileMirror, FileMirrorRootUnconfigured, get_file_mirror
from app.file_preview import DOCX_EXTENSIONS, DOWNLOAD_ONLY_EXTENSIONS, EXCEL_EXTENSIONS, HTML_EXTENSIONS, PDF_EXTENSIONS
from app.file_preview import read_zip_image_preview_entry, zip_image_preview_entries
from app.file_preview import render_docx_preview_html, render_excel_preview_html, render_excel_preview_multi_sheet_html, render_html_notice_preview_html
from app.info_price_coverage import build_coverage_matrix
from app.quota_coverage import build_quota_coverage_matrix
from app.info_price_downstream import (
    build_audit_view,
    build_review_view,
    list_info_price_extracts,
    load_extract_payload,
)
from app.models import (
    Archive,
    ArchiveFile,
    CollectionTask,
    DataSource,
    FileAsset,
    FileProcessing,
    IngestEvent,
    QuotaArchiveProfile,
    QuotaPublicationSet,
)
from app.info_price_parse import (
    ParseError,
    cancel_parse_job,
    delete_xlsx_output,
    detect_city_from_filename,
    detect_city,
    read_xlsx_output,
    stream_parse_events,
    submit_parse_job,
)
from app.quota_api import router as quota_router
from app.quota_compare import router as quota_compare_router
from app.national_cost_info_regions import load_national_regions
from app.national_info_price_audit import build_audit_summary, load_source_audit_rows, validate_source_audit
from app.runner import run_processing_task
from app.schemas import (
    ArchiveCreate,
    ArchiveDetailResponse,
    ArchiveFromIngestEventResponse,
    ArchiveFileAttach,
    ArchiveFileResponse,
    ArchiveFromIngestEventCreate,
    ArchivePatch,
    ArchiveResponse,
    ArchiveSummaryResponse,
    CollectionTaskCreate,
    CollectionTaskResponse,
    CrawlerCampaignCreateRequest,
    CrawlerCoverageBackfillRequest,
    CrawlerLoopRunRequest,
    CrawlerSchedulerRunRequest,
    CrawlerWorkerRunRequest,
    DataSourceCreate,
    DataSourceResponse,
    IngestEventResponse,
    IngestResponse,
    ParseCancelResponse,
    ParseDeleteResponse,
    ParseOutputResponse,
    ParseSubmitRequest,
    ParseSubmitResponse,
    ProcessingRunResponse,
    TradingLoopRunRequest,
    TradingSchedulerRunRequest,
    TradingWorkerRunRequest,
)
from app.storage import ObjectStore, get_object_store
from app.storage_audit import audit_file_asset_objects


def _require_file_asset_for_preview(session: Session, file_id: str) -> FileAsset:
    asset = session.get(FileAsset, file_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="FILE_ASSET_NOT_FOUND")
    return asset


def _read_file_asset_bytes(storage: ObjectStore, asset: FileAsset) -> bytes:
    try:
        return storage.get_object(asset.bucket, asset.object_key)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="FILE_ASSET_OBJECT_NOT_FOUND") from exc


def _parse_http_byte_range(range_header: str | None, total_size: int) -> tuple[int, int] | None:
    if range_header is None:
        return None
    unit, separator, range_spec = range_header.strip().partition("=")
    if separator != "=" or unit.lower() != "bytes" or "," in range_spec:
        raise ValueError("unsupported byte range")
    start_text, separator, end_text = range_spec.partition("-")
    if separator != "-":
        raise ValueError("invalid byte range")
    start_text = start_text.strip()
    end_text = end_text.strip()
    if total_size <= 0 or (not start_text and not end_text):
        raise ValueError("empty byte range")

    if not start_text:
        if not end_text.isdigit():
            raise ValueError("invalid suffix byte range")
        suffix_length = int(end_text)
        if suffix_length <= 0:
            raise ValueError("invalid suffix byte range")
        return max(total_size - suffix_length, 0), total_size - 1

    if not start_text.isdigit() or (end_text and not end_text.isdigit()):
        raise ValueError("invalid byte range bounds")
    start = int(start_text)
    if start >= total_size:
        raise ValueError("unsatisfiable byte range")
    end = total_size - 1 if not end_text else min(int(end_text), total_size - 1)
    if end < start:
        raise ValueError("reversed byte range")
    return start, end


def _zip_entry_count(content: bytes) -> int:
    try:
        with ZipFile(BytesIO(content)) as archive:
            return len([info for info in archive.infolist() if not info.is_dir()])
    except BadZipFile as exc:
        raise HTTPException(status_code=415, detail="ZIP_PREVIEW_BAD_ZIP") from exc


def _zip_preview_allowed(session: Session, asset: FileAsset) -> bool:
    mounts = session.execute(
        select(ArchiveFile, Archive)
        .join(Archive, Archive.archive_id == ArchiveFile.archive_id)
        .where(ArchiveFile.file_id == asset.file_id)
    ).all()
    if not mounts:
        return True
    for mounted, archive in mounts:
        if mounted.file_role == "priced_source" or archive.domain_type == "trading":
            return False
    return True


def create_app(*, init_schema: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if init_schema and os.getenv("FILE_ASSET_SCHEMA_READY") != "1":
            init_db()
        yield

    app = FastAPI(title="KPI Cloud File Asset Service", version="0.1.0", lifespan=lifespan)

    _basic_auth = os.getenv("FILE_ASSET_BASIC_AUTH")
    if _basic_auth and ":" in _basic_auth:
        _ba_user, _ba_pass = _basic_auth.split(":", 1)
        app.add_middleware(BasicAuthMiddleware, username=_ba_user, password=_ba_pass)

    ui_dir = Path(__file__).parent / "ui"
    if ui_dir.exists():
        app.mount("/ui-assets", StaticFiles(directory=ui_dir), name="ui_assets")

    app.include_router(quota_router)
    app.include_router(quota_compare_router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ui", response_class=HTMLResponse)
    def info_price_collection_ui() -> HTMLResponse:
        index_path = ui_dir / "index.html"
        if not index_path.exists():
            raise HTTPException(status_code=404, detail="ui shell is not installed")
        return HTMLResponse(index_path.read_text(encoding="utf-8"))

    @app.get("/ui/", response_class=HTMLResponse)
    def info_price_collection_ui_slash() -> HTMLResponse:
        return info_price_collection_ui()

    @app.get("/review", response_class=HTMLResponse)
    def info_price_review_ui() -> HTMLResponse:
        index_path = ui_dir / "review.html"
        if not index_path.exists():
            raise HTTPException(status_code=404, detail="review ui shell is not installed")
        return HTMLResponse(index_path.read_text(encoding="utf-8"))

    @app.get("/review/", response_class=HTMLResponse)
    def info_price_review_ui_slash() -> HTMLResponse:
        return info_price_review_ui()

    @app.get("/audit", response_class=HTMLResponse)
    def info_price_audit_ui() -> HTMLResponse:
        index_path = ui_dir / "audit.html"
        if not index_path.exists():
            raise HTTPException(status_code=404, detail="audit ui shell is not installed")
        return HTMLResponse(index_path.read_text(encoding="utf-8"))

    @app.get("/audit/", response_class=HTMLResponse)
    def info_price_audit_ui_slash() -> HTMLResponse:
        return info_price_audit_ui()

    @app.get("/crawler", response_class=HTMLResponse)
    def crawler_console_ui() -> HTMLResponse:
        index_path = ui_dir / "crawler.html"
        if not index_path.exists():
            raise HTTPException(status_code=404, detail="crawler ui shell is not installed")
        return HTMLResponse(index_path.read_text(encoding="utf-8"), headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

    @app.get("/crawler/", response_class=HTMLResponse)
    def crawler_console_ui_slash() -> HTMLResponse:
        return crawler_console_ui()

    def serialize_data_source(source: DataSource) -> dict[str, object]:
        return {
            "source_id": source.source_id,
            "source_scope": source.source_scope,
            "tenant_code": source.tenant_code,
            "asset_tenant_code": source.asset_tenant_code,
            "managed_by": source.managed_by,
            "source_type": source.source_type,
            "connector_type": source.connector_type,
            "name": source.name,
            "base_url": source.base_url,
            "url": source.url or source.base_url,
            "url_alt": source.url_alt,
            "province": source.province,
            "city": source.city,
            "region_code": source.region_code,
            "data_domain": source.data_domain,
            "format": source.format,
            "downloadable": source.downloadable,
            "bucket": source.bucket,
            "owner": source.owner,
            "reviewer": source.reviewer,
            "remark": source.remark,
            "frequency": source.frequency,
            "config": source.config,
            "schedule_policy": source.schedule_policy,
            "status": source.status,
        }

    def serialize_collection_task(task: CollectionTask) -> dict[str, object]:
        return {
            "task_id": task.task_id,
            "source_id": task.source_id,
            "asset_tenant_code": task.asset_tenant_code,
            "operator_type": task.operator_type,
            "operator_id": task.operator_id,
            "task_type": task.task_type,
            "trigger_type": task.trigger_type,
            "batch_id": task.batch_id,
            "data_domain": task.data_domain,
            "status": task.status,
            "period_raw": task.period_raw,
            "period_start": task.period_start,
            "period_end": task.period_end,
            "period_note": task.period_note,
            "worker_id": task.worker_id,
            "lease_expires_at": task.lease_expires_at.isoformat() if task.lease_expires_at else None,
            "heartbeat_at": task.heartbeat_at.isoformat() if task.heartbeat_at else None,
            "config_override": task.config_override,
            "new_file_count": task.new_file_count,
            "duplicate_file_count": task.duplicate_file_count,
            "failed_count": task.failed_count,
        }

    def archive_http_error(exc: ValueError) -> HTTPException:
        detail = str(exc)
        if "NOT_FOUND" in detail:
            status_code = 404
        elif "ARCHIVE_BUSINESS_KEY_EXISTS" in detail:
            status_code = 409
        else:
            status_code = 400
        return HTTPException(status_code=status_code, detail=detail)

    def parse_form_datetime(value: str | None, field_name: str) -> datetime | None:
        if not value:
            return None
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = f"{normalized[:-1]}+00:00"
        try:
            return datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"{field_name} must be ISO-8601 datetime") from exc

    def parse_source_metadata(
        source_metadata: str | None,
        *,
        discovered_at: str | None = None,
        fetched_at: str | None = None,
    ) -> dict | None:
        metadata: dict = {}
        if source_metadata:
            try:
                parsed = json.loads(source_metadata)
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=400, detail="source_metadata must be a JSON object") from exc
            if not isinstance(parsed, dict):
                raise HTTPException(status_code=400, detail="source_metadata must be a JSON object")
            metadata.update(parsed)
        if discovered_at:
            metadata["discovered_at"] = discovered_at
        if fetched_at:
            metadata["fetched_at"] = fetched_at
        return metadata or None

    def serialize_archive_file_response(mounted) -> dict[str, object]:
        asset = mounted.file_asset
        return {
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
            "file_name": asset.file_name if asset else None,
            "file_ext": asset.file_ext if asset else None,
            "mime_type": asset.mime_type if asset else None,
            "file_size": asset.file_size if asset else None,
            "bucket": asset.bucket if asset else None,
            "object_key": asset.object_key if asset else None,
        }

    @app.post("/api/data-sources", response_model=DataSourceResponse)
    def create_data_source_endpoint(
        payload: DataSourceCreate,
        session: Session = Depends(get_db_session),
    ) -> dict[str, object]:
        source = create_data_source(session, **payload.model_dump())
        return serialize_data_source(source)

    @app.get("/api/data-sources", response_model=list[DataSourceResponse])
    def list_data_sources_endpoint(
        source_scope: str | None = None,
        tenant_code: str | None = None,
        source_type: str | None = None,
        status: str | None = None,
        session: Session = Depends(get_db_session),
    ) -> list[dict[str, object]]:
        return [
            serialize_data_source(source)
            for source in list_data_sources(
                session,
                source_scope=source_scope,
                tenant_code=tenant_code,
                source_type=source_type,
                status=status,
            )
        ]

    @app.post("/api/collection-tasks", response_model=CollectionTaskResponse)
    def create_collection_task_endpoint(
        payload: CollectionTaskCreate,
        session: Session = Depends(get_db_session),
    ) -> dict[str, object]:
        task = create_collection_task(session, **payload.model_dump())
        return serialize_collection_task(task)

    @app.get("/api/collection-tasks", response_model=list[CollectionTaskResponse])
    def list_collection_tasks_endpoint(
        source_id: str | None = None,
        asset_tenant_code: str | None = None,
        status: str | None = None,
        batch_id: str | None = None,
        session: Session = Depends(get_db_session),
    ) -> list[dict[str, object]]:
        return [
            serialize_collection_task(task)
            for task in list_collection_tasks(
                session,
                source_id=source_id,
                asset_tenant_code=asset_tenant_code,
                status=status,
                batch_id=batch_id,
            )
        ]

    @app.get("/api/crawler/sources")
    def list_crawler_sources_endpoint(
        session: Session = Depends(get_db_session),
    ) -> list[dict[str, object]]:
        return list_crawler_sources(session)

    @app.get("/api/crawler/tasks")
    def list_crawler_tasks_endpoint(
        source_id: str | None = None,
        status: str | None = None,
        limit: int = 80,
        session: Session = Depends(get_db_session),
    ) -> list[dict[str, object]]:
        return list_crawler_tasks(session, source_id=source_id, status=status, limit=limit)

    @app.get("/api/crawler/parse-manifest")
    def crawler_parse_manifest_endpoint() -> dict[str, object]:
        return summarize_parse_manifest(get_settings().parse_manifest_path)

    @app.get("/api/crawler/parse-manifest/issues")
    def crawler_parse_manifest_issues_endpoint(limit: int = 50) -> dict[str, object]:
        return list_parse_manifest_issues(get_settings().parse_manifest_path, limit=limit)

    @app.post("/api/crawler/scheduler/run")
    def run_crawler_scheduler_endpoint(
        payload: CrawlerSchedulerRunRequest,
        session: Session = Depends(get_db_session),
    ) -> dict[str, object]:
        try:
            return run_scheduler(
                session,
                trigger=payload.trigger,
                source_id=payload.source_id,
                site_id=payload.site_id,
                dry_run=payload.dry_run,
                force=payload.force,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/crawler/worker/run")
    def run_crawler_worker_endpoint(
        payload: CrawlerWorkerRunRequest,
        session: Session = Depends(get_db_session),
        storage: ObjectStore = Depends(get_object_store),
    ) -> dict[str, object]:
        try:
            report = run_worker(
                session,
                limit=max(1, min(payload.limit, 50)),
                source_id=payload.source_id,
                site_id=payload.site_id,
                adapter_kind=payload.adapter_kind,
                batch_id=payload.batch_id,
                dry_run=payload.dry_run,
                trigger=payload.trigger,
                worker_id=payload.worker_id,
                lease_seconds=payload.lease_seconds,
                storage=storage,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return report.to_dict()

    @app.post("/api/crawler/coverage-backfill")
    def run_crawler_coverage_backfill_endpoint(
        payload: CrawlerCoverageBackfillRequest,
        session: Session = Depends(get_db_session),
    ) -> dict[str, object]:
        try:
            return run_coverage_backfill(
                session,
                region_code=payload.region_code,
                start_period=payload.start_period,
                end_period=payload.end_period,
                source_id=payload.source_id,
                dry_run=payload.dry_run,
                include_covered=payload.include_covered,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/crawler/campaigns")
    def create_crawler_campaign_endpoint(
        payload: CrawlerCampaignCreateRequest,
        session: Session = Depends(get_db_session),
    ) -> dict[str, object]:
        try:
            campaign = create_campaign(
                session,
                source_id=payload.source_id,
                name=payload.name,
                start_period=payload.start_period,
                end_period=payload.end_period,
                item_limit=payload.item_limit,
                mode=payload.mode,
                created_by="api:crawler_campaign",
            )
            return campaign_to_dict(campaign)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/crawler/campaigns")
    def list_crawler_campaigns_endpoint(
        source_id: str | None = None,
        limit: int = 100,
        session: Session = Depends(get_db_session),
    ) -> list[dict]:
        return list_campaigns(session, source_id=source_id, limit=limit)

    @app.post("/api/crawler/loop/run")
    def run_crawler_loop_endpoint(
        payload: CrawlerLoopRunRequest,
        session: Session = Depends(get_db_session),
        storage: ObjectStore = Depends(get_object_store),
    ) -> dict:
        try:
            return run_loop_once(
                session,
                worker_limit=max(1, min(payload.worker_limit, 50)),
                max_worker_cycles=max(1, min(payload.max_worker_cycles, 100)),
                force=payload.force,
                dry_run=payload.dry_run,
                trigger=payload.trigger,
                worker_id=payload.worker_id,
                lease_seconds=payload.lease_seconds,
                storage=storage,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/trading/sources", response_model=list[DataSourceResponse])
    def list_trading_sources_endpoint(
        session: Session = Depends(get_db_session),
    ) -> list[dict[str, object]]:
        return [serialize_data_source(source) for source in list_trading_sources(session)]

    @app.post("/api/trading/sources/register")
    def register_trading_sources_endpoint(
        session: Session = Depends(get_db_session),
    ) -> dict[str, object]:
        return {"registered": [result.to_dict() for result in restore_all_source_configs(session)]}

    @app.post("/api/trading/scheduler/run")
    def run_trading_scheduler_endpoint(
        payload: TradingSchedulerRunRequest,
        session: Session = Depends(get_db_session),
    ) -> dict[str, object]:
        try:
            return run_trading_scheduler(
                session,
                trigger=payload.trigger,
                source_id=payload.source_id,
                site_id=payload.site_id,
                dry_run=payload.dry_run,
                force=payload.force,
                verify=payload.verify,
                channel_ids=payload.channel_ids,
                max_pages=payload.max_pages,
                page_size=payload.page_size,
                max_items_per_channel=payload.max_items_per_channel,
                start_date=payload.start_date,
                end_date=payload.end_date,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/trading/worker/run")
    def run_trading_worker_endpoint(
        payload: TradingWorkerRunRequest,
        session: Session = Depends(get_db_session),
        storage: ObjectStore = Depends(get_object_store),
    ) -> dict[str, object]:
        try:
            return run_trading_worker(
                session,
                limit=max(1, min(payload.limit, 50)),
                source_id=payload.source_id,
                site_id=payload.site_id,
                dry_run=payload.dry_run,
                trigger=payload.trigger,
                worker_id=payload.worker_id,
                lease_seconds=payload.lease_seconds,
                storage=storage,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/trading/loop/run")
    def run_trading_loop_endpoint(
        payload: TradingLoopRunRequest,
        session: Session = Depends(get_db_session),
        storage: ObjectStore = Depends(get_object_store),
    ) -> dict[str, object]:
        try:
            return run_trading_loop_once(
                session,
                worker_limit=max(1, min(payload.worker_limit, 50)),
                max_worker_cycles=max(1, min(payload.max_worker_cycles, 100)),
                force=payload.force,
                dry_run=payload.dry_run,
                trigger=payload.trigger,
                worker_id=payload.worker_id,
                lease_seconds=payload.lease_seconds,
                storage=storage,
                source_id=payload.source_id,
                site_id=payload.site_id,
                verify=payload.verify,
                channel_ids=payload.channel_ids,
                max_pages=payload.max_pages,
                page_size=payload.page_size,
                max_items_per_channel=payload.max_items_per_channel,
                start_date=payload.start_date,
                end_date=payload.end_date,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/file-assets")
    def list_file_assets_endpoint(
        tenant_code: str | None = None,
        source_type: str | None = None,
        batch_id: str | None = None,
        limit: int = 100,
        session: Session = Depends(get_db_session),
    ) -> list[dict[str, object]]:
        capped_limit = max(1, min(limit, 500))
        statement = select(FileAsset)
        if tenant_code:
            statement = statement.where(FileAsset.tenant_code == tenant_code)
        statement = statement.order_by(FileAsset.created_at.desc()).limit(capped_limit)

        rows: list[dict[str, object]] = []
        for asset in session.scalars(statement).all():
            latest_event = session.scalars(
                select(IngestEvent)
                .where(IngestEvent.file_id == asset.file_id)
                .order_by(IngestEvent.ingested_at.desc())
                .limit(1)
            ).first()
            if source_type and (latest_event is None or latest_event.source_type != source_type):
                continue
            if batch_id and (latest_event is None or latest_event.batch_id != batch_id):
                continue
            rows.append(
                {
                    "file_id": asset.file_id,
                    "tenant_code": asset.tenant_code,
                    "bucket": asset.bucket,
                    "object_key": asset.object_key,
                    "sha256": asset.sha256,
                    "file_name": asset.file_name,
                    "file_ext": asset.file_ext,
                    "mime_type": asset.mime_type,
                    "file_size": asset.file_size,
                    "parent_file_id": asset.parent_file_id,
                    "lifecycle": asset.lifecycle,
                    "created_at": asset.created_at.isoformat(),
                    "ingest_event_id": latest_event.event_id if latest_event else None,
                    "source_id": latest_event.source_id if latest_event else None,
                    "task_id": latest_event.task_id if latest_event else None,
                    "source_type": latest_event.source_type if latest_event else None,
                    "batch_id": latest_event.batch_id if latest_event else None,
                    "source_url": latest_event.source_url if latest_event else None,
                    "source_item_key": latest_event.source_item_key if latest_event else None,
                    "original_name": latest_event.original_name if latest_event else asset.file_name,
                    "ingested_at": latest_event.ingested_at.isoformat() if latest_event else None,
                }
            )
        return rows

    @app.get("/api/storage-audit/file-assets")
    def storage_file_asset_audit_endpoint(
        domain_type: str | None = None,
        region_code: str | None = None,
        limit: int | None = None,
        issue_limit: int = 50,
        session: Session = Depends(get_db_session),
        storage: ObjectStore = Depends(get_object_store),
    ) -> dict[str, object]:
        audit = audit_file_asset_objects(
            session,
            storage,
            domain_type=domain_type,
            region_code=region_code,
            limit=max(1, min(limit, 10000)) if limit is not None else None,
            issue_limit=max(0, min(issue_limit, 500)),
        )
        failed_count = (
            int(audit["missing_count"])
            + int(audit["size_mismatch_count"])
            + int(audit["error_count"])
            + int(audit["orphan_reference_count"])
        )
        checked_count = int(audit["checked_count"])
        audit["health_status"] = "healthy" if checked_count and failed_count == 0 else "degraded" if failed_count else "empty"
        audit["availability_rate"] = (int(audit["ok_count"]) / checked_count) if checked_count else None
        return audit

    @app.post("/api/file-mirror/export")
    def export_file_mirror_endpoint(
        domain_type: str | None = None,
        region_code: str | None = None,
        limit: int = 100,
        offset: int = 0,
        session: Session = Depends(get_db_session),
        storage: ObjectStore = Depends(get_object_store),
        mirror: FileMirror = Depends(get_file_mirror),
    ) -> dict[str, object]:
        if not mirror.configured:
            raise HTTPException(status_code=400, detail="FILE_MIRROR_ROOT_UNCONFIGURED")
        statement = (
            select(Archive, ArchiveFile, FileAsset)
            .join(ArchiveFile, ArchiveFile.archive_id == Archive.archive_id)
            .join(FileAsset, ArchiveFile.file_id == FileAsset.file_id)
            .where(Archive.is_withdrawn.is_(False))
            .order_by(Archive.created_at.desc(), Archive.archive_id.desc(), ArchiveFile.sort_order, ArchiveFile.added_at)
            .offset(max(0, offset))
            .limit(max(1, min(limit, 500)))
        )
        if domain_type:
            statement = statement.where(Archive.domain_type == domain_type)
        if region_code:
            statement = statement.where(Archive.region_code == region_code)
        rows = session.execute(statement).all()
        exported: list[dict[str, object]] = []
        try:
            for archive, mounted, asset in rows:
                content = _read_file_asset_bytes(storage, asset)
                file_status = mirror.export_file(archive, mounted, asset, content)
                file_status["archive_id"] = archive.archive_id
                file_status["file_id"] = asset.file_id
                file_status["file_name"] = asset.file_name
                exported.append(file_status)
        except FileMirrorRootUnconfigured as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "matched_count": len(rows),
            "exported_count": len(exported),
            "files": exported,
        }

    @app.get("/api/file-assets/{file_id}/download")
    def download_file_asset_endpoint(
        file_id: str,
        session: Session = Depends(get_db_session),
        storage: ObjectStore = Depends(get_object_store),
    ) -> Response:
        asset = session.get(FileAsset, file_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="FILE_ASSET_NOT_FOUND")
        try:
            content = storage.get_object(asset.bucket, asset.object_key)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="FILE_ASSET_OBJECT_NOT_FOUND") from exc
        content_type = asset.mime_type or "application/octet-stream"
        filename = quote(asset.file_name)
        return Response(
            content=content,
            media_type=content_type,
            headers={"Content-Disposition": f"attachment; filename*=utf-8''{filename}"},
        )

    @app.get("/api/file-assets/{file_id}/preview")
    def preview_file_asset_endpoint(
        file_id: str,
        request: Request,
        session: Session = Depends(get_db_session),
        storage: ObjectStore = Depends(get_object_store),
    ) -> Response:
        asset = session.get(FileAsset, file_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="FILE_ASSET_NOT_FOUND")

        file_ext = (asset.file_ext or Path(asset.file_name).suffix).replace(".", "").lower()
        filename = quote(asset.file_name)
        if file_ext in PDF_EXTENSIONS:
            total_size = int(asset.file_size)
            try:
                byte_range = _parse_http_byte_range(request.headers.get("range"), total_size)
            except ValueError as exc:
                raise HTTPException(
                    status_code=416,
                    detail="PREVIEW_RANGE_NOT_SATISFIABLE",
                    headers={
                        "Accept-Ranges": "bytes",
                        "Content-Range": f"bytes */{total_size}",
                    },
                ) from exc
            try:
                stream = storage.open_object(asset.bucket, asset.object_key, byte_range)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="FILE_ASSET_OBJECT_NOT_FOUND") from exc

            headers = {
                "Accept-Ranges": "bytes",
                "Content-Disposition": f"inline; filename*=utf-8''{filename}",
            }
            status_code = 200
            content_length = total_size
            if byte_range is not None:
                start, end = byte_range
                status_code = 206
                content_length = end - start + 1
                headers["Content-Range"] = f"bytes {start}-{end}/{total_size}"
            headers["Content-Length"] = str(content_length)
            return StreamingResponse(
                stream.iter_chunks(),
                status_code=status_code,
                media_type=asset.mime_type or "application/pdf",
                headers=headers,
            )

        try:
            content = storage.get_object(asset.bucket, asset.object_key)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="FILE_ASSET_OBJECT_NOT_FOUND") from exc
        if file_ext in EXCEL_EXTENSIONS:
            # Preview renders bytes for a human only; it deliberately does not persist extraction output.
            return HTMLResponse(
                render_excel_preview_html(file_name=asset.file_name, content=content, file_ext=file_ext),
                headers={"X-File-Processing": "0", "X-Preview-Mode": "ephemeral"},
            )
        if file_ext in HTML_EXTENSIONS:
            # Notice HTML preview is display-only and deliberately does not persist structured fields.
            return HTMLResponse(
                render_html_notice_preview_html(file_name=asset.file_name, content=content),
                headers={"X-File-Processing": "0", "X-Preview-Mode": "ephemeral"},
            )
        if file_ext in DOCX_EXTENSIONS:
            # Word preview is display-only; original docx remains the opaque L0 blob.
            return HTMLResponse(
                render_docx_preview_html(file_name=asset.file_name, content=content),
                headers={"X-File-Processing": "0", "X-Preview-Mode": "ephemeral"},
            )
        if file_ext in DOWNLOAD_ONLY_EXTENSIONS:
            raise HTTPException(status_code=415, detail="PREVIEW_DOWNLOAD_ONLY")
        raise HTTPException(status_code=415, detail="PREVIEW_UNSUPPORTED")

    @app.get("/api/file-assets/{file_id}/zip-preview")
    def zip_preview_manifest_endpoint(
        file_id: str,
        session: Session = Depends(get_db_session),
        storage: ObjectStore = Depends(get_object_store),
    ) -> JSONResponse:
        asset = _require_file_asset_for_preview(session, file_id)
        if not _zip_preview_allowed(session, asset):
            raise HTTPException(status_code=415, detail="ZIP_PREVIEW_OPAQUE_SOURCE")
        content = _read_file_asset_bytes(storage, asset)
        file_ext = (asset.file_ext or Path(asset.file_name).suffix).replace(".", "").lower()
        if file_ext != "zip":
            raise HTTPException(status_code=415, detail="ZIP_PREVIEW_UNSUPPORTED")
        try:
            entries = zip_image_preview_entries(file_id=file_id, content=content)
        except ValueError as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        if not entries:
            raise HTTPException(status_code=415, detail="ZIP_PREVIEW_NO_IMAGE_ENTRIES")
        return JSONResponse(
            {
                "file_id": asset.file_id,
                "file_name": asset.file_name,
                "entry_count": _zip_entry_count(content),
                "previewable_count": len(entries),
                "entries": entries,
                "preview_mode": "ephemeral",
                "file_processing": 0,
            },
            headers={"X-File-Processing": "0", "X-Preview-Mode": "ephemeral"},
        )

    @app.get("/api/file-assets/{file_id}/zip-preview/{entry_index}")
    def zip_preview_entry_endpoint(
        file_id: str,
        entry_index: int,
        session: Session = Depends(get_db_session),
        storage: ObjectStore = Depends(get_object_store),
    ) -> Response:
        asset = _require_file_asset_for_preview(session, file_id)
        if not _zip_preview_allowed(session, asset):
            raise HTTPException(status_code=415, detail="ZIP_PREVIEW_OPAQUE_SOURCE")
        content = _read_file_asset_bytes(storage, asset)
        try:
            entry_name, entry_content, content_type = read_zip_image_preview_entry(
                content=content,
                entry_index=entry_index,
            )
        except IndexError as exc:
            raise HTTPException(status_code=404, detail="ZIP_PREVIEW_ENTRY_NOT_FOUND") from exc
        except ValueError as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        filename = quote(Path(entry_name).name)
        return Response(
            content=entry_content,
            media_type=content_type,
            headers={
                "Content-Disposition": f"inline; filename*=utf-8''{filename}",
                "X-File-Processing": "0",
                "X-Preview-Mode": "ephemeral",
            },
        )

    @app.get("/api/file-processing")
    def list_file_processing_endpoint(
        file_id: str | None = None,
        tenant_code: str | None = None,
        processor: str | None = None,
        status: str | None = None,
        limit: int = 200,
        session: Session = Depends(get_db_session),
    ) -> list[dict[str, object]]:
        capped_limit = max(1, min(limit, 500))
        statement = select(FileProcessing, FileAsset).join(FileAsset, FileProcessing.file_id == FileAsset.file_id)
        if file_id:
            statement = statement.where(FileProcessing.file_id == file_id)
        if tenant_code:
            statement = statement.where(FileAsset.tenant_code == tenant_code)
        if processor:
            statement = statement.where(FileProcessing.processor == processor)
        if status:
            statement = statement.where(FileProcessing.status == status)
        statement = statement.order_by(FileProcessing.started_at.desc(), FileProcessing.processing_id.desc()).limit(
            capped_limit
        )

        rows = []
        for task, asset in session.execute(statement).all():
            rows.append(
                {
                    "processing_id": task.processing_id,
                    "file_id": task.file_id,
                    "file_name": asset.file_name,
                    "tenant_code": asset.tenant_code,
                    "processor": task.processor,
                    "status": task.status,
                    "attempt": task.attempt,
                    "output_bucket": task.output_bucket,
                    "output_key": task.output_key,
                    "error": task.error,
                    "started_at": task.started_at.isoformat() if task.started_at else None,
                    "finished_at": task.finished_at.isoformat() if task.finished_at else None,
                }
            )
        return rows

    @app.get("/api/info-price/coverage-matrix")
    def info_price_coverage_matrix_endpoint(
        start_period: str = "2026-01",
        end_period: str | None = None,
        province_code: str | None = None,
        target_level: str | None = None,
        session: Session = Depends(get_db_session),
    ) -> list[dict[str, object]]:
        rows = build_coverage_matrix(
            session,
            start_period=start_period,
            end_period=end_period,
            province_code=province_code,
        )
        if target_level:
            rows = [row for row in rows if row.target_level == target_level]
        return [row.to_dict() for row in rows]

    @app.get("/api/data-lake/quota/coverage-matrix")
    def quota_coverage_matrix_endpoint(
        session: Session = Depends(get_db_session),
    ) -> dict[str, object]:
        # 2026-08-17: 行=年份（desc，未知在末尾），列=省份，单元格=档案数
        # 路径必须挂 /api/data-lake/quota 下（与 quota_api.py router prefix 一致）
        # 不接 query 参数：用户原话"只用展示一个矩阵，不用选省份"
        return build_quota_coverage_matrix(session)

    @app.get("/api/info-price/national-completeness")
    def national_info_price_completeness_endpoint() -> dict[str, object]:
        regions_path = getattr(
            app.state,
            "national_cost_info_regions_path",
            Path(__file__).resolve().parent.parent / "data" / "national_cost_info_regions.csv",
        )
        audit_path = getattr(
            app.state,
            "national_info_price_audit_path",
            Path(__file__).resolve().parent.parent / "data" / "national_info_price_sources.csv",
        )
        regions = load_national_regions(regions_path)
        rows = load_source_audit_rows(audit_path)
        validation = validate_source_audit(rows, regions)
        summary = build_audit_summary(rows, regions)
        summary["missing_target_region_codes"] = validation.missing_target_region_codes
        summary["invalid_rows"] = validation.invalid_rows
        return summary

    @app.post("/api/archives", response_model=ArchiveResponse)
    def create_archive_endpoint(
        payload: ArchiveCreate,
        session: Session = Depends(get_db_session),
        mirror: FileMirror = Depends(get_file_mirror),
    ) -> dict[str, object]:
        try:
            archive = create_archive(session, **payload.model_dump())
            return get_archive_detail(session, archive.archive_id, mirror=mirror)
        except ValueError as exc:
            raise archive_http_error(exc) from exc

    @app.get("/api/archives", response_model=list[ArchiveSummaryResponse])
    def list_archives_endpoint(
        response: Response,
        domain_type: str | None = None,
        status: str | None = None,
        region_code: str | None = None,
        channel_type: str | None = None,
        tenant_code: str | None = None,
        source_id: str | None = None,
        search: str | None = None,
        # ── quota 域 chip 筛选（仅在 domain_type=quota 时生效，2026-07-29 增）──
        primary: str | None = Query(
            None,
            description="定额体系大类；与 quota_api /facets 同义：all|construction_quota|industry_quota|boq_standard (v0.9.2 起改用 book_category 单字段)",
        ),
        jurisdiction_code: str | None = Query(
            None,
            description="省级行政区划码（510000 / 440000 …）",
        ),
        industry_sector_code: str | None = Query(
            None, description="行业代码（专业工程定额用）"
        ),
        edition_year: int | None = Query(
            None,
            description="定额版本年（1900-2100 整数）",
        ),
        edition_label: str | None = Query(
            None, description="定额版本标识字符串"
        ),
        discipline_code: str | None = Query(
            None, description="专业分类码"
        ),
        limit: int = 100,
        offset: int = 0,
        session: Session = Depends(get_db_session),
        mirror: FileMirror = Depends(get_file_mirror),
    ) -> list[dict[str, object]]:
        try:
            quota_filter_active = any(
                v is not None
                for v in (
                    primary,
                    jurisdiction_code,
                    industry_sector_code,
                    edition_year,
                    edition_label,
                    discipline_code,
                )
            )
            # quota 域 chip 筛选：路由到带 join 的 SQL，但保留 primary_file 预览字段。
            if quota_filter_active:
                items, total_count = _run_quota_filtered_listing(
                    session,
                    domain_type=domain_type,
                    status=status,
                    region_code=region_code,
                    channel_type=channel_type,
                    tenant_code=tenant_code,
                    source_id=source_id,
                    search=search,
                    primary=primary,
                    jurisdiction_code=jurisdiction_code,
                    industry_sector_code=industry_sector_code,
                    edition_year=edition_year,
                    edition_label=edition_label,
                    discipline_code=discipline_code,
                    limit=limit,
                    offset=offset,
                    mirror=mirror,
                )
                response.headers["X-Total-Count"] = str(total_count)
                response.headers["X-Limit"] = str(max(1, min(limit, 500)))
                response.headers["X-Offset"] = str(max(0, offset))
                return items

            # 通用路径：所有现有 caller 行为不变
            total_count = count_archives(
                session,
                domain_type=domain_type,
                status=status,
                region_code=region_code,
                channel_type=channel_type,
                tenant_code=tenant_code,
                source_id=source_id,
                search=search,
            )
            response.headers["X-Total-Count"] = str(total_count)
            response.headers["X-Limit"] = str(max(1, min(limit, 500)))
            response.headers["X-Offset"] = str(max(0, offset))
            return list_archives(
                session,
                domain_type=domain_type,
                status=status,
                region_code=region_code,
                channel_type=channel_type,
                tenant_code=tenant_code,
                source_id=source_id,
                search=search,
                limit=limit,
                offset=offset,
                mirror=mirror,
            )
        except ValueError as exc:
            raise archive_http_error(exc) from exc

    @app.post("/api/archives/from-ingest-event", response_model=ArchiveFromIngestEventResponse)
    def create_archive_from_ingest_event_endpoint(
        payload: ArchiveFromIngestEventCreate,
        session: Session = Depends(get_db_session),
        mirror: FileMirror = Depends(get_file_mirror),
    ) -> dict[str, object]:
        try:
            archive, attached_to_existing = create_archive_from_ingest_event_with_flag(
                session, **payload.model_dump()
            )
            detail = get_archive_detail(session, archive.archive_id, mirror=mirror)
            detail["attached_to_existing"] = attached_to_existing
            return detail
        except ValueError as exc:
            raise archive_http_error(exc) from exc

    @app.get("/api/archives/{archive_id}", response_model=ArchiveDetailResponse)
    def get_archive_detail_endpoint(
        archive_id: str,
        session: Session = Depends(get_db_session),
        mirror: FileMirror = Depends(get_file_mirror),
    ) -> dict[str, object]:
        try:
            return get_archive_detail(session, archive_id, mirror=mirror)
        except ValueError as exc:
            raise archive_http_error(exc) from exc

    @app.post("/api/archives/{archive_id}/mirror")
    def mirror_archive_files_endpoint(
        archive_id: str,
        session: Session = Depends(get_db_session),
        storage: ObjectStore = Depends(get_object_store),
        mirror: FileMirror = Depends(get_file_mirror),
    ) -> dict[str, object]:
        archive = session.get(Archive, archive_id)
        if archive is None or archive.is_withdrawn:
            raise HTTPException(status_code=404, detail=f"ARCHIVE_NOT_FOUND: {archive_id}")
        if not mirror.configured:
            raise HTTPException(status_code=400, detail="FILE_MIRROR_ROOT_UNCONFIGURED")
        rows = session.execute(
            select(ArchiveFile, FileAsset)
            .join(FileAsset, ArchiveFile.file_id == FileAsset.file_id)
            .where(ArchiveFile.archive_id == archive.archive_id)
            .order_by(ArchiveFile.sort_order, ArchiveFile.added_at)
        ).all()
        exported: list[dict[str, object]] = []
        try:
            for mounted, asset in rows:
                content = _read_file_asset_bytes(storage, asset)
                exported.append(mirror.export_file(archive, mounted, asset, content))
        except FileMirrorRootUnconfigured as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "archive_id": archive.archive_id,
            "exported_count": len(exported),
            "files": exported,
        }

    @app.patch("/api/archives/{archive_id}", response_model=ArchiveResponse)
    def patch_archive_endpoint(
        archive_id: str,
        payload: ArchivePatch,
        session: Session = Depends(get_db_session),
        mirror: FileMirror = Depends(get_file_mirror),
    ) -> dict[str, object]:
        try:
            archive = patch_archive(
                session,
                archive_id,
                **payload.model_dump(exclude_unset=True),
            )
            return get_archive_detail(session, archive.archive_id, mirror=mirror)
        except ValueError as exc:
            raise archive_http_error(exc) from exc

    @app.delete("/api/archives/{archive_id}")
    def delete_archive_endpoint(
        archive_id: str,
        session: Session = Depends(get_db_session),
    ) -> dict[str, object]:
        try:
            delete_archive(session, archive_id, actor_type="user", actor_id="ui:manual-delete")
            return {"archive_id": archive_id, "deleted": True}
        except ValueError as exc:
            raise archive_http_error(exc) from exc

    @app.post("/api/archives/{archive_id}/withdraw")
    def withdraw_archive_endpoint(
        archive_id: str,
        session: Session = Depends(get_db_session),
    ) -> dict[str, object]:
        try:
            archive = withdraw_archive(
                session,
                archive_id,
                actor_type="user",
                actor_id="ui:soft-delete",
            )
            return {
                "archive_id": archive.archive_id,
                "withdrawn": archive.is_withdrawn,
                "storage_deleted": False,
            }
        except ValueError as exc:
            raise archive_http_error(exc) from exc

    @app.post("/api/archives/{archive_id}/files", response_model=ArchiveFileResponse)
    def attach_archive_file_endpoint(
        archive_id: str,
        payload: ArchiveFileAttach,
        session: Session = Depends(get_db_session),
    ) -> dict[str, object]:
        try:
            mounted = attach_archive_file(
                session,
                archive_id,
                **payload.model_dump(),
            )
            return serialize_archive_file_response(mounted)
        except ValueError as exc:
            raise archive_http_error(exc) from exc

    @app.post("/api/file-assets/ingest", response_model=IngestResponse)
    async def ingest_file(
        file: Annotated[UploadFile, File()],
        tenant_code: Annotated[str, Form()],
        source_type: Annotated[str, Form()],
        batch_id: Annotated[str | None, Form()] = None,
        source_url: Annotated[str | None, Form()] = None,
        source_id: Annotated[str | None, Form()] = None,
        task_id: Annotated[str | None, Form()] = None,
        source_item_key: Annotated[str | None, Form()] = None,
        source_modified_at: Annotated[str | None, Form()] = None,
        source_metadata: Annotated[str | None, Form()] = None,
        derive_tasks: Annotated[bool, Form()] = True,
        discovered_at: Annotated[str | None, Form()] = None,
        fetched_at: Annotated[str | None, Form()] = None,
        session: Session = Depends(get_db_session),
        storage: ObjectStore = Depends(get_object_store),
    ) -> dict[str, object]:
        content = await file.read()
        metadata_payload = parse_source_metadata(
            source_metadata,
            discovered_at=discovered_at,
            fetched_at=fetched_at,
        )
        channel_type = (metadata_payload or {}).get("channel_type")
        result = register_asset(
            session,
            storage,
            tenant_code=tenant_code,
            source_type=source_type,
            batch_id=batch_id,
            file_name=file.filename or "upload.bin",
            content=content,
            source_url=source_url,
            mime_type=file.content_type,
            source_id=source_id,
            task_id=task_id,
            source_item_key=source_item_key,
            source_modified_at=parse_form_datetime(source_modified_at, "source_modified_at"),
            source_metadata=metadata_payload,
            channel_type=channel_type,
            derive_tasks=derive_tasks,
        )
        existing_archive_rows = session.execute(
            select(Archive.archive_id, Archive.title, ArchiveFile.added_at)
            .join(ArchiveFile, ArchiveFile.archive_id == Archive.archive_id)
            .where(ArchiveFile.file_id == result.file_id)
            .order_by(ArchiveFile.added_at.desc())
        ).all()
        seen_archive_ids: set[str] = set()
        existing_archives: list[dict[str, str]] = []
        for row in existing_archive_rows:
            if row.archive_id in seen_archive_ids:
                continue
            seen_archive_ids.add(row.archive_id)
            existing_archives.append({"archive_id": row.archive_id, "title": row.title})
        ingest_payload = result.to_dict()
        ingest_payload["existing_archives"] = existing_archives
        return ingest_payload

    @app.get("/api/ingest-events", response_model=list[IngestEventResponse])
    def list_ingest_events(
        tenant_code: str | None = None,
        batch_id: str | None = None,
        session: Session = Depends(get_db_session),
    ) -> list[dict[str, object]]:
        statement = select(IngestEvent, FileAsset).join(FileAsset, IngestEvent.file_id == FileAsset.file_id)
        if tenant_code:
            statement = statement.where(FileAsset.tenant_code == tenant_code)
        if batch_id:
            statement = statement.where(IngestEvent.batch_id == batch_id)
        statement = statement.order_by(IngestEvent.ingested_at)

        rows = []
        for event, asset in session.execute(statement).all():
            rows.append(
                {
                    "event_id": event.event_id,
                    "file_id": event.file_id,
                    "tenant_code": asset.tenant_code,
                    "source_id": event.source_id,
                    "task_id": event.task_id,
                    "source_type": event.source_type,
                    "batch_id": event.batch_id,
                    "source_url": event.source_url,
                    "source_item_key": event.source_item_key,
                    "source_modified_at": event.source_modified_at.isoformat() if event.source_modified_at else None,
                    "source_metadata": event.source_metadata,
                    "original_name": event.original_name,
                    "ingested_at": event.ingested_at.isoformat(),
                }
            )
        return rows

    @app.post("/api/file-processing/{processing_id}/run", response_model=ProcessingRunResponse)
    def run_processing(
        processing_id: str,
        session: Session = Depends(get_db_session),
        storage: ObjectStore = Depends(get_object_store),
    ) -> dict[str, object]:
        result = run_processing_task(session, storage, processing_id)
        return {
            "processing_id": result.processing_id,
            "processor": result.processor,
            "status": result.status,
            "created_file_ids": result.created_file_ids,
            "duplicated_file_ids": result.duplicated_file_ids,
            "message": result.message,
        }

    @app.get("/api/file-processing/{processing_id}/output")
    def get_processing_output(
        processing_id: str,
        session: Session = Depends(get_db_session),
        storage: ObjectStore = Depends(get_object_store),
    ) -> JSONResponse:
        task = session.get(FileProcessing, processing_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"processing task not found: {processing_id}")
        if not task.output_bucket or not task.output_key:
            raise HTTPException(status_code=404, detail="processing task has no output")
        content = storage.get_object(task.output_bucket, task.output_key)
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail="processing output is not JSON") from exc
        return JSONResponse(payload)

    @app.get("/api/info-price/extracts")
    def list_info_price_extracts_endpoint(
        limit: int = 100,
        session: Session = Depends(get_db_session),
        storage: ObjectStore = Depends(get_object_store),
    ) -> list[dict[str, object]]:
        return list_info_price_extracts(session, storage, limit=limit)

    @app.get("/api/info-price/extracts/{processing_id}/review")
    def get_info_price_review_view(
        processing_id: str,
        session: Session = Depends(get_db_session),
        storage: ObjectStore = Depends(get_object_store),
    ) -> dict[str, object]:
        try:
            task, asset, payload = load_extract_payload(session, storage, processing_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return build_review_view(task, asset, payload)

    @app.get("/api/info-price/extracts/{processing_id}/audit")
    def get_info_price_audit_view(
        processing_id: str,
        session: Session = Depends(get_db_session),
        storage: ObjectStore = Depends(get_object_store),
    ) -> dict[str, object]:
        try:
            task, asset, payload = load_extract_payload(session, storage, processing_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return build_audit_view(task, asset, payload)

    # ===== PDF -> xlsx parse pipeline endpoints (cost_info row action) =====

    def _parse_error_to_http(exc: Exception) -> HTTPException:
        """Map info_price_parse.ParseError to FastAPI HTTPException with sensible status codes."""
        error_code = getattr(exc, "error_code", "RUNPY_CRASH")
        status_map = {
            "ARCHIVE_NOT_FOUND": 404,
            "NO_XLSX_AVAILABLE": 404,
            "ARCHIVE_WITHDRAWN": 400,
            "ARCHIVE_NO_FILE": 400,
            "UNSUPPORTED_CITY": 400,
            "INVALID_FILENAME": 400,
            "PDF_DOWNLOAD_FAILED": 502,
            "PYTHON_NOT_FOUND": 500,
            "RUNPY_NOT_FOUND": 500,
            "RUNPY_CRASH": 500,
            "XLSX_NOT_FOUND": 500,
            "TIMEOUT": 504,
            "SEMAPHORE_QUEUE_FULL": 409,
            "PARSE_ALREADY_RUNNING": 409,
        }
        return HTTPException(status_code=status_map.get(error_code, 400), detail=error_code)

    @app.post("/api/archives/{archive_id}/parse", status_code=202)
    def post_parse(
        archive_id: str,
        body: ParseSubmitRequest | None = None,
        session: Session = Depends(get_db_session),
        storage: ObjectStore = Depends(get_object_store),
    ) -> ParseSubmitResponse:
        """Start a parse job. Body.year + body.period override auto-detection."""
        from app.database import get_session_factory
        from app.models import ArchiveFile, FileAsset
        archive = session.get(Archive, archive_id)
        if archive is None:
            raise HTTPException(status_code=404, detail="ARCHIVE_NOT_FOUND")
        # 2026-08-13: 防重复提交——同一 archive 正在解析/排队中，拒绝再次提交，
        # 否则会启动第二个 run.py 并行抢同一批 chunk/result.json（文件锁死锁）。
        if archive.parse_status in ("queued", "running"):
            raise HTTPException(status_code=409, detail="PARSE_ALREADY_RUNNING")
        primary_row = session.execute(
            select(ArchiveFile, FileAsset)
            .outerjoin(FileAsset, ArchiveFile.file_id == FileAsset.file_id)
            .where(ArchiveFile.archive_id == archive_id, ArchiveFile.is_primary == True)  # noqa: E712
            .order_by(ArchiveFile.sort_order)
            .limit(1)
        ).first()
        if primary_row is None:
            raise HTTPException(status_code=400, detail="ARCHIVE_NO_FILE")
        primary_file_name = primary_row[1].file_name if primary_row[1] else None
        if not primary_file_name:
            raise HTTPException(status_code=400, detail="ARCHIVE_NO_FILE")
        # 2026-08-07 改：detect_city 走三级 fallback(filename → title → region_code)
        # region_code 让广州(440100)能识别(广州 PDF filename 是数字 ID)
        detected = detect_city(
            primary_file_name,
            title=str(archive.title or ""),
            region_code=str(archive.region_code or ""),
        )
        if body is not None and body.year and body.period:
            year = body.year
            period = body.period
            city = detected[0] if detected else "成都"
        else:
            if detected is None:
                raise HTTPException(status_code=400, detail="INVALID_FILENAME")
            city, params = detected
            year, period = params["year"], params["period"]
        try:
            task_id = submit_parse_job(
                session_factory=get_session_factory(),
                storage=storage,
                settings=get_settings(),
                archive_id=archive_id,
                year=year,
                period=period,
                city_code=city,
            )
        except ParseError as exc:
            raise _parse_error_to_http(exc) from exc
        return ParseSubmitResponse(
            task_id=task_id,
            archive_id=archive_id,
            parse_status="queued",
            estimated_duration_seconds="~30s",
        )

    @app.get("/api/archives/{archive_id}/parse-status")
    def get_parse_status(archive_id: str) -> dict[str, object]:
        """Lightweight badge endpoint (no streaming, no logs). Used by row-level poll."""
        from app.database import get_session_factory
        with get_session_factory()() as session:
            archive = session.get(Archive, archive_id)
            if archive is None:
                raise HTTPException(status_code=404, detail="ARCHIVE_NOT_FOUND")
            return {
                "archive_id": archive.archive_id,
                "parse_status": archive.parse_status,
                "parse_phase": archive.parse_phase,
                "parse_error_code": archive.parse_error_code,
                "parse_started_at": archive.parse_started_at.isoformat() if archive.parse_started_at else None,
                "parse_finished_at": archive.parse_finished_at.isoformat() if archive.parse_finished_at else None,
                "final_xlsx_key": archive.final_xlsx_key,
            }

    @app.get("/api/archives/{archive_id}/parse-stream")
    def get_parse_stream(
        archive_id: str,
        since: int = 0,
    ) -> StreamingResponse:
        """SSE stream of parse progress. ?since=N for Last-Event-ID reconnect."""
        from app.database import get_session_factory
        archive_row = get_session_factory()().get(Archive, archive_id)
        if archive_row is None:
            raise HTTPException(status_code=404, detail="ARCHIVE_NOT_FOUND")
        task_id = archive_row.parse_task_id
        if not task_id:
            raise HTTPException(status_code=404, detail="NO_ACTIVE_TASK")
        generator = stream_parse_events(task_id, since_seq=since)
        return StreamingResponse(
            generator,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/archives/{archive_id}/parse-cancel")
    def post_parse_cancel(
        archive_id: str,
        session: Session = Depends(get_db_session),
    ) -> ParseCancelResponse:
        """Cancel a running parse job."""
        archive = session.get(Archive, archive_id)
        if archive is None:
            raise HTTPException(status_code=404, detail="ARCHIVE_NOT_FOUND")
        task_id = archive.parse_task_id
        if not task_id:
            return ParseCancelResponse(task_id="", cancelled=False, already_terminal=True)
        cancelled, already_terminal = cancel_parse_job(task_id)
        return ParseCancelResponse(task_id=task_id, cancelled=cancelled, already_terminal=already_terminal)

    @app.get("/api/archives/{archive_id}/parse-output")
    def get_parse_output(
        archive_id: str,
        session: Session = Depends(get_db_session),
        storage: ObjectStore = Depends(get_object_store),
    ) -> Response:
        """2026-08-03 改：直接代理 MinIO xlsx 字节流给前端（同源返回，绕过 MinIO CORS preflight 拦截）。"""
        from app.database import get_session_factory
        archive_row = session.get(Archive, archive_id)
        if archive_row is None:
            raise HTTPException(status_code=404, detail="ARCHIVE_NOT_FOUND")
        final_xlsx_key = archive_row.final_xlsx_key
        if not final_xlsx_key:
            raise HTTPException(status_code=404, detail="PARSE_OUTPUT_NOT_READY")
        settings = get_settings()
        try:
            content = storage.get_object(settings.extract_bucket, final_xlsx_key)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="FILE_ASSET_OBJECT_NOT_FOUND") from exc
        filename = (final_xlsx_key.split("/")[-1] or "parse-output.xlsx")
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(len(content)),
                "X-Parse-Finished-At": (archive_row.parse_finished_at.isoformat() if archive_row.parse_finished_at else ""),
            },
        )

    @app.get("/api/archives/{archive_id}/parse-output-preview", response_class=HTMLResponse)
    def get_parse_output_preview(
        archive_id: str,
        session: Session = Depends(get_db_session),
        storage: ObjectStore = Depends(get_object_store),
    ) -> HTMLResponse:
        """2026-08-03 新增：解析输出的 xlsx HTML 预览（viewer 右 pane / 双预览用）。"""
        archive_row = session.get(Archive, archive_id)
        if archive_row is None:
            raise HTTPException(status_code=404, detail="ARCHIVE_NOT_FOUND")
        final_xlsx_key = archive_row.final_xlsx_key
        if not final_xlsx_key:
            raise HTTPException(status_code=404, detail="PARSE_OUTPUT_NOT_READY")
        settings = get_settings()
        try:
            content = storage.get_object(settings.extract_bucket, final_xlsx_key)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="FILE_ASSET_OBJECT_NOT_FOUND") from exc
        filename = final_xlsx_key.split("/")[-1] or "parse-output.xlsx"
        # 信息价 xlsx 多 sheet（info_price_parse.py:731 遍历 wb.worksheets）,
        # 走多 sheet tab 版渲染；定额单 sheet（service.py:190「定额条目」）走分页版,本端点不涉及。
        html = render_excel_preview_multi_sheet_html(file_name=filename, content=content, file_ext="xlsx")
        return HTMLResponse(
            html,
            headers={"X-Preview-Mode": "ephemeral", "X-Parse-Finished-At": (archive_row.parse_finished_at.isoformat() if archive_row.parse_finished_at else "")},
        )

    @app.get("/api/archives/{archive_id}/parse-output-full-preview", response_class=HTMLResponse)
    def get_parse_output_full_preview(
        archive_id: str,
        session: Session = Depends(get_db_session),
        storage: ObjectStore = Depends(get_object_store),
    ) -> HTMLResponse:
        """2026-08-03 新增：解析输出的 xlsx 完整版 HTML 预览（不截断行/列，viewer 内替换用）。"""
        archive_row = session.get(Archive, archive_id)
        if archive_row is None:
            raise HTTPException(status_code=404, detail="ARCHIVE_NOT_FOUND")
        final_xlsx_key = archive_row.final_xlsx_key
        if not final_xlsx_key:
            raise HTTPException(status_code=404, detail="PARSE_OUTPUT_NOT_READY")
        settings = get_settings()
        try:
            content = storage.get_object(settings.extract_bucket, final_xlsx_key)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="FILE_ASSET_OBJECT_NOT_FOUND") from exc
        filename = final_xlsx_key.split("/")[-1] or "parse-output.xlsx"
        # 不截断行/列，渲染全表
        # 信息价多 sheet（cost_info 域），走多 sheet tab 版
        html = render_excel_preview_multi_sheet_html(
            file_name=filename,
            content=content,
            file_ext="xlsx",
            max_rows=99999,
            max_cols=999,
        )
        return HTMLResponse(
            html,
            headers={"X-Preview-Mode": "full"},
        )

    @app.delete("/api/archives/{archive_id}/parse-output")
    def delete_parse_output(
        archive_id: str,
        session: Session = Depends(get_db_session),
        storage: ObjectStore = Depends(get_object_store),
    ) -> ParseDeleteResponse:
        """Delete the MinIO xlsx + reset archive parse_* fields (preserve history timestamps)."""
        from app.database import get_session_factory
        try:
            delete_xlsx_output(
                session_factory=get_session_factory(),
                storage=storage,
                settings=get_settings(),
                archive_id=archive_id,
            )
        except ParseError as exc:
            raise _parse_error_to_http(exc) from exc
        return ParseDeleteResponse(archive_id=archive_id, deleted=True)

    return app


app = create_app()
