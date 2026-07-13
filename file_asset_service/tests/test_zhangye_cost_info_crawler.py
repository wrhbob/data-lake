from app.collection import create_data_source
from app.models import Archive, ArchiveFile, FileAsset, FileProcessing, IngestEvent
from app.storage import FakeObjectStore
from app.zhangye_cost_info import (
    ZHANGYE_COST_INFO_PARSER_VERSION,
    ZHANGYE_LIST_URL,
    ingest_zhangye_cost_info_issue,
    list_zhangye_cost_info_issues,
    zhangye_cost_info_source_config,
)


def cell_value(cell):
    return cell["value"]


class FakeZhangyeCostInfoClient:
    def __init__(self, *, list_html="", detail_html_by_url=None, bytes_by_url=None):
        self.list_html = list_html
        self.detail_html_by_url = detail_html_by_url or {}
        self.bytes_by_url = bytes_by_url or {}
        self.get_text_calls = []
        self.downloaded = []

    def get_text(self, url):
        self.get_text_calls.append(url)
        if url == ZHANGYE_LIST_URL:
            return self.list_html
        if url in self.detail_html_by_url:
            return self.detail_html_by_url[url]
        raise AssertionError(f"unexpected GET: {url}")

    def get_bytes(self, url):
        self.downloaded.append(url)
        if url in self.bytes_by_url:
            return self.bytes_by_url[url], "application/zip"
        if url.endswith(".zip"):
            return b"PK\x03\x04 zhangye zip package", "application/zip"
        raise AssertionError(f"unexpected download: {url}")


def test_zhangye_source_config_marks_city_bimonthly_zip_package_source():
    config = zhangye_cost_info_source_config()
    parser = config["parser"]["parsers"][ZHANGYE_COST_INFO_PARSER_VERSION]
    target = config["coverage_expectation"]["target_regions"][0]

    assert config["stable"]["site_id"] == "cost_info.gs.zhangye.zjj.price_info"
    assert config["stable"]["region_code"] == "620700"
    assert config["stable"]["coverage_region_code"] == "620700"
    assert config["stable"]["publisher_scope"] == "city"
    assert config["stable"]["publisher_name"] == "张掖市住房和城乡建设局"
    assert config["stable"]["publisher_type"] == "official_housing_urban_rural_development"
    assert config["stable"]["period_kind"] == "bimonthly"
    assert config["price_coordinates"]["price_kind"] == "guidance"
    assert parser["adapter_kind"] == "zhangye_zip"
    assert parser["list_strategy"] == "static_list_detail_zip_anchor"
    assert parser["detail_strategy"] == "article_detail_zip_anchor"
    assert parser["period"]["kind"] == "bimonthly"
    assert config["source_shape"]["source_attachment_mode"] == "zip_package"
    assert config["source_shape"]["parsability"] == "opaque_pdf_package"
    assert target["region_code"] == "620700"
    assert target["region_name"] == "张掖市"
    assert target["target_level"] == "city"


def test_zhangye_list_parses_notice_list_and_filters_non_cost_info_notices():
    client = FakeZhangyeCostInfoClient(list_html=_zhangye_list_html())
    parser = zhangye_cost_info_source_config()["parser"]["parsers"][ZHANGYE_COST_INFO_PARSER_VERSION]

    rows = list_zhangye_cost_info_issues(client, parser=parser, max_items=5)

    assert rows == [
        {
            "source_item_key": "1537621",
            "title": "张掖市2026年3-4月建设工程材料信息价及人工信息价",
            "source_title": "张掖市住房和城乡建设局 关于发布《张掖市2026年3-4月建设工程材料信息价及人工信息价》的通知",
            "period": "2026年3-4月",
            "period_year": 2026,
            "period_start_month": 3,
            "period_end_month": 4,
            "period_start": "2026-03",
            "period_end": "2026-04",
            "publish_date": "2026-04-28",
            "detail_url": "https://www.zhangye.gov.cn/jsj/dzdt/tzgg/202604/t20260428_1537621.html",
        }
    ]


def test_zhangye_detail_zip_attachment_ingests_bimonthly_archive_without_processing(db_session):
    detail_url = "https://www.zhangye.gov.cn/jsj/dzdt/tzgg/202604/t20260428_1537621.html"
    zip_url = "https://www.zhangye.gov.cn/jsj/dzdt/tzgg/202604/P020260428660861964604.zip"
    client = FakeZhangyeCostInfoClient(
        list_html=_zhangye_list_html(),
        detail_html_by_url={detail_url: _zhangye_detail_html()},
        bytes_by_url={zip_url: b"PK\x03\x04 zhangye 2026 3-4 zip"},
    )
    config = zhangye_cost_info_source_config()
    parser = config["parser"]["parsers"][ZHANGYE_COST_INFO_PARSER_VERSION]
    source = _create_zhangye_source(db_session, config=config)
    row = list_zhangye_cost_info_issues(client, parser=parser, max_items=1)[0]

    archive = ingest_zhangye_cost_info_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=row,
        actor_id="zhangye-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    event = db_session.query(IngestEvent).filter_by(source_item_key=row["source_item_key"]).one()
    files = (
        db_session.query(ArchiveFile, FileAsset)
        .join(FileAsset, ArchiveFile.file_id == FileAsset.file_id)
        .filter(ArchiveFile.archive_id == archive.archive_id)
        .all()
    )

    assert stored.source_item_key == "1537621"
    assert event.source_item_key == stored.source_item_key
    assert stored.business_key == (
        f"cost_info:{source.source_id}:620700:2026-03:"
        "张掖市2026年3-4月建设工程材料信息价及人工信息价"
    )
    assert stored.region_code == "620700"
    assert stored.period_kind == "bimonthly"
    assert stored.price_kind == "guidance"
    assert cell_value(stored.metadata_payload["period"]) == "2026年3-4月"
    assert cell_value(stored.metadata_payload["period_raw"]) == "2026年3-4月"
    assert cell_value(stored.metadata_payload["period_start"]) == "2026-03"
    assert cell_value(stored.metadata_payload["period_year"]) == 2026
    assert cell_value(stored.metadata_payload["period_start_month"]) == 3
    assert cell_value(stored.metadata_payload["period_end_month"]) == 4
    assert cell_value(stored.metadata_payload["source_title"]) == row["source_title"]
    assert cell_value(stored.metadata_payload["publisher_type"]) == "official_housing_urban_rural_development"
    assert cell_value(stored.metadata_payload["publisher_scope"]) == "city"
    assert cell_value(stored.metadata_payload["source_attachment_mode"]) == "zip_package"
    assert cell_value(stored.metadata_payload["publication_mode"]) == "DETAIL_PAGE_ATTACHMENT"
    assert cell_value(stored.metadata_payload["parsability"]) == "opaque_pdf_package"
    assert cell_value(stored.metadata_payload["price_source_type"]) == "info_price"
    assert "参考" in cell_value(stored.metadata_payload["price_kind_basis"])
    assert cell_value(stored.metadata_payload["attachment_discovery_methods"]) == ["detail_page_zip_anchor"]
    assert cell_value(stored.metadata_payload["opaque_package"]) is True
    assert [(mounted.file_role, asset.file_ext) for mounted, asset in files] == [("zip_package", ".zip")]
    assert client.get_text_calls == [ZHANGYE_LIST_URL, detail_url]
    assert client.downloaded == [zip_url]
    assert db_session.query(FileProcessing).count() == 0


def _zhangye_list_html():
    return """
    <ul>
      <li>
        <a href="./202606/t20260605_1554321.html">
          张掖市住房和城乡建设局 关于开展安全生产检查的通知</TRS_DOCUMENT>
          <span>2026-06-05</span>
        </a>
      </li>
      <li>
        <a href="./202604/t20260428_1537621.html">
          张掖市住房和城乡建设局 关于发布《张掖市2026年3-4月建设工程材料信息价及人工信息价》的通知</TRS_DOCUMENT>
          <span>2026-04-28</span>
        </a>
      </li>
      <li>
        <a href="./202604/t20260428_1537623.html">
          2026年市本级公共租赁住房申请家庭公示名单（第五批）</TRS_DOCUMENT>
          <span>2026-04-28</span>
        </a>
      </li>
    </ul>
    """


def _zhangye_detail_html():
    return """
    <html><head>
      <meta name="ArticleTitle" content="张掖市住房和城乡建设局 关于发布《张掖市2026年3-4月建设工程材料信息价及人工信息价》的通知">
      <meta name="PubDate" content="2026-04-28 18:20">
      <meta name="ContentSource" content="张掖市住房和城乡建设局">
    </head><body>
      <div id="zoom">
        <p>现将编制完成的张掖市2026年3－4月建设工程材料信息价及人工信息价予以发布，供有关单位参考。</p>
        <p>附件：<a href="./P020260428660861964604.zip">2026年3-4月信息价.zip</a></p>
      </div>
    </body></html>
    """


def _create_zhangye_source(db_session, *, config):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="张掖市住房和城乡建设局-信息价",
        data_domain="cost_info",
        province="甘肃省",
        city="张掖市",
        region_code="620700",
        config=config,
    )
