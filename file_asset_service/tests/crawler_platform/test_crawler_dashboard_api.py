from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.collection import create_collection_task, create_data_source
from app.database import get_db_session
from app.main import create_app
from app.models import Base, CollectionTask
from app.parse_manifest import PARSE_MANIFEST_FIELDS
from app.storage import FakeObjectStore, get_object_store


def build_client():
    engine = create_engine(
        "sqlite+pysqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    storage = FakeObjectStore()
    app = create_app(init_schema=False)

    def override_db_session():
        with Session() as session:
            yield session

    def override_object_store():
        return storage

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_object_store] = override_object_store
    return TestClient(app), Session, storage


def make_source(session, *, site_id="cost_info.sc.deyang", adapter_kind="mock", status="active", enabled=True):
    return create_data_source(
        session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="德阳市人民政府-公示公告",
        data_domain="cost_info",
        base_url="https://www.deyang.gov.cn/info/iList.jsp?node_id=GKdyszf&cat_id=25723",
        url="https://www.deyang.gov.cn/info/iList.jsp?node_id=GKdyszf&cat_id=25723",
        province="四川省",
        city="德阳市",
        region_code="510600",
        config={
            "registry_schema_version": "source_registry.v1",
            "stable": {
                "site_id": site_id,
                "domain_type": "cost_info",
                "region_code": "510600",
                "publisher_name": "德阳市人民政府",
                "entry_url": "https://www.deyang.gov.cn/info/iList.jsp?node_id=GKdyszf&cat_id=25723",
            },
            "parser": {
                "active_parser_version": "deyang.cost-info-pdf-list.v1",
                "parsers": {
                    "deyang.cost-info-pdf-list.v1": {
                        "adapter_kind": adapter_kind,
                        "list_url": "https://www.deyang.gov.cn/info/iList.jsp?node_id=GKdyszf&cat_id=25723",
                    }
                },
            },
        },
        schedule_policy={
            "enabled": enabled,
            "frequency": "daily",
            "timezone": "Asia/Shanghai",
            "max_attempts": 3,
            "early_stop_duplicate": True,
            "rate_limit": {"host": "www.deyang.gov.cn", "min_delay_seconds": 0, "jitter_seconds": 0},
        },
        status=status,
    )


def make_beijing_source(session, *, status="active"):
    return create_data_source(
        session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="北京市住房和城乡建设委员会",
        data_domain="cost_info",
        base_url="https://zjw.beijing.gov.cn/bjjs/gczj14/zjxx/index.shtml",
        url="https://zjw.beijing.gov.cn/bjjs/gczj14/zjxx/index.shtml",
        province="北京市",
        city=None,
        region_code="110000",
        config={
            "registry_schema_version": "source_registry.v1",
            "stable": {
                "site_id": "cost_info.bj.zjw.main",
                "domain_type": "cost_info",
                "region_code": "110000",
                "coverage_region_code": "110000",
                "publisher_scope": "province",
                "publisher_name": "北京市住房和城乡建设委员会",
                "entry_url": "https://zjw.beijing.gov.cn/bjjs/gczj14/zjxx/index.shtml",
            },
            "parser": {
                "active_parser_version": "beijing.zjw-main-pdf-list.v1",
                "parsers": {
                    "beijing.zjw-main-pdf-list.v1": {
                        "adapter_kind": "beijing_pdf",
                        "list_url": "https://zjw.beijing.gov.cn/bjjs/gczj14/zjxx/index.shtml",
                    }
                },
            },
            "coverage_expectation": {
                "target_regions": [
                    {
                        "region_code": "110000",
                        "region_name": "北京市",
                        "target_level": "province",
                        "requires_city_source": False,
                        "source_completeness_status": "province_source_only",
                        "source_audit_status": "official_main_source",
                    }
                ]
            },
        },
        schedule_policy={
            "enabled": True,
            "frequency": "monthly",
            "timezone": "Asia/Shanghai",
            "max_attempts": 3,
            "early_stop_duplicate": True,
        },
        status=status,
    )


def make_task(session, source, *, status="pending", task_type="crawl_incremental"):
    task = create_collection_task(
        session,
        source_id=source.source_id,
        operator_type="system",
        task_type=task_type,
        trigger_type="scheduled",
        data_domain="cost_info",
        status=status,
    )
    task.scheduled_at = datetime(2026, 6, 26, 2, 0, tzinfo=UTC)
    session.commit()
    return task


def write_parse_manifest(path, rows):
    import csv

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=PARSE_MANIFEST_FIELDS)
        writer.writeheader()
        for row in rows:
            payload = {field: "" for field in PARSE_MANIFEST_FIELDS}
            payload.update(row)
            writer.writerow(payload)


def test_crawler_dashboard_page_is_served():
    client, _Session, _storage = build_client()

    response = client.get("/crawler")

    assert response.status_code == 200
    assert "crawler-console" in response.text
    assert "crawler.js" in response.text
    assert "parseManifestStatus" in response.text
    assert "parseManifestRows" in response.text
    assert "coverageRegionCode" in response.text
    assert "coverageMatrixRows" in response.text
    assert "coverage-range-dry-run" in response.text


def test_crawler_parse_manifest_api_reports_unconfigured(monkeypatch):
    monkeypatch.delenv("FILE_ASSET_PARSE_MANIFEST_PATH", raising=False)
    client, _Session, _storage = build_client()

    response = client.get("/api/crawler/parse-manifest")

    assert response.status_code == 200
    assert response.json() == {
        "configured": False,
        "exists": False,
        "path": None,
        "file_name": None,
        "updated_at": None,
        "byte_size": None,
        "schema_version": None,
        "row_count": 0,
        "ready_count": 0,
        "not_ready_count": 0,
        "ready_rate": None,
        "missing_field_counts": {},
    }


def test_crawler_parse_manifest_api_summarizes_csv_and_lists_blockers(tmp_path, monkeypatch):
    manifest_path = tmp_path / "parse_manifest.csv"
    write_parse_manifest(
        manifest_path,
        [
            {
                "manifest_schema_version": "parse_manifest.v1",
                "object_key": "ready-a.pdf",
                "sha256": "sha-ready-a",
                "file_names": "成都市2026年5月信息价.pdf",
                "resolved_regions": "成都市",
                "period_starts": "2026-05",
                "parse_ready": "true",
            },
            {
                "manifest_schema_version": "parse_manifest.v1",
                "object_key": "blocked.zip",
                "sha256": "sha-blocked",
                "file_names": "hash-only.zip",
                "source_urls": "https://example.test/blocked",
                "parse_ready": "false",
                "missing_fields": "archive_ids|period",
            },
            {
                "manifest_schema_version": "parse_manifest.v1",
                "object_key": "ready-b.xlsx",
                "sha256": "sha-ready-b",
                "original_names": "绵阳市2026年6月材料价格.xlsx",
                "resolved_regions": "绵阳市",
                "period_raws": "2026年6月",
                "parse_ready": "true",
            },
        ],
    )
    monkeypatch.setenv("FILE_ASSET_PARSE_MANIFEST_PATH", str(manifest_path))
    client, _Session, _storage = build_client()

    status = client.get("/api/crawler/parse-manifest")

    assert status.status_code == 200
    payload = status.json()
    assert payload["configured"] is True
    assert payload["exists"] is True
    assert payload["path"] == str(manifest_path)
    assert payload["file_name"] == "parse_manifest.csv"
    assert payload["schema_version"] == "parse_manifest.v1"
    assert payload["row_count"] == 3
    assert payload["ready_count"] == 2
    assert payload["not_ready_count"] == 1
    assert payload["ready_rate"] == 2 / 3
    assert payload["missing_field_counts"] == {"archive_ids": 1, "period": 1}
    assert payload["updated_at"] is not None
    assert payload["byte_size"] > 0

    blockers = client.get("/api/crawler/parse-manifest/issues?limit=5")

    assert blockers.status_code == 200
    assert blockers.json() == {
        "configured": True,
        "exists": True,
        "path": str(manifest_path),
        "limit": 5,
        "issue_count": 1,
        "issues": [
            {
                "object_key": "blocked.zip",
                "sha256": "sha-blocked",
                "file_names": "hash-only.zip",
                "original_names": "",
                "resolved_regions": "",
                "region_codes": "",
                "period_starts": "",
                "period_raws": "",
                "source_urls": "https://example.test/blocked",
                "missing_fields": "archive_ids|period",
            }
        ],
    }


def test_crawler_sources_api_reports_registry_schedule_and_task_counts(monkeypatch):
    from app import crawler_dashboard

    monkeypatch.setattr(crawler_dashboard, "utcnow", lambda: datetime(2026, 6, 26, 4, 0, tzinfo=UTC))
    client, Session, _storage = build_client()
    with Session() as session:
        source = make_source(session)
        make_task(session, source, status="pending")
        make_task(session, source, status="running")
        make_task(session, source, status="done")
        make_task(session, source, status="failed")

    response = client.get("/api/crawler/sources")

    assert response.status_code == 200
    rows = response.json()
    assert rows == [
        {
            "source_id": rows[0]["source_id"],
            "site_id": "cost_info.sc.deyang",
            "name": "德阳市人民政府-公示公告",
            "status": "active",
            "province": "四川省",
            "city": "德阳市",
            "region_code": "510600",
            "adapter_kind": "mock",
            "schedule_enabled": True,
            "frequency": "daily",
            "is_due": False,
            "pending_count": 1,
            "worker_pending_count": 1,
            "legacy_pending_count": 0,
            "running_count": 1,
            "done_count": 1,
            "failed_count": 1,
            "last_task_status": "failed",
            "last_task_type": "crawl_incremental",
            "last_task_finished_at": None,
            "last_error_code": None,
            "can_run_worker": True,
        }
    ]


def test_crawler_scheduler_run_endpoint_dry_runs_then_creates_task():
    client, Session, _storage = build_client()
    with Session() as session:
        make_source(session)

    dry_run = client.post("/api/crawler/scheduler/run", json={"dry_run": True, "site_id": "cost_info.sc.deyang"})
    assert dry_run.status_code == 200
    assert dry_run.json()["task_created"] == 0
    with Session() as session:
        assert session.query(CollectionTask).count() == 0

    created = client.post(
        "/api/crawler/scheduler/run",
        json={"dry_run": False, "force": True, "site_id": "cost_info.sc.deyang"},
    )

    assert created.status_code == 200
    assert created.json()["task_created"] == 1
    with Session() as session:
        task = session.query(CollectionTask).one()
        assert task.status == "pending"
        assert task.task_type == "crawl_incremental"
        assert task.config_override["site_id"] == "cost_info.sc.deyang"
        assert task.config_override["adapter_kind"] == "mock"


def test_crawler_worker_run_endpoint_returns_worker_report_with_fake_storage():
    client, Session, storage = build_client()
    with Session() as session:
        source = make_source(session)
        task = make_task(session, source, status="pending")
        source_id = source.source_id
        task_id = task.task_id

    response = client.post("/api/crawler/worker/run", json={"dry_run": True, "source_id": source_id, "limit": 1})

    assert response.status_code == 200
    report = response.json()
    assert report["run_type"] == "worker"
    assert report["summary"]["leased_count"] == 1
    assert report["summary"]["done_count"] == 1
    assert report["summary"]["archive_created_count"] == 0
    assert report["health_status"] == "healthy"
    assert report["per_source"][0]["status"] == "dry_run"
    assert storage.put_count == 0
    with Session() as session:
        task = session.get(CollectionTask, task_id)
        assert task.status == "pending"


def test_crawler_worker_run_endpoint_accepts_worker_id_and_lease_seconds():
    client, Session, _storage = build_client()
    with Session() as session:
        source = make_source(session)
        task = make_task(session, source, status="pending")
        source_id = source.source_id
        task_id = task.task_id

    response = client.post(
        "/api/crawler/worker/run",
        json={
            "dry_run": False,
            "source_id": source_id,
            "worker_id": "crawler-node-api",
            "lease_seconds": 600,
            "limit": 1,
        },
    )

    assert response.status_code == 200
    report = response.json()
    assert report["worker_id"] == "crawler-node-api"
    assert report["summary"]["leased_count"] == 1
    assert report["summary"]["done_count"] == 1
    with Session() as session:
        task = session.get(CollectionTask, task_id)
        assert task.status == "done"
        assert task.worker_id == "crawler-node-api"
        assert task.lease_expires_at is None
        assert task.heartbeat_at is not None


def test_crawler_worker_run_endpoint_filters_by_batch_id():
    client, Session, _storage = build_client()
    with Session() as session:
        source = make_source(session)
        first = make_task(session, source, status="pending")
        first.batch_id = "batch-old"
        second = make_task(session, source, status="pending")
        second.batch_id = "batch-target"
        source_id = source.source_id
        first_id = first.task_id
        second_id = second.task_id
        session.commit()

    response = client.post(
        "/api/crawler/worker/run",
        json={
            "dry_run": True,
            "source_id": source_id,
            "batch_id": "batch-target",
            "limit": 10,
        },
    )

    assert response.status_code == 200
    report = response.json()
    assert report["summary"]["leased_count"] == 1
    with Session() as session:
        first = session.get(CollectionTask, first_id)
        second = session.get(CollectionTask, second_id)
        assert first.status == "pending"
        assert second.status == "pending"


def test_crawler_sources_api_separates_worker_pending_from_legacy_pending_tasks():
    client, Session, _storage = build_client()
    with Session() as session:
        source = make_source(session)
        make_task(session, source, status="pending", task_type="crawl_incremental")
        make_task(session, source, status="pending", task_type="crawl")

    response = client.get("/api/crawler/sources")

    assert response.status_code == 200
    row = response.json()[0]
    assert row["pending_count"] == 1
    assert row["worker_pending_count"] == 1
    assert row["legacy_pending_count"] == 1
    assert row["can_run_worker"] is True


def test_crawler_coverage_backfill_creates_period_scoped_beijing_tasks():
    client, Session, _storage = build_client()
    with Session() as session:
        source = make_beijing_source(session)
        source_id = source.source_id

    preview = client.post(
        "/api/crawler/coverage-backfill",
        json={
            "region_code": "110000",
            "start_period": "2026-07",
            "end_period": "2026-08",
            "dry_run": True,
        },
    )

    assert preview.status_code == 200
    assert preview.json()["dry_run"] is True
    assert preview.json()["task_created"] == 0
    assert [task["period"] for task in preview.json()["tasks"]] == ["2026-07", "2026-08"]
    with Session() as session:
        assert session.query(CollectionTask).count() == 0

    response = client.post(
        "/api/crawler/coverage-backfill",
        json={
            "region_code": "110000",
            "start_period": "2026-07",
            "end_period": "2026-08",
            "dry_run": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["region_code"] == "110000"
    assert body["requested_periods"] == ["2026-07", "2026-08"]
    assert body["task_created"] == 2
    assert body["task_skipped_existing"] == 0
    assert [task["period"] for task in body["tasks"]] == ["2026-07", "2026-08"]
    assert {task["source_id"] for task in body["tasks"]} == {source_id}
    assert {task["adapter_kind"] for task in body["tasks"]} == {"beijing_pdf"}
    assert {bool(task.get("batch_id")) for task in body["tasks"]} == {True}

    with Session() as session:
        tasks = session.query(CollectionTask).order_by(CollectionTask.period_start).all()
        assert [task.period_start for task in tasks] == ["2026-07", "2026-08"]
        assert [task.period_end for task in tasks] == ["2026-07", "2026-08"]
        assert {task.task_type for task in tasks} == {"crawl_issue"}
        assert {task.trigger_type for task in tasks} == {"coverage_backfill"}
        assert {task.status for task in tasks} == {"pending"}
        assert tasks[0].config_override["coverage_backfill"] == {
            "region_code": "110000",
            "period": "2026-07",
            "requested_periods": ["2026-07"],
            "source_id": source_id,
            "site_id": "cost_info.bj.zjw.main",
        }

    duplicate = client.post(
        "/api/crawler/coverage-backfill",
        json={
            "region_code": "110000",
            "start_period": "2026-07",
            "end_period": "2026-08",
            "dry_run": False,
        },
    )

    assert duplicate.status_code == 200
    assert duplicate.json()["task_created"] == 0
    assert duplicate.json()["task_skipped_existing"] == 2
