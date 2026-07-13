from pathlib import Path

from app.national_cost_info_regions import load_national_regions
from app.national_info_price_audit import (
    SOURCE_STATUSES,
    build_audit_summary,
    build_blocked_rows,
    build_crawl_queue_rows,
    load_source_audit_rows,
    validate_source_audit,
)


REGION_HEADER = "admin_division_version,province_code,province_name,target_region_code,target_region_name,target_level,is_required,source_note\n"
AUDIT_HEADER = (
    "admin_division_version,province_code,province_name,target_region_code,target_region_name,target_level,"
    "source_status,site_id,source_name,publisher_name,publisher_scope,entry_url,evidence_url,"
    "source_attachment_mode,publication_mode,adapter_kind,crawl_pattern,price_kind,period_kind,"
    "blocked_reason,manual_path,audit_note,review_status\n"
)


def write_regions(path: Path) -> None:
    path.write_text(
        REGION_HEADER
        + "mca_geoname_2026_snapshot,510000,四川省,510100,成都市,prefecture,1,地级市\n"
        + "mca_geoname_2026_snapshot,510000,四川省,510300,自贡市,prefecture,1,地级市\n"
        + "mca_geoname_2026_snapshot,110000,北京市,110000,北京市,municipality,1,直辖市\n",
        encoding="utf-8",
    )


def write_audit(path: Path, body: str) -> None:
    path.write_text(AUDIT_HEADER + body, encoding="utf-8")


def test_source_status_set_is_closed():
    assert SOURCE_STATUSES == {
        "auto_crawl_ready",
        "coverage_declaration",
        "source_blocked",
        "pending_verify",
        "missing_official_source",
    }


def test_validate_source_audit_reports_missing_required_target(tmp_path):
    region_path = tmp_path / "regions.csv"
    audit_path = tmp_path / "audit.csv"
    write_regions(region_path)
    write_audit(
        audit_path,
        "mca_geoname_2026_snapshot,510000,四川省,510100,成都市,prefecture,"
        "source_blocked,cost_info.sc.chengdu.manual,成都市信息价,成都市造价站,city,"
        "https://example.com/chengdu,https://example.com/chengdu,"
        "pdf_only,MANUAL_ONLY,,manual,unspecified,monthly,需会员,017/manual_upload,官方源受限,accepted\n",
    )

    regions = load_national_regions(region_path)
    rows = load_source_audit_rows(audit_path)

    result = validate_source_audit(rows, regions)

    assert result.missing_target_region_codes == ["110000", "510300"]
    assert result.invalid_rows == []
    assert result.is_complete is False


def test_validate_source_audit_requires_fields_by_status(tmp_path):
    region_path = tmp_path / "regions.csv"
    audit_path = tmp_path / "audit.csv"
    write_regions(region_path)
    write_audit(
        audit_path,
        "mca_geoname_2026_snapshot,510000,四川省,510100,成都市,prefecture,"
        "auto_crawl_ready,,成都市信息价,成都市造价站,city,"
        ",https://example.com/chengdu,pdf_only,DIRECT_PDF,,,guidance,monthly,,,缺 site 和入口,accepted\n",
    )

    result = validate_source_audit(load_source_audit_rows(audit_path), load_national_regions(region_path))

    assert result.invalid_rows == [
        {
            "line_number": 2,
            "target_region_code": "510100",
            "errors": [
                "AUTO_CRAWL_READY_SITE_ID_REQUIRED",
                "AUTO_CRAWL_READY_ENTRY_URL_REQUIRED",
                "AUTO_CRAWL_READY_ADAPTER_OR_PATTERN_REQUIRED",
            ],
        }
    ]


def test_build_audit_summary_queue_and_blocked_rows(tmp_path):
    region_path = tmp_path / "regions.csv"
    audit_path = tmp_path / "audit.csv"
    write_regions(region_path)
    write_audit(
        audit_path,
        "mca_geoname_2026_snapshot,510000,四川省,510100,成都市,prefecture,"
        "source_blocked,cost_info.sc.chengdu.manual,成都市信息价,成都市造价站,city,"
        "https://example.com/chengdu,https://example.com/chengdu,"
        "pdf_only,MANUAL_ONLY,,manual,unspecified,monthly,需会员,017/manual_upload,官方源受限,accepted\n"
        "mca_geoname_2026_snapshot,510000,四川省,510300,自贡市,prefecture,"
        "auto_crawl_ready,cost_info.sc.zigong,自贡市信息价,自贡市住建局,city,"
        "https://example.com/zigong,https://example.com/zigong,"
        "zip_package,DIRECT_WEB,sichuan_pdf,li,guidance,monthly,,,公开附件,accepted\n"
        "mca_geoname_2026_snapshot,110000,北京市,110000,北京市,municipality,"
        "coverage_declaration,cost_info.bj.zjw.main,北京市工程造价信息,北京市住建委,municipality,"
        "https://example.com/beijing,https://example.com/beijing,"
        "html_table,DIRECT_WEB,,html_table,guidance,monthly,,,在线表只声明覆盖,accepted\n",
    )

    rows = load_source_audit_rows(audit_path)
    regions = load_national_regions(region_path)

    assert build_audit_summary(rows, regions)["by_source_status"] == {
        "auto_crawl_ready": 1,
        "coverage_declaration": 1,
        "source_blocked": 1,
    }
    assert build_crawl_queue_rows(rows) == [
        {
            "province_code": "510000",
            "province_name": "四川省",
            "target_region_code": "510300",
            "target_region_name": "自贡市",
            "site_id": "cost_info.sc.zigong",
            "source_name": "自贡市信息价",
            "adapter_kind": "sichuan_pdf",
            "crawl_pattern": "li",
            "entry_url": "https://example.com/zigong",
            "review_status": "accepted",
        }
    ]
    assert build_blocked_rows(rows)[0]["manual_path"] == "017/manual_upload"
