from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.adapters.base_cost_info_adapter import DiscoveredIssue
from app.collection import create_collection_task, create_data_source
from app.cost_info_worker import _apply_worker_row_lock, _lease_pending_tasks, run_worker
from app.models import Archive, CollectionTask
from app.storage import FakeObjectStore


def make_source(db_session, *, site_id="cost_info.sc.deyang", adapter_kind="mock", status="active"):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name=f"{site_id} 造价站",
        data_domain="cost_info",
        base_url="https://zjj.deyang.gov.cn/",
        url="https://zjj.deyang.gov.cn/",
        province="四川省",
        city="德阳市",
        region_code="510600",
        config={
            "registry_schema_version": "source_registry.v1",
            "stable": {
                "site_id": site_id,
                "domain_type": "cost_info",
                "region_code": "510600",
                "entry_url": "https://zjj.deyang.gov.cn/xxj/",
            },
            "parser": {
                "active_parser_version": "test.parser.v1",
                "parsers": {
                    "test.parser.v1": {
                        "adapter_kind": adapter_kind,
                    }
                },
            },
        },
        schedule_policy={
            "enabled": True,
            "frequency": "daily",
            "timezone": "Asia/Shanghai",
            "early_stop_duplicate": True,
            "max_attempts": 3,
            "rate_limit": {"host": "zjj.deyang.gov.cn"},
        },
        status=status,
    )


def make_task(
    db_session,
    source,
    *,
    status="pending",
    attempt=0,
    max_attempts=3,
    task_type="crawl_incremental",
    batch_id=None,
    period_start=None,
):
    task = create_collection_task(
        db_session,
        source_id=source.source_id,
        operator_type="system",
        task_type=task_type,
        trigger_type="scheduled",
        data_domain="cost_info",
        batch_id=batch_id,
        status=status,
        period_raw=period_start,
        period_start=period_start,
        period_end=period_start,
    )
    task.scheduled_at = datetime(2026, 6, 26, 1, 0, tzinfo=UTC)
    task.attempt = attempt
    task.max_attempts = max_attempts
    db_session.commit()
    return task


def add_archive(db_session, source, *, source_item_key):
    archive = Archive(
        domain_type="cost_info",
        channel_type="crawler",
        business_key=f"cost_info:{source.config['stable']['site_id']}:notice:{source_item_key}",
        title=f"已采集 {source_item_key}",
        source_id=source.source_id,
        task_id=None,
        tenant_code=source.asset_tenant_code,
        visibility_scope="public",
        status="pending_tag",
    )
    db_session.add(archive)
    db_session.commit()
    return archive


def add_period_archive(db_session, source, *, period):
    archive = Archive(
        domain_type="cost_info",
        channel_type="crawler",
        business_key=f"cost_info:{source.config['stable']['site_id']}:{period}",
        title=f"{source.name} {period} 信息价",
        region_code=source.region_code,
        source_id=source.source_id,
        task_id=None,
        tenant_code=source.asset_tenant_code,
        visibility_scope="public",
        status="pending_tag",
        metadata_payload={
            "period": period,
            "coverage_region_code": source.region_code,
        },
    )
    db_session.add(archive)
    db_session.commit()
    return archive


class BoomDiscoverAdapter:
    adapter_kind = "boom_discover"

    def discover(self, source, task, client):
        raise TimeoutError("timeout while listing")

    def ingest(self, source, task, issue, storage):
        raise AssertionError("ingest should not be called")


def naive(value):
    return value.replace(tzinfo=None) if value is not None else None


def test_worker_dispatches_mock_adapter(db_session):
    source = make_source(db_session, adapter_kind="mock")
    task = make_task(db_session, source)

    report = run_worker(
        db_session,
        limit=1,
        source_id=source.source_id,
        now=datetime(2026, 6, 26, 2, 0, tzinfo=UTC),
        storage=FakeObjectStore(),
    )

    db_session.refresh(task)
    assert report.leased_count == 1
    assert report.done_count == 1
    assert report.archive_created_count == 3
    assert report.duplicate_count == 0
    assert task.status == "done"
    assert task.discovered_count == 3
    assert task.new_file_count == 3
    assert report.per_source[0]["adapter_kind"] == "mock"


def test_worker_early_stop_on_three_duplicates(db_session):
    source = make_source(db_session, adapter_kind="mock")
    task = make_task(db_session, source)
    for index in range(3):
        add_archive(db_session, source, source_item_key=f"mock_issue_{index}")

    report = run_worker(
        db_session,
        limit=1,
        source_id=source.source_id,
        now=datetime(2026, 6, 26, 2, 0, tzinfo=UTC),
        storage=FakeObjectStore(),
    )

    db_session.refresh(task)
    assert task.status == "done"
    assert report.done_count == 1
    assert report.duplicate_count == 3
    assert report.archive_created_count == 0
    assert report.per_source[0]["early_stopped"] is True


def test_worker_clears_stale_error_fields_when_retry_succeeds(db_session):
    source = make_source(db_session, adapter_kind="mock")
    task = make_task(db_session, source, attempt=1)
    task.error_code = "COST_INFO_CRAWL_TASK_FAILED"
    task.error_message = "HTTP Error 412: Precondition Failed"
    task.config_override = {"last_error": {"exception_type": "HTTPError"}}
    for index in range(3):
        add_archive(db_session, source, source_item_key=f"mock_issue_{index}")
    db_session.commit()

    report = run_worker(
        db_session,
        limit=1,
        source_id=source.source_id,
        now=datetime(2026, 6, 26, 2, 0, tzinfo=UTC),
        storage=FakeObjectStore(),
    )

    db_session.refresh(task)
    assert report.done_count == 1
    assert task.status == "done"
    assert task.error_code is None
    assert task.error_message is None
    assert task.config_override["last_error"]["exception_type"] == "HTTPError"


def test_worker_retry_on_task_exception(db_session, monkeypatch):
    from app.adapters import ADAPTERS

    monkeypatch.setitem(ADAPTERS, "boom_discover", BoomDiscoverAdapter)
    source = make_source(db_session, adapter_kind="boom_discover")
    task = make_task(db_session, source, attempt=0, max_attempts=3)

    report = run_worker(
        db_session,
        limit=1,
        source_id=source.source_id,
        now=datetime(2026, 6, 26, 2, 0, tzinfo=UTC),
        storage=FakeObjectStore(),
    )

    db_session.refresh(task)
    assert report.retry_count == 1
    assert report.done_count == 0
    assert task.status == "pending"
    assert task.attempt == 1
    assert task.error_code == "DOWNLOAD_TIMEOUT"
    assert task.config_override["last_error"]["exception_type"] == "TimeoutError"
    assert task.scheduled_at > datetime(2026, 6, 26, 2, 0)


def test_worker_dead_letter_writes_manual_recovery_to_task_override(db_session, monkeypatch):
    from app.adapters import ADAPTERS

    monkeypatch.setitem(ADAPTERS, "boom_discover", BoomDiscoverAdapter)
    source = make_source(db_session, adapter_kind="boom_discover")
    task = make_task(db_session, source, attempt=2, max_attempts=3)

    report = run_worker(
        db_session,
        limit=1,
        source_id=source.source_id,
        now=datetime(2026, 6, 26, 2, 0, tzinfo=UTC),
        storage=FakeObjectStore(),
    )

    db_session.refresh(task)
    db_session.refresh(source)
    assert report.dead_letter_count == 1
    assert task.status == "failed"
    assert task.attempt == 3
    assert task.error_code == "MAX_ATTEMPTS_EXCEEDED"
    assert task.config_override["manual_recovery"]["spec"] == "017"
    assert "manual_recovery" not in source.config


def test_worker_auto_suspends_source_after_five_recent_failures(db_session, monkeypatch):
    from app.adapters import ADAPTERS

    monkeypatch.setitem(ADAPTERS, "boom_discover", BoomDiscoverAdapter)
    source = make_source(db_session, adapter_kind="boom_discover")
    for index in range(4):
        previous = make_task(db_session, source, status="failed", attempt=3, max_attempts=3)
        previous.scheduled_at = datetime(2026, 6, 25, 1, index, tzinfo=UTC)
        previous.finished_at = datetime(2026, 6, 25, 1, index, tzinfo=UTC)
    task = make_task(db_session, source, attempt=0, max_attempts=1)
    db_session.commit()

    report = run_worker(
        db_session,
        limit=1,
        source_id=source.source_id,
        now=datetime(2026, 6, 26, 2, 0, tzinfo=UTC),
        storage=FakeObjectStore(),
    )

    db_session.refresh(task)
    db_session.refresh(source)
    assert report.dead_letter_count == 1
    assert report.manual_recovery_count == 1
    assert task.status == "failed"
    assert source.status == "manual_recovery"


def test_worker_red_line_no_file_processing(db_session):
    source = make_source(db_session, adapter_kind="mock")
    make_task(db_session, source)

    report = run_worker(
        db_session,
        limit=1,
        source_id=source.source_id,
        now=datetime(2026, 6, 26, 2, 0, tzinfo=UTC),
        storage=FakeObjectStore(),
    )

    assert report.file_processing_rows_created == 0
    assert report.file_processing_passes is True
    assert report.health_status == "healthy"


def test_worker_filters_by_site_id_and_adapter_kind(db_session):
    first = make_source(db_session, site_id="cost_info.sc.deyang", adapter_kind="mock")
    second = make_source(db_session, site_id="cost_info.cq.municipal", adapter_kind="mock")
    make_task(db_session, first)
    make_task(db_session, second)

    report = run_worker(
        db_session,
        limit=10,
        site_id="cost_info.cq.municipal",
        adapter_kind="mock",
        now=datetime(2026, 6, 26, 2, 0, tzinfo=UTC),
        storage=FakeObjectStore(),
    )

    tasks = {task.source_id: task.status for task in db_session.query(CollectionTask).all()}
    assert report.leased_count == 1
    assert tasks[first.source_id] == "pending"
    assert tasks[second.source_id] == "done"


def test_worker_filters_by_batch_id(db_session):
    source = make_source(db_session, adapter_kind="mock")
    first = make_task(db_session, source, batch_id="batch-old")
    second = make_task(db_session, source, batch_id="batch-target")

    report = run_worker(
        db_session,
        limit=10,
        source_id=source.source_id,
        batch_id="batch-target",
        now=datetime(2026, 6, 26, 2, 0, tzinfo=UTC),
        storage=FakeObjectStore(),
    )

    db_session.refresh(first)
    db_session.refresh(second)
    assert report.leased_count == 1
    assert first.status == "pending"
    assert second.status == "done"


def test_worker_leases_pending_tasks_with_worker_identity_and_expiry(db_session):
    source = make_source(db_session, adapter_kind="mock")
    task = make_task(db_session, source)
    now = datetime(2026, 6, 26, 2, 0, tzinfo=UTC)

    leased = _lease_pending_tasks(
        db_session,
        limit=1,
        now=now,
        source_id=source.source_id,
        worker_id="crawler-node-a",
        lease_seconds=600,
    )

    db_session.refresh(task)
    assert [leased_task.task_id for leased_task, _source in leased] == [task.task_id]
    assert task.status == "running"
    assert task.started_at == naive(now)
    assert task.worker_id == "crawler-node-a"
    assert task.lease_expires_at == naive(now + timedelta(seconds=600))
    assert task.heartbeat_at == naive(now)

    second = _lease_pending_tasks(
        db_session,
        limit=1,
        now=now,
        source_id=source.source_id,
        worker_id="crawler-node-b",
        lease_seconds=600,
    )

    db_session.refresh(task)
    assert second == []
    assert task.worker_id == "crawler-node-a"


def test_worker_postgres_lease_query_uses_skip_locked():
    statement = _apply_worker_row_lock(select(CollectionTask), dialect_name="postgresql")

    compiled = str(statement.compile(dialect=postgresql.dialect()))

    assert "FOR UPDATE" in compiled
    assert "SKIP LOCKED" in compiled


def test_worker_reclaims_expired_running_lease(db_session):
    source = make_source(db_session, adapter_kind="mock")
    task = make_task(db_session, source, status="running")
    task.worker_id = "dead-node"
    task.started_at = datetime(2026, 6, 26, 1, 0, tzinfo=UTC)
    task.heartbeat_at = datetime(2026, 6, 26, 1, 5, tzinfo=UTC)
    task.lease_expires_at = datetime(2026, 6, 26, 1, 30, tzinfo=UTC)
    db_session.commit()
    now = datetime(2026, 6, 26, 2, 0, tzinfo=UTC)

    leased = _lease_pending_tasks(
        db_session,
        limit=1,
        now=now,
        source_id=source.source_id,
        worker_id="crawler-node-b",
        lease_seconds=900,
    )

    db_session.refresh(task)
    assert [leased_task.task_id for leased_task, _source in leased] == [task.task_id]
    assert task.status == "running"
    assert task.worker_id == "crawler-node-b"
    assert task.lease_expires_at == naive(now + timedelta(seconds=900))
    assert task.heartbeat_at == naive(now)


def test_worker_keeps_last_worker_id_and_clears_active_lease_after_completion(db_session):
    source = make_source(db_session, adapter_kind="mock")
    task = make_task(db_session, source)
    now = datetime(2026, 6, 26, 2, 0, tzinfo=UTC)

    report = run_worker(
        db_session,
        limit=1,
        source_id=source.source_id,
        worker_id="crawler-node-a",
        lease_seconds=600,
        now=now,
        storage=FakeObjectStore(),
    )

    db_session.refresh(task)
    assert report.worker_id == "crawler-node-a"
    assert report.leased_count == 1
    assert task.status == "done"
    assert task.worker_id == "crawler-node-a"
    assert task.lease_expires_at is None
    assert task.heartbeat_at == naive(now)


def test_worker_marks_period_task_done_when_same_source_period_is_already_covered(db_session):
    source = make_source(db_session, adapter_kind="mock")
    task = make_task(
        db_session,
        source,
        task_type="crawl_issue",
        batch_id="covered-period-task",
        period_start="2026-06",
    )
    add_period_archive(db_session, source, period="2026-06")
    storage = FakeObjectStore()

    report = run_worker(
        db_session,
        limit=1,
        source_id=source.source_id,
        batch_id="covered-period-task",
        now=datetime(2026, 6, 26, 2, 0, tzinfo=UTC),
        storage=storage,
    )

    db_session.refresh(task)
    assert report.leased_count == 1
    assert report.done_count == 1
    assert report.archive_created_count == 0
    assert report.per_source == [
        {
            "source_id": source.source_id,
            "site_id": source.config["stable"]["site_id"],
            "adapter_kind": "mock",
            "status": "skipped_covered",
            "period": "2026-06",
            "archive_created": 0,
            "duplicates": 1,
            "failed": 0,
            "early_stopped": False,
        }
    ]
    assert task.status == "done"
    assert task.duplicate_file_count == 1
    assert task.cursor_after == {
        "skipped_already_covered": True,
        "period": "2026-06",
    }
    assert storage.put_count == 0
