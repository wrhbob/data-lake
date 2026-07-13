from __future__ import annotations

import csv
import json
from datetime import UTC, datetime

from app.info_price_acceptance import (
    FIRST_WAVE_PROVINCES,
    build_archive_list_rows,
    build_source_audit_rows,
    export_info_price_acceptance_package,
)
from app.models import Archive, ArchiveFile, CollectionTask, DataSource, FileAsset, FileProcessing


def cell(value, source_level="crawler"):
    return {
        "value": value,
        "source_level": source_level,
        "tagged_by": "acceptance-test",
        "tagged_at": "2026-06-24T00:00:00+08:00",
    }


def add_cost_info_source(db_session, *, code="510100", name="成都信息价公开源", status="active"):
    source = DataSource(
        source_scope="platform_public",
        tenant_code=None,
        asset_tenant_code="platform_public",
        managed_by="platform",
        source_type="info_price",
        connector_type="http_site",
        name=name,
        province="四川省",
        city="成都市",
        region_code=code,
        data_domain="cost_info",
        status=status,
        downloadable=True,
        config={
            "stable": {
                "domain_type": "cost_info",
                "publisher_region_code": "510000",
                "coverage_region_code": code,
                "publisher_name": "成都市建设工程造价站",
                "publisher_scope": "city",
                "acquisition": "auto_crawl",
                "source_attachment_mode": "pdf_only",
                "parsability": "text_pdf",
                "entry_url": "https://example.gov.cn/cost-info",
            },
            "active_parser_version": "acceptance.test.v1",
            "coverage_expectation": {
                "target_regions": [
                    {
                        "region_code": code,
                        "region_name": "成都市",
                        "target_level": "city",
                        "requires_city_source": True,
                        "declared_periods": ["2026-05"],
                    }
                ]
            },
        },
    )
    db_session.add(source)
    db_session.commit()
    return source


def add_archive_with_file(db_session, source):
    archive = Archive(
        domain_type="cost_info",
        channel_type="crawler",
        collection_method="auto",
        business_key=f"cost_info:{source.source_id}:510100:2026-05:成都市2026年5月信息价",
        title="成都市2026年5月信息价",
        region_code="510100",
        source_id=source.source_id,
        source_url="https://example.gov.cn/cost-info/2026-05.pdf",
        tenant_code="platform_public",
        visibility_scope="public",
        status="archived",
        metadata_payload={
            "period": cell("2026-05"),
            "coverage_region_code": cell("510100"),
            "tax_type": cell(None),
            "price_source_type": cell("info_price"),
        },
        field_sources={
            key: {"source_level": "crawler", "tagged_by": "acceptance-test", "tagged_at": "2026-06-24T00:00:00+08:00"}
            for key in ("domain_type", "channel_type", "business_key", "title", "region_code")
        },
    )
    db_session.add(archive)
    db_session.flush()
    asset = FileAsset(
        tenant_code="platform_public",
        bucket="cost-raw",
        object_key="objects/aa/bb/test.pdf",
        sha256="a" * 64,
        file_name="成都市2026年5月信息价.pdf",
        file_ext=".pdf",
        mime_type="application/pdf",
        file_size=12,
    )
    db_session.add(asset)
    db_session.flush()
    db_session.add(
        ArchiveFile(
            archive_id=archive.archive_id,
            file_id=asset.file_id,
            file_role="main_document",
            is_primary=True,
            display_name=asset.file_name,
        )
    )
    db_session.commit()
    return archive, asset


def add_trading_archive(db_session, source_id):
    archive = Archive(
        domain_type="trading",
        channel_type="crawler",
        collection_method="auto",
        business_key=f"trading:{source_id}:notice-001",
        title="交易公告不应进入信息价验收包",
        source_id=source_id,
        tenant_code="platform_public",
        visibility_scope="public",
        status="archived",
        metadata_payload={},
        field_sources={
            key: {"source_level": "crawler", "tagged_by": "acceptance-test", "tagged_at": "2026-06-24T00:00:00+08:00"}
            for key in ("domain_type", "channel_type", "business_key", "title")
        },
    )
    db_session.add(archive)
    db_session.commit()
    return archive


def test_source_audit_rows_include_first_wave_missing_provinces(db_session):
    source = add_cost_info_source(db_session)

    rows = build_source_audit_rows(db_session, province_codes=["510000", "420000"])

    sichuan = next(row for row in rows if row["province_code"] == "510000")
    hubei = next(row for row in rows if row["province_code"] == "420000")
    assert sichuan["source_id"] == source.source_id
    assert sichuan["audit_status"] == "auto_crawl_ready"
    assert sichuan["manual_path"] is None
    assert hubei["audit_status"] == "missing_source_audit"
    assert hubei["remark"] == "首批省份尚无 source registry 记录，验收不可判定为完成。"


def test_archive_list_rows_are_scoped_to_cost_info_first_wave(db_session):
    source = add_cost_info_source(db_session)
    archive, _asset = add_archive_with_file(db_session, source)
    add_trading_archive(db_session, source.source_id)

    rows = build_archive_list_rows(db_session, province_codes=["510000"])

    assert len(rows) == 1
    assert rows[0]["archive_id"] == archive.archive_id
    assert rows[0]["period"] == "2026-05"
    assert rows[0]["file_count"] == 1
    assert rows[0]["primary_file_name"] == "成都市2026年5月信息价.pdf"


def test_export_info_price_acceptance_package_writes_fixed_files(db_session, tmp_path):
    source = add_cost_info_source(db_session)
    _archive, asset = add_archive_with_file(db_session, source)
    db_session.add(
        CollectionTask(
            source_id=source.source_id,
            asset_tenant_code="platform_public",
            operator_type="platform_ops",
            task_type="crawl",
            trigger_type="manual",
            batch_id="acceptance-batch-001",
            data_domain="cost_info",
            status="done",
        )
    )
    db_session.add(FileProcessing(file_id=asset.file_id, processor="should-not-run-for-layer0", status="pending"))
    db_session.commit()

    result = export_info_price_acceptance_package(
        db_session,
        tmp_path,
        start_period="2026-05",
        end_period="2026-05",
        province_codes=["510000"],
        generated_at=datetime(2026, 6, 24, tzinfo=UTC),
    )

    expected_files = {
        "first_wave_source_audit.csv",
        "first_wave_collection_run.json",
        "first_wave_coverage_matrix.csv",
        "first_wave_archive_list.csv",
        "_manifest.json",
    }
    assert {path.name for path in result.files.values()} == expected_files
    assert all(path.exists() for path in result.files.values())

    summary = json.loads((tmp_path / "first_wave_collection_run.json").read_text())
    assert summary["package_schema_version"] == "info_price_acceptance.v1"
    assert summary["scope"]["province_codes"] == ["510000"]
    assert summary["archive_counts"]["by_channel"] == {"crawler": 1}
    assert summary["task_counts"]["by_status"] == {"done": 1}
    assert summary["red_lines"]["file_processing_actual"] == 1
    assert summary["red_lines"]["file_processing_passes"] is False

    with (tmp_path / "first_wave_source_audit.csv").open(encoding="utf-8") as handle:
        source_rows = list(csv.DictReader(handle))
    assert source_rows[0]["parser_version"] == "acceptance.test.v1"

    with (tmp_path / "first_wave_coverage_matrix.csv").open(encoding="utf-8") as handle:
        coverage_rows = list(csv.DictReader(handle))
    assert coverage_rows[0]["business_coverage_status"] == "covered"
    assert coverage_rows[0]["province_name"] == "四川省"

    manifest = json.loads((tmp_path / "_manifest.json").read_text())
    assert manifest["files"]["source_audit"] == "first_wave_source_audit.csv"


def test_export_defaults_to_all_first_wave_provinces(db_session, tmp_path):
    export_info_price_acceptance_package(
        db_session,
        tmp_path,
        start_period="2026-05",
        end_period="2026-05",
        generated_at=datetime(2026, 6, 24, tzinfo=UTC),
    )

    with (tmp_path / "first_wave_source_audit.csv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == len(FIRST_WAVE_PROVINCES)
    assert {row["audit_status"] for row in rows} == {"missing_source_audit"}
