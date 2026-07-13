from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CollectionTask, DataSource

PLATFORM_PUBLIC_TENANT = "platform_public"
VALID_SOURCE_SCOPES = {"platform_public", "tenant_private"}
VALID_MANAGED_BY = {"platform", "tenant"}
VALID_OPERATOR_TYPES = {"platform_ops", "tenant_user", "system"}


def _utc_batch_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d%H%M%S")


def _short_random() -> str:
    return uuid4().hex[:4]


def derive_asset_tenant(source_scope: str, tenant_code: str | None) -> str:
    if source_scope == "platform_public":
        if tenant_code is not None:
            raise ValueError("platform_public source must not set tenant_code")
        return PLATFORM_PUBLIC_TENANT
    if source_scope == "tenant_private":
        if not tenant_code:
            raise ValueError("tenant_private requires tenant_code")
        return tenant_code
    raise ValueError(f"unsupported source_scope: {source_scope}")


def create_data_source(
    session: Session,
    *,
    source_scope: str,
    tenant_code: str | None,
    managed_by: str,
    source_type: str,
    connector_type: str,
    name: str,
    data_domain: str,
    base_url: str | None = None,
    url: str | None = None,
    url_alt: str | None = None,
    province: str | None = None,
    city: str | None = None,
    region_code: str | None = None,
    auth_secret_ref: str | None = None,
    format: str | None = None,
    downloadable: bool | None = None,
    bucket: str | None = None,
    owner: str | None = None,
    reviewer: str | None = None,
    remark: str | None = None,
    frequency: str | None = None,
    config: dict | None = None,
    schedule_policy: dict | None = None,
    status: str | None = None,
    created_by: str | None = None,
) -> DataSource:
    if source_scope not in VALID_SOURCE_SCOPES:
        raise ValueError(f"unsupported source_scope: {source_scope}")
    if managed_by not in VALID_MANAGED_BY:
        raise ValueError(f"unsupported managed_by: {managed_by}")

    source = DataSource(
        source_scope=source_scope,
        tenant_code=tenant_code,
        asset_tenant_code=derive_asset_tenant(source_scope, tenant_code),
        managed_by=managed_by,
        source_type=source_type,
        connector_type=connector_type,
        name=name,
        base_url=base_url or url,
        url=url or base_url,
        url_alt=url_alt,
        province=province,
        city=city,
        region_code=region_code,
        data_domain=data_domain,
        auth_secret_ref=auth_secret_ref,
        format=format,
        downloadable=downloadable,
        bucket=bucket,
        owner=owner,
        reviewer=reviewer,
        remark=remark,
        frequency=frequency,
        config=config or {},
        schedule_policy=schedule_policy,
        status=status or "active",
        created_by=created_by,
    )
    session.add(source)
    session.commit()
    session.refresh(source)
    return source


def get_data_source(session: Session, source_id: str) -> DataSource:
    source = session.get(DataSource, source_id)
    if source is None:
        raise ValueError(f"data_source not found: {source_id}")
    return source


def build_batch_id(source: DataSource) -> str:
    return f"{source.source_type}:{source.source_id}:{_utc_batch_timestamp()}:{_short_random()}"


def create_collection_task(
    session: Session,
    *,
    source_id: str,
    operator_type: str,
    task_type: str,
    trigger_type: str,
    operator_id: str | None = None,
    data_domain: str | None = None,
    batch_id: str | None = None,
    status: str | None = None,
    period_raw: str | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
    period_note: str | None = None,
    config_override: dict | None = None,
    created_by: str | None = None,
) -> CollectionTask:
    if operator_type not in VALID_OPERATOR_TYPES:
        raise ValueError(f"unsupported operator_type: {operator_type}")

    source = get_data_source(session, source_id)
    task = CollectionTask(
        source_id=source.source_id,
        asset_tenant_code=source.asset_tenant_code,
        operator_type=operator_type,
        operator_id=operator_id,
        task_type=task_type,
        trigger_type=trigger_type,
        batch_id=batch_id or build_batch_id(source),
        data_domain=data_domain or source.data_domain,
        status=status or "pending",
        period_raw=period_raw,
        period_start=period_start,
        period_end=period_end,
        period_note=period_note,
        config_override=config_override,
        created_by=created_by,
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


def list_data_sources(
    session: Session,
    *,
    source_scope: str | None = None,
    tenant_code: str | None = None,
    source_type: str | None = None,
    status: str | None = None,
) -> list[DataSource]:
    statement = select(DataSource)
    if source_scope:
        statement = statement.where(DataSource.source_scope == source_scope)
    if tenant_code:
        statement = statement.where(DataSource.tenant_code == tenant_code)
    if source_type:
        statement = statement.where(DataSource.source_type == source_type)
    if status:
        statement = statement.where(DataSource.status == status)
    statement = statement.order_by(DataSource.created_at)
    return list(session.scalars(statement).all())


def list_collection_tasks(
    session: Session,
    *,
    source_id: str | None = None,
    asset_tenant_code: str | None = None,
    status: str | None = None,
    batch_id: str | None = None,
) -> list[CollectionTask]:
    statement = select(CollectionTask)
    if source_id:
        statement = statement.where(CollectionTask.source_id == source_id)
    if asset_tenant_code:
        statement = statement.where(CollectionTask.asset_tenant_code == asset_tenant_code)
    if status:
        statement = statement.where(CollectionTask.status == status)
    if batch_id:
        statement = statement.where(CollectionTask.batch_id == batch_id)
    statement = statement.order_by(CollectionTask.created_at)
    return list(session.scalars(statement).all())
