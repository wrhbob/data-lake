from pathlib import Path

from app.file_mirror import FileMirror, get_file_mirror
from test_api import build_client, cell, create_data_source, field_sources


def create_mirror_archive(client, *, title="《泉州工程造价管理》2026年第1期.pdf"):
    source = create_data_source(
        client,
        source_scope="platform_public",
        managed_by="platform",
        source_type="info_price",
        connector_type="http_site",
        name="泉州市住房和城乡建设局",
        data_domain="cost_info",
        province="福建省",
        city="泉州市",
        region_code="350500",
        config={
            "stable": {
                "site_id": "cost_info.fj.quanzhou",
                "coverage_region_code": "350500",
                "publisher_scope": "city",
                "publisher_region_code": "350500",
                "publisher_name": "泉州市住房和城乡建设局",
            }
        },
    )
    ingest = client.post(
        "/api/file-assets/ingest",
        data={
            "tenant_code": "platform_public",
            "source_type": "info_price",
            "source_id": source["source_id"],
            "batch_id": "mirror-api",
            "source_item_key": "mirror:350500:2026-01",
            "derive_tasks": "false",
        },
        files={"file": (title, b"%PDF mirror bytes", "application/pdf")},
    )
    assert ingest.status_code == 200

    archive = client.post(
        "/api/archives/from-ingest-event",
        json={
            "event_id": ingest.json()["ingest_event_id"],
            "domain_type": "cost_info",
            "channel_type": "crawler",
            "collection_method": "auto",
            "business_key": "cost_info:fj:quanzhou:2026-01:issue-1",
            "title": "《泉州工程造价管理》2026年第1期",
            "region_code": "350500",
            "publish_date": "2026-01-12",
            "visibility_scope": "public",
            "status": "collected",
            "metadata": {
                "province_raw": cell("福建省"),
                "city_raw": cell("泉州市"),
                "period": cell("2026-01"),
                "period_raw": cell("2026年第1期"),
                "coverage_region_code": cell("350500"),
            },
            "field_sources": field_sources(
                "domain_type",
                "channel_type",
                "collection_method",
                "business_key",
                "title",
                "region_code",
                "publish_date",
            ),
            "actor_type": "crawler",
            "actor_id": "mirror-test",
        },
    )
    assert archive.status_code == 200
    return archive.json()


def test_archive_detail_reports_missing_and_mirrored_nas_directory_status(tmp_path):
    client, _ = build_client()
    client.app.dependency_overrides[get_file_mirror] = lambda: FileMirror(root=tmp_path)
    archive = create_mirror_archive(client)
    archive_id = archive["archive_id"]

    detail_before = client.get(f"/api/archives/{archive_id}")

    assert detail_before.status_code == 200
    file_before = detail_before.json()["files"][0]
    assert file_before["mirror_status"] == "missing"
    assert file_before["mirror_relative_path"] == "信息价/福建省/泉州市/2026/《泉州工程造价管理》2026年第1期.pdf"
    assert file_before["mirror_path"].endswith(file_before["mirror_relative_path"])

    exported = client.post(f"/api/archives/{archive_id}/mirror")

    assert exported.status_code == 200
    body = exported.json()
    assert body["archive_id"] == archive_id
    assert body["exported_count"] == 1
    assert body["files"][0]["mirror_status"] == "mirrored"
    target = Path(body["files"][0]["mirror_path"])
    assert target.read_bytes() == b"%PDF mirror bytes"

    detail_after = client.get(f"/api/archives/{archive_id}")
    assert detail_after.status_code == 200
    assert detail_after.json()["files"][0]["mirror_status"] == "mirrored"


def test_mirror_endpoint_reports_unconfigured_root():
    client, _ = build_client()
    archive = create_mirror_archive(client)

    response = client.post(f"/api/archives/{archive['archive_id']}/mirror")

    assert response.status_code == 400
    assert response.json()["detail"] == "FILE_MIRROR_ROOT_UNCONFIGURED"


def test_bulk_mirror_export_filters_by_domain_type(tmp_path):
    client, _ = build_client()
    client.app.dependency_overrides[get_file_mirror] = lambda: FileMirror(root=tmp_path)
    archive = create_mirror_archive(client)

    response = client.post("/api/file-mirror/export", params={"domain_type": "cost_info", "limit": 10})

    assert response.status_code == 200
    body = response.json()
    assert body["matched_count"] == 1
    assert body["exported_count"] == 1
    assert body["files"][0]["archive_id"] == archive["archive_id"]
    assert body["files"][0]["mirror_status"] == "mirrored"
    assert (tmp_path / body["files"][0]["mirror_relative_path"]).read_bytes() == b"%PDF mirror bytes"
