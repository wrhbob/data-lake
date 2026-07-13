from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_session_factory, init_db
from app.models import Archive, ArchiveFile, Blob, CollectionTask, DataSource, FileAsset, IngestEvent

MANIFEST_SCHEMA_VERSION = "parse_manifest.v1"

PARSE_MANIFEST_FIELDS = [
    "manifest_schema_version",
    "bucket",
    "object_key",
    "nas_path",
    "sha256",
    "file_ext",
    "mime_type",
    "byte_size",
    "etag",
    "blob_created_at",
    "file_asset_count",
    "ingest_event_count",
    "file_ids",
    "archive_ids",
    "file_names",
    "original_names",
    "resolved_provinces",
    "resolved_regions",
    "source_provinces",
    "source_cities",
    "region_codes",
    "archive_titles",
    "domain_types",
    "file_roles",
    "period_kinds",
    "publish_dates",
    "period_starts",
    "period_ends",
    "period_raws",
    "source_urls",
    "source_names",
    "source_ids",
    "task_ids",
    "batch_ids",
    "ingested_ats",
    "parse_ready",
    "missing_fields",
]


RegionMap = dict[str, tuple[str, str]]


@dataclass(frozen=True)
class ExportParseManifestResult:
    output_path: Path
    output_format: str
    row_count: int
    ready_count: int
    not_ready_count: int


def build_parse_manifest_rows(
    session: Session,
    *,
    bucket: str = "cost-raw",
    nas_root: str | Path | None = None,
    region_map: RegionMap | None = None,
    domain_type: str | None = None,
) -> list[dict[str, str]]:
    records: dict[str, _ObjectContext] = {}
    # SQLAlchemy cannot portably outer-join DataSource on a Python-side coalesce
    # across both SQLite and PostgreSQL here, so fetch sources once and resolve
    # source_id precedence while folding rows.
    sources = {source.source_id: source for source in session.scalars(select(DataSource)).all()}
    statement = (
        select(Blob, FileAsset, IngestEvent, CollectionTask, ArchiveFile, Archive)
        .select_from(Blob)
        .outerjoin(FileAsset, FileAsset.blob_hash == Blob.blob_hash)
        .outerjoin(IngestEvent, IngestEvent.file_id == FileAsset.file_id)
        .outerjoin(CollectionTask, CollectionTask.task_id == IngestEvent.task_id)
        .outerjoin(ArchiveFile, ArchiveFile.file_id == FileAsset.file_id)
        .outerjoin(Archive, Archive.archive_id == ArchiveFile.archive_id)
        .where(Blob.storage_bucket == bucket)
        .order_by(Blob.blob_storage_key)
    )
    if domain_type is not None:
        statement = statement.where(Archive.domain_type == domain_type)

    for blob, asset, event, task, archive_file, archive in session.execute(statement).all():
        context = records.setdefault(
            blob.blob_hash,
            _ObjectContext(blob=blob, nas_root=str(nas_root) if nas_root is not None else None),
        )
        context.add_asset(asset)
        context.add_event(event)
        context.add_task(task)
        context.add_archive_file(archive_file)
        context.add_archive(archive, region_map=region_map or {})
        source_id = _first_nonempty(
            getattr(event, "source_id", None),
            getattr(task, "source_id", None),
            getattr(archive, "source_id", None),
        )
        if source_id:
            context.add_source(sources.get(source_id))

    return [context.to_row() for context in records.values()]


def export_parse_manifest(
    session: Session,
    output_path: str | Path,
    *,
    output_format: str = "csv",
    bucket: str = "cost-raw",
    nas_root: str | Path | None = None,
    region_map: RegionMap | None = None,
    region_map_path: str | Path | None = None,
    domain_type: str | None = None,
) -> ExportParseManifestResult:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    resolved_region_map = dict(region_map or {})
    if region_map_path is not None:
        resolved_region_map.update(load_region_map(region_map_path))

    rows = build_parse_manifest_rows(
        session,
        bucket=bucket,
        nas_root=nas_root,
        region_map=resolved_region_map,
        domain_type=domain_type,
    )

    normalized_format = output_format.lower()
    if normalized_format == "csv":
        with output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=PARSE_MANIFEST_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
    elif normalized_format == "jsonl":
        with output.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    else:
        raise ValueError("output_format must be 'csv' or 'jsonl'")

    ready_count = sum(1 for row in rows if row["parse_ready"] == "true")
    return ExportParseManifestResult(
        output_path=output,
        output_format=normalized_format,
        row_count=len(rows),
        ready_count=ready_count,
        not_ready_count=len(rows) - ready_count,
    )


def load_region_map(path: str | Path) -> RegionMap:
    mapping: RegionMap = {}
    with Path(path).open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            region_code = (row.get("target_region_code") or "").strip()
            province_name = (row.get("province_name") or "").strip()
            region_name = (row.get("target_region_name") or "").strip()
            if region_code and (province_name or region_name):
                mapping[region_code] = (province_name, region_name)
    return mapping


class _ObjectContext:
    def __init__(self, *, blob: Blob, nas_root: str | None) -> None:
        self.blob = blob
        self.nas_root = nas_root
        self.file_asset_ids: set[str] = set()
        self.ingest_event_ids: set[str] = set()
        self.values: dict[str, set[str]] = {
            key: set()
            for key in (
                "file_ids",
                "archive_ids",
                "file_names",
                "original_names",
                "resolved_provinces",
                "resolved_regions",
                "source_provinces",
                "source_cities",
                "region_codes",
                "archive_titles",
                "domain_types",
                "file_roles",
                "period_kinds",
                "publish_dates",
                "period_starts",
                "period_ends",
                "period_raws",
                "source_urls",
                "source_names",
                "source_ids",
                "task_ids",
                "batch_ids",
                "ingested_ats",
            )
        }

    def add_asset(self, asset: FileAsset | None) -> None:
        if asset is None:
            return
        self.file_asset_ids.add(asset.file_id)
        self._add("file_ids", asset.file_id)
        self._add("file_names", asset.file_name)

    def add_event(self, event: IngestEvent | None) -> None:
        if event is None:
            return
        self.ingest_event_ids.add(event.event_id)
        self._add("original_names", event.original_name)
        self._add("source_urls", event.source_url)
        self._add("source_ids", event.source_id)
        self._add("task_ids", event.task_id)
        self._add("batch_ids", event.batch_id)
        self._add("ingested_ats", _isoformat(event.ingested_at))

    def add_task(self, task: CollectionTask | None) -> None:
        if task is None:
            return
        self._add("task_ids", task.task_id)
        self._add("batch_ids", task.batch_id)
        self._add("period_starts", task.period_start)
        self._add("period_ends", task.period_end)
        self._add("period_raws", task.period_raw)

    def add_archive_file(self, archive_file: ArchiveFile | None) -> None:
        if archive_file is None:
            return
        self._add("file_roles", archive_file.file_role)

    def add_archive(self, archive: Archive | None, *, region_map: RegionMap) -> None:
        if archive is None:
            return
        self._add("archive_ids", archive.archive_id)
        self._add("archive_titles", archive.title)
        self._add("region_codes", archive.region_code)
        self._add("domain_types", archive.domain_type)
        self._add("period_kinds", archive.period_kind)
        self._add("publish_dates", _date_to_string(archive.publish_date))
        self._add("source_urls", archive.source_url)
        self._add("source_ids", archive.source_id)
        self._add("task_ids", archive.task_id)
        self._add("batch_ids", archive.batch_id)

        metadata = archive.metadata_payload or {}
        self._add("period_starts", _metadata_value(metadata.get("period_start")) or _metadata_value(metadata.get("period")))
        self._add("period_ends", _metadata_value(metadata.get("period_end")))
        self._add("period_raws", _metadata_value(metadata.get("period_raw")) or archive.title)

        if archive.region_code and archive.region_code in region_map:
            province_name, region_name = region_map[archive.region_code]
            self._add("resolved_provinces", province_name)
            self._add("resolved_regions", region_name)

    def add_source(self, source: DataSource | None) -> None:
        if source is None:
            return
        self._add("source_names", source.name)
        self._add("source_ids", source.source_id)
        self._add("source_provinces", source.province)
        self._add("source_cities", source.city)
        self._add("resolved_provinces", source.province)
        self._add("resolved_regions", source.city)

    def to_row(self) -> dict[str, str]:
        missing_fields = self._missing_fields()
        row = {
            "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
            "bucket": self.blob.storage_bucket,
            "object_key": self.blob.blob_storage_key,
            "nas_path": self._nas_path(),
            "sha256": self.blob.blob_hash,
            "file_ext": _file_ext(self.blob.blob_storage_key),
            "mime_type": self.blob.mime_type or "",
            "byte_size": str(self.blob.byte_size),
            "etag": self.blob.etag or "",
            "blob_created_at": _isoformat(self.blob.created_at),
            "file_asset_count": str(len(self.file_asset_ids)),
            "ingest_event_count": str(len(self.ingest_event_ids)),
            "parse_ready": "false" if missing_fields else "true",
            "missing_fields": "|".join(missing_fields),
        }
        for key in self.values:
            row[key] = _join_values(self.values[key])
        return {field: row.get(field, "") for field in PARSE_MANIFEST_FIELDS}

    def _missing_fields(self) -> list[str]:
        missing = []
        if not self.values["archive_ids"]:
            missing.append("archive_ids")
        if not self.values["original_names"] and not self.values["file_names"]:
            missing.append("original_names")
        if not self.values["period_starts"] and not self.values["period_raws"]:
            missing.append("period")
        if not self.values["resolved_regions"] and not self.values["region_codes"]:
            missing.append("region")
        return missing

    def _nas_path(self) -> str:
        if not self.nas_root:
            return ""
        return f"{self.nas_root.rstrip('/')}/{self.blob.blob_storage_key}"

    def _add(self, key: str, value: object | None) -> None:
        if value is None:
            return
        text = str(value).strip()
        if text:
            self.values[key].add(text)


def _metadata_value(value: Any) -> str | None:
    if isinstance(value, dict):
        raw = value.get("value")
        return None if raw is None else str(raw)
    if value is None:
        return None
    return str(value)


def _join_values(values: set[str]) -> str:
    return " | ".join(sorted(values))


def _isoformat(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _date_to_string(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _file_ext(object_key: str) -> str:
    name = Path(object_key).name
    return Path(name).suffix.lower()


def _first_nonempty(*values: object | None) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export parser manifest from file asset catalog.")
    parser.add_argument("output_path", help="Target .csv or .jsonl path")
    parser.add_argument("--format", choices=("csv", "jsonl"), default=None, help="Output format")
    parser.add_argument("--bucket", default="cost-raw", help="Source bucket to export")
    parser.add_argument("--nas-root", default=None, help="NAS root prefix, e.g. /nas/cost-raw")
    parser.add_argument("--region-map", default=None, help="CSV with target_region_code/province_name/target_region_name")
    parser.add_argument("--domain-type", default=None, help="Optional archive domain filter, e.g. cost_info")
    args = parser.parse_args(argv)

    output_path = Path(args.output_path)
    output_format = args.format or ("jsonl" if output_path.suffix.lower() == ".jsonl" else "csv")

    init_db()
    Session = get_session_factory()
    with Session() as session:
        result = export_parse_manifest(
            session,
            output_path,
            output_format=output_format,
            bucket=args.bucket,
            nas_root=args.nas_root,
            region_map_path=args.region_map,
            domain_type=args.domain_type,
        )

    print(
        json.dumps(
            {
                "output_path": str(result.output_path),
                "output_format": result.output_format,
                "row_count": result.row_count,
                "ready_count": result.ready_count,
                "not_ready_count": result.not_ready_count,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
