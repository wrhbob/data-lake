import json

from app.national_info_price_acceptance import export_national_info_price_package


def write_csv(path, header, rows):
    path.write_text(header + "".join(rows), encoding="utf-8")


REGION_HEADER = "admin_division_version,province_code,province_name,target_region_code,target_region_name,target_level,is_required,source_note\n"
AUDIT_HEADER = (
    "admin_division_version,province_code,province_name,target_region_code,target_region_name,target_level,"
    "source_status,site_id,source_name,publisher_name,publisher_scope,entry_url,evidence_url,"
    "source_attachment_mode,publication_mode,adapter_kind,crawl_pattern,price_kind,period_kind,"
    "blocked_reason,manual_path,audit_note,review_status\n"
)


def test_export_national_info_price_package_writes_expected_files(tmp_path):
    regions_path = tmp_path / "regions.csv"
    audit_path = tmp_path / "audit.csv"
    output_dir = tmp_path / "out"
    write_csv(
        regions_path,
        REGION_HEADER,
        [
            "mca_geoname_2026_snapshot,510000,四川省,510100,成都市,prefecture,1,地级市\n",
            "mca_geoname_2026_snapshot,510000,四川省,510300,自贡市,prefecture,1,地级市\n",
        ],
    )
    write_csv(
        audit_path,
        AUDIT_HEADER,
        [
            "mca_geoname_2026_snapshot,510000,四川省,510100,成都市,prefecture,"
            "source_blocked,cost_info.sc.chengdu.manual,成都市信息价,成都市造价站,city,"
            "https://example.com/chengdu,https://example.com/chengdu,"
            "pdf_only,MANUAL_ONLY,,manual,unspecified,monthly,需会员,017/manual_upload,官方源受限,accepted\n",
            "mca_geoname_2026_snapshot,510000,四川省,510300,自贡市,prefecture,"
            "auto_crawl_ready,cost_info.sc.zigong,自贡市信息价,自贡市住建局,city,"
            "https://example.com/zigong,https://example.com/zigong,"
            "zip_package,DIRECT_WEB,sichuan_pdf,li,guidance,monthly,,,公开附件,accepted\n",
        ],
    )

    result = export_national_info_price_package(
        regions_path=regions_path,
        audit_path=audit_path,
        output_dir=output_dir,
    )

    assert sorted(path.name for path in result.files.values()) == [
        "national_blocked_sources.csv",
        "national_crawl_queue.csv",
        "national_pattern_summary.csv",
        "national_source_audit.csv",
        "national_source_completeness.json",
    ]
    summary = json.loads((output_dir / "national_source_completeness.json").read_text(encoding="utf-8"))
    assert summary["target_count"] == 2
    assert summary["audited_target_count"] == 2
    assert summary["is_complete"] is True
    assert "cost_info.sc.zigong" in (output_dir / "national_crawl_queue.csv").read_text(encoding="utf-8")
    assert "017/manual_upload" in (output_dir / "national_blocked_sources.csv").read_text(encoding="utf-8")
