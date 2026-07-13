"""HOTFIX · /api/data-lake/quota/archives 字段名修复 + 新增详情端点测试。

回归覆盖:
- L346 archive.archive_status → archive.status 修复
- L347 getattr(archive, "biz_key") → archive.business_key 修复
- 删除 getattr(archive, "province", None)
- count_stmt 与 /stats 同源(无 filter 时一致)
- 新增 GET /archives/{archive_id} 详情端点结构
- 不存在的 archive_id 返回 404
"""
from test_api import build_client


def _compose_regional_set(client, *, jurisdiction_code="510000", edition_year="2025"):
    """通过 compose 端点创建一份四川定额体系(单分册),返回 compose 响应。"""
    payload = {
        "action": "new_set",
        "systemType": "construction_regional",
        "path": {
            "jurisdiction_code": jurisdiction_code,
            "jurisdiction_level": "province",
        },
        "set": {
            "material_type": "quota_base",
            "title": f"四川省{edition_year}建设工程计价定额",
            "edition_year": edition_year,
            "edition_label": f"{edition_year}版",
            "issuer_name": "四川省住房和城乡建设厅",
        },
        "volumes": [
            {"volume_title": "建筑工程", "discipline_code": "construction", "files": []},
        ],
        "unassignedFiles": [],
        "supplementFiles": [],
    }
    response = client.post("/api/data-lake/quota/compose", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body.get("ok") is True, body
    return body


def test_quota_archives_list_uses_correct_model_fields():
    """修复回归:GET /archives 返回 status / business_key / region_code,不再 AttributeError。"""
    client, _ = build_client()
    compose_body = _compose_regional_set(client)
    archive_id = compose_body["archives"][0]["archive_id"]

    response = client.get("/api/data-lake/quota/archives")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] >= 1
    items = body["items"]
    assert len(items) >= 1

    # 找到刚创建的那一条
    created = next((it for it in items if it["archive_id"] == archive_id), None)
    assert created is not None, "compose 创建的档案未出现在 /archives 列表中"

    # 修复点 1: status 字段(原 archive.archive_status 触发 AttributeError)
    assert created["status"] == "pending_tag"
    # 修复点 2: business_key 字段(原 getattr(archive, "biz_key") 恒为 None)
    assert created["business_key"], "business_key 不应为空"
    assert created["business_key"].startswith("quota:")
    # 修复点 3: province 字段已移除
    assert "province" not in created, "列表项不应再含 province 字段"
    # region_code 字段存在(compose 不写 Archive.region_code,所以可能为 None;
    # 但 pubset.jurisdiction_code 必须透传出来用作省份展示)
    assert "region_code" in created
    assert created["jurisdiction_code"] == "510000"
    assert created["edition_year"] == 2025


def test_quota_archives_list_total_matches_stats_without_filter():
    """口径核对:无 filter 时 /archives 的 total == /stats 的 archived。"""
    client, _ = build_client()
    _compose_regional_set(client)

    archives = client.get("/api/data-lake/quota/archives").json()
    stats = client.get("/api/data-lake/quota/stats").json()

    assert archives["total"] == stats["archived"], (
        f"口径不一致: /archives total={archives['total']}, /stats archived={stats['archived']}"
    )


def test_quota_archive_detail_returns_full_structure():
    """新增 GET /archives/{archive_id} 返回 archive + publication_set + volumes + files。"""
    client, _ = build_client()
    compose_body = _compose_regional_set(client)
    archive_id = compose_body["archives"][0]["archive_id"]

    response = client.get(f"/api/data-lake/quota/archives/{archive_id}")
    assert response.status_code == 200, response.text
    body = response.json()

    # archive 主对象
    archive = body["archive"]
    assert archive["archive_id"] == archive_id
    assert archive["status"] == "pending_tag"
    assert archive["business_key"].startswith("quota:")
    assert archive["title"].endswith("建筑工程")
    assert archive["volume_title"] == "建筑工程"
    assert archive["discipline_code"] == "construction"
    assert archive["file_count"] == 0

    # publication_set 回显
    pubset = body["publication_set"]
    assert pubset is not None
    assert pubset["jurisdiction_code"] == "510000"
    assert pubset["quota_system_type"] == "construction_regional"
    assert pubset["material_type"] == "quota_base"
    assert pubset["edition_year"] == 2025
    assert pubset["issuer_name"] == "四川省住房和城乡建设厅"

    # volumes 列表(至少 1 条,当前 archive 标记 is_current)
    volumes = body["volumes"]
    assert len(volumes) >= 1
    current = next((v for v in volumes if v["is_current"]), None)
    assert current is not None, "未标记 is_current 的分册"
    assert current["archive_id"] == archive_id

    # files 空列表(compose 不上传文件)
    assert body["files"] == []


def test_quota_archive_detail_returns_404_for_missing_id():
    """不存在的 archive_id → 404。"""
    client, _ = build_client()
    response = client.get("/api/data-lake/quota/archives/nonexistent-archive-id")
    assert response.status_code == 404
    body = response.json()
    assert "detail" in body
