import pytest
from datetime import UTC, datetime, timedelta

from app.chongqing_cost_info import (
    CHONGQING_COST_INFO_PARSER_VERSION,
    QUERY_INFO_PRICE_ENDPOINT,
    bootstrap_chongqing_cookie_with_playwright,
    chongqing_cost_info_source_config,
    fanout_chongqing_pdf_tasks,
    ingest_latest_chongqing_pdf_issue,
    run_due_chongqing_pdf_tasks,
    run_chongqing_pdf_incremental,
)
from app.collection import create_data_source
from app.models import Archive, ArchiveEvent, ArchiveFile, CollectionTask, CrawlLineage, FileProcessing, Outbox
from app.storage import FakeObjectStore


class FakeChongqingClient:
    def __init__(self):
        self.calls = []

    def post_form_json(self, url, payload):
        self.calls.append(("POST_FORM", url, payload))
        if QUERY_INFO_PRICE_ENDPOINT in url:
            raise AssertionError("price row endpoint must not be called in Layer 0")
        assert "QueryFileDownInfoList" in url
        assert payload == {
            "type": "DownPdf",
            "keyWord": "",
            "orderBy": "",
            "isontopNum": "2",
            "pageIndex": "0",
            "pageSize": "-1",
        }
        return {
            "TotalCount": 1,
            "_Items": [
                {
                    "guid": "e03bac18-05e3-451c-bde8-94d94a03adbb",
                    "fileTitle": "2026年第六期 总第415期",
                    "fileName": "水印-重庆工程造价2026-6期6-17.pdf",
                    "fileSize": "4547663",
                    "filePath": "|9634e1c1-0039-448f-8bf2-6c4962ceacfc.pdf|水印-重庆工程造价2026-6期6-17.pdf|4547663",
                    "pubDate": "2026-06-18T14:17:26.163",
                }
            ]
        }

    def get_bytes(self, url):
        self.calls.append(("GET", url, None))
        if QUERY_INFO_PRICE_ENDPOINT in url:
            raise AssertionError("price row endpoint must not be called in Layer 0")
        assert url == "http://manage.cqsgczjxx.org/_UploadFile/9634e1c1-0039-448f-8bf2-6c4962ceacfc.pdf"
        return b"%PDF-1.7\nreal cq bytes\n%%EOF", "application/pdf"


def cell_value(cell):
    return cell["value"]


def test_chongqing_source_config_records_pdf_only_tier2_strategy():
    config = chongqing_cost_info_source_config()

    assert config["stable"]["site_id"] == "cost_info.cq.cqsgczjxx"
    assert config["stable"]["region_code"] == "500000"
    assert config["stable"]["publisher_type"] == "cost_station_public_institution"
    assert config["parser"]["active_parser_version"] == CHONGQING_COST_INFO_PARSER_VERSION
    parser = config["parser"]["parsers"][CHONGQING_COST_INFO_PARSER_VERSION]
    assert parser["scope"] == "pdf_file_list_only"
    assert parser["file_list_endpoint"].endswith("/Service/FileDownInfoMgeSvr.assx/QueryFileDownInfoList")
    assert QUERY_INFO_PRICE_ENDPOINT in parser["prohibited_endpoints"]
    assert config["ops"]["crawl_strategy_tier"] == "tier2_dynamic_html"
    assert config["ops"]["session_bootstrap"] == "playwright_cookie_then_http"


class _FakeChongqingPage:
    def goto(self, *args, **kwargs):
        ...

    def wait_for_timeout(self, *args, **kwargs):
        ...


class _FakeChongqingContext:
    def __init__(self, cookies):
        self._cookies = cookies

    def new_page(self):
        return _FakeChongqingPage()

    def cookies(self):
        return self._cookies


class _FakeChongqingBrowser:
    def __init__(self, cookies):
        self._cookies = cookies

    def new_context(self, **kwargs):
        return _FakeChongqingContext(self._cookies)

    def close(self):
        ...


class _FakeChongqingChromium:
    def __init__(self, cookies):
        self._cookies = cookies

    def launch(self, **kwargs):
        return _FakeChongqingBrowser(self._cookies)


class _FakeChongqingPlaywright:
    def __init__(self, cookies):
        self.chromium = _FakeChongqingChromium(cookies)


class _FakeChongqingStarter:
    """Context manager mimicking ``sync_playwright()`` for unit tests."""

    def __init__(self, cookies):
        self._cookies = cookies

    def __enter__(self):
        return _FakeChongqingPlaywright(self._cookies)

    def __exit__(self, *exc):
        return False


def _fake_chongqing_playwright(cookies):
    return lambda: _FakeChongqingStarter(cookies)


def test_chongqing_bootstrap_formats_browser_cookies_as_header():
    cookies = [
        {"name": "anti", "value": "token", "domain": "www.cqsgczjxx.org"},
        {"name": "other", "value": "value", "domain": "www.cqsgczjxx.org"},
    ]
    assert (
        bootstrap_chongqing_cookie_with_playwright(playwright_starter=_fake_chongqing_playwright(cookies))
        == "anti=token; other=value"
    )


def test_chongqing_bootstrap_raises_when_challenge_cookie_missing():
    import pytest

    with pytest.raises(ValueError, match="CHONGQING_CHALLENGE_COOKIE_MISSING"):
        bootstrap_chongqing_cookie_with_playwright(playwright_starter=_fake_chongqing_playwright([]))


def test_chongqing_pdf_only_issue_ingests_archive_without_touching_price_json(db_session):
    config = chongqing_cost_info_source_config()
    source = create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="重庆市建设工程造价信息网",
        data_domain="cost_info",
        region_code="500000",
        config=config,
    )
    client = FakeChongqingClient()

    archive = ingest_latest_chongqing_pdf_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        year="2026",
        actor_id="cq-crawler-test",
    )
    db_session.refresh(archive)

    assert all(QUERY_INFO_PRICE_ENDPOINT not in call[1] for call in client.calls)
    assert [call[0] for call in client.calls] == ["POST_FORM", "GET"]
    assert archive.region_code == "500000"
    assert archive.status == "collected"
    assert archive.publish_date.isoformat() == "2026-06-18"
    assert archive.business_key == (
        f"cost_info:{source.source_id}:500000:2026-06:2026年第六期 总第415期"
    )
    assert cell_value(archive.metadata_payload["period_start"]) == "2026-06"
    assert cell_value(archive.metadata_payload["total_issue_no"]) == 415
    assert cell_value(archive.metadata_payload["price_source_type"]) == "info_price"
    assert archive.metadata_payload["price_source_type"]["source_level"] == "crawler"
    assert archive.metadata_payload["tax_type"]["value"] is None
    assert archive.metadata_payload["tax_type"]["source_level"] == "crawler"
    assert cell_value(archive.metadata_payload["extractability_hint"]) == "low_pdf_only"
    assert archive.field_sources["business_key"]["source_level"] == "crawler"
    assert archive.field_sources["region_code"]["source_level"] == "crawler"

    mounted = db_session.query(ArchiveFile).filter_by(archive_id=archive.archive_id).one()
    assert mounted.file_role == "main_document"
    assert mounted.representation_role == "primary"
    assert mounted.is_primary is True
    assert mounted.display_name == "水印-重庆工程造价2026-6期6-17.pdf"

    lineage = db_session.query(CrawlLineage).filter_by(archive_id=archive.archive_id).one()
    assert lineage.parser_version == CHONGQING_COST_INFO_PARSER_VERSION
    assert lineage.source_metadata["detail_url"] == "http://www.cqsgczjxx.org/Pages/CQZJW/priceInformation.aspx"
    assert lineage.source_metadata["listed_title"] == "2026年第六期 总第415期"
    assert lineage.source_metadata["source_file_guid"] == "e03bac18-05e3-451c-bde8-94d94a03adbb"

    events = db_session.query(ArchiveEvent).filter_by(archive_id=archive.archive_id).all()
    assert {event.event_type for event in events} == {"ARCHIVE_CREATED", "ARCHIVE_FILE_ATTACHED"}
    assert db_session.query(Outbox).count() == len(events)
    assert db_session.query(FileProcessing).count() == 0


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("2026年第六期 总第415期", "2026-06"),
        ("2026年第12期 总第421期", "2026-12"),
    ],
)
def test_chongqing_period_supports_chinese_issue_numbers(db_session, title, expected):
    source = create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="重庆市建设工程造价信息网",
        data_domain="cost_info",
        region_code="500000",
        config=chongqing_cost_info_source_config(),
    )
    client = FakeChongqingClient()
    client.post_form_json = lambda url, payload: {
        "_Items": [
            {
                "guid": "period-test",
                "fileTitle": title,
                "fileName": "水印-重庆工程造价.pdf",
                "filePath": "|9634e1c1-0039-448f-8bf2-6c4962ceacfc.pdf|水印-重庆工程造价.pdf|1",
                "pubDate": "2026-06-18T14:17:26.163",
            }
        ]
    }

    archive = ingest_latest_chongqing_pdf_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        year="2026",
        actor_id="cq-crawler-test",
    )

    assert cell_value(archive.metadata_payload["period_start"]) == expected


class FakeChongqingBulkClient(FakeChongqingClient):
    def __init__(self, rows):
        super().__init__()
        self.rows = rows

    def post_form_json(self, url, payload):
        self.calls.append(("POST_FORM", url, payload))
        if QUERY_INFO_PRICE_ENDPOINT in url:
            raise AssertionError("price row endpoint must not be called in Layer 0")
        return {"TotalCount": len(self.rows), "_Items": self.rows}

    def get_bytes(self, url):
        self.calls.append(("GET", url, None))
        return b"%PDF-1.7\nbulk cq bytes\n%%EOF", "application/pdf"


def create_chongqing_source(db_session):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="重庆市建设工程造价信息网",
        data_domain="cost_info",
        region_code="500000",
        config=chongqing_cost_info_source_config(),
    )


def cq_row(title, storage_name, display_name, pub_date, guid):
    return {
        "guid": guid,
        "fileTitle": title,
        "fileName": display_name,
        "fileSize": "4547663",
        "filePath": f"|{storage_name}|{display_name}|4547663",
        "pubDate": pub_date,
    }


def test_chongqing_incremental_ingests_list_rows_with_form_payload_and_no_price_json(db_session):
    source = create_chongqing_source(db_session)
    client = FakeChongqingBulkClient(
        [
            cq_row("2026年第六期 总第415期", "415.pdf", "水印-重庆工程造价2026-6期.pdf", "2026-06-18T14:17:26.163", "guid-415"),
            cq_row("2026年第五期 总第414期", "414.pdf", "水印-重庆工程造价2026-5期.pdf", "2026-05-20T10:17:55.063", "guid-414"),
        ]
    )

    report = run_chongqing_pdf_incremental(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        actor_id="cq-incremental-test",
        as_of_date="2026-06-21",
    )

    assert report["listed_count"] == 2
    assert report["ingested_count"] == 2
    assert report["stopped_on_existing"] is False
    assert report["cursor_source_item_key"] == "guid-415"
    assert report["crawl_lag_days"] == 3
    assert client.calls[0] == (
        "POST_FORM",
        "http://www.cqsgczjxx.org/Service/FileDownInfoMgeSvr.assx/QueryFileDownInfoList",
        {"type": "DownPdf", "keyWord": "", "orderBy": "", "isontopNum": "2", "pageIndex": "0", "pageSize": "-1"},
    )
    assert all(QUERY_INFO_PRICE_ENDPOINT not in call[1] for call in client.calls)
    assert db_session.query(Archive).filter_by(domain_type="cost_info").count() == 2
    assert db_session.query(FileProcessing).count() == 0


def test_chongqing_incremental_stops_on_existing_business_key_before_download(db_session):
    source = create_chongqing_source(db_session)
    row = cq_row("2026年第六期 总第415期", "415.pdf", "水印-重庆工程造价2026-6期.pdf", "2026-06-18T14:17:26.163", "guid-415")
    ingest_latest_chongqing_pdf_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=FakeChongqingBulkClient([row]),
        year="2026",
        actor_id="cq-existing-seed",
    )
    client = FakeChongqingBulkClient([row])

    report = run_chongqing_pdf_incremental(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        actor_id="cq-incremental-test",
    )

    assert report["ingested_count"] == 0
    assert report["skipped_existing_count"] == 1
    assert report["stopped_on_existing"] is True
    assert [call[0] for call in client.calls] == ["POST_FORM"]


def test_chongqing_history_backfill_skips_existing_and_continues(db_session):
    source = create_chongqing_source(db_session)
    existing = cq_row("2026年第六期 总第415期", "415.pdf", "水印-重庆工程造价2026-6期.pdf", "2026-06-18T14:17:26.163", "guid-415")
    older = cq_row("2026年第五期 总第414期", "414.pdf", "水印-重庆工程造价2026-5期.pdf", "2026-05-20T10:17:55.063", "guid-414")
    ingest_latest_chongqing_pdf_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=FakeChongqingBulkClient([existing]),
        year="2026",
        actor_id="cq-existing-seed",
    )
    client = FakeChongqingBulkClient([existing, older])

    report = run_chongqing_pdf_incremental(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        actor_id="cq-history-test",
        stop_on_existing=False,
    )

    assert report["ingested_count"] == 1
    assert report["skipped_existing_count"] == 1
    assert report["stopped_on_existing"] is False
    assert [call[0] for call in client.calls] == ["POST_FORM", "GET"]
    assert db_session.query(Archive).filter_by(domain_type="cost_info").count() == 2


def test_chongqing_task_fanout_creates_one_pending_task_per_issue_with_polite_schedule(db_session):
    source = create_chongqing_source(db_session)
    rows = [
        cq_row("2026年第六期 总第415期", "415.pdf", "水印-重庆工程造价2026-6期.pdf", "2026-06-18T14:17:26.163", "guid-415"),
        cq_row("2026年第五期 总第414期", "414.pdf", "水印-重庆工程造价2026-5期.pdf", "2026-05-20T10:17:55.063", "guid-414"),
    ]
    base_time = datetime(2026, 6, 21, 10, 0, tzinfo=UTC)

    report = fanout_chongqing_pdf_tasks(
        db_session,
        source_id=source.source_id,
        rows=rows,
        mode="history_backfill",
        base_time=base_time,
        min_delay_seconds=3,
    )

    assert report == {
        "source_id": source.source_id,
        "listed_count": 2,
        "created_count": 2,
        "skipped_existing_archive_count": 0,
        "skipped_existing_task_count": 0,
    }
    tasks = db_session.query(CollectionTask).filter_by(task_type="crawl_issue").order_by(CollectionTask.scheduled_at).all()
    assert [task.status for task in tasks] == ["pending", "pending"]
    assert [task.priority for task in tasks] == [20, 20]
    assert tasks[0].scheduled_at.replace(tzinfo=UTC) == base_time
    assert tasks[1].scheduled_at.replace(tzinfo=UTC) == base_time + timedelta(seconds=3)
    assert tasks[0].config_override["queue_mode"] == "history_backfill"
    assert tasks[0].config_override["parser_version"] == CHONGQING_COST_INFO_PARSER_VERSION
    assert tasks[0].config_override["source_item"]["guid"] == "guid-415"
    assert tasks[0].config_override["source_item"]["filePath"] == "|415.pdf|水印-重庆工程造价2026-6期.pdf|4547663"


def test_chongqing_task_fanout_skips_existing_archive_and_existing_task(db_session):
    source = create_chongqing_source(db_session)
    existing = cq_row("2026年第六期 总第415期", "415.pdf", "水印-重庆工程造价2026-6期.pdf", "2026-06-18T14:17:26.163", "guid-415")
    pending = cq_row("2026年第五期 总第414期", "414.pdf", "水印-重庆工程造价2026-5期.pdf", "2026-05-20T10:17:55.063", "guid-414")
    ingest_latest_chongqing_pdf_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=FakeChongqingBulkClient([existing]),
        year="2026",
        actor_id="cq-existing-seed",
    )
    fanout_chongqing_pdf_tasks(
        db_session,
        source_id=source.source_id,
        rows=[pending],
        mode="history_backfill",
        base_time=datetime(2026, 6, 21, 10, 0, tzinfo=UTC),
    )

    report = fanout_chongqing_pdf_tasks(
        db_session,
        source_id=source.source_id,
        rows=[existing, pending],
        mode="history_backfill",
        base_time=datetime(2026, 6, 21, 10, 0, tzinfo=UTC),
    )

    assert report["created_count"] == 0
    assert report["skipped_existing_archive_count"] == 1
    assert report["skipped_existing_task_count"] == 1
    assert db_session.query(CollectionTask).filter_by(task_type="crawl_issue").count() == 1


def test_chongqing_due_task_worker_marks_done_without_processing(db_session):
    source = create_chongqing_source(db_session)
    row = cq_row("2026年第六期 总第415期", "415.pdf", "水印-重庆工程造价2026-6期.pdf", "2026-06-18T14:17:26.163", "guid-415")
    due_at = datetime(2026, 6, 21, 10, 0, tzinfo=UTC)
    fanout_chongqing_pdf_tasks(
        db_session,
        source_id=source.source_id,
        rows=[row],
        mode="history_backfill",
        base_time=due_at,
    )
    client = FakeChongqingBulkClient([])
    storage = FakeObjectStore()

    report = run_due_chongqing_pdf_tasks(
        db_session,
        storage,
        source_id=source.source_id,
        client=client,
        now=due_at,
        limit=1,
        retry_delay_seconds=60,
    )

    task = db_session.query(CollectionTask).filter_by(task_type="crawl_issue").one()
    assert report == {"leased_count": 1, "done_count": 1, "retry_count": 0, "dead_letter_count": 0}
    assert task.status == "done"
    assert task.attempt == 1
    assert task.downloaded_count == 1
    assert task.new_file_count == 1
    assert db_session.query(CollectionTask).count() == 1
    assert db_session.query(Archive).filter_by(domain_type="cost_info").count() == 1
    assert db_session.query(FileProcessing).count() == 0
    assert [call[0] for call in client.calls] == ["GET"]


def test_chongqing_due_task_worker_retries_then_dead_letters_to_manual_recovery(db_session):
    source = create_chongqing_source(db_session)
    row = cq_row("2026年第六期 总第415期", "415.pdf", "水印-重庆工程造价2026-6期.pdf", "2026-06-18T14:17:26.163", "guid-415")
    now = datetime(2026, 6, 21, 10, 0, tzinfo=UTC)
    fanout_chongqing_pdf_tasks(db_session, source_id=source.source_id, rows=[row], mode="history_backfill", base_time=now)
    task = db_session.query(CollectionTask).filter_by(task_type="crawl_issue").one()
    task.max_attempts = 2
    db_session.commit()

    class FailingClient(FakeChongqingBulkClient):
        def get_bytes(self, url):
            self.calls.append(("GET", url, None))
            raise TimeoutError("site timeout")

    client = FailingClient([])
    first = run_due_chongqing_pdf_tasks(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        now=now,
        limit=1,
        retry_delay_seconds=60,
    )
    db_session.refresh(task)

    assert first == {"leased_count": 1, "done_count": 0, "retry_count": 1, "dead_letter_count": 0}
    assert task.status == "pending"
    assert task.attempt == 1
    assert task.scheduled_at.replace(tzinfo=UTC) == now + timedelta(seconds=60)
    assert task.failed_count == 1

    second = run_due_chongqing_pdf_tasks(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        now=now + timedelta(seconds=60),
        limit=1,
        retry_delay_seconds=60,
    )
    db_session.refresh(task)

    assert second == {"leased_count": 1, "done_count": 0, "retry_count": 0, "dead_letter_count": 1}
    assert task.status == "failed"
    assert task.attempt == 2
    assert task.error_code == "CHONGQING_CRAWL_TASK_FAILED"
    assert task.config_override["manual_recovery"]["spec"] == "017"
    assert task.config_override["manual_recovery"]["required"] is True
    assert db_session.query(Archive).count() == 0
