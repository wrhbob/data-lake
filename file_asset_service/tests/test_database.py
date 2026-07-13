from sqlalchemy import create_engine
from sqlalchemy import inspect, text
from sqlalchemy.orm import sessionmaker

from app import database
from app.models import Base


def test_get_db_session_opens_session_from_configured_factory(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    monkeypatch.setattr(database, "_session_factory", factory)

    generator = database.get_db_session()
    session = next(generator)

    assert session.bind is engine
    generator.close()


def test_migrate_ingest_event_t2_columns_adds_missing_columns():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                create table ingest_event (
                    event_id varchar(36) primary key,
                    file_id varchar(36) not null,
                    source_type varchar(64) not null,
                    batch_id varchar(128),
                    source_url text,
                    original_name varchar(255) not null,
                    ingested_at datetime not null
                )
                """
            )
        )

    database.migrate_ingest_event_t2_columns(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("ingest_event")}
    assert {
        "source_id",
        "task_id",
        "source_item_key",
        "source_modified_at",
        "metadata",
    }.issubset(columns)


def test_migrate_blob_columns_adds_blob_pointer_to_existing_file_asset_table():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                create table file_asset (
                    file_id varchar(36) primary key,
                    tenant_code varchar(64) not null,
                    bucket varchar(128) not null,
                    object_key varchar(512) not null,
                    version varchar(64) not null,
                    sha256 varchar(64) not null,
                    file_name varchar(255) not null,
                    file_ext varchar(32) not null,
                    file_size bigint not null
                )
                """
            )
        )

    database.migrate_blob_columns(engine)

    table_names = set(inspect(engine).get_table_names())
    columns = {column["name"] for column in inspect(engine).get_columns("file_asset")}
    indexes = {index["name"] for index in inspect(engine).get_indexes("file_asset")}
    assert "blob" in table_names
    assert "blob_hash" in columns
    assert "ix_file_asset_blob_hash" in indexes


def test_migrate_collection_ledger_columns_adds_missing_columns():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                create table data_source (
                    source_id varchar(36) primary key,
                    source_scope varchar(32) not null,
                    tenant_code varchar(64),
                    asset_tenant_code varchar(64) not null,
                    managed_by varchar(32) not null,
                    source_type varchar(64) not null,
                    connector_type varchar(64) not null,
                    name varchar(255) not null,
                    base_url text,
                    region_code varchar(32),
                    data_domain varchar(64) not null,
                    auth_secret_ref text,
                    config json not null,
                    schedule_policy json,
                    status varchar(32) not null,
                    created_by varchar(128),
                    created_at datetime not null,
                    updated_at datetime not null,
                    deleted_at datetime
                )
                """
            )
        )
        connection.execute(
            text(
                """
                create table collection_task (
                    task_id varchar(36) primary key,
                    source_id varchar(36) not null,
                    asset_tenant_code varchar(64) not null,
                    operator_type varchar(32) not null,
                    task_type varchar(32) not null,
                    trigger_type varchar(32) not null,
                    batch_id varchar(128) not null,
                    data_domain varchar(64) not null,
                    status varchar(32) not null
                )
                """
            )
        )

    database.migrate_collection_ledger_columns(engine)

    source_columns = {column["name"] for column in inspect(engine).get_columns("data_source")}
    task_columns = {column["name"] for column in inspect(engine).get_columns("collection_task")}
    assert {
        "province",
        "city",
        "url",
        "url_alt",
        "format",
        "downloadable",
        "bucket",
        "owner",
        "reviewer",
        "remark",
        "frequency",
    }.issubset(source_columns)
    assert {
        "period_raw",
        "period_start",
        "period_end",
        "period_note",
        "worker_id",
        "lease_expires_at",
        "heartbeat_at",
    }.issubset(task_columns)


def test_archive_jsonb_migration_statements_cast_existing_postgres_columns():
    assert database.archive_jsonb_migration_statements() == [
        "ALTER TABLE archive ALTER COLUMN metadata TYPE JSONB USING metadata::jsonb",
        "ALTER TABLE archive ALTER COLUMN field_sources TYPE JSONB USING field_sources::jsonb",
    ]


def test_migrate_archive_columns_adds_spec_018_contract_columns():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                create table archive (
                    archive_id varchar(36) primary key,
                    domain_type varchar(64) not null,
                    channel_type varchar(32) not null,
                    business_key text not null,
                    title text not null,
                    source_id varchar(36) not null,
                    tenant_code varchar(64) not null,
                    visibility_scope varchar(32) not null,
                    status varchar(32) not null,
                    region_code varchar(32),
                    metadata json not null,
                    field_sources json not null,
                    version integer not null,
                    is_current boolean not null,
                    is_withdrawn boolean not null,
                    created_at datetime not null,
                    updated_at datetime not null
                )
                """
            )
        )
        connection.execute(
            text(
                """
                insert into archive (
                    archive_id, domain_type, channel_type, business_key, title, source_id,
                    tenant_code, visibility_scope, status, metadata, field_sources,
                    version, is_current, is_withdrawn, created_at, updated_at
                )
                values (
                    'a1', 'cost_info', 'crawler', 'cost_info:legacy', 'legacy',
                    'source-1', 'platform_public', 'public', 'pending_tag', '{}', '{}',
                    1, true, false, '2026-06-20 10:00:00', '2026-06-20 10:00:00'
                )
                """
            )
        )
        connection.execute(
            text(
                """
                insert into archive (
                    archive_id, domain_type, channel_type, business_key, title, source_id,
                    tenant_code, visibility_scope, status, region_code, metadata, field_sources,
                    version, is_current, is_withdrawn, created_at, updated_at
                )
                values (
                    'bj1', 'cost_info', 'crawler',
                    'cost_info:beijing:110000:2026-01:2026年01月北京工程造价信息',
                    '2026年01月北京工程造价信息',
                    'source-bj', 'platform_public', 'public', 'collected', '110000',
                    '{"period":{"value":"2026-01","tagged_by":"beijing.zjw-main-pdf-list.v1"}}',
                    '{}', 1, true, false, '2026-06-20 10:00:00', '2026-06-20 10:00:00'
                )
                """
            )
        )

    database.migrate_archive_columns(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("archive")}
    assert {"collection_method", "metadata_schema_version", "preview_status", "price_kind", "period_kind"}.issubset(columns)

    with engine.connect() as connection:
        result = connection.execute(text("select price_kind, period_kind from archive where archive_id = 'a1'")).one()
        beijing_result = connection.execute(text("select price_kind, period_kind from archive where archive_id = 'bj1'")).one()
    assert result == ("unspecified", "monthly")
    assert beijing_result == ("guidance", "monthly")


def test_migrate_archive_file_columns_renames_created_at_to_added_at_contract():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                create table archive_file (
                    archive_file_id varchar(36) primary key,
                    archive_id varchar(36) not null,
                    file_id varchar(36) not null,
                    file_role varchar(32) not null,
                    created_at datetime not null
                )
                """
            )
        )

    database.migrate_archive_file_columns(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("archive_file")}
    assert "added_at" in columns


def test_archive_file_role_constraint_sql_includes_trading_l0_roles():
    sql = database.archive_file_role_check_sql()

    assert "'qingdan_package'" in sql
    assert "'drawing'" in sql
    assert "'geological'" in sql
    assert "'tender_doc'" in sql


def test_migrate_archive_event_columns_adds_before_after_payload_contract():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                create table archive_event (
                    event_id integer primary key,
                    archive_id varchar(36) not null,
                    event_type varchar(64) not null,
                    payload json not null,
                    delivery_status varchar(32) not null,
                    occurred_at datetime not null
                )
                """
            )
        )
        connection.execute(
            text(
                """
                insert into archive_event (
                    event_id, archive_id, event_type, payload, delivery_status, occurred_at
                ) values (
                    1, 'archive-1', 'ARCHIVE_CREATED', '{"status":"pending_tag"}', 'pending', '2026-06-20T00:00:00'
                )
                """
            )
        )

    database.migrate_archive_event_columns(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("archive_event")}
    row = engine.connect().execute(text("select after_payload from archive_event where event_id = 1")).one()
    assert "before_payload" in columns
    assert "after_payload" in columns
    assert row.after_payload == '{"status":"pending_tag"}'


def test_migrate_audit_log_columns_adds_error_code_for_rejection_statistics():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                create table audit_log (
                    audit_id integer primary key,
                    actor_type varchar(32),
                    actor_id varchar(128),
                    action varchar(64) not null,
                    target_type varchar(64) not null,
                    target_id varchar(128) not null,
                    before_payload json,
                    after_payload json,
                    occurred_at datetime not null
                )
                """
            )
        )

    database.migrate_audit_log_columns(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("audit_log")}
    indexes = {index["name"] for index in inspect(engine).get_indexes("audit_log")}
    assert "error_code" in columns
    assert "ix_audit_log_error_code" in indexes
