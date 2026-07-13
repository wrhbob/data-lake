from app.collection import create_data_source
from app.models import Archive, FileProcessing, IngestEvent
from app.shangluo_cost_info import (
    SHANGLUO_COST_INFO_PARSER_VERSION,
    SHANGLUO_LIST_URL,
    ingest_shangluo_cost_info_issue,
    list_shangluo_cost_info_issues,
    shangluo_cost_info_source_config,
)
from app.storage import FakeObjectStore


def cell_value(cell):
    return cell["value"]


class FakeShangluoCostInfoClient:
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
            return b"%PDF shangluo issue bytes", "application/pdf"
        raise AssertionError(f"unexpected download: {url}")


def test_shangluo_source_config_marks_city_issue_based_mixed_journal_pdf_source():
    config = shangluo_cost_info_source_config()
    parser = config["parser"]["parsers"][SHANGLUO_COST_INFO_PARSER_VERSION]
    target = config["coverage_expectation"]["target_regions"][0]

    assert config["stable"]["site_id"] == "cost_info.sn.shangluo.zjj.cost_journal"
    assert config["stable"]["region_code"] == "611000"
    assert config["stable"]["coverage_region_code"] == "611000"
    assert config["stable"]["publisher_scope"] == "city"
    assert config["stable"]["publisher_name"] == "商洛市住房和城乡建设局"
    assert config["stable"]["publisher_type"] == "official_housing_urban_rural_development"
    assert config["stable"]["period_kind"] == "issue_based"
    assert config["price_coordinates"]["price_kind"] == "unspecified"
    assert "未文档级分离" in config["price_coordinates"]["price_kind_basis"]
    assert parser["adapter_kind"] == "shangluo_pdf"
    assert parser["list_strategy"] == "visual_sitebuilder_paginated_notice_list"
    assert parser["detail_strategy"] == "visual_sitebuilder_virtual_attach_pdf"
    assert parser["period"]["kind"] == "issue_based"
    assert config["source_shape"]["source_attachment_mode"] == "pdf_attachment"
    assert target["region_code"] == "611000"
    assert target["region_name"] == "商洛市"
    assert target["target_level"] == "city"


def test_shangluo_list_parses_paginated_notice_list_and_keeps_cost_journal_issues():
    second_page = "https://www.shangluo.gov.cn/zjj/index/tzgg/6.htm"
    client = FakeShangluoCostInfoClient(
        html_by_url={
            SHANGLUO_LIST_URL: _shangluo_list_page_1(),
            second_page: _shangluo_list_page_2(),
        }
    )
    parser = shangluo_cost_info_source_config()["parser"]["parsers"][SHANGLUO_COST_INFO_PARSER_VERSION]

    rows = list_shangluo_cost_info_issues(client, parser=parser, max_pages=2, max_items=5)

    assert [row["period"] for row in rows] == [
        "2026年第1期",
        "2025年第4期",
        "2025年第3期",
        "2025年第2期",
    ]
    assert rows[0]["title"] == "商洛工程造价管理信息（2026年第1期）"
    assert rows[0]["source_title"] == "商洛工程造价管理信息（2026年第1期）"
    assert rows[0]["period_year"] == 2026
    assert rows[0]["period_issue_no"] == 1
    assert rows[0]["publish_date"] == "2026-04-20"
    assert rows[0]["source_item_key"] == "5685"
    assert rows[0]["detail_url"] == "https://www.shangluo.gov.cn/zjj/info/1033/5685.htm"
    assert rows[2]["detail_url"] == "https://www.shangluo.gov.cn/zjj/info/1033/5195.htm"
    assert all("公租房" not in row["source_title"] for row in rows)


def test_shangluo_detail_virtual_attach_pdf_ingests_archive_without_processing(db_session):
    detail_url = "https://www.shangluo.gov.cn/zjj/info/1033/5685.htm"
    pdf_url = (
        "https://www.shangluo.gov.cn/zjj/virtual_attach_file.vsb?"
        "afc=encoded&oid=1985101194&tid=1033&nid=5685&e=.pdf"
    )
    client = FakeShangluoCostInfoClient(
        html_by_url={
            SHANGLUO_LIST_URL: _shangluo_list_page_1(),
            detail_url: _shangluo_detail_html(pdf_url),
        },
        bytes_by_url={pdf_url: b"%PDF shangluo 2026 issue 1"},
    )
    config = shangluo_cost_info_source_config()
    parser = config["parser"]["parsers"][SHANGLUO_COST_INFO_PARSER_VERSION]
    source = _create_shangluo_source(db_session, config=config)
    row = list_shangluo_cost_info_issues(client, parser=parser, max_pages=1, max_items=1)[0]

    archive = ingest_shangluo_cost_info_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=row,
        actor_id="shangluo-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    event = db_session.query(IngestEvent).filter_by(source_item_key=row["source_item_key"]).one()

    assert stored.source_item_key == "5685"
    assert event.source_item_key == stored.source_item_key
    assert stored.region_code == "611000"
    assert stored.period_kind == "issue_based"
    assert stored.price_kind == "unspecified"
    assert cell_value(stored.metadata_payload["period"]) == "2026年第1期"
    assert cell_value(stored.metadata_payload["period_raw"]) == "2026年第1期"
    assert cell_value(stored.metadata_payload["period_start"]) is None
    assert cell_value(stored.metadata_payload["period_year"]) == 2026
    assert cell_value(stored.metadata_payload["period_issue_no"]) == 1
    assert cell_value(stored.metadata_payload["source_title"]) == "商洛工程造价管理信息（2026年第1期）"
    assert cell_value(stored.metadata_payload["publisher_type"]) == "official_housing_urban_rural_development"
    assert cell_value(stored.metadata_payload["publisher_scope"]) == "city"
    assert cell_value(stored.metadata_payload["source_attachment_mode"]) == "pdf_attachment"
    assert cell_value(stored.metadata_payload["publication_mode"]) == "DETAIL_PAGE_ATTACHMENT"
    assert cell_value(stored.metadata_payload["price_source_type"]) == "info_price"
    assert "未文档级分离" in cell_value(stored.metadata_payload["price_kind_basis"])
    assert cell_value(stored.metadata_payload["attachment_discovery_methods"]) == [
        "detail_page_virtual_attach_pdf_anchor"
    ]
    assert client.get_text_calls == [SHANGLUO_LIST_URL, detail_url]
    assert client.downloaded == [pdf_url]
    assert db_session.query(FileProcessing).count() == 0


def _shangluo_list_page_1():
    return """
    <ul class="gg-list">
      <li id="line_u13_3">
        <span>2026-05-14</span>
        <a href="../info/1033/5795.htm" title="2026年市明珠花园保障性租赁住房名单公示">公租房公示</a>
      </li>
      <li id="line_u13_4">
        <span>2026-04-20</span>
        <a href="../info/1033/5685.htm" title="商洛工程造价管理信息（2026年第1期）">商洛工程造价管理信息（2026年第1期）</a>
      </li>
      <li id="line_u13_10">
        <span>2026-01-15</span>
        <a href="../info/1033/5375.htm" title="商洛工程造价管理信息（2025年第4期）">商洛工程造价管理信息（2025年第4期）</a>
      </li>
    </ul>
    """


def _shangluo_list_page_2():
    return """
    <ul class="gg-list">
      <li id="line_u13_15">
        <span>2025-10-14</span>
        <a href="../../info/1033/5195.htm" title="商洛工程造价管理信息（2025年第3期）">商洛工程造价管理信息（2025年第3期）</a>
      </li>
      <li id="line_u13_23">
        <span>2025-07-04</span>
        <a href="../../info/1033/4624.htm" title="商洛工程造价管理信息（2025年第2期）">商洛工程造价管理信息（2025年第2期）</a>
      </li>
    </ul>
    """


def _shangluo_detail_html(pdf_url):
    return f"""
    <html><head>
      <meta name="ArticleTitle" content="商洛工程造价管理信息（2026年第1期）" />
      <meta name="PubDate" content="2026-04-20 11:24" />
      <meta name="ContentSource" content="商洛市人民政府 商洛市住房和城乡建设局" />
    </head><body>
      <p>
        <script>showVsbpdfIframe("{pdf_url}","100%","600","0","border",[]);</script>
      </p>
      <ul style="list-style-type:none;">
        <li>附件【<a href="{pdf_url}" target="_blank">商洛工程造价管理信息（2026年第1季度）.pdf</a>】已下载</li>
      </ul>
    </body></html>
    """


def _create_shangluo_source(db_session, *, config):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="商洛市住房和城乡建设局-工程造价管理信息",
        data_domain="cost_info",
        province="陕西省",
        city="商洛市",
        region_code="611000",
        config=config,
    )
