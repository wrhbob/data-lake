from __future__ import annotations

import hashlib
import mimetypes
import os
from datetime import datetime
from dataclasses import asdict, dataclass, field
from collections.abc import Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Blob, CollectionTask, FileAsset, FileProcessing, FileRelation, IngestEvent
from app.normalization import normalize_source_url
from app.processors import derive_processors
from app.storage import ObjectStore, build_blob_storage_key


@dataclass(frozen=True)
class RegisterAssetResult:
    file_id: str
    ingest_event_id: str | None
    bucket: str
    object_key: str
    sha256: str
    duplicated: bool
    processing_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def compute_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def file_extension(file_name: str) -> str:
    _, ext = os.path.splitext(file_name)
    return ext.lower()


def guess_mime_type(file_name: str, fallback: str | None = None) -> str | None:
    guessed, _ = mimetypes.guess_type(file_name)
    return guessed or fallback


def resolve_collection_task(
    session: Session,
    *,
    task_id: str | None,
    tenant_code: str,
    source_id: str | None,
    batch_id: str | None,
) -> tuple[CollectionTask | None, str | None, str | None]:
    if task_id is None:
        return None, source_id, batch_id

    task = session.get(CollectionTask, task_id)
    if task is None:
        raise ValueError(f"collection_task not found: {task_id}")
    if task.asset_tenant_code != tenant_code:
        raise ValueError("TENANT_MISMATCH: collection_task asset tenant does not match file tenant")
    if source_id is not None and source_id != task.source_id:
        raise ValueError("SOURCE_MISMATCH: source_id does not match collection_task")
    return task, task.source_id, batch_id or task.batch_id


def update_collection_task_counts(
    task: CollectionTask | None,
    *,
    duplicated: bool,
    processing_count: int,
) -> None:
    if task is None:
        return
    task.downloaded_count += 1
    if duplicated:
        task.duplicate_file_count += 1
    else:
        task.new_file_count += 1
    task.processing_created_count += processing_count


def register_asset(
    session: Session,
    storage: ObjectStore,
    *,
    tenant_code: str,
    source_type: str,
    batch_id: str | None,
    file_name: str,
    content: bytes,
    source_url: str | None = None,
    relations: Iterable[Mapping[str, str]] | None = None,
    parent_file_id: str | None = None,
    bucket: str | None = None,
    create_ingest_event: bool = True,
    derive_tasks: bool = True,
    mime_type: str | None = None,
    source_id: str | None = None,
    task_id: str | None = None,
    source_item_key: str | None = None,
    source_modified_at: datetime | None = None,
    source_metadata: dict | None = None,
    channel_type: str | None = None,
) -> RegisterAssetResult:
    collection_task, resolved_source_id, resolved_batch_id = resolve_collection_task(
        session,
        task_id=task_id,
        tenant_code=tenant_code,
        source_id=source_id,
        batch_id=batch_id,
    )
    source_id = resolved_source_id
    batch_id = resolved_batch_id
    normalized_source_url = normalize_source_url(source_url) or None

    settings = get_settings()
    target_bucket = bucket or settings.raw_bucket
    sha256 = compute_sha256(content)
    ext = file_extension(file_name)
    content_type = guess_mime_type(file_name, mime_type)

    blob = session.scalar(select(Blob).where(Blob.blob_hash == sha256))
    if blob is None:
        object_key = build_blob_storage_key(blob_hash=sha256, file_ext=ext)
        etag = storage.put_object(target_bucket, object_key, content, content_type=content_type)
        blob = Blob(
            blob_hash=sha256,
            storage_bucket=target_bucket,
            blob_storage_key=object_key,
            byte_size=len(content),
            mime_type=content_type,
            etag=etag,
        )
        session.add(blob)
        session.flush()

    existing = session.scalar(
        select(FileAsset).where(
            FileAsset.tenant_code == tenant_code,
            FileAsset.sha256 == sha256,
        )
    )

    processing_ids: list[str] = []
    duplicated = existing is not None
    if existing is None:
        asset = FileAsset(
            tenant_code=tenant_code,
            bucket=blob.storage_bucket,
            object_key=blob.blob_storage_key,
            sha256=sha256,
            blob_hash=blob.blob_hash,
            file_name=file_name,
            file_ext=ext,
            mime_type=content_type,
            file_size=len(content),
            etag=blob.etag,
            parent_file_id=parent_file_id,
        )
        session.add(asset)
        session.flush()

        if derive_tasks:
            for processor in derive_processors(ext, source_type, channel_type=channel_type):
                processing_task = FileProcessing(file_id=asset.file_id, processor=processor)
                session.add(processing_task)
                session.flush()
                processing_ids.append(processing_task.processing_id)
    else:
        asset = existing
        if asset.blob_hash is None:
            asset.blob_hash = blob.blob_hash

    ingest_event_id: str | None = None
    if create_ingest_event:
        event = IngestEvent(
            file_id=asset.file_id,
            source_id=source_id,
            task_id=collection_task.task_id if collection_task else task_id,
            source_type=source_type,
            batch_id=batch_id,
            source_url=normalized_source_url,
            original_name=file_name,
            source_item_key=source_item_key,
            source_modified_at=source_modified_at,
            source_metadata=source_metadata,
        )
        session.add(event)
        session.flush()
        ingest_event_id = event.event_id

    if relations:
        for relation in relations:
            rel_type = relation.get("rel_type")
            rel_id = relation.get("rel_id")
            if not rel_type or not rel_id:
                continue
            exists = session.scalar(
                select(FileRelation).where(
                    FileRelation.file_id == asset.file_id,
                    FileRelation.rel_type == rel_type,
                    FileRelation.rel_id == rel_id,
                )
            )
            if exists is None:
                session.add(FileRelation(file_id=asset.file_id, rel_type=rel_type, rel_id=rel_id))

    update_collection_task_counts(collection_task, duplicated=duplicated, processing_count=len(processing_ids))

    session.commit()
    return RegisterAssetResult(
        file_id=asset.file_id,
        ingest_event_id=ingest_event_id,
        bucket=asset.bucket,
        object_key=asset.object_key,
        sha256=asset.sha256,
        duplicated=duplicated,
        processing_ids=processing_ids,
    )


def register_processor_output(
    session: Session,
    storage: ObjectStore,
    *,
    tenant_code: str,
    processor: str,
    parent_file_id: str,
    file_name: str,
    content: bytes,
    source_type: str = "info_price",
    batch_id: str | None = None,
    bucket: str | None = None,
    mime_type: str | None = None,
) -> RegisterAssetResult:
    settings = get_settings()
    result = register_asset(
        session,
        storage,
        tenant_code=tenant_code,
        source_type=source_type,
        batch_id=batch_id or f"processor-{processor}",
        file_name=file_name,
        content=content,
        parent_file_id=parent_file_id,
        bucket=bucket or settings.extract_bucket,
        create_ingest_event=False,
        derive_tasks=True,
        mime_type=mime_type,
    )
    relation_exists = session.scalar(
        select(FileRelation).where(
            FileRelation.file_id == result.file_id,
            FileRelation.rel_type == "derived_from",
            FileRelation.rel_id == parent_file_id,
        )
    )
    if relation_exists is None:
        session.add(FileRelation(file_id=result.file_id, rel_type="derived_from", rel_id=parent_file_id))
        session.commit()
    return result
