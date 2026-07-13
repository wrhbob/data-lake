from pathlib import Path

import pytest

from app.national_cost_info_regions import (
    NationalRegion,
    load_national_regions,
    summarize_region_targets,
    validate_region_seed,
)


def write_region_seed(path: Path, body: str) -> None:
    path.write_text(
        "admin_division_version,province_code,province_name,target_region_code,target_region_name,target_level,is_required,source_note\n"
        + body,
        encoding="utf-8",
    )


def test_load_national_regions_reads_required_targets(tmp_path):
    csv_path = tmp_path / "regions.csv"
    write_region_seed(
        csv_path,
        "mca_geoname_2026_snapshot,110000,北京市,110000,北京市,municipality,1,直辖市按市级目标处理\n"
        "mca_geoname_2026_snapshot,510000,四川省,510100,成都市,prefecture,1,省下辖地级行政单元\n"
        "mca_geoname_2026_snapshot,510000,四川省,510300,自贡市,prefecture,1,省下辖地级行政单元\n",
    )

    rows = load_national_regions(csv_path)

    assert rows == [
        NationalRegion(
            admin_division_version="mca_geoname_2026_snapshot",
            province_code="110000",
            province_name="北京市",
            target_region_code="110000",
            target_region_name="北京市",
            target_level="municipality",
            is_required=True,
            source_note="直辖市按市级目标处理",
        ),
        NationalRegion(
            admin_division_version="mca_geoname_2026_snapshot",
            province_code="510000",
            province_name="四川省",
            target_region_code="510100",
            target_region_name="成都市",
            target_level="prefecture",
            is_required=True,
            source_note="省下辖地级行政单元",
        ),
        NationalRegion(
            admin_division_version="mca_geoname_2026_snapshot",
            province_code="510000",
            province_name="四川省",
            target_region_code="510300",
            target_region_name="自贡市",
            target_level="prefecture",
            is_required=True,
            source_note="省下辖地级行政单元",
        ),
    ]


def test_validate_region_seed_rejects_duplicate_required_target(tmp_path):
    csv_path = tmp_path / "regions.csv"
    write_region_seed(
        csv_path,
        "mca_geoname_2026_snapshot,510000,四川省,510100,成都市,prefecture,1,first\n"
        "mca_geoname_2026_snapshot,510000,四川省,510100,成都市,prefecture,1,duplicate\n",
    )

    rows = load_national_regions(csv_path)

    with pytest.raises(ValueError, match="DUPLICATE_TARGET_REGION_CODE"):
        validate_region_seed(rows)


def test_summarize_region_targets_counts_required_targets_by_province(tmp_path):
    csv_path = tmp_path / "regions.csv"
    write_region_seed(
        csv_path,
        "mca_geoname_2026_snapshot,110000,北京市,110000,北京市,municipality,1,直辖市\n"
        "mca_geoname_2026_snapshot,510000,四川省,510100,成都市,prefecture,1,地级市\n"
        "mca_geoname_2026_snapshot,510000,四川省,510300,自贡市,prefecture,1,地级市\n"
        "mca_geoname_2026_snapshot,510000,四川省,510000,四川省,province,0,省级来源行不参与目标完整性分母\n",
    )

    summary = summarize_region_targets(load_national_regions(csv_path))

    assert summary == {
        "110000": {
            "province_name": "北京市",
            "required_target_count": 1,
            "target_levels": {"municipality": 1},
        },
        "510000": {
            "province_name": "四川省",
            "required_target_count": 2,
            "target_levels": {"prefecture": 2},
        },
    }
