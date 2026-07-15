from datetime import UTC, datetime

from app.collection import create_data_source
from app.crawl_campaign import create_campaign
from app.crawler_loop import run_loop_once
from app.cost_info_scheduler import ensure_aware_utc, next_scan_at, run_scheduler
from app.cost_info_worker import run_worker
from app.models import CollectionTask, CrawlItem, SourceCrawlState
from app.storage import FakeObjectStore


def make_mock_source(db_session, *, frequency="daily", policy_extra=None):
    policy = {
        "enabled": True,
        "frequency": frequency,
        "timezone": "Asia/Shanghai",
        "max_attempts": 3,
        "rate_limit": {"host": "mock.gov.cn", "min_delay_seconds": 0, "jitter_seconds": 0},
    }
    policy.update(policy_extra or {})
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="任务循环 Mock 站",
        data_domain="cost_info",
        base_url="https://mock.gov.cn/list",
        url="https://mock.gov.cn/list",
        province="测试省",
        city="测试市",
        region_code="990000",
        config={
            "stable": {"site_id": "cost_info.test.loop", "entry_url": "https://mock.gov.cn/list"},
            "parser": {"active_parser_version": "mock.v1", "parsers": {"mock.v1": {"adapter_kind": "mock"}}},
        },
        schedule_policy=policy,
        status="active",
    )


def test_monthly_release_window_persists_next_scan_and_requeues_when_due(db_session):
    source = make_mock_source(
        db_session,
        frequency="monthly",
        policy_extra={"scan_days": [3, 5], "scan_times": ["08:00", "14:00"]},
    )
    first_now = datetime(2026, 6, 2, 2, 0, tzinfo=UTC)  # 10:00 Asia/Shanghai

    first = run_scheduler(db_session, now=first_now)
    task = db_session.query(CollectionTask).one()
    state = db_session.get(SourceCrawlState, source.source_id)

    assert first["task_created"] == 1
    assert state is not None
    assert ensure_aware_utc(state.next_scan_at) == datetime(2026, 6, 3, 0, 0, tzinfo=UTC)
    assert next_scan_at(source, after=first_now) == ensure_aware_utc(state.next_scan_at)

    task.status = "done"
    db_session.commit()
    second = run_scheduler(db_session, now=state.next_scan_at)

    assert second["task_created"] == 1
    assert db_session.query(CollectionTask).count() == 2


def test_campaign_task_records_each_discovered_item_and_completes(db_session):
    source = make_mock_source(db_session)
    campaign = create_campaign(
        db_session,
        source_id=source.source_id,
        name="2026 年 6 月历史首采",
        start_period="2026-06",
        end_period="2026-06",
        item_limit=10,
        now=datetime(2026, 6, 26, tzinfo=UTC),
    )
    task = db_session.query(CollectionTask).one()

    assert task.task_type == "crawl_campaign_collect"
    assert task.config_override["crawl_campaign"]["campaign_id"] == campaign.campaign_id

    report = run_worker(
        db_session,
        now=datetime(2026, 6, 26, 1, 0, tzinfo=UTC),
        storage=FakeObjectStore(),
    )
    db_session.refresh(campaign)
    items = db_session.query(CrawlItem).filter_by(campaign_id=campaign.campaign_id).all()

    assert report.done_count == 1
    assert campaign.status == "completed"
    assert campaign.discovered_count == 3
    assert campaign.completed_count == 3
    assert campaign.failed_count == 0
    assert {item.status for item in items} == {"done"}


def test_loop_round_schedules_and_drains_normal_tasks(db_session):
    source = make_mock_source(db_session)
    report = run_loop_once(
        db_session,
        now=datetime(2026, 6, 26, 2, 0, tzinfo=UTC),
        worker_limit=5,
        storage=FakeObjectStore(),
    )

    state = db_session.get(SourceCrawlState, source.source_id)
    assert report["scheduler"]["task_created"] == 1
    assert report["worker_summary"]["done_count"] == 1
    assert state is not None
    assert ensure_aware_utc(state.last_succeeded_at) == datetime(2026, 6, 26, 2, 0, tzinfo=UTC)
    assert db_session.query(CollectionTask).filter_by(status="done").count() == 1
