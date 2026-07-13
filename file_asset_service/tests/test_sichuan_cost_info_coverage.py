from app.models import Archive, DataSource
from app.sichuan_cost_info import (
    SICHUAN_AREA_ENDPOINT,
    SICHUAN_PERIOD_ENDPOINT,
    build_sichuan_full_coverage_expectation,
    build_sichuan_mianyang_coverage_expectation,
    build_sichuan_online_table_coverage_expectations,
    parse_sichuan_periods,
    sichuan_province_cost_info_source_config,
    upsert_sichuan_full_coverage_source,
    upsert_sichuan_mianyang_coverage_source,
    upsert_sichuan_online_table_declaration_sources,
)
from app.info_price_coverage import build_coverage_matrix


def sichuan_area_payload():
    return [
        {
            "id": "川",
            "text": "四川",
            "children": [
                {
                    "id": "川B",
                    "text": "绵阳市",
                    "children": [
                        {"id": "川B-0010", "text": "绵阳市区", "children": []},
                        {"id": "川B-0020", "text": "梓潼县", "children": []},
                        {"id": "川B-0030", "text": "三台县", "children": []},
                        {"id": "川B-0040", "text": "盐亭县", "children": []},
                        {"id": "川B-0050", "text": "安州区", "children": []},
                        {"id": "川B-0060", "text": "平武县", "children": []},
                        {"id": "川B-0070", "text": "江油市", "children": []},
                        {"id": "川B-0080", "text": "北川县", "children": []},
                    ],
                }
            ],
        }
    ]


def sichuan_full_area_payload():
    city_names = {
        "川A": "成都市",
        "川B": "绵阳市",
        "川C": "自贡市",
        "川D": "攀枝花市",
        "川E": "泸州市",
        "川F": "德阳市",
        "川H": "广元市",
        "川J": "遂宁市",
        "川K": "内江市",
        "川L": "乐山市",
        "川M": "资阳市",
        "川Q": "宜宾市",
        "川R": "南充市",
        "川S": "达州市",
        "川T": "雅安市",
        "川U": "阿坝州",
        "川V": "甘孜州",
        "川W": "凉山州",
        "川X": "广安市",
        "川Y": "巴中市",
        "川Z": "眉山市",
    }
    children = []
    for area_id, name in city_names.items():
        node_children = []
        if area_id == "川B":
            node_children = sichuan_area_payload()[0]["children"][0]["children"]
        children.append({"id": area_id, "text": name, "children": node_children})
    return [{"id": "川", "text": "四川", "children": children}]


def sichuan_period_payload():
    return [
        {"PeriodNo": 756, "PeriodName": "2026年04月", "Year": 2026, "CurYearNo": 4},
        {"PeriodNo": 755, "PeriodName": "2026年03月", "Year": 2026, "CurYearNo": 3},
        {"PeriodNo": 754, "PeriodName": "2026年02月", "Year": 2026, "CurYearNo": 2},
        {"PeriodNo": 753, "PeriodName": "2026年01月", "Year": 2026, "CurYearNo": 1},
        {"PeriodNo": 752, "PeriodName": "2025年12月", "Year": 2025, "CurYearNo": 12},
    ]


def test_sichuan_province_source_config_marks_html_table_directory_only():
    config = sichuan_province_cost_info_source_config()

    assert config["stable"]["site_id"] == "cost_info.sc.province.coverage"
    assert config["stable"]["region_code"] == "510000"
    assert config["stable"]["publisher_scope"] == "province"
    assert config["price_coordinates"] == {"price_source_type": "info_price", "tax_type": None}
    assert config["source_shape"]["source_attachment_mode"] == "html_table"
    assert config["source_shape"]["acquisition"] == "coverage_directory_only"
    assert config["parser"]["area_endpoint"] == SICHUAN_AREA_ENDPOINT
    assert config["parser"]["period_endpoint"] == SICHUAN_PERIOD_ENDPOINT
    assert "source_priority" not in config["stable"]


def test_build_sichuan_mianyang_coverage_expectation_uses_directory_periods_only():
    expectation = build_sichuan_mianyang_coverage_expectation(
        area_payload=sichuan_area_payload(),
        period_payload=sichuan_period_payload(),
        start_period="2026-01",
    )

    targets = expectation["target_regions"]
    by_code = {target["region_code"]: target for target in targets}

    assert parse_sichuan_periods(sichuan_period_payload(), start_period="2026-01") == [
        {"period": "2026-04", "period_no": 756, "period_name": "2026年04月"},
        {"period": "2026-03", "period_no": 755, "period_name": "2026年03月"},
        {"period": "2026-02", "period_no": 754, "period_name": "2026年02月"},
        {"period": "2026-01", "period_no": 753, "period_name": "2026年01月"},
    ]
    assert len(targets) == 8
    assert by_code["510700-市区"]["external_area_id"] == "川B-0010"
    assert by_code["510725"]["region_name"] == "梓潼县"
    assert by_code["510722"]["region_name"] == "三台县"
    assert by_code["510725"]["requires_city_source"] is True
    assert by_code["510725"]["declared_periods"] == ["2026-04", "2026-03", "2026-02", "2026-01"]
    assert "目录声明" in by_code["510725"]["coverage_note"]


def test_build_sichuan_full_coverage_expectation_covers_21_city_prefectures():
    expectation = build_sichuan_full_coverage_expectation(
        area_payload=sichuan_full_area_payload(),
        period_payload=sichuan_period_payload(),
        start_period="2026-01",
    )

    targets = expectation["target_regions"]
    city_targets = [target for target in targets if target["target_level"] == "city"]
    by_code = {target["region_code"]: target for target in targets}

    assert len(city_targets) == 21
    assert by_code["510100"]["region_name"] == "成都市"
    assert by_code["510100"]["external_area_id"] == "川A"
    assert by_code["510100"]["source_audit_status"] == "manual_upload_required"
    assert by_code["510300"]["source_audit_status"] == "auto_crawl_verified"
    assert by_code["510400"]["source_audit_status"] == "auto_crawl_verified"
    assert by_code["510500"]["source_audit_status"] == "auto_crawl_verified"
    assert by_code["511700"]["source_audit_status"] == "auto_crawl_verified"
    assert "iframe" in by_code["511700"]["source_audit_note"]
    assert by_code["511900"]["source_audit_status"] == "online_table_declaration"
    assert by_code["512000"]["source_audit_status"] == "online_table_declaration"
    assert by_code["510800"]["source_completeness_status"] == "source_blocked"
    assert by_code["511400"]["source_completeness_status"] == "source_blocked"
    assert by_code["511800"]["source_completeness_status"] == "source_blocked"
    assert by_code["510700-市区"]["target_level"] == "subregion"
    assert by_code["510725"]["region_name"] == "梓潼县"
    assert by_code["510700"]["declared_periods"] == ["2026-04", "2026-03", "2026-02", "2026-01"]
    assert "不镜像价格表内容" in by_code["510400"]["coverage_note"]


def test_upsert_sichuan_mianyang_coverage_source_creates_no_archives(db_session):
    source = upsert_sichuan_mianyang_coverage_source(
        db_session,
        area_payload=sichuan_area_payload(),
        period_payload=sichuan_period_payload(),
        start_period="2026-01",
        actor_id="coverage-test",
    )

    stored = db_session.get(DataSource, source.source_id)
    targets = stored.config["coverage_expectation"]["target_regions"]

    assert stored.name == "四川省工程造价信息网-覆盖目录"
    assert stored.region_code == "510000"
    assert len(targets) == 8
    assert db_session.query(Archive).count() == 0


def test_upsert_sichuan_full_coverage_source_creates_city_matrix_targets_only(db_session):
    source = upsert_sichuan_full_coverage_source(
        db_session,
        area_payload=sichuan_full_area_payload(),
        period_payload=sichuan_period_payload(),
        start_period="2026-01",
        actor_id="coverage-test",
    )

    stored = db_session.get(DataSource, source.source_id)
    targets = stored.config["coverage_expectation"]["target_regions"]
    city_targets = [target for target in targets if target["target_level"] == "city"]

    assert stored.name == "四川省工程造价信息网-覆盖目录"
    assert stored.config["parser"]["parsers"]["sceci.coverage-directory.v1"]["city_area_id"] == "*"
    assert len(city_targets) == 21
    assert db_session.query(Archive).count() == 0


def test_build_sichuan_online_table_declarations_cover_bazhong_and_ziyang_only():
    expectations = build_sichuan_online_table_coverage_expectations(
        period_payload=sichuan_period_payload(),
        start_period="2026-04",
    )

    assert set(expectations) == {"511900", "512000"}
    bazhong = expectations["511900"]["target_regions"][0]
    ziyang = expectations["512000"]["target_regions"][0]

    assert bazhong["region_name"] == "巴中市"
    assert bazhong["declared_periods"] == ["2026-04"]
    assert bazhong["city_source_url"] == "https://zfcxjsj.cnbz.gov.cn/ztzl/bzsjzclscxxj/index.html"
    assert bazhong["source_audit_status"] == "online_table_declaration"
    assert "原站跳转" in bazhong["coverage_note"]
    assert ziyang["region_name"] == "资阳市"
    assert ziyang["city_source_url"] == (
        "http://www.ziyang.gov.cn/zysrmzf/xxgkgsgg/pc/content/content_2068898430624374784.html"
    )


def test_upsert_sichuan_online_table_declaration_sources_create_no_archives(db_session):
    sources = upsert_sichuan_online_table_declaration_sources(
        db_session,
        period_payload=sichuan_period_payload(),
        start_period="2026-04",
        actor_id="coverage-test",
    )

    by_region = {source.region_code: source for source in sources}

    assert set(by_region) == {"511900", "512000"}
    assert by_region["511900"].name == "巴中市建筑材料市场信息价-在线表声明"
    assert by_region["511900"].downloadable is False
    assert by_region["511900"].config["stable"]["publisher_scope"] == "city"
    assert by_region["511900"].config["source_shape"]["source_attachment_mode"] == "html_table"
    assert by_region["511900"].config["source_shape"]["acquisition"] == "coverage_declaration_only"
    assert by_region["511900"].config["coverage_expectation"]["target_regions"][0]["declared_periods"] == ["2026-04"]
    assert db_session.query(Archive).count() == 0


def test_sichuan_coverage_matrix_marks_online_table_and_blocked_city_statuses(db_session):
    province = upsert_sichuan_full_coverage_source(
        db_session,
        area_payload=sichuan_full_area_payload(),
        period_payload=sichuan_period_payload(),
        start_period="2026-04",
        actor_id="coverage-test",
    )
    city_sources = upsert_sichuan_online_table_declaration_sources(
        db_session,
        period_payload=sichuan_period_payload(),
        start_period="2026-04",
        actor_id="coverage-test",
    )

    rows = build_coverage_matrix(db_session, start_period="2026-04", end_period="2026-04", province_code="510000")
    by_code = {row.coverage_region_code: row for row in rows if row.target_level == "city"}

    assert len(by_code) == 21
    assert all(row.source_audit_status for row in by_code.values())
    assert by_code["511900"].business_coverage_status == "covered"
    assert by_code["511900"].source_completeness_status == "dual_source"
    assert by_code["511900"].province_source_count == 1
    assert by_code["511900"].city_source_count == 1
    assert by_code["511900"].source_visit_url == "https://zfcxjsj.cnbz.gov.cn/ztzl/bzsjzclscxxj/index.html"
    assert province.source_id in by_code["511900"].source_ids
    assert city_sources[0].source_id in by_code["511900"].source_ids
    assert by_code["512000"].source_completeness_status == "dual_source"
    assert by_code["512000"].source_visit_url == (
        "http://www.ziyang.gov.cn/zysrmzf/xxgkgsgg/pc/content/content_2068898430624374784.html"
    )
    for region_code in ("510800", "511400", "511800"):
        assert by_code[region_code].business_coverage_status == "covered"
        assert by_code[region_code].source_completeness_status == "source_blocked"
        assert by_code[region_code].source_audit_status == "source_blocked"
        assert by_code[region_code].province_source_count == 1
        assert by_code[region_code].city_source_count == 0
