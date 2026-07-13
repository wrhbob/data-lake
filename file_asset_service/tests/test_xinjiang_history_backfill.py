from datetime import UTC, datetime, timedelta

from app.collection import create_data_source
from app.models import Archive, CollectionTask, FileProcessing
from app.storage import FakeObjectStore
from app.xinjiang_cost_info import xinjiang_area_cost_info_source_config
from app.xinjiang_history_backfill import (
    fanout_xinjiang_history_tasks,
    list_xinjiang_history_issues,
    run_due_xinjiang_history_tasks,
)


class FakeXinjiangHistoryClient:
    def __init__(self, *, rows_by_areaid: dict[str, list[dict]] | None = None, fail_download: bool = False):
        self.rows_by_areaid = rows_by_areaid or {
            "2": [
                {"ID": "u-202604", "Name": "乌鲁木齐市2026年4月份建设工程综合价格信息", "ReleaseDate": "/Date(1780358400000)/"},
                {"ID": "u-202512", "Name": "乌鲁木齐市2025年12月份建设工程综合价格信息", "ReleaseDate": "/Date(1765900800000)/"},
                {"ID": "u-202312", "Name": "乌鲁木齐市2023年12月份建设工程综合价格信息", "ReleaseDate": "/Date(1702828800000)/"},
            ]
        }
        self.fail_download = fail_download
        self.posted = []
        self.downloaded = []

    def post_form_json(self, url, payload):
        self.posted.append((url, payload))
        rows = list(self.rows_by_areaid.get(str(payload["areaid"]), []))
        page = int(payload["page"])
        page_size = int(payload["pagesize"])
        start = (page - 1) * page_size
        return {"Total": len(rows), "Rows": rows[start : start + page_size]}

    def get_text(self, url):
        return """
        <a href="javascript:LookFile('/Upload/File/doc-guid/新疆历史.doc')">doc</a>
        <a href="javascript:LookFile('/Upload/File/xls-guid/新疆历史.xlsx')">xlsx</a>
        """

    def get_bytes(self, url):
        self.downloaded.append(url)
        if self.fail_download:
            raise TimeoutError("xjzj timeout")
        if url.endswith(".doc"):
            return b"DOC bytes", "application/msword"
        if url.endswith(".xlsx"):
            return b"XLSX bytes", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        raise AssertionError(f"unexpected download: {url}")


def create_xinjiang_source(db_session, key: str):
    config = xinjiang_area_cost_info_source_config(key)
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name=f"新疆工程造价信息网-{key}",
        data_domain="cost_info",
        region_code=config["stable"]["region_code"],
        province="新疆",
        city=config["coverage_expectation"]["target_regions"][0]["region_name"],
        config=config,
    )


def parser_for(source):
    parser_version = source.config["parser"]["active_parser_version"]
    return source.config["parser"]["parsers"][parser_version]


def test_xinjiang_history_list_paginates_and_filters_period_window(db_session):
    source = create_xinjiang_source(db_session, "urumqi")
    client = FakeXinjiangHistoryClient()

    rows = list_xinjiang_history_issues(
        client,
        parser=parser_for(source),
        page_size=2,
        start_period="2024-01",
        end_period="2026-12",
    )

    assert [row["ID"] for row in rows] == ["u-202604", "u-202512"]
    assert [payload["page"] for _, payload in client.posted] == ["1", "2"]


def test_xinjiang_history_fanout_creates_pending_tasks_with_polite_schedule(db_session):
    source = create_xinjiang_source(db_session, "urumqi")
    base_time = datetime(2026, 6, 23, 10, 0, tzinfo=UTC)

    report = fanout_xinjiang_history_tasks(
        db_session,
        source_id=source.source_id,
        client=FakeXinjiangHistoryClient(),
        base_time=base_time,
        min_delay_seconds=8,
        jitter_seconds=0,
        start_period="2024-01",
        end_period="2026-12",
        page_size=2,
        as_of_date="2026-06-23",
    )

    assert report["listed_count"] == 2
    assert report["created_count"] == 2
    assert report["adapter_kind"] == "aspnet_ajax_area"
    tasks = db_session.query(CollectionTask).filter_by(task_type="crawl_issue").order_by(CollectionTask.scheduled_at).all()
    assert [task.status for task in tasks] == ["pending", "pending"]
    assert tasks[0].scheduled_at.replace(tzinfo=UTC) == base_time
    assert tasks[1].scheduled_at.replace(tzinfo=UTC) == base_time + timedelta(seconds=8)
    assert tasks[0].period_start == "2026-04"
    assert tasks[0].config_override["queue_mode"] == "history_backfill"
    assert tasks[0].config_override["adapter_kind"] == "aspnet_ajax_area"
    assert tasks[0].config_override["source_item"]["ID"] == "u-202604"


def test_xinjiang_history_worker_leases_one_due_task_across_same_host_sources(db_session):
    urumqi = create_xinjiang_source(db_session, "urumqi")
    karamay = create_xinjiang_source(db_session, "karamay")
    client = FakeXinjiangHistoryClient(
        rows_by_areaid={
            "2": [{"ID": "u-202604", "Name": "乌鲁木齐市2026年4月份建设工程综合价格信息", "ReleaseDate": "/Date(1780358400000)/"}],
            "4": [{"ID": "k-202604", "Name": "克拉玛依市2026年4月份建设工程综合价格信息", "ReleaseDate": "/Date(1780358400000)/"}],
        }
    )
    now = datetime(2026, 6, 23, 10, 0, tzinfo=UTC)
    fanout_xinjiang_history_tasks(db_session, source_id=urumqi.source_id, client=client, base_time=now, jitter_seconds=0)
    fanout_xinjiang_history_tasks(db_session, source_id=karamay.source_id, client=client, base_time=now, jitter_seconds=0)

    report = run_due_xinjiang_history_tasks(
        db_session,
        FakeObjectStore(),
        source_ids=[urumqi.source_id, karamay.source_id],
        client=client,
        now=now,
        limit=5,
    )

    assert report == {"leased_count": 1, "done_count": 1, "retry_count": 0, "dead_letter_count": 0}
    assert db_session.query(CollectionTask).filter_by(status="done").count() == 1
    assert db_session.query(CollectionTask).filter_by(status="pending").count() == 1
    assert db_session.query(Archive).filter_by(domain_type="cost_info").count() == 1
    assert db_session.query(FileProcessing).count() == 0


def test_xinjiang_history_worker_dead_letters_to_017_manual_recovery(db_session):
    source = create_xinjiang_source(db_session, "urumqi")
    now = datetime(2026, 6, 23, 10, 0, tzinfo=UTC)
    fanout_xinjiang_history_tasks(
        db_session,
        source_id=source.source_id,
        client=FakeXinjiangHistoryClient(),
        base_time=now,
        jitter_seconds=0,
        max_tasks=1,
    )
    task = db_session.query(CollectionTask).filter_by(task_type="crawl_issue").one()
    task.max_attempts = 1
    db_session.commit()

    report = run_due_xinjiang_history_tasks(
        db_session,
        FakeObjectStore(),
        source_ids=[source.source_id],
        client=FakeXinjiangHistoryClient(fail_download=True),
        now=now,
        retry_delay_seconds=60,
    )
    db_session.refresh(task)

    assert report == {"leased_count": 1, "done_count": 0, "retry_count": 0, "dead_letter_count": 1}
    assert task.status == "failed"
    assert task.error_code == "XINJIANG_HISTORY_CRAWL_TASK_FAILED"
    assert task.config_override["manual_recovery"]["spec"] == "017"
    assert task.config_override["manual_recovery"]["required"] is True
    assert db_session.query(Archive).count() == 0
