from io import BytesIO
from zipfile import ZipFile

from openpyxl import Workbook
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db_session
from app.main import create_app
from app.models import Base
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
    return TestClient(app), storage


def upload(client, name, content, batch_id="batch-001"):
    return client.post(
        "/api/file-assets/ingest",
        data={
            "tenant_code": "tenant_a",
            "source_type": "info_price",
            "batch_id": batch_id,
        },
        files={"file": (name, content, "application/octet-stream")},
    )


def field_sources(*fields, level="crawler", by="api-test"):
    return {
        field: {"source_level": level, "tagged_by": by, "tagged_at": "2026-06-30T10:00:00+08:00"}
        for field in fields
    }


def cell(value, level="crawler", by="api-test"):
    return {"value": value, "source_level": level, "tagged_by": by, "tagged_at": "2026-06-30T10:00:00+08:00"}


def create_data_source(client, **overrides):
    payload = {
        "source_scope": "platform_public",
        "managed_by": "platform",
        "source_type": "info_price",
        "connector_type": "http_site",
        "name": "测试信息价源",
        "data_domain": "cost_info",
    }
    payload.update(overrides)
    response = client.post("/api/data-sources", json=payload)
    assert response.status_code == 200
    return response.json()


def zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def xlsx_bytes(rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def docx_bytes(paragraphs: list[str]) -> bytes:
    document_xml = "".join(
        f"<w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>"
        for paragraph in paragraphs
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "word/document.xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>{document_xml}</w:body>
</w:document>""",
        )
    return buffer.getvalue()


def test_ingest_endpoint_returns_asset_and_ingest_event():
    client, storage = build_client()

    response = upload(client, "信息价.xlsx", b"xlsx bytes")

    assert response.status_code == 200
    body = response.json()
    assert body["file_id"]
    assert body["ingest_event_id"]
    assert body["bucket"] == "cost-raw"
    assert body["object_key"]
    assert body["sha256"]
    assert body["duplicated"] is False
    assert body["processing_ids"] == []
    assert storage.put_count == 1


def test_zip_preview_lists_images_and_streams_single_entry_ephemerally():
    client, _ = build_client()
    archive = zip_bytes(
        {
            "images/page-001.png": b"\x89PNG\r\n\x1a\nimage-one",
            "images/page-002.jpg": b"\xff\xd8image-two",
            "readme.txt": b"not previewable",
        }
    )
    uploaded = upload(client, "自贡2026年5月图片包.zip", archive)
    file_id = uploaded.json()["file_id"]
    before_processing = client.get("/api/file-processing", params={"file_id": file_id}).json()

    manifest = client.get(f"/api/file-assets/{file_id}/zip-preview")

    assert manifest.status_code == 200
    assert manifest.headers["X-File-Processing"] == "0"
    assert manifest.headers["X-Preview-Mode"] == "ephemeral"
    body = manifest.json()
    assert body["entry_count"] == 3
    assert body["previewable_count"] == 2
    assert [entry["name"] for entry in body["entries"]] == ["images/page-001.png", "images/page-002.jpg"]
    assert body["entries"][0]["preview_url"] == f"/api/file-assets/{file_id}/zip-preview/0"

    image = client.get(f"/api/file-assets/{file_id}/zip-preview/0")

    assert image.status_code == 200
    assert image.content == b"\x89PNG\r\n\x1a\nimage-one"
    assert image.headers["content-type"] == "image/png"
    assert image.headers["X-File-Processing"] == "0"
    assert image.headers["X-Preview-Mode"] == "ephemeral"
    after_processing = client.get("/api/file-processing", params={"file_id": file_id}).json()
    assert len(after_processing) == len(before_processing)


def test_zip_preview_rejects_non_previewable_zip_entries():
    client, _ = build_client()
    uploaded = upload(client, "清单控制价.zip", zip_bytes({"inside/readme.txt": b"not an image"}))
    file_id = uploaded.json()["file_id"]

    manifest = client.get(f"/api/file-assets/{file_id}/zip-preview")
    entry = client.get(f"/api/file-assets/{file_id}/zip-preview/0")

    assert manifest.status_code == 415
    assert manifest.json()["detail"] == "ZIP_PREVIEW_NO_IMAGE_ENTRIES"
    assert entry.status_code == 415


def test_duplicate_upload_returns_existing_file_and_new_event():
    client, storage = build_client()

    first = upload(client, "a.xlsx", b"same bytes", batch_id="batch-001").json()
    second = upload(client, "b.xlsx", b"same bytes", batch_id="batch-002").json()

    assert second["file_id"] == first["file_id"]
    assert second["ingest_event_id"] != first["ingest_event_id"]
    assert second["duplicated"] is True
    assert storage.put_count == 1


def test_download_file_asset_returns_original_blob_bytes():
    client, _ = build_client()
    uploaded = upload(client, "重庆工程造价2026-6期.pdf", b"%PDF original bytes").json()

    response = client.get(f"/api/file-assets/{uploaded['file_id']}/download")

    assert response.status_code == 200
    assert response.content == b"%PDF original bytes"
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment;" in response.headers["content-disposition"]
    assert "filename*=utf-8''" in response.headers["content-disposition"]


def test_storage_audit_endpoint_reports_missing_file_asset_objects():
    client, storage = build_client()
    ok = upload(client, "成都市2026年5月信息价.pdf", b"%PDF ok").json()
    missing = upload(client, "德阳市2026年2月信息价.pdf", b"%PDF missing").json()
    storage.objects.pop((missing["bucket"], missing["object_key"]))

    response = client.get("/api/storage-audit/file-assets")

    assert response.status_code == 200
    body = response.json()
    assert body["checked_count"] == 2
    assert body["ok_count"] == 1
    assert body["missing_count"] == 1
    assert body["size_mismatch_count"] == 0
    assert body["error_count"] == 0
    assert body["health_status"] == "degraded"
    assert body["issues"][0]["file_id"] == missing["file_id"]
    assert body["issues"][0]["status"] == "missing"
    assert body["issues"][0]["file_name"] == "德阳市2026年2月信息价.pdf"


def test_storage_audit_endpoint_filters_archived_cost_info_region_assets():
    client, storage = build_client()
    source = create_data_source(
        client,
        source_scope="platform_public",
        managed_by="platform",
        source_type="info_price",
        connector_type="http_site",
        name="成都市建设工程造价站",
        data_domain="cost_info",
        region_code="510100",
    )
    archived = client.post(
        "/api/file-assets/ingest",
        data={
            "tenant_code": "platform_public",
            "source_type": "info_price",
            "source_id": source["source_id"],
            "batch_id": "audit-archived",
        },
        files={"file": ("成都市2026年5月信息价.pdf", b"%PDF archived", "application/pdf")},
    ).json()
    pending = client.post(
        "/api/file-assets/ingest",
        data={
            "tenant_code": "platform_public",
            "source_type": "info_price",
            "source_id": source["source_id"],
            "batch_id": "audit-pending",
        },
        files={"file": ("成都市2026年6月信息价.pdf", b"%PDF pending", "application/pdf")},
    ).json()
    archived_archive = client.post(
        "/api/archives/from-ingest-event",
        json={
            "event_id": archived["ingest_event_id"],
            "domain_type": "cost_info",
            "channel_type": "crawler",
            "collection_method": "auto",
            "price_kind": "guidance",
            "period_kind": "monthly",
            "title": "成都市2026年5月信息价",
            "visibility_scope": "public",
            "status": "archived",
            "business_key": f"cost_info:{source['source_id']}:510100:2026-05",
            "region_code": "510100",
            "metadata": {"period": cell("2026-05")},
            "field_sources": field_sources("domain_type", "channel_type", "business_key", "title", "region_code"),
        },
    )
    assert archived_archive.status_code == 200, archived_archive.text
    pending_archive = client.post(
        "/api/archives/from-ingest-event",
        json={
            "event_id": pending["ingest_event_id"],
            "domain_type": "cost_info",
            "channel_type": "crawler",
            "collection_method": "auto",
            "price_kind": "guidance",
            "period_kind": "monthly",
            "title": "成都市2026年6月信息价",
            "visibility_scope": "public",
            "status": "pending_tag",
            "business_key": f"cost_info:{source['source_id']}:510100:2026-06",
            "region_code": "510100",
            "metadata": {"period": cell("2026-06")},
            "field_sources": field_sources("domain_type", "channel_type", "business_key", "title", "region_code"),
        },
    )
    assert pending_archive.status_code == 200, pending_archive.text
    storage.objects.pop((pending["bucket"], pending["object_key"]))

    response = client.get("/api/storage-audit/file-assets", params={"domain_type": "cost_info", "region_code": "510100"})

    assert response.status_code == 200
    body = response.json()
    assert body["checked_count"] == 1
    assert body["ok_count"] == 1
    assert body["missing_count"] == 0
    assert body["health_status"] == "healthy"
    assert body["issues"] == []


def test_preview_pdf_returns_inline_original_blob_bytes():
    client, _ = build_client()
    uploaded = upload(client, "2025年成都市信息价02期.pdf", b"%PDF original bytes").json()

    response = client.get(f"/api/file-assets/{uploaded['file_id']}/preview")

    assert response.status_code == 200
    assert response.content == b"%PDF original bytes"
    assert response.headers["content-type"] == "application/pdf"
    assert "inline;" in response.headers["content-disposition"]


def test_preview_excel_returns_ephemeral_html_without_file_processing():
    client, _ = build_client()
    content = xlsx_bytes([["序号", "材料名称", "不含税信息价(元)"], [1, "HRB400E 螺纹钢", 3300.5]])
    uploaded = upload(client, "绵阳市2026年4月信息价.xlsx", content).json()

    before = client.get("/api/file-processing", params={"file_id": uploaded["file_id"]}).json()
    response = client.get(f"/api/file-assets/{uploaded['file_id']}/preview")
    after = client.get("/api/file-processing", params={"file_id": uploaded["file_id"]}).json()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<table" in response.text
    assert "HRB400E 螺纹钢" in response.text
    assert "不含税信息价(元)" in response.text
    assert before == after == []


def test_preview_html_notice_returns_sanitized_ephemeral_reading_html():
    client, _ = build_client()
    raw_html = """
    <html>
      <head><script>alert("head")</script></head>
      <body>
        <div class="right-content">
          <html>
            <head><style>.MsoNormal { color: red; }</style></head>
            <body>
              <p>1.1 本招标项目已批准建设，项目业主为<u>成都建设单位</u>，招标人为<u>成都招标人</u>。</p>
              <p>2.2 计划工期：365日历天。</p>
              <script>window.evil = true;</script>
              <a href="javascript:alert(1)" onclick="evil()">坏链接</a>
            </body>
          </html>
        </div>
        <div id="ContentPlaceHolder1_divfileList">招标文件.CDZ</div>
      </body>
    </html>
    """.encode()
    uploaded = upload(client, "成都招标公告.html", raw_html).json()

    before = client.get("/api/file-processing", params={"file_id": uploaded["file_id"]}).json()
    response = client.get(f"/api/file-assets/{uploaded['file_id']}/preview")
    after = client.get("/api/file-processing", params={"file_id": uploaded["file_id"]}).json()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["X-File-Processing"] == "0"
    assert response.headers["X-Preview-Mode"] == "ephemeral"
    assert 'class="notice-html-preview"' in response.text
    assert "招标人为" in response.text
    assert "计划工期：365日历天" in response.text
    assert "招标文件.CDZ" not in response.text
    assert "<script" not in response.text
    assert "onclick" not in response.text
    assert "javascript:" not in response.text
    assert before == after == []


def test_preview_docx_returns_ephemeral_html_without_file_processing():
    client, _ = build_client()
    content = docx_bytes(["同乐公园设计任务书", "建设内容：公园绿地、配套服务设施。"])
    uploaded = upload(client, "设计任务书-同乐公园.docx", content).json()

    before = client.get("/api/file-processing", params={"file_id": uploaded["file_id"]}).json()
    response = client.get(f"/api/file-assets/{uploaded['file_id']}/preview")
    after = client.get("/api/file-processing", params={"file_id": uploaded["file_id"]}).json()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["X-File-Processing"] == "0"
    assert response.headers["X-Preview-Mode"] == "ephemeral"
    assert 'class="docx-preview"' in response.text
    assert "同乐公园设计任务书" in response.text
    assert "建设内容：公园绿地、配套服务设施。" in response.text
    assert before == after == []


def test_preview_zip_is_download_only_and_does_not_create_processing():
    client, _ = build_client()
    uploaded = upload(client, "自贡2026年5月信息价图片包.zip", zip_bytes({"a.jpg": b"image"})).json()

    before = client.get("/api/file-processing", params={"file_id": uploaded["file_id"]}).json()
    response = client.get(f"/api/file-assets/{uploaded['file_id']}/preview")
    after = client.get("/api/file-processing", params={"file_id": uploaded["file_id"]}).json()

    assert response.status_code == 415
    assert response.json()["detail"] == "PREVIEW_DOWNLOAD_ONLY"
    assert after == before


def test_ingest_event_filter_uses_file_asset_tenant():
    client, _ = build_client()
    upload(client, "a.xlsx", b"same bytes", batch_id="batch-001")

    matched = client.get("/api/ingest-events", params={"tenant_code": "tenant_a", "batch_id": "batch-001"})
    missing = client.get("/api/ingest-events", params={"tenant_code": "tenant_b", "batch_id": "batch-001"})

    assert matched.status_code == 200
    assert len(matched.json()) == 1
    assert missing.status_code == 200
    assert missing.json() == []


def test_ingest_endpoint_passes_source_metadata_and_times():
    client, _ = build_client()

    response = client.post(
        "/api/file-assets/ingest",
        data={
            "tenant_code": "tenant_a",
            "source_type": "info_price",
            "batch_id": "batch-time",
            "source_item_key": "gz-2026-06",
            "source_modified_at": "2026-06-16T09:00:00+08:00",
            "discovered_at": "2026-06-17T10:00:00+08:00",
            "fetched_at": "2026-06-17T10:05:00+08:00",
            "source_metadata": '{"column_path_raw":"直属单位文件","page_title":"2026年6月信息价"}',
        },
        files={"file": ("信息价.xlsx", b"xlsx bytes", "application/octet-stream")},
    )

    assert response.status_code == 200
    events = client.get("/api/ingest-events", params={"tenant_code": "tenant_a", "batch_id": "batch-time"}).json()
    assert events[0]["source_modified_at"].startswith("2026-06-16T09:00:00")
    assert events[0]["source_metadata"] == {
        "column_path_raw": "直属单位文件",
        "page_title": "2026年6月信息价",
        "discovered_at": "2026-06-17T10:00:00+08:00",
        "fetched_at": "2026-06-17T10:05:00+08:00",
    }


def test_file_processing_is_not_externally_created():
    client, _ = build_client()

    response = client.post("/api/file-processing", json={"file_id": "x", "processor": "xls_parse"})

    assert response.status_code == 405


def test_run_processing_endpoint_executes_unzip_task():
    client, storage = build_client()
    ingest = upload(
        client,
        "archive.zip",
        zip_bytes({"inside/材料信息价.xlsx": b"xlsx bytes"}),
    ).json()

    response = client.post(f"/api/file-processing/{ingest['processing_ids'][0]}/run")

    assert response.status_code == 200
    body = response.json()
    assert body["processor"] == "unzip"
    assert body["status"] == "succeeded"
    assert len(body["created_file_ids"]) == 1
    assert body["duplicated_file_ids"] == []
    assert storage.put_count == 2
