from app.collection import create_data_source
from app.lanzhou_cost_info import (
    LANZHOU_COST_INFO_PARSER_VERSION,
    LANZHOU_LIST_URL,
    ingest_lanzhou_cost_info_issue,
    lanzhou_cost_info_source_config,
    list_lanzhou_cost_info_issues,
)
from app.models import Archive, FileProcessing, IngestEvent
from app.storage import FakeObjectStore


def cell_value(cell):
    return cell["value"]


class FakeLanzhouCostInfoClient:
    def __init__(self, *, list_html, detail_html_by_url, bytes_by_url=None):
        self.list_html = list_html
        self.detail_html_by_url = detail_html_by_url
        self.bytes_by_url = bytes_by_url or {}
        self.get_text_calls = []
        self.downloaded = []

    def get_text(self, url):
        self.get_text_calls.append(url)
        if url == LANZHOU_LIST_URL:
            return self.list_html
        if url in self.detail_html_by_url:
            return self.detail_html_by_url[url]
        raise AssertionError(f"unexpected GET: {url}")

    def get_bytes(self, url):
        self.downloaded.append(url)
        if url in self.bytes_by_url:
            return self.bytes_by_url[url], "application/pdf;charset=UTF-8"
        if url.endswith(".pdf") or "downfile.jsp" in url:
            return b"%PDF lanzhou issue bytes", "application/pdf;charset=UTF-8"
        raise AssertionError(f"unexpected download: {url}")


def test_lanzhou_source_config_marks_city_issue_based_mixed_cost_station_journal():
    config = lanzhou_cost_info_source_config()
    parser = config["parser"]["parsers"][LANZHOU_COST_INFO_PARSER_VERSION]
    target = config["coverage_expectation"]["target_regions"][0]

    assert config["stable"]["site_id"] == "cost_info.gs.lanzhou.cost_station_journal"
    assert config["stable"]["region_code"] == "620100"
    assert config["stable"]["coverage_region_code"] == "620100"
    assert config["stable"]["publisher_scope"] == "city"
    assert config["stable"]["publisher_name"] == "兰州市建设工程造价站"
    assert config["stable"]["publisher_type"] == "cost_station_public_institution"
    assert config["stable"]["period_kind"] == "issue_based"
    assert config["price_coordinates"]["price_kind"] == "unspecified"
    assert "市场参考价格" in config["price_coordinates"]["price_kind_basis"]
    assert parser["list_strategy"] == "hanweb_jpage_static_datastore"
    assert parser["detail_strategy"] == "article_detail_pdf_anchor"
    assert parser["period"]["kind"] == "issue_based"
    assert target["region_code"] == "620100"
    assert target["region_name"] == "兰州市"
    assert target["target_level"] == "city"


def test_lanzhou_list_parses_static_datastore_and_filters_non_cost_info_downloads():
    client = FakeLanzhouCostInfoClient(
        list_html=_lanzhou_list_html(),
        detail_html_by_url={},
    )
    parser = lanzhou_cost_info_source_config()["parser"]["parsers"][LANZHOU_COST_INFO_PARSER_VERSION]

    rows = list_lanzhou_cost_info_issues(client, parser=parser, max_items=5)

    assert [row["period"] for row in rows] == [
        "2026年第1期",
        "2025年第6期",
        "2025年第5期",
        "2025年第4期",
        "2025年第2期",
    ]
    assert rows[0]["title"] == "2026年第1期兰州建设工程造价信息"
    assert rows[0]["source_title"] == "2026 年第 1 期兰州建设工程造价信息"
    assert rows[0]["period_year"] == 2026
    assert rows[0]["period_issue_no"] == 1
    assert rows[0]["publish_date"] == "2026-04-09"
    assert rows[0]["source_item_key"] == "1693681"
    assert rows[0]["detail_url"] == "https://zjj.lanzhou.gov.cn/art/2026/4/9/art_13792_1693681.html"
    assert all("补贴" not in row["source_title"] for row in rows)
    assert all("综合材料预算指导价格" not in row["source_title"] for row in rows)


def test_lanzhou_detail_pdf_attachment_ingests_issue_based_archive_without_processing(db_session):
    detail_url = "https://zjj.lanzhou.gov.cn/art/2026/4/9/art_13792_1693681.html"
    pdf_url = (
        "https://zjj.lanzhou.gov.cn/module/download/downfile.jsp?"
        "classid=0&filename=24d0d17a60844d13aa2718b912d919ec.pdf"
    )
    client = FakeLanzhouCostInfoClient(
        list_html=_lanzhou_list_html(),
        detail_html_by_url={detail_url: _lanzhou_detail_html()},
        bytes_by_url={pdf_url: b"%PDF lanzhou 2026 issue 1"},
    )
    config = lanzhou_cost_info_source_config()
    parser = config["parser"]["parsers"][LANZHOU_COST_INFO_PARSER_VERSION]
    source = _create_lanzhou_source(db_session, config=config)
    row = list_lanzhou_cost_info_issues(client, parser=parser, max_items=1)[0]

    archive = ingest_lanzhou_cost_info_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=row,
        actor_id="lanzhou-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    event = db_session.query(IngestEvent).filter_by(source_item_key=row["source_item_key"]).one()

    assert stored.source_item_key == "1693681"
    assert event.source_item_key == stored.source_item_key
    assert stored.region_code == "620100"
    assert stored.period_kind == "issue_based"
    assert stored.price_kind == "unspecified"
    assert cell_value(stored.metadata_payload["period"]) == "2026年第1期"
    assert cell_value(stored.metadata_payload["period_raw"]) == "2026年第1期"
    assert cell_value(stored.metadata_payload["period_start"]) is None
    assert cell_value(stored.metadata_payload["period_year"]) == 2026
    assert cell_value(stored.metadata_payload["period_issue_no"]) == 1
    assert "period_month" not in stored.metadata_payload
    assert cell_value(stored.metadata_payload["source_title"]) == "2026 年第 1 期兰州建设工程造价信息"
    assert cell_value(stored.metadata_payload["publisher_type"]) == "cost_station_public_institution"
    assert cell_value(stored.metadata_payload["publisher_scope"]) == "city"
    assert cell_value(stored.metadata_payload["source_attachment_mode"]) == "pdf_attachment"
    assert cell_value(stored.metadata_payload["publication_mode"]) == "DETAIL_PAGE_ATTACHMENT"
    assert cell_value(stored.metadata_payload["price_source_type"]) == "info_price"
    assert "未文档级分离" in cell_value(stored.metadata_payload["price_kind_basis"])
    assert cell_value(stored.metadata_payload["attachment_discovery_methods"]) == ["detail_page_pdf_anchor"]
    assert client.get_text_calls == [LANZHOU_LIST_URL, detail_url]
    assert client.downloaded == [pdf_url]
    assert db_session.query(FileProcessing).count() == 0


def _lanzhou_list_html():
    return """
    <script type="text/xml">
      <record><![CDATA[<li><a href='/art/2026/4/9/art_13792_1693681.html' title='2026 年第 1 期兰州建设工程造价信息' target="_blank">2026 年第 1 期兰州建设工程造价信息</a><span>2026-04-09</span></li>]]></record>
      <record><![CDATA[<li><a href='/art/2026/2/9/art_13792_1679303.html' title='支持大学生群体留兰购买新建商品住房购房补贴实施细则' target="_blank">支持大学生群体留兰购买新建商品住房购房补贴实施细则</a><span>2026-02-09</span></li>]]></record>
      <record><![CDATA[<li><a href='/art/2026/1/23/art_13792_1675011.html' title='2025年第6期兰州建设工程信息价格' target="_blank">2025年第6期兰州建设工程信息价格</a><span>2026-01-23</span></li>]]></record>
      <record><![CDATA[<li><a href='/art/2025/11/24/art_13792_1575510.html' title='2025年第5期兰州建设工程信息价格' target="_blank">2025年第5期兰州建设工程信息价格</a><span>2025-11-24</span></li>]]></record>
      <record><![CDATA[<li><a href='/art/2025/9/19/art_13792_1558277.html' title='2025年第四期兰州建设工程信息价格' target="_blank">2025年第四期兰州建设工程信息价格</a><span>2025-09-19</span></li>]]></record>
      <record><![CDATA[<li><a href='/art/2025/6/4/art_13792_1508445.html' title='2025年度第2期兰州市建设工程价格信息' target="_blank">2025年度第2期兰州市建设工程价格信息</a><span>2025-06-04</span></li>]]></record>
      <record><![CDATA[<li><a href='/art/2025/5/20/art_13792_1501498.html' title='关于印发《组建物业管理委员会工作指引》的通知' target="_blank">关于印发《组建物业管理委员会工作指引》的通知</a><span>2025-05-20</span></li>]]></record>
      <record><![CDATA[<li><a href='/art/2025/3/21/art_13792_1470711.html' title='兰州建设工程信息价格(2025年第1期)' target="_blank">兰州建设工程信息价格(2025年第1期)</a><span>2025-03-21</span></li>]]></record>
      <record><![CDATA[<li><a href='/art/2020/10/20/art_13792_938309.html' title='关于印发《2020年七至八月实物法调整的综合材料预算指导价格》的通知' target="_blank">关于印发《2020年七至八月实物法调整的综合材料预算指导价格》的通知</a><span>2020-10-20</span></li>]]></record>
    </script>
    """


def _lanzhou_detail_html():
    return """
    <html><head>
      <meta name="ArticleTitle" content="2026 年第 1 期兰州建设工程造价信息">
      <meta name="description" content="2026 年第 1 期兰州建设工程造价信息.pdf">
    </head><body>
      <td align="center"> 信息来源：兰州市住房和城乡建设局</td>
      <div id="zoom">
        <a href="/module/download/downfile.jsp?classid=0&filename=24d0d17a60844d13aa2718b912d919ec.pdf">
          <img src="/module/jslib/icons/acrobat.png"/>2026 年第 1 期兰州建设工程造价信息.pdf
        </a>
        <a href="/picture/0/ignored.jpg">封面图</a>
      </div>
    </body></html>
    """


def _create_lanzhou_source(db_session, *, config):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="兰州市建设工程造价站-兰州建设工程造价信息",
        data_domain="cost_info",
        province="甘肃省",
        city="兰州市",
        region_code="620100",
        config=config,
    )
