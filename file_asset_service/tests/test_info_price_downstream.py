from test_api import build_client
from test_info_price_schema import minimal_xlsx


def parsed_info_price_processing_id(client) -> str:
    ingest = client.post(
        "/api/file-assets/ingest",
        data={
            "tenant_code": "tenant_downstream",
            "source_type": "info_price_governance",
            "batch_id": "batch-downstream",
            "source_item_key": "downstream-smoke",
        },
        files={
            "file": (
                "下游信息价.xlsx",
                minimal_xlsx(
                    [
                        ["下游信息价样本"],
                        ["序号", "材料名称及规格型号", "单位", "除税综合\n信息价", "含税综合\n信息价"],
                        [1, "低碳热轧盘条（高线）HPB300 Φ6", "t", 3200.99, 3609.12],
                        [2, "螺纹钢 HRB400E Φ12", "t", 4000, 452],
                        [3, "未匹配新材料 X-100", "件", 100, 113],
                    ]
                ),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    ).json()
    processing = client.get("/api/file-processing", params={"file_id": ingest["file_id"]}).json()
    processing_id = next(item["processing_id"] for item in processing if item["processor"] == "info_price_parse")
    client.post(f"/api/file-processing/{processing_id}/run")
    return processing_id


def test_info_price_extracts_api_lists_parsed_outputs():
    client, _ = build_client()
    processing_id = parsed_info_price_processing_id(client)

    response = client.get("/api/info-price/extracts")

    assert response.status_code == 200
    extracts = response.json()
    current = next(item for item in extracts if item["processing_id"] == processing_id)
    assert current["schema"] == "info_price_extract.v1"
    assert current["row_count"] == 3
    assert current["file_name"] == "下游信息价.xlsx"
    assert current["next_step"] == "review"


def test_review_api_derives_manual_work_without_reviewing_every_row():
    client, _ = build_client()
    processing_id = parsed_info_price_processing_id(client)

    response = client.get(f"/api/info-price/extracts/{processing_id}/review")

    assert response.status_code == 200
    review = response.json()
    assert review["role"] == "reviewer"
    assert review["total_rows"] == 3
    assert review["manual_count"] > 0
    assert review["auto_confirmed_count"] == review["total_rows"] - review["manual_count"]
    assert review["scope_note"] == "只处理待人工项，不逐行看"
    assert review["quality_gate"]["primary_action"] == "确认并提交审核"
    assert review["quality_gate"]["secondary_action"] == "打回重解析"
    assert review["pending_material_matches"][0]["actions"] == ["确认", "换", "登记新品种"]
    assert review["low_confidence_items"][0]["source_location"].startswith("Sheet1")
    assert review["low_confidence_items"][0]["actions"] == ["采纳", "改"]


def test_audit_api_is_quality_gate_without_edit_actions():
    client, _ = build_client()
    processing_id = parsed_info_price_processing_id(client)

    response = client.get(f"/api/info-price/extracts/{processing_id}/audit")

    assert response.status_code == 200
    audit = response.json()
    assert audit["role"] == "auditor"
    assert audit["can_edit_data"] is False
    assert audit["editable_actions"] == []
    assert audit["can_release"] is False
    assert audit["previous_period_compare"]["status"] == "missing_baseline"
    assert audit["previous_period_compare"]["message"] == "缺上期基准，不能完成正式审核"
    assert "放行即签名" in audit["release_signature"]["warning"]
    assert len(audit["sample_rows"]) <= 5
    assert audit["return_to_review"]["requires_row_and_reason"] is True


def test_review_and_audit_are_separate_pages_with_distinct_roles():
    client, _ = build_client()

    review = client.get("/review")
    audit = client.get("/audit")

    assert review.status_code == 200
    assert audit.status_code == 200
    assert 'data-page="review"' in review.text
    assert "解析复核台" in review.text
    assert "复核是加工" in review.text
    assert "登记新品种" in review.text
    assert 'data-page="audit"' in audit.text
    assert "审核台" in audit.text
    assert "审核是质检" in audit.text
    assert "缺上期基准，不能完成正式审核" in audit.text
    assert "放行即签名" in audit.text
    assert "登记新品种" not in audit.text
    assert 'data-review-edit-action' not in audit.text
