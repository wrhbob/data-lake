from app.collection import create_data_source
from app.models import Archive, FileProcessing, IngestEvent
from app.shaanxi_cost_info import (
    SHAANXI_COST_INFO_PARSER_VERSION,
    SHAANXI_LIST_URL,
    ingest_shaanxi_cost_info_issue,
    list_shaanxi_cost_info_issues,
    shaanxi_cost_info_source_config,
)
from app.storage import FakeObjectStore


def cell_value(cell):
    return cell["value"]


class FakeShaanxiCostInfoClient:
    def __init__(self, *, html_by_url, bytes_by_url=None):
        self.html_by_url = html_by_url
        self.bytes_by_url = bytes_by_url or {}
        self.get_text_calls = []
        self.downloaded = []

    def get_text(self, url):
        self.get_text_calls.append(url)
        if url in self.html_by_url:
            return self.html_by_url[url]
        raise AssertionError(f"unexpected GET: {url}")

    def get_bytes(self, url):
        self.downloaded.append(url)
        if url in self.bytes_by_url:
            return self.bytes_by_url[url], "application/pdf"
        if url.endswith(".pdf"):
            return b"%PDF shaanxi province material info price", "application/pdf"
        raise AssertionError(f"unexpected download: {url}")


def test_shaanxi_source_config_marks_province_monthly_mixed_material_info_price():
    config = shaanxi_cost_info_source_config()
    parser = config["parser"]["parsers"][SHAANXI_COST_INFO_PARSER_VERSION]
    target = config["coverage_expectation"]["target_regions"][0]

    assert config["stable"]["site_id"] == "cost_info.sn.province.material_info_price"
    assert config["stable"]["region_code"] == "610000"
    assert config["stable"]["coverage_region_code"] == "610000"
    assert config["stable"]["publisher_scope"] == "province"
    assert config["stable"]["publisher_name"] == "陕西省建设工程造价服务中心"
    assert config["stable"]["publisher_type"] == "cost_station_public_institution"
    assert config["stable"]["period_kind"] == "monthly"
    assert config["price_coordinates"]["price_kind"] == "unspecified"
    assert "生产、销售企业报价" in config["price_coordinates"]["price_kind_basis"]
    assert parser["list_strategy"] == "static_html_paginated_article_list"
    assert parser["detail_strategy"] == "article_detail_pdf_anchor"
    assert parser["period"]["kind"] == "monthly"
    assert target["region_code"] == "610000"
    assert target["region_name"] == "陕西省"
    assert target["target_level"] == "province"


def test_shaanxi_list_filters_city_journals_and_keeps_province_material_info_price():
    client = FakeShaanxiCostInfoClient(
        html_by_url={
            SHAANXI_LIST_URL: _shaanxi_list_page_1(),
            "https://js.shaanxi.gov.cn/sy/yw/zjglfw/zjxx/index_1.html": _shaanxi_list_page_2(),
        }
    )
    parser = shaanxi_cost_info_source_config()["parser"]["parsers"][SHAANXI_COST_INFO_PARSER_VERSION]

    rows = list_shaanxi_cost_info_issues(client, parser=parser, max_pages=2, max_items=5)

    assert [row["period"] for row in rows] == ["2026-05", "2026-04", "2026-03", "2026-02", "2026-01"]
    assert rows[0]["title"] == "陕西工程造价信息2026年5月材料信息价"
    assert rows[0]["source_title"] == "2026年5月材料信息价"
    assert rows[0]["period_year"] == 2026
    assert rows[0]["period_month"] == 5
    assert rows[0]["publish_date"] == "2026-06-01"
    assert rows[0]["source_item_key"] == "3643169"
    assert rows[0]["detail_url"] == "https://js.shaanxi.gov.cn/sy/yw/zjglfw/zjxx/202606/t20260601_3643169.html"
    assert all("设区市造价信息" not in row["source_title"] for row in rows)


def test_shaanxi_detail_pdf_attachment_ingests_monthly_mixed_archive_without_processing(db_session):
    detail_url = "https://js.shaanxi.gov.cn/sy/yw/zjglfw/zjxx/202606/t20260601_3643169.html"
    pdf_url = "https://js.shaanxi.gov.cn/sy/yw/zjglfw/zjxx/202606/P020260601322559475097.pdf"
    client = FakeShaanxiCostInfoClient(
        html_by_url={
            SHAANXI_LIST_URL: _shaanxi_list_page_1(),
            detail_url: _shaanxi_detail_html(),
        },
        bytes_by_url={pdf_url: b"%PDF shaanxi 2026 05 province material info price"},
    )
    config = shaanxi_cost_info_source_config()
    parser = config["parser"]["parsers"][SHAANXI_COST_INFO_PARSER_VERSION]
    source = _create_shaanxi_source(db_session, config=config)
    row = list_shaanxi_cost_info_issues(client, parser=parser, max_pages=1, max_items=1)[0]

    archive = ingest_shaanxi_cost_info_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=row,
        actor_id="shaanxi-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    event = db_session.query(IngestEvent).filter_by(source_item_key=row["source_item_key"]).one()

    assert stored.source_item_key == "3643169"
    assert event.source_item_key == stored.source_item_key
    assert stored.region_code == "610000"
    assert stored.period_kind == "monthly"
    assert stored.price_kind == "unspecified"
    assert cell_value(stored.metadata_payload["period"]) == "2026-05"
    assert cell_value(stored.metadata_payload["period_raw"]) == stored.title
    assert cell_value(stored.metadata_payload["period_start"]) == "2026-05"
    assert cell_value(stored.metadata_payload["period_year"]) == 2026
    assert cell_value(stored.metadata_payload["period_month"]) == 5
    assert cell_value(stored.metadata_payload["source_title"]) == "2026年5月材料信息价"
    assert cell_value(stored.metadata_payload["publisher_type"]) == "cost_station_public_institution"
    assert cell_value(stored.metadata_payload["publisher_scope"]) == "province"
    assert cell_value(stored.metadata_payload["source_attachment_mode"]) == "pdf_attachment"
    assert cell_value(stored.metadata_payload["publication_mode"]) == "DETAIL_PAGE_ATTACHMENT"
    assert cell_value(stored.metadata_payload["price_source_type"]) == "info_price"
    assert "未文档级分离" in cell_value(stored.metadata_payload["price_kind_basis"])
    assert cell_value(stored.metadata_payload["attachment_discovery_methods"]) == ["detail_page_pdf_anchor"]
    assert client.get_text_calls == [SHAANXI_LIST_URL, detail_url]
    assert client.downloaded == [pdf_url]
    assert db_session.query(FileProcessing).count() == 0


def _shaanxi_list_page_1():
    return """
    <ul class="cm-news-list gl-news-list">
      <li class="clearfix">
        <a href="./202606/t20260623_3649700.html" title="设区市造价信息---《汉中建设工程造价信息》2026年第5期">设区市造价信息---《汉中建设工程造价信息》2026年第5期</a>
        <span>2026-06-23</span>
      </li>
      <li class="clearfix">
        <a href="./202606/t20260601_3643169.html" title="2026年5月材料信息价">2026年5月材料信息价</a>
        <span>2026-06-01</span>
      </li>
      <li class="clearfix">
        <a href="./202604/t20260430_3634612.html" title="2026年4月材料信息价">2026年4月材料信息价</a>
        <span>2026-04-30</span>
      </li>
    </ul>
    """


def _shaanxi_list_page_2():
    return """
    <ul class="cm-news-list gl-news-list">
      <li class="clearfix">
        <a href="./202603/t20260331_3626149.html" title="2026年3月材料信息价">2026年3月材料信息价</a>
        <span>2026-03-31</span>
      </li>
      <li class="clearfix">
        <a href="./202603/t20260302_3616243.html" title="2026年2月材料信息价">2026年2月材料信息价</a>
        <span>2026-03-02</span>
      </li>
      <li class="clearfix">
        <a href="./202601/t20260130_3609314.html" title="2026年1月材料信息价">2026年1月材料信息价</a>
        <span>2026-01-30</span>
      </li>
      <li class="clearfix">
        <a href="./202601/t20260112_3604218.html" title="设区市造价信息---《西安工程造价信息》2025年12月">设区市造价信息---《西安工程造价信息》2025年12月</a>
        <span>2026-01-12</span>
      </li>
    </ul>
    """


def _shaanxi_detail_html():
    return """
    <html><head>
      <meta name="Description" content="2026年5月材料信息价.pdf">
      <meta name="ArticleTitle" content="2026年5月材料信息价">
      <meta name="ContentSource" content="省造价中心">
    </head><body>
      <script>if ("省造价中心" != "") { document.write('<span class="cm-con">省造价中心</span>'); }</script>
      <div class="view TRS_UEDITOR trs_paper_default trs_web">
        <p class="insertfileTag">
          <a appendix="true" data-appendix="true" needdownload="true"
             href="./P020260601322559475097.pdf"
             title="2026年5月材料信息价.pdf"
             download="2026年5月材料信息价.pdf"
             OLDSRC="/protect/P0202606/P020260601/P020260601322559475097.pdf">2026年5月材料信息价.pdf</a>
        </p>
      </div>
    </body></html>
    """


def _create_shaanxi_source(db_session, *, config):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="陕西省建设工程造价服务中心-材料信息价",
        data_domain="cost_info",
        province="陕西省",
        city=None,
        region_code="610000",
        config=config,
    )
