import hashlib

from app.assets import register_asset
from app.models import Blob, FileAsset, IngestEvent


def test_duplicate_in_same_tenant_reuses_asset_and_records_new_ingest(db_session, fake_storage):
    content = b"same info price workbook bytes"

    first = register_asset(
        db_session,
        fake_storage,
        tenant_code="tenant_a",
        source_type="info_price",
        batch_id="batch-001",
        file_name="2024-01-info-price.xlsx",
        content=content,
    )
    second = register_asset(
        db_session,
        fake_storage,
        tenant_code="tenant_a",
        source_type="info_price",
        batch_id="batch-002",
        file_name="2024-01-info-price-copy.xlsx",
        content=content,
    )

    assert first.file_id == second.file_id
    assert first.duplicated is False
    assert second.duplicated is True
    assert first.ingest_event_id != second.ingest_event_id
    assert fake_storage.put_count == 1
    assert db_session.query(FileAsset).count() == 1
    assert db_session.query(IngestEvent).filter_by(file_id=first.file_id).count() == 2


def test_same_sha_in_different_tenants_creates_separate_assets(db_session, fake_storage):
    content = b"cross tenant same file bytes"

    first = register_asset(
        db_session,
        fake_storage,
        tenant_code="tenant_a",
        source_type="info_price",
        batch_id="batch-001",
        file_name="same.xlsx",
        content=content,
    )
    second = register_asset(
        db_session,
        fake_storage,
        tenant_code="tenant_b",
        source_type="info_price",
        batch_id="batch-001",
        file_name="same.xlsx",
        content=content,
    )

    assert first.file_id != second.file_id
    assert second.duplicated is False
    assert fake_storage.put_count == 1
    assert db_session.query(FileAsset).count() == 2
    assert db_session.query(Blob).count() == 1
    first_asset = db_session.get(FileAsset, first.file_id)
    second_asset = db_session.get(FileAsset, second.file_id)
    assert first_asset.blob_hash == first.sha256
    assert second_asset.blob_hash == second.sha256


def test_duplicate_existing_asset_gets_blob_pointer_without_rewriting_old_location(db_session, fake_storage):
    content = b"legacy file bytes"
    sha256 = hashlib.sha256(content).hexdigest()
    legacy = FileAsset(
        tenant_code="tenant_a",
        bucket="cost-raw",
        object_key="tenant=tenant_a/source=legacy/batch=old/sha256=ab/legacy.pdf",
        sha256=sha256,
        file_name="legacy.pdf",
        file_ext=".pdf",
        file_size=len(content),
    )
    db_session.add(legacy)
    db_session.commit()

    result = register_asset(
        db_session,
        fake_storage,
        tenant_code="tenant_a",
        source_type="info_price",
        batch_id="batch-001",
        file_name="legacy-copy.pdf",
        content=content,
    )
    db_session.refresh(legacy)

    assert result.file_id == legacy.file_id
    assert result.duplicated is True
    assert legacy.blob_hash == sha256
    assert legacy.object_key == "tenant=tenant_a/source=legacy/batch=old/sha256=ab/legacy.pdf"
    assert db_session.query(Blob).count() == 1


def test_ingest_event_does_not_store_redundant_tenant_code(db_session, fake_storage):
    result = register_asset(
        db_session,
        fake_storage,
        tenant_code="tenant_a",
        source_type="info_price",
        batch_id="batch-001",
        file_name="a.xlsx",
        content=b"tenant consistency lives on file_asset",
    )
    event = db_session.get(IngestEvent, result.ingest_event_id)

    assert event.file_id == result.file_id
    assert "tenant_code" not in IngestEvent.__table__.columns


def test_sha256_is_content_hash(db_session, fake_storage):
    content = b"hash me"
    result = register_asset(
        db_session,
        fake_storage,
        tenant_code="tenant_a",
        source_type="info_price",
        batch_id="batch-001",
        file_name="a.pdf",
        content=content,
    )

    assert result.sha256 == hashlib.sha256(content).hexdigest()
