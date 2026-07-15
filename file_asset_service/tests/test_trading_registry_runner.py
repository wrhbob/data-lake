from sqlalchemy import inspect

from app.archive_service import get_archive_detail
from app.collection import create_data_source
from app.models import Archive, ArchiveEvent, ArchiveFile, CollectionTask, CrawlLineage, FileAsset, FileProcessing, IngestEvent, Outbox
from app.storage import FakeObjectStore
from app.trading_registry import (
    FETCH_STATUS_FETCHED,
    FETCH_STATUS_PENDING,
    TradingAttachment,
    TradingDiscoveredItem,
    fetch_pending_trading_archive_file,
    ingest_trading_registry_item,
    run_pending_trading_fetches,
    trading_exchange_source_config,
)


def cell_value(cell):
    return cell["value"]


def create_trading_source(db_session):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="public_resource_exchange",
        connector_type="source_registry",
        name="小型公共资源交易中心",
        data_domain="trading",
        region_code="510100",
        config=trading_exchange_source_config(
            site_id="trading.small.exchange",
            publisher_name="小型公共资源交易中心",
            entry_url="https://trade.example.gov.cn/",
            region_code="510100",
            parser_version="small-exchange.trading.v1",
            channels=[
                {
                    "channel_id": "tender_notice",
                    "notice_family": "tender",
                    "entry_url": "https://trade.example.gov.cn/tender",
                    "notice_type_raw": "招标公告",
                },
                {
                    "channel_id": "change_notice",
                    "notice_family": "change",
                    "entry_url": "https://trade.example.gov.cn/change",
                    "notice_type_raw": "变更公告",
                },
                {
                    "channel_id": "award_candidate",
                    "notice_family": "award_candidate",
                    "entry_url": "https://trade.example.gov.cn/award",
                    "notice_type_raw": "中标候选人公示",
                },
                {
                    "channel_id": "termination_notice",
                    "notice_family": "tombstone",
                    "entry_url": "https://trade.example.gov.cn/termination",
                    "notice_type_raw": "中止废标公告",
                },
            ],
        ),
    )


class PendingFetchClient:
    def __init__(self):
        self.calls = []

    def get_bytes(self, url):
        self.calls.append(url)
        return b"opaque drawing zip bytes", "application/zip"


class SelectivePendingFetchClient:
    def __init__(self, *, failing_url_fragment: str):
        self.failing_url_fragment = failing_url_fragment
        self.calls = []

    def get_bytes(self, url):
        self.calls.append(url)
        if self.failing_url_fragment in url:
            raise TimeoutError("ATTACHMENT_DOWNLOAD_TIMEOUT: total timeout 1200s")
        return f"fetched bytes for {url}".encode(), "application/zip"


def test_trading_source_config_records_multichannel_layer0_boundaries():
    config = trading_exchange_source_config(
        site_id="trading.small.exchange",
        publisher_name="小型公共资源交易中心",
        entry_url="https://trade.example.gov.cn/",
        region_code="510100",
        parser_version="small-exchange.trading.v1",
        source_priority="official",
        channels=[
            {"channel_id": "tender_notice", "notice_family": "tender", "entry_url": "https://trade.example.gov.cn/tender"},
            {"channel_id": "change_notice", "notice_family": "change", "entry_url": "https://trade.example.gov.cn/change"},
            {"channel_id": "termination_notice", "notice_family": "tombstone", "entry_url": "https://trade.example.gov.cn/termination"},
        ],
    )

    parser = config["parser"]["parsers"]["small-exchange.trading.v1"]

    assert config["stable"]["domain_type"] == "trading"
    assert config["stable"]["publisher_type"] == "exchange_center"
    assert config["stable"]["source_priority"] == "official"
    assert [channel["channel_id"] for channel in parser["channels"]] == [
        "tender_notice",
        "change_notice",
        "termination_notice",
    ]
    assert parser["attachment_rules"]["gated_policy"] == "metadata_only"
    assert ".cjz" in parser["attachment_rules"]["priced_source_extensions"]
    assert parser["red_lines"] == [
        "do_not_parse_priced_source",
        "do_not_group_project_code",
        "gated_metadata_only",
    ]


def test_trading_registry_records_pending_fetch_attachment_in_archive_manifest(db_session):
    source = create_trading_source(db_session)

    archive = ingest_trading_registry_item(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        item=TradingDiscoveredItem(
            source_item_key="notice-pending-drawing-001",
            title="重庆某学校扩建项目施工招标公告",
            publish_date="2026-06-23",
            detail_url="https://trade.example.gov.cn/tender/pending-001",
            channel_id="tender_notice",
            notice_family="tender",
            notice_type_raw="招标公告",
            project_name_raw="重庆某学校扩建项目",
            attachments=[
                TradingAttachment(
                    file_name="notice-pending-drawing-001.html",
                    content="<html>公告正文</html>".encode(),
                    url="https://trade.example.gov.cn/tender/pending-001",
                    content_type="text/html",
                    file_role="web_snapshot",
                ),
                TradingAttachment(
                    file_name="招标文件.CQZF",
                    content=b"opaque cqzf bytes",
                    url="https://trade.example.gov.cn/tender/pending-001/files/tender.cqzf",
                    content_type="application/octet-stream",
                    file_role="qingdan_package",
                ),
                TradingAttachment(
                    file_name="施工图纸.zip",
                    content=None,
                    url="https://trade.example.gov.cn/tender/pending-001/files/drawing.zip",
                    content_type="application/zip",
                    file_role="drawing",
                    fetch_status=FETCH_STATUS_PENDING,
                    metadata={
                        "access_status": "PENDING_FETCH",
                        "expected_file_size": 524288000,
                        "defer_reason": "large_attachment_offpeak",
                    },
                ),
            ],
        ),
        actor_id="trading-pending-fetch-test",
    )

    files = db_session.query(ArchiveFile).filter_by(archive_id=archive.archive_id).order_by(ArchiveFile.sort_order).all()
    roles_by_name = {mounted.display_name: mounted.file_role for mounted in files}
    pending = next(mounted for mounted in files if mounted.display_name == "施工图纸.zip")
    detail = get_archive_detail(db_session, archive.archive_id)
    detail_by_name = {row["display_name"]: row for row in detail["files"]}

    assert roles_by_name == {
        "notice-pending-drawing-001.html": "web_snapshot",
        "招标文件.CQZF": "qingdan_package",
        "施工图纸.zip": "drawing",
    }
    assert pending.file_id is None
    assert pending.fetch_status == FETCH_STATUS_PENDING
    assert pending.metadata_payload["access_status"] == "PENDING_FETCH"
    assert pending.metadata_payload["expected_file_size"] == 524288000
    assert detail_by_name["施工图纸.zip"]["file_id"] is None
    assert detail_by_name["施工图纸.zip"]["fetch_status"] == FETCH_STATUS_PENDING
    assert detail_by_name["施工图纸.zip"]["file_name"] == "施工图纸.zip"
    assert detail_by_name["招标文件.CQZF"]["fetch_status"] == FETCH_STATUS_FETCHED
    assert db_session.query(FileAsset).filter_by(file_name="施工图纸.zip").one_or_none() is None
    assert db_session.query(FileProcessing).count() == 0


def test_trading_registry_deduplicates_repeated_attachment_blob_and_role(db_session):
    source = create_trading_source(db_session)

    archive = ingest_trading_registry_item(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        item=TradingDiscoveredItem(
            source_item_key="notice-repeat-drawing-001",
            title="重复附件公告",
            publish_date="2026-07-15",
            detail_url="https://trade.example.gov.cn/tender/repeat-001",
            channel_id="tender_notice",
            notice_family="tender",
            notice_type_raw="招标公告",
            attachments=[
                TradingAttachment(
                    file_name="notice-repeat-drawing-001.html",
                    content="<html>公告正文</html>".encode(),
                    url="https://trade.example.gov.cn/tender/repeat-001",
                    content_type="text/html",
                    file_role="web_snapshot",
                ),
                TradingAttachment(
                    file_name="图纸A.rar",
                    content=b"same opaque drawing",
                    url="https://trade.example.gov.cn/tender/repeat-001/a.rar",
                    content_type="application/x-rar-compressed",
                    file_role="drawing",
                ),
                TradingAttachment(
                    file_name="图纸B.rar",
                    content=b"same opaque drawing",
                    url="https://trade.example.gov.cn/tender/repeat-001/b.rar",
                    content_type="application/x-rar-compressed",
                    file_role="drawing",
                ),
            ],
        ),
    )

    files = db_session.query(ArchiveFile).filter_by(archive_id=archive.archive_id).all()
    task = db_session.query(CollectionTask).filter_by(source_id=source.source_id).one()
    assert len(files) == 2
    assert [mounted.file_role for mounted in files].count("drawing") == 1
    assert task.status == "done"


def test_fetch_pending_trading_archive_file_fills_blob_without_parsing(db_session):
    source = create_trading_source(db_session)
    storage = FakeObjectStore()
    archive = ingest_trading_registry_item(
        db_session,
        storage,
        source_id=source.source_id,
        item=TradingDiscoveredItem(
            source_item_key="notice-pending-drawing-002",
            title="重庆某学校扩建项目施工招标公告",
            publish_date="2026-06-23",
            detail_url="https://trade.example.gov.cn/tender/pending-002",
            channel_id="tender_notice",
            notice_family="tender",
            notice_type_raw="招标公告",
            project_name_raw="重庆某学校扩建项目",
            attachments=[
                TradingAttachment(
                    file_name="notice-pending-drawing-002.html",
                    content="<html>公告正文</html>".encode(),
                    url="https://trade.example.gov.cn/tender/pending-002",
                    content_type="text/html",
                    file_role="web_snapshot",
                ),
                TradingAttachment(
                    file_name="施工图纸.zip",
                    content=None,
                    url="https://trade.example.gov.cn/tender/pending-002/files/drawing.zip",
                    content_type="application/zip",
                    file_role="drawing",
                    fetch_status=FETCH_STATUS_PENDING,
                    metadata={"access_status": "PENDING_FETCH"},
                ),
            ],
        ),
        actor_id="trading-pending-fetch-test",
    )
    pending = (
        db_session.query(ArchiveFile)
        .filter_by(archive_id=archive.archive_id, display_name="施工图纸.zip")
        .one()
    )

    fetched = fetch_pending_trading_archive_file(
        db_session,
        storage,
        archive_file_id=pending.archive_file_id,
        client=PendingFetchClient(),
        actor_id="trading-offpeak-fetch-test",
    )
    db_session.refresh(fetched)

    assert fetched.file_id is not None
    assert fetched.fetch_status == FETCH_STATUS_FETCHED
    asset = db_session.get(FileAsset, fetched.file_id)
    assert asset.file_name == "施工图纸.zip"
    assert asset.file_size == len(b"opaque drawing zip bytes")
    assert fetched.metadata_payload["access_status"] == "PUBLIC"
    assert fetched.metadata_payload["fetch_mode"] == "offpeak"
    assert db_session.query(FileProcessing).count() == 0


def test_run_pending_trading_fetches_processes_pending_rows_in_order(db_session):
    source = create_trading_source(db_session)
    storage = FakeObjectStore()
    for index in range(2):
        ingest_trading_registry_item(
            db_session,
            storage,
            source_id=source.source_id,
            item=TradingDiscoveredItem(
                source_item_key=f"notice-pending-batch-{index}",
                title=f"重庆某学校扩建项目施工招标公告{index}",
                publish_date="2026-06-23",
                detail_url=f"https://trade.example.gov.cn/tender/pending-batch-{index}",
                channel_id="tender_notice",
                notice_family="tender",
                notice_type_raw="招标公告",
                project_name_raw="重庆某学校扩建项目",
                attachments=[
                    TradingAttachment(
                        file_name=f"notice-pending-batch-{index}.html",
                        content=b"<html></html>",
                        url=f"https://trade.example.gov.cn/tender/pending-batch-{index}",
                        content_type="text/html",
                        file_role="web_snapshot",
                    ),
                    TradingAttachment(
                        file_name=f"施工图纸{index}.zip",
                        content=None,
                        url=f"https://trade.example.gov.cn/tender/pending-batch-{index}/files/drawing.zip",
                        content_type="application/zip",
                        file_role="drawing",
                        fetch_status=FETCH_STATUS_PENDING,
                        metadata={"access_status": "PENDING_FETCH"},
                    ),
                ],
            ),
            actor_id="trading-pending-fetch-test",
        )

    report = run_pending_trading_fetches(
        db_session,
        storage,
        source_id=source.source_id,
        client=PendingFetchClient(),
        limit=1,
        actor_id="trading-offpeak-batch-test",
    )

    assert report["seen_count"] == 1
    assert report["fetched_count"] == 1
    assert report["failed_count"] == 0
    assert len(report["fetched_items"]) == 1
    assert db_session.query(ArchiveFile).filter_by(fetch_status=FETCH_STATUS_PENDING).count() == 1
    assert db_session.query(ArchiveFile).filter_by(fetch_status=FETCH_STATUS_FETCHED, file_role="drawing").count() == 1


def test_run_pending_trading_fetches_marks_failure_and_continues_same_batch(db_session):
    source = create_trading_source(db_session)
    storage = FakeObjectStore()
    for index in range(2):
        ingest_trading_registry_item(
            db_session,
            storage,
            source_id=source.source_id,
            item=TradingDiscoveredItem(
                source_item_key=f"notice-pending-failure-continue-{index}",
                title=f"重庆某学校扩建项目施工招标公告{index}",
                publish_date="2026-06-23",
                detail_url=f"https://trade.example.gov.cn/tender/pending-failure-continue-{index}",
                channel_id="tender_notice",
                notice_family="tender",
                notice_type_raw="招标公告",
                project_name_raw="重庆某学校扩建项目",
                attachments=[
                    TradingAttachment(
                        file_name=f"notice-pending-failure-continue-{index}.html",
                        content=b"<html></html>",
                        url=f"https://trade.example.gov.cn/tender/pending-failure-continue-{index}",
                        content_type="text/html",
                        file_role="web_snapshot",
                    ),
                    TradingAttachment(
                        file_name=f"施工图纸{index}.zip",
                        content=None,
                        url=f"https://trade.example.gov.cn/tender/pending-failure-continue-{index}/files/drawing.zip",
                        content_type="application/zip",
                        file_role="drawing",
                        fetch_status=FETCH_STATUS_PENDING,
                        metadata={"access_status": "PENDING_FETCH"},
                    ),
                ],
            ),
            actor_id="trading-pending-fetch-test",
        )

    report = run_pending_trading_fetches(
        db_session,
        storage,
        source_id=source.source_id,
        client=SelectivePendingFetchClient(failing_url_fragment="pending-failure-continue-0"),
        limit=2,
        actor_id="trading-offpeak-batch-test",
    )

    failed = db_session.query(ArchiveFile).filter_by(display_name="施工图纸0.zip").one()
    fetched = db_session.query(ArchiveFile).filter_by(display_name="施工图纸1.zip").one()

    assert report["seen_count"] == 2
    assert report["failed_count"] == 1
    assert report["fetched_count"] == 1
    assert failed.fetch_status == FETCH_STATUS_PENDING
    assert failed.file_id is None
    assert failed.metadata_payload["access_status"] == "PENDING_FETCH"
    assert failed.metadata_payload["fetch_attempts"] == 1
    assert failed.metadata_payload["last_fetch_error"].startswith("ATTACHMENT_DOWNLOAD_TIMEOUT")
    assert failed.metadata_payload["last_fetch_actor_id"] == "trading-offpeak-batch-test"
    assert failed.metadata_payload["last_fetch_attempted_at"]
    assert fetched.fetch_status == FETCH_STATUS_FETCHED
    assert fetched.file_id is not None
    assert db_session.query(FileProcessing).count() == 0


def test_run_pending_trading_fetches_skips_failed_pending_rows_by_default(db_session):
    source = create_trading_source(db_session)
    storage = FakeObjectStore()
    for index in range(2):
        archive = ingest_trading_registry_item(
            db_session,
            storage,
            source_id=source.source_id,
            item=TradingDiscoveredItem(
                source_item_key=f"notice-pending-skip-failed-{index}",
                title=f"重庆某学校扩建项目施工招标公告{index}",
                publish_date="2026-06-23",
                detail_url=f"https://trade.example.gov.cn/tender/pending-skip-failed-{index}",
                channel_id="tender_notice",
                notice_family="tender",
                notice_type_raw="招标公告",
                project_name_raw="重庆某学校扩建项目",
                attachments=[
                    TradingAttachment(
                        file_name=f"notice-pending-skip-failed-{index}.html",
                        content=b"<html></html>",
                        url=f"https://trade.example.gov.cn/tender/pending-skip-failed-{index}",
                        content_type="text/html",
                        file_role="web_snapshot",
                    ),
                    TradingAttachment(
                        file_name=f"施工图纸{index}.zip",
                        content=None,
                        url=f"https://trade.example.gov.cn/tender/pending-skip-failed-{index}/files/drawing.zip",
                        content_type="application/zip",
                        file_role="drawing",
                        fetch_status=FETCH_STATUS_PENDING,
                        metadata={"access_status": "PENDING_FETCH"},
                    ),
                ],
            ),
            actor_id="trading-pending-fetch-test",
        )
        if index == 0:
            failed = db_session.query(ArchiveFile).filter_by(archive_id=archive.archive_id, display_name="施工图纸0.zip").one()
            failed.metadata_payload = {
                **failed.metadata_payload,
                "fetch_attempts": 1,
                "last_fetch_error": "ATTACHMENT_DOWNLOAD_TIMEOUT: total timeout 1200s",
                "last_fetch_attempted_at": "2026-06-24T10:00:00+00:00",
            }
            db_session.commit()

    report = run_pending_trading_fetches(
        db_session,
        storage,
        source_id=source.source_id,
        client=PendingFetchClient(),
        limit=1,
        actor_id="trading-offpeak-batch-test",
    )

    skipped = db_session.query(ArchiveFile).filter_by(display_name="施工图纸0.zip").one()
    fetched = db_session.query(ArchiveFile).filter_by(display_name="施工图纸1.zip").one()

    assert report["seen_count"] == 1
    assert report["failed_count"] == 0
    assert report["fetched_count"] == 1
    assert report["skipped_failed_count"] == 1
    assert skipped.fetch_status == FETCH_STATUS_PENDING
    assert skipped.file_id is None
    assert fetched.fetch_status == FETCH_STATUS_FETCHED
    assert fetched.file_id is not None


def test_ingest_trading_registry_item_mounts_priced_source_without_parsing_or_grouping(db_session):
    source = create_trading_source(db_session)

    archive = ingest_trading_registry_item(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        item=TradingDiscoveredItem(
            source_item_key="notice-20260621-001",
            title="成都市青羊区学校改扩建项目施工招标公告",
            publish_date="2026-06-21",
            detail_url="https://trade.example.gov.cn/tender/notice-001",
            channel_id="tender_notice",
            notice_family="tender",
            notice_type_raw="招标公告",
            project_code_raw="51010020260621001",
            tender_code_raw="QY-SG-2026-001",
            exchange_name="小型公共资源交易中心",
            column_path_raw="工程建设 / 招标公告",
            project_name_raw="成都市青羊区学校改扩建项目",
            engineering_type_raw="房建工程",
            discovered_at="2026-06-21T09:00:00+08:00",
            fetched_at="2026-06-21T09:05:00+08:00",
            attachments=[
                TradingAttachment(
                    file_name="成都市青羊区学校改扩建项目招标公告.html",
                    content=b"<html>notice shell only</html>",
                    url="https://trade.example.gov.cn/tender/notice-001",
                    content_type="text/html",
                ),
                TradingAttachment(
                    file_name="成都市青羊区学校改扩建项目控制价.cjz",
                    content=b"CJZ opaque bytes, never parsed",
                    url="https://trade.example.gov.cn/tender/notice-001/control.cjz",
                    content_type="application/octet-stream",
                ),
                TradingAttachment(
                    file_name="招标文件CA下载",
                    content=None,
                    url="https://trade.example.gov.cn/tender/notice-001/ca",
                    gated=True,
                    gated_reason="CA_LOGIN_REQUIRED",
                ),
            ],
        ),
        actor_id="trading-registry-test",
    )

    db_session.refresh(archive)
    detail_files = db_session.query(ArchiveFile).filter_by(archive_id=archive.archive_id).all()
    lineages = db_session.query(CrawlLineage).filter_by(archive_id=archive.archive_id).all()
    events = db_session.query(ArchiveEvent).filter_by(archive_id=archive.archive_id).all()

    assert archive.domain_type == "trading"
    assert archive.channel_type == "crawler"
    assert archive.business_key == f"trading:{source.source_id}:notice-20260621-001"
    assert "51010020260621001" not in archive.business_key
    assert cell_value(archive.metadata_payload["project_code_raw"]) == "51010020260621001"
    assert cell_value(archive.metadata_payload["notice_type_raw"]) == "招标公告"
    assert cell_value(archive.metadata_payload["gated_attachment_count"]) == 1
    assert archive.field_sources["business_key"]["source_level"] == "crawler"

    assert {mounted.file_role for mounted in detail_files} == {"web_snapshot", "priced_source"}
    assert {mounted.file_role: mounted.is_primary for mounted in detail_files}["priced_source"] is False
    assert {lineage.parser_version for lineage in lineages} == {"small-exchange.trading.v1"}
    assert lineages[0].source_metadata["project_code_raw"] == "51010020260621001"
    assert db_session.query(FileProcessing).count() == 0
    assert db_session.query(Outbox).count() == len(events)


def test_trading_registry_keeps_same_project_code_as_independent_archives(db_session):
    source = create_trading_source(db_session)
    common_project_code = "51010020260621001"

    first = ingest_trading_registry_item(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        item=TradingDiscoveredItem(
            source_item_key="notice-20260621-001",
            title="成都市青羊区学校改扩建项目施工招标公告",
            publish_date="2026-06-21",
            detail_url="https://trade.example.gov.cn/tender/notice-001",
            channel_id="tender_notice",
            notice_family="tender",
            notice_type_raw="招标公告",
            project_code_raw=common_project_code,
            exchange_name="小型公共资源交易中心",
            column_path_raw="工程建设 / 招标公告",
            discovered_at="2026-06-21T09:00:00+08:00",
            fetched_at="2026-06-21T09:05:00+08:00",
            attachments=[
                TradingAttachment(
                    file_name="notice-001.html",
                    content=b"<html>notice 1</html>",
                    url="https://trade.example.gov.cn/tender/notice-001",
                    content_type="text/html",
                )
            ],
        ),
    )
    second = ingest_trading_registry_item(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        item=TradingDiscoveredItem(
            source_item_key="notice-20260622-002",
            title="成都市青羊区学校改扩建项目施工变更公告",
            publish_date="2026-06-22",
            detail_url="https://trade.example.gov.cn/change/notice-002",
            channel_id="change_notice",
            notice_family="change",
            notice_type_raw="变更公告",
            project_code_raw=common_project_code,
            exchange_name="小型公共资源交易中心",
            column_path_raw="工程建设 / 变更公告",
            discovered_at="2026-06-22T09:00:00+08:00",
            fetched_at="2026-06-22T09:05:00+08:00",
            attachments=[
                TradingAttachment(
                    file_name="notice-002.html",
                    content=b"<html>notice 2</html>",
                    url="https://trade.example.gov.cn/change/notice-002",
                    content_type="text/html",
                )
            ],
        ),
    )

    assert first.archive_id != second.archive_id
    assert first.business_key == f"trading:{source.source_id}:notice-20260621-001"
    assert second.business_key == f"trading:{source.source_id}:notice-20260622-002"
    assert db_session.query(Archive).filter_by(domain_type="trading").count() == 2
    assert "trading_project" not in inspect(db_session.get_bind()).get_table_names()


def test_trading_registry_preserves_public_roles_and_locked_manifest(db_session):
    source = create_trading_source(db_session)

    archive = ingest_trading_registry_item(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        item=TradingDiscoveredItem(
            source_item_key="notice-full-attachment-001",
            title="成都市青羊区道路工程施工招标公告",
            publish_date="2026-06-22",
            detail_url="https://trade.example.gov.cn/tender/full-attachment-001",
            channel_id="tender_notice",
            notice_family="tender",
            notice_type_raw="招标公告",
            exchange_name="小型公共资源交易中心",
            column_path_raw="工程建设 / 招标公告",
            discovered_at="2026-06-22T09:00:00+08:00",
            fetched_at="2026-06-22T09:05:00+08:00",
            attachments=[
                TradingAttachment(
                    file_name="notice-full-attachment-001.html",
                    content=b"<html>notice shell</html>",
                    url="https://trade.example.gov.cn/tender/full-attachment-001",
                    content_type="text/html",
                ),
                TradingAttachment(
                    file_name="招标文件.CDZ",
                    content=b"opaque cdz bytes",
                    url="https://trade.example.gov.cn/tender/full-attachment-001/files/tender.cdz",
                    content_type="application/octet-stream",
                    file_role="qingdan_package",
                    metadata={
                        "access_status": "PUBLIC",
                        "source_attachment_label": "招标文件",
                        "source_event_id": "caller-spoofed-event-id",
                    },
                ),
                TradingAttachment(
                    file_name="施工图.zip",
                    content=b"opaque drawing zip bytes",
                    url="https://trade.example.gov.cn/tender/full-attachment-001/files/drawing.zip",
                    content_type="application/zip",
                    file_role="drawing",
                    metadata={"access_status": "PUBLIC", "source_attachment_label": "工程图"},
                ),
                TradingAttachment(
                    file_name="招标文件CA下载",
                    content=None,
                    url="https://trade.example.gov.cn/tender/full-attachment-001/files/locked",
                    gated=True,
                    gated_reason="CA_REQUIRED",
                    file_role="tender_doc",
                    metadata={
                        "file_name": "伪造公开招标文件.zip",
                        "url": "https://evil.example.test/spoofed.zip",
                        "access_status": "PUBLIC",
                        "gated_reason": "CALLER_OVERRIDE",
                        "file_role": "public_doc",
                        "source_attachment_label": "招标文件",
                    },
                ),
            ],
        ),
        actor_id="trading-registry-test",
    )

    files = db_session.query(ArchiveFile).filter_by(archive_id=archive.archive_id).all()
    roles_by_name = {mounted.display_name: mounted.file_role for mounted in files}
    metadata_by_name = {mounted.display_name: mounted.metadata_payload for mounted in files}
    tender_file = next(mounted for mounted in files if mounted.display_name == "招标文件.CDZ")
    tender_ingest_event = db_session.query(IngestEvent).filter_by(file_id=tender_file.file_id).one()
    gated = cell_value(archive.metadata_payload["gated_attachments"])

    assert roles_by_name == {
        "notice-full-attachment-001.html": "web_snapshot",
        "招标文件.CDZ": "qingdan_package",
        "施工图.zip": "drawing",
    }
    assert metadata_by_name["招标文件.CDZ"]["access_status"] == "PUBLIC"
    assert metadata_by_name["招标文件.CDZ"]["source_event_id"] == tender_ingest_event.event_id
    assert metadata_by_name["招标文件.CDZ"]["source_event_id"] != "caller-spoofed-event-id"
    assert metadata_by_name["施工图.zip"]["source_attachment_label"] == "工程图"
    assert cell_value(archive.metadata_payload["gated_attachment_count"]) == 1
    assert gated == [
        {
            "file_name": "招标文件CA下载",
            "url": "https://trade.example.gov.cn/tender/full-attachment-001/files/locked",
            "access_status": "LOCKED",
            "gated_reason": "CA_REQUIRED",
            "file_role": "tender_doc",
            "source_attachment_label": "招标文件",
        }
    ]
    assert "招标文件CA下载" not in roles_by_name
    assert db_session.query(FileAsset).filter_by(file_name="招标文件CA下载").one_or_none() is None
