from __future__ import annotations

from datetime import UTC, datetime, timedelta

import boto3
import pytest
from botocore.exceptions import ClientError

from app.models import Archive, ArchiveFile, FileAsset
from app.storage import FakeObjectStore, S3ObjectStore


def test_fake_object_store_stat_object_returns_size_and_metadata():
    storage = FakeObjectStore()
    etag = storage.put_object("cost-raw", "objects/aa/bb/test.pdf", b"abcdef", content_type="application/pdf")

    stat = storage.stat_object("cost-raw", "objects/aa/bb/test.pdf")

    assert stat.byte_size == 6
    assert stat.etag == etag
    assert stat.content_type == "application/pdf"


def test_fake_object_store_stat_object_raises_key_error_for_missing():
    storage = FakeObjectStore()

    with pytest.raises(KeyError):
        storage.stat_object("cost-raw", "objects/missing.pdf")


def test_s3_object_store_stat_object_maps_missing_key_to_key_error(monkeypatch):
    class MissingObjectClient:
        def head_object(self, *, Bucket, Key):
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "HeadObject")

        def get_object(self, *, Bucket, Key):
            raise AssertionError("stat_object must not download object bytes")

    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: MissingObjectClient())
    store = S3ObjectStore(
        endpoint_url="http://minio.example",
        access_key_id="access",
        secret_access_key="secret",
        region_name="us-east-1",
    )

    with pytest.raises(KeyError):
        store.stat_object("cost-raw", "missing.pdf")


def test_audit_file_asset_objects_counts_ok_missing_and_size_mismatch(db_session):
    from app.storage_audit import audit_file_asset_objects

    storage = FakeObjectStore()
    now = datetime(2026, 6, 30, 9, 0, tzinfo=UTC)
    ok = add_asset(db_session, "成都市2026年5月信息价.pdf", "ok.pdf", 4, created_at=now)
    missing = add_asset(db_session, "武汉市2026年5月信息价.pdf", "missing.pdf", 6, created_at=now - timedelta(minutes=1))
    mismatch = add_asset(db_session, "重庆市2026年5月信息价.pdf", "mismatch.pdf", 8, created_at=now - timedelta(minutes=2))
    storage.put_object(ok.bucket, ok.object_key, b"1234", content_type="application/pdf")
    storage.put_object(mismatch.bucket, mismatch.object_key, b"123", content_type="application/pdf")

    result = audit_file_asset_objects(db_session, storage)

    assert result["checked_count"] == 3
    assert result["ok_count"] == 1
    assert result["missing_count"] == 1
    assert result["size_mismatch_count"] == 1
    assert result["error_count"] == 0
    assert result["expected_bytes"] == 18
    assert result["available_bytes"] == 7
    assert [issue["status"] for issue in result["issues"]] == ["missing", "size_mismatch"]
    assert result["issues"][0]["file_id"] == missing.file_id
    assert result["issues"][0]["actual_size"] is None
    assert result["issues"][1]["file_id"] == mismatch.file_id
    assert result["issues"][1]["actual_size"] == 3


def test_audit_file_asset_objects_filters_by_archive_domain_and_region(db_session):
    from app.storage_audit import audit_file_asset_objects

    storage = FakeObjectStore()
    now = datetime(2026, 6, 30, 10, 0, tzinfo=UTC)
    chengdu = add_asset(db_session, "成都市2026年5月信息价.pdf", "chengdu.pdf", 10, created_at=now)
    wuhan = add_asset(db_session, "武汉市2026年5月信息价.pdf", "wuhan.pdf", 5, created_at=now - timedelta(minutes=1))
    storage.put_object(chengdu.bucket, chengdu.object_key, b"short", content_type="application/pdf")
    storage.put_object(wuhan.bucket, wuhan.object_key, b"12345", content_type="application/pdf")
    add_archive_with_file(
        db_session,
        asset=chengdu,
        title="成都市2026年5月信息价",
        domain_type="cost_info",
        region_code="510100",
    )
    add_archive_with_file(
        db_session,
        asset=wuhan,
        title="武汉市2026年5月信息价",
        domain_type="cost_info",
        region_code="420100",
    )

    result = audit_file_asset_objects(db_session, storage, domain_type="cost_info", region_code="510100")

    assert result["checked_count"] == 1
    assert result["size_mismatch_count"] == 1
    assert result["missing_count"] == 0
    assert result["issues"] == [
        {
            "status": "size_mismatch",
            "file_id": chengdu.file_id,
            "file_name": "成都市2026年5月信息价.pdf",
            "bucket": "cost-raw",
            "object_key": "objects/chengdu.pdf",
            "expected_size": 10,
            "actual_size": 5,
            "archive_title": "成都市2026年5月信息价",
            "region_code": "510100",
        }
    ]


def test_audit_file_asset_objects_scoped_audit_includes_collected_and_excludes_pending_matches(db_session):
    from app.storage_audit import audit_file_asset_objects

    storage = FakeObjectStore()
    now = datetime(2026, 6, 30, 11, 0, tzinfo=UTC)
    pending = add_asset(db_session, "成都市2026年6月信息价-待确认.pdf", "pending-chengdu.pdf", 7, created_at=now)
    collected = add_asset(
        db_session,
        "成都市2026年6月信息价-已采集.pdf",
        "collected-chengdu.pdf",
        4,
        created_at=now - timedelta(minutes=1),
    )
    storage.put_object(collected.bucket, collected.object_key, b"1234", content_type="application/pdf")
    add_archive_with_file(
        db_session,
        asset=pending,
        title="成都市2026年6月信息价-待确认",
        domain_type="cost_info",
        region_code="510100",
        status="pending_tag",
    )
    add_archive_with_file(
        db_session,
        asset=collected,
        title="成都市2026年6月信息价-已采集",
        domain_type="cost_info",
        region_code="510100",
        status="collected",
    )

    result = audit_file_asset_objects(db_session, storage, domain_type="cost_info", region_code="510100")

    assert result["checked_count"] == 1
    assert result["ok_count"] == 1
    assert result["missing_count"] == 0
    assert result["issues"] == []


def test_audit_file_asset_objects_reports_orphan_archive_file_references(db_session):
    from app.storage_audit import audit_file_asset_objects

    archive = Archive(
        domain_type="cost_info",
        channel_type="crawler",
        collection_method="legacy_batch_import",
        business_key="cost_info:orphan:110000:2025-12",
        title="2025年12月北京工程造价信息",
        region_code="110000",
        source_id="source-beijing",
        tenant_code="platform_public",
        visibility_scope="public",
        status="archived",
        metadata_payload={},
        field_sources={},
    )
    db_session.add(archive)
    db_session.flush()
    mounted = ArchiveFile(
        archive_id=archive.archive_id,
        file_id="missing-file-asset-id",
        file_role="main_document",
        is_primary=True,
        display_name="2025年12月北京工程造价信息.pdf",
    )
    db_session.add(mounted)
    db_session.commit()

    result = audit_file_asset_objects(db_session, FakeObjectStore())

    assert result["checked_count"] == 0
    assert result["orphan_reference_count"] == 1
    assert result["orphan_archive_count"] == 1
    assert result["issues"] == [
        {
            "status": "orphan_file_reference",
            "archive_id": archive.archive_id,
            "archive_title": archive.title,
            "region_code": "110000",
            "archive_file_id": mounted.archive_file_id,
            "file_id": "missing-file-asset-id",
            "file_name": "2025年12月北京工程造价信息.pdf",
        }
    ]


def test_audit_file_asset_objects_ignores_orphans_on_withdrawn_archives(db_session):
    from app.storage_audit import audit_file_asset_objects

    archive = Archive(
        domain_type="cost_info",
        channel_type="crawler",
        collection_method="legacy_batch_import",
        business_key="cost_info:withdrawn-orphan:110000:2025-12",
        title="已撤下的重复信息价",
        region_code="110000",
        source_id="source-beijing",
        tenant_code="platform_public",
        visibility_scope="public",
        status="archived",
        is_withdrawn=True,
        is_current=False,
        metadata_payload={},
        field_sources={},
    )
    db_session.add(archive)
    db_session.flush()
    db_session.add(
        ArchiveFile(
            archive_id=archive.archive_id,
            file_id="missing-file-on-withdrawn-archive",
            file_role="main_document",
            is_primary=True,
        )
    )
    db_session.commit()

    result = audit_file_asset_objects(db_session, FakeObjectStore())

    assert result["orphan_reference_count"] == 0
    assert result["orphan_archive_count"] == 0
    assert result["issues"] == []


def add_asset(db_session, file_name: str, object_name: str, file_size: int, *, created_at: datetime) -> FileAsset:
    asset = FileAsset(
        tenant_code="platform_public",
        bucket="cost-raw",
        object_key=f"objects/{object_name}",
        sha256=f"{object_name:0<64}"[:64],
        file_name=file_name,
        file_ext=".pdf",
        mime_type="application/pdf",
        file_size=file_size,
        created_at=created_at,
    )
    db_session.add(asset)
    db_session.commit()
    return asset


def add_archive_with_file(
    db_session,
    *,
    asset: FileAsset,
    title: str,
    domain_type: str,
    region_code: str,
    status: str = "archived",
) -> Archive:
    archive = Archive(
        domain_type=domain_type,
        channel_type="crawler",
        collection_method="auto",
        business_key=f"{domain_type}:audit:{asset.file_id}",
        title=title,
        region_code=region_code,
        source_id=f"source-{region_code}",
        tenant_code="platform_public",
        visibility_scope="public",
        status=status,
        metadata_payload={},
        field_sources={},
    )
    db_session.add(archive)
    db_session.flush()
    db_session.add(
        ArchiveFile(
            archive_id=archive.archive_id,
            file_id=asset.file_id,
            file_role="main_document",
            is_primary=True,
            display_name=asset.file_name,
        )
    )
    db_session.commit()
    return archive
