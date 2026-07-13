from datetime import UTC, datetime

import pytest

from app.archive_service import (
    attach_file,
    create_archive,
    create_archive_from_ingest_event,
    get_archive_detail,
    list_archives,
    patch_archive,
)
from app.collection import create_data_source
from app.collection import create_collection_task
from app.assets import register_asset
from app.models import ArchiveEvent, AuditLog, CrawlLineage, FileAsset, FileProcessing, IngestEvent, Outbox
from app.storage import FakeObjectStore


def provenance_for(*fields, level="manual", by="user:ops"):
    return {
        field: {"source_level": level, "tagged_by": by, "tagged_at": "2026-06-20T10:00:00+08:00"}
        for field in fields
    }


def cell(value, level="manual", by="user:ops"):
    return {"value": value, "source_level": level, "tagged_by": by, "tagged_at": "2026-06-20T10:00:00+08:00"}


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


def create_quota_source(db_session):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="public_resource_exchange",
        connector_type="http_site",
        name="浙江省造价定额站",
        data_domain="quota",
    )


def create_policy_source(db_session):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="public_resource_exchange",
        connector_type="http_site",
        name="浙江省住建厅",
        data_domain="policy_regulation",
    )


def create_cost_info_source(db_session):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="新疆工程造价信息网",
        data_domain="cost_info",
        region_code="650000",
        config={
            "registry_schema_version": "source_registry.v1",
            "stable": {
                "site_id": "cost_info.xj.xjzj",
                "domain_type": "cost_info",
                "region_code": "650000",
                "publisher_type": "cost_station_small_site",
                "publisher_name": "新疆工程造价信息网",
                "entry_url": "https://example.gov.cn/price/",
                "periodicity": {"unit": "month", "period_key_format": "YYYY-MM"},
            },
            "parser": {
                "active_parser_version": "xjzj.price-list.v1",
                "parsers": {
                    "xjzj.price-list.v1": {
                        "scope": "list_detail_html_only",
                        "period": {"source": "title", "regex": "(20\\d{2})年(\\d{1,2})月"},
                    }
                },
            },
            "ops": {"enabled": True, "crawl_strategy_tier": "tier1_static_html"},
        },
    )


def create_asset(db_session, *, tenant_code="platform_public", file_name="notice.html", file_ext=".html"):
    asset = FileAsset(
        tenant_code=tenant_code,
        bucket="cost-raw",
        object_key=f"objects/aa/bb/{file_name}",
        sha256=f"hash-{tenant_code}-{file_name}",
        file_name=file_name,
        file_ext=file_ext,
        file_size=12,
    )
    db_session.add(asset)
    db_session.commit()
    return asset


def test_cost_info_vertical_slice_creates_one_archive_for_pdf_and_excel_with_lineage_outbox(db_session):
    source = create_cost_info_source(db_session)
    task = create_collection_task(
        db_session,
        source_id=source.source_id,
        operator_type="system",
        task_type="crawl",
        trigger_type="manual",
        batch_id="cost-info-xj-2026-06",
        period_raw="2026年6月",
        period_start="2026-06",
        config_override={
            "parser_version": "xjzj.price-list.v1",
            "config_digest": "sha256:test-config",
            "config_snapshot": source.config,
        },
    )
    storage = FakeObjectStore()
    common_metadata = {
        "period_raw": cell("2026年6月", level="crawler", by="xjzj.price-list.v1"),
        "period_start": cell("2026-06", level="crawler", by="xjzj.price-list.v1"),
        "publisher_name": cell("新疆工程造价信息网", level="crawler", by="xjzj.price-list.v1"),
    }
    common_sources = provenance_for("domain_type", "channel_type", "title", "region_code", "publish_date", level="crawler", by="xjzj.price-list.v1")
    pdf = register_asset(
        db_session,
        storage,
        tenant_code="platform_public",
        source_type="info_price",
        batch_id=task.batch_id,
        task_id=task.task_id,
        file_name="2026年6月新疆建设工程综合价格信息.pdf",
        content=b"%PDF-1.4 cost info",
        source_url="HTTPS://Example.Gov.CN/price/detail?utm_source=wechat&id=202606#download",
        source_item_key="xj-2026-06-pdf",
        source_metadata={
            "discovered_at": "2026-06-16T09:00:00+08:00",
            "fetched_at": "2026-06-16T09:05:00+08:00",
        },
    )
    pdf_archive = create_archive_from_ingest_event(
        db_session,
        event_id=pdf.ingest_event_id,
        domain_type="cost_info",
        channel_type="crawler",
        title="2026年6月 新疆建设工程综合价格信息",
        region_code="650000",
        publish_date="2026-06-16",
        visibility_scope="public",
        status="collected",
        metadata=common_metadata,
        field_sources=common_sources,
        actor_type="system",
        actor_id="xjzj.price-list.v1",
    )
    xlsx = register_asset(
        db_session,
        storage,
        tenant_code="platform_public",
        source_type="info_price",
        batch_id=task.batch_id,
        task_id=task.task_id,
        file_name="2026年6月新疆建设工程综合价格信息.xlsx",
        content=b"xlsx bytes are stored but not parsed",
        source_url="https://example.gov.cn/price/detail?id=202606&spm=tracking",
        source_item_key="xj-2026-06-xlsx",
        source_metadata={"discovered_at": "2026-06-16T09:00:00+08:00", "fetched_at": "2026-06-16T09:06:00+08:00"},
    )
    xlsx_archive = create_archive_from_ingest_event(
        db_session,
        event_id=xlsx.ingest_event_id,
        domain_type="cost_info",
        channel_type="crawler",
        title="2026年6月 新疆建设工程综合价格信息",
        region_code="650000",
        publish_date="2026-06-16",
        visibility_scope="public",
        status="collected",
        metadata=common_metadata,
        field_sources=common_sources,
        actor_type="system",
        actor_id="xjzj.price-list.v1",
    )

    detail = get_archive_detail(db_session, pdf_archive.archive_id)
    events = db_session.query(ArchiveEvent).filter_by(archive_id=pdf_archive.archive_id).all()
    lineages = db_session.query(CrawlLineage).filter_by(archive_id=pdf_archive.archive_id).all()

    assert xlsx_archive.archive_id == pdf_archive.archive_id
    assert detail["price_kind"] == "unspecified"
    assert pdf_archive.business_key == (
        f"cost_info:{source.source_id}:650000:2026-06:2026年6月 新疆建设工程综合价格信息"
    )
    assert len(detail["files"]) == 2
    assert {item["file_name"]: item["representation_role"] for item in detail["files"]} == {
        "2026年6月新疆建设工程综合价格信息.pdf": "secondary",
        "2026年6月新疆建设工程综合价格信息.xlsx": "primary",
    }
    assert all(item["added_at"] for item in detail["files"])
    assert all("created_at" not in item for item in detail["files"])
    assert detail["version"] == 1
    assert all(lineage.parser_version == "xjzj.price-list.v1" for lineage in lineages)
    assert {lineage.source_item_key for lineage in lineages} == {"xj-2026-06-pdf", "xj-2026-06-xlsx"}
    assert db_session.query(Outbox).count() == len(events)
    assert [event.event_id for event in events] == sorted(event.event_id for event in events)
    assert all(isinstance(event.event_id, int) for event in events)
    assert all(isinstance(outbox.outbox_id, int) for outbox in db_session.query(Outbox).all())
    assert db_session.query(FileProcessing).count() == 0
    assert db_session.get(IngestEvent, pdf.ingest_event_id).source_url == "https://example.gov.cn/price/detail?id=202606"


def test_cost_info_archive_price_kind_is_additive_and_not_business_key_component(db_session):
    source = create_cost_info_source(db_session)
    result = register_asset(
        db_session,
        FakeObjectStore(),
        tenant_code="platform_public",
        source_type="info_price",
        batch_id="batch-bj-guidance",
        file_name="2026年01月北京工程造价信息.pdf",
        content=b"%PDF-1.4 beijing guidance",
        source_id=source.source_id,
        source_url="https://example.gov.cn/beijing/guidance/2026-01.pdf",
        source_item_key="beijing-main-tab:2026-01:guidance",
    )
    business_key = f"cost_info:{source.source_id}:110000:2026-01:2026年01月北京工程造价信息"

    archive = create_archive_from_ingest_event(
        db_session,
        event_id=result.ingest_event_id,
        domain_type="cost_info",
        channel_type="crawler",
        business_key=business_key,
        title="2026年01月北京工程造价信息",
        region_code="110000",
        publish_date="2026-01-22",
        visibility_scope="public",
        status="collected",
        price_kind="guidance",
        period_kind="issue_based",
        metadata={"period": cell("2026-01", level="crawler", by="beijing.zjw-main-pdf-list.v1")},
        field_sources=provenance_for(
            "domain_type",
            "channel_type",
            "business_key",
            "title",
            "region_code",
            "publish_date",
            level="crawler",
            by="beijing.zjw-main-pdf-list.v1",
        ),
        actor_type="system",
        actor_id="beijing.zjw-main-pdf-list.v1",
    )
    detail = get_archive_detail(db_session, archive.archive_id)

    assert archive.price_kind == "guidance"
    assert archive.period_kind == "issue_based"
    assert detail["price_kind"] == "guidance"
    assert detail["period_kind"] == "issue_based"
    assert archive.business_key == business_key
    assert "guidance" not in archive.business_key
    assert "issue_based" not in archive.business_key


def test_cost_info_archive_rejects_unknown_price_kind(db_session):
    source = create_cost_info_source(db_session)

    with pytest.raises(ValueError, match="UNSUPPORTED_PRICE_KIND"):
        create_archive(
            db_session,
            domain_type="cost_info",
            channel_type="crawler",
            business_key=f"cost_info:{source.source_id}:110000:2026-01:bad-price-kind",
            title="bad-price-kind",
            source_id=source.source_id,
            tenant_code="platform_public",
            visibility_scope="public",
            price_kind="parsed_from_pdf",
            metadata={},
            field_sources=provenance_for("domain_type", "channel_type", "business_key", "title", level="crawler", by="collector"),
        )


def test_cost_info_late_excel_append_to_archived_pdf_is_additive(db_session):
    source = create_cost_info_source(db_session)
    storage = FakeObjectStore()
    metadata = {"period_start": cell("2026-07", level="crawler", by="xjzj.price-list.v1")}
    field_sources = provenance_for("domain_type", "channel_type", "title", "region_code", "publish_date", level="crawler", by="xjzj.price-list.v1")
    pdf = register_asset(
        db_session,
        storage,
        tenant_code="platform_public",
        source_type="info_price",
        batch_id="cost-info-xj-2026-07",
        source_id=source.source_id,
        file_name="2026年7月新疆建设工程综合价格信息.pdf",
        content=b"%PDF-1.4 cost info",
    )
    archive = create_archive_from_ingest_event(
        db_session,
        event_id=pdf.ingest_event_id,
        domain_type="cost_info",
        channel_type="crawler",
        title="2026年7月 新疆建设工程综合价格信息",
        region_code="650000",
        publish_date="2026-07-16",
        status="archived",
        metadata=metadata,
        field_sources=field_sources,
    )
    version_before = archive.version
    xlsx = register_asset(
        db_session,
        storage,
        tenant_code="platform_public",
        source_type="info_price",
        batch_id="cost-info-xj-2026-07",
        source_id=source.source_id,
        file_name="2026年7月新疆建设工程综合价格信息.xlsx",
        content=b"xlsx bytes are stored but not parsed",
    )

    updated = create_archive_from_ingest_event(
        db_session,
        event_id=xlsx.ingest_event_id,
        domain_type="cost_info",
        channel_type="crawler",
        title="2026年7月 新疆建设工程综合价格信息",
        region_code="650000",
        publish_date="2026-07-16",
        status="archived",
        metadata=metadata,
        field_sources=field_sources,
    )

    event_types = [event.event_type for event in db_session.query(ArchiveEvent).filter_by(archive_id=archive.archive_id)]
    audit_actions = [audit.action for audit in db_session.query(AuditLog).filter_by(target_id=archive.archive_id)]
    assert updated.archive_id == archive.archive_id
    assert updated.version == version_before
    assert "ADD_REPRESENTATION" in event_types
    assert "ADD_REPRESENTATION" in audit_actions
    assert len(get_archive_detail(db_session, archive.archive_id)["files"]) == 2


def test_create_archive_records_archive_created_event(db_session):
    source = create_exchange_source(db_session)

    archive = create_archive(
        db_session,
        domain_type="trading",
        channel_type="crawler",
        business_key="trading:src:notice-001",
        title="施工招标公告",
        source_id=source.source_id,
        tenant_code="platform_public",
        visibility_scope="public",
        status="pending_tag",
        metadata={"project_code_raw": cell("P-001")},
        field_sources=provenance_for("domain_type", "channel_type", "business_key", "title"),
        actor_type="system",
        actor_id="collector",
    )

    event = db_session.query(ArchiveEvent).filter_by(archive_id=archive.archive_id).one()
    assert archive.metadata_payload["project_code_raw"]["value"] == "P-001"
    assert "project_group_key" not in archive.metadata_payload
    assert event.event_type == "ARCHIVE_CREATED"
    assert event.after_payload["status"] == "pending_tag"


def test_create_archive_rejects_collected_without_file(db_session):
    source = create_exchange_source(db_session)

    with pytest.raises(ValueError, match="COLLECTED_REQUIRES_FILE"):
        create_archive(
            db_session,
            domain_type="trading",
            channel_type="crawler",
            business_key="trading:src:fileless-collected",
            title="施工招标公告",
            source_id=source.source_id,
            tenant_code="platform_public",
            visibility_scope="public",
            status="collected",
            field_sources=provenance_for("domain_type", "channel_type", "business_key", "title"),
        )


def test_patch_archive_rejects_illegal_discovered_to_archived_transition(db_session):
    source = create_exchange_source(db_session)
    archive = create_archive(
        db_session,
        domain_type="trading",
        channel_type="crawler",
        business_key="trading:src:illegal-transition",
        title="施工招标公告",
        source_id=source.source_id,
        tenant_code="platform_public",
        visibility_scope="public",
        status="discovered",
        field_sources=provenance_for("domain_type", "channel_type", "business_key", "title"),
    )

    with pytest.raises(ValueError, match="ILLEGAL_ARCHIVE_STATUS_TRANSITION"):
        patch_archive(db_session, archive.archive_id, status="archived")


def test_create_quota_archive_accepts_metadata_cells_and_field_sources(db_session):
    source = create_quota_source(db_session)

    archive = create_archive(
        db_session,
        domain_type="quota",
        channel_type="crawler",
        business_key="quota:330000:JZZS2020",
        title="浙江省建设工程计价依据",
        region_code="330000",
        source_id=source.source_id,
        tenant_code="platform_public",
        visibility_scope="public",
        status="pending_tag",
        metadata={"quota_code_raw": cell("JZZS2020")},
        field_sources=provenance_for("domain_type", "channel_type", "business_key", "title", "region_code"),
    )

    detail = get_archive_detail(db_session, archive.archive_id)

    assert detail["metadata"]["quota_code_raw"]["value"] == "JZZS2020"
    assert detail["field_sources"]["title"]["source_level"] == "manual"


def test_create_archive_rejects_extracted_metadata_and_records_audit(db_session):
    source = create_exchange_source(db_session)
    business_key = "trading:src:notice-extracted"

    with pytest.raises(ValueError, match="METADATA_EXTRACTION_FORBIDDEN"):
        create_archive(
            db_session,
            domain_type="trading",
            channel_type="crawler",
            business_key=business_key,
            title="施工招标公告",
            source_id=source.source_id,
            tenant_code="platform_public",
            visibility_scope="public",
            status="pending_tag",
            metadata={"project_code_raw": cell("P-EXTRACTED", level="extracted")},
            field_sources=provenance_for("domain_type", "channel_type", "business_key", "title"),
        )

    audit = db_session.query(AuditLog).filter_by(action="ARCHIVE_WRITE_REJECTED").one()
    assert audit.error_code == "METADATA_EXTRACTION_FORBIDDEN"
    assert audit.after_payload["error_code"] == "METADATA_EXTRACTION_FORBIDDEN"
    assert audit.after_payload["target_key"] == business_key


def test_rejected_archive_write_records_audit_without_committing_caller_changes(db_session):
    source = create_exchange_source(db_session)
    source_id = source.source_id
    original_name = source.name
    business_key = "trading:src:notice-isolated-audit"

    source.name = "SHOULD_NOT_COMMIT"

    with pytest.raises(ValueError, match="METADATA_EXTRACTION_FORBIDDEN"):
        create_archive(
            db_session,
            domain_type="trading",
            channel_type="crawler",
            business_key=business_key,
            title="施工招标公告",
            source_id=source_id,
            tenant_code="platform_public",
            visibility_scope="public",
            status="pending_tag",
            metadata={"project_code_raw": cell("P-EXTRACTED", level="extracted")},
            field_sources=provenance_for("domain_type", "channel_type", "business_key", "title"),
        )

    db_session.rollback()

    reloaded = db_session.get(type(source), source_id)
    audit = db_session.query(AuditLog).filter_by(action="ARCHIVE_WRITE_REJECTED").one()
    assert audit.error_code == "METADATA_EXTRACTION_FORBIDDEN"
    assert reloaded.name == original_name
    assert audit.after_payload["error_code"] == "METADATA_EXTRACTION_FORBIDDEN"
    assert audit.after_payload["target_key"] == business_key


def test_create_archive_rejects_extracted_field_source_and_records_audit(db_session):
    source = create_exchange_source(db_session)
    business_key = "trading:src:notice-extracted-source"

    with pytest.raises(ValueError, match="FIELD_SOURCE_EXTRACTION_FORBIDDEN"):
        create_archive(
            db_session,
            domain_type="trading",
            channel_type="crawler",
            business_key=business_key,
            title="施工招标公告",
            source_id=source.source_id,
            tenant_code="platform_public",
            visibility_scope="public",
            status="pending_tag",
            field_sources=provenance_for(
                "domain_type",
                "channel_type",
                "business_key",
                "title",
                level="extracted",
            ),
        )

    audit = db_session.query(AuditLog).filter_by(action="ARCHIVE_WRITE_REJECTED").one()
    assert audit.error_code == "FIELD_SOURCE_EXTRACTION_FORBIDDEN"
    assert audit.after_payload["error_code"] == "FIELD_SOURCE_EXTRACTION_FORBIDDEN"
    assert audit.after_payload["target_key"] == business_key


def test_create_archive_rejects_duplicate_business_key(db_session):
    source = create_exchange_source(db_session)
    payload = {
        "domain_type": "trading",
        "channel_type": "crawler",
        "business_key": "trading:src:notice-duplicate",
        "title": "施工招标公告",
        "source_id": source.source_id,
        "tenant_code": "platform_public",
        "visibility_scope": "public",
        "status": "pending_tag",
        "field_sources": provenance_for("domain_type", "channel_type", "business_key", "title"),
    }
    create_archive(db_session, **payload)

    with pytest.raises(ValueError, match="ARCHIVE_BUSINESS_KEY_EXISTS"):
        create_archive(db_session, **payload)


def test_create_archive_from_ingest_event_builds_archive_and_primary_file(db_session):
    source = create_exchange_source(db_session)
    result = register_asset(
        db_session,
        FakeObjectStore(),
        tenant_code="platform_public",
        source_type="public_resource_exchange",
        batch_id="batch-010",
        file_name="notice.html",
        content=b"<html>notice</html>",
        source_id=source.source_id,
        source_url="https://example.gov.cn/notice/010",
        source_item_key="notice-010",
    )

    archive = create_archive_from_ingest_event(
        db_session,
        event_id=result.ingest_event_id,
        domain_type="trading",
        channel_type="crawler",
        title="施工招标公告",
        visibility_scope="public",
        status="pending_tag",
        metadata={"notice_type_raw": cell("招标公告", level="crawler", by="collector"), "project_code_raw": cell("P-010", level="crawler", by="collector")},
        field_sources=provenance_for("domain_type", "channel_type", "business_key", "title", level="crawler", by="collector"),
        actor_type="system",
        actor_id="collector",
    )
    detail = get_archive_detail(db_session, archive.archive_id)

    assert archive.business_key == f"trading:{source.source_id}:notice-010"
    assert archive.tenant_code == "platform_public"
    assert archive.source_id == source.source_id
    assert archive.batch_id == "batch-010"
    assert archive.source_item_key == "notice-010"
    assert archive.source_url == "https://example.gov.cn/notice/010"
    assert detail["files"][0]["file_id"] == result.file_id
    assert detail["files"][0]["file_role"] == "web_snapshot"
    assert detail["files"][0]["is_primary"] is True


def test_create_archive_from_ingest_event_records_crawl_lineage(db_session):
    source = create_exchange_source(db_session)
    discovered_at = datetime(2026, 6, 19, 9, 0, tzinfo=UTC)
    result = register_asset(
        db_session,
        FakeObjectStore(),
        tenant_code="platform_public",
        source_type="public_resource_exchange",
        batch_id="batch-lineage",
        file_name="notice.html",
        content=b"<html>notice</html>",
        source_id=source.source_id,
        source_url="https://example.gov.cn/notice/lineage",
        source_item_key="notice-lineage",
        source_modified_at=discovered_at,
        source_metadata={"column_path_raw": "工程建设/招标公告", "discovered_at": discovered_at.isoformat()},
    )
    event = db_session.get(IngestEvent, result.ingest_event_id)

    archive = create_archive_from_ingest_event(
        db_session,
        event_id=result.ingest_event_id,
        domain_type="trading",
        channel_type="crawler",
        title="施工招标公告",
        metadata={"notice_type_raw": cell("招标公告", level="crawler", by="collector")},
        field_sources=provenance_for("domain_type", "channel_type", "title", level="crawler", by="collector"),
        actor_type="system",
        actor_id="collector",
    )

    lineage = db_session.query(CrawlLineage).filter_by(archive_id=archive.archive_id).one()
    assert lineage.source_id == source.source_id
    assert lineage.task_id is None
    assert lineage.batch_id == "batch-lineage"
    assert lineage.source_item_key == "notice-lineage"
    assert lineage.source_url == "https://example.gov.cn/notice/lineage"
    assert lineage.source_metadata == {"column_path_raw": "工程建设/招标公告", "discovered_at": discovered_at.isoformat()}
    assert lineage.discovered_at.isoformat().startswith("2026-06-19T09:00:00")
    assert lineage.fetched_at == event.ingested_at


def test_create_quota_archive_from_ingest_event_prefers_raw_metadata_for_business_key(db_session):
    source = create_quota_source(db_session)
    result = register_asset(
        db_session,
        FakeObjectStore(),
        tenant_code="platform_public",
        source_type="public_resource_exchange",
        batch_id="batch-quota-raw",
        file_name="quota.pdf",
        content=b"quota",
        source_id=source.source_id,
        source_item_key="quota-raw",
    )

    archive = create_archive_from_ingest_event(
        db_session,
        event_id=result.ingest_event_id,
        domain_type="quota",
        channel_type="crawler",
        title="浙江省建设工程计价依据",
        metadata={
            "region_raw": cell("330000", level="crawler", by="collector"),
            "quota_code_raw": cell("JZZS2020", level="crawler", by="collector"),
            "version": cell("2020", level="crawler", by="collector"),
            "effective_date": cell("2020-01-01", level="crawler", by="collector"),
        },
        field_sources=provenance_for("domain_type", "channel_type", "title", level="crawler", by="collector"),
        actor_type="system",
        actor_id="collector",
    )

    assert archive.business_key == "quota:330000:JZZS2020:2020:2020-01-01"
    assert archive.field_sources["business_key"]["source_level"] == "crawler"


def test_create_policy_archive_from_ingest_event_prefers_raw_metadata_for_business_key(db_session):
    source = create_policy_source(db_session)
    result = register_asset(
        db_session,
        FakeObjectStore(),
        tenant_code="platform_public",
        source_type="public_resource_exchange",
        batch_id="batch-policy-raw",
        file_name="policy.pdf",
        content=b"policy",
        source_id=source.source_id,
        source_item_key="policy-raw",
    )

    archive = create_archive_from_ingest_event(
        db_session,
        event_id=result.ingest_event_id,
        domain_type="policy_regulation",
        channel_type="crawler",
        title="浙江省建设工程造价管理办法",
        metadata={
            "issuing_authority_raw": cell("浙江省住建厅", level="crawler", by="collector"),
            "document_no_raw": cell("浙建发〔2020〕1号", level="crawler", by="collector"),
            "publish_date_raw": cell("2020-06-01", level="crawler", by="collector"),
        },
        field_sources=provenance_for("domain_type", "channel_type", "title", level="crawler", by="collector"),
        actor_type="system",
        actor_id="collector",
    )

    assert archive.business_key == "policy:浙建发〔2020〕1号"
    assert archive.field_sources["business_key"]["source_level"] == "crawler"


def test_create_archive_from_ingest_event_rejects_duplicate_source_item(db_session):
    source = create_exchange_source(db_session)
    result = register_asset(
        db_session,
        FakeObjectStore(),
        tenant_code="platform_public",
        source_type="public_resource_exchange",
        batch_id="batch-011",
        file_name="notice.html",
        content=b"<html>notice duplicate</html>",
        source_id=source.source_id,
        source_item_key="notice-011",
    )
    payload = {
        "event_id": result.ingest_event_id,
        "domain_type": "trading",
        "channel_type": "crawler",
        "title": "施工招标公告",
        "visibility_scope": "public",
        "status": "pending_tag",
        "field_sources": provenance_for("domain_type", "channel_type", "business_key", "title", level="crawler", by="collector"),
    }
    create_archive_from_ingest_event(db_session, **payload)

    with pytest.raises(ValueError, match="ARCHIVE_BUSINESS_KEY_EXISTS"):
        create_archive_from_ingest_event(db_session, **payload)


def test_list_archives_returns_counts_and_primary_file(db_session):
    source = create_exchange_source(db_session)
    archive = create_archive(
        db_session,
        domain_type="trading",
        channel_type="crawler",
        business_key="trading:src:notice-002",
        title="学校施工招标公告",
        source_id=source.source_id,
        tenant_code="platform_public",
        visibility_scope="public",
        status="pending_tag",
        field_sources=provenance_for("domain_type", "channel_type", "business_key", "title"),
    )
    main = create_asset(db_session, file_name="notice.html", file_ext=".html")
    priced = create_asset(db_session, file_name="清单.cjz", file_ext=".cjz")
    attach_file(db_session, archive.archive_id, main.file_id, file_role="main_document", is_primary=True, sort_order=20)
    attach_file(db_session, archive.archive_id, priced.file_id, file_role=None, sort_order=30)

    rows = list_archives(db_session, domain_type="trading", search="学校")

    assert len(rows) == 1
    assert rows[0]["file_count"] == 2
    assert rows[0]["priced_source_count"] == 1
    assert rows[0]["primary_file"]["file_id"] == main.file_id
    assert rows[0]["primary_file"]["file_role"] == "main_document"


def test_get_archive_detail_returns_files_sorted_by_sort_order(db_session):
    source = create_exchange_source(db_session)
    archive = create_archive(
        db_session,
        domain_type="trading",
        channel_type="crawler",
        business_key="trading:src:notice-003",
        title="施工招标公告",
        source_id=source.source_id,
        tenant_code="platform_public",
        visibility_scope="public",
        status="pending_tag",
        field_sources=provenance_for("domain_type", "channel_type", "business_key", "title"),
    )
    later = create_asset(db_session, file_name="later.pdf", file_ext=".pdf")
    earlier = create_asset(db_session, file_name="earlier.html", file_ext=".html")
    attach_file(db_session, archive.archive_id, later.file_id, file_role="attachment", sort_order=30)
    attach_file(db_session, archive.archive_id, earlier.file_id, file_role="web_snapshot", sort_order=10)

    detail = get_archive_detail(db_session, archive.archive_id)

    assert [item["file_id"] for item in detail["files"]] == [earlier.file_id, later.file_id]
    assert "project_group_key" not in detail
    assert "winning_amount" not in detail


def test_patch_archive_updates_metadata_and_ready_event_without_delivery(db_session):
    source = create_exchange_source(db_session)
    archive = create_archive(
        db_session,
        domain_type="trading",
        channel_type="crawler",
        business_key="trading:src:notice-004",
        title="施工招标公告",
        source_id=source.source_id,
        tenant_code="platform_public",
        visibility_scope="public",
        status="pending_tag",
        metadata={"notice_type_raw": cell("招标公告")},
        field_sources=provenance_for("domain_type", "channel_type", "business_key", "title"),
    )

    updated = patch_archive(
        db_session,
        archive.archive_id,
        status="ready_for_governance",
        metadata={"project_code_raw": cell("P-004")},
        field_sources=provenance_for("domain_type", "channel_type", "business_key", "title"),
        actor_type="platform_ops",
        actor_id="user-001",
    )

    events = db_session.query(ArchiveEvent).filter_by(archive_id=archive.archive_id).all()
    audit = db_session.query(AuditLog).filter_by(target_id=archive.archive_id).one()
    assert updated.status == "ready_for_governance"
    assert updated.metadata_payload == {"notice_type_raw": cell("招标公告"), "project_code_raw": cell("P-004")}
    assert events[-1].event_type == "ARCHIVE_READY_FOR_GOVERNANCE"
    outbox = db_session.query(Outbox).filter_by(event_id=events[-1].event_id).one()
    assert outbox.status == "pending"
    assert audit.action == "ARCHIVE_TAGGED"


def test_patch_archive_rejects_missing_field_source(db_session):
    source = create_exchange_source(db_session)
    archive = create_archive(
        db_session,
        domain_type="trading",
        channel_type="crawler",
        business_key="trading:src:notice-missing-source",
        title="施工招标公告",
        source_id=source.source_id,
        tenant_code="platform_public",
        visibility_scope="public",
        status="pending_tag",
        field_sources=provenance_for("domain_type", "channel_type", "business_key", "title"),
    )

    with pytest.raises(ValueError, match="FIELD_SOURCE_MISSING: region_code"):
        patch_archive(
            db_session,
            archive.archive_id,
            region_code="330100",
            field_sources=provenance_for("domain_type", "channel_type", "business_key", "title"),
        )


def test_patch_archive_missing_field_source_records_audit_without_mutating_archive(db_session):
    source = create_exchange_source(db_session)
    archive = create_archive(
        db_session,
        domain_type="trading",
        channel_type="crawler",
        business_key="trading:src:notice-missing-source-audit",
        title="施工招标公告",
        source_id=source.source_id,
        tenant_code="platform_public",
        visibility_scope="public",
        status="pending_tag",
        field_sources=provenance_for("domain_type", "channel_type", "business_key", "title"),
    )
    archive_id = archive.archive_id

    with pytest.raises(ValueError, match="FIELD_SOURCE_MISSING: region_code"):
        patch_archive(
            db_session,
            archive_id,
            region_code="330100",
            field_sources=provenance_for("domain_type", "channel_type", "business_key", "title"),
        )

    db_session.rollback()

    reloaded = db_session.get(type(archive), archive_id)
    audit = db_session.query(AuditLog).filter_by(action="ARCHIVE_WRITE_REJECTED").one()
    assert reloaded.region_code is None
    assert audit.error_code == "FIELD_SOURCE_MISSING"
    assert audit.after_payload["error_code"] == "FIELD_SOURCE_MISSING"
    assert audit.after_payload["target_key"] == "trading:src:notice-missing-source-audit"


def test_patch_archive_rejects_non_object_field_sources(db_session):
    source = create_exchange_source(db_session)
    archive = create_archive(
        db_session,
        domain_type="trading",
        channel_type="crawler",
        business_key="trading:src:notice-invalid-field-sources",
        title="施工招标公告",
        source_id=source.source_id,
        tenant_code="platform_public",
        visibility_scope="public",
        status="pending_tag",
        field_sources=provenance_for("domain_type", "channel_type", "business_key", "title"),
    )

    with pytest.raises(ValueError, match="field_sources must be a JSON object"):
        patch_archive(db_session, archive.archive_id, field_sources=["title"])


def test_attach_file_enforces_tenant_and_priced_source_rules(db_session):
    source = create_exchange_source(db_session)
    archive = create_archive(
        db_session,
        domain_type="trading",
        channel_type="crawler",
        business_key="trading:src:notice-005",
        title="施工招标公告",
        source_id=source.source_id,
        tenant_code="platform_public",
        visibility_scope="public",
        status="pending_tag",
        field_sources=provenance_for("domain_type", "channel_type", "business_key", "title"),
    )
    other_tenant = create_asset(db_session, tenant_code="tenant_a", file_name="notice.html", file_ext=".html")
    pdf = create_asset(db_session, file_name="notice.pdf", file_ext=".pdf")
    cjz = create_asset(db_session, file_name="清单.cjz", file_ext=".cjz")

    with pytest.raises(ValueError, match="ARCHIVE_FILE_TENANT_MISMATCH"):
        attach_file(db_session, archive.archive_id, other_tenant.file_id, file_role="web_snapshot")

    with pytest.raises(ValueError, match="PRICED_SOURCE_ROLE_MISMATCH"):
        attach_file(db_session, archive.archive_id, pdf.file_id, file_role="priced_source")

    mounted = attach_file(db_session, archive.archive_id, cjz.file_id, file_role=None)

    assert mounted.file_role == "priced_source"


def test_archived_archive_allows_additive_representation_without_bumping_version(db_session):
    source = create_exchange_source(db_session)
    archive = create_archive(
        db_session,
        domain_type="trading",
        channel_type="crawler",
        business_key="trading:src:notice-006",
        title="施工招标公告",
        source_id=source.source_id,
        tenant_code="platform_public",
        visibility_scope="public",
        status="archived",
        field_sources=provenance_for("domain_type", "channel_type", "business_key", "title"),
    )
    asset = create_asset(db_session, file_name="notice.html", file_ext=".html")

    mounted = attach_file(db_session, archive.archive_id, asset.file_id, file_role="web_snapshot")
    db_session.refresh(archive)

    event = db_session.query(ArchiveEvent).filter_by(archive_id=archive.archive_id).order_by(ArchiveEvent.occurred_at.desc()).first()
    audit = db_session.query(AuditLog).filter_by(target_id=archive.archive_id, action="ADD_REPRESENTATION").one()
    assert mounted.file_id == asset.file_id
    assert archive.version == 1
    assert event.event_type == "ADD_REPRESENTATION"
    assert audit.after_payload["file_id"] == asset.file_id
