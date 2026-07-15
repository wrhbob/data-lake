from app.archive_rules import metadata_cell
from app.archive_service import create_archive
from app.collection import create_data_source
from app.info_price_coverage import build_coverage_matrix, build_issue_groups
from app.models import Archive


def field_sources():
    return {
        field: {"source_level": "crawler", "tagged_by": "coverage-test", "tagged_at": "2026-06-22T00:00:00+08:00"}
        for field in ("domain_type", "channel_type", "business_key", "title", "region_code", "publish_date")
    }


def cost_info_source(db_session, *, name, region_code, publisher_scope, target_region_code, status="可自动采"):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name=name,
        data_domain="cost_info",
        region_code=region_code,
        config={
            "stable": {
                "site_id": f"cost_info.test.{region_code}.{publisher_scope}",
                "domain_type": "cost_info",
                "region_code": region_code,
                "publisher_scope": publisher_scope,
                "publisher_region_code": region_code,
                "publisher_name": name,
            },
            "coverage_expectation": {
                "target_regions": [
                    {
                        "region_code": target_region_code,
                        "region_name": "成都市",
                        "requires_city_source": True,
                        "source_completeness_status": "source_blocked" if status == "来源受阻" else "pending_source_audit",
                        "coverage_note": "成都官方源受限" if status == "来源受阻" else "",
                    }
                ]
            },
            "ops": {"source_audit_status": status},
        },
    )


def add_evidence(db_session, source, *, period="2026-06", coverage_region_code="510100"):
    return create_archive(
        db_session,
        domain_type="cost_info",
        channel_type="crawler",
        business_key=f"cost_info:{source.source_id}:{coverage_region_code}:{period}:信息价",
        title=f"{period} 信息价",
        source_id=source.source_id,
        tenant_code=source.asset_tenant_code,
        visibility_scope="public",
        status="pending_tag",
        region_code=source.region_code,
        publish_date=f"{period}-20",
        metadata={
            "period": metadata_cell(period, source_level="crawler", tagged_by="coverage-test"),
            "coverage_region_code": metadata_cell(coverage_region_code, source_level="crawler", tagged_by="coverage-test"),
            "publisher_scope": metadata_cell(source.config["stable"]["publisher_scope"], source_level="crawler", tagged_by="coverage-test"),
            "price_source_type": metadata_cell("info_price", source_level="crawler", tagged_by="coverage-test"),
            "tax_type": metadata_cell(None, source_level="crawler", tagged_by="coverage-test"),
        },
        field_sources=field_sources(),
        actor_type="system",
        actor_id="coverage-test",
    )


def test_coverage_matrix_counts_directory_declaration_without_archive_for_mianyang_paradox(db_session):
    city = create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="绵阳市住房和城乡建设委员会",
        data_domain="cost_info",
        region_code="510700",
        city="绵阳市",
        config={
            "stable": {
                "site_id": "cost_info.sc.mianyang",
                "domain_type": "cost_info",
                "region_code": "510700",
                "coverage_region_code": "510700-市区",
                "publisher_scope": "city",
                "publisher_region_code": "510700",
                "publisher_name": "绵阳市住房和城乡建设委员会",
                "entry_url": "https://zjw.my.gov.cn/myszjj/c101133/list.shtml",
            }
        },
    )
    province = create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="四川省工程造价信息网-覆盖目录",
        data_domain="cost_info",
        region_code="510000",
        config={
            "stable": {
                "site_id": "cost_info.sc.province.coverage",
                "domain_type": "cost_info",
                "region_code": "510000",
                "publisher_scope": "province",
                "publisher_region_code": "510000",
                "publisher_name": "四川省建设工程造价总站",
                "entry_url": "http://202.61.90.35:8032/pubpages/pricelist.aspx",
            },
            "coverage_expectation": {
                "target_regions": [
                    {
                        "region_code": "510700-市区",
                        "region_name": "绵阳市区",
                        "requires_city_source": True,
                        "declared_periods": ["2026-04"],
                        "coverage_note": "四川省站GetAreaList/GetAllPublishPeriodList目录声明",
                    },
                    {
                        "region_code": "510725",
                        "region_name": "梓潼县",
                        "requires_city_source": True,
                        "declared_periods": ["2026-04"],
                        "coverage_note": "四川省站GetAreaList/GetAllPublishPeriodList目录声明",
                    },
                ]
            },
        },
    )
    add_evidence(db_session, city, period="2026-04", coverage_region_code="510700-市区")

    rows = build_coverage_matrix(db_session, start_period="2026-04", end_period="2026-04")
    by_region = {row.coverage_region_code: row for row in rows}

    assert by_region["510700-市区"].business_coverage_status == "covered"
    assert by_region["510700-市区"].coverage_region_name == "绵阳市区"
    assert by_region["510700-市区"].source_completeness_status == "dual_source"
    assert by_region["510700-市区"].province_source_count == 1
    assert by_region["510700-市区"].city_source_count == 1
    assert by_region["510700-市区"].source_visit_url == "https://zjw.my.gov.cn/myszjj/c101133/list.shtml"
    assert province.source_id in by_region["510700-市区"].source_ids
    assert city.source_id in by_region["510700-市区"].source_ids
    assert by_region["510725"].business_coverage_status == "covered"
    assert by_region["510725"].source_completeness_status == "city_source_missing"
    assert by_region["510725"].province_source_count == 1
    assert by_region["510725"].city_source_count == 0
    assert by_region["510725"].source_visit_url == "http://202.61.90.35:8032/pubpages/pricelist.aspx"
    assert by_region["510725"].to_dict()["source_visit_url"] == "http://202.61.90.35:8032/pubpages/pricelist.aspx"
    assert db_session.query(Archive).count() == 1


def test_coverage_matrix_marks_future_unpublished_period_pending_verify_not_missing(db_session):
    source = cost_info_source(
        db_session,
        name="乌鲁木齐建筑市场运行服务中心",
        region_code="650100",
        publisher_scope="city",
        target_region_code="650100",
    )
    add_evidence(db_session, source, period="2026-04", coverage_region_code="650100")

    rows = build_coverage_matrix(db_session, start_period="2026-04", end_period="2026-05")
    by_period = {row.period: row for row in rows}

    assert by_period["2026-04"].business_coverage_status == "covered"
    assert by_period["2026-05"].business_coverage_status == "pending_verify"
    assert "未发布" in (by_period["2026-05"].coverage_note or "")


def test_coverage_matrix_marks_unbackfilled_period_pending_verify_not_missing(db_session):
    source = cost_info_source(
        db_session,
        name="自贡市人民政府-材料价格",
        region_code="510300",
        publisher_scope="city",
        target_region_code="510300",
    )
    add_evidence(db_session, source, period="2026-05", coverage_region_code="510300")

    rows = build_coverage_matrix(db_session, start_period="2026-04", end_period="2026-05")
    by_period = {row.period: row for row in rows}

    assert by_period["2026-04"].business_coverage_status == "pending_verify"
    assert "待回填核验" in (by_period["2026-04"].coverage_note or "")
    assert by_period["2026-05"].business_coverage_status == "covered"


def test_coverage_matrix_exposes_issue_label_and_evidence_titles(db_session):
    source = cost_info_source(
        db_session,
        name="三明市住房和城乡建设局",
        region_code="350400",
        publisher_scope="city",
        target_region_code="350400",
    )
    archive = add_evidence(db_session, source, period="2026-05", coverage_region_code="350400")
    archive.title = "三明市建设工程主要材料综合价格2026年第5期"
    archive.metadata_payload = {
        **archive.metadata_payload,
        "period_raw": metadata_cell("2026年第5期", source_level="crawler", tagged_by="coverage-test"),
    }
    db_session.add(archive)
    db_session.commit()

    rows = build_coverage_matrix(db_session, start_period="2026-05", end_period="2026-05")

    assert rows[0].period_label == "第5期"
    assert rows[0].evidence_titles == ["三明市建设工程主要材料综合价格2026年第5期"]
    assert rows[0].to_dict()["period_label"] == "第5期"
    assert rows[0].to_dict()["evidence_titles"] == ["三明市建设工程主要材料综合价格2026年第5期"]


def test_coverage_matrix_normalizes_prefecture_target_level_to_city(db_session):
    source = cost_info_source(
        db_session,
        name="泉州市住房和城乡建设局",
        region_code="350500",
        publisher_scope="city",
        target_region_code="350500",
    )
    config = dict(source.config)
    coverage = dict(config["coverage_expectation"])
    target = dict(coverage["target_regions"][0])
    target["target_level"] = "prefecture"
    coverage["target_regions"] = [target]
    config["coverage_expectation"] = coverage
    source.config = config
    db_session.add(source)
    db_session.commit()

    rows = build_coverage_matrix(db_session, start_period="2026-05", end_period="2026-05", province_code="350000")

    assert rows[0].target_level == "city"
    assert rows[0].to_dict()["target_level"] == "city"


def test_coverage_matrix_maps_municipality_province_target_to_city(db_session):
    source = cost_info_source(
        db_session,
        name="北京市住房和城乡建设委员会",
        region_code="110000",
        publisher_scope="province",
        target_region_code="110000",
    )
    config = dict(source.config)
    coverage = dict(config["coverage_expectation"])
    target = dict(coverage["target_regions"][0])
    target["region_name"] = "北京市"
    target["target_level"] = "province"
    coverage["target_regions"] = [target]
    config["coverage_expectation"] = coverage
    source.config = config
    db_session.add(source)
    db_session.commit()

    rows = build_coverage_matrix(db_session, start_period="2024-01", end_period="2024-01", province_code="110000")

    assert len(rows) == 1
    assert rows[0].coverage_region_name == "北京市"
    assert rows[0].target_level == "city"


def test_issue_groups_same_region_period_archives_without_adoption(db_session):
    province = cost_info_source(
        db_session,
        name="四川省建设工程造价总站",
        region_code="510000",
        publisher_scope="province",
        target_region_code="510100",
    )
    city = cost_info_source(
        db_session,
        name="成都市建设工程造价站",
        region_code="510100",
        publisher_scope="city",
        target_region_code="510100",
    )
    add_evidence(db_session, province)
    add_evidence(db_session, city)

    groups = build_issue_groups(db_session, start_period="2026-06", end_period="2026-06")

    assert len(groups) == 1
    assert groups[0].issue_key == "cost_info:510100:2026-06"
    assert groups[0].coverage_region_code == "510100"
    assert groups[0].period == "2026-06"
    assert groups[0].evidence_count == 2
    assert len(groups[0].business_keys) == 2
    assert not hasattr(groups[0], "adopted_archive_id")


def test_coverage_matrix_marks_dual_source_without_rewriting_archives(db_session):
    province = cost_info_source(
        db_session,
        name="四川省建设工程造价总站",
        region_code="510000",
        publisher_scope="province",
        target_region_code="510100",
    )
    city = cost_info_source(
        db_session,
        name="成都市建设工程造价站",
        region_code="510100",
        publisher_scope="city",
        target_region_code="510100",
    )
    first = add_evidence(db_session, province)
    second = add_evidence(db_session, city)

    rows = build_coverage_matrix(db_session, start_period="2026-06", end_period="2026-06")

    assert len(rows) == 1
    assert rows[0].business_coverage_status == "covered"
    assert rows[0].source_completeness_status == "dual_source"
    assert rows[0].province_source_count == 1
    assert rows[0].city_source_count == 1
    assert db_session.query(Archive).count() == 2
    assert db_session.get(Archive, first.archive_id).business_key == first.business_key
    assert db_session.get(Archive, second.archive_id).business_key == second.business_key


def test_coverage_matrix_distinguishes_province_covered_from_city_source_missing(db_session):
    province = cost_info_source(
        db_session,
        name="四川省建设工程造价总站",
        region_code="510000",
        publisher_scope="province",
        target_region_code="510100",
    )
    add_evidence(db_session, province)

    rows = build_coverage_matrix(db_session, start_period="2026-06", end_period="2026-06")

    assert len(rows) == 1
    assert rows[0].business_coverage_status == "covered"
    assert rows[0].source_completeness_status == "city_source_missing"
    assert rows[0].province_source_count == 1
    assert rows[0].city_source_count == 0


def test_coverage_matrix_uses_stable_coverage_region_when_narrower_than_publisher_region(db_session):
    source = create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="绵阳市住房和城乡建设委员会",
        data_domain="cost_info",
        region_code="510700",
        city="绵阳市",
        config={
            "stable": {
                "site_id": "cost_info.sc.mianyang",
                "domain_type": "cost_info",
                "region_code": "510700",
                "coverage_region_code": "510700-市区",
                "publisher_scope": "city",
                "publisher_region_code": "510700",
                "publisher_name": "绵阳市住房和城乡建设委员会",
            }
        },
    )
    add_evidence(db_session, source, period="2026-04", coverage_region_code="510700-市区")

    rows = build_coverage_matrix(db_session, start_period="2026-04", end_period="2026-04")

    assert len(rows) == 1
    assert rows[0].coverage_region_code == "510700-市区"
    assert rows[0].business_coverage_status == "covered"
    assert rows[0].source_completeness_status == "city_source_present"


def test_coverage_matrix_counts_city_center_evidence_for_parent_city_view(db_session):
    source = create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="绵阳市住房和城乡建设委员会",
        data_domain="cost_info",
        region_code="510700",
        city="绵阳市",
        config={
            "stable": {
                "site_id": "cost_info.sc.mianyang",
                "domain_type": "cost_info",
                "region_code": "510700",
                "coverage_region_code": "510700-市区",
                "publisher_scope": "city",
                "publisher_region_code": "510700",
                "publisher_name": "绵阳市住房和城乡建设委员会",
            },
            "coverage_expectation": {
                "target_regions": [
                    {
                        "region_code": "510700",
                        "region_name": "绵阳市",
                        "target_level": "city",
                        "requires_city_source": True,
                    }
                ]
            },
        },
    )
    add_evidence(db_session, source, period="2026-04", coverage_region_code="510700-市区")

    rows = build_coverage_matrix(db_session, start_period="2026-01")
    by_period = {row.period: row for row in rows if row.coverage_region_code == "510700"}

    assert "2026-04" in by_period
    assert by_period["2026-04"].business_coverage_status == "covered"
    assert by_period["2026-04"].source_completeness_status == "city_source_present"
    assert by_period["2026-04"].city_source_count == 1
    assert by_period["2026-04"].source_ids == [source.source_id]
    assert db_session.query(Archive).count() == 1


def test_coverage_matrix_reports_manual_only_blocked_source_without_archive(db_session):
    cost_info_source(
        db_session,
        name="成都市建设工程造价站",
        region_code="510100",
        publisher_scope="city",
        target_region_code="510100",
        status="来源受阻",
    )

    rows = build_coverage_matrix(db_session, start_period="2026-06", end_period="2026-06")

    assert len(rows) == 1
    assert rows[0].business_coverage_status == "pending_verify"
    assert rows[0].source_completeness_status == "source_blocked"


def test_coverage_matrix_default_end_period_is_scoped_to_requested_province(db_session):
    province = create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="四川省工程造价信息网-覆盖目录",
        data_domain="cost_info",
        region_code="510000",
        config={
            "stable": {
                "site_id": "cost_info.sc.province.coverage",
                "domain_type": "cost_info",
                "region_code": "510000",
                "publisher_scope": "province",
                "publisher_region_code": "510000",
                "publisher_name": "四川省建设工程造价总站",
            },
            "coverage_expectation": {
                "target_regions": [
                    {
                        "region_code": "510100",
                        "region_name": "成都市",
                        "target_level": "city",
                        "requires_city_source": True,
                        "declared_periods": ["2026-04"],
                    }
                ]
            },
        },
    )
    chongqing = cost_info_source(
        db_session,
        name="重庆市建设工程造价总站",
        region_code="500100",
        publisher_scope="province",
        target_region_code="500100",
    )
    add_evidence(db_session, chongqing, period="2026-06", coverage_region_code="500100")

    rows = build_coverage_matrix(db_session, start_period="2026-04", province_code="510000")

    assert [row.period for row in rows] == ["2026-04"]
    assert rows[0].source_ids == [province.source_id]


def test_coverage_matrix_normalizes_title_like_period_from_publish_date(db_session):
    source = cost_info_source(
        db_session,
        name="珠海市工程造价信息化平台",
        region_code="440400",
        publisher_scope="city",
        target_region_code="440400",
    )
    create_archive(
        db_session,
        domain_type="cost_info",
        channel_type="crawler",
        business_key=f"cost_info:{source.source_id}:440400:2026-05:珠海信息价",
        title="关于发布2026年5月珠海工程造价信息的通知",
        source_id=source.source_id,
        tenant_code=source.asset_tenant_code,
        visibility_scope="public",
        status="pending_tag",
        region_code=source.region_code,
        publish_date="2026-05-20",
        metadata={
            "period": metadata_cell("关于发布2026年5月珠海工程造价信息的通知", source_level="crawler", tagged_by="coverage-test"),
            "coverage_region_code": metadata_cell("440400", source_level="crawler", tagged_by="coverage-test"),
            "publisher_scope": metadata_cell("city", source_level="crawler", tagged_by="coverage-test"),
            "price_source_type": metadata_cell("info_price", source_level="crawler", tagged_by="coverage-test"),
            "tax_type": metadata_cell(None, source_level="crawler", tagged_by="coverage-test"),
        },
        field_sources=field_sources(),
        actor_type="system",
        actor_id="coverage-test",
    )

    rows = build_coverage_matrix(db_session, start_period="2026-05", province_code="440000")

    assert [row.period for row in rows] == ["2026-05"]
    assert rows[0].business_coverage_status == "covered"
    assert rows[0].source_completeness_status == "city_source_present"


def test_municipality_source_registers_as_city_source(db_session):
    # 直辖市源 publisher_scope=municipality，应被归一为市级源登记进覆盖目标，
    # 否则覆盖矩阵 source_ids 为空、补爬会报 source_not_found（北京/天津/上海/重庆盲区）。
    source = create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="北京市住房和城乡建设委员会-工程造价信息",
        data_domain="cost_info",
        region_code="110000",
        city="北京市",
        config={
            "stable": {
                "site_id": "cost_info.test.110000.municipality",
                "domain_type": "cost_info",
                "region_code": "110000",
                "publisher_scope": "municipality",
                "publisher_region_code": "110000",
                "publisher_name": "北京市住房和城乡建设委员会",
            },
        },
    )

    rows = build_coverage_matrix(
        db_session,
        start_period="2024-01",
        end_period="2024-01",
        province_code="110000",
    )

    assert rows, "municipality target should be built"
    row = rows[0]
    assert row.coverage_region_code == "110000"
    assert source.source_id in row.source_ids
    assert row.city_source_count == 1
    assert row.source_completeness_status == "city_source_present"
