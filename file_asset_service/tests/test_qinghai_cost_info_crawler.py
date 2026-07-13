from app.collection import create_data_source
from app.models import Archive, FileProcessing, IngestEvent
from app.qinghai_cost_info import (
    QINGHAI_COST_INFO_PARSER_VERSION,
    QINGHAI_DYNAMIC_LIST_URL_TEMPLATE,
    qinghai_cost_info_source_config,
    ingest_qinghai_cost_info_issue,
    list_qinghai_cost_info_issues,
)
from app.storage import FakeObjectStore


def cell_value(cell):
    return cell["value"]


class FakeQinghaiCostInfoClient:
    def __init__(self, *, html_by_url=None, bytes_by_url=None):
        self.html_by_url = html_by_url or {}
        self.bytes_by_url = bytes_by_url or {}
        self.requested_text = []
        self.downloaded = []

    def get_text(self, url):
        self.requested_text.append(url)
        try:
            return self.html_by_url[url]
        except KeyError as exc:
            raise AssertionError(f"unexpected list request: {url}") from exc

    def get_bytes(self, url):
        self.downloaded.append(url)
        if url in self.bytes_by_url:
            return self.bytes_by_url[url], "application/pdf"
        if url.endswith(".pdf"):
            return b"%PDF qinghai issue bytes", "application/pdf"
        raise AssertionError(f"unexpected download: {url}")


def test_qinghai_source_config_marks_government_issue_based_guidance_source():
    config = qinghai_cost_info_source_config()
    parser = config["parser"]["parsers"][QINGHAI_COST_INFO_PARSER_VERSION]
    target = config["coverage_expectation"]["target_regions"][0]

    assert config["stable"]["site_id"] == "cost_info.qh.zjt.main"
    assert config["stable"]["region_code"] == "630000"
    assert config["stable"]["publisher_type"] == "official_housing_urban_rural_development"
    assert config["stable"]["publisher_name"] == "青海省住房和城乡建设厅"
    assert config["stable"]["period_kind"] == "issue_based"
    assert config["price_coordinates"]["price_kind"] == "guidance"
    assert parser["dynamic_list_url_template"] == QINGHAI_DYNAMIC_LIST_URL_TEMPLATE
    assert parser["period"]["kind"] == "issue_based"
    assert parser["title_regex"] == r"(?P<period>(?P<year>20\d{2})年第(?P<issue>\d{1,2})(?:[—–-](?P<issue_end>\d{1,2}))?期)《青海工程造价管理信息》"
    assert target["region_code"] == "630000"
    assert target["requires_city_source"] is False


def test_qinghai_list_discovery_keeps_exact_main_journal_and_filters_market_and_notices():
    parser = qinghai_cost_info_source_config()["parser"]["parsers"][QINGHAI_COST_INFO_PARSER_VERSION]
    list_url = parser["list_url"]
    client = FakeQinghaiCostInfoClient(
        html_by_url={
            list_url: """
                <table>
                  <tr>
                    <td><a href="http://zjt.qinghai.gov.cn/files/market-2026-1-2.pdf"
                      title="2026年第1—2期《青海建设工程市场价格信息》">市场价格</a></td>
                    <td>2026-02-28</td>
                  </tr>
                  <tr>
                    <td><a href="http://zjt.qinghai.gov.cn/files/main-2026-2.pdf"
                      title="2026年第2期《青海工程造价管理信息》">主刊2</a></td>
                    <td>2026-04-29</td>
                  </tr>
                  <tr>
                    <td><a href="/files/main-2026-3-4.pdf"
                      title="2026年第3—4期《青海工程造价管理信息》">主刊合刊</a></td>
                    <td>2026-06-30</td>
                  </tr>
                  <tr>
                    <td><a href="/html/8/79000.html"
                      title="青海省住房和城乡建设厅关于开展2025年度工程造价咨询统计调查的通知">通知</a></td>
                    <td>2026-01-10</td>
                  </tr>
                </table>
            """
        }
    )

    rows = list_qinghai_cost_info_issues(client, parser=parser, page=1)

    assert [row["title"] for row in rows] == [
        "2026年第3—4期《青海工程造价管理信息》",
        "2026年第2期《青海工程造价管理信息》",
    ]
    assert rows[0]["period"] == "2026年第3—4期"
    assert rows[0]["period_year"] == 2026
    assert rows[0]["period_issue_no"] == 3
    assert rows[0]["period_issue_end_no"] == 4
    assert rows[0]["source_item_key"] == "files/main-2026-3-4.pdf"
    assert rows[0]["attachments"] == [
        {
            "file_name": "main-2026-3-4.pdf",
            "url": "http://zjt.qinghai.gov.cn/files/main-2026-3-4.pdf",
            "discovery_method": "list_direct_pdf",
        }
    ]
    assert rows[1]["period"] == "2026年第2期"
    assert rows[1]["period_issue_end_no"] is None
    assert all("市场价格信息" not in str(row["title"]) for row in rows)
    assert all("通知" not in str(row["title"]) for row in rows)


def test_qinghai_issue_ingests_layer0_archive_with_issue_period_guidance_and_no_processing(db_session):
    pdf_url = "http://zjt.qinghai.gov.cn/files/main-2026-3-4.pdf"
    parser = qinghai_cost_info_source_config()["parser"]["parsers"][QINGHAI_COST_INFO_PARSER_VERSION]
    list_url = parser["list_url"]
    client = FakeQinghaiCostInfoClient(
        html_by_url={
            list_url: """
                <table>
                  <tr>
                    <td><a href="http://zjt.qinghai.gov.cn/files/main-2026-3-4.pdf"
                      title="2026年第3—4期《青海工程造价管理信息》">主刊合刊</a></td>
                    <td>2026-06-30</td>
                  </tr>
                </table>
            """
        },
        bytes_by_url={pdf_url: b"%PDF qinghai 2026 issue 3-4"},
    )
    config = qinghai_cost_info_source_config()
    source = _create_qinghai_source(db_session, config=config)
    row = list_qinghai_cost_info_issues(client, parser=parser)[0]

    archive = ingest_qinghai_cost_info_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=row,
        actor_id="qinghai-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    event = db_session.query(IngestEvent).filter_by(source_item_key="files/main-2026-3-4.pdf").one()

    assert stored.source_item_key == "files/main-2026-3-4.pdf"
    assert event.source_item_key == "files/main-2026-3-4.pdf"
    assert stored.period_kind == "issue_based"
    assert stored.price_kind == "guidance"
    assert cell_value(stored.metadata_payload["period"]) == "2026年第3—4期"
    assert cell_value(stored.metadata_payload["period_raw"]) == "2026年第3—4期"
    assert cell_value(stored.metadata_payload["period_start"]) is None
    assert cell_value(stored.metadata_payload["period_year"]) == 2026
    assert cell_value(stored.metadata_payload["period_issue_no"]) == 3
    assert cell_value(stored.metadata_payload["period_issue_end_no"]) == 4
    assert "period_month" not in stored.metadata_payload
    assert cell_value(stored.metadata_payload["publisher_type"]) == "official_housing_urban_rural_development"
    assert cell_value(stored.metadata_payload["publisher_scope"]) == "province"
    assert cell_value(stored.metadata_payload["source_attachment_mode"]) == "pdf_only"
    assert cell_value(stored.metadata_payload["publication_mode"]) == "DIRECT_PDF"
    assert "issue_based" not in stored.business_key
    assert client.downloaded == [pdf_url]
    assert db_session.query(FileProcessing).count() == 0


def _create_qinghai_source(db_session, *, config):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="青海省住房和城乡建设厅-造价信息",
        data_domain="cost_info",
        province="青海省",
        region_code="630000",
        config=config,
    )
