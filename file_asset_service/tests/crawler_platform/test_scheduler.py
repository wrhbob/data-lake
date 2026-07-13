from datetime import UTC, datetime, timedelta

from app.collection import create_collection_task, create_data_source
from app.cost_info_scheduler import ensure_aware_utc, is_due, run_scheduler
from app.models import CollectionTask


def make_source(db_session, *, site_id="cost_info.sc.deyang", status="active", enabled=True, frequency="daily"):
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
                "publisher_name": "德阳市住房和城乡建设局",
                "entry_url": "https://zjj.deyang.gov.cn/",
            },
            "parser": {
                "active_parser_version": "deyang.cost-info-pdf-list.v1",
                "parsers": {
                    "deyang.cost-info-pdf-list.v1": {
                        "adapter_kind": "sichuan_pdf",
                        "list_url": "https://zjj.deyang.gov.cn/xxj/",
                    }
                },
            },
        },
        schedule_policy={
            "enabled": enabled,
            "frequency": frequency,
            "timezone": "Asia/Shanghai",
            "max_attempts": 4,
        },
        status=status,
    )


def test_scheduler_creates_pending_task_for_due_active_cost_info_source(db_session):
    source = make_source(db_session)
    now = datetime(2026, 6, 26, 2, 30, tzinfo=UTC)

    report = run_scheduler(db_session, now=now, trigger="manual")

    task = db_session.query(CollectionTask).filter_by(source_id=source.source_id).one()
    assert report["source_seen"] == 1
    assert report["source_due"] == 1
    assert report["task_created"] == 1
    assert task.status == "pending"
    assert task.task_type == "crawl_incremental"
    assert task.trigger_type == "manual"
    assert task.data_domain == "cost_info"
    assert task.asset_tenant_code == source.asset_tenant_code
    assert ensure_aware_utc(task.scheduled_at) == now
    assert task.max_attempts == 4
    assert task.batch_id.startswith("cost_info:cost_info.sc.deyang:20260626023000")
    assert task.config_override["site_id"] == "cost_info.sc.deyang"
    assert task.config_override["adapter_kind"] == "sichuan_pdf"
    assert task.config_override["active_parser_version"] == "deyang.cost-info-pdf-list.v1"
    assert task.config_override["config_digest"].startswith("sha256:")


def test_scheduler_dry_run_reports_due_without_writing_task(db_session):
    make_source(db_session)

    report = run_scheduler(db_session, now=datetime(2026, 6, 26, tzinfo=UTC), dry_run=True)

    assert report["source_seen"] == 1
    assert report["source_due"] == 1
    assert report["task_created"] == 0
    assert db_session.query(CollectionTask).count() == 0


def test_scheduler_skips_disabled_pending_verify_and_policy_disabled_sources(db_session):
    make_source(db_session, site_id="cost_info.sc.active")
    make_source(db_session, site_id="cost_info.sc.pending", status="pending_verify")
    make_source(db_session, site_id="cost_info.sc.disabled", status="disabled")
    make_source(db_session, site_id="cost_info.sc.policy_off", enabled=False)

    report = run_scheduler(db_session, now=datetime(2026, 6, 26, tzinfo=UTC))

    assert report["source_seen"] == 2
    assert report["source_due"] == 1
    assert report["task_created"] == 1
    assert report["task_skipped_disabled"] == 2
    assert db_session.query(CollectionTask).count() == 1


def test_scheduler_is_idempotent_when_pending_task_exists(db_session):
    source = make_source(db_session)
    create_collection_task(
        db_session,
        source_id=source.source_id,
        operator_type="system",
        task_type="crawl_incremental",
        trigger_type="scheduled",
        data_domain="cost_info",
        status="pending",
    )

    report = run_scheduler(db_session, now=datetime(2026, 6, 26, tzinfo=UTC), force=True)

    assert report["source_due"] == 1
    assert report["task_created"] == 0
    assert report["task_skipped_pending"] == 1
    assert db_session.query(CollectionTask).count() == 1


def test_daily_due_uses_asia_shanghai_calendar_day(db_session):
    source = make_source(db_session)
    last_task = create_collection_task(
        db_session,
        source_id=source.source_id,
        operator_type="system",
        task_type="crawl_incremental",
        trigger_type="scheduled",
        data_domain="cost_info",
        status="done",
    )
    last_task.scheduled_at = datetime(2026, 6, 25, 18, 0, tzinfo=UTC)
    db_session.commit()

    assert is_due(source, db_session, now=datetime(2026, 6, 26, 2, 30, tzinfo=UTC)) is False
    assert is_due(source, db_session, now=datetime(2026, 6, 26, 16, 30, tzinfo=UTC)) is True


def test_weekly_due_waits_seven_days(db_session):
    source = make_source(db_session, frequency="weekly")
    last_task = create_collection_task(
        db_session,
        source_id=source.source_id,
        operator_type="system",
        task_type="crawl_incremental",
        trigger_type="scheduled",
        data_domain="cost_info",
        status="done",
    )
    now = datetime(2026, 6, 26, 8, 0, tzinfo=UTC)
    last_task.scheduled_at = now - timedelta(days=6, hours=23)
    db_session.commit()

    assert is_due(source, db_session, now=now) is False

    last_task.scheduled_at = now - timedelta(days=7)
    db_session.commit()
    assert is_due(source, db_session, now=now) is True
