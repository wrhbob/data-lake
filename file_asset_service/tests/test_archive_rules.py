import pytest

from app.archive_rules import (
    ARCHIVE_EVENT_TYPES,
    ARCHIVE_STATUSES,
    C_CLASS_ARCHIVE_FIELDS,
    LAYER0_ALLOWED_SOURCE_LEVELS,
    SOURCE_LEVELS,
    build_cost_info_business_key,
    build_policy_regulation_business_key,
    build_quota_business_key,
    build_standard_atlas_business_key,
    build_trading_business_key,
    infer_archive_file_role,
    metadata_cell,
    metadata_value,
    normalize_source_url,
    validate_archive_provenance,
    validate_archive_file_role,
)


def test_archive_statuses_are_frozen():
    assert ARCHIVE_STATUSES == {
        "discovered",
        "collecting",
        "collect_failed",
        "quarantined",
        "collected",
        "pending_tag",
        "archived",
        "ready_for_governance",
    }


def test_archive_event_types_are_frozen():
    assert "ARCHIVE_READY_FOR_GOVERNANCE" in ARCHIVE_EVENT_TYPES
    assert "PROJECT_AGGREGATED" not in ARCHIVE_EVENT_TYPES


def test_trading_business_key_does_not_use_project_code():
    key = build_trading_business_key(
        source_id="src",
        source_item_key="notice-001",
        title="《ＡＢＣ 学校（施工） 招标公告》",
        publish_date="2026-06-20",
        notice_type_raw="招标公告",
    )

    assert key == "trading:src:notice-001"


def test_cost_info_business_key_uses_region_period_and_normalized_title():
    key = build_cost_info_business_key(
        source_id="source-1",
        region_code="650000",
        period="2026-06",
        title=" ２０２６年６月　建設工程綜合價格信息（烏魯木齊） ",
    )

    assert key == "cost_info:source-1:650000:2026-06:2026年6月 建设工程综合价格信息 乌鲁木齐"


def test_normalize_source_url_removes_fragment_sorts_query_and_drops_tracking_params():
    normalized = normalize_source_url(
        "HTTPS://Example.Gov.CN/price/list?utm_source=wechat&page=2&b=2&a=1&spm=abc#section"
    )

    assert normalized == "https://example.gov.cn/price/list?a=1&b=2&page=2"


def test_priced_source_is_extension_based():
    assert infer_archive_file_role(file_name="清单.cjz", is_primary=False) == "priced_source"

    with pytest.raises(ValueError, match="PRICED_SOURCE_ROLE_MISMATCH"):
        validate_archive_file_role(file_name="公告.pdf", requested_role="priced_source", is_primary=False)


def test_cdz_container_needs_source_qingdan_mark_for_qingdan_role():
    assert infer_archive_file_role(file_name="招标文件.CDZ", domain_type="trading", is_primary=False) == "zip_package"
    assert (
        infer_archive_file_role(
            file_name="招标文件.CDZ",
            domain_type="trading",
            source_item_role="qingdan_package",
            is_primary=False,
        )
        == "qingdan_package"
    )


def test_trading_file_roles_are_mechanical_and_opaque():
    assert (
        infer_archive_file_role(
            file_name="招标文件.CDZ",
            domain_type="trading",
            source_item_role="qingdan_package",
        )
        == "qingdan_package"
    )
    assert (
        infer_archive_file_role(
            file_name="工程量清单.zip",
            domain_type="trading",
            source_item_role="control_price_qingdan",
        )
        == "qingdan_package"
    )
    assert infer_archive_file_role(file_name="控制价.cjz", domain_type="trading") == "priced_source"
    assert (
        infer_archive_file_role(
            file_name="东升项目施工图.zip",
            domain_type="trading",
            source_item_role="工程图",
        )
        == "drawing"
    )
    assert infer_archive_file_role(file_name="地勘报告.pdf", domain_type="trading") == "geological"
    assert infer_archive_file_role(file_name="招标文件正文.pdf", domain_type="trading") == "tender_doc"
    assert infer_archive_file_role(file_name="未知资料.zip", domain_type="trading") == "zip_package"


@pytest.mark.parametrize("file_name", ["未知资料.zip", "未知资料.rar", "未知资料.7z", "未知资料.CDZ"])
def test_trading_unknown_opaque_containers_fallback_to_zip_package(file_name):
    assert infer_archive_file_role(file_name=file_name, domain_type="trading") == "zip_package"


def test_trading_explicit_roles_are_supported_without_parsing():
    assert (
        validate_archive_file_role(
            file_name="招标文件.CDZ",
            requested_role="qingdan_package",
            domain_type="trading",
        )
        == "qingdan_package"
    )
    assert (
        validate_archive_file_role(
            file_name="图纸.pdf",
            requested_role="drawing",
            domain_type="trading",
        )
        == "drawing"
    )
    assert (
        validate_archive_file_role(
            file_name="地勘报告.pdf",
            requested_role="geological",
            domain_type="trading",
        )
        == "geological"
    )
    assert (
        validate_archive_file_role(
            file_name="招标文件正文.pdf",
            requested_role="tender_doc",
            domain_type="trading",
        )
        == "tender_doc"
    )


def test_source_levels_are_frozen_for_layer0():
    assert SOURCE_LEVELS == {"crawler", "filename", "manual", "extracted"}
    assert LAYER0_ALLOWED_SOURCE_LEVELS == {"crawler", "filename", "manual"}


def test_metadata_cell_stores_value_and_source():
    cell = metadata_cell("川建价发〔2026〕12号", source_level="manual", tagged_by="user:ops")

    assert cell["value"] == "川建价发〔2026〕12号"
    assert cell["source_level"] == "manual"
    assert cell["tagged_by"] == "user:ops"
    assert "tagged_at" in cell
    assert metadata_value(cell) == "川建价发〔2026〕12号"
    assert metadata_value("legacy raw") == "legacy raw"


def test_metadata_cell_rejects_extracted_source_level():
    with pytest.raises(ValueError, match="METADATA_EXTRACTION_FORBIDDEN"):
        metadata_cell("OCR text", source_level="extracted", tagged_by="ocr")


def test_archive_provenance_rejects_extracted_metadata():
    with pytest.raises(ValueError, match="METADATA_EXTRACTION_FORBIDDEN"):
        validate_archive_provenance(
            archive_fields={
                "domain_type": "policy_regulation",
                "channel_type": "crawler",
                "business_key": "policy_regulation:src:doc",
                "title": "政策文件",
                "region_code": None,
                "publish_date": None,
            },
            metadata={
                "document_no_raw": {
                    "value": "川建价发〔2026〕12号",
                    "source_level": "extracted",
                    "tagged_by": "ocr",
                    "tagged_at": "2026-06-20T10:00:00+08:00",
                }
            },
            field_sources={
                "domain_type": {
                    "source_level": "crawler",
                    "tagged_by": "crawler",
                    "tagged_at": "2026-06-20T10:00:00+08:00",
                },
                "channel_type": {
                    "source_level": "crawler",
                    "tagged_by": "crawler",
                    "tagged_at": "2026-06-20T10:00:00+08:00",
                },
                "business_key": {
                    "source_level": "crawler",
                    "tagged_by": "crawler",
                    "tagged_at": "2026-06-20T10:00:00+08:00",
                },
                "title": {
                    "source_level": "crawler",
                    "tagged_by": "crawler",
                    "tagged_at": "2026-06-20T10:00:00+08:00",
                },
            },
        )


def test_archive_provenance_rejects_extracted_field_source():
    with pytest.raises(ValueError, match="FIELD_SOURCE_EXTRACTION_FORBIDDEN"):
        validate_archive_provenance(
            archive_fields={
                "domain_type": "quota",
                "channel_type": "manual_upload",
                "business_key": "quota:sichuan:JZZS:2020:2020-01-01",
                "title": "四川2020定额库",
                "region_code": "510000",
                "publish_date": None,
            },
            metadata={},
            field_sources={
                "domain_type": {
                    "source_level": "manual",
                    "tagged_by": "user:ops",
                    "tagged_at": "2026-06-20T10:00:00+08:00",
                },
                "channel_type": {
                    "source_level": "manual",
                    "tagged_by": "user:ops",
                    "tagged_at": "2026-06-20T10:00:00+08:00",
                },
                "business_key": {
                    "source_level": "manual",
                    "tagged_by": "user:ops",
                    "tagged_at": "2026-06-20T10:00:00+08:00",
                },
                "title": {
                    "source_level": "manual",
                    "tagged_by": "user:ops",
                    "tagged_at": "2026-06-20T10:00:00+08:00",
                },
                "region_code": {
                    "source_level": "extracted",
                    "tagged_by": "ocr",
                    "tagged_at": "2026-06-20T10:00:00+08:00",
                },
            },
        )


def test_archive_provenance_requires_sources_for_non_empty_c_class_fields():
    assert C_CLASS_ARCHIVE_FIELDS == {
        "title",
        "business_key",
        "domain_type",
        "channel_type",
        "region_code",
        "publish_date",
    }

    with pytest.raises(ValueError, match="FIELD_SOURCE_MISSING: title"):
        validate_archive_provenance(
            archive_fields={
                "domain_type": "quota",
                "channel_type": "batch_import",
                "business_key": "quota:src:file",
                "title": "四川2020定额库",
                "region_code": None,
                "publish_date": None,
            },
            metadata={},
            field_sources={
                "domain_type": {
                    "source_level": "filename",
                    "tagged_by": "rule",
                    "tagged_at": "2026-06-20T10:00:00+08:00",
                },
                "channel_type": {
                    "source_level": "filename",
                    "tagged_by": "rule",
                    "tagged_at": "2026-06-20T10:00:00+08:00",
                },
                "business_key": {
                    "source_level": "filename",
                    "tagged_by": "rule",
                    "tagged_at": "2026-06-20T10:00:00+08:00",
                },
            },
        )


def test_archive_provenance_rejects_unsupported_source_level():
    with pytest.raises(ValueError, match="UNSUPPORTED_SOURCE_LEVEL: ai"):
        validate_archive_provenance(
            archive_fields={
                "domain_type": "quota",
                "channel_type": "manual_upload",
                "business_key": "quota:src:file",
                "title": "",
                "region_code": None,
                "publish_date": None,
            },
            metadata={
                "quota_code_raw": {
                    "value": "JZZS",
                    "source_level": "ai",
                    "tagged_by": "user:ops",
                    "tagged_at": "2026-06-20T10:00:00+08:00",
                }
            },
            field_sources={
                "domain_type": {
                    "source_level": "manual",
                    "tagged_by": "user:ops",
                    "tagged_at": "2026-06-20T10:00:00+08:00",
                },
                "channel_type": {
                    "source_level": "manual",
                    "tagged_by": "user:ops",
                    "tagged_at": "2026-06-20T10:00:00+08:00",
                },
                "business_key": {
                    "source_level": "manual",
                    "tagged_by": "user:ops",
                    "tagged_at": "2026-06-20T10:00:00+08:00",
                },
            },
        )


def test_archive_provenance_rejects_invalid_metadata_cells():
    base_fields = {
        "domain_type": "quota",
        "channel_type": "manual_upload",
        "business_key": "quota:src:file",
        "title": "",
        "region_code": None,
        "publish_date": None,
    }
    base_sources = {
        "domain_type": {"source_level": "manual", "tagged_by": "user:ops", "tagged_at": "2026-06-20T10:00:00+08:00"},
        "channel_type": {"source_level": "manual", "tagged_by": "user:ops", "tagged_at": "2026-06-20T10:00:00+08:00"},
        "business_key": {"source_level": "manual", "tagged_by": "user:ops", "tagged_at": "2026-06-20T10:00:00+08:00"},
    }

    with pytest.raises(ValueError, match="METADATA_VALUE_REQUIRED: quota_code_raw"):
        validate_archive_provenance(
            archive_fields=base_fields,
            metadata={
                "quota_code_raw": {
                    "source_level": "manual",
                    "tagged_by": "user:ops",
                    "tagged_at": "2026-06-20T10:00:00+08:00",
                }
            },
            field_sources=base_sources,
        )

    with pytest.raises(ValueError, match="METADATA_CELL_REQUIRED: quota_code_raw"):
        validate_archive_provenance(
            archive_fields=base_fields,
            metadata={"quota_code_raw": "JZZS"},
            field_sources=base_sources,
        )

    with pytest.raises(ValueError, match="PROJECT_GROUP_KEY_FORBIDDEN"):
        validate_archive_provenance(
            archive_fields=base_fields,
            metadata={
                "project_group_key": {
                    "value": "legacy-project",
                    "source_level": "manual",
                    "tagged_by": "user:ops",
                    "tagged_at": "2026-06-20T10:00:00+08:00",
                }
            },
            field_sources=base_sources,
        )


def test_archive_provenance_rejects_metadata_with_metadata_specific_codes():
    base_fields = {
        "domain_type": "quota",
        "channel_type": "manual_upload",
        "business_key": "quota:src:file",
        "title": "",
        "region_code": None,
        "publish_date": None,
    }
    base_sources = {
        "domain_type": {"source_level": "manual", "tagged_by": "user:ops", "tagged_at": "2026-06-20T10:00:00+08:00"},
        "channel_type": {"source_level": "manual", "tagged_by": "user:ops", "tagged_at": "2026-06-20T10:00:00+08:00"},
        "business_key": {"source_level": "manual", "tagged_by": "user:ops", "tagged_at": "2026-06-20T10:00:00+08:00"},
    }

    with pytest.raises(ValueError, match="METADATA_INVALID"):
        validate_archive_provenance(
            archive_fields=base_fields,
            metadata="not-object",
            field_sources=base_sources,
        )

    with pytest.raises(ValueError, match="METADATA_TAGGED_BY_REQUIRED: quota_code_raw"):
        validate_archive_provenance(
            archive_fields=base_fields,
            metadata={
                "quota_code_raw": {
                    "value": "JZZS",
                    "source_level": "manual",
                    "tagged_at": "2026-06-20T10:00:00+08:00",
                }
            },
            field_sources=base_sources,
        )

    with pytest.raises(ValueError, match="METADATA_TAGGED_AT_REQUIRED: quota_code_raw"):
        validate_archive_provenance(
            archive_fields=base_fields,
            metadata={
                "quota_code_raw": {
                    "value": "JZZS",
                    "source_level": "manual",
                    "tagged_by": "user:ops",
                }
            },
            field_sources=base_sources,
        )


def test_archive_provenance_rejects_invalid_field_sources():
    archive_fields = {
        "domain_type": "quota",
        "channel_type": "manual_upload",
        "business_key": "quota:src:file",
        "title": "",
        "region_code": None,
        "publish_date": None,
    }

    with pytest.raises(ValueError, match="FIELD_SOURCE_INVALID: domain_type"):
        validate_archive_provenance(
            archive_fields=archive_fields,
            metadata={},
            field_sources={"domain_type": "manual"},
        )

    with pytest.raises(ValueError, match="FIELD_SOURCES_INVALID"):
        validate_archive_provenance(
            archive_fields=archive_fields,
            metadata={},
            field_sources="not-object",
        )

    with pytest.raises(ValueError, match="FIELD_SOURCE_TAGGED_BY_REQUIRED: domain_type"):
        validate_archive_provenance(
            archive_fields=archive_fields,
            metadata={},
            field_sources={
                "domain_type": {
                    "source_level": "manual",
                    "tagged_at": "2026-06-20T10:00:00+08:00",
                }
            },
        )

    with pytest.raises(ValueError, match="FIELD_SOURCE_TAGGED_AT_REQUIRED: domain_type"):
        validate_archive_provenance(
            archive_fields=archive_fields,
            metadata={},
            field_sources={
                "domain_type": {
                    "source_level": "manual",
                    "tagged_by": "user:ops",
                }
            },
        )


def test_quota_business_key_prefers_quota_code():
    assert (
        build_quota_business_key(
            source_id="src",
            title="四川省建设工程定额",
            region="sichuan",
            quota_code="JZZS",
            version="2020",
            effective_date="2020-01-01",
            standard_no=None,
        )
        == "quota:sichuan:JZZS:2020:2020-01-01"
    )


def test_policy_business_key_prefers_document_no():
    assert (
        build_policy_regulation_business_key(
            source_id="src",
            title="四川省造价政策",
            issuing_authority="川建价发",
            document_no="〔2026〕12号",
            publish_date="2026-03-01",
        )
        == "policy:〔2026〕12号"
    )


def test_standard_atlas_business_key_prefers_standard_no():
    assert (
        build_standard_atlas_business_key(
            source_id="src",
            title="屋面工程技术规范",
            standard_no="GB50345",
            version="2012",
            publish_date="2012-01-01",
        )
        == "standard_atlas:GB50345:2012"
    )


def test_quota_bill_standard_business_key_uses_region_for_province_standard():
    assert (
        build_quota_business_key(
            source_id="src",
            title="四川省清单计价规范",
            region="sichuan",
            quota_code=None,
            version="2013",
            effective_date=None,
            standard_no="DB51/T50500",
        )
        == "quota:sichuan:DB51/T50500:2013"
    )


def test_quota_file_roles_are_mechanical():
    assert infer_archive_file_role(file_name="四川2020定额库.xlsx", domain_type="quota") == "quota_db"
    assert infer_archive_file_role(file_name="GB50500-2013清单计价规范.pdf", domain_type="quota") == "bill_standard"
    assert infer_archive_file_role(file_name="补充定额.pdf", domain_type="quota") == "quota_supplement"
    assert infer_archive_file_role(file_name="定额解释.docx", domain_type="quota") == "quota_interpretation"
    assert infer_archive_file_role(file_name="普通附件.xlsx", domain_type="quota") == "attachment"


def test_policy_file_roles_are_mechanical():
    assert (
        infer_archive_file_role(file_name="川建价发〔2026〕12号.pdf", domain_type="policy_regulation", is_primary=True)
        == "policy_document"
    )
    assert infer_archive_file_role(file_name="费率表附件.xlsx", domain_type="policy_regulation") == "policy_attachment"


def test_standard_atlas_file_roles_are_mechanical():
    assert infer_archive_file_role(file_name="23J909工程做法.pdf", domain_type="standard_atlas") == "atlas_document"
    assert infer_archive_file_role(file_name="GB50345屋面工程技术规范.pdf", domain_type="standard_atlas") == "standard_document"
    assert infer_archive_file_role(file_name="构造大样扫描图.png", domain_type="standard_atlas") == "scan_image"


def test_web_snapshot_role_preserves_source_item_role_behavior():
    assert infer_archive_file_role(file_name="snapshot.bin", source_item_role="web_page_snapshot") == "web_snapshot"
    assert infer_archive_file_role(file_name="snapshot.html") == "web_snapshot"


def test_cover_role_inferred_for_named_cover_image():
    assert infer_archive_file_role(file_name="封面.jpg") == "cover"
    assert infer_archive_file_role(file_name="眉山2025第5期封面.png") == "cover"
    # 封面 takes precedence over the primary/main_document fallback
    assert infer_archive_file_role(file_name="封面.jpg", is_primary=True) == "cover"
    # generic image without 封面 keyword falls through to attachment (not primary)
    assert infer_archive_file_role(file_name="random-image.jpg") == "attachment"
    # standard_atlas scan image is not stolen by the cover rule
    assert infer_archive_file_role(file_name="构造大样扫描图.png", domain_type="standard_atlas") == "scan_image"
