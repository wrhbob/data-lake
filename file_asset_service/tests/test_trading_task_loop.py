from datetime import UTC, datetime

from app.models import CollectionTask, SourceCrawlState
from app.trading_config_factories import restore_all_source_configs
from app.trading_source_registry import find_trading_data_source_by_site_id
from app.trading_task_loop import run_trading_loop_once, run_trading_scheduler, run_trading_worker


def _result(*, ingested: int = 1, skipped: int = 0, failed: int = 0):
    return {
        "ingested_count": ingested,
        "skipped_existing_count": skipped,
        "failed_count": failed,
        "failed_items": [],
        "channels": [
            {
                "channel_id": "tender_notice",
                "cursor_source_item_key": "notice-001",
                "cursor_publish_date": "2026-07-15",
            }
        ],
    }


def test_trading_factories_register_sources_pending_small_verification(db_session):
    results = restore_all_source_configs(db_session)

    assert {result.site_id for result in results} == {
        "trading.changsha.hnsggzy.jsgc",
        "trading.cdggzy.jsgc",
        "trading.cqggzy.jsgc",
        "trading.scggzy.jsgc",
    }
    chengdu = find_trading_data_source_by_site_id(db_session, "trading.cdggzy.jsgc")
    assert chengdu is not None
    assert chengdu.status == "pending_verify"
    assert chengdu.schedule_policy["frequency"] == "daily"
    assert chengdu.schedule_policy["verification"] == {
        "channel_ids": ["tender_notice"],
        "max_pages": 1,
        "page_size": 1,
        "max_items_per_channel": 1,
    }


def test_verification_task_promotes_source_and_persists_cursor(monkeypatch, db_session, fake_storage):
    restore_all_source_configs(db_session)
    source = find_trading_data_source_by_site_id(db_session, "trading.cdggzy.jsgc")
    assert source is not None
    now = datetime(2026, 7, 15, 1, 30, tzinfo=UTC)

    blocked = run_trading_scheduler(db_session, now=now, source_id=source.source_id, force=True)
    assert blocked["task_created"] == 0
    assert blocked["task_skipped_unverified"] == 1

    scheduled = run_trading_scheduler(
        db_session,
        now=now,
        source_id=source.source_id,
        force=True,
        verify=True,
    )
    assert scheduled["task_created"] == 1
    task = db_session.query(CollectionTask).one()
    assert task.task_type == "trading_incremental"
    assert task.config_override["trading_crawl"]["verification_run"] is True
    assert task.config_override["trading_crawl"]["channel_ids"] == ["tender_notice"]

    monkeypatch.setattr("app.trading_task_loop._run_task", lambda *args, **kwargs: _result())
    worker = run_trading_worker(db_session, now=now, storage=fake_storage)

    db_session.refresh(task)
    db_session.refresh(source)
    state = db_session.get(SourceCrawlState, source.source_id)
    assert worker["done_count"] == 1
    assert task.status == "done"
    assert task.new_file_count == 1
    assert task.cursor_after["source_item_key"] == "notice-001"
    assert source.status == "active"
    assert state is not None
    assert state.last_seen_publish_date == "2026-07-15"
    assert state.next_scan_at is not None


def test_loop_schedules_and_drains_one_trading_source(monkeypatch, db_session, fake_storage):
    restore_all_source_configs(db_session)
    source = find_trading_data_source_by_site_id(db_session, "trading.cqggzy.jsgc")
    assert source is not None
    monkeypatch.setattr("app.trading_task_loop._run_task", lambda *args, **kwargs: _result(ingested=0, skipped=1))

    report = run_trading_loop_once(
        db_session,
        now=datetime(2026, 7, 15, 2, 0, tzinfo=UTC),
        source_id=source.source_id,
        verify=True,
        force=True,
        storage=fake_storage,
    )

    assert report["scheduler"]["task_created"] == 1
    assert report["worker_summary"]["done_count"] == 1
    assert report["worker_summary"]["duplicate_count"] == 1


def test_history_backfill_task_keeps_scanning_past_known_notices(monkeypatch, db_session, fake_storage):
    restore_all_source_configs(db_session)
    source = find_trading_data_source_by_site_id(db_session, "trading.cdggzy.jsgc")
    assert source is not None
    source.status = "active"
    db_session.commit()

    report = run_trading_scheduler(
        db_session,
        now=datetime(2026, 7, 15, 2, 0, tzinfo=UTC),
        source_id=source.source_id,
        force=True,
        channel_ids=["tender_notice"],
        start_date="2026-07-06",
        end_date="2026-07-15",
    )
    task = db_session.query(CollectionTask).one()

    assert report["task_created"] == 1
    assert task.task_type == "trading_history_backfill"
    assert task.period_raw == "2026-07-06..2026-07-15"
    assert task.config_override["trading_crawl"]["history_backfill"] is True
    assert task.config_override["trading_crawl"]["stop_on_existing"] is False
    assert task.config_override["trading_crawl"]["max_items_per_channel"] is None

    monkeypatch.setattr("app.trading_task_loop._run_task", lambda *args, **kwargs: _result(ingested=3, skipped=1))
    worker = run_trading_worker(db_session, now=datetime(2026, 7, 15, 2, 0, tzinfo=UTC), storage=fake_storage)

    db_session.refresh(task)
    assert worker["archive_created_count"] == 3
    assert task.status == "done"
    assert task.new_file_count == 3
    assert task.duplicate_file_count == 1


def test_scheduler_truncates_descriptive_trigger_label_to_task_column_size(db_session):
    restore_all_source_configs(db_session)
    source = find_trading_data_source_by_site_id(db_session, "trading.cdggzy.jsgc")
    assert source is not None
    source.status = "active"
    db_session.commit()

    run_trading_scheduler(
        db_session,
        source_id=source.source_id,
        force=True,
        trigger="chengdu_10day_engineering_tender_backfill",
    )

    task = db_session.query(CollectionTask).one()
    assert task.trigger_type == "chengdu_10day_engineering_tender"
    assert len(task.trigger_type) == 32
