import pytest

from app.assets import register_asset
from app.collection import create_collection_task, create_data_source
from app.models import IngestEvent


def create_tenant_task(db_session):
    source = create_data_source(
        db_session,
        source_scope="tenant_private",
        tenant_code="tenant_a",
        managed_by="tenant",
        source_type="info_price",
        connector_type="http_site",
        name="客户信息价网站",
        data_domain="info_price",
    )
    task = create_collection_task(
        db_session,
        source_id=source.source_id,
        operator_type="tenant_user",
        operator_id="user_001",
        task_type="sync",
        trigger_type="manual",
    )
    return source, task


def test_register_asset_links_ingest_event_to_source_and_task(db_session, fake_storage):
    source, task = create_tenant_task(db_session)

    result = register_asset(
        db_session,
        fake_storage,
        tenant_code="tenant_a",
        source_type="info_price",
        batch_id=None,
        file_name="信息价.xlsx",
        content=b"xlsx bytes",
        source_url="https://example.gov.cn/price.xlsx",
        source_id=source.source_id,
        task_id=task.task_id,
        source_item_key="price-2026-06",
        source_metadata={"page_title": "2026年6月信息价"},
    )

    event = db_session.get(IngestEvent, result.ingest_event_id)
    assert event.source_id == source.source_id
    assert event.task_id == task.task_id
    assert event.batch_id == task.batch_id
    assert event.source_item_key == "price-2026-06"
    assert event.source_metadata == {"page_title": "2026年6月信息价"}


def test_register_asset_rejects_task_tenant_mismatch_before_storage_write(db_session, fake_storage):
    _, task = create_tenant_task(db_session)

    with pytest.raises(ValueError, match="TENANT_MISMATCH"):
        register_asset(
            db_session,
            fake_storage,
            tenant_code="tenant_b",
            source_type="info_price",
            batch_id=None,
            file_name="wrong-tenant.xlsx",
            content=b"xlsx bytes",
            task_id=task.task_id,
        )

    assert fake_storage.put_count == 0


def test_register_asset_updates_collection_task_counts(db_session, fake_storage):
    _, task = create_tenant_task(db_session)

    first = register_asset(
        db_session,
        fake_storage,
        tenant_code="tenant_a",
        source_type="info_price",
        batch_id=None,
        file_name="first.xlsx",
        content=b"same bytes",
        task_id=task.task_id,
    )
    second = register_asset(
        db_session,
        fake_storage,
        tenant_code="tenant_a",
        source_type="info_price",
        batch_id=None,
        file_name="second.xlsx",
        content=b"same bytes",
        task_id=task.task_id,
    )

    db_session.refresh(task)
    assert first.duplicated is False
    assert second.duplicated is True
    assert task.downloaded_count == 2
    assert task.new_file_count == 1
    assert task.duplicate_file_count == 1
    assert task.processing_created_count == len(first.processing_ids)
