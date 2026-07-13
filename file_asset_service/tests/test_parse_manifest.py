from __future__ import annotations

import csv
import json
from datetime import UTC, datetime

from app.models import Archive, ArchiveFile, Blob, CollectionTask, DataSource, FileAsset, IngestEvent
from app.parse_manifest import build_parse_manifest_rows, export_parse_manifest


def cell(value: str | None) -> dict[str, str | None]:
    return {
        "value": value,
        "source_level": "crawler",
        "tagged_by": "test",
        "tagged_at": "2026-06-30T00:00:00+00:00",
    }


def test_build_parse_manifest_rows_joins_blob_to_business_context(db_session):
    source = add_source(db_session)
    task = CollectionTask(
        source_id=source.source_id,
        asset_tenant_code="platform_public",
        operator_type="platform_ops",
        task_type="crawl_issue",
        trigger_type="manual",
        batch_id="cost_info:chengdu:20260630:001",
        data_domain="cost_info",
        status="done",
        period_start="2026-05",
        period_raw="2026年第5期",
    )
    db_session.add(task)
    db_session.flush()
    blob = add_blob(db_session)
    asset = add_asset(db_session, blob)
    archive = Archive(
        domain_type="cost_info",
        channel_type="crawler",
        collection_method="auto",
        price_kind="guidance",
        period_kind="monthly",
        business_key="cost_info:510100:2026-05:chengdu",
        title="成都市2026年第5期建设工程造价信息",
        region_code="510100",
        publish_date=datetime(2026, 6, 12, tzinfo=UTC).date(),
        source_id=source.source_id,
        task_id=task.task_id,
        batch_id=task.batch_id,
        source_url="https://example.gov.cn/cost/2026-05.pdf",
        tenant_code="platform_public",
        visibility_scope="public",
        status="archived",
        metadata_payload={
            "period_start": cell("2026-05"),
            "period_raw": cell("2026年第5期"),
        },
        field_sources={},
    )
    db_session.add(archive)
    db_session.flush()
    db_session.add(
        ArchiveFile(
            archive_id=archive.archive_id,
            file_id=asset.file_id,
            file_role="main_document",
            display_name=asset.file_name,
            is_primary=True,
        )
    )
    db_session.add(
        IngestEvent(
            file_id=asset.file_id,
            source_id=source.source_id,
            task_id=task.task_id,
            source_type="info_price",
            batch_id=task.batch_id,
            source_url="https://example.gov.cn/cost/2026-05.pdf",
            original_name="成都市2026年第5期建设工程造价信息.pdf",
            ingested_at=datetime(2026, 6, 30, 8, 0, tzinfo=UTC),
        )
    )
    db_session.commit()

    rows = build_parse_manifest_rows(
        db_session,
        nas_root="/nas/cost-raw",
        region_map={"510100": ("四川省", "成都市")},
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["manifest_schema_version"] == "parse_manifest.v1"
    assert row["bucket"] == "cost-raw"
    assert row["object_key"] == blob.blob_storage_key
    assert row["nas_path"] == "/nas/cost-raw/objects/aa/bb/" + blob.blob_hash + ".pdf"
    assert row["sha256"] == blob.blob_hash
    assert row["original_names"] == "成都市2026年第5期建设工程造价信息.pdf"
    assert row["resolved_provinces"] == "四川省"
    assert row["resolved_regions"] == "成都市"
    assert row["region_codes"] == "510100"
    assert row["period_starts"] == "2026-05"
    assert row["period_raws"] == "2026年第5期"
    assert row["source_urls"] == "https://example.gov.cn/cost/2026-05.pdf"
    assert row["source_names"] == "成都信息价公开源"
    assert row["archive_ids"] == archive.archive_id
    assert row["file_ids"] == asset.file_id
    assert row["task_ids"] == task.task_id
    assert row["parse_ready"] == "true"
    assert row["missing_fields"] == ""


def test_build_parse_manifest_rows_marks_orphan_blob_as_not_ready(db_session):
    blob = add_blob(db_session, blob_hash="b" * 64, object_key="objects/bb/bb/" + "b" * 64 + ".xlsx")

    rows = build_parse_manifest_rows(db_session, nas_root="/nas/cost-raw")

    assert rows == [
        {
            "manifest_schema_version": "parse_manifest.v1",
            "bucket": "cost-raw",
            "object_key": blob.blob_storage_key,
            "nas_path": "/nas/cost-raw/objects/bb/bb/" + "b" * 64 + ".xlsx",
            "sha256": "b" * 64,
            "file_ext": ".xlsx",
            "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "byte_size": "88",
            "etag": "etag-" + "b" * 8,
            "blob_created_at": "2026-06-30T08:00:00+00:00",
            "file_asset_count": "0",
            "ingest_event_count": "0",
            "file_ids": "",
            "archive_ids": "",
            "file_names": "",
            "original_names": "",
            "resolved_provinces": "",
            "resolved_regions": "",
            "source_provinces": "",
            "source_cities": "",
            "region_codes": "",
            "archive_titles": "",
            "domain_types": "",
            "file_roles": "",
            "period_kinds": "",
            "publish_dates": "",
            "period_starts": "",
            "period_ends": "",
            "period_raws": "",
            "source_urls": "",
            "source_names": "",
            "source_ids": "",
            "task_ids": "",
            "batch_ids": "",
            "ingested_ats": "",
            "parse_ready": "false",
            "missing_fields": "archive_ids|original_names|period|region",
        }
    ]


def test_export_parse_manifest_writes_csv_and_jsonl(db_session, tmp_path):
    blob = add_blob(db_session)
    asset = add_asset(db_session, blob)
    db_session.add(
        IngestEvent(
            file_id=asset.file_id,
            source_type="info_price",
            batch_id="manual:001",
            original_name=asset.file_name,
            ingested_at=datetime(2026, 6, 30, 8, 30, tzinfo=UTC),
        )
    )
    db_session.commit()

    csv_result = export_parse_manifest(db_session, tmp_path / "parse_manifest.csv", output_format="csv")
    jsonl_result = export_parse_manifest(db_session, tmp_path / "parse_manifest.jsonl", output_format="jsonl")

    assert csv_result.row_count == 1
    assert jsonl_result.row_count == 1

    with (tmp_path / "parse_manifest.csv").open(encoding="utf-8") as handle:
        csv_rows = list(csv.DictReader(handle))
    assert csv_rows[0]["object_key"] == blob.blob_storage_key
    assert csv_rows[0]["file_names"] == asset.file_name

    jsonl_rows = [
        json.loads(line)
        for line in (tmp_path / "parse_manifest.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert jsonl_rows[0]["object_key"] == blob.blob_storage_key
    assert jsonl_rows[0]["file_names"] == asset.file_name


def add_source(db_session) -> DataSource:
    source = DataSource(
        source_scope="platform_public",
        asset_tenant_code="platform_public",
        managed_by="platform",
        source_type="info_price",
        connector_type="http_site",
        name="成都信息价公开源",
        province="四川省",
        city="成都市",
        region_code="510100",
        data_domain="cost_info",
        status="active",
        config={},
    )
    db_session.add(source)
    db_session.flush()
    return source


def add_blob(
    db_session,
    *,
    blob_hash: str = "a" * 64,
    object_key: str | None = None,
) -> Blob:
    key = object_key or f"objects/aa/bb/{blob_hash}.pdf"
    blob = Blob(
        blob_hash=blob_hash,
        storage_bucket="cost-raw",
        blob_storage_key=key,
        byte_size=88,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if key.endswith(".xlsx")
        else "application/pdf",
        etag="etag-" + blob_hash[:8],
        created_at=datetime(2026, 6, 30, 8, 0, tzinfo=UTC),
    )
    db_session.add(blob)
    db_session.flush()
    return blob


def add_asset(db_session, blob: Blob) -> FileAsset:
    asset = FileAsset(
        tenant_code="platform_public",
        bucket=blob.storage_bucket,
        object_key=blob.blob_storage_key,
        sha256=blob.blob_hash,
        blob_hash=blob.blob_hash,
        file_name="成都市2026年第5期建设工程造价信息.pdf",
        file_ext=".pdf",
        mime_type=blob.mime_type,
        file_size=blob.byte_size,
        etag=blob.etag,
    )
    db_session.add(asset)
    db_session.flush()
    return asset
