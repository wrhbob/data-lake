from app.collection import create_data_source
from app.models import Archive, FileProcessing, IngestEvent
from app.storage import FakeObjectStore
from app.wuhan_cost_info import (
    WUHAN_COST_INFO_PARSER_VERSION,
    ingest_wuhan_cost_info_issue,
    list_wuhan_cost_info_issues,
    wuhan_cost_info_source_config,
)


def cell_value(cell):
    return cell["value"]


class FakeWuhanCostInfoClient:
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
            return b"%PDF wuhan comprehensive price bytes", "application/pdf"
        raise AssertionError(f"unexpected download: {url}")


def test_wuhan_source_config_marks_city_monthly_guidance_source():
    config = wuhan_cost_info_source_config()
    parser = config["parser"]["parsers"][WUHAN_COST_INFO_PARSER_VERSION]
    target = config["coverage_expectation"]["target_regions"][0]

    assert config["stable"]["site_id"] == "cost_info.hb.wuhan.zgj.comprehensive"
    assert config["stable"]["region_code"] == "420100"
    assert config["stable"]["publisher_scope"] == "city"
    assert config["stable"]["publisher_region_code"] == "420100"
    assert config["stable"]["period_kind"] == "monthly"
    assert config["price_coordinates"]["price_kind"] == "guidance"
    assert parser["period"]["kind"] == "monthly"
    assert parser["title_required_keywords"] == ["武汉市建设工程综合价格信息"]
    assert target["region_code"] == "420100"
    assert target["requires_city_source"] is False


def test_wuhan_list_discovers_monthly_comprehensive_price_pdfs_and_filters_mixed_rows():
    config = wuhan_cost_info_source_config()
    parser = config["parser"]["parsers"][WUHAN_COST_INFO_PARSER_VERSION]
    list_url = parser["list_url"]
    client = FakeWuhanCostInfoClient(
        html_by_url={
            list_url: """
                <ul class="info-list">
                  <li><a href="https://zgj.wuhan.gov.cn/xxgk/xxgkml/sjfb/zyjzcljgjc/202605/t20260529_2771013.shtml"
                    title="2026年5月主要建筑材料价格监测情况简报">2026年5月主要建筑材料价格监测情况简报</a><span>2026-05-29</span></li>
                  <li><a href="https://zgj.wuhan.gov.cn/xxgk/xxgkml/sjfb/zyjzcljgjc/202605/t20260529_2771009.shtml"
                    title="市招标设计造价中心、市绿建中心关于发布2026年5月武汉市建设工程综合价格信息的通知">综合价格信息</a><span>2026-05-29</span></li>
                  <li><a href="https://zgj.wuhan.gov.cn/xxgk/xxgkml/sjfb/zyjzcljgjc/202604/t20260430_2759314.shtml"
                    title="市招标设计造价中心、市绿建中心关于发布2026年4月武汉市建设工程综合价格信息的通知">综合价格信息</a><span>2026-04-30</span></li>
                  <li><a href="https://zgj.wuhan.gov.cn/xxgk/xxgkml/sjfb/zyjzcljgjc/202604/t20260417_2754217.shtml"
                    title="市招标设计造价中心关于发布二O二六年第一季度武汉市建设工程人工成本信息的通知">人工成本</a><span>2026-04-17</span></li>
                  <li><a href="https://zgj.wuhan.gov.cn/xxgk/xxgkml/sjfb/zyjzcljgjc/202604/t20260417_2754226.shtml"
                    title="市招标设计造价中心关于发布二O二六年第一季度武汉市苗木参考价格的通知">苗木参考价格</a><span>2026-04-17</span></li>
                  <li><a href="https://zgj.wuhan.gov.cn/xxgk/xxgkml/sjfb/zyjzcljgjc/202506/t20250630_2612345.shtml"
                    title="市招标设计造价中心关于发布二O二五年第二季度武汉市缺类缺项材料价格信息的通知">缺类缺项材料</a><span>2025-06-30</span></li>
                </ul>
            """
        }
    )

    rows = list_wuhan_cost_info_issues(client, parser=parser)

    assert [row["period"] for row in rows] == ["2026-05", "2026-04"]
    assert rows[0]["period_year"] == 2026
    assert rows[0]["period_month"] == 5
    assert rows[0]["publish_date"] == "2026-05-29"
    assert rows[0]["source_item_key"] == "xxgk/xxgkml/sjfb/zyjzcljgjc/202605/t20260529_2771009.shtml"
    assert rows[0]["detail_url"] == (
        "https://zgj.wuhan.gov.cn/xxgk/xxgkml/sjfb/zyjzcljgjc/202605/t20260529_2771009.shtml"
    )
    assert all("主要建筑材料价格监测情况简报" not in str(row["title"]) for row in rows)
    assert all("人工成本" not in str(row["title"]) for row in rows)
    assert all("苗木参考价格" not in str(row["title"]) for row in rows)
    assert all("缺类缺项材料" not in str(row["title"]) for row in rows)


def test_wuhan_monthly_comprehensive_pdf_ingests_layer0_without_processing(db_session):
    detail_url = "https://zgj.wuhan.gov.cn/xxgk/xxgkml/sjfb/zyjzcljgjc/202605/t20260529_2771009.shtml"
    pdf_url = "https://zgj.wuhan.gov.cn/xxgk/xxgkml/sjfb/zyjzcljgjc/202605/P020260529574502592380.pdf"
    config = wuhan_cost_info_source_config()
    parser = config["parser"]["parsers"][WUHAN_COST_INFO_PARSER_VERSION]
    list_url = parser["list_url"]
    client = FakeWuhanCostInfoClient(
        html_by_url={
            list_url: f"""
                <ul class="info-list">
                  <li><a href="{detail_url}"
                    title="市招标设计造价中心、市绿建中心关于发布2026年5月武汉市建设工程综合价格信息的通知">
                    综合价格信息</a><span>2026-05-29</span></li>
                </ul>
            """,
            detail_url: f"""
                <html><head><meta name="ArticleTitle"
                  content="市招标设计造价中心、市绿建中心关于发布2026年5月武汉市建设工程综合价格信息的通知"></head>
                  <body>
                    <script>var pdf = "{pdf_url}"</script>
                    <p id="annex">附件:</p>
                    <li><a href="{pdf_url}" target="_blank">
                      市招标设计造价中心、市绿建中心关于发布2026年5月武汉市建设工程综合价格信息的通知（武市政价字〔2026〕17号 ）.pdf
                    </a></li>
                  </body>
                </html>
            """,
        },
        bytes_by_url={pdf_url: b"%PDF wuhan 2026-05 comprehensive price"},
    )
    source = _create_wuhan_source(db_session, config=config)
    row = list_wuhan_cost_info_issues(client, parser=parser)[0]

    archive = ingest_wuhan_cost_info_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=row,
        actor_id="wuhan-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    event = db_session.query(IngestEvent).filter_by(
        source_item_key="xxgk/xxgkml/sjfb/zyjzcljgjc/202605/t20260529_2771009.shtml"
    ).one()

    assert stored.source_item_key == "xxgk/xxgkml/sjfb/zyjzcljgjc/202605/t20260529_2771009.shtml"
    assert event.source_item_key == "xxgk/xxgkml/sjfb/zyjzcljgjc/202605/t20260529_2771009.shtml"
    assert stored.period_kind == "monthly"
    assert stored.price_kind == "guidance"
    assert cell_value(stored.metadata_payload["period"]) == "2026-05"
    assert cell_value(stored.metadata_payload["period_raw"]) == stored.title
    assert cell_value(stored.metadata_payload["period_start"]) == "2026-05"
    assert cell_value(stored.metadata_payload["publisher_scope"]) == "city"
    assert cell_value(stored.metadata_payload["publisher_type"]) == "cost_station_public_institution"
    assert cell_value(stored.metadata_payload["source_attachment_mode"]) == "pdf_attachment"
    assert cell_value(stored.metadata_payload["publication_mode"]) == "DETAIL_PAGE_ATTACHMENT"
    assert cell_value(stored.metadata_payload["price_source_type"]) == "info_price"
    assert client.requested_text == [list_url, detail_url]
    assert client.downloaded == [pdf_url]
    assert db_session.query(FileProcessing).count() == 0


def _create_wuhan_source(db_session, *, config):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="武汉市住房和城市更新局-建设工程综合价格信息",
        data_domain="cost_info",
        province="湖北省",
        city="武汉市",
        region_code="420100",
        config=config,
    )
