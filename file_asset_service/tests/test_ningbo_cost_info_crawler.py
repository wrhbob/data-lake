from app.collection import create_data_source
from app.models import Archive, FileProcessing, IngestEvent
from app.ningbo_cost_info import (
    NINGBO_COST_INFO_PARSER_VERSION,
    NINGBO_ENTRY_URL,
    ingest_ningbo_cost_info_issue,
    list_ningbo_cost_info_issues,
    ningbo_cost_info_source_config,
)
from app.storage import FakeObjectStore


def cell_value(cell):
    return cell["value"]


class FakeNingboCostInfoClient:
    def __init__(self, *, list_html, detail_html_by_url, bytes_by_url=None, post_html_by_argument=None):
        self.list_html = list_html
        self.detail_html_by_url = detail_html_by_url
        self.bytes_by_url = bytes_by_url or {}
        self.post_html_by_argument = post_html_by_argument or {}
        self.get_text_calls = []
        self.posted_forms = []
        self.posted_downloads = []

    def get_text(self, url):
        self.get_text_calls.append(url)
        if url == NINGBO_ENTRY_URL:
            return self.list_html
        if url in self.detail_html_by_url:
            return self.detail_html_by_url[url]
        raise AssertionError(f"unexpected GET: {url}")

    def post_form(self, url, data):
        self.posted_forms.append((url, dict(data)))
        argument = str(data.get("__EVENTARGUMENT"))
        if argument in self.post_html_by_argument:
            return self.post_html_by_argument[argument]
        raise AssertionError("initial Ningbo batch must not post WebForms pagination")

    def post_form_bytes(self, url, data):
        self.posted_downloads.append((url, dict(data)))
        if url in self.bytes_by_url:
            return self.bytes_by_url[url], "application/pdf", (
                "attachment; filename=2026%e5%b9%b45%e6%9c%88%e5%88%8a%e7%bb%bc%e5%90%88%e7%89%88.pdf"
            )
        raise AssertionError(f"unexpected download POST: {url}")


def test_ningbo_source_config_marks_city_monthly_mixed_cost_station_journal():
    config = ningbo_cost_info_source_config()
    parser = config["parser"]["parsers"][NINGBO_COST_INFO_PARSER_VERSION]
    target = config["coverage_expectation"]["target_regions"][0]

    assert config["stable"]["site_id"] == "cost_info.zj.ningbo.comprehensive_journal"
    assert config["stable"]["region_code"] == "330200"
    assert config["stable"]["coverage_region_code"] == "330200"
    assert config["stable"]["publisher_scope"] == "city"
    assert config["stable"]["publisher_name"] == "宁波市建筑市场管理服务总站（宁波市建设工程造价管理服务总站）"
    assert config["stable"]["publisher_type"] == "cost_station_public_institution"
    assert config["stable"]["period_kind"] == "monthly"
    assert config["price_coordinates"]["price_kind"] == "unspecified"
    assert "整本月刊" in config["price_coordinates"]["price_kind_basis"]
    assert parser["list_strategy"] == "aspnet_webforms_journal_list"
    assert parser["download_strategy"] == "detail_page_webforms_postback"
    assert parser["pagination"]["initial_batch_pages"] == 1
    assert parser["pagination"]["postback_target"] == "ctl00$ContentPlaceContent$Pager"
    assert target["region_code"] == "330200"
    assert target["region_name"] == "宁波市"
    assert target["target_level"] == "city"


def test_ningbo_list_parses_first_page_monthly_journal_rows_only():
    client = FakeNingboCostInfoClient(
        list_html=_ningbo_list_html(),
        detail_html_by_url={},
    )
    parser = ningbo_cost_info_source_config()["parser"]["parsers"][NINGBO_COST_INFO_PARSER_VERSION]

    rows = list_ningbo_cost_info_issues(client, parser=parser, max_pages=1)

    assert [row["period"] for row in rows] == ["2026-05", "2026-04", "2026-03", "2026-02", "2026-01"]
    assert rows[0]["title"] == "宁波造价信息2026年5月刊综合版"
    assert rows[0]["listed_title"] == "2026年5月刊综合版"
    assert rows[0]["period_year"] == 2026
    assert rows[0]["period_month"] == 5
    assert rows[0]["detail_url"] == "https://www.nbzj.net/Book/ElectronicJournalView.aspx?ContentId=9784"
    assert rows[0]["source_item_key"] == "9784"
    assert rows[0]["webforms"]["viewstate"] == "state-page-1"
    assert rows[0]["webforms"]["eventvalidation"] == "event-page-1"
    assert len(client.posted_forms) == 0


def test_ningbo_webforms_postback_carries_state_for_future_history_pages():
    client = FakeNingboCostInfoClient(
        list_html=_ningbo_list_html(),
        detail_html_by_url={},
        post_html_by_argument={"2": _ningbo_page2_html()},
    )
    parser = ningbo_cost_info_source_config()["parser"]["parsers"][NINGBO_COST_INFO_PARSER_VERSION]

    rows = list_ningbo_cost_info_issues(client, parser=parser, max_pages=2)

    assert [row["period"] for row in rows] == ["2026-05", "2026-04", "2026-03", "2026-02", "2026-01", "2025-12"]
    assert len(client.posted_forms) == 1
    url, data = client.posted_forms[0]
    assert url == NINGBO_ENTRY_URL
    assert data["__VIEWSTATE"] == "state-page-1"
    assert data["__EVENTVALIDATION"] == "event-page-1"
    assert data["ctl00$ContentPlaceContent$Pager_input"] == "1"
    assert data["__EVENTTARGET"] == "ctl00$ContentPlaceContent$Pager"
    assert data["__EVENTARGUMENT"] == "2"


def test_ningbo_detail_postback_download_ingests_layer0_mixed_journal_without_processing(db_session):
    detail_url = "https://www.nbzj.net/Book/ElectronicJournalView.aspx?ContentId=9784"
    client = FakeNingboCostInfoClient(
        list_html=_ningbo_list_html(),
        detail_html_by_url={detail_url: _ningbo_detail_html()},
        bytes_by_url={detail_url: b"%PDF ningbo 2026-05 comprehensive journal"},
    )
    config = ningbo_cost_info_source_config()
    parser = config["parser"]["parsers"][NINGBO_COST_INFO_PARSER_VERSION]
    source = _create_ningbo_source(db_session, config=config)
    row = list_ningbo_cost_info_issues(client, parser=parser, max_pages=1)[0]

    archive = ingest_ningbo_cost_info_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=row,
        actor_id="ningbo-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    event = db_session.query(IngestEvent).filter_by(source_item_key=row["source_item_key"]).one()

    assert stored.source_item_key == "9784"
    assert event.source_item_key == stored.source_item_key
    assert stored.region_code == "330200"
    assert stored.period_kind == "monthly"
    assert stored.price_kind == "unspecified"
    assert cell_value(stored.metadata_payload["period"]) == "2026-05"
    assert cell_value(stored.metadata_payload["period_raw"]) == "宁波造价信息2026年5月刊综合版"
    assert cell_value(stored.metadata_payload["listed_title"]) == "2026年5月刊综合版"
    assert cell_value(stored.metadata_payload["source_title"]) == "2026年5月刊综合版"
    assert cell_value(stored.metadata_payload["period_start"]) == "2026-05"
    assert cell_value(stored.metadata_payload["period_year"]) == 2026
    assert cell_value(stored.metadata_payload["period_month"]) == 5
    assert cell_value(stored.metadata_payload["publisher_type"]) == "cost_station_public_institution"
    assert cell_value(stored.metadata_payload["publisher_scope"]) == "city"
    assert cell_value(stored.metadata_payload["source_attachment_mode"]) == "pdf_attachment"
    assert cell_value(stored.metadata_payload["publication_mode"]) == "DETAIL_PAGE_POSTBACK_DOWNLOAD"
    assert cell_value(stored.metadata_payload["price_source_type"]) == "info_price"
    assert "未文档级分离" in cell_value(stored.metadata_payload["price_kind_basis"])
    assert cell_value(stored.metadata_payload["attachment_discovery_methods"]) == ["webforms_download_postback"]
    assert client.get_text_calls == [NINGBO_ENTRY_URL, detail_url]
    assert client.posted_downloads == [
        (
            detail_url,
            {
                "__EVENTTARGET": "ctl00$ContentPlaceContent$lbtnDownLoad",
                "__EVENTARGUMENT": "",
                "__VIEWSTATE": "detail-state",
                "__EVENTVALIDATION": "detail-event",
            },
        )
    ]
    assert db_session.query(FileProcessing).count() == 0


def _ningbo_list_html():
    return """
    <form method="post" action="./ElectronicJournalList.aspx?CategoryId=193" id="aspnetForm">
      <input type="hidden" name="__EVENTTARGET" id="__EVENTTARGET" value="" />
      <input type="hidden" name="__EVENTARGUMENT" id="__EVENTARGUMENT" value="" />
      <input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="state-page-1" />
      <input type="hidden" name="__EVENTVALIDATION" id="__EVENTVALIDATION" value="event-page-1" />
      <select name="ctl00$ContentPlaceContent$Pager_input" id="ctl00_ContentPlaceContent_Pager_input">
        <option value="1" selected="true">1</option><option value="2">2</option>
      </select>
      <li><a href="ElectronicJournalView.aspx?ContentId=9784" target="_blank"><div><img alt="2026年5月刊综合版" /></div><h3><strong>2026年5月刊综合版</strong><span>››</span></h3></a></li>
      <li><a href="ElectronicJournalView.aspx?ContentId=9768" target="_blank"><div><img alt="2026年4月刊综合版" /></div><h3><strong>2026年4月刊综合版</strong><span>››</span></h3></a></li>
      <li><a href="ElectronicJournalView.aspx?ContentId=9748" target="_blank"><div><img alt="2026年3月刊综合版" /></div><h3><strong>2026年3月刊综合版</strong><span>››</span></h3></a></li>
      <li><a href="ElectronicJournalView.aspx?ContentId=9711" target="_blank"><div><img alt="2026年2月刊综合版" /></div><h3><strong>2026年2月刊综合版</strong><span>››</span></h3></a></li>
      <li><a href="ElectronicJournalView.aspx?ContentId=9703" target="_blank"><div><img alt="2026年1月刊综合版" /></div><h3><strong>2026年1月刊综合版</strong><span>››</span></h3></a></li>
      <li><a href="ElectronicJournalView.aspx?ContentId=9999" target="_blank"><div><img alt="2026年5月刊商情版" /></div><h3><strong>2026年5月刊商情版</strong><span>››</span></h3></a></li>
      <a href="javascript:__doPostBack('ctl00$ContentPlaceContent$Pager','2')">2</a>
    </form>
    """


def _ningbo_page2_html():
    return """
    <form method="post" action="./ElectronicJournalList.aspx?CategoryId=193" id="aspnetForm">
      <input type="hidden" name="__EVENTTARGET" id="__EVENTTARGET" value="" />
      <input type="hidden" name="__EVENTARGUMENT" id="__EVENTARGUMENT" value="" />
      <input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="state-page-2" />
      <input type="hidden" name="__EVENTVALIDATION" id="__EVENTVALIDATION" value="event-page-2" />
      <li><a href="ElectronicJournalView.aspx?ContentId=9682" target="_blank"><div><img alt="2025年12月刊综合版" /></div><h3><strong>2025年12月刊综合版</strong><span>››</span></h3></a></li>
    </form>
    """


def _ningbo_detail_html():
    return """
    <form method="post" action="./ElectronicJournalView.aspx?ContentId=9784" id="aspnetForm">
      <input type="hidden" name="__EVENTTARGET" id="__EVENTTARGET" value="" />
      <input type="hidden" name="__EVENTARGUMENT" id="__EVENTARGUMENT" value="" />
      <input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="detail-state" />
      <input type="hidden" name="__EVENTVALIDATION" id="__EVENTVALIDATION" value="detail-event" />
      <div class="z_title color02">2026年5月刊综合版</div>
      <li class="color02">时间：2026-06-01</li>
      <a id="ctl00_ContentPlaceContent_lbtnDownLoad" href="javascript:__doPostBack(&#39;ctl00$ContentPlaceContent$lbtnDownLoad&#39;,&#39;&#39;)">期刊下载</a>
    </form>
    """


def _create_ningbo_source(db_session, *, config):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="宁波市建筑市场管理服务总站-宁波造价信息综合版",
        data_domain="cost_info",
        province="浙江省",
        city="宁波市",
        region_code="330200",
        config=config,
    )
