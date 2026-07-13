from app.collection import create_data_source
from app.hainan_cost_info import (
    HAINAN_COST_INFO_PARSER_VERSION,
    hainan_cost_info_source_config,
    ingest_hainan_cost_info_issue,
    list_hainan_cost_info_issues,
)
from app.models import Archive, FileProcessing, IngestEvent
from app.storage import FakeObjectStore


def cell_value(cell):
    return cell["value"]


class FakeHainanCostInfoClient:
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
            raise AssertionError(f"unexpected text request: {url}") from exc

    def get_bytes(self, url):
        self.downloaded.append(url)
        if url in self.bytes_by_url:
            return self.bytes_by_url[url], "application/pdf"
        if url.endswith(".pdf"):
            return b"%PDF hainan market reference bytes", "application/pdf"
        raise AssertionError(f"unexpected download: {url}")


def test_hainan_source_config_marks_government_monthly_guidance_source_despite_market_reference_title():
    config = hainan_cost_info_source_config()
    parser = config["parser"]["parsers"][HAINAN_COST_INFO_PARSER_VERSION]
    target = config["coverage_expectation"]["target_regions"][0]

    assert config["stable"]["site_id"] == "cost_info.hi.zjt.guidance"
    assert config["stable"]["region_code"] == "460000"
    assert config["stable"]["publisher_type"] == "official_housing_urban_rural_development"
    assert config["stable"]["publisher_name"] == "海南省住房和城乡建设厅"
    assert config["stable"]["period_kind"] == "monthly"
    assert config["price_coordinates"]["price_kind"] == "guidance"
    assert parser["period"]["kind"] == "monthly"
    assert parser["title_required_keywords"] == ["海南省建设工程", "市场参考价"]
    assert "编制说明自证为信息价" in target["coverage_note"]
    assert target["region_code"] == "460000"
    assert target["requires_city_source"] is False


def test_hainan_list_discovers_monthly_guidance_pdf_named_market_reference_and_filters_non_matching_titles():
    config = hainan_cost_info_source_config()
    parser = config["parser"]["parsers"][HAINAN_COST_INFO_PARSER_VERSION]
    list_url = parser["list_url"]
    client = FakeHainanCostInfoClient(
        html_by_url={
            list_url: """
                <ul>
                  <li><a href="/szjt/dejgxx/202606/48b7466e45854b30a0f0e43c2136c366.shtml"
                    title="2026年5月海南省建设工程主要材料、园林绿化苗木及施工机具与周转材料租赁市场参考价">
                    <span>2026-06-16</span>2026年5月海南省建设工程主要材料、园林绿化苗木及施工机具与周转材料租赁市场参考价</a></li>
                  <li><a href="/szjt/dejgxx/202605/9369d7be3ee94be5bf944a98176dea9b.shtml"
                    title="2026年4月海南省建设工程主要材料、园林绿化苗木及施工机具与周转材料租赁市场参考价">
                    <span>2026-05-20</span>2026年4月海南省建设工程主要材料、园林绿化苗木及施工机具与周转材料租赁市场参考价</a></li>
                  <li><a href="/szjt/dejgxx/202606/notice.shtml"
                    title="关于建设工程造价咨询企业检查的通知"><span>2026-06-01</span>通知</a></li>
                  <li><a href="/szjt/dejgxx/202606/other.shtml"
                    title="2026年5月海南省交通建设工程材料价格信息"><span>2026-06-03</span>交通材料</a></li>
                </ul>
            """
        }
    )

    rows = list_hainan_cost_info_issues(client, parser=parser)

    assert [row["period"] for row in rows] == ["2026-05", "2026-04"]
    assert rows[0]["period_year"] == 2026
    assert rows[0]["period_month"] == 5
    assert rows[0]["publish_date"] == "2026-06-16"
    assert rows[0]["source_item_key"] == "szjt/dejgxx/202606/48b7466e45854b30a0f0e43c2136c366.shtml"
    assert rows[0]["detail_url"] == "https://zjt.hainan.gov.cn/szjt/dejgxx/202606/48b7466e45854b30a0f0e43c2136c366.shtml"
    assert all("交通建设" not in str(row["title"]) for row in rows)
    assert all("通知" not in str(row["title"]) for row in rows)


def test_hainan_guidance_pdf_named_market_reference_ingests_layer0_without_processing(db_session):
    detail_url = "https://zjt.hainan.gov.cn/szjt/dejgxx/202606/48b7466e45854b30a0f0e43c2136c366.shtml"
    pdf_url = (
        "https://zjt.hainan.gov.cn/szjt/dejgxx/202606/"
        "48b7466e45854b30a0f0e43c2136c366/files/bbeddc37a9ae4c94a63cf8aba8b25f1f.pdf"
    )
    config = hainan_cost_info_source_config()
    parser = config["parser"]["parsers"][HAINAN_COST_INFO_PARSER_VERSION]
    list_url = parser["list_url"]
    client = FakeHainanCostInfoClient(
        html_by_url={
            list_url: """
                <ul>
                  <li><a href="/szjt/dejgxx/202606/48b7466e45854b30a0f0e43c2136c366.shtml"
                    title="2026年5月海南省建设工程主要材料、园林绿化苗木及施工机具与周转材料租赁市场参考价">
                    <span>2026-06-16</span>2026年5月海南省建设工程主要材料、园林绿化苗木及施工机具与周转材料租赁市场参考价</a></li>
                </ul>
            """,
            detail_url: """
                <UCAPCONTENT>
                  <img src="48b7466e45854b30a0f0e43c2136c366/images/icon.gif">
                  <a href="48b7466e45854b30a0f0e43c2136c366/files/bbeddc37a9ae4c94a63cf8aba8b25f1f.pdf" target="_blank">
                    2026年5月海南省建设工程主要材料、园林绿化苗木及施工机具与周转材料租赁市场参考价
                  </a>
                </UCAPCONTENT>
            """,
        },
        bytes_by_url={pdf_url: b"%PDF hainan 2026-05 market reference"},
    )
    source = _create_hainan_source(db_session, config=config)
    row = list_hainan_cost_info_issues(client, parser=parser)[0]

    archive = ingest_hainan_cost_info_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=row,
        actor_id="hainan-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    event = db_session.query(IngestEvent).filter_by(
        source_item_key="szjt/dejgxx/202606/48b7466e45854b30a0f0e43c2136c366.shtml"
    ).one()

    assert stored.source_item_key == "szjt/dejgxx/202606/48b7466e45854b30a0f0e43c2136c366.shtml"
    assert event.source_item_key == "szjt/dejgxx/202606/48b7466e45854b30a0f0e43c2136c366.shtml"
    assert stored.period_kind == "monthly"
    assert stored.price_kind == "guidance"
    assert cell_value(stored.metadata_payload["period"]) == "2026-05"
    assert cell_value(stored.metadata_payload["period_raw"]) == stored.title
    assert cell_value(stored.metadata_payload["period_start"]) == "2026-05"
    assert cell_value(stored.metadata_payload["publisher_type"]) == "official_housing_urban_rural_development"
    assert cell_value(stored.metadata_payload["publisher_scope"]) == "province"
    assert cell_value(stored.metadata_payload["source_attachment_mode"]) == "pdf_attachment"
    assert cell_value(stored.metadata_payload["publication_mode"]) == "DETAIL_PAGE_ATTACHMENT"
    assert cell_value(stored.metadata_payload["price_source_type"]) == "info_price"
    assert client.requested_text == [list_url, detail_url]
    assert client.downloaded == [pdf_url]
    assert db_session.query(FileProcessing).count() == 0


def _create_hainan_source(db_session, *, config):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="海南省住房和城乡建设厅-价格信息",
        data_domain="cost_info",
        province="海南省",
        region_code="460000",
        config=config,
    )
