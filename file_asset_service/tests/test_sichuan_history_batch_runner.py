from datetime import UTC, datetime

from app.archive_rules import metadata_cell
from app.archive_service import create_archive
from app.collection import create_collection_task
from app.models import Archive, ArchiveFile, CollectionTask, FileAsset, FileProcessing
from app.sichuan_history_batch_runner import (
    SICHUAN_PROD_BATCH_REGION_CODES,
    _client_for,
    _file_processing_count,
    _remaining_tasks,
    _select_sources,
    upsert_batch_sources,
    verify_batch,
)


def test_sichuan_prod_batch_sources_include_deyang_static_column(db_session):
    sources = upsert_batch_sources(db_session)
    by_region = {source.region_code: source for source in sources}

    assert SICHUAN_PROD_BATCH_REGION_CODES == (
        "510300",
        "510700",
        "510500",
        "510600",
        "510900",
        "510400",
        "511100",
        "511700",
    )
    assert sorted(by_region) == sorted(SICHUAN_PROD_BATCH_REGION_CODES)
    deyang = by_region["510600"]
    assert deyang.config["stable"]["entry_url"] == "https://www.deyang.gov.cn/info/iList.jsp?node_id=GKdyszf&cat_id=25723"
    assert deyang.config["parser"]["parsers"][deyang.config["parser"]["active_parser_version"]]["pagination"]["max_pages"] == 14
    assert deyang.config["ops"]["queue"]["max_concurrent_per_host"] == 1
    assert deyang.config["ops"]["queue"]["min_delay_seconds"] >= 5
    assert "Macintosh; Intel Mac OS X" in deyang.config["ops"]["http"]["user_agent"]
    client = _client_for(deyang)
    assert client.min_interval_seconds == 5
    assert client.jitter_seconds == 5
    assert "Macintosh; Intel Mac OS X" in client.user_agent
    slow_client = _client_for(deyang, min_interval_seconds=8, jitter_seconds=2)
    assert slow_client.min_interval_seconds == 8
    assert slow_client.jitter_seconds == 2
    assert "Macintosh; Intel Mac OS X" in slow_client.user_agent


def test_sichuan_prod_batch_sources_can_be_filtered_by_region_codes(db_session):
    sources = upsert_batch_sources(db_session)

    selected = _select_sources(sources, "510500,511700")

    assert [source.region_code for source in selected] == ["510500", "511700"]


def test_verify_batch_counts_city_center_periods_under_parent_region(db_session):
    sources = upsert_batch_sources(db_session)
    mianyang = next(source for source in sources if source.region_code == "510700")
    create_archive(
        db_session,
        domain_type="cost_info",
        channel_type="crawler",
        business_key=f"cost_info:{mianyang.source_id}:510700:2026-04:绵阳市区信息价",
        title="绵阳市区2026年04月材料价格信息",
        source_id=mianyang.source_id,
        tenant_code=mianyang.asset_tenant_code,
        visibility_scope="public",
        status="pending_tag",
        region_code="510700",
        publish_date="2026-05-10",
        metadata={
            "period": metadata_cell("2026-04", source_level="crawler", tagged_by="runner-test"),
            "coverage_region_code": metadata_cell("510700-市区", source_level="crawler", tagged_by="runner-test"),
            "publisher_scope": metadata_cell("city", source_level="crawler", tagged_by="runner-test"),
        },
        field_sources={
            field: {"source_level": "crawler", "tagged_by": "runner-test", "tagged_at": "2026-06-22T00:00:00+08:00"}
            for field in ("domain_type", "channel_type", "business_key", "title", "region_code", "publish_date")
        },
        actor_type="system",
        actor_id="runner-test",
    )

    report = verify_batch(db_session, [mianyang])

    assert report["periods_by_region"]["510700"] == ["2026-04"]


def test_remaining_tasks_counts_pending_and_running_only(db_session):
    sources = upsert_batch_sources(db_session)
    source = sources[0]
    for status in ("pending", "running", "done"):
        task = create_collection_task(
            db_session,
            source_id=source.source_id,
            operator_type="system",
            operator_id="runner-test",
            task_type="crawl_issue",
            trigger_type="history_backfill",
            data_domain="cost_info",
            batch_id=f"runner-test-{status}",
            period_raw=f"runner-test-{status}",
            period_start="2026-05",
            config_override={"source_item": {"title": status}},
        )
        task.status = status
        task.scheduled_at = datetime(2026, 6, 22, 10, 0, tzinfo=UTC)
        db_session.commit()

    assert _remaining_tasks(db_session, sources) == 2
    assert db_session.query(CollectionTask).filter_by(task_type="crawl_issue").count() == 3


def test_file_processing_count_is_scoped_to_batch_sources(db_session):
    sources = upsert_batch_sources(db_session)
    in_scope, out_of_scope = sources[0], sources[1]
    _add_file_processing_for_source(db_session, out_of_scope.source_id, out_of_scope.asset_tenant_code)

    assert _file_processing_count(db_session, [in_scope.source_id]) == 0
    assert _file_processing_count(db_session, [out_of_scope.source_id]) == 1


def _add_file_processing_for_source(db_session, source_id: str, tenant_code: str) -> None:
    file_asset = FileAsset(
        tenant_code=tenant_code,
        bucket="test",
        object_key=f"test/{source_id}.pdf",
        version="v1",
        sha256=f"sha-{source_id}",
        file_name=f"{source_id}.pdf",
        file_ext=".pdf",
        file_size=10,
    )
    db_session.add(file_asset)
    db_session.flush()
    archive = Archive(
        domain_type="cost_info",
        channel_type="crawler",
        collection_method="auto",
        business_key=f"cost_info:{source_id}:test",
        title="runner scope test",
        source_id=source_id,
        tenant_code=tenant_code,
        metadata_payload={},
    )
    db_session.add(archive)
    db_session.flush()
    db_session.add(
        ArchiveFile(
            archive_id=archive.archive_id,
            file_id=file_asset.file_id,
            file_role="main_document",
            representation_role="primary",
            is_primary=True,
        )
    )
    db_session.add(FileProcessing(file_id=file_asset.file_id, processor="should-not-exist-for-layer0"))
    db_session.commit()
