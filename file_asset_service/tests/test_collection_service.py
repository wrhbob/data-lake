import re

import pytest

from app.collection import (
    PLATFORM_PUBLIC_TENANT,
    create_collection_task,
    create_data_source,
)


def test_create_platform_public_source_derives_platform_asset_tenant(db_session):
    source = create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="http_site",
        name="广州建设工程信息价",
        data_domain="info_price",
    )

    assert source.asset_tenant_code == PLATFORM_PUBLIC_TENANT
    assert source.tenant_code is None


def test_create_tenant_external_public_source_lands_in_tenant_asset_pool(db_session):
    source = create_data_source(
        db_session,
        source_scope="tenant_private",
        tenant_code="tenant_a",
        managed_by="tenant",
        source_type="info_price",
        connector_type="http_site",
        name="客户自定义信息价网站",
        data_domain="info_price",
    )

    assert source.asset_tenant_code == "tenant_a"
    assert source.managed_by == "tenant"


def test_create_platform_managed_tenant_source_lands_in_tenant_asset_pool(db_session):
    source = create_data_source(
        db_session,
        source_scope="tenant_private",
        tenant_code="tenant_a",
        managed_by="platform",
        source_type="public_resource_exchange",
        connector_type="http_site",
        name="平台代采公共交易中心",
        data_domain="public_trade",
    )

    assert source.asset_tenant_code == "tenant_a"
    assert source.managed_by == "platform"


def test_tenant_private_source_requires_tenant_code(db_session):
    with pytest.raises(ValueError, match="tenant_private requires tenant_code"):
        create_data_source(
            db_session,
            source_scope="tenant_private",
            tenant_code=None,
            managed_by="tenant",
            source_type="enterprise_drive",
            connector_type="webdav",
            name="企业共享盘",
            data_domain="enterprise_project",
        )


def test_create_collection_task_derives_asset_tenant_and_batch(db_session):
    source = create_data_source(
        db_session,
        source_scope="tenant_private",
        tenant_code="tenant_a",
        managed_by="tenant",
        source_type="enterprise_drive",
        connector_type="webdav",
        name="企业共享盘",
        data_domain="enterprise_project",
    )

    task = create_collection_task(
        db_session,
        source_id=source.source_id,
        operator_type="tenant_user",
        operator_id="user_001",
        task_type="sync",
        trigger_type="manual",
    )

    assert task.asset_tenant_code == "tenant_a"
    assert task.data_domain == "enterprise_project"
    assert task.status == "pending"
    assert re.match(r"enterprise_drive:[a-f0-9-]+:\d{14}:[a-f0-9]{4}", task.batch_id)
