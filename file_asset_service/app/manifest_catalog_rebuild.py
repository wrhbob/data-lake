"""Rebuild the file-asset catalog (Blob/FileAsset/IngestEvent/Archive) from an
exported parse manifest, for the case where the live PostgreSQL catalog was lost
but the raw objects still exist in object storage (NAS) and the manifest CSV is
the surviving snapshot of the old catalog.

The manifest is the catalog's own inverse (see app/parse_manifest.py): each row
is keyed by sha256 and carries object_key + archive title/region/period/domain +
file role + original name + source url. This importer reconstructs catalog rows
that *reference* the existing NAS objects (no re-upload, no re-crawl) and reuses
the service-layer archive creation so all validation, business-key derivation,
dedup, file attachment and lineage are preserved.

Usage (source .env first, like the other CLIs):

    python -m app.manifest_catalog_rebuild ../manifests/latest/parse_manifest.csv
    python -m app.manifest_catalog_rebuild ../manifests/latest/parse_manifest.csv --max-rows 20
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.archive_rules import build_cost_info_business_key, metadata_value
from app.archive_service import create_archive_from_ingest_event
from app.collection import PLATFORM_PUBLIC_TENANT, create_data_source
from app.database import get_session_factory, init_db
from app.models import Archive, ArchiveFile, Blob, DataSource, FileAsset, IngestEvent
from app.normalization import normalize_key_text, normalize_source_url

DOMAIN_TYPE = "cost_info"
CHANNEL_TYPE = "crawler"  # VALID_CHANNEL_TYPES
COLLECTION_METHOD = "legacy_batch_import"  # VALID_COLLECTION_METHODS
ARCHIVE_STATUS = "collected"  # has a file attached; appears in 全部档案
SOURCE_TYPE = "info_price"
TAGGED_BY = "manifest_catalog_rebuild"
SOURCE_LEVEL = "crawler"
LEGACY_SOURCE_NAME = "历史信息价导入（未匹配来源）"
LEGACY_SITE_ID = "legacy_cost_info_import"
NAME_MAX = 255


@dataclass
class RebuildResult:
    total_rows: int = 0
    considered: int = 0  # cost_info + parse_ready rows
    archives_created: int = 0
    archives_attached: int = 0  # extra file attached to an existing archive
    skipped_existing: int = 0  # file already mounted to a cost_info archive
    skipped_cross_source_duplicate: int = 0  # same archive already exists under another source_id
    skipped_incomplete: int = 0  # not ready / missing key fields / wrong domain
    errors: int = 0
    blobs_created: int = 0
    assets_created: int = 0
    events_created: int = 0
    source_match: Counter = field(default_factory=Counter)
    error_samples: list[str] = field(default_factory=list)


def _split(values: str | None) -> list[str]:
    if not values:
        return []
    return [v.strip() for v in values.split("|") if v.strip()]


def _first(values: str | None) -> str | None:
    parts = _split(values)
    return parts[0] if parts else None


def _truncate(value: str | None, limit: int = NAME_MAX) -> str | None:
    if value is None:
        return None
    return value if len(value) <= limit else value[:limit]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _cell(value: object) -> dict[str, object]:
    return {"value": value, "source_level": SOURCE_LEVEL, "tagged_by": TAGGED_BY, "tagged_at": _now_iso()}


def _field_source() -> dict[str, object]:
    return {"source_level": SOURCE_LEVEL, "tagged_by": TAGGED_BY, "tagged_at": _now_iso()}


_MIME_BY_EXT = {
    ".pdf": "application/pdf",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".html": "text/html",
    ".htm": "text/html",
    ".zip": "application/zip",
    ".rar": "application/vnd.rar",
}


def _mime_for_ext(ext: str | None) -> str:
    return _MIME_BY_EXT.get((ext or "").lower(), "application/octet-stream")


class SourceMatcher:
    """Resolve a manifest archive to a real data_source row (the NOT-NULL FK).

    Manifest source_ids are stale UUIDs from the lost catalog, so we match by
    region_code (primary), province prefix, source name, and finally a legacy
    catch-all source created on demand so every archive has a valid FK.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.region_index: dict[str, str] = {}
        self.province_index: dict[str, list[DataSource]] = {}
        self.name_index: dict[str, str] = {}
        for source in session.scalars(select(DataSource).where(DataSource.data_domain == DOMAIN_TYPE)).all():
            if source.region_code:
                self.region_index.setdefault(source.region_code, source.source_id)
                self.province_index.setdefault(source.region_code[:2], []).append(source)
            if source.name:
                self.name_index.setdefault(source.name, source.source_id)
        self._legacy_id: str | None = None

    def _province_pick(self, prefix: str) -> str | None:
        candidates = self.province_index.get(prefix, [])
        if not candidates:
            return None
        for source in candidates:
            stable = (source.config or {}).get("stable") or {}
            if stable.get("publisher_scope") == "province":
                return source.source_id
        return candidates[0].source_id

    def _legacy(self) -> str:
        if self._legacy_id:
            return self._legacy_id
        existing = self.session.scalar(
            select(DataSource).where(
                DataSource.name == LEGACY_SOURCE_NAME,
                DataSource.data_domain == DOMAIN_TYPE,
            )
        )
        if existing is not None:
            self._legacy_id = existing.source_id
            return self._legacy_id
        source = create_data_source(
            self.session,
            source_scope="platform_public",
            tenant_code=None,
            managed_by="platform",
            source_type=SOURCE_TYPE,
            connector_type="source_registry",
            name=LEGACY_SOURCE_NAME,
            data_domain=DOMAIN_TYPE,
            status="active",
            config={"stable": {"site_id": LEGACY_SITE_ID, "domain_type": DOMAIN_TYPE}},
            created_by=TAGGED_BY,
        )
        self._legacy_id = source.source_id
        return self._legacy_id

    def _tenant(self, source_id: str) -> str:
        source = self.session.get(DataSource, source_id)
        return source.asset_tenant_code if source and source.asset_tenant_code else PLATFORM_PUBLIC_TENANT

    def match(self, region_code: str | None, name: str | None) -> tuple[str, str, str]:
        if region_code and region_code in self.region_index:
            sid = self.region_index[region_code]
            return sid, "region", self._tenant(sid)
        if region_code:
            sid = self._province_pick(region_code[:2])
            if sid:
                return sid, "province", self._tenant(sid)
        if name and name in self.name_index:
            sid = self.name_index[name]
            return sid, "name", self._tenant(sid)
        sid = self._legacy()
        return sid, "legacy", self._tenant(sid)


def _get_or_create_blob(
    session: Session,
    *,
    blob_hash: str,
    bucket: str,
    object_key: str,
    byte_size: int,
    mime_type: str | None,
    etag: str | None,
) -> tuple[Blob, bool]:
    existing = session.scalar(select(Blob).where(Blob.blob_hash == blob_hash))
    if existing is not None:
        return existing, False
    blob = Blob(
        blob_hash=blob_hash,
        storage_bucket=bucket,
        blob_storage_key=object_key,
        byte_size=byte_size,
        mime_type=mime_type or None,
        etag=etag or None,
    )
    session.add(blob)
    session.flush()
    return blob, True


def _get_or_create_file_asset(
    session: Session,
    *,
    tenant_code: str,
    bucket: str,
    object_key: str,
    sha256: str,
    file_name: str,
    file_ext: str,
    file_size: int,
    mime_type: str | None,
    etag: str | None,
    blob_hash: str,
) -> tuple[FileAsset, bool]:
    existing = session.scalar(
        select(FileAsset).where(FileAsset.tenant_code == tenant_code, FileAsset.sha256 == sha256)
    )
    if existing is not None:
        return existing, False
    asset = FileAsset(
        tenant_code=tenant_code,
        bucket=bucket,
        object_key=object_key,
        sha256=sha256,
        file_name=_truncate(file_name) or sha256,
        file_ext=file_ext or "",
        file_size=file_size,
        mime_type=mime_type or None,
        etag=etag or None,
        blob_hash=blob_hash,
    )
    session.add(asset)
    session.flush()
    return asset, True


def _get_or_create_ingest_event(
    session: Session,
    *,
    file_id: str,
    source_type: str,
    original_name: str,
    source_id: str,
    source_url: str | None,
    batch_id: str,
) -> tuple[IngestEvent, bool]:
    existing = session.scalar(
        select(IngestEvent).where(IngestEvent.file_id == file_id).order_by(IngestEvent.ingested_at.desc())
    )
    if existing is not None:
        return existing, False
    event = IngestEvent(
        file_id=file_id,
        source_type=source_type,
        original_name=_truncate(original_name) or file_id,
        source_id=source_id,
        source_url=source_url,
        batch_id=batch_id,
    )
    session.add(event)
    session.flush()
    return event, True


def _already_mounted_cost_info(session: Session, file_id: str) -> bool:
    return (
        session.scalar(
            select(ArchiveFile.archive_file_id)
            .join(Archive, Archive.archive_id == ArchiveFile.archive_id)
            .where(ArchiveFile.file_id == file_id, Archive.domain_type == DOMAIN_TYPE)
        )
        is not None
    )


def _archive_period(archive: Archive) -> str:
    if archive.coverage_period:
        return archive.coverage_period.strip()
    metadata = archive.metadata_payload or {}
    for key in ("period_start", "period", "period_raw"):
        value = metadata_value(metadata.get(key))
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _find_valid_cross_source_duplicate(
    session: Session,
    *,
    tenant_code: str,
    source_id: str,
    region_code: str,
    period: str,
    title: str,
    source_url: str | None,
    sha256: str,
) -> Archive | None:
    """Find the same valid archive even when a rebuild matched another data source.

    The normal business key contains ``source_id``.  A restored manifest can be
    matched to a duplicate source registry row, so the database uniqueness key
    alone cannot prevent a second archive.  Require matching business
    coordinates plus strong file evidence (canonical URL or SHA-256), and only
    reuse candidates that still have a valid FileAsset relationship.
    """

    normalized_title = normalize_key_text(title)
    normalized_url = normalize_source_url(source_url)
    candidates = session.scalars(
        select(Archive).where(
            Archive.tenant_code == tenant_code,
            Archive.domain_type == DOMAIN_TYPE,
            Archive.region_code == region_code,
            Archive.source_id != source_id,
            Archive.is_withdrawn.is_(False),
        )
    ).all()
    for candidate in candidates:
        if normalize_key_text(candidate.title) != normalized_title or _archive_period(candidate) != period:
            continue
        mounted_assets = session.execute(
            select(ArchiveFile, FileAsset)
            .join(FileAsset, ArchiveFile.file_id == FileAsset.file_id)
            .where(ArchiveFile.archive_id == candidate.archive_id)
        ).all()
        if not mounted_assets:
            continue
        url_matches = bool(normalized_url) and any(
            normalize_source_url(mounted.source_url or candidate.source_url) == normalized_url
            for mounted, _asset in mounted_assets
        )
        hash_matches = any(asset.sha256 == sha256 for _mounted, asset in mounted_assets)
        if url_matches or hash_matches:
            return candidate
    return None


def rebuild_from_manifest(
    session: Session,
    csv_path: str | Path,
    *,
    max_rows: int | None = None,
) -> RebuildResult:
    result = RebuildResult()
    matcher = SourceMatcher(session)
    with Path(csv_path).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    result.total_rows = len(rows)

    for row in rows:
        domain = _first(row.get("domain_types"))
        if domain != DOMAIN_TYPE:
            continue
        if (row.get("parse_ready") or "").strip() != "true":
            result.skipped_incomplete += 1
            continue
        if max_rows is not None and result.considered >= max_rows:
            break
        result.considered += 1
        sha = ""
        try:
            sha = (row.get("sha256") or "").strip()
            object_key = (row.get("object_key") or "").strip()
            bucket = (row.get("bucket") or "cost-raw").strip()
            if not sha or not object_key:
                result.skipped_incomplete += 1
                continue

            byte_size = int(row.get("byte_size") or 0)
            file_ext = (row.get("file_ext") or "").strip()
            mime_type = (row.get("mime_type") or "").strip() or _mime_for_ext(file_ext)
            etag = (row.get("etag") or "").strip()

            title = (
                _first(row.get("archive_titles"))
                or _first(row.get("period_raws"))
                or _first(row.get("original_names"))
                or sha
            )
            region_code = _first(row.get("region_codes"))
            period_start = _first(row.get("period_starts"))
            period_end = _first(row.get("period_ends"))
            period_raw = _first(row.get("period_raws"))
            publish_date = _first(row.get("publish_dates"))
            original_name = _first(row.get("original_names")) or _first(row.get("file_names")) or sha
            source_url = _first(row.get("source_urls"))
            source_name = _first(row.get("source_names"))

            if not title or not region_code or not (period_start or period_raw):
                result.skipped_incomplete += 1
                continue

            source_id, match_kind, tenant_code = matcher.match(region_code, source_name)
            result.source_match[match_kind] += 1

            period_for_key = period_start or period_raw
            duplicate = _find_valid_cross_source_duplicate(
                session,
                tenant_code=tenant_code,
                source_id=source_id,
                region_code=region_code,
                period=period_for_key,
                title=title,
                source_url=source_url,
                sha256=sha,
            )
            if duplicate is not None:
                result.skipped_cross_source_duplicate += 1
                continue

            blob, blob_new = _get_or_create_blob(
                session,
                blob_hash=sha,
                bucket=bucket,
                object_key=object_key,
                byte_size=byte_size,
                mime_type=mime_type,
                etag=etag,
            )
            if blob_new:
                result.blobs_created += 1
            asset, asset_new = _get_or_create_file_asset(
                session,
                tenant_code=tenant_code,
                bucket=bucket,
                object_key=object_key,
                sha256=sha,
                file_name=original_name,
                file_ext=file_ext,
                file_size=byte_size,
                mime_type=mime_type,
                etag=etag,
                blob_hash=sha,
            )
            if asset_new:
                result.assets_created += 1
            event, event_new = _get_or_create_ingest_event(
                session,
                file_id=asset.file_id,
                source_type=SOURCE_TYPE,
                original_name=original_name,
                source_id=source_id,
                source_url=source_url,
                batch_id=f"manifest_rebuild:{sha[:12]}",
            )
            if event_new:
                result.events_created += 1

            if _already_mounted_cost_info(session, asset.file_id):
                result.skipped_existing += 1
                continue

            metadata: dict[str, object] = {}
            if period_start:
                metadata["period_start"] = _cell(period_start)
            if period_end:
                metadata["period_end"] = _cell(period_end)
            if period_raw:
                metadata["period_raw"] = _cell(period_raw)

            field_sources: dict[str, object] = {
                "domain_type": _field_source(),
                "channel_type": _field_source(),
                "title": _field_source(),
            }
            if region_code:
                field_sources["region_code"] = _field_source()
            if publish_date:
                field_sources["publish_date"] = _field_source()

            business_key = build_cost_info_business_key(
                source_id=source_id,
                region_code=region_code,
                period=period_for_key,
                title=title,
            )
            existed = session.scalar(
                select(Archive).where(
                    Archive.tenant_code == tenant_code,
                    Archive.domain_type == DOMAIN_TYPE,
                    Archive.business_key == business_key,
                    Archive.version == 1,
                )
            )

            create_archive_from_ingest_event(
                session,
                event_id=event.event_id,
                domain_type=DOMAIN_TYPE,
                channel_type=CHANNEL_TYPE,
                collection_method=COLLECTION_METHOD,
                status=ARCHIVE_STATUS,
                title=title,
                region_code=region_code,
                publish_date=publish_date,
                metadata=metadata,
                field_sources=field_sources,
                actor_type="system",
                actor_id=TAGGED_BY,
            )
            if existed is not None:
                result.archives_attached += 1
            else:
                result.archives_created += 1
        except Exception as exc:  # noqa: BLE001 - log and continue per row
            result.errors += 1
            session.rollback()
            if len(result.error_samples) < 12:
                result.error_samples.append(f"{sha[:12] or '?'}: {type(exc).__name__}: {str(exc)[:180]}")
            continue

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild file-asset catalog from a parse manifest CSV.")
    parser.add_argument("csv_path", type=Path, help="Path to parse_manifest.csv")
    parser.add_argument("--max-rows", type=int, default=None, help="Process at most N cost_info ready rows (pilot)")
    args = parser.parse_args()

    init_db()
    session_factory = get_session_factory()
    with session_factory() as session:
        result = rebuild_from_manifest(session, args.csv_path, max_rows=args.max_rows)

    print(f"total_rows            = {result.total_rows}")
    print(f"considered (ready ci) = {result.considered}")
    print(f"archives_created      = {result.archives_created}")
    print(f"archives_attached     = {result.archives_attached}")
    print(f"skipped_existing      = {result.skipped_existing}")
    print(f"skipped_cross_source  = {result.skipped_cross_source_duplicate}")
    print(f"skipped_incomplete    = {result.skipped_incomplete}")
    print(f"errors                = {result.errors}")
    print(f"blobs_created         = {result.blobs_created}")
    print(f"assets_created        = {result.assets_created}")
    print(f"events_created        = {result.events_created}")
    print(f"source_match          = {dict(result.source_match)}")
    if result.error_samples:
        print("error_samples:")
        for sample in result.error_samples:
            print(f"  - {sample}")
    return 0 if result.errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
