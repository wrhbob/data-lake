from app.collection import create_data_source
from app.info_price_coverage import build_coverage_matrix
from app.models import Archive, ArchiveFile, FileAsset, FileProcessing
from app.storage import FakeObjectStore
from app.xinjiang_cost_info import (
    XINJIANG_AREA_SOURCE_DEFS,
    XINJIANG_ASPNET_AJAX_AREA_PARSER_VERSION,
    XINJIANG_URUMQI_AREA_ID,
    XINJIANG_URUMQI_PARSER_VERSION,
    ingest_xinjiang_area_issue,
    ingest_xinjiang_urumqi_issue,
    list_xinjiang_area_issues,
    list_xinjiang_urumqi_issues,
    run_xinjiang_urumqi_incremental,
    xinjiang_area_cost_info_source_config,
    xinjiang_urumqi_cost_info_source_config,
)


def cell_value(cell):
    return cell["value"]


class FakeXinjiangClient:
    def __init__(self, *, detail_html: str | None = None, rows: list[dict] | None = None):
        self.detail_html = detail_html or ""
        self.rows = rows
        self.posted = []
        self.downloaded = []

    def post_form_json(self, url, payload):
        self.posted.append((url, payload))
        return {
            "Total": 101,
            "Rows": self.rows
            or [
                {
                    "ID": "6788",
                    "Name": "乌鲁木齐市2026年4月份建设工程综合价格信息",
                    "ReleaseDate": "/Date(1780358400000)/",
                }
            ],
        }

    def get_text(self, url):
        return self.detail_html

    def get_bytes(self, url):
        self.downloaded.append(url)
        if url.endswith(".doc"):
            return b"DOC original bytes", "application/msword"
        if url.endswith(".xlsx"):
            return (
                b"XLSX original bytes, not parsed",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        raise AssertionError(f"unexpected download: {url}")


def test_xinjiang_urumqi_source_config_uses_areaid_2_and_023_metadata():
    config = xinjiang_urumqi_cost_info_source_config()
    parser = config["parser"]["parsers"][XINJIANG_URUMQI_PARSER_VERSION]

    assert config["stable"]["site_id"] == "cost_info.xj.urumqi"
    assert config["stable"]["region_code"] == "650100"
    assert config["stable"]["coverage_region_code"] == "650100"
    assert config["stable"]["publisher_scope"] == "city"
    assert config["price_coordinates"] == {"price_source_type": "info_price", "tax_type": None}
    assert config["source_shape"]["parsability"] == "structured"
    assert parser["areaid"] == XINJIANG_URUMQI_AREA_ID
    assert parser["list_endpoint"].endswith("/Home/GetPoliciesListBy")
    assert "source_priority" not in config["stable"]


def test_xinjiang_area_configs_cover_xjzj_visible_areaid_platform_entries():
    expected = {
        "yili": ("1", "654000", "伊犁哈萨克自治州"),
        "urumqi": ("2", "650100", "乌鲁木齐市"),
        "changji": ("3", "652300", "昌吉回族自治州"),
        "karamay": ("4", "650200", "克拉玛依市"),
        "shihezi": ("5", "659001", "石河子市"),
        "tacheng": ("7", "654200", "塔城地区"),
        "altay": ("8", "654300", "阿勒泰地区"),
        "hami": ("9", "650500", "哈密市"),
        "bayinguoleng": ("10", "652800", "巴音郭楞蒙古自治州"),
        "akesu": ("11", "652900", "阿克苏地区"),
        "kashi": ("12", "653100", "喀什地区"),
        "wujiaqu": ("13", "659004", "五家渠市"),
        "boertala": ("14", "652700", "博尔塔拉蒙古自治州"),
        "kezilesu": ("15", "653000", "克孜勒苏柯尔克孜自治州"),
        "hetian": ("16", "653200", "和田地区"),
        "turpan": ("17", "650400", "吐鲁番市"),
    }

    assert set(XINJIANG_AREA_SOURCE_DEFS) == set(expected)

    for key, (areaid, region_code, city_name) in expected.items():
        config = xinjiang_area_cost_info_source_config(key)
        parser = config["parser"]["parsers"][XINJIANG_ASPNET_AJAX_AREA_PARSER_VERSION]

        assert config["stable"]["site_id"] == f"cost_info.xj.{key}"
        assert config["stable"]["region_code"] == region_code
        assert config["stable"]["coverage_region_code"] == region_code
        target = config["coverage_expectation"]["target_regions"][0]
        assert target["region_code"] == region_code
        assert target["region_name"] == city_name
        assert target["source_audit_status"] == "aspnet_ajax_area_verified"
        assert config["source_shape"]["source_attachment_mode"] == "mixed"
        assert parser["list_strategy"] == "aspnet_ajax_area"
        assert parser["areaid"] == areaid
        assert f"areaid={areaid}" in config["stable"]["entry_url"]


def test_list_xinjiang_area_issues_posts_shared_aspnet_area_api_with_configured_areaid():
    client = FakeXinjiangClient()
    config = xinjiang_area_cost_info_source_config("karamay")
    parser = config["parser"]["parsers"][XINJIANG_ASPNET_AJAX_AREA_PARSER_VERSION]

    result = list_xinjiang_area_issues(client, parser=parser, page=2, page_size=5)

    assert result.total == 101
    posted_url, posted_payload = client.posted[0]
    assert posted_url == parser["list_endpoint"]
    assert posted_payload["guid"] == parser["guid"]
    assert posted_payload["areaid"] == "4"
    assert posted_payload["page"] == "2"
    assert posted_payload["pagesize"] == "5"


def test_list_xinjiang_area_issues_filters_rows_by_configured_area_title_keywords():
    client = FakeXinjiangClient(
        rows=[
            {
                "ID": "6775",
                "Name": "阿拉尔市2026年4月份建设工程综合价格信息",
                "ReleaseDate": "/Date(1780405021000)/",
            },
            {
                "ID": "6774",
                "Name": "阿克苏地区2026年4月份建设工程综合价格信息",
                "ReleaseDate": "/Date(1780404985000)/",
            },
        ]
    )
    config = xinjiang_area_cost_info_source_config("akesu")
    parser = config["parser"]["parsers"][XINJIANG_ASPNET_AJAX_AREA_PARSER_VERSION]

    result = list_xinjiang_area_issues(client, parser=parser, page=1, page_size=20)

    assert [row["ID"] for row in result.rows] == ["6774"]


def test_list_xinjiang_urumqi_issues_posts_official_list_api():
    client = FakeXinjiangClient()
    config = xinjiang_urumqi_cost_info_source_config()
    parser = config["parser"]["parsers"][XINJIANG_URUMQI_PARSER_VERSION]

    result = list_xinjiang_urumqi_issues(client, parser=parser, page=1, page_size=20)

    assert result.total == 101
    assert result.rows[0]["ID"] == "6788"
    posted_url, posted_payload = client.posted[0]
    assert posted_url == parser["list_endpoint"]
    assert posted_payload["guid"] == parser["guid"]
    assert posted_payload["areaid"] == XINJIANG_URUMQI_AREA_ID
    assert posted_payload["page"] == "1"
    assert posted_payload["pagesize"] == "20"


def test_ingest_xinjiang_area_issue_uses_shared_strategy_for_non_urumqi_region(db_session):
    detail_html = """
    <a href="javascript:LookFile('/Upload/File/doc-guid/克拉玛依市2026年4月份建设工程综合价格信息.doc')">doc</a>
    <a href="javascript:LookFile('/Upload/File/xlsx-guid/克拉玛依市2026年4月份建设工程综合价格信息.xlsx')">xlsx</a>
    """
    client = FakeXinjiangClient(detail_html=detail_html)
    config = xinjiang_area_cost_info_source_config("karamay")
    source = create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="新疆工程造价信息网-克拉玛依",
        data_domain="cost_info",
        region_code="650200",
        config=config,
    )

    archive = ingest_xinjiang_area_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row={
            "ID": "6783",
            "Name": "克拉玛依市2026年4月份建设工程综合价格信息（含独山子）",
            "ReleaseDate": "/Date(1780358400000)/",
        },
        parser=config["parser"]["parsers"][XINJIANG_ASPNET_AJAX_AREA_PARSER_VERSION],
        actor_id="xj-area-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    files = (
        db_session.query(ArchiveFile, FileAsset)
        .join(FileAsset, ArchiveFile.file_id == FileAsset.file_id)
        .filter(ArchiveFile.archive_id == archive.archive_id)
        .order_by(ArchiveFile.sort_order)
        .all()
    )

    assert stored.business_key == (
        f"cost_info:{source.source_id}:650200:2026-04:克拉玛依市2026年4月份建设工程综合价格信息 含独山子"
    )
    assert cell_value(stored.metadata_payload["period"]) == "2026-04"
    assert cell_value(stored.metadata_payload["coverage_region_code"]) == "650200"
    assert cell_value(stored.metadata_payload["price_source_type"]) == "info_price"
    assert stored.metadata_payload["tax_type"]["value"] is None
    assert cell_value(stored.metadata_payload["publisher_scope"]) == "city"
    assert cell_value(stored.metadata_payload["parsability"]) == "structured"
    assert [asset.file_ext for _, asset in files] == [".doc", ".xlsx"]
    assert db_session.query(FileProcessing).count() == 0


def test_xinjiang_area_configs_create_full_province_coverage_targets(db_session):
    sources = {}
    for key in XINJIANG_AREA_SOURCE_DEFS:
        config = xinjiang_area_cost_info_source_config(key)
        sources[key] = create_data_source(
            db_session,
            source_scope="platform_public",
            tenant_code=None,
            managed_by="platform",
            source_type="info_price",
            connector_type="source_registry",
            name=f"新疆工程造价信息网-{key}",
            data_domain="cost_info",
            region_code=config["stable"]["region_code"],
            config=config,
        )
    detail_html = """
    <a href="javascript:LookFile('/Upload/File/doc-guid/克拉玛依市2026年4月份建设工程综合价格信息.doc')">doc</a>
    <a href="javascript:LookFile('/Upload/File/xlsx-guid/克拉玛依市2026年4月份建设工程综合价格信息.xlsx')">xlsx</a>
    """
    ingest_xinjiang_area_issue(
        db_session,
        FakeObjectStore(),
        source_id=sources["karamay"].source_id,
        client=FakeXinjiangClient(detail_html=detail_html),
        row={
            "ID": "6783",
            "Name": "克拉玛依市2026年4月份建设工程综合价格信息（含独山子）",
            "ReleaseDate": "/Date(1780358400000)/",
        },
        parser=sources["karamay"].config["parser"]["parsers"][XINJIANG_ASPNET_AJAX_AREA_PARSER_VERSION],
        actor_id="xj-area-test",
    )

    rows = build_coverage_matrix(db_session, start_period="2026-04", end_period="2026-04", province_code="650000")
    by_region = {row.coverage_region_code: row for row in rows}

    assert set(by_region) == {source.region_code for source in sources.values()}
    assert by_region["650200"].business_coverage_status == "covered"
    assert by_region["650200"].source_completeness_status == "city_source_present"
    assert by_region["650200"].source_audit_status == "aspnet_ajax_area_verified"
    assert by_region["650200"].source_visit_url.endswith("areaid=4&tid=c8df326e-cf0e-494d-b643-bd45cdb32773")
    assert by_region["654000"].business_coverage_status == "missing"
    assert by_region["654000"].source_completeness_status == "city_source_present"


def test_ingest_xinjiang_urumqi_issue_downloads_doc_xlsx_originals_only(db_session):
    detail_html = """
    <a href="javascript:LookFile('/Upload/File/doc-guid/乌鲁木齐市2026年4月份建设工程综合价格信息.doc')">doc</a>
    <a href="javascript:LookFile('/Upload/File/xlsx-guid/乌鲁木齐市2026年4月份建设工程综合价格信息.xlsx')">xlsx</a>
    """
    client = FakeXinjiangClient(detail_html=detail_html)
    config = xinjiang_urumqi_cost_info_source_config()
    source = create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="新疆工程造价信息网-乌鲁木齐",
        data_domain="cost_info",
        region_code="650100",
        config=config,
    )

    archive = ingest_xinjiang_urumqi_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row={
            "ID": "6788",
            "Name": "乌鲁木齐市2026年4月份建设工程综合价格信息",
            "ReleaseDate": "/Date(1780358400000)/",
        },
        parser=config["parser"]["parsers"][XINJIANG_URUMQI_PARSER_VERSION],
        actor_id="xj-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    files = (
        db_session.query(ArchiveFile, FileAsset)
        .join(FileAsset, ArchiveFile.file_id == FileAsset.file_id)
        .filter(ArchiveFile.archive_id == archive.archive_id)
        .order_by(ArchiveFile.sort_order)
        .all()
    )

    assert stored.business_key == (
        f"cost_info:{source.source_id}:650100:2026-04:乌鲁木齐市2026年4月份建设工程综合价格信息"
    )
    assert cell_value(stored.metadata_payload["period"]) == "2026-04"
    assert cell_value(stored.metadata_payload["coverage_region_code"]) == "650100"
    assert cell_value(stored.metadata_payload["price_source_type"]) == "info_price"
    assert stored.metadata_payload["tax_type"]["value"] is None
    assert cell_value(stored.metadata_payload["publisher_scope"]) == "city"
    assert cell_value(stored.metadata_payload["parsability"]) == "structured"
    assert cell_value(stored.metadata_payload["source_attachment_mode"]) == "mixed"
    assert [asset.file_ext for _, asset in files] == [".doc", ".xlsx"]
    assert [mounted.is_primary for mounted, _ in files] == [False, True]
    assert client.downloaded == [
        "https://www.xjzj.com/Upload/File/doc-guid/乌鲁木齐市2026年4月份建设工程综合价格信息.doc",
        "https://www.xjzj.com/Upload/File/xlsx-guid/乌鲁木齐市2026年4月份建设工程综合价格信息.xlsx",
    ]
    assert db_session.query(FileProcessing).count() == 0


def test_run_xinjiang_urumqi_incremental_reports_listed_count_and_ingests_once(db_session):
    detail_html = """
    <a href="javascript:LookFile('/Upload/File/doc-guid/乌鲁木齐市2026年4月份建设工程综合价格信息.doc')">doc</a>
    <a href="javascript:LookFile('/Upload/File/xlsx-guid/乌鲁木齐市2026年4月份建设工程综合价格信息.xlsx')">xlsx</a>
    """
    client = FakeXinjiangClient(detail_html=detail_html)
    config = xinjiang_urumqi_cost_info_source_config()
    source = create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="新疆工程造价信息网-乌鲁木齐",
        data_domain="cost_info",
        region_code="650100",
        config=config,
    )

    report = run_xinjiang_urumqi_incremental(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        max_items=1,
        as_of_date="2026-06-22",
    )

    assert report["listed_count"] == 101
    assert report["ingested_count"] == 1
    assert report["skipped_existing_count"] == 0
    assert report["cursor_source_item_key"] == "6788"
    assert report["cursor_publish_date"] == "2026-06-02"
    assert db_session.query(Archive).filter_by(domain_type="cost_info").count() == 1
