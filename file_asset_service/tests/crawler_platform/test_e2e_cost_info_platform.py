from datetime import UTC, datetime

from app.archive_rules import build_cost_info_business_key
from app.cost_info_registry import find_data_source_by_site_id, import_source_config, set_active_source
from app.cost_info_scheduler import run_scheduler
from app.cost_info_worker import run_worker
from app.models import Archive, ArchiveFile, CollectionTask, FileAsset, FileProcessing, IngestEvent
from app.sichuan_pdf_cost_info import DEYANG_PARSER_VERSION, deyang_cost_info_source_config
from app.storage import FakeObjectStore


LIST_URL = "https://www.deyang.gov.cn/info/iList.jsp?node_id=GKdyszf&cat_id=25723"
DETAIL_URL = "https://www.deyang.gov.cn/gk/fjbm/fzjj/fadingnarong/gqgg/1956445.htm"
PDF_URL = "https://www.deyang.gov.cn/wcm.files/upload/GKdyszf/eupload/ueditor/file/20260602/1780384437452077195.pdf"
TITLE = "关于发布德阳市2026年04月建筑材料市场价格信息的通知"
SITE_ID = "cost_info.sc.deyang"


class FakeSourceRegistryHttpClient:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls = []
        self.__class__.instances.append(self)

    def get_text(self, url: str) -> str:
        self.calls.append(("get_text", url))
        if url == LIST_URL:
            return f"""
            <li>
              <a href="/gk/fjbm/fzjj/fadingnarong/gqgg/1956445.htm">{TITLE}</a>
              <span>2026-06-02</span>
            </li>
            """
        if url == DETAIL_URL:
            return """
            <p>附件：
              <a href="/wcm.files/upload/GKdyszf/eupload/ueditor/file/20260602/1780384437452077195.pdf">
                德阳市2026年04月建筑材料市场价格信息.pdf
              </a>
            </p>
            """
        raise AssertionError(f"unexpected text fetch: {url}")

    def post_form(self, url: str, data: dict[str, object]) -> str:
        raise AssertionError(f"unexpected form fetch: {url} {data}")

    def post_form_json(self, url: str, data: dict[str, object]) -> dict:
        raise AssertionError(f"unexpected form json fetch: {url} {data}")

    def post_json(self, url: str, payload: dict[str, object]) -> dict:
        raise AssertionError(f"unexpected json fetch: {url} {payload}")

    def get_bytes(self, url: str) -> tuple[bytes, str | None]:
        self.calls.append(("get_bytes", url))
        if url == PDF_URL:
            return b"%PDF deyang e2e fixture bytes", "application/pdf"
        raise AssertionError(f"unexpected download: {url}")


def _deyang_e2e_config() -> dict:
    config = deyang_cost_info_source_config()
    config["stable"] = {
        **config["stable"],
        "province": "四川省",
        "city": "德阳市",
    }
    parser = config["parser"]["parsers"][DEYANG_PARSER_VERSION]
    parser["pagination"] = {"max_pages": 1}
    config["schedule_policy"] = {
        "enabled": True,
        "frequency": "daily",
        "timezone": "Asia/Shanghai",
        "max_attempts": 3,
        "max_items_per_run": 5,
        "early_stop_duplicate": True,
        "rate_limit": {
            "host": "www.deyang.gov.cn",
            "min_delay_seconds": 0,
            "jitter_seconds": 0,
            "request_timeout_seconds": 30,
            "per_read_timeout_seconds": 5,
            "attachment_total_timeout_seconds": 120,
        },
    }
    return config


def test_registry_scheduler_worker_e2e_real_sichuan_pdf_adapter_without_network(db_session, monkeypatch):
    from app.crawler_platform import cost_info_http_client

    FakeSourceRegistryHttpClient.instances = []
    monkeypatch.setattr(cost_info_http_client, "SourceRegistryHttpClient", FakeSourceRegistryHttpClient)

    config = _deyang_e2e_config()
    imported = import_source_config(config, db_session)
    source = find_data_source_by_site_id(db_session, SITE_ID)
    assert imported.action == "created"
    assert source is not None
    assert source.status == "pending_verify"

    set_active_source(db_session, site_id=SITE_ID)
    db_session.refresh(source)
    assert source.status == "active"

    first_scheduler_report = run_scheduler(
        db_session,
        now=datetime(2026, 6, 26, 2, 0, tzinfo=UTC),
        trigger="manual",
        site_id=SITE_ID,
    )
    task = db_session.query(CollectionTask).filter_by(source_id=source.source_id).one()

    assert first_scheduler_report["task_created"] == 1
    assert task.status == "pending"
    assert task.config_override["site_id"] == SITE_ID
    assert task.config_override["adapter_kind"] == "sichuan_pdf"
    assert task.config_override["active_parser_version"] == DEYANG_PARSER_VERSION

    storage = FakeObjectStore()
    first_worker_report = run_worker(
        db_session,
        limit=1,
        site_id=SITE_ID,
        now=datetime(2026, 6, 26, 2, 1, tzinfo=UTC),
        storage=storage,
    )

    db_session.refresh(task)
    archive = db_session.query(Archive).filter_by(domain_type="cost_info").one()
    file_asset = db_session.query(FileAsset).one()
    archive_file = db_session.query(ArchiveFile).one()
    ingest_event = db_session.query(IngestEvent).one()

    assert first_worker_report.leased_count == 1
    assert first_worker_report.done_count == 1
    assert first_worker_report.archive_created_count == 1
    assert first_worker_report.duplicate_count == 0
    assert first_worker_report.file_processing_passes is True
    assert first_worker_report.file_processing_rows_created == 0
    assert first_worker_report.health_status == "healthy"
    assert first_worker_report.to_dict()["red_lines"]["file_processing_passes"] is True
    assert task.status == "done"
    assert task.discovered_count == 1
    assert task.downloaded_count == 1
    assert task.new_file_count == 1
    assert task.processing_created_count == 0
    assert archive.business_key == build_cost_info_business_key(
        source_id=source.source_id,
        region_code="510600",
        period="2026-04",
        title=TITLE,
    )
    assert archive.title == TITLE
    assert archive.task_id == task.task_id
    assert archive_file.archive_id == archive.archive_id
    assert archive_file.file_id == file_asset.file_id
    assert ingest_event.task_id == task.task_id
    assert ingest_event.source_url == PDF_URL
    assert storage.put_count == 1
    assert db_session.query(FileProcessing).count() == 0
    assert FakeSourceRegistryHttpClient.instances[0].kwargs["referer"] == LIST_URL
    assert FakeSourceRegistryHttpClient.instances[0].kwargs["read_total_timeout_seconds"] == 120
    assert FakeSourceRegistryHttpClient.instances[0].calls == [
        ("get_text", LIST_URL),
        ("get_text", DETAIL_URL),
        ("get_bytes", PDF_URL),
    ]

    second_scheduler_report = run_scheduler(
        db_session,
        now=datetime(2026, 6, 27, 2, 0, tzinfo=UTC),
        trigger="scheduled",
        site_id=SITE_ID,
    )
    second_worker_report = run_worker(
        db_session,
        limit=1,
        site_id=SITE_ID,
        now=datetime(2026, 6, 27, 2, 1, tzinfo=UTC),
        storage=storage,
    )

    assert second_scheduler_report["task_created"] == 1
    assert second_worker_report.leased_count == 1
    assert second_worker_report.done_count == 1
    assert second_worker_report.archive_created_count == 0
    assert second_worker_report.duplicate_count == 1
    assert second_worker_report.health_status == "healthy"
    assert db_session.query(CollectionTask).filter_by(status="done").count() == 2
    assert db_session.query(Archive).filter_by(domain_type="cost_info").count() == 1
    assert db_session.query(FileAsset).count() == 1
    assert db_session.query(IngestEvent).count() == 1
    assert db_session.query(FileProcessing).count() == 0
    assert storage.put_count == 1
    assert FakeSourceRegistryHttpClient.instances[1].calls == [("get_text", LIST_URL)]
