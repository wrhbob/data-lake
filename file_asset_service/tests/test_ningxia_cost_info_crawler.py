from app.collection import create_data_source
from app.models import Archive, FileProcessing, IngestEvent
from app.ningxia_cost_info import (
    NINGXIA_COST_INFO_PARSER_VERSION,
    ningxia_cost_info_source_config,
    ingest_ningxia_cost_info_issue,
    list_ningxia_cost_info_issues,
)
from app.storage import FakeObjectStore


def cell_value(cell):
    return cell["value"]


class FakeNingxiaCostInfoClient:
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
            return b"%PDF ningxia issue bytes", "application/pdf"
        raise AssertionError(f"unexpected download: {url}")


def test_ningxia_source_config_marks_government_issue_based_guidance_source():
    config = ningxia_cost_info_source_config()
    parser = config["parser"]["parsers"][NINGXIA_COST_INFO_PARSER_VERSION]
    target = config["coverage_expectation"]["target_regions"][0]

    assert config["stable"]["site_id"] == "cost_info.nx.jst.main"
    assert config["stable"]["region_code"] == "640000"
    assert config["stable"]["publisher_type"] == "official_housing_urban_rural_development"
    assert config["stable"]["publisher_name"] == "宁夏回族自治区住房和城乡建设厅"
    assert config["stable"]["period_kind"] == "issue_based"
    assert config["price_coordinates"]["price_kind"] == "guidance"
    assert parser["period"]["kind"] == "issue_based"
    assert parser["title_regex"] == r"(?P<period>(?P<year>20\d{2})年第(?P<issue>\d{1,2})(?:[—–-](?P<issue_end>\d{1,2}))?期)\s*《宁夏工程造价》"
    assert target["region_code"] == "640000"
    assert target["requires_city_source"] is False


def test_ningxia_list_discovery_anchors_on_journal_title_even_when_notice_and_filters_other_notices():
    config = ningxia_cost_info_source_config()
    parser = config["parser"]["parsers"][NINGXIA_COST_INFO_PARSER_VERSION]
    list_url = parser["list_url"]
    client = FakeNingxiaCostInfoClient(
        html_by_url={
            list_url: """
                <ul class="sj-list">
                  <li><a class="ellipsis fl" href="./202605/t20260518_5242529.html" target="_blank">
                    自治区住房和城乡建设厅关于发布2026年第2期《宁夏工程造价》的通知</a>
                    <time class="fr">2026-05-18</time></li>
                  <li><a class="ellipsis fl" href="./202603/t20260324_5201067.html" target="_blank">
                    自治区住房和城乡建设厅关于发布 2026年第1期《宁夏工程造价》的通知</a>
                    <time class="fr">2026-03-20</time></li>
                  <li><a class="ellipsis fl" href="./202508/t20250821_4994519.html" target="_blank">
                    关于厂商价格信息管理系统上线运行的通知</a>
                    <time class="fr">2025-08-21</time></li>
                  <li><a class="ellipsis fl" href="./202512/t20251226_5120155.html" target="_blank">
                    自治区住房和城乡建设厅关于发布《宁夏回族自治区城市地下管网更新改造工程消耗量定额》的通知</a>
                    <time class="fr">2025-12-08</time></li>
                  <li><a class="ellipsis fl" href="./202502/t20250220_4800000.html" target="_blank">
                    自治区住房和城乡建设厅关于建筑业企业资质公告</a>
                    <time class="fr">2025-02-20</time></li>
                  <li><a class="ellipsis fl" href="./202602/t20260228_5180909.html" target="_blank">
                    自治区住房和城乡建设厅关于发布 《宁夏回族自治区安装工程材料价格信息》 （2025版）的通知</a>
                    <time class="fr">2026-02-12</time></li>
                </ul>
            """
        }
    )

    rows = list_ningxia_cost_info_issues(client, parser=parser, page=1)

    assert [row["title"] for row in rows] == [
        "自治区住房和城乡建设厅关于发布2026年第2期《宁夏工程造价》的通知",
        "自治区住房和城乡建设厅关于发布 2026年第1期《宁夏工程造价》的通知",
    ]
    assert all("通知" in str(row["title"]) for row in rows)
    assert rows[0]["period"] == "2026年第2期"
    assert rows[0]["period_year"] == 2026
    assert rows[0]["period_issue_no"] == 2
    assert rows[0]["period_issue_end_no"] is None
    assert rows[0]["source_item_key"] == "ztzl/gczj/zjtt/202605/t20260518_5242529.html"
    assert rows[0]["detail_url"] == "https://jst.nx.gov.cn/ztzl/gczj/zjtt/202605/t20260518_5242529.html"
    assert all("厂商价格" not in str(row["title"]) for row in rows)
    assert all("消耗量定额" not in str(row["title"]) for row in rows)
    assert all("资质公告" not in str(row["title"]) for row in rows)


def test_ningxia_issue_ingests_detail_pdf_layer0_guidance_and_no_processing(db_session):
    detail_url = "https://jst.nx.gov.cn/ztzl/gczj/zjtt/202605/t20260518_5242529.html"
    pdf_url = "https://jst.nx.gov.cn/ztzl/gczj/zjtt/202605/P020260518392313525886.pdf"
    config = ningxia_cost_info_source_config()
    parser = config["parser"]["parsers"][NINGXIA_COST_INFO_PARSER_VERSION]
    list_url = parser["list_url"]
    client = FakeNingxiaCostInfoClient(
        html_by_url={
            list_url: """
                <ul class="sj-list">
                  <li><a class="ellipsis fl" href="./202605/t20260518_5242529.html" target="_blank">
                    自治区住房和城乡建设厅关于发布2026年第2期《宁夏工程造价》的通知</a>
                    <time class="fr">2026-05-18</time></li>
                </ul>
            """,
            detail_url: """
                <html><head><meta name="ArticleTitle"
                  content="自治区住房和城乡建设厅关于发布2026年第2期《宁夏工程造价》的通知"></head>
                  <body>
                    <script type="text/javascript">
                    var hasFJ='<a href="./P020260518392313525886.pdf">工程造价2026第2期..pdf</a>';
                    </script>
                  </body>
                </html>
            """,
        },
        bytes_by_url={pdf_url: b"%PDF ningxia 2026 issue 2"},
    )
    source = _create_ningxia_source(db_session, config=config)
    row = list_ningxia_cost_info_issues(client, parser=parser)[0]

    archive = ingest_ningxia_cost_info_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=row,
        actor_id="ningxia-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    event = db_session.query(IngestEvent).filter_by(
        source_item_key="ztzl/gczj/zjtt/202605/t20260518_5242529.html"
    ).one()

    assert stored.source_item_key == "ztzl/gczj/zjtt/202605/t20260518_5242529.html"
    assert event.source_item_key == "ztzl/gczj/zjtt/202605/t20260518_5242529.html"
    assert stored.period_kind == "issue_based"
    assert stored.price_kind == "guidance"
    assert cell_value(stored.metadata_payload["period"]) == "2026年第2期"
    assert cell_value(stored.metadata_payload["period_raw"]) == "2026年第2期"
    assert cell_value(stored.metadata_payload["period_start"]) is None
    assert cell_value(stored.metadata_payload["period_year"]) == 2026
    assert cell_value(stored.metadata_payload["period_issue_no"]) == 2
    assert "period_month" not in stored.metadata_payload
    assert cell_value(stored.metadata_payload["publisher_type"]) == "official_housing_urban_rural_development"
    assert cell_value(stored.metadata_payload["publisher_scope"]) == "province"
    assert cell_value(stored.metadata_payload["source_attachment_mode"]) == "pdf_attachment"
    assert cell_value(stored.metadata_payload["publication_mode"]) == "DETAIL_PAGE_ATTACHMENT"
    assert "issue_based" not in stored.business_key
    assert client.requested_text == [list_url, detail_url]
    assert client.downloaded == [pdf_url]
    assert db_session.query(FileProcessing).count() == 0


def _create_ningxia_source(db_session, *, config):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="宁夏回族自治区住房和城乡建设厅-工程造价",
        data_domain="cost_info",
        province="宁夏回族自治区",
        region_code="640000",
        config=config,
    )
