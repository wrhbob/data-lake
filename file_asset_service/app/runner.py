from __future__ import annotations

import json
from dataclasses import dataclass, field
from io import BytesIO
from zipfile import BadZipFile, ZipFile

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assets import register_processor_output
from app.config import get_settings
from app.models import FileAsset, FileProcessing, IngestEvent, utcnow
from app.processors import PLACEHOLDER_V01_PROCESSORS, build_info_price_extract, parse_info_price_xlsx
from app.storage import ObjectStore


@dataclass(frozen=True)
class ProcessingRunResult:
    processing_id: str
    processor: str
    status: str
    created_file_ids: list[str] = field(default_factory=list)
    duplicated_file_ids: list[str] = field(default_factory=list)
    message: str | None = None


def run_processing_task(session: Session, storage: ObjectStore, processing_id: str) -> ProcessingRunResult:
    task = session.get(FileProcessing, processing_id)
    if task is None:
        raise ValueError(f"processing task not found: {processing_id}")
    if task.status == "succeeded":
        return ProcessingRunResult(
            processing_id=task.processing_id,
            processor=task.processor,
            status="skipped",
            message="already succeeded",
        )

    task.status = "running"
    task.attempt += 1
    task.started_at = utcnow()
    task.error = None
    session.commit()

    if task.processor == "unzip":
        return _run_unzip_task(session, storage, task.processing_id)
    if task.processor == "info_price_parse":
        return _run_info_price_parse_task(session, storage, task.processing_id)
    if task.processor in PLACEHOLDER_V01_PROCESSORS:
        task = session.get(FileProcessing, processing_id)
        task.status = "skipped"
        task.finished_at = utcnow()
        task.error = "placeholder processor in v0.1"
        session.commit()
        return ProcessingRunResult(
            processing_id=task.processing_id,
            processor=task.processor,
            status=task.status,
            message=task.error,
        )

    task = session.get(FileProcessing, processing_id)
    task.status = "failed"
    task.finished_at = utcnow()
    task.error = f"processor is not implemented: {task.processor}"
    session.commit()
    return ProcessingRunResult(
        processing_id=task.processing_id,
        processor=task.processor,
        status=task.status,
        message=task.error,
    )


def _run_info_price_parse_task(session: Session, storage: ObjectStore, processing_id: str) -> ProcessingRunResult:
    task = session.get(FileProcessing, processing_id)
    asset = session.get(FileAsset, task.file_id)
    if asset.file_ext.lower() not in {".xlsx", ".xls"}:
        task.status = "failed"
        task.finished_at = utcnow()
        task.error = f"info_price_parse only supports xls/xlsx in v0.1: {asset.file_ext}"
        session.commit()
        return ProcessingRunResult(
            processing_id=task.processing_id,
            processor=task.processor,
            status=task.status,
            message=task.error,
        )

    content = storage.get_object(asset.bucket, asset.object_key)
    rows = parse_info_price_xlsx(content)
    payload = build_info_price_extract(asset.file_id, rows)
    settings = get_settings()
    output_key = f"info_price_parse/{task.processing_id}.json"
    storage.put_object(
        settings.extract_bucket,
        output_key,
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        content_type="application/json",
    )

    task.status = "succeeded"
    task.output_bucket = settings.extract_bucket
    task.output_key = output_key
    task.finished_at = utcnow()
    session.commit()
    return ProcessingRunResult(
        processing_id=task.processing_id,
        processor=task.processor,
        status=task.status,
        message=f"extracted {len(rows)} info price rows",
    )


def _run_unzip_task(session: Session, storage: ObjectStore, processing_id: str) -> ProcessingRunResult:
    task = session.get(FileProcessing, processing_id)
    asset = session.get(FileAsset, task.file_id)
    source_type = _source_type_for_asset(session, asset.file_id)
    content = storage.get_object(asset.bucket, asset.object_key)
    created_file_ids: list[str] = []
    duplicated_file_ids: list[str] = []

    try:
        with ZipFile(BytesIO(content)) as archive:
            for member in archive.infolist():
                if member.is_dir() or _should_ignore_zip_member(member.filename):
                    continue
                child_content = archive.read(member)
                child = register_processor_output(
                    session,
                    storage,
                    tenant_code=asset.tenant_code,
                    processor="unzip",
                    parent_file_id=asset.file_id,
                    file_name=member.filename,
                    content=child_content,
                    source_type=source_type,
                    batch_id=f"unzip-{asset.file_id}",
                )
                if child.duplicated:
                    duplicated_file_ids.append(child.file_id)
                else:
                    created_file_ids.append(child.file_id)
    except BadZipFile as exc:
        task = session.get(FileProcessing, processing_id)
        task.status = "failed"
        task.finished_at = utcnow()
        task.error = f"bad zip file: {exc}"
        session.commit()
        return ProcessingRunResult(
            processing_id=task.processing_id,
            processor=task.processor,
            status=task.status,
            message=task.error,
        )

    settings = get_settings()
    task = session.get(FileProcessing, processing_id)
    output_count = len(created_file_ids) + len(duplicated_file_ids)
    task.status = "succeeded"
    task.output_bucket = settings.extract_bucket
    task.output_key = f"unzip:{output_count}"
    task.finished_at = utcnow()
    session.commit()
    return ProcessingRunResult(
        processing_id=task.processing_id,
        processor=task.processor,
        status=task.status,
        created_file_ids=created_file_ids,
        duplicated_file_ids=duplicated_file_ids,
    )


def _source_type_for_asset(session: Session, file_id: str) -> str:
    event = session.scalar(
        select(IngestEvent)
        .where(IngestEvent.file_id == file_id)
        .order_by(IngestEvent.ingested_at.desc())
        .limit(1)
    )
    return event.source_type if event else "processor"


def _should_ignore_zip_member(name: str) -> bool:
    parts = [part for part in name.split("/") if part]
    if not parts:
        return True
    if parts[0] == "__MACOSX":
        return True
    return parts[-1] == ".DS_Store"
