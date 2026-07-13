from app.archive_service import attach_file
from app.collection import create_data_source
from app.models import Archive, ArchiveEvent, ArchiveFile, AuditLog, Blob, FileAsset, Outbox
from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects import postgresql


def create_exchange_source(db_session):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="public_resource_exchange",
        connector_type="http_site",
        name="杭州市公共资源交易中心",
        data_domain="trading",
    )


def test_archive_stores_trading_projection_without_project_object(db_session):
    source = create_exchange_source(db_session)
    archive = Archive(
        domain_type="trading",
        channel_type="crawler",
        business_key="trading:src:item-001",
        title="施工招标公告",
        source_id=source.source_id,
        tenant_code="platform_public",
        visibility_scope="public",
        status="pending_tag",
        metadata_payload={"project_code_raw": "P-001"},
    )
    db_session.add(archive)
    db_session.commit()

    assert archive.archive_id
    assert archive.metadata_payload["project_code_raw"] == "P-001"
    assert "project_group_key" not in archive.metadata_payload


def test_archive_stores_field_sources(db_session):
    source = create_exchange_source(db_session)
    archive = Archive(
        domain_type="trading",
        channel_type="crawler",
        business_key="trading:src:item-001",
        title="施工招标公告",
        source_id=source.source_id,
        tenant_code="platform_public",
        visibility_scope="public",
        status="pending_tag",
        field_sources={"title": {"source_level": "manual"}},
    )
    db_session.add(archive)
    db_session.commit()

    assert archive.field_sources["title"]["source_level"] == "manual"


def test_archive_json_columns_compile_to_jsonb_for_postgres():
    dialect = postgresql.dialect()

    assert Archive.__table__.c["metadata"].type.compile(dialect=dialect) == "JSONB"
    assert Archive.__table__.c["field_sources"].type.compile(dialect=dialect) == "JSONB"


def test_archive_contract_columns_match_spec_018():
    dialect = postgresql.dialect()

    assert "metadata_schema_version" in Archive.__table__.c
    assert "preview_status" in Archive.__table__.c
    assert Archive.__table__.c["publish_date"].type.compile(dialect=dialect) == "DATE"
    assert "added_at" in ArchiveFile.__table__.c
    assert "created_at" not in ArchiveFile.__table__.c


def test_ordered_event_outbox_and_audit_keys_compile_to_bigserial_for_postgres():
    dialect = postgresql.dialect()

    assert str(ArchiveEvent.__table__.c["event_id"].type.compile(dialect=dialect)) == "BIGINT"
    assert str(Outbox.__table__.c["outbox_id"].type.compile(dialect=dialect)) == "BIGINT"
    assert str(AuditLog.__table__.c["audit_id"].type.compile(dialect=dialect)) == "BIGINT"
    assert "before_payload" in ArchiveEvent.__table__.c
    assert "after_payload" in ArchiveEvent.__table__.c
    assert "payload" not in ArchiveEvent.__table__.c
    assert "delivery_status" not in ArchiveEvent.__table__.c
    assert "error_code" in AuditLog.__table__.c


def test_file_asset_keeps_blob_hash_pointer_for_spec_018():
    assert "blob_hash" in FileAsset.__table__.c
    assert "blob_id" not in FileAsset.__table__.c


def test_blob_storage_key_is_globally_unique_for_spec_018():
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in Blob.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("blob_storage_key",) in unique_columns


def test_archive_file_mounts_file_asset(db_session):
    source = create_exchange_source(db_session)
    asset = FileAsset(
        tenant_code="platform_public",
        bucket="cost-raw",
        object_key="objects/aa/bb/hash.html",
        sha256="hash",
        file_name="notice.html",
        file_ext=".html",
        file_size=12,
    )
    db_session.add(asset)
    db_session.flush()
    archive = Archive(
        domain_type="trading",
        channel_type="crawler",
        business_key="trading:src:item-002",
        title="施工招标公告",
        source_id=source.source_id,
        tenant_code="platform_public",
        visibility_scope="public",
        status="pending_tag",
    )
    db_session.add(archive)
    db_session.flush()

    mounted = ArchiveFile(
        archive_id=archive.archive_id,
        file_id=asset.file_id,
        file_role="web_snapshot",
        is_primary=True,
        sort_order=1,
    )
    db_session.add(mounted)
    db_session.commit()

    assert mounted.archive_id == archive.archive_id
    assert mounted.file_id == asset.file_id


def test_archive_file_role_constraint_includes_trading_l0_roles():
    constraints = [
        constraint.sqltext.text
        for constraint in ArchiveFile.__table__.constraints
        if getattr(constraint, "name", "") == "ck_archive_file_role"
    ]

    assert constraints
    assert "qingdan_package" in constraints[0]
    assert "drawing" in constraints[0]
    assert "geological" in constraints[0]
    assert "tender_doc" in constraints[0]


def test_archive_file_can_mount_trading_l0_role_through_attach_service(db_session):
    source = create_exchange_source(db_session)
    asset = FileAsset(
        tenant_code="platform_public",
        bucket="cost-raw",
        object_key="objects/aa/bb/drawing.pdf",
        sha256="drawing-hash",
        file_name="施工图.pdf",
        file_ext=".pdf",
        file_size=128,
    )
    archive = Archive(
        domain_type="trading",
        channel_type="crawler",
        business_key="trading:src:item-003",
        title="施工招标公告",
        source_id=source.source_id,
        tenant_code="platform_public",
        visibility_scope="public",
        status="pending_tag",
    )
    db_session.add_all([asset, archive])
    db_session.flush()

    mounted = attach_file(
        db_session,
        archive.archive_id,
        asset.file_id,
        file_role="drawing",
        display_name="施工图.pdf",
        is_primary=False,
    )
    db_session.commit()

    assert mounted.file_role == "drawing"
    assert mounted.archive_id == archive.archive_id
    assert mounted.file_id == asset.file_id
