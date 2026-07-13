from test_api import build_client


def test_create_platform_data_source_api():
    client, _ = build_client()

    response = client.post(
        "/api/data-sources",
        json={
            "source_scope": "platform_public",
            "managed_by": "platform",
            "source_type": "info_price",
            "connector_type": "http_site",
            "name": "广州建设工程信息价",
            "data_domain": "info_price",
            "config": {"allowed_ext": [".xlsx", ".pdf"]},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["asset_tenant_code"] == "platform_public"
    assert body["managed_by"] == "platform"
    assert body["config"] == {"allowed_ext": [".xlsx", ".pdf"]}


def test_create_tenant_external_source_and_task_api():
    client, _ = build_client()
    source = client.post(
        "/api/data-sources",
        json={
            "source_scope": "tenant_private",
            "tenant_code": "tenant_a",
            "managed_by": "tenant",
            "source_type": "info_price",
            "connector_type": "http_site",
            "name": "客户自定义信息价网站",
            "data_domain": "info_price",
        },
    ).json()

    response = client.post(
        "/api/collection-tasks",
        json={
            "source_id": source["source_id"],
            "operator_type": "tenant_user",
            "operator_id": "user_001",
            "task_type": "sync",
            "trigger_type": "manual",
            "config_override": {"parser_version": "tenant.price-list.v1", "config_digest": "sha256:test"},
        },
    )

    assert response.status_code == 200
    task = response.json()
    assert task["source_id"] == source["source_id"]
    assert task["asset_tenant_code"] == "tenant_a"
    assert task["status"] == "pending"
    assert task["batch_id"]
    assert task["config_override"]["parser_version"] == "tenant.price-list.v1"


def test_task_linked_ingest_api_records_source_and_task():
    client, _ = build_client()
    source = client.post(
        "/api/data-sources",
        json={
            "source_scope": "tenant_private",
            "tenant_code": "tenant_a",
            "managed_by": "tenant",
            "source_type": "info_price",
            "connector_type": "http_site",
            "name": "客户自定义信息价网站",
            "data_domain": "info_price",
        },
    ).json()
    task = client.post(
        "/api/collection-tasks",
        json={
            "source_id": source["source_id"],
            "operator_type": "tenant_user",
            "task_type": "sync",
            "trigger_type": "manual",
        },
    ).json()

    ingest = client.post(
        "/api/file-assets/ingest",
        data={
            "tenant_code": "tenant_a",
            "source_type": "info_price",
            "task_id": task["task_id"],
            "source_item_key": "price-2026-06",
        },
        files={"file": ("信息价.xlsx", b"xlsx bytes", "application/octet-stream")},
    )

    assert ingest.status_code == 200
    events = client.get("/api/ingest-events", params={"tenant_code": "tenant_a"}).json()
    assert events[0]["source_id"] == source["source_id"]
    assert events[0]["task_id"] == task["task_id"]
    assert events[0]["source_item_key"] == "price-2026-06"
