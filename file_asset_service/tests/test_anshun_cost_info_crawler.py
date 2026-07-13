from app.anshun_cost_info import (
    ANSHUN_COST_INFO_PARSER_VERSION,
    ANSHUN_LIST_URL,
    anshun_cost_info_source_config,
    ingest_anshun_cost_info_issue,
    list_anshun_cost_info_issues,
)
from app.collection import create_data_source
from app.models import Archive, FileProcessing, IngestEvent
from app.storage import FakeObjectStore


def cell_value(cell):
    return cell["value"]


class FakeAnshunCostInfoClient:
    def __init__(self, *, list_html, detail_html_by_url, bytes_by_url=None):
        self.list_html = list_html
        self.detail_html_by_url = detail_html_by_url
        self.bytes_by_url = bytes_by_url or {}
        self.get_text_calls = []
        self.downloaded = []

    def get_text(self, url):
        self.get_text_calls.append(url)
        if url == ANSHUN_LIST_URL:
            return self.list_html
        if url in self.detail_html_by_url:
            return self.detail_html_by_url[url]
        raise AssertionError(f"unexpected GET: {url}")

    def get_bytes(self, url):
        self.downloaded.append(url)
        if url in self.bytes_by_url:
            return self.bytes_by_url[url], "application/vnd.ms-excel"
        if url.endswith(".xls"):
            return b"\xd0\xcf\x11\xe0 anshun monthly xls bytes", "application/vnd.ms-excel"
        raise AssertionError(f"unexpected download: {url}")


def test_anshun_source_config_marks_city_monthly_guidance_official_xls_source():
    config = anshun_cost_info_source_config()
    parser = config["parser"]["parsers"][ANSHUN_COST_INFO_PARSER_VERSION]
    target = config["coverage_expectation"]["target_regions"][0]

    assert config["stable"]["site_id"] == "cost_info.gz.anshun.material_reference_price"
    assert config["stable"]["region_code"] == "520400"
    assert config["stable"]["coverage_region_code"] == "520400"
    assert config["stable"]["publisher_scope"] == "city"
    assert config["stable"]["publisher_name"] == "安顺市住房和城乡建设局"
    assert config["stable"]["publisher_type"] == "official_housing_urban_rural_development"
    assert config["stable"]["period_kind"] == "monthly"
    assert config["price_coordinates"]["price_kind"] == "guidance"
    assert "市住建局（市场科）" in config["price_coordinates"]["price_kind_basis"]
    assert parser["list_strategy"] == "static_html_single_page_article_list"
    assert parser["detail_strategy"] == "article_detail_xls_anchor"
    assert parser["attachments"]["allowed"] == ["xls"]
    assert target["region_code"] == "520400"
    assert target["region_name"] == "安顺市"
    assert target["target_level"] == "city"


def test_anshun_list_filters_mixed_building_management_feed_to_monthly_material_reference_xls_rows():
    client = FakeAnshunCostInfoClient(
        list_html=_anshun_list_html(),
        detail_html_by_url={},
    )
    parser = anshun_cost_info_source_config()["parser"]["parsers"][ANSHUN_COST_INFO_PARSER_VERSION]

    rows = list_anshun_cost_info_issues(client, parser=parser, max_items=5)

    assert [row["period"] for row in rows] == ["2026-05", "2026-04", "2026-03"]
    assert rows[0]["title"] == "2026年5月份安顺市区主要建筑安装材料市场综合参考价"
    assert rows[0]["source_item_key"] == "90518367"
    assert rows[0]["period_year"] == 2026
    assert rows[0]["period_month"] == 5
    assert rows[0]["publish_date"] == "2026-06-12"
    assert rows[0]["detail_url"] == (
        "http://zfcx.anshun.gov.cn/zwgk/zfxxgk/xxgkml/cxjs/jzgl_84084/202606/t20260612_90518367.html"
    )
    assert client.get_text_calls == [ANSHUN_LIST_URL]


def test_anshun_detail_xls_ingests_layer0_archive_without_processing(db_session):
    detail_url = "http://zfcx.anshun.gov.cn/zwgk/zfxxgk/xxgkml/cxjs/jzgl_84084/202606/t20260612_90518367.html"
    xls_url = "http://zfcx.anshun.gov.cn/zwgk/zfxxgk/xxgkml/cxjs/jzgl_84084/202606/P020260612389613086419.xls"
    client = FakeAnshunCostInfoClient(
        list_html=_anshun_list_html(),
        detail_html_by_url={detail_url: _anshun_detail_html()},
        bytes_by_url={xls_url: b"\xd0\xcf\x11\xe0 anshun 2026-05 material price xls"},
    )
    config = anshun_cost_info_source_config()
    parser = config["parser"]["parsers"][ANSHUN_COST_INFO_PARSER_VERSION]
    source = _create_anshun_source(db_session, config=config)
    row = list_anshun_cost_info_issues(client, parser=parser, max_items=1)[0]

    archive = ingest_anshun_cost_info_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=row,
        actor_id="anshun-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    event = db_session.query(IngestEvent).filter_by(source_item_key=row["source_item_key"]).one()

    assert stored.source_item_key == "90518367"
    assert event.source_item_key == stored.source_item_key
    assert stored.region_code == "520400"
    assert stored.period_kind == "monthly"
    assert stored.price_kind == "guidance"
    assert cell_value(stored.metadata_payload["period"]) == "2026-05"
    assert cell_value(stored.metadata_payload["period_raw"]) == "2026年5月份安顺市区主要建筑安装材料市场综合参考价"
    assert cell_value(stored.metadata_payload["period_start"]) == "2026-05"
    assert cell_value(stored.metadata_payload["period_year"]) == 2026
    assert cell_value(stored.metadata_payload["period_month"]) == 5
    assert cell_value(stored.metadata_payload["publisher_type"]) == "official_housing_urban_rural_development"
    assert cell_value(stored.metadata_payload["publisher_scope"]) == "city"
    assert cell_value(stored.metadata_payload["source_attachment_mode"]) == "xls_attachment"
    assert cell_value(stored.metadata_payload["publication_mode"]) == "DETAIL_PAGE_ATTACHMENT"
    assert cell_value(stored.metadata_payload["parsability"]) == "opaque_spreadsheet"
    assert cell_value(stored.metadata_payload["price_source_type"]) == "info_price"
    assert "市住建局（市场科）" in cell_value(stored.metadata_payload["price_kind_basis"])
    assert cell_value(stored.metadata_payload["attachment_discovery_methods"]) == ["detail_page_xls_anchor"]
    assert client.get_text_calls == [ANSHUN_LIST_URL, detail_url]
    assert client.downloaded == [xls_url]
    assert db_session.query(FileProcessing).count() == 0


def _anshun_list_html():
    return """
    <html><body>
      <div class="zfxxgk_zdgkc">
        <ul>
          <li><a target="_blank" href="http://zfcx.anshun.gov.cn/zwgk/zfxxgk/xxgkml/cxjs/jzgl_84084/202606/t20260612_90518367.html" title="2026年5月份安顺市区主要建筑安装材料市场综合参考价">2026年5月份安顺市区主要建筑安装材料市场综合参考价</a><b>2026-06-12</b></li>
          <li><a target="_blank" href="http://zfcx.anshun.gov.cn/zwgk/zfxxgk/xxgkml/cxjs/jzgl_84084/202606/t20260602_90238533.html" title="安顺市住房和城乡建设局行政处罚台账(2026年第一批）">安顺市住房和城乡建设局行政处罚台账(2026年第一批）</a><b>2026-06-02</b></li>
          <li><a target="_blank" href="http://zfcx.anshun.gov.cn/zwgk/zfxxgk/xxgkml/cxjs/jzgl_84084/202605/t20260512_90162539.html" title="2026年4月份安顺市区主要建筑安装材料市场综合参考价">2026年4月份安顺市区主要建筑安装材料市场综合参考价</a><b>2026-05-12</b></li>
          <li><a target="_blank" href="http://zfcx.anshun.gov.cn/zwgk/zfxxgk/xxgkml/cxjs/jzgl_84084/202604/t20260421_90026917.html" title="2026年3月份安顺市区主要建筑安装材料市场综合参考价">2026年3月份安顺市区主要建筑安装材料市场综合参考价</a><b>2026-04-21</b></li>
          <li><a target="_blank" href="http://zfcx.anshun.gov.cn/zwgk/zfxxgk/xxgkml/cxjs/jzgl_84084/202603/t20260330_89923648.html" title="关于在招标投标活动过程中不符合招投标相关管理要求予以警示的公示">关于在招标投标活动过程中不符合招投标相关管理要求予以警示的公示</a><b>2026-03-30</b></li>
        </ul>
      </div>
      <script>createPageHTML(28, 0, "index", "html")</script>
    </body></html>
    """


def _anshun_detail_html():
    return """
    <html>
      <head>
        <meta name="ArticleTitle" content="2026年5月份安顺市区主要建筑安装材料市场综合参考价" />
        <meta name="ContentSource" content="市住建局（市场科）"/>
        <meta name="Description" content="2026年5月份安顺市区主要建筑安装材料市场综合参考价.xls" />
      </head>
      <body>
        <h2>2026年5月份安顺市区主要建筑安装材料市场综合参考价</h2>
        <a appendix="true" data-appendix="true" needdownload="true" href="./P020260612389613086419.xls" title="2026年5月份安顺市区主要建筑安装材料市场综合参考价.xls" download="2026年5月份安顺市区主要建筑安装材料市场综合参考价.xls">2026年5月份安顺市区主要建筑安装材料市场综合参考价.xls</a>
      </body>
    </html>
    """


def _create_anshun_source(db_session, *, config):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="安顺市住房和城乡建设局-主要建筑安装材料市场综合参考价",
        data_domain="cost_info",
        province="贵州省",
        city="安顺市",
        region_code="520400",
        config=config,
    )
