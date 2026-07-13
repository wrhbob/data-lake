"""P0-3 · SPEC-QA-001 quota seed ingestion + projection/reconciliation tests.

Covers the approved corrections: distinct-file_asset inventory, raw-only ingestion (no
Archive/Publication Set), classification semantics (附录 != discipline), deterministic
duplicate canonical, directory-year as a low-confidence suggestion, and seed idempotency.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from app import database, quota_projection, quota_seed
from app.collection import create_data_source
from app.models import (
    Archive,
    Base,
    DataSource,
    FileAsset,
    IngestEvent,
    QuotaProjectionCandidate,
    QuotaPublicationSet,
)
from app.storage import FakeObjectStore


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    database.migrate_quota_tables(engine)
    return sessionmaker(bind=engine, future=True, expire_on_commit=False)()


def _quota_source(session) -> DataSource:
    return create_data_source(
        session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="quota_registry",
        connector_type="manual_seed",
        name="test-quota-source",
        data_domain="quota",
        region_code="510000",
    )


def _asset(session, *, sha, name, tenant="platform_public", created_at=None) -> FileAsset:
    asset = FileAsset(
        tenant_code=tenant,
        bucket="cost-raw",
        object_key=f"objects/{sha}.pdf",
        sha256=sha,
        file_name=name,
        file_ext=".pdf",
        file_size=1024,
        created_at=created_at or datetime(2026, 1, 1, tzinfo=UTC),
    )
    session.add(asset)
    session.flush()
    return asset


def _ingest(session, *, file_id, source_id, source_item_key):
    session.add(
        IngestEvent(
            file_id=file_id,
            source_id=source_id,
            source_type="quota_registry",
            original_name="x.pdf",
            source_item_key=source_item_key,
        )
    )
    session.flush()


def _write_seed_dir(tmp_path):
    seed_dir = tmp_path / "四川2025"
    seed_dir.mkdir()
    (seed_dir / "四川省建设工程工程量清单计价定额——市政工程.pdf").write_bytes(b"pdf-market")
    (seed_dir / "四川省建设工程工程量清单计价定额——附录.pdf").write_bytes(b"pdf-appendix")
    return tmp_path


# --- seed (P3-D1) -------------------------------------------------------------


def test_seed_dry_run_lists_files_without_writing(tmp_path):
    root = _write_seed_dir(tmp_path)
    with _session() as session:
        plan = quota_seed.plan_seed(session, root)
        assert plan["file_count"] == 2
        assert plan["expected_new"] == 2
        assert plan["source_exists"] is False
        # dry-run writes nothing
        assert session.scalar(select(func.count()).select_from(FileAsset)) == 0
        assert session.scalar(select(func.count()).select_from(DataSource)) == 0


def test_seed_apply_is_raw_only_and_idempotent(tmp_path):
    root = _write_seed_dir(tmp_path)
    storage = FakeObjectStore()
    with _session() as session:
        summary = quota_seed.apply_seed(session, storage, root)
        assert summary["newly_ingested_count"] == 2
        assert summary["source_created"] is True
        # raw-only: no Archive, no Publication Set
        assert session.scalar(select(func.count()).select_from(Archive)) == 0
        assert session.scalar(select(func.count()).select_from(QuotaPublicationSet)) == 0
        assert session.scalar(select(func.count()).select_from(FileAsset)) == 2
        assert session.scalar(select(func.count()).select_from(IngestEvent)) == 2

        # re-run: no new DataSource / FileAsset / IngestEvent
        summary2 = quota_seed.apply_seed(session, storage, root)
        assert summary2["newly_ingested_count"] == 0
        assert summary2["skipped_count"] == 2
        assert summary2["source_created"] is False
        assert session.scalar(select(func.count()).select_from(DataSource)) == 1
        assert session.scalar(select(func.count()).select_from(FileAsset)) == 2
        assert session.scalar(select(func.count()).select_from(IngestEvent)) == 2


def test_seed_same_key_different_hash_is_flagged_not_overwritten(tmp_path):
    root = _write_seed_dir(tmp_path)
    target = root / "四川2025" / "四川省建设工程工程量清单计价定额——市政工程.pdf"
    storage = FakeObjectStore()
    with _session() as session:
        quota_seed.apply_seed(session, storage, root)
        # same stable source_item_key, changed content
        target.write_bytes(b"pdf-market-CHANGED")

        plan = quota_seed.plan_seed(session, root)
        assert plan["hash_conflicts"] == [target.relative_to(root).as_posix()]

        summary = quota_seed.apply_seed(session, storage, root)
        assert summary["hash_conflict_count"] == 1
        assert summary["newly_ingested_count"] == 0  # conflict refused, not overwritten
        assert session.scalar(select(func.count()).select_from(FileAsset)) == 2


# --- inventory (correction #1) -----------------------------------------------


def test_inventory_counts_distinct_file_asset_across_multiple_ingest_events():
    with _session() as session:
        source = _quota_source(session)
        asset = _asset(session, sha="a" * 64, name="定额.pdf")
        _ingest(session, file_id=asset.file_id, source_id=source.source_id, source_item_key="四川2025/a.pdf")
        _ingest(session, file_id=asset.file_id, source_id=source.source_id, source_item_key="四川2025/a-copy.pdf")
        session.commit()

        assert quota_projection.quota_inventory_file_ids(session) == [asset.file_id]
        report = quota_projection.run_projection(session)
        assert report["quota_raw_total"] == 1
        assert session.scalar(select(func.count()).select_from(QuotaProjectionCandidate)) == 1


# --- duplicate (corrections #4/#5) -------------------------------------------


def test_two_file_assets_same_sha_marks_one_duplicate_to_canonical():
    with _session() as session:
        source = _quota_source(session)
        early = _asset(
            session, sha="d" * 64, name="定额-1.pdf", tenant="platform_public",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        late = _asset(
            session, sha="d" * 64, name="定额-2.pdf", tenant="tenant-x",
            created_at=datetime(2026, 3, 1, tzinfo=UTC),
        )
        _ingest(session, file_id=early.file_id, source_id=source.source_id, source_item_key="四川2025/1.pdf")
        _ingest(session, file_id=late.file_id, source_id=source.source_id, source_item_key="四川2025/2.pdf")
        session.commit()

        report = quota_projection.run_projection(session)
        assert report["quota_raw_total"] == 2  # SHA does not shrink the denominator
        assert report["duplicate"] == 1
        assert report["pending"] == 1

        canonical = session.scalar(
            select(QuotaProjectionCandidate).where(QuotaProjectionCandidate.file_id == early.file_id)
        )
        duplicate = session.scalar(
            select(QuotaProjectionCandidate).where(QuotaProjectionCandidate.file_id == late.file_id)
        )
        assert canonical.projection_status == "pending"
        assert duplicate.projection_status == "duplicate"
        assert duplicate.duplicate_of_file_id == early.file_id  # earliest created_at wins


# --- classification semantics (correction #3) --------------------------------


def test_appendix_is_file_role_not_discipline():
    with _session() as session:
        source = _quota_source(session)
        asset = _asset(session, sha="e" * 64, name="《四川省建设工程工程量清单计价定额——附录》.pdf")
        _ingest(session, file_id=asset.file_id, source_id=source.source_id, source_item_key="四川2025/附录.pdf")
        session.commit()

        quota_projection.run_projection(session)
        candidate = session.scalar(
            select(QuotaProjectionCandidate).where(QuotaProjectionCandidate.file_id == asset.file_id)
        )
        meta = candidate.suggested_metadata
        assert meta["file_role"] == "appendix"
        assert "discipline_code" not in meta
        assert "discipline_label_candidate" not in meta


def test_unknown_discipline_label_is_candidate_not_code():
    with _session() as session:
        source = _quota_source(session)
        asset = _asset(session, sha="f" * 64, name="《四川省建设工程工程量清单计价定额——园林绿化工程》.pdf")
        _ingest(session, file_id=asset.file_id, source_id=source.source_id, source_item_key="四川2025/园林.pdf")
        session.commit()

        quota_projection.run_projection(session)
        candidate = session.scalar(
            select(QuotaProjectionCandidate).where(QuotaProjectionCandidate.file_id == asset.file_id)
        )
        meta = candidate.suggested_metadata
        assert meta.get("discipline_label_candidate") == "园林绿化工程"
        assert "discipline_code" not in meta  # not auto-added to the controlled dictionary


# --- year suggestion (P3-D2) + no side effects -------------------------------


def test_edition_year_is_low_confidence_suggestion_only():
    with _session() as session:
        source = _quota_source(session)
        asset = _asset(session, sha="b" * 64, name="市政工程.pdf")
        _ingest(session, file_id=asset.file_id, source_id=source.source_id, source_item_key="四川2025/市政.pdf")
        session.commit()

        quota_projection.run_projection(session)
        candidate = session.scalar(
            select(QuotaProjectionCandidate).where(QuotaProjectionCandidate.file_id == asset.file_id)
        )
        assert candidate.suggested_metadata["edition_year"] == {
            "value": 2025,
            "source": "nas_path",
            "status": "suggested",
        }
        # no formal Publication Set / Archive created from a suggestion
        assert session.scalar(select(func.count()).select_from(QuotaPublicationSet)) == 0
        assert session.scalar(select(func.count()).select_from(Archive)) == 0


def test_edition_year_from_datasource_name_when_absent_from_source_item_key():
    """P3-D2: year lives in the batch root / DataSource name, NOT in source_item_key."""
    with _session() as session:
        source = create_data_source(
            session,
            source_scope="platform_public",
            tenant_code=None,
            managed_by="platform",
            source_type="quota_registry",
            connector_type="manual_seed",
            name="四川2025清单定额-seed",  # year here
            data_domain="quota",
            region_code="510000",
        )
        asset = _asset(session, sha="9" * 64, name="市政工程.pdf")
        # source_item_key has NO year, source_url is None -> must fall back to DataSource name
        _ingest(session, file_id=asset.file_id, source_id=source.source_id, source_item_key="市政工程.pdf")
        session.commit()

        report = quota_projection.run_projection(session)
        candidate = session.scalar(
            select(QuotaProjectionCandidate).where(QuotaProjectionCandidate.file_id == asset.file_id)
        )
        assert candidate.suggested_metadata["edition_year"] == {
            "value": 2025,
            "source": "nas_path",
            "status": "suggested",
        }
        assert any("datasource_name" in rule for rule in candidate.matched_rules)
        assert report["quota_raw_total"] == 1
        assert session.scalar(select(func.count()).select_from(QuotaPublicationSet)) == 0
        assert session.scalar(select(func.count()).select_from(Archive)) == 0


def test_pending_candidate_edition_year_backfilled_on_rerun_without_recreating():
    with _session() as session:
        source = create_data_source(
            session,
            source_scope="platform_public",
            tenant_code=None,
            managed_by="platform",
            source_type="quota_registry",
            connector_type="manual_seed",
            name="sichuan-seed",  # no year yet
            data_domain="quota",
            region_code="510000",
        )
        asset = _asset(session, sha="8" * 64, name="市政工程.pdf")
        _ingest(session, file_id=asset.file_id, source_id=source.source_id, source_item_key="市政工程.pdf")
        session.commit()

        first = quota_projection.run_projection(session)
        candidate = session.scalar(
            select(QuotaProjectionCandidate).where(QuotaProjectionCandidate.file_id == asset.file_id)
        )
        assert first["newly_created_candidates"] == 1
        assert "edition_year" not in candidate.suggested_metadata

        # year evidence appears (e.g. DataSource renamed to the real batch name)
        source.name = "四川2025清单定额-seed"
        session.commit()

        second = quota_projection.run_projection(session)
        assert second["newly_created_candidates"] == 0  # not recreated
        assert second["enriched_candidates"] == 1
        session.refresh(candidate)
        assert candidate.suggested_metadata["edition_year"]["value"] == 2025
        assert session.scalar(select(func.count()).select_from(QuotaProjectionCandidate)) == 1
        assert session.scalar(select(func.count()).select_from(QuotaPublicationSet)) == 0
        assert session.scalar(select(func.count()).select_from(Archive)) == 0

        # human-resolved candidate must NOT be re-touched
        candidate.projection_status = "linked"
        candidate.suggested_metadata = {"edition_year": {"value": 2099, "source": "human", "status": "confirmed"}}
        session.commit()
        third = quota_projection.run_projection(session)
        assert third["enriched_candidates"] == 0
        session.refresh(candidate)
        assert candidate.suggested_metadata["edition_year"]["value"] == 2099


# --- Fix 2: --skip-init schema precheck ---------------------------------------


def test_verify_quota_schema_raises_when_table_missing():
    with _session() as session:
        session.execute(text("drop table quota_projection_candidate"))
        session.commit()
        with pytest.raises(quota_seed.QuotaSchemaIncomplete):
            quota_seed.verify_quota_schema(session.get_bind())


def test_skip_init_apply_refuses_and_writes_nothing_when_schema_incomplete(tmp_path):
    root = _write_seed_dir(tmp_path)
    storage = FakeObjectStore()
    with _session() as session:
        session.execute(text("drop table quota_projection_candidate"))
        session.commit()
        with pytest.raises(quota_seed.QuotaSchemaIncomplete):
            quota_seed.apply_seed(session, storage, root)
        # refused BEFORE any object/DB write
        assert session.scalar(select(func.count()).select_from(FileAsset)) == 0
        assert session.scalar(select(func.count()).select_from(DataSource)) == 0


def test_projection_is_idempotent_and_creates_no_sets_or_archives():
    with _session() as session:
        source = _quota_source(session)
        asset = _asset(session, sha="c" * 64, name="市政工程.pdf")
        _ingest(session, file_id=asset.file_id, source_id=source.source_id, source_item_key="四川2025/c.pdf")
        session.commit()

        first = quota_projection.run_projection(session)
        second = quota_projection.run_projection(session)

        assert first["newly_created_candidates"] == 1
        assert second["newly_created_candidates"] == 0
        assert second["newly_created_publication_sets"] == 0
        assert second["newly_created_archives"] == 0
        assert second["invariant_ok"] is True
        assert session.scalar(select(func.count()).select_from(QuotaProjectionCandidate)) == 1
        assert session.scalar(select(func.count()).select_from(QuotaPublicationSet)) == 0
        assert session.scalar(select(func.count()).select_from(Archive)) == 0
