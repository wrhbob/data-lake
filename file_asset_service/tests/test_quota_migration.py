"""P0-2 · SPEC-QA-001 quota schema migration + constraint tests.

Covers mandatory revision #10 (fresh SQLite, legacy-upgrade, idempotency, other-domain
integrity, duplicate-blocking, guarded downgrade) and the #6 field constraints.
"""

import uuid

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app import database
from app import quota_migration
from app.models import (
    Base,
    QuotaArchiveProfile,
    QuotaProjectionCandidate,
    QuotaPublicationRelation,
    QuotaPublicationSet,
)


def _memory_engine():
    return create_engine("sqlite+pysqlite:///:memory:", future=True)


def _fresh_engine():
    """Fresh DB the way the app builds it: create_all + quota migration."""
    engine = _memory_engine()
    Base.metadata.create_all(engine)
    database.migrate_quota_tables(engine)
    return engine


def _legacy_archive_file_engine(rows: list[dict]):
    """A pre-quota DB: minimal archive/file_asset/archive_file without the new columns."""
    engine = _memory_engine()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                create table archive (
                    archive_id varchar(36) primary key,
                    domain_type varchar(64) not null,
                    title text not null
                )
                """
            )
        )
        connection.execute(
            text(
                """
                create table file_asset (
                    file_id varchar(36) primary key,
                    file_name varchar(255) not null
                )
                """
            )
        )
        connection.execute(
            text(
                """
                create table archive_file (
                    archive_file_id varchar(36) primary key,
                    archive_id varchar(36) not null,
                    file_id varchar(36),
                    file_role varchar(32) not null,
                    is_primary integer not null default 0
                )
                """
            )
        )
        connection.execute(
            text("insert into archive (archive_id, domain_type, title) values ('a-cost', 'cost_info', 'legacy cost')")
        )
        connection.execute(
            text("insert into archive (archive_id, domain_type, title) values ('a-trade', 'trading', 'legacy trade')")
        )
        for row in rows:
            connection.execute(
                text(
                    "insert into archive_file (archive_file_id, archive_id, file_id, file_role, is_primary) "
                    "values (:id, :archive_id, :file_id, :file_role, :is_primary)"
                ),
                row,
            )
    return engine


def _pub_set(**overrides) -> QuotaPublicationSet:
    defaults = dict(
        biz_key=f"quota:test:{uuid.uuid4()}",
        publication_family_code="GB_50500",
        title="测试资料体系",
        material_type="quota_base",
        jurisdiction_level="province",
        jurisdiction_code="510000",
        issuer_name="四川省住建厅",
        edition_label="2020定额",
        legal_status="unknown",
        metadata_status="missing",
        tenant_code="platform_public",
    )
    defaults.update(overrides)
    return QuotaPublicationSet(**defaults)


# --- Fresh create_all + seed --------------------------------------------------


def test_fresh_create_all_registers_quota_schema_and_seed():
    engine = _fresh_engine()
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {
        "quota_publication_set",
        "quota_archive_profile",
        "quota_projection_candidate",
        "quota_publication_relation",
        "quota_dictionary",
    }.issubset(tables)

    columns = {column["name"] for column in inspector.get_columns("archive_file")}
    assert {"page_range", "link_source", "linked_by"}.issubset(columns)
    index_names = {index["name"] for index in inspector.get_indexes("archive_file")}
    assert "ix_archive_file_link_source" in index_names

    with engine.connect() as connection:
        industry = connection.execute(
            text("select count(*) from quota_dictionary where dict_type = 'industry_sector'")
        ).scalar()
        discipline = connection.execute(
            text("select code from quota_dictionary where dict_type = 'discipline' order by code")
        ).all()
    assert industry == 12
    assert [row[0] for row in discipline] == ["general"]


def test_migrate_quota_tables_is_idempotent():
    engine = _fresh_engine()
    # Second and third run must not error nor duplicate seed rows.
    database.migrate_quota_tables(engine)
    database.migrate_quota_tables(engine)
    with engine.connect() as connection:
        total = connection.execute(text("select count(*) from quota_dictionary")).scalar()
    assert total == 13  # 12 industry sectors + general


# --- Legacy upgrade -----------------------------------------------------------


def test_legacy_upgrade_preserves_data_and_other_domains():
    engine = _legacy_archive_file_engine(
        rows=[
            {"id": "af1", "archive_id": "a-cost", "file_id": "f1", "file_role": "main_document", "is_primary": 1},
            {"id": "af2", "archive_id": "a-cost", "file_id": "f2", "file_role": "cover", "is_primary": 0},
        ]
    )

    database.migrate_quota_tables(engine)
    database.migrate_quota_tables(engine)  # idempotent second run

    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("archive_file")}
    assert {"page_range", "link_source", "linked_by"}.issubset(columns)

    with engine.connect() as connection:
        archive_count = connection.execute(text("select count(*) from archive")).scalar()
        rows = connection.execute(
            text("select archive_id, file_role, page_range, link_source from archive_file order by archive_file_id")
        ).all()
    assert archive_count == 2  # other-domain rows untouched
    assert rows[0] == ("a-cost", "main_document", "", "import")  # legacy row readable + backfilled defaults



def test_duplicate_role_blocks_migration_without_deleting():
    engine = _legacy_archive_file_engine(
        rows=[
            {"id": "af1", "archive_id": "a-cost", "file_id": "f1", "file_role": "main_document", "is_primary": 1},
            {"id": "af2", "archive_id": "a-cost", "file_id": "f1", "file_role": "main_document", "is_primary": 0},
        ]
    )

    with pytest.raises(database.QuotaMigrationBlocked) as excinfo:
        database.migrate_quota_tables(engine)
    assert excinfo.value.code == "ARCHIVE_FILE_ROLE_DUPLICATES"

    with engine.connect() as connection:
        remaining = connection.execute(text("select count(*) from archive_file")).scalar()
        columns = {column["name"] for column in inspect(engine).get_columns("archive_file")}
    assert remaining == 2  # nothing deleted
    # role duplicate guard aborts before the new unique constraint is created; the
    # additive columns may already exist but no data was touched.
    assert "page_range" in columns


def test_migration_allows_multiple_primary_and_creates_no_global_single_primary_index():
    """Regression guard for the approved #5 deviation: non-quota archives may legitimately
    carry multiple is_primary=true representation rows, so the migration must NOT create a
    global 'one primary per archive' unique index. quota's 'exactly one main_document' stays
    an application-layer check in P0-4."""
    engine = _legacy_archive_file_engine(
        rows=[
            {"id": "af1", "archive_id": "a-cost", "file_id": "f1", "file_role": "priced_source", "is_primary": 1},
            {"id": "af2", "archive_id": "a-cost", "file_id": "f2", "file_role": "main_document", "is_primary": 1},
        ]
    )

    # Must not raise / must not block multiple primaries on a non-quota archive.
    database.migrate_quota_tables(engine)

    inspector = inspect(engine)
    with engine.connect() as connection:
        primaries = connection.execute(
            text("select count(*) from archive_file where archive_id='a-cost' and is_primary=1")
        ).scalar()
    assert primaries == 2  # both is_primary rows coexist

    # No global single-primary unique index/constraint over archive_id may exist.
    for index in inspector.get_indexes("archive_file"):
        columns = set(index.get("column_names") or [])
        assert not (index.get("unique") and columns == {"archive_id"}), (
            "migration must not create a global single-primary unique index"
        )
        assert "primary" not in (index.get("name") or "").lower() or not index.get("unique")
    for unique in inspector.get_unique_constraints("archive_file"):
        columns = set(unique.get("column_names") or [])
        assert columns != {"archive_id"}, "migration must not create a global single-primary unique constraint"


# --- Downgrade (D4) -----------------------------------------------------------


def test_downgrade_dev_drops_schema_when_clean():
    engine = _fresh_engine()  # only the seeded dictionary, no business rows
    result = quota_migration.downgrade(engine, mode="dev")
    tables = set(inspect(engine).get_table_names())
    assert not ({"quota_publication_set", "quota_dictionary"} & tables)
    assert result["mode"] == "dev"


def test_downgrade_dev_refuses_when_business_data_present():
    engine = _fresh_engine()
    factory = sessionmaker(bind=engine, future=True)
    with factory() as session:
        session.add(_pub_set())
        session.commit()

    with pytest.raises(quota_migration.DowngradeRefused):
        quota_migration.downgrade(engine, mode="dev")

    # data + table still present (no silent delete)
    assert "quota_publication_set" in set(inspect(engine).get_table_names())
    with engine.connect() as connection:
        assert connection.execute(text("select count(*) from quota_publication_set")).scalar() == 1


def test_downgrade_prod_keeps_tables_and_data_drops_indexes():
    engine = _fresh_engine()
    factory = sessionmaker(bind=engine, future=True)
    with factory() as session:
        session.add(_pub_set())
        session.commit()

    result = quota_migration.downgrade(engine, mode="prod")

    tables = set(inspect(engine).get_table_names())
    assert "quota_publication_set" in tables  # non-destructive
    with engine.connect() as connection:
        assert connection.execute(text("select count(*) from quota_publication_set")).scalar() == 1
    index_names = {index["name"] for index in inspect(engine).get_indexes("archive_file")}
    assert "ix_archive_file_link_source" not in index_names
    assert result["mode"] == "prod"


# --- #6 field constraints -----------------------------------------------------


def test_publication_set_industry_sector_orthogonality():
    engine = _fresh_engine()
    factory = sessionmaker(bind=engine, future=True)

    # construction_regional must NOT carry an industry sector
    with factory() as session:
        session.add(_pub_set(quota_system_type="construction_regional", industry_sector_code="power_grid"))
        with pytest.raises(IntegrityError):
            session.commit()

    # industry_specialty MUST carry an industry sector
    with factory() as session:
        session.add(_pub_set(quota_system_type="industry_specialty", industry_sector_code=None))
        with pytest.raises(IntegrityError):
            session.commit()

    # valid industry_specialty
    with factory() as session:
        session.add(_pub_set(quota_system_type="industry_specialty", industry_sector_code="power_grid"))
        session.commit()


def test_publication_relation_self_and_triple_unique():
    engine = _fresh_engine()
    factory = sessionmaker(bind=engine, future=True)
    with factory() as session:
        source = _pub_set()
        target = _pub_set()
        session.add_all([source, target])
        session.commit()
        source_id = source.publication_set_id
        target_id = target.publication_set_id

    with factory() as session:
        session.add(
            QuotaPublicationRelation(
                source_publication_set_id=source_id,
                target_publication_set_id=source_id,
                relation_type="supersedes",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    with factory() as session:
        session.add(
            QuotaPublicationRelation(
                source_publication_set_id=source_id,
                target_publication_set_id=target_id,
                relation_type="supersedes",
            )
        )
        session.commit()

    with factory() as session:
        session.add(
            QuotaPublicationRelation(
                source_publication_set_id=source_id,
                target_publication_set_id=target_id,
                relation_type="supersedes",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_completeness_and_confidence_bounds():
    engine = _fresh_engine()
    factory = sessionmaker(bind=engine, future=True)
    with factory() as session:
        pub = _pub_set()
        session.add(pub)
        session.commit()
        pub_id = pub.publication_set_id

    with factory() as session:
        session.add(
            QuotaArchiveProfile(
                archive_id="arc-1",
                publication_set_id=pub_id,
                document_role="main_volume",
                completeness_score=150,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    with factory() as session:
        session.add(
            QuotaProjectionCandidate(
                file_id="file-1",
                projection_status="pending",
                suggestion_confidence=1.5,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
