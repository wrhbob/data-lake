from app.collection import create_data_source
from app.jiuquan_cost_info import (
    JIUQUAN_COST_INFO_PARSER_VERSION,
    JIUQUAN_LIST_URL,
    ingest_jiuquan_cost_info_issue,
    jiuquan_cost_info_source_config,
    list_jiuquan_cost_info_issues,
)
from app.models import Archive, FileProcessing, IngestEvent
from app.storage import FakeObjectStore


def cell_value(cell):
    return cell["value"]


class FakeJiuquanCostInfoClient:
    def __init__(self, *, list_html="", detail_html_by_url=None, bytes_by_url=None):
        self.list_html = list_html
        self.detail_html_by_url = detail_html_by_url or {}
        self.bytes_by_url = bytes_by_url or {}
        self.get_text_calls = []
        self.downloaded = []

    def get_text(self, url):
        self.get_text_calls.append(url)
        if url == JIUQUAN_LIST_URL:
            return self.list_html
        if url in self.detail_html_by_url:
            return self.detail_html_by_url[url]
        raise AssertionError(f"unexpected GET: {url}")

    def get_bytes(self, url):
        self.downloaded.append(url)
        if url in self.bytes_by_url:
            return self.bytes_by_url[url], "application/pdf"
        if url.endswith(".pdf"):
            return b"%PDF jiuquan issue bytes", "application/pdf"
        raise AssertionError(f"unexpected download: {url}")


def test_jiuquan_source_config_marks_city_issue_based_guidance_pdf_source():
    config = jiuquan_cost_info_source_config()
    parser = config["parser"]["parsers"][JIUQUAN_COST_INFO_PARSER_VERSION]
    target = config["coverage_expectation"]["target_regions"][0]

    assert config["stable"]["site_id"] == "cost_info.gs.jiuquan.zjj.price_info"
    assert config["stable"]["region_code"] == "620900"
    assert config["stable"]["coverage_region_code"] == "620900"
    assert config["stable"]["publisher_scope"] == "city"
    assert config["stable"]["publisher_name"] == "酒泉市住房和城乡建设局"
    assert config["stable"]["publisher_type"] == "official_housing_urban_rural_development"
    assert config["stable"]["period_kind"] == "issue_based"
    assert config["price_coordinates"]["price_kind"] == "guidance"
    assert config["price_coordinates"]["tax_type"] is None
    assert parser["adapter_kind"] == "jiuquan_pdf"
    assert parser["list_strategy"] == "static_list_detail_pdf_anchor"
    assert parser["detail_strategy"] == "article_detail_pdf_anchor"
    assert parser["period"]["kind"] == "issue_based"
    assert target["region_code"] == "620900"
    assert target["region_name"] == "酒泉市"
    assert target["target_level"] == "city"


def test_jiuquan_list_parses_notice_list_and_filters_non_cost_info_notices():
    client = FakeJiuquanCostInfoClient(list_html=_jiuquan_list_html())
    parser = jiuquan_cost_info_source_config()["parser"]["parsers"][JIUQUAN_COST_INFO_PARSER_VERSION]

    rows = list_jiuquan_cost_info_issues(client, parser=parser, max_items=5)

    assert rows == [
        {
            "source_item_key": "7c0f7f036f8347a0bbfae8649cede720",
            "title": "酒泉市2026年第2期建设工程人工市场信息价及材料信息价格",
            "source_title": "酒泉市住房和城乡建设局关于发布酒泉市2026年第二期建设工程人工市场信息价及材料信息价格的通知",
            "period": "2026年第2期",
            "period_year": 2026,
            "period_issue_no": 2,
            "publish_date": "2026-04-30",
            "detail_url": "https://zjj.jiuquan.gov.cn/zjj/c106845/202604/7c0f7f036f8347a0bbfae8649cede720.shtml",
        }
    ]


def test_jiuquan_detail_pdf_attachment_ingests_issue_based_archive_without_processing(db_session):
    detail_url = "https://zjj.jiuquan.gov.cn/zjj/c106845/202604/7c0f7f036f8347a0bbfae8649cede720.shtml"
    pdf_url = (
        "https://zjj.jiuquan.gov.cn/zjj/c106845/202604/"
        "7c0f7f036f8347a0bbfae8649cede720/files/"
        "关于发布酒泉市2026年第二期建设工程材料信息价格及人工信息价的通知.pdf"
    )
    client = FakeJiuquanCostInfoClient(
        list_html=_jiuquan_list_html(),
        detail_html_by_url={detail_url: _jiuquan_detail_html()},
        bytes_by_url={pdf_url: b"%PDF jiuquan 2026 issue 2"},
    )
    config = jiuquan_cost_info_source_config()
    parser = config["parser"]["parsers"][JIUQUAN_COST_INFO_PARSER_VERSION]
    source = _create_jiuquan_source(db_session, config=config)
    row = list_jiuquan_cost_info_issues(client, parser=parser, max_items=1)[0]

    archive = ingest_jiuquan_cost_info_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=row,
        actor_id="jiuquan-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    event = db_session.query(IngestEvent).filter_by(source_item_key=row["source_item_key"]).one()

    assert stored.source_item_key == "7c0f7f036f8347a0bbfae8649cede720"
    assert event.source_item_key == stored.source_item_key
    assert stored.region_code == "620900"
    assert stored.period_kind == "issue_based"
    assert stored.price_kind == "guidance"
    assert cell_value(stored.metadata_payload["period"]) == "2026年第2期"
    assert cell_value(stored.metadata_payload["period_raw"]) == "2026年第2期"
    assert cell_value(stored.metadata_payload["period_year"]) == 2026
    assert cell_value(stored.metadata_payload["period_issue_no"]) == 2
    assert cell_value(stored.metadata_payload["source_title"]) == row["source_title"]
    assert cell_value(stored.metadata_payload["publisher_type"]) == "official_housing_urban_rural_development"
    assert cell_value(stored.metadata_payload["publisher_scope"]) == "city"
    assert cell_value(stored.metadata_payload["source_attachment_mode"]) == "pdf_attachment"
    assert cell_value(stored.metadata_payload["publication_mode"]) == "DETAIL_PAGE_ATTACHMENT"
    assert cell_value(stored.metadata_payload["price_source_type"]) == "info_price"
    assert "计价参考" in cell_value(stored.metadata_payload["price_kind_basis"])
    assert cell_value(stored.metadata_payload["attachment_discovery_methods"]) == ["detail_page_pdf_anchor"]
    assert client.get_text_calls == [JIUQUAN_LIST_URL, detail_url]
    assert client.downloaded == [pdf_url]
    assert db_session.query(FileProcessing).count() == 0


def _jiuquan_list_html():
    return """
    <ul id="list">
      <li>
        <a href="/zjj/c106845/202606/75cb151747604fed82d562ae68caa228.shtml"
           target="_blank" title='酒泉市住房和城乡建设局生态环境保护责任清单'>责任清单</a>
        <span>2026-06-10</span>
      </li>
      <li>
        <a href="/zjj/c106845/202604/7c0f7f036f8347a0bbfae8649cede720.shtml"
           target="_blank" title='酒泉市住房和城乡建设局关于发布酒泉市2026年第二期建设工程人工市场信息价及材料信息价格的通知'>
          酒泉市住房和城乡建设局关于发布酒泉市2026年第二期建设工程人工市场信息价及材料信息价格的通知
        </a>
        <span>2026-04-30</span>
      </li>
      <li>
        <a href="/zjj/c106845/202604/15f2d38b20234bda99bbaa3cb43caa99.shtml"
           target="_blank" title='酒泉城区西北片区地下管网互联互通及配套设施建设项目绩效评价'>项目绩效评价</a>
        <span>2026-04-21</span>
      </li>
    </ul>
    """


def _jiuquan_detail_html():
    return """
    <html><head>
      <meta name="ArticleTitle" content="酒泉市住房和城乡建设局关于发布酒泉市2026年第二期建设工程人工市场信息价及材料信息价格的通知">
      <meta name="ContentSource" content="市造价站">
    </head><body>
      <div class="pages_content" id="UCAP-CONTENT">
        <p>本信息价格仅作为编制工程概预算、招标控制价等的计价参考。</p>
        <p>附件：1.酒泉市2026年第二期建设工程人工市场信息价</p>
        <p>2.酒泉市2026年第二期建设工程材料信息价格</p>
        <p><img src="7c0f7f036f8347a0bbfae8649cede720/images/pdf.gif">
          <a href="7c0f7f036f8347a0bbfae8649cede720/files/关于发布酒泉市2026年第二期建设工程材料信息价格及人工信息价的通知.pdf">
            关于发布酒泉市2026年第二期建设工程材料信息价格及人工信息价的通知
          </a>
        </p>
      </div>
    </body></html>
    """


def _create_jiuquan_source(db_session, *, config):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="酒泉市住房和城乡建设局-信息价",
        data_domain="cost_info",
        province="甘肃省",
        city="酒泉市",
        region_code="620900",
        config=config,
    )
