from app.collection import create_data_source
from app.models import Archive, FileProcessing, IngestEvent
from app.nanjing_cost_info import (
    NANJING_COST_INFO_PARSER_VERSION,
    NANJING_ENTRY_URL,
    ingest_nanjing_cost_info_issue,
    list_nanjing_cost_info_issues,
    nanjing_cost_info_source_config,
)
from app.storage import FakeObjectStore


def cell_value(cell):
    return cell["value"]


class FakeNanjingCostInfoClient:
    def __init__(self, *, list_html, detail_html_by_url, bytes_by_url=None, post_html_by_argument=None):
        self.list_html = list_html
        self.detail_html_by_url = detail_html_by_url
        self.bytes_by_url = bytes_by_url or {}
        self.post_html_by_argument = post_html_by_argument or {}
        self.get_text_calls = []
        self.posted_forms = []
        self.downloaded = []

    def get_text(self, url):
        self.get_text_calls.append(url)
        if url == NANJING_ENTRY_URL:
            return self.list_html
        if url in self.detail_html_by_url:
            return self.detail_html_by_url[url]
        raise AssertionError(f"unexpected GET: {url}")

    def post_form(self, url, data):
        self.posted_forms.append((url, dict(data)))
        argument = str(data.get("__EVENTARGUMENT"))
        if argument in self.post_html_by_argument:
            return self.post_html_by_argument[argument]
        raise AssertionError("initial Nanjing batch must not post WebForms pagination")

    def get_bytes(self, url):
        self.downloaded.append(url)
        if url in self.bytes_by_url:
            return self.bytes_by_url[url], "application/pdf"
        if url.endswith(".pdf"):
            return b"%PDF nanjing monthly issue bytes", "application/pdf"
        raise AssertionError(f"unexpected download: {url}")


def test_nanjing_source_config_marks_city_monthly_guidance_cost_station_source():
    config = nanjing_cost_info_source_config()
    parser = config["parser"]["parsers"][NANJING_COST_INFO_PARSER_VERSION]
    target = config["coverage_expectation"]["target_regions"][0]

    assert config["stable"]["site_id"] == "cost_info.js.nanjing.material_market_info"
    assert config["stable"]["region_code"] == "320100"
    assert config["stable"]["coverage_region_code"] == "320100"
    assert config["stable"]["publisher_scope"] == "city"
    assert config["stable"]["publisher_name"] == "南京市建设工程造价监督站"
    assert config["stable"]["publisher_type"] == "cost_station_public_institution"
    assert config["stable"]["period_kind"] == "monthly"
    assert config["price_coordinates"]["price_kind"] == "guidance"
    assert "招标控制价" in config["price_coordinates"]["price_kind_basis"]
    assert parser["list_strategy"] == "aspnet_webforms_stateful_gridview"
    assert parser["pagination"]["initial_batch_pages"] == 1
    assert parser["pagination"]["postback_target"] == "_ctl0$ContentPlaceHolder1$GridViewStanderd"
    assert target["region_code"] == "320100"
    assert target["region_name"] == "南京市"
    assert target["target_level"] == "city"


def test_nanjing_list_parses_webforms_state_and_first_page_monthly_rows_only():
    client = FakeNanjingCostInfoClient(
        list_html=_nanjing_list_html(),
        detail_html_by_url={},
    )
    parser = nanjing_cost_info_source_config()["parser"]["parsers"][NANJING_COST_INFO_PARSER_VERSION]

    rows = list_nanjing_cost_info_issues(client, parser=parser, max_pages=1)

    assert [row["period"] for row in rows] == ["2026-05", "2026-04", "2026-03", "2026-02", "2026-01"]
    assert rows[0]["title"] == "南京市2026年5月建设工程材料市场信息价格"
    assert rows[0]["listed_title"] == "南京市二〇二六年五月建设工程材料市场信息价格"
    assert rows[0]["period_year"] == 2026
    assert rows[0]["period_month"] == 5
    assert rows[0]["publish_date"] == "2026-05-29"
    assert rows[0]["detail_url"] == "https://www.njszj.cn/zjweb/MessageShow.aspx?Id=c481dc5c-314e-4edb-a259-d85e0cb2954f"
    assert rows[0]["source_item_key"] == "c481dc5c-314e-4edb-a259-d85e0cb2954f"
    assert rows[0]["webforms"]["viewstate"] == "state-page-1"
    assert rows[0]["webforms"]["eventvalidation"] == "event-page-1"
    assert len(client.posted_forms) == 0


def test_nanjing_webforms_postback_carries_viewstate_for_future_history_pages():
    client = FakeNanjingCostInfoClient(
        list_html=_nanjing_list_html(),
        detail_html_by_url={},
        post_html_by_argument={"Page$2": _nanjing_page2_html()},
    )
    parser = nanjing_cost_info_source_config()["parser"]["parsers"][NANJING_COST_INFO_PARSER_VERSION]

    rows = list_nanjing_cost_info_issues(client, parser=parser, max_pages=2)

    assert [row["period"] for row in rows] == [
        "2026-05",
        "2026-04",
        "2026-03",
        "2026-02",
        "2026-01",
        "2025-12",
    ]
    assert len(client.posted_forms) == 1
    url, data = client.posted_forms[0]
    assert url == NANJING_ENTRY_URL
    assert data["__VIEWSTATE"] == "state-page-1"
    assert data["__EVENTVALIDATION"] == "event-page-1"
    assert data["__EVENTTARGET"] == "_ctl0$ContentPlaceHolder1$GridViewStanderd"
    assert data["__EVENTARGUMENT"] == "Page$2"
    assert data["_ctl0:ContentPlaceHolder1:hidCategoryID"] == "f55f3e95-167d-4993-b811-3d5327f9ee78"


def test_nanjing_detail_pdf_ingests_layer0_archive_without_processing(db_session):
    detail_url = "https://www.njszj.cn/zjweb/MessageShow.aspx?Id=c481dc5c-314e-4edb-a259-d85e0cb2954f"
    pdf_url = (
        "https://www.njszj.cn/ZJMis/MaterialPD/2026/"
        "%c4%cf%be%a9%ca%d0%b6%fe%a9%96%b6%fe%c1%f9%c4%ea%ce%e5%d4%c2.pdf"
    )
    client = FakeNanjingCostInfoClient(
        list_html=_nanjing_list_html(),
        detail_html_by_url={detail_url: _nanjing_detail_html(pdf_url=pdf_url)},
        bytes_by_url={pdf_url: b"%PDF nanjing 2026-05"},
    )
    config = nanjing_cost_info_source_config()
    parser = config["parser"]["parsers"][NANJING_COST_INFO_PARSER_VERSION]
    source = _create_nanjing_source(db_session, config=config)
    row = list_nanjing_cost_info_issues(client, parser=parser, max_pages=1)[0]

    archive = ingest_nanjing_cost_info_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=row,
        actor_id="nanjing-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    event = db_session.query(IngestEvent).filter_by(source_item_key=row["source_item_key"]).one()

    assert stored.source_item_key == "c481dc5c-314e-4edb-a259-d85e0cb2954f"
    assert event.source_item_key == stored.source_item_key
    assert stored.region_code == "320100"
    assert stored.period_kind == "monthly"
    assert stored.price_kind == "guidance"
    assert cell_value(stored.metadata_payload["period"]) == "2026-05"
    assert cell_value(stored.metadata_payload["period_raw"]) == "南京市2026年5月建设工程材料市场信息价格"
    assert cell_value(stored.metadata_payload["listed_title"]) == "南京市二〇二六年五月建设工程材料市场信息价格"
    assert cell_value(stored.metadata_payload["source_title"]) == "南京市二〇二六年五月建设工程材料市场信息价格"
    assert cell_value(stored.metadata_payload["period_start"]) == "2026-05"
    assert cell_value(stored.metadata_payload["period_year"]) == 2026
    assert cell_value(stored.metadata_payload["period_month"]) == 5
    assert cell_value(stored.metadata_payload["publisher_type"]) == "cost_station_public_institution"
    assert cell_value(stored.metadata_payload["publisher_scope"]) == "city"
    assert cell_value(stored.metadata_payload["source_attachment_mode"]) == "pdf_attachment"
    assert cell_value(stored.metadata_payload["publication_mode"]) == "DETAIL_PAGE_ATTACHMENT"
    assert cell_value(stored.metadata_payload["price_source_type"]) == "info_price"
    assert cell_value(stored.metadata_payload["price_kind_basis"]).startswith("PDF总说明")
    assert cell_value(stored.metadata_payload["attachment_discovery_methods"]) == ["detail_page_pdf_anchor"]
    assert client.get_text_calls == [NANJING_ENTRY_URL, detail_url]
    assert client.downloaded == [pdf_url]
    assert db_session.query(FileProcessing).count() == 0


def _nanjing_list_html():
    return """
    <form method="post" action="./StdCategory.aspx?CategoryID=f55f3e95-167d-4993-b811-3d5327f9ee78" id="aspnetForm">
      <input type="hidden" name="__EVENTTARGET" id="__EVENTTARGET" value="" />
      <input type="hidden" name="__EVENTARGUMENT" id="__EVENTARGUMENT" value="" />
      <input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="state-page-1" />
      <input type="hidden" name="__VIEWSTATEENCRYPTED" id="__VIEWSTATEENCRYPTED" value="" />
      <input type="hidden" name="__EVENTVALIDATION" id="__EVENTVALIDATION" value="event-page-1" />
      <input type="hidden" name="_ctl0:ContentPlaceHolder1:hidCategoryID" id="_ctl0_ContentPlaceHolder1_hidCategoryID" value="f55f3e95-167d-4993-b811-3d5327f9ee78" />
      <table>
        <tr><td><a id="detail" target="_blank" href="/zjweb/MessageShow.aspx?Id=c481dc5c-314e-4edb-a259-d85e0cb2954f">南京市二〇二六年五月建设工程材料市场信息价格</a></td><td>[2026-05-29]</td></tr>
        <tr><td><a id="detail" target="_blank" href="/zjweb/MessageShow.aspx?Id=5ac54041-3c2b-46f9-ad0f-b78ab90c5e1c">南京市二〇二六年四月建设工程材料市场信息价格</a></td><td>[2026-04-30]</td></tr>
        <tr><td><a id="detail" target="_blank" href="/zjweb/MessageShow.aspx?Id=67618608-40af-4007-8ac2-31a1b532f150">南京市二〇二六年三月建设工程材料市场信息价格</a></td><td>[2026-03-31]</td></tr>
        <tr><td><a id="detail" target="_blank" href="/zjweb/MessageShow.aspx?Id=478b7690-9cde-4b41-83f6-b498db957cfd">南京市二〇二六年二月建设工程材料市场信息价格</a></td><td>[2026-02-28]</td></tr>
        <tr><td><a id="detail" target="_blank" href="/zjweb/MessageShow.aspx?Id=9c795667-5559-430b-9341-3498870e0332">南京市二〇二六年一月建设工程材料市场信息价格</a></td><td>[2026-01-30]</td></tr>
        <tr><td><a id="detail" target="_blank" href="/zjweb/MessageShow.aspx?Id=ignored">南京市二〇二六年五月其他通知</a></td><td>[2026-05-20]</td></tr>
        <tr><td><a href="javascript:__doPostBack('_ctl0$ContentPlaceHolder1$GridViewStanderd','Page$2')">2</a></td></tr>
      </table>
    </form>
    """


def _nanjing_detail_html(*, pdf_url):
    return f"""
    <html><body>
      <div>南京市二〇二六年五月建设工程材料市场信息价格</div>
      <div>（发稿时间：2026-05-29 16:07:01 来源：市造价站 浏览次数：7700）</div>
      <a href="{pdf_url}">南京市二〇二六年五月建设工程材料市场信息价格.pdf</a>
      <div>（没有相关下载！）</div>
    </body></html>
    """


def _nanjing_page2_html():
    return """
    <form method="post" action="./StdCategory.aspx?CategoryID=f55f3e95-167d-4993-b811-3d5327f9ee78" id="aspnetForm">
      <input type="hidden" name="__EVENTTARGET" id="__EVENTTARGET" value="" />
      <input type="hidden" name="__EVENTARGUMENT" id="__EVENTARGUMENT" value="" />
      <input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="state-page-2" />
      <input type="hidden" name="__EVENTVALIDATION" id="__EVENTVALIDATION" value="event-page-2" />
      <table>
        <tr><td><a target="_blank" href="/zjweb/MessageShow.aspx?Id=2025-12-id">南京市二〇二五年十二月建设工程材料市场信息价格</a></td><td>[2025-12-31]</td></tr>
      </table>
    </form>
    """


def _create_nanjing_source(db_session, *, config):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="南京市建设工程造价监督站-南京建设工程材料市场信息价",
        data_domain="cost_info",
        province="江苏省",
        city="南京市",
        region_code="320100",
        config=config,
    )
