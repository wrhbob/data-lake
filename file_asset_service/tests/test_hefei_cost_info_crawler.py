from app.collection import create_data_source
from app.hefei_cost_info import (
    HEFEI_COST_INFO_PARSER_VERSION,
    HEFEI_ENTRY_URL,
    hefei_cost_info_source_config,
    ingest_hefei_cost_info_issue,
    list_hefei_cost_info_issues,
)
from app.models import Archive, FileProcessing, IngestEvent
from app.storage import FakeObjectStore


def cell_value(cell):
    return cell["value"]


class FakeHefeiCostInfoClient:
    def __init__(self, *, initial_html, pages, bytes_by_url=None):
        self.initial_html = initial_html
        self.pages = pages
        self.bytes_by_url = bytes_by_url or {}
        self.get_text_calls = []
        self.posted_forms = []
        self.downloaded = []

    def get_text(self, url):
        self.get_text_calls.append(url)
        assert url == HEFEI_ENTRY_URL
        return self.initial_html

    def post_form(self, url, data):
        self.posted_forms.append((url, dict(data)))
        assert url == HEFEI_ENTRY_URL
        year = str(data.get("hidn_ID") or "2026")
        page = int(data.get("PageNumber1$hCurrPage") or 1)
        return self.pages[(year, page)]

    def get_bytes(self, url):
        self.downloaded.append(url)
        if url in self.bytes_by_url:
            return self.bytes_by_url[url], "application/pdf"
        if url.endswith(".pdf"):
            return b"%PDF hefei monthly issue bytes", "application/pdf"
        raise AssertionError(f"unexpected download: {url}")


def test_hefei_source_config_marks_city_monthly_guidance_source_by_substantive_basis():
    config = hefei_cost_info_source_config()
    parser = config["parser"]["parsers"][HEFEI_COST_INFO_PARSER_VERSION]
    target = config["coverage_expectation"]["target_regions"][0]

    assert config["stable"]["site_id"] == "cost_info.ah.hefei.price_info"
    assert config["stable"]["region_code"] == "340100"
    assert config["stable"]["coverage_region_code"] == "340100"
    assert config["stable"]["publisher_scope"] == "city"
    assert config["stable"]["publisher_name"] == "合肥市建筑市场监督管理处"
    assert config["stable"]["publisher_type"] == "cost_station_public_institution"
    assert config["stable"]["period_kind"] == "monthly"
    assert config["price_coordinates"]["price_kind"] == "guidance"
    assert parser["regular_issue_type"] == "正刊"
    assert parser["period"]["kind"] == "monthly"
    assert "最高投标限价" in config["price_coordinates"]["price_kind_basis"]
    assert target["region_code"] == "340100"
    assert target["region_name"] == "合肥市"
    assert target["target_level"] == "city"
    assert target["requires_city_source"] is False


def test_hefei_list_posts_aspnet_year_pages_and_filters_supplement_journal():
    client = FakeHefeiCostInfoClient(
        initial_html=_hefei_shell_html(),
        pages={
            ("2025", 1): _hefei_issue_page(
                year=2025,
                current_page=1,
                max_page=2,
                issues=[
                    (12, "/updatePDF/2025/12/5/bdf1c6d4-2f64-48d6-b3bd-3294a1631ecc.pdf", "正刊"),
                    (11, "/updatePDF/2025/11/5/180c5c1d-cae6-446d-b6a1-781b0123e067.pdf", "正刊"),
                    (10, "/updatePDF/2025/10/9/aeca67da-880c-4480-b397-61263f8721a9.pdf", "副刊"),
                ],
            ),
            ("2025", 2): _hefei_issue_page(
                year=2025,
                current_page=2,
                max_page=2,
                issues=[
                    (6, "/updatePDF/2025/6/5/70d52abd-df00-4f73-948c-a0c3d53666a0.pdf", "正刊"),
                ],
            ),
        },
    )
    parser = hefei_cost_info_source_config()["parser"]["parsers"][HEFEI_COST_INFO_PARSER_VERSION]

    rows = list_hefei_cost_info_issues(client, parser=parser, years=[2025], max_pages_per_year=2)

    assert [row["period"] for row in rows] == ["2025-12", "2025-11", "2025-06"]
    assert rows[0]["title"] == "2025年12月合肥建设工程市场价格信息"
    assert rows[0]["listed_title"] == "2025年12月刊"
    assert rows[0]["source_item_key"] == "updatePDF/2025/12/5/bdf1c6d4-2f64-48d6-b3bd-3294a1631ecc.pdf"
    assert rows[0]["period_year"] == 2025
    assert rows[0]["period_month"] == 12
    assert rows[0]["detail_url"] == HEFEI_ENTRY_URL
    assert rows[0]["attachments"] == [
        {
            "file_name": "合肥建设工程市场价格信息2025年12月.pdf",
            "url": "http://112.124.14.202:8009/updatePDF/2025/12/5/bdf1c6d4-2f64-48d6-b3bd-3294a1631ecc.pdf",
            "discovery_method": "aspnet_year_issue_pdf_link",
        }
    ]
    assert len(client.posted_forms) == 2
    assert client.posted_forms[0][1]["hidn_OP"] == "ZKNF"
    assert client.posted_forms[0][1]["hidn_ID"] == "2025"
    assert client.posted_forms[1][1]["PageNumber1$hCurrPage"] == "2"
    assert all(row["issue_type"] == "正刊" for row in rows)


def test_hefei_monthly_issue_ingests_layer0_archive_without_processing(db_session):
    pdf_url = "http://112.124.14.202:8009/updatePDF/2026/6/5/93ab25e7-f82c-44b6-bcb9-aefdd7fc6019.pdf"
    client = FakeHefeiCostInfoClient(
        initial_html=_hefei_shell_html(),
        pages={
            ("2026", 1): _hefei_issue_page(
                year=2026,
                current_page=1,
                max_page=1,
                issues=[
                    (6, "/updatePDF/2026/6/5/93ab25e7-f82c-44b6-bcb9-aefdd7fc6019.pdf", "正刊"),
                ],
            )
        },
        bytes_by_url={pdf_url: b"%PDF hefei 2026-06"},
    )
    config = hefei_cost_info_source_config()
    parser = config["parser"]["parsers"][HEFEI_COST_INFO_PARSER_VERSION]
    source = _create_hefei_source(db_session, config=config)
    row = list_hefei_cost_info_issues(client, parser=parser, years=[2026])[0]

    archive = ingest_hefei_cost_info_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=row,
        actor_id="hefei-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    event = db_session.query(IngestEvent).filter_by(source_item_key=row["source_item_key"]).one()

    assert stored.source_item_key == "updatePDF/2026/6/5/93ab25e7-f82c-44b6-bcb9-aefdd7fc6019.pdf"
    assert event.source_item_key == stored.source_item_key
    assert stored.region_code == "340100"
    assert stored.period_kind == "monthly"
    assert stored.price_kind == "guidance"
    assert cell_value(stored.metadata_payload["period"]) == "2026-06"
    assert cell_value(stored.metadata_payload["period_raw"]) == "2026年6月合肥建设工程市场价格信息"
    assert cell_value(stored.metadata_payload["period_start"]) == "2026-06"
    assert cell_value(stored.metadata_payload["period_year"]) == 2026
    assert cell_value(stored.metadata_payload["period_month"]) == 6
    assert cell_value(stored.metadata_payload["publisher_type"]) == "cost_station_public_institution"
    assert cell_value(stored.metadata_payload["publisher_scope"]) == "city"
    assert cell_value(stored.metadata_payload["source_attachment_mode"]) == "pdf_only"
    assert cell_value(stored.metadata_payload["publication_mode"]) == "DIRECT_PDF"
    assert cell_value(stored.metadata_payload["price_source_type"]) == "info_price"
    assert cell_value(stored.metadata_payload["price_kind_basis"]).startswith("PDF编制说明")
    assert client.downloaded == [pdf_url]
    assert db_session.query(FileProcessing).count() == 0


def _hefei_shell_html():
    return """
    <form method="post" action="./index.aspx" id="form1">
      <input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="state0" />
      <input type="hidden" name="__VIEWSTATEGENERATOR" id="__VIEWSTATEGENERATOR" value="F3A4114E" />
      <input type="hidden" name="__EVENTVALIDATION" id="__EVENTVALIDATION" value="event0" />
      <input type="hidden" name="hidn_OP" id="hidn_OP" />
      <input type="hidden" name="hidn_ID" id="hidn_ID" />
      <input type="hidden" name="hidn_Search" id="hidn_Search" />
      <input type="hidden" name="hidn_LJ" id="hidn_LJ" />
      <input type="hidden" name="hidn_NF" id="hidn_NF" />
      <input type="hidden" name="hidn_YF" id="hidn_YF" />
      <input type="hidden" name="hidn_QKLX" id="hidn_QKLX" />
    </form>
    """


def _hefei_issue_page(*, year, current_page, max_page, issues):
    rows = "\n".join(
        f"""
        <li><a href="javascript:void(0);" onclick="toOpen('/SubscriptionFile/{year}/{month}/5/book/HF{year}Z{month:02d}.html','{year}','{month}月','{issue_type}')">
          <img src="/SubscriptionFile/{year}/{month}/5/cover.jpg" /></a><span>{year}年{month}月刊</span>
          <br /><a target='_Blank' href="{pdf_path}">下载本期价格信息</a></li>
        """
        for month, pdf_path, issue_type in issues
    )
    return f"""
    <form method="post" action="./index.aspx" id="form1">
      <input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="state{year}{current_page}" />
      <input type="hidden" name="__VIEWSTATEGENERATOR" id="__VIEWSTATEGENERATOR" value="F3A4114E" />
      <input type="hidden" name="__EVENTVALIDATION" id="__EVENTVALIDATION" value="event{year}{current_page}" />
      <span id="lblCondition">{year}年</span>
      <ul>{rows}</ul>
      <span id="PageNumber1_lblCurrPage">{current_page}</span>
      <span id="PageNumber1_lblMaxPage">{max_page}</span>
      <input name="PageNumber1$txtPage" type="text" id="PageNumber1_txtPage" />
      <input type="hidden" name="PageNumber1$hCurrPage" id="PageNumber1_hCurrPage" value="{current_page}" />
      <input type="hidden" name="PageNumber1$hMaxPage" id="PageNumber1_hMaxPage" value="{max_page}" />
      <input type="hidden" name="hidn_OP" id="hidn_OP" />
      <input type="hidden" name="hidn_ID" id="hidn_ID" />
      <input type="hidden" name="hidn_Search" id="hidn_Search" />
      <input type="hidden" name="hidn_LJ" id="hidn_LJ" />
      <input type="hidden" name="hidn_NF" id="hidn_NF" />
      <input type="hidden" name="hidn_YF" id="hidn_YF" />
      <input type="hidden" name="hidn_QKLX" id="hidn_QKLX" />
    </form>
    """


def _create_hefei_source(db_session, *, config):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="合肥市建筑市场监督管理处-合肥建设工程市场价格信息",
        data_domain="cost_info",
        province="安徽省",
        city="合肥市",
        region_code="340100",
        config=config,
    )
