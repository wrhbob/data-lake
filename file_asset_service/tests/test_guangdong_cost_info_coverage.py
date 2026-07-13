from app.archive_rules import metadata_cell
from app.archive_service import create_archive
from app.guangdong_cost_info import (
    GUANGDONG_BLOCKED_CITY_SOURCES,
    GUANGDONG_DECLARATION_CITY_SOURCES,
    GUANGDONG_FILE_SOURCE_CITY_CODES,
    upsert_guangdong_demo_coverage_sources,
)
from app.info_price_coverage import build_coverage_matrix
from app.models import Archive


def field_sources():
    return {
        field: {"source_level": "crawler", "tagged_by": "gd-coverage-test", "tagged_at": "2026-06-23T00:00:00+08:00"}
        for field in ("domain_type", "channel_type", "business_key", "title", "region_code", "publish_date")
    }


def add_guangdong_file_evidence(db_session, source, *, period="2026-05"):
    return create_archive(
        db_session,
        domain_type="cost_info",
        channel_type="crawler",
        business_key=f"cost_info:{source.source_id}:{source.region_code}:{period}:{source.city}信息价",
        title=f"{source.city}{period}信息价",
        source_id=source.source_id,
        tenant_code=source.asset_tenant_code,
        visibility_scope="public",
        status="collected",
        region_code=source.region_code,
        publish_date=f"{period}-20",
        metadata={
            "period": metadata_cell(period, source_level="crawler", tagged_by="gd-coverage-test"),
            "coverage_region_code": metadata_cell(source.region_code, source_level="crawler", tagged_by="gd-coverage-test"),
            "publisher_scope": metadata_cell("city", source_level="crawler", tagged_by="gd-coverage-test"),
            "price_source_type": metadata_cell("info_price", source_level="crawler", tagged_by="gd-coverage-test"),
            "tax_type": metadata_cell(None, source_level="crawler", tagged_by="gd-coverage-test"),
        },
        field_sources=field_sources(),
        actor_type="system",
        actor_id="gd-coverage-test",
        allow_fileless_collected=True,
    )


def test_guangdong_demo_coverage_sources_cover_all_21_city_statuses(db_session):
    bundle = upsert_guangdong_demo_coverage_sources(
        db_session,
        declared_periods=["2026-05"],
        actor_id="gd-coverage-test",
    )
    for source in bundle["file_sources"]:
        add_guangdong_file_evidence(db_session, source, period="2026-05")

    rows = build_coverage_matrix(db_session, start_period="2026-05", end_period="2026-05", province_code="440000")
    by_code = {row.coverage_region_code: row for row in rows if row.target_level == "city"}

    assert len(by_code) == 21
    assert set(GUANGDONG_FILE_SOURCE_CITY_CODES) == {source.region_code for source in bundle["file_sources"]}
    assert set(GUANGDONG_DECLARATION_CITY_SOURCES) == {source.region_code for source in bundle["declaration_sources"]}
    assert set(GUANGDONG_BLOCKED_CITY_SOURCES) == {source.region_code for source in bundle["blocked_sources"]}
    for source in bundle["file_sources"]:
        assert source.config["stable"]["period_kind"] == "monthly"
        assert source.config["price_coordinates"]["price_kind"] == "guidance"
    assert all(row.source_audit_status for row in by_code.values())
    assert not [row for row in by_code.values() if row.business_coverage_status == "missing"]
    assert not [row for row in by_code.values() if row.source_completeness_status == "pending_source_audit"]

    assert by_code["440400"].business_coverage_status == "covered"
    assert by_code["440400"].source_completeness_status == "city_source_present"
    assert by_code["440400"].source_audit_status == "spa_api_file_source"
    assert by_code["440400"].source_visit_url == "https://zhjg.zhszjj.com:7528/#/gdzj-second/gdzj-price-info"

    assert by_code["440300"].business_coverage_status == "covered"
    assert by_code["440300"].source_completeness_status == "city_source_present"
    assert by_code["440300"].source_audit_status == "online_table_declaration"
    assert by_code["440300"].source_visit_url == "https://zjj.sz.gov.cn/szzjxx/web/pc/index"

    assert by_code["440800"].business_coverage_status == "pending_verify"
    assert by_code["440800"].source_completeness_status == "source_blocked"
    assert by_code["440800"].source_audit_status == "source_blocked"
    assert by_code["445200"].source_completeness_status == "source_blocked"

    assert db_session.query(Archive).count() == len(GUANGDONG_FILE_SOURCE_CITY_CODES)


def test_file_source_stays_present_when_selected_month_has_no_publication(db_session):
    bundle = upsert_guangdong_demo_coverage_sources(
        db_session,
        declared_periods=["2026-05"],
        actor_id="gd-coverage-test",
    )
    zhongshan = next(source for source in bundle["file_sources"] if source.region_code == "442000")
    add_guangdong_file_evidence(db_session, zhongshan, period="2026-01")

    rows = build_coverage_matrix(db_session, start_period="2026-05", end_period="2026-05", province_code="440000")
    by_code = {row.coverage_region_code: row for row in rows if row.target_level == "city"}

    assert by_code["442000"].business_coverage_status == "pending_verify"
    assert by_code["442000"].source_completeness_status == "city_source_present"
    assert by_code["442000"].source_audit_status == "auto_crawl_verified"
    assert by_code["442000"].source_visit_url == "https://jsj.zs.gov.cn/zwgk/tjsj/index.html"
