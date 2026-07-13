import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models import CollectionTask, DataSource


def enable_sqlite_foreign_keys(db_session):
    db_session.execute(text("PRAGMA foreign_keys=ON"))


def test_platform_public_source_requires_platform_asset_tenant(db_session):
    source = DataSource(
        source_id="src-platform",
        source_scope="platform_public",
        tenant_code=None,
        asset_tenant_code="platform_public",
        managed_by="platform",
        source_type="info_price",
        connector_type="http_site",
        name="广州建设工程信息价",
        data_domain="info_price",
    )
    db_session.add(source)
    db_session.commit()

    saved = db_session.get(DataSource, "src-platform")
    assert saved.asset_tenant_code == "platform_public"
    assert saved.managed_by == "platform"


def test_tenant_private_source_requires_matching_asset_tenant(db_session):
    source = DataSource(
        source_id="src-tenant",
        source_scope="tenant_private",
        tenant_code="tenant_a",
        asset_tenant_code="tenant_a",
        managed_by="tenant",
        source_type="enterprise_drive",
        connector_type="webdav",
        name="企业共享盘",
        data_domain="enterprise_project",
    )
    db_session.add(source)
    db_session.commit()

    saved = db_session.get(DataSource, "src-tenant")
    assert saved.tenant_code == "tenant_a"
    assert saved.asset_tenant_code == "tenant_a"


def test_tenant_private_source_rejects_cross_tenant_asset_pool(db_session):
    db_session.add(
        DataSource(
            source_id="src-invalid",
            source_scope="tenant_private",
            tenant_code="tenant_a",
            asset_tenant_code="tenant_b",
            managed_by="tenant",
            source_type="info_price",
            connector_type="http_site",
            name="错误租户源",
            data_domain="info_price",
        )
    )

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_collection_task_must_match_source_asset_tenant(db_session):
    enable_sqlite_foreign_keys(db_session)
    db_session.add(
        DataSource(
            source_id="src-task",
            source_scope="tenant_private",
            tenant_code="tenant_a",
            asset_tenant_code="tenant_a",
            managed_by="platform",
            source_type="public_resource_exchange",
            connector_type="http_site",
            name="平台代采公共交易中心",
            data_domain="public_trade",
        )
    )
    db_session.commit()

    db_session.add(
        CollectionTask(
            task_id="task-invalid",
            source_id="src-task",
            asset_tenant_code="tenant_b",
            operator_type="platform_ops",
            task_type="crawl",
            trigger_type="manual",
            batch_id="public_resource_exchange:src-task:20260618093000:a001",
            data_domain="public_trade",
        )
    )

    with pytest.raises(IntegrityError):
        db_session.commit()
