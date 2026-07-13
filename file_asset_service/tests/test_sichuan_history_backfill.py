from datetime import UTC, datetime, timedelta

from app.collection import create_data_source
from app.info_price_coverage import build_coverage_matrix
from app.models import Archive, CollectionTask, FileProcessing
from app.sichuan_history_backfill import (
    SICHUAN_AUTO_HISTORY_REGION_CODES,
    fanout_sichuan_history_tasks,
    run_due_sichuan_history_tasks,
)
from app.sichuan_pdf_cost_info import luzhou_cost_info_source_config
from app.storage import FakeObjectStore
from app.zigong_cost_info import zigong_cost_info_source_config


class FakeLuzhouHistoryClient:
    def __init__(self, *, fail_download: bool = False):
        self.fail_download = fail_download
        self.calls = []

    def get_text(self, url):
        self.calls.append(("GET_TEXT", url))
        if url.endswith("/hyfw/gczjxx/"):
            return """
            <li><span>2026-06-12</span><a href="/hyfw/gczjxx/content_202605.html">泸州市2026年5月工程造价信息</a></li>
            <li><span>2026-05-11</span><a href="/hyfw/gczjxx/content_202604.html">泸州市2026年4月工程造价信息</a></li>
            """
        return '<a href="/upload/202605/luzhou-2026-05.pdf">PDF</a>'

    def post_form_json(self, url, data):
        raise AssertionError("luzhou history backfill must not call ajax")

    def get_bytes(self, url):
        self.calls.append(("GET_BYTES", url))
        if self.fail_download:
            raise TimeoutError("site timeout")
        return b"%PDF-1.7\nluzhou history bytes\n%%EOF", "application/pdf"


class FakeLongTitleHistoryClient(FakeLuzhouHistoryClient):
    long_title = "佛山市建设工程造价服务中心关于发布2025年11月份房屋建筑工程和市政基础设施工程台班价格指数、主要建筑材料价格指数等造价信息的通知"

    def get_text(self, url):
        self.calls.append(("GET_TEXT", url))
        if url.endswith("/hyfw/gczjxx/"):
            return f"""
            <li><span>2025-12-17</span><a href="/hyfw/gczjxx/content_long.html">{self.long_title}</a></li>
            """
        return '<a href="/upload/202511/long-title.pdf">PDF</a>'


class FailingDeyangHttpClient(FakeLuzhouHistoryClient):
    def get_text(self, url):
        self.calls.append(("GET_TEXT", url))
        raise TimeoutError("503 from normal http")


class FakeDeyangPlaywrightListClient:
    def __init__(self):
        self.calls = []

    def get_text(self, url):
        self.calls.append(("GET_TEXT", url))
        return """
        <li><span>2026-06-12</span>
          <a href="/gk/fjbm/fzjj/fadingnarong/gqgg/1956445.htm">关于发布德阳市2026年04月建筑材料市场价格信息的通知</a>
        </li>
        """


class FakeZigongHistoryClient:
    def __init__(self):
        self.calls = []

    def get_text(self, url):
        self.calls.append(("GET_TEXT", url))
        return """
        <script>
        var articleList = [{
          "id": "2058746804822818816",
          "title": "材料价格信息（2026年5月）",
          "pubDate": "2026-06-03 10:30:00",
          "urls": "{\\"pc\\":\\"/zgsrmzf/c00145/pc/content/content_2058746804822818816.html\\"}",
          "articleFiles": [{
            "fileName": "材料信息价2026年第5期（电子版）.zip",
            "domainName": "https://www.zg.gov.cn",
            "filePath": "/zgsrmzf/c00145/2058746804822818816/2FdILMqd.zip",
            "fileId": "2058746566540996608"
          }]
        }];
        </script>
        """

    def get_bytes(self, url):
        self.calls.append(("GET_BYTES", url))
        return b"PK\x03\x04zigong opaque zip bytes", "application/zip"


def create_source(db_session, *, name, region_code, config):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name=name,
        data_domain="cost_info",
        region_code=region_code,
        province="四川",
        city=name[:3],
        config=config,
    )


def test_sichuan_auto_history_scope_is_eight_file_sources_without_chengdu():
    assert SICHUAN_AUTO_HISTORY_REGION_CODES == (
        "510400",
        "510500",
        "510600",
        "510900",
        "511100",
        "511700",
        "510700",
        "510300",
    )
    assert "510100" not in SICHUAN_AUTO_HISTORY_REGION_CODES


def test_sichuan_history_fanout_creates_one_pending_task_per_luzhou_issue_with_polite_schedule(db_session):
    source = create_source(
        db_session,
        name="泸州市住房和城乡建设局",
        region_code="510500",
        config=luzhou_cost_info_source_config(),
    )
    base_time = datetime(2026, 6, 22, 10, 0, tzinfo=UTC)

    report = fanout_sichuan_history_tasks(
        db_session,
        source_id=source.source_id,
        client=FakeLuzhouHistoryClient(),
        mode="history_backfill",
        base_time=base_time,
        min_delay_seconds=3,
        jitter_seconds=0,
        as_of_date="2026-06-22",
    )

    assert report == {
        "source_id": source.source_id,
        "region_code": "510500",
        "listed_count": 2,
        "created_count": 2,
        "skipped_existing_archive_count": 0,
        "skipped_existing_task_count": 0,
        "adapter_kind": "sichuan_pdf",
        "cursor_source_item_key": "https://zjj.luzhou.gov.cn/hyfw/gczjxx/content_202605.html",
        "cursor_publish_date": "2026-06-12",
        "crawl_lag_days": 10,
    }
    tasks = db_session.query(CollectionTask).filter_by(task_type="crawl_issue").order_by(CollectionTask.scheduled_at).all()
    assert [task.status for task in tasks] == ["pending", "pending"]
    assert [task.priority for task in tasks] == [20, 20]
    assert tasks[0].scheduled_at.replace(tzinfo=UTC) == base_time
    assert tasks[1].scheduled_at.replace(tzinfo=UTC) == base_time + timedelta(seconds=3)
    assert tasks[0].period_start == "2026-05"
    assert tasks[0].config_override["queue_mode"] == "history_backfill"
    assert tasks[0].config_override["adapter_kind"] == "sichuan_pdf"
    assert tasks[0].config_override["source_item"]["detail_url"].endswith("content_202605.html")


def test_sichuan_history_fanout_truncates_task_period_raw_but_keeps_full_source_item_title(db_session):
    source = create_source(
        db_session,
        name="泸州市住房和城乡建设局",
        region_code="510500",
        config=luzhou_cost_info_source_config(),
    )

    report = fanout_sichuan_history_tasks(
        db_session,
        source_id=source.source_id,
        client=FakeLongTitleHistoryClient(),
        mode="history_backfill",
        base_time=datetime(2026, 6, 22, 10, 0, tzinfo=UTC),
        jitter_seconds=0,
    )

    task = db_session.query(CollectionTask).filter_by(task_type="crawl_issue").one()
    assert report["created_count"] == 1
    assert len(task.period_raw) <= 64
    assert task.period_raw.endswith("...")
    assert task.config_override["source_item"]["title"] == FakeLongTitleHistoryClient.long_title


def test_sichuan_history_fanout_skips_existing_archive_and_existing_task(db_session):
    source = create_source(
        db_session,
        name="泸州市住房和城乡建设局",
        region_code="510500",
        config=luzhou_cost_info_source_config(),
    )
    base_time = datetime(2026, 6, 22, 10, 0, tzinfo=UTC)
    fanout_sichuan_history_tasks(
        db_session,
        source_id=source.source_id,
        client=FakeLuzhouHistoryClient(),
        mode="history_backfill",
        base_time=base_time,
        jitter_seconds=0,
        max_tasks=1,
    )
    run_due_sichuan_history_tasks(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=FakeLuzhouHistoryClient(),
        now=base_time,
        limit=1,
    )

    report = fanout_sichuan_history_tasks(
        db_session,
        source_id=source.source_id,
        client=FakeLuzhouHistoryClient(),
        mode="history_backfill",
        base_time=base_time,
        jitter_seconds=0,
    )

    assert report["created_count"] == 1
    assert report["skipped_existing_archive_count"] == 1
    assert report["skipped_existing_task_count"] == 0
    assert db_session.query(CollectionTask).filter_by(task_type="crawl_issue").count() == 2

    repeat = fanout_sichuan_history_tasks(
        db_session,
        source_id=source.source_id,
        client=FakeLuzhouHistoryClient(),
        mode="history_backfill",
        base_time=base_time,
        jitter_seconds=0,
    )
    assert repeat["created_count"] == 0
    assert repeat["skipped_existing_task_count"] == 1


def test_sichuan_due_worker_ingests_luzhou_task_and_marks_done_without_processing(db_session):
    source = create_source(
        db_session,
        name="泸州市住房和城乡建设局",
        region_code="510500",
        config=luzhou_cost_info_source_config(),
    )
    due_at = datetime(2026, 6, 22, 10, 0, tzinfo=UTC)
    fanout_sichuan_history_tasks(
        db_session,
        source_id=source.source_id,
        client=FakeLuzhouHistoryClient(),
        mode="history_backfill",
        base_time=due_at,
        jitter_seconds=0,
        max_tasks=1,
    )

    report = run_due_sichuan_history_tasks(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=FakeLuzhouHistoryClient(),
        now=due_at,
        limit=1,
        retry_delay_seconds=60,
    )

    task = db_session.query(CollectionTask).filter_by(task_type="crawl_issue").one()
    archive = db_session.query(Archive).filter_by(domain_type="cost_info").one()
    assert report == {"leased_count": 1, "done_count": 1, "retry_count": 0, "dead_letter_count": 0}
    assert task.status == "done"
    assert task.attempt == 1
    assert task.downloaded_count == 1
    assert task.new_file_count == 1
    assert archive.business_key == f"cost_info:{source.source_id}:510500:2026-05:泸州市2026年5月工程造价信息"
    assert archive.metadata_payload["period"]["value"] == "2026-05"
    assert db_session.query(FileProcessing).count() == 0


def test_sichuan_history_worker_caps_due_lease_by_per_host_limit(db_session):
    source = create_source(
        db_session,
        name="泸州市住房和城乡建设局",
        region_code="510500",
        config=luzhou_cost_info_source_config(),
    )
    due_at = datetime(2026, 6, 22, 10, 0, tzinfo=UTC)
    fanout_sichuan_history_tasks(
        db_session,
        source_id=source.source_id,
        client=FakeLuzhouHistoryClient(),
        mode="history_backfill",
        base_time=due_at,
        min_delay_seconds=0,
        jitter_seconds=0,
    )

    report = run_due_sichuan_history_tasks(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=FakeLuzhouHistoryClient(),
        now=due_at,
        limit=5,
    )

    assert report["leased_count"] == 1
    assert db_session.query(CollectionTask).filter_by(status="pending").count() == 1
    assert db_session.query(CollectionTask).filter_by(status="done").count() == 1


def test_sichuan_history_fanout_applies_configurable_jitter_to_schedule(db_session):
    source = create_source(
        db_session,
        name="泸州市住房和城乡建设局",
        region_code="510500",
        config=luzhou_cost_info_source_config(),
    )
    due_at = datetime(2026, 6, 22, 10, 0, tzinfo=UTC)

    fanout_sichuan_history_tasks(
        db_session,
        source_id=source.source_id,
        client=FakeLuzhouHistoryClient(),
        mode="history_backfill",
        base_time=due_at,
        min_delay_seconds=3,
        jitter_seconds=2,
        jitter_func=lambda window: window,
    )

    tasks = db_session.query(CollectionTask).filter_by(task_type="crawl_issue").order_by(CollectionTask.scheduled_at).all()
    assert tasks[0].scheduled_at.replace(tzinfo=UTC) == due_at
    assert tasks[1].scheduled_at.replace(tzinfo=UTC) == due_at + timedelta(seconds=5)


def test_sichuan_history_backfill_densifies_coverage_matrix_for_luzhou_periods(db_session):
    source = create_source(
        db_session,
        name="泸州市住房和城乡建设局",
        region_code="510500",
        config=luzhou_cost_info_source_config(),
    )
    due_at = datetime(2026, 6, 22, 10, 0, tzinfo=UTC)
    fanout_sichuan_history_tasks(
        db_session,
        source_id=source.source_id,
        client=FakeLuzhouHistoryClient(),
        mode="history_backfill",
        base_time=due_at,
        min_delay_seconds=3,
        jitter_seconds=0,
    )
    run_due_sichuan_history_tasks(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=FakeLuzhouHistoryClient(),
        now=due_at,
        limit=1,
    )
    run_due_sichuan_history_tasks(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=FakeLuzhouHistoryClient(),
        now=due_at + timedelta(seconds=3),
        limit=1,
    )

    rows = build_coverage_matrix(db_session, start_period="2026-04", end_period="2026-05", province_code="510000")
    by_period = {row.period: row for row in rows if row.coverage_region_code == "510500"}

    assert set(by_period) == {"2026-04", "2026-05"}
    assert by_period["2026-04"].business_coverage_status == "covered"
    assert by_period["2026-04"].source_completeness_status == "city_source_present"
    assert by_period["2026-05"].business_coverage_status == "covered"
    assert by_period["2026-05"].source_completeness_status == "city_source_present"


def test_sichuan_due_worker_retries_then_dead_letters_to_017_manual_recovery(db_session):
    source = create_source(
        db_session,
        name="泸州市住房和城乡建设局",
        region_code="510500",
        config=luzhou_cost_info_source_config(),
    )
    now = datetime(2026, 6, 22, 10, 0, tzinfo=UTC)
    fanout_sichuan_history_tasks(
        db_session,
        source_id=source.source_id,
        client=FakeLuzhouHistoryClient(),
        mode="history_backfill",
        base_time=now,
        jitter_seconds=0,
        max_tasks=1,
    )
    task = db_session.query(CollectionTask).filter_by(task_type="crawl_issue").one()
    task.max_attempts = 2
    db_session.commit()

    first = run_due_sichuan_history_tasks(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=FakeLuzhouHistoryClient(fail_download=True),
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

    second = run_due_sichuan_history_tasks(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=FakeLuzhouHistoryClient(fail_download=True),
        now=now + timedelta(seconds=60),
        limit=1,
        retry_delay_seconds=60,
    )
    db_session.refresh(task)

    assert second == {"leased_count": 1, "done_count": 0, "retry_count": 0, "dead_letter_count": 1}
    assert task.status == "failed"
    assert task.attempt == 2
    assert task.error_code == "SICHUAN_HISTORY_CRAWL_TASK_FAILED"
    assert task.config_override["manual_recovery"]["spec"] == "017"
    assert task.config_override["manual_recovery"]["required"] is True
    assert db_session.query(Archive).count() == 0


def test_sichuan_deyang_fanout_uses_playwright_list_client_after_http_503(db_session):
    from app.sichuan_pdf_cost_info import deyang_cost_info_source_config

    source = create_source(
        db_session,
        name="德阳市人民政府",
        region_code="510600",
        config=deyang_cost_info_source_config(),
    )

    report = fanout_sichuan_history_tasks(
        db_session,
        source_id=source.source_id,
        client=FailingDeyangHttpClient(),
        playwright_list_client=FakeDeyangPlaywrightListClient(),
        mode="history_backfill",
        base_time=datetime(2026, 6, 22, 10, 0, tzinfo=UTC),
        jitter_seconds=0,
    )

    task = db_session.query(CollectionTask).filter_by(task_type="crawl_issue").one()
    assert report["created_count"] == 1
    assert task.config_override["list_client_fallback"] == "playwright"
    assert task.config_override["source_item"]["detail_url"].endswith("1956445.htm")


def test_sichuan_history_worker_routes_zigong_zip_as_opaque_without_unzip(db_session):
    source = create_source(
        db_session,
        name="自贡市人民政府-材料价格",
        region_code="510300",
        config=zigong_cost_info_source_config(),
    )
    due_at = datetime(2026, 6, 22, 10, 0, tzinfo=UTC)
    fanout_sichuan_history_tasks(
        db_session,
        source_id=source.source_id,
        client=FakeZigongHistoryClient(),
        mode="history_backfill",
        base_time=due_at,
        jitter_seconds=0,
    )

    report = run_due_sichuan_history_tasks(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=FakeZigongHistoryClient(),
        now=due_at,
        limit=1,
    )

    archive = db_session.query(Archive).filter_by(domain_type="cost_info").one()
    assert report["done_count"] == 1
    assert archive.metadata_payload["source_attachment_mode"]["value"] == "zip_package"
    assert archive.metadata_payload["opaque_package"]["value"] is True
    assert db_session.query(FileProcessing).count() == 0
