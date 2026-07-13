from test_api import build_client


def field_sources(*fields, level="crawler", by="api-test"):
    return {
        field: {"source_level": level, "tagged_by": by, "tagged_at": "2026-06-20T10:00:00+08:00"}
        for field in fields
    }


def cell(value, level="crawler", by="api-test"):
    return {"value": value, "source_level": level, "tagged_by": by, "tagged_at": "2026-06-20T10:00:00+08:00"}


def create_cost_info_manual_source(client, *, region_code="511400", city="眉山市"):
    response = client.post(
        "/api/data-sources",
        json={
            "source_scope": "platform_public",
            "managed_by": "platform",
            "source_type": "info_price",
            "connector_type": "manual_upload",
            "name": f"{city}-人工补录信息价",
            "data_domain": "cost_info",
            "region_code": region_code,
            "city": city,
            "config": {
                "stable": {
                    "site_id": f"cost_info.manual.{region_code}",
                    "domain_type": "cost_info",
                    "region_code": region_code,
                    "coverage_region_code": region_code,
                    "publisher_scope": "city",
                    "publisher_region_code": region_code,
                    "publisher_name": f"{city}建设工程造价管理站",
                }
            },
        },
    )
    assert response.status_code == 200
    return response.json()


def create_cost_info_manual_archive(client, source, *, period, title, region_code="511400", content=b"%PDF manual"):
    ingest = client.post(
        "/api/file-assets/ingest",
        data={
            "tenant_code": "platform_public",
            "source_type": "info_price",
            "batch_id": f"manual-{region_code}-{period}",
            "source_id": source["source_id"],
            "source_item_key": f"manual:{region_code}:{period}:{title}",
            "derive_tasks": "false",
        },
        files={"file": (title, content, "application/pdf")},
    )
    assert ingest.status_code == 200
    metadata = {
        "period": cell(period, level="manual", by="ui:manual-upload"),
        "period_start": cell(period, level="manual", by="ui:manual-upload"),
        "period_raw": cell(period, level="manual", by="ui:manual-upload"),
        "coverage_region_code": cell(region_code, level="manual", by="ui:manual-upload"),
        "price_source_type": cell("info_price", level="manual", by="ui:manual-upload"),
        "tax_type": cell(None, level="manual", by="ui:manual-upload"),
        "producer": cell("眉山市建设工程造价管理站", level="manual", by="ui:manual-upload"),
        "publisher": cell("眉山市建设工程造价管理站", level="manual", by="ui:manual-upload"),
        "publisher_scope": cell("city", level="manual", by="ui:manual-upload"),
        "publisher_region_code": cell(region_code, level="manual", by="ui:manual-upload"),
        "parsability": cell("image_based", level="manual", by="ui:manual-upload"),
        "publication_mode": cell("MANUAL_ONLY", level="manual", by="ui:manual-upload"),
        "source_attachment_mode": cell("pdf_only", level="manual", by="ui:manual-upload"),
    }
    archive = client.post(
        "/api/archives/from-ingest-event",
        json={
            "event_id": ingest.json()["ingest_event_id"],
            "domain_type": "cost_info",
            "channel_type": "manual_upload",
            "collection_method": "manual_denovo",
            "business_key": f"cost_info:{source['source_id']}:{region_code}:{period}:{title}",
            "title": title,
            "region_code": region_code,
            "publish_date": None,
            "visibility_scope": "public",
            "status": "collected",
            "metadata": metadata,
            "field_sources": field_sources(
                "domain_type",
                "channel_type",
                "collection_method",
                "business_key",
                "title",
                "region_code",
                "publish_date",
                level="manual",
                by="ui:manual-upload",
            ),
            "actor_type": "user",
            "actor_id": "ui:manual-upload",
        },
    )
    assert archive.status_code == 200
    return archive.json()


def create_source(client):
    response = client.post(
        "/api/data-sources",
        json={
            "source_scope": "platform_public",
            "managed_by": "platform",
            "source_type": "public_resource_exchange",
            "connector_type": "http_site",
            "name": "杭州市公共资源交易中心",
            "data_domain": "trading",
        },
    )
    assert response.status_code == 200
    return response.json()


def create_archive(client, source_id, *, business_key="trading:src:notice-api-001", status="pending_tag"):
    response = client.post(
        "/api/archives",
        json={
            "domain_type": "trading",
            "channel_type": "crawler",
            "business_key": business_key,
            "title": "学校施工招标公告",
            "source_id": source_id,
            "tenant_code": "platform_public",
            "visibility_scope": "public",
            "status": status,
            "metadata": {
                "project_code_raw": cell("P-API-001"),
                "notice_type_raw": cell("招标公告"),
            },
            "field_sources": field_sources("domain_type", "channel_type", "business_key", "title"),
        },
    )
    assert response.status_code == 200
    return response.json()


def test_cost_info_archive_api_exposes_price_kind_as_additive_field():
    client, _ = build_client()
    source = create_cost_info_manual_source(client, region_code="110000", city="北京市")

    created = client.post(
        "/api/archives",
        json={
            "domain_type": "cost_info",
            "channel_type": "crawler",
            "business_key": f"cost_info:{source['source_id']}:110000:2026-01:2026年01月北京工程造价信息",
            "title": "2026年01月北京工程造价信息",
            "source_id": source["source_id"],
            "tenant_code": "platform_public",
            "visibility_scope": "public",
            "status": "pending_tag",
            "region_code": "110000",
            "publish_date": "2026-01-22",
            "price_kind": "guidance",
            "period_kind": "issue_based",
            "metadata": {"period": cell("2026-01")},
            "field_sources": field_sources("domain_type", "channel_type", "business_key", "title", "region_code", "publish_date"),
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["price_kind"] == "guidance"
    assert body["period_kind"] == "issue_based"
    assert body["business_key"].endswith(":2026年01月北京工程造价信息")

    listed = client.get("/api/archives", params={"domain_type": "cost_info"})
    assert listed.status_code == 200
    assert listed.json()[0]["price_kind"] == "guidance"
    assert listed.json()[0]["period_kind"] == "issue_based"

    patched = client.patch(f"/api/archives/{body['archive_id']}", json={"price_kind": "market_reference", "period_kind": "monthly"})
    assert patched.status_code == 200
    assert patched.json()["price_kind"] == "market_reference"
    assert patched.json()["period_kind"] == "monthly"
    assert patched.json()["business_key"] == body["business_key"]


def test_cost_info_archive_list_falls_back_publisher_metadata_from_source_config():
    client, _ = build_client()
    source = client.post(
        "/api/data-sources",
        json={
            "source_scope": "platform_public",
            "managed_by": "platform",
            "source_type": "info_price",
            "connector_type": "source_registry",
            "name": "泸州市住房和城乡建设局",
            "data_domain": "cost_info",
            "province": "四川",
            "city": "泸州市",
            "region_code": "510500",
            "config": {
                "stable": {
                    "site_id": "cost_info.sc.luzhou",
                    "publisher_type": "official_housing_urban_rural_development",
                    "publisher_scope": "city",
                    "publisher_name": "泸州市住房和城乡建设局",
                    "publisher_region_code": "510500",
                }
            },
        },
    )
    assert source.status_code == 200
    source_id = source.json()["source_id"]

    created = client.post(
        "/api/archives",
        json={
            "domain_type": "cost_info",
            "channel_type": "crawler",
            "business_key": f"cost_info:{source_id}:510500:2026-05:泸州2026年5月信息价",
            "title": "泸州2026年5月信息价",
            "source_id": source_id,
            "tenant_code": "platform_public",
            "visibility_scope": "public",
            "status": "pending_tag",
            "region_code": "510500",
            "metadata": {
                "period": cell("2026-05"),
                "publisher_scope": cell("city"),
            },
            "field_sources": field_sources("domain_type", "channel_type", "business_key", "title", "region_code"),
        },
    )
    assert created.status_code == 200

    listed = client.get("/api/archives", params={"domain_type": "cost_info", "source_id": source_id}).json()
    detail = client.get(f"/api/archives/{created.json()['archive_id']}").json()

    assert listed[0]["metadata"]["publisher_type"]["value"] == "official_housing_urban_rural_development"
    assert listed[0]["metadata"]["publisher_scope"]["value"] == "city"
    assert listed[0]["metadata"]["publisher_name"]["value"] == "泸州市住房和城乡建设局"
    assert listed[0]["metadata"]["publisher_region_code"]["value"] == "510500"
    assert listed[0]["metadata"]["publisher_type"]["source_level"] == "source_config"
    assert detail["metadata"]["publisher_type"]["value"] == "official_housing_urban_rural_development"


def test_cost_info_archive_api_defaults_price_kind_to_unspecified():
    client, _ = build_client()
    source = create_cost_info_manual_source(client)

    created = client.post(
        "/api/archives",
        json={
            "domain_type": "cost_info",
            "channel_type": "manual_upload",
            "business_key": f"cost_info:{source['source_id']}:511400:2026-06:眉山市2026年6月信息价.pdf",
            "title": "眉山市2026年6月信息价.pdf",
            "source_id": source["source_id"],
            "tenant_code": "platform_public",
            "visibility_scope": "public",
            "status": "pending_tag",
            "region_code": "511400",
            "metadata": {"period": cell("2026-06", level="manual", by="ui:manual-upload")},
            "field_sources": field_sources("domain_type", "channel_type", "business_key", "title", "region_code", level="manual", by="ui:manual-upload"),
        },
    )

    assert created.status_code == 200
    assert created.json()["price_kind"] == "unspecified"
    assert created.json()["period_kind"] == "monthly"


def ingest_file(client, *, file_name="notice.html", content=b"<html></html>"):
    response = client.post(
        "/api/file-assets/ingest",
        data={
            "tenant_code": "platform_public",
            "source_type": "public_resource_exchange",
            "batch_id": "batch-api-001",
        },
        files={"file": (file_name, content, "application/octet-stream")},
    )
    assert response.status_code == 200
    return response.json()


def test_archive_api_create_list_detail_patch_and_attach():
    client, _ = build_client()
    source = create_source(client)
    archive = create_archive(client, source["source_id"])
    file_asset = ingest_file(client)

    attached = client.post(
        f"/api/archives/{archive['archive_id']}/files",
        json={
            "file_id": file_asset["file_id"],
            "file_role": "web_snapshot",
            "is_primary": True,
            "sort_order": 10,
        },
    )
    assert attached.status_code == 200
    assert attached.json()["file_role"] == "web_snapshot"

    listed = client.get("/api/archives", params={"domain_type": "trading", "search": "学校"})
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert rows[0]["archive_id"] == archive["archive_id"]
    assert rows[0]["file_count"] == 1
    assert rows[0]["primary_file"]["file_id"] == file_asset["file_id"]
    assert "project_group_key" not in rows[0]

    detail = client.get(f"/api/archives/{archive['archive_id']}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["metadata"]["project_code_raw"]["value"] == "P-API-001"
    assert body["field_sources"]["title"]["source_level"] == "crawler"
    assert body["files"][0]["file_id"] == file_asset["file_id"]
    assert "winning_amount" not in body

    patched = client.patch(
        f"/api/archives/{archive['archive_id']}",
        json={
            "status": "ready_for_governance",
            "metadata": {"exchange_name": cell("杭州市公共资源交易中心", level="manual", by="user:test")},
            "field_sources": field_sources(
                "title",
                "domain_type",
                "channel_type",
                "business_key",
                level="manual",
                by="user:test",
            ),
        },
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "ready_for_governance"
    assert patched.json()["metadata"]["notice_type_raw"]["value"] == "招标公告"
    assert patched.json()["metadata"]["exchange_name"]["value"] == "杭州市公共资源交易中心"


def test_archive_list_returns_total_count_header_and_supports_offset():
    client, _ = build_client()
    source = create_source(client)
    for index in range(3):
        create_archive(client, source["source_id"], business_key=f"trading:src:notice-api-{index}")

    first_page = client.get("/api/archives", params={"domain_type": "trading", "limit": 2})

    assert first_page.status_code == 200
    assert first_page.headers["x-total-count"] == "3"
    assert len(first_page.json()) == 2

    second_page = client.get("/api/archives", params={"domain_type": "trading", "limit": 2, "offset": 2})

    assert second_page.status_code == 200
    assert second_page.headers["x-total-count"] == "3"
    assert len(second_page.json()) == 1


def test_archive_api_rejects_project_group_key_and_forced_priced_source():
    client, _ = build_client()
    source = create_source(client)

    rejected = client.post(
        "/api/archives",
        json={
            "domain_type": "trading",
            "channel_type": "crawler",
            "business_key": "trading:src:notice-api-002",
            "title": "施工招标公告",
            "source_id": source["source_id"],
            "tenant_code": "platform_public",
            "visibility_scope": "public",
            "status": "pending_tag",
            "metadata": {"project_group_key": cell("do-not-store")},
            "field_sources": field_sources("domain_type", "channel_type", "business_key", "title"),
        },
    )
    assert rejected.status_code == 400

    archive = create_archive(client, source["source_id"], business_key="trading:src:notice-api-003")
    pdf = ingest_file(client, file_name="notice.pdf", content=b"pdf bytes")
    forced = client.post(
        f"/api/archives/{archive['archive_id']}/files",
        json={"file_id": pdf["file_id"], "file_role": "priced_source"},
    )

    assert forced.status_code == 400
    assert "PRICED_SOURCE_ROLE_MISMATCH" in forced.json()["detail"]


def test_archive_api_allows_additive_attach_after_archived():
    client, _ = build_client()
    source = create_source(client)
    archive = create_archive(
        client,
        source["source_id"],
        business_key="trading:src:notice-api-004",
        status="archived",
    )
    file_asset = ingest_file(client)

    response = client.post(
        f"/api/archives/{archive['archive_id']}/files",
        json={"file_id": file_asset["file_id"], "file_role": "web_snapshot"},
    )

    assert response.status_code == 200
    assert response.json()["file_id"] == file_asset["file_id"]
    detail = client.get(f"/api/archives/{archive['archive_id']}").json()
    assert detail["version"] == 1
    assert len(detail["files"]) == 1


def test_archive_api_create_from_ingest_event():
    client, _ = build_client()
    source = create_source(client)
    ingest = client.post(
        "/api/file-assets/ingest",
        data={
            "tenant_code": "platform_public",
            "source_type": "public_resource_exchange",
            "batch_id": "batch-from-event",
            "source_id": source["source_id"],
            "source_url": "https://example.gov.cn/notice/from-event",
            "source_item_key": "notice-from-event",
        },
        files={"file": ("notice.html", b"<html>notice</html>", "text/html")},
    )
    assert ingest.status_code == 200

    response = client.post(
        "/api/archives/from-ingest-event",
        json={
            "event_id": ingest.json()["ingest_event_id"],
            "domain_type": "trading",
            "channel_type": "crawler",
            "title": "施工招标公告",
            "metadata": {"notice_type_raw": cell("招标公告"), "project_code_raw": cell("P-FROM-EVENT")},
            "field_sources": field_sources("domain_type", "channel_type", "title"),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["business_key"] == f"trading:{source['source_id']}:notice-from-event"
    assert body["tenant_code"] == "platform_public"
    assert body["source_id"] == source["source_id"]
    assert body["batch_id"] == "batch-from-event"
    assert body["source_item_key"] == "notice-from-event"
    assert body["files"][0]["file_id"] == ingest.json()["file_id"]
    assert body["files"][0]["is_primary"] is True


def _cost_info_from_ingest_event_body(event_id, *, title, period, region_code="511400"):
    return {
        "event_id": event_id,
        "domain_type": "cost_info",
        "channel_type": "manual_upload",
        "collection_method": "manual_denovo",
        "title": title,
        "region_code": region_code,
        "metadata": {
            "period": cell(period, level="manual", by="ui:test"),
            "period_start": cell(period, level="manual", by="ui:test"),
            "period_raw": cell(period, level="manual", by="ui:test"),
            "coverage_region_code": cell(region_code, level="manual", by="ui:test"),
        },
        "field_sources": field_sources(
            "domain_type",
            "channel_type",
            "collection_method",
            "title",
            "region_code",
            level="manual",
            by="ui:test",
        ),
    }


def test_ingest_existing_archives_and_attached_to_existing_matrix():
    # 矩阵：新建/补挂 × 首传/重复，覆盖 existing_archives 与 attached_to_existing 两个字段
    client, _ = build_client()
    source = create_cost_info_manual_source(client)

    def ingest(filename, content):
        return client.post(
            "/api/file-assets/ingest",
            data={
                "tenant_code": "platform_public",
                "source_type": "info_price",
                "batch_id": "batch-matrix",
                "source_id": source["source_id"],
                "source_item_key": f"item:{filename}",
                "derive_tasks": "false",
            },
            files={"file": (filename, content, "application/pdf")},
        )

    # 首传：全新文件 → duplicated=False，尚未挂任何档案 → existing_archives=[]
    first = ingest("a.pdf", b"%PDF A")
    assert first.status_code == 200, first.text
    assert first.json()["duplicated"] is False
    assert first.json()["existing_archives"] == []

    # 文件入湖但未挂档案时重传 → duplicated=True，空关联仍返 []（非 null）
    redup_before_attach = ingest("a.pdf", b"%PDF A")
    assert redup_before_attach.json()["duplicated"] is True
    assert redup_before_attach.json()["existing_archives"] == []

    # 新建档案（首文件 a.pdf）→ attached_to_existing=False
    created = client.post(
        "/api/archives/from-ingest-event",
        json=_cost_info_from_ingest_event_body(
            first.json()["ingest_event_id"], title="眉山2025第5期信息价", period="2025-05"
        ),
    )
    assert created.status_code == 200, created.text
    assert created.json()["attached_to_existing"] is False
    archive_id = created.json()["archive_id"]

    # 第二个新文件（不同内容、同 business_key）→ 补挂到已有档案，attached_to_existing=True
    second = ingest("b.pdf", b"%PDF B")
    assert second.json()["duplicated"] is False
    assert second.json()["existing_archives"] == []
    attached = client.post(
        "/api/archives/from-ingest-event",
        json=_cost_info_from_ingest_event_body(
            second.json()["ingest_event_id"], title="眉山2025第5期信息价", period="2025-05"
        ),
    )
    assert attached.status_code == 200, attached.text
    assert attached.json()["attached_to_existing"] is True
    assert attached.json()["archive_id"] == archive_id

    # 重传 a.pdf（已挂到档案）→ duplicated=True，existing_archives 含该档案
    redup_after_attach = ingest("a.pdf", b"%PDF A")
    assert redup_after_attach.json()["duplicated"] is True
    assert redup_after_attach.json()["existing_archives"] == [
        {"archive_id": archive_id, "title": "眉山2025第5期信息价"}
    ]


def test_existing_archives_one_to_many_most_recent_first():
    # 同一 sha256 挂在多个档案 → existing_archives 一对多，按 attach 时间倒序（最近在首位）
    client, _ = build_client()
    source = create_cost_info_manual_source(client)

    ingest = client.post(
        "/api/file-assets/ingest",
        data={
            "tenant_code": "platform_public",
            "source_type": "info_price",
            "batch_id": "batch-1tomany",
            "source_id": source["source_id"],
            "source_item_key": "item:shared.pdf",
            "derive_tasks": "false",
        },
        files={"file": ("shared.pdf", b"%PDF SHARED", "application/pdf")},
    )
    assert ingest.status_code == 200, ingest.text
    event_id = ingest.json()["ingest_event_id"]

    archive_older = client.post(
        "/api/archives/from-ingest-event",
        json=_cost_info_from_ingest_event_body(event_id, title="眉山2025第4期信息价", period="2025-04"),
    )
    archive_newer = client.post(
        "/api/archives/from-ingest-event",
        json=_cost_info_from_ingest_event_body(event_id, title="眉山2025第6期信息价", period="2025-06"),
    )
    assert archive_older.status_code == 200 and archive_newer.status_code == 200
    assert archive_older.json()["archive_id"] != archive_newer.json()["archive_id"]

    redup = client.post(
        "/api/file-assets/ingest",
        data={
            "tenant_code": "platform_public",
            "source_type": "info_price",
            "batch_id": "batch-1tomany-2",
            "source_id": source["source_id"],
            "source_item_key": "item:shared.pdf",
            "derive_tasks": "false",
        },
        files={"file": ("shared.pdf", b"%PDF SHARED", "application/pdf")},
    )
    assert redup.status_code == 200, redup.text
    assert [item["archive_id"] for item in redup.json()["existing_archives"]] == [
        archive_newer.json()["archive_id"],
        archive_older.json()["archive_id"],
    ]


def test_archive_api_from_ingest_event_error_codes_are_stable():
    client, _ = build_client()

    missing = client.post(
        "/api/archives/from-ingest-event",
        json={
            "event_id": "missing-event",
            "domain_type": "trading",
            "channel_type": "crawler",
            "title": "施工招标公告",
            "field_sources": field_sources("domain_type", "channel_type", "title"),
        },
    )
    assert missing.status_code == 404
    assert "INGEST_EVENT_NOT_FOUND" in missing.json()["detail"]

    source = create_source(client)
    ingest = client.post(
        "/api/file-assets/ingest",
        data={
            "tenant_code": "platform_public",
            "source_type": "public_resource_exchange",
            "batch_id": "batch-error",
            "source_id": source["source_id"],
            "source_item_key": "notice-error",
        },
        files={"file": ("notice.html", b"<html>notice</html>", "text/html")},
    ).json()
    rejected = client.post(
        "/api/archives/from-ingest-event",
        json={
            "event_id": ingest["ingest_event_id"],
            "domain_type": "trading",
            "channel_type": "crawler",
            "title": "施工招标公告",
            "metadata": {"project_group_key": cell("forbidden")},
            "field_sources": field_sources("domain_type", "channel_type", "title"),
        },
    )

    assert rejected.status_code == 400
    assert "PROJECT_GROUP_KEY_FORBIDDEN" in rejected.json()["detail"]


def test_quota_archive_api_create_attach_list_detail():
    client, _ = build_client()
    source = client.post(
        "/api/data-sources",
        json={
            "source_scope": "platform_public",
            "managed_by": "platform",
            "source_type": "quota_library",
            "connector_type": "manual_upload",
            "name": "四川定额库导入",
            "data_domain": "quota",
        },
    )
    assert source.status_code == 200
    source_body = source.json()

    ingest = client.post(
        "/api/file-assets/ingest",
        data={
            "tenant_code": "platform_public",
            "source_type": "quota_library",
            "batch_id": "quota-batch-001",
            "source_id": source_body["source_id"],
            "source_item_key": "sichuan-jzzs-2020",
        },
        files={
            "file": (
                "四川2020定额库.xlsx",
                b"xlsx bytes",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert ingest.status_code == 200
    ingest_body = ingest.json()

    archive = client.post(
        "/api/archives",
        json={
            "domain_type": "quota",
            "channel_type": "manual_upload",
            "business_key": "quota:sichuan:JZZS:2020:2020-01-01",
            "title": "四川2020定额库",
            "source_id": source_body["source_id"],
            "tenant_code": "platform_public",
            "visibility_scope": "public",
            "status": "pending_tag",
            "region_code": "510000",
            "metadata": {
                "quota_code_raw": cell("JZZS2020", level="manual", by="user:test"),
                "version": cell("2020", level="manual", by="user:test"),
                "effective_date": cell("2020-01-01", level="manual", by="user:test"),
                "specialty_raw": cell("建筑", level="manual", by="user:test"),
            },
            "field_sources": field_sources(
                "domain_type",
                "channel_type",
                "business_key",
                "title",
                "region_code",
                level="manual",
                by="user:test",
            ),
        },
    )
    assert archive.status_code == 200
    archive_body = archive.json()

    attached = client.post(
        f"/api/archives/{archive_body['archive_id']}/files",
        json={"file_id": ingest_body["file_id"], "is_primary": True, "sort_order": 10},
    )
    assert attached.status_code == 200
    assert attached.json()["file_role"] == "quota_db"

    listed = client.get("/api/archives", params={"domain_type": "quota", "search": "四川"})
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert rows[0]["title"] == "四川2020定额库"
    assert rows[0]["business_key"] == "quota:sichuan:JZZS:2020:2020-01-01"

    detail = client.get(f"/api/archives/{archive_body['archive_id']}")
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["metadata"]["quota_code_raw"]["value"] == "JZZS2020"
    assert detail_body["field_sources"]["region_code"]["source_level"] == "manual"
    assert detail_body["files"][0]["file_role"] == "quota_db"


def test_cost_info_manual_upload_create_and_duplicate_link():
    client, _ = build_client()
    source = client.post(
        "/api/data-sources",
        json={
            "source_scope": "platform_public",
            "managed_by": "platform",
            "source_type": "info_price",
            "connector_type": "manual_upload",
            "name": "成都市建设工程造价和招投标监督服务站-信息价",
            "data_domain": "cost_info",
            "region_code": "510100",
            "city": "成都市",
            "config": {
                "stable": {
                    "site_id": "cost_info.sc.chengdu.manual",
                    "domain_type": "cost_info",
                    "region_code": "510100",
                    "coverage_region_code": "510100",
                    "publisher_scope": "city",
                    "publisher_region_code": "510100",
                    "publisher_name": "成都市建设工程造价和招投标监督服务站",
                },
                "ops": {"source_audit_status": "来源受阻"},
            },
        },
    )
    assert source.status_code == 200
    source_body = source.json()
    business_key = f"cost_info:{source_body['source_id']}:510100:2025-02:2025年成都市信息价02期"
    metadata = {
        "period": cell("2025-02", level="manual", by="user:chengdu"),
        "period_start": cell("2025-02", level="manual", by="user:chengdu"),
        "period_raw": cell("2025年成都市信息价02期", level="manual", by="user:chengdu"),
        "coverage_region_code": cell("510100", level="manual", by="user:chengdu"),
        "price_source_type": cell("info_price", level="manual", by="user:chengdu"),
        "tax_type": cell(None, level="manual", by="user:chengdu"),
        "producer": cell("成都市建设工程造价和招投标监督服务站", level="manual", by="user:chengdu"),
        "publisher": cell("成都市建设工程造价和招投标监督服务站", level="manual", by="user:chengdu"),
        "supervising_authority": cell("成都市住房和城乡建设局", level="manual", by="user:chengdu"),
        "publisher_scope": cell("city", level="manual", by="user:chengdu"),
        "publisher_region_code": cell("510100", level="manual", by="user:chengdu"),
        "parsability": cell("image_based", level="manual", by="user:chengdu"),
        "publication_mode": cell("MANUAL_ONLY", level="manual", by="user:chengdu"),
        "source_attachment_mode": cell("pdf_only", level="manual", by="user:chengdu"),
    }
    common_data = {
        "tenant_code": "platform_public",
        "source_type": "info_price",
        "batch_id": "manual-chengdu-2025-02",
        "source_id": source_body["source_id"],
        "source_item_key": "chengdu-info-price-2025-02",
    }

    first_ingest = client.post(
        "/api/file-assets/ingest",
        data=common_data,
        files={"file": ("2025年成都市信息价02期.pdf", b"%PDF manual bytes", "application/pdf")},
    )
    assert first_ingest.status_code == 200
    first_ingest_body = first_ingest.json()
    first_archive = client.post(
        "/api/archives/from-ingest-event",
        json={
            "event_id": first_ingest_body["ingest_event_id"],
            "domain_type": "cost_info",
            "channel_type": "manual_upload",
            "collection_method": "manual_denovo",
            "business_key": business_key,
            "title": "2025年成都市信息价02期",
            "region_code": "510100",
            "publish_date": "2025-03-03",
            "visibility_scope": "public",
            "status": "collected",
            "metadata": metadata,
            "field_sources": field_sources(
                "domain_type",
                "channel_type",
                "business_key",
                "title",
                "region_code",
                "publish_date",
                level="manual",
                by="user:chengdu",
            ),
            "actor_type": "user",
            "actor_id": "user:chengdu",
        },
    )
    assert first_archive.status_code == 200

    second_ingest = client.post(
        "/api/file-assets/ingest",
        data={**common_data, "batch_id": "manual-chengdu-2025-02-repeat"},
        files={"file": ("2025年成都市信息价02期.pdf", b"%PDF manual bytes", "application/pdf")},
    )
    assert second_ingest.status_code == 200
    second_ingest_body = second_ingest.json()
    second_archive = client.post(
        "/api/archives/from-ingest-event",
        json={
            "event_id": second_ingest_body["ingest_event_id"],
            "domain_type": "cost_info",
            "channel_type": "manual_upload",
            "collection_method": "manual_denovo",
            "business_key": business_key,
            "title": "2025年成都市信息价02期",
            "region_code": "510100",
            "publish_date": "2025-03-03",
            "visibility_scope": "public",
            "status": "collected",
            "metadata": metadata,
            "field_sources": field_sources(
                "domain_type",
                "channel_type",
                "business_key",
                "title",
                "region_code",
                "publish_date",
                level="manual",
                by="user:chengdu",
            ),
            "actor_type": "user",
            "actor_id": "user:chengdu",
        },
    )

    assert second_ingest_body["file_id"] == first_ingest_body["file_id"]
    assert second_ingest_body["duplicated"] is True
    assert second_archive.status_code == 200
    assert second_archive.json()["archive_id"] == first_archive.json()["archive_id"]
    detail = client.get(f"/api/archives/{first_archive.json()['archive_id']}")
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["channel_type"] == "manual_upload"
    assert detail_body["collection_method"] == "manual_denovo"
    assert detail_body["business_key"] == business_key
    assert detail_body["metadata"]["period"]["source_level"] == "manual"
    assert detail_body["metadata"]["tax_type"]["value"] is None
    assert detail_body["metadata"]["parsability"]["value"] == "image_based"
    assert len(detail_body["files"]) == 1
    processing = client.get("/api/file-processing", params={"file_id": first_ingest_body["file_id"]})
    assert processing.status_code == 200
    assert processing.json() == []


def test_cost_info_manual_upload_turns_blocked_coverage_cell_present():
    client, _ = build_client()
    blocked_source = client.post(
        "/api/data-sources",
        json={
            "source_scope": "platform_public",
            "managed_by": "platform",
            "source_type": "info_price",
            "connector_type": "source_registry",
            "name": "眉山市信息价来源受阻声明",
            "data_domain": "cost_info",
            "region_code": "511400",
            "city": "眉山市",
            "config": {
                "stable": {
                    "site_id": "cost_info.sc.meishan.blocked",
                    "domain_type": "cost_info",
                    "region_code": "511400",
                    "publisher_scope": "city",
                    "publisher_region_code": "511400",
                    "publisher_name": "眉山市建设工程造价管理站",
                },
                "coverage_expectation": {
                    "target_regions": [
                        {
                            "region_code": "511400",
                            "region_name": "眉山市",
                            "target_level": "city",
                            "requires_city_source": True,
                            "source_completeness_status": "source_blocked",
                            "source_audit_status": "source_blocked",
                            "source_audit_note": "官方源受阻，走017人工补录",
                        }
                    ]
                },
            },
        },
    )
    assert blocked_source.status_code == 200
    before = client.get(
        "/api/info-price/coverage-matrix",
        params={"start_period": "2026-06", "end_period": "2026-06", "province_code": "510000"},
    )
    assert before.status_code == 200
    before_row = next(row for row in before.json() if row["coverage_region_code"] == "511400")
    assert before_row["business_coverage_status"] == "pending_verify"
    assert before_row["source_completeness_status"] == "source_blocked"

    manual_source = client.post(
        "/api/data-sources",
        json={
            "source_scope": "platform_public",
            "managed_by": "platform",
            "source_type": "info_price",
            "connector_type": "manual_upload",
            "name": "眉山市-人工补录信息价",
            "data_domain": "cost_info",
            "region_code": "511400",
            "city": "眉山市",
            "config": {
                "stable": {
                    "site_id": "cost_info.manual.511400",
                    "domain_type": "cost_info",
                    "region_code": "511400",
                    "coverage_region_code": "511400",
                    "publisher_scope": "city",
                    "publisher_region_code": "511400",
                    "publisher_name": "眉山市建设工程造价管理站",
                },
                "ops": {"source_audit_status": "人工补录"},
            },
        },
    )
    assert manual_source.status_code == 200
    source_body = manual_source.json()
    ingest = client.post(
        "/api/file-assets/ingest",
        data={
            "tenant_code": "platform_public",
            "source_type": "info_price",
            "batch_id": "manual-meishan-2026-06",
            "source_id": source_body["source_id"],
            "source_item_key": "manual:511400:2026-06:眉山市2026年6月信息价.pdf",
        },
        files={"file": ("眉山市2026年6月信息价.pdf", b"%PDF manual meishan", "application/pdf")},
    )
    assert ingest.status_code == 200
    ingest_body = ingest.json()
    metadata = {
        "period": cell("2026-06", level="manual", by="ui:manual-upload"),
        "period_start": cell("2026-06", level="manual", by="ui:manual-upload"),
        "period_raw": cell("2026-06", level="manual", by="ui:manual-upload"),
        "coverage_region_code": cell("511400", level="manual", by="ui:manual-upload"),
        "price_source_type": cell("info_price", level="manual", by="ui:manual-upload"),
        "tax_type": cell(None, level="manual", by="ui:manual-upload"),
        "producer": cell("眉山市建设工程造价管理站", level="manual", by="ui:manual-upload"),
        "publisher": cell("眉山市建设工程造价管理站", level="manual", by="ui:manual-upload"),
        "publisher_scope": cell("city", level="manual", by="ui:manual-upload"),
        "publisher_region_code": cell("511400", level="manual", by="ui:manual-upload"),
        "parsability": cell("image_based", level="manual", by="ui:manual-upload"),
        "publication_mode": cell("MANUAL_ONLY", level="manual", by="ui:manual-upload"),
        "source_attachment_mode": cell("pdf_only", level="manual", by="ui:manual-upload"),
    }
    archive = client.post(
        "/api/archives/from-ingest-event",
        json={
            "event_id": ingest_body["ingest_event_id"],
            "domain_type": "cost_info",
            "channel_type": "manual_upload",
            "collection_method": "manual_denovo",
            "business_key": f"cost_info:{source_body['source_id']}:511400:2026-06:眉山市2026年6月信息价.pdf",
            "title": "眉山市2026年6月信息价.pdf",
            "region_code": "511400",
            "publish_date": None,
            "visibility_scope": "public",
            "status": "collected",
            "metadata": metadata,
            "field_sources": field_sources(
                "domain_type",
                "channel_type",
                "collection_method",
                "business_key",
                "title",
                "region_code",
                "publish_date",
                level="manual",
                by="ui:manual-upload",
            ),
            "actor_type": "user",
            "actor_id": "ui:manual-upload",
        },
    )
    assert archive.status_code == 200

    after = client.get(
        "/api/info-price/coverage-matrix",
        params={"start_period": "2026-06", "end_period": "2026-06", "province_code": "510000"},
    )
    assert after.status_code == 200
    after_row = next(row for row in after.json() if row["coverage_region_code"] == "511400")
    assert after_row["business_coverage_status"] == "covered"
    assert after_row["source_completeness_status"] == "city_source_present"
    assert after_row["city_source_count"] == 1
    assert source_body["source_id"] in after_row["source_ids"]
    processing = client.get("/api/file-processing", params={"file_id": ingest_body["file_id"]})
    assert processing.status_code == 200
    assert processing.json() == []


def test_coverage_matrix_exposes_primary_file_download_for_covered_cell():
    client, _ = build_client()
    source = create_cost_info_manual_source(client, region_code="110000", city="北京市")
    archive = create_cost_info_manual_archive(
        client,
        source,
        period="2026-06",
        title="北京市2026年6月信息价.pdf",
        region_code="110000",
        content=b"%PDF beijing june",
    )

    response = client.get(
        "/api/info-price/coverage-matrix",
        params={"start_period": "2026-06", "end_period": "2026-06", "province_code": "110000"},
    )

    assert response.status_code == 200
    row = next(item for item in response.json() if item["coverage_region_code"] == "110000")
    assert row["business_coverage_status"] == "covered"
    assert row["evidence_archive_ids"] == [archive["archive_id"]]
    assert row["primary_file_id"] == archive["files"][0]["file_id"]
    assert row["primary_file_name"] == "北京市2026年6月信息价.pdf"
    assert row["primary_download_url"] == f"/api/file-assets/{archive['files'][0]['file_id']}/download"
    assert row["file_count"] == 1


def test_manual_archive_patch_recomputes_business_key_and_rejects_duplicate():
    client, _ = build_client()
    source = create_cost_info_manual_source(client)
    may = create_cost_info_manual_archive(
        client,
        source,
        period="2026-05",
        title="2026年第5期眉山市建设工程造价信息.pdf",
        content=b"%PDF meishan may",
    )
    june_wrong = create_cost_info_manual_archive(
        client,
        source,
        period="2026-05",
        title="2026年第6期眉山市建设工程造价信息.pdf",
        content=b"%PDF meishan june",
    )
    original_detail = client.get(f"/api/archives/{june_wrong['archive_id']}").json()
    original_file_id = original_detail["files"][0]["file_id"]

    corrected_key = f"cost_info:{source['source_id']}:511400:2026-06:2026年第6期眉山市建设工程造价信息.pdf"
    patched = client.patch(
        f"/api/archives/{june_wrong['archive_id']}",
        json={
            "business_key": corrected_key,
            "title": "2026年第6期眉山市建设工程造价信息.pdf",
            "region_code": "511400",
            "publish_date": None,
            "metadata": {
                "period": cell("2026-06", level="manual", by="ui:manual-edit"),
                "period_start": cell("2026-06", level="manual", by="ui:manual-edit"),
                "period_raw": cell("2026-06", level="manual", by="ui:manual-edit"),
                "coverage_region_code": cell("511400", level="manual", by="ui:manual-edit"),
                "price_source_type": cell("info_price", level="manual", by="ui:manual-edit"),
                "tax_type": cell(None, level="manual", by="ui:manual-edit"),
                "producer": cell("眉山市建设工程造价管理站", level="manual", by="ui:manual-edit"),
                "publisher": cell("眉山市建设工程造价管理站", level="manual", by="ui:manual-edit"),
                "publisher_scope": cell("city", level="manual", by="ui:manual-edit"),
                "publisher_region_code": cell("511400", level="manual", by="ui:manual-edit"),
            },
            "field_sources": field_sources(
                "business_key",
                "title",
                "region_code",
                "publish_date",
                level="manual",
                by="ui:manual-edit",
            ),
            "actor_type": "user",
            "actor_id": "ui:manual-edit",
        },
    )

    assert patched.status_code == 200
    patched_body = patched.json()
    assert patched_body["business_key"] == corrected_key
    assert patched_body["metadata"]["period"]["value"] == "2026-06"
    assert patched_body["metadata"]["period"]["source_level"] == "manual"
    detail = client.get(f"/api/archives/{june_wrong['archive_id']}").json()
    assert detail["files"][0]["file_id"] == original_file_id

    duplicate = client.patch(
        f"/api/archives/{june_wrong['archive_id']}",
        json={
            "business_key": may["business_key"],
            "title": may["title"],
            "region_code": "511400",
            "metadata": {
                "period": cell("2026-05", level="manual", by="ui:manual-edit"),
                "period_start": cell("2026-05", level="manual", by="ui:manual-edit"),
                "period_raw": cell("2026-05", level="manual", by="ui:manual-edit"),
                "coverage_region_code": cell("511400", level="manual", by="ui:manual-edit"),
            },
            "field_sources": field_sources(
                "business_key",
                "title",
                "region_code",
                "publish_date",
                level="manual",
                by="ui:manual-edit",
            ),
        },
    )
    assert duplicate.status_code == 409
    assert "ARCHIVE_BUSINESS_KEY_EXISTS" in duplicate.text


def test_manual_archive_delete_removes_archive_but_keeps_file_reuploadable():
    client, _ = build_client()
    source = create_cost_info_manual_source(client)
    archive = create_cost_info_manual_archive(
        client,
        source,
        period="2026-06",
        title="2026年第6期眉山市建设工程造价信息.pdf",
        content=b"%PDF meishan delete",
    )
    detail = client.get(f"/api/archives/{archive['archive_id']}").json()
    file_id = detail["files"][0]["file_id"]

    deleted = client.delete(f"/api/archives/{archive['archive_id']}")

    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert client.get(f"/api/archives/{archive['archive_id']}").status_code == 404
    listed = client.get("/api/archives", params={"domain_type": "cost_info", "channel_type": "manual_upload"})
    assert listed.status_code == 200
    assert all(row["archive_id"] != archive["archive_id"] for row in listed.json())
    download = client.get(f"/api/file-assets/{file_id}/download")
    assert download.status_code == 200
    assert download.content == b"%PDF meishan delete"

    reuploaded = create_cost_info_manual_archive(
        client,
        source,
        period="2026-06",
        title="2026年第6期眉山市建设工程造价信息.pdf",
        content=b"%PDF meishan delete",
    )
    assert reuploaded["business_key"] == archive["business_key"]
    assert reuploaded["archive_id"] != archive["archive_id"]


def test_manual_upload_ingest_can_disable_zip_processing():
    client, _ = build_client()
    ingest = client.post(
        "/api/file-assets/ingest",
        data={
            "tenant_code": "platform_public",
            "source_type": "info_price",
            "batch_id": "manual-zip-no-processing",
            "source_item_key": "manual:511400:2026-06:眉山信息价.zip",
            "derive_tasks": "false",
        },
        files={"file": ("眉山信息价.zip", b"PK\x03\x04opaque", "application/zip")},
    )

    assert ingest.status_code == 200
    ingest_body = ingest.json()
    assert ingest_body["processing_ids"] == []
    processing = client.get("/api/file-processing", params={"file_id": ingest_body["file_id"]})
    assert processing.status_code == 200
    assert processing.json() == []
