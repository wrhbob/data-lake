from app.catalog_cleanup_report import normalize_archive_period
from app.models import Archive


def test_normalize_archive_period_keeps_two_digit_october_to_december_months():
    archive = Archive(
        coverage_period=None,
        metadata_payload={"period_raw": {"value": "2025年12月北京工程造价信息"}},
    )

    assert normalize_archive_period(archive) == "2025-12"


def test_normalize_archive_period_zero_pads_single_digit_month():
    archive = Archive(
        coverage_period=None,
        metadata_payload={"period_start": {"value": "2026年4月"}},
    )

    assert normalize_archive_period(archive) == "2026-04"
