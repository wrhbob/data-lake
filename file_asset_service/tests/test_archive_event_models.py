from app.collection import create_data_source
from app.models import Archive, ArchiveEvent, AuditLog, CrawlLineage, Outbox


def make_archive(db_session):
    source = create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="public_resource_exchange",
        connector_type="http_site",
        name="杭州市公共资源交易中心",
        data_domain="trading",
    )
    archive = Archive(
        domain_type="trading",
        channel_type="crawler",
        business_key="trading:src:event-001",
        title="施工招标公告",
        source_id=source.source_id,
        tenant_code="platform_public",
        visibility_scope="public",
        status="pending_tag",
    )
    db_session.add(archive)
    db_session.flush()
    return source, archive


def test_archive_event_records_ready_for_governance_without_delivery_engine(db_session):
    _, archive = make_archive(db_session)
    event = ArchiveEvent(
        archive_id=archive.archive_id,
        event_type="ARCHIVE_READY_FOR_GOVERNANCE",
        actor_type="system",
        actor_id="test",
        before_payload={"status": "pending_tag"},
        after_payload={"status": "ready_for_governance"},
    )
    db_session.add(event)
    db_session.flush()
    outbox = Outbox(event_id=event.event_id, idempotency_key=f"archive_event:{event.event_id}")
    db_session.add(outbox)
    db_session.commit()

    assert event.event_id
    assert event.after_payload["status"] == "ready_for_governance"
    assert outbox.status == "pending"
    assert "delivery_status" not in ArchiveEvent.__table__.c


def test_audit_log_records_target_and_before_after_payload(db_session):
    _, archive = make_archive(db_session)
    audit = AuditLog(
        actor_type="platform_ops",
        actor_id="user-001",
        action="ARCHIVE_TAGGED",
        target_type="archive",
        target_id=archive.archive_id,
        before_payload={"status": "pending_tag"},
        after_payload={"status": "archived"},
    )
    db_session.add(audit)
    db_session.commit()

    assert audit.audit_id
    assert audit.target_id == archive.archive_id


def test_crawl_lineage_records_source_item_without_parsing(db_session):
    source, archive = make_archive(db_session)
    lineage = CrawlLineage(
        archive_id=archive.archive_id,
        source_id=source.source_id,
        task_id=None,
        batch_id="batch-001",
        source_item_key="notice-001",
        source_url="https://example.gov.cn/notice/001",
        source_metadata={"column_path_raw": "工程建设/招标公告"},
    )
    db_session.add(lineage)
    db_session.commit()

    assert lineage.lineage_id
    assert lineage.source_metadata["column_path_raw"] == "工程建设/招标公告"
