import json

from app.collection import create_data_source
from app.linxia_cost_info import (
    LINXIA_COST_INFO_PARSER_VERSION,
    LINXIA_LIST_API_URL,
    ingest_linxia_cost_info_issue,
    linxia_cost_info_source_config,
    list_linxia_cost_info_issues,
)
from app.models import Archive, FileProcessing, IngestEvent
from app.storage import FakeObjectStore


def cell_value(cell):
    return cell["value"]


class FakeLinxiaCostInfoClient:
    def __init__(self, *, list_payload="", detail_html_by_url=None, bytes_by_url=None):
        self.list_payload = list_payload
        self.detail_html_by_url = detail_html_by_url or {}
        self.bytes_by_url = bytes_by_url or {}
        self.get_text_calls = []
        self.downloaded = []

    def get_text(self, url):
        self.get_text_calls.append(url)
        if url.startswith(LINXIA_LIST_API_URL):
            return self.list_payload
        if url in self.detail_html_by_url:
            return self.detail_html_by_url[url]
        raise AssertionError(f"unexpected GET: {url}")

    def get_bytes(self, url):
        self.downloaded.append(url)
        if url in self.bytes_by_url:
            return self.bytes_by_url[url], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if "document/download" in url:
            return b"linxia xlsx bytes", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        raise AssertionError(f"unexpected download: {url}")


def test_linxia_source_config_marks_prefecture_bimonthly_xlsx_source():
    config = linxia_cost_info_source_config()
    parser = config["parser"]["parsers"][LINXIA_COST_INFO_PARSER_VERSION]
    target = config["coverage_expectation"]["target_regions"][0]

    assert config["stable"]["site_id"] == "cost_info.gs.linxia.zjj.price_info"
    assert config["stable"]["region_code"] == "622900"
    assert config["stable"]["coverage_region_code"] == "622900"
    assert config["stable"]["publisher_scope"] == "prefecture"
    assert config["stable"]["publisher_name"] == "临夏州住房和城乡建设局"
    assert config["stable"]["publisher_type"] == "official_housing_urban_rural_development"
    assert config["stable"]["period_kind"] == "bimonthly"
    assert config["price_coordinates"]["price_kind"] == "guidance"
    assert "并非政府定价" in config["price_coordinates"]["price_kind_basis"]
    assert parser["adapter_kind"] == "linxia_xlsx"
    assert parser["list_strategy"] == "jpaas_unit_list_detail_xlsx_anchor"
    assert parser["detail_strategy"] == "article_detail_xlsx_anchor"
    assert parser["period"]["kind"] == "bimonthly"
    assert target["region_code"] == "622900"
    assert target["region_name"] == "临夏回族自治州"
    assert target["target_level"] == "prefecture"


def test_linxia_list_parses_jpaas_unit_payload_and_filters_non_cost_info_notices():
    client = FakeLinxiaCostInfoClient(list_payload=_linxia_list_payload())
    parser = linxia_cost_info_source_config()["parser"]["parsers"][LINXIA_COST_INFO_PARSER_VERSION]

    rows = list_linxia_cost_info_issues(client, parser=parser, max_items=5)

    assert rows == [
        {
            "source_item_key": "ad11ac42d05a440ca88b05cd72968a1f",
            "title": "临夏州2026年3-4月建设工程一类材料信息价",
            "source_title": "关于发布临夏州2026年3—4月建设工程...",
            "period": "2026年3-4月",
            "period_year": 2026,
            "period_start_month": 3,
            "period_end_month": 4,
            "period_start": "2026-03",
            "period_end": "2026-04",
            "publish_date": "2026-05-20",
            "detail_url": (
                "https://www.linxia.gov.cn/lxz/zwgk/bmxxgkpt/lxzzjj/fdzdgknr/bmwj/art/2026/"
                "art_ad11ac42d05a440ca88b05cd72968a1f.html"
            ),
        },
        {
            "source_item_key": "0f91b139e71345d88ebf188bd3d73073",
            "title": "临夏州2026年1-2月建设工程一类材料信息价",
            "source_title": "关于发布临夏州2026年1—2月建设工程...",
            "period": "2026年1-2月",
            "period_year": 2026,
            "period_start_month": 1,
            "period_end_month": 2,
            "period_start": "2026-01",
            "period_end": "2026-02",
            "publish_date": "2026-03-12",
            "detail_url": (
                "https://www.linxia.gov.cn/lxz/zwgk/bmxxgkpt/lxzzjj/fdzdgknr/bmwj/art/2026/"
                "art_0f91b139e71345d88ebf188bd3d73073.html"
            ),
        },
    ]


def test_linxia_detail_xlsx_attachment_ingests_bimonthly_archive_without_processing(db_session):
    detail_url = (
        "https://www.linxia.gov.cn/lxz/zwgk/bmxxgkpt/lxzzjj/fdzdgknr/bmwj/art/2026/"
        "art_ad11ac42d05a440ca88b05cd72968a1f.html"
    )
    xlsx_url = (
        "https://www.linxia.gov.cn/api-gateway/jpaas-web-server/front/document/download?"
        "fileUrl=encoded&fileName=%E4%B8%B4%E5%A4%8F%E5%B7%9E2026%E5%B9%B43%7E4%E6%9C%88.xlsx"
    )
    client = FakeLinxiaCostInfoClient(
        list_payload=_linxia_list_payload(),
        detail_html_by_url={detail_url: _linxia_detail_html()},
        bytes_by_url={xlsx_url: b"linxia 2026 3-4 xlsx"},
    )
    config = linxia_cost_info_source_config()
    parser = config["parser"]["parsers"][LINXIA_COST_INFO_PARSER_VERSION]
    source = _create_linxia_source(db_session, config=config)
    row = list_linxia_cost_info_issues(client, parser=parser, max_items=1)[0]

    archive = ingest_linxia_cost_info_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=row,
        actor_id="linxia-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    event = db_session.query(IngestEvent).filter_by(source_item_key=row["source_item_key"]).one()

    assert stored.source_item_key == "ad11ac42d05a440ca88b05cd72968a1f"
    assert event.source_item_key == stored.source_item_key
    assert stored.region_code == "622900"
    assert stored.period_kind == "bimonthly"
    assert stored.price_kind == "guidance"
    assert cell_value(stored.metadata_payload["period"]) == "2026年3-4月"
    assert cell_value(stored.metadata_payload["period_raw"]) == "2026年3-4月"
    assert cell_value(stored.metadata_payload["period_year"]) == 2026
    assert cell_value(stored.metadata_payload["period_start_month"]) == 3
    assert cell_value(stored.metadata_payload["period_end_month"]) == 4
    assert cell_value(stored.metadata_payload["source_title"]) == row["source_title"]
    assert cell_value(stored.metadata_payload["publisher_type"]) == "official_housing_urban_rural_development"
    assert cell_value(stored.metadata_payload["publisher_scope"]) == "prefecture"
    assert cell_value(stored.metadata_payload["source_attachment_mode"]) == "xlsx_attachment"
    assert cell_value(stored.metadata_payload["publication_mode"]) == "DETAIL_PAGE_ATTACHMENT"
    assert cell_value(stored.metadata_payload["price_source_type"]) == "info_price"
    assert "计价参考" in cell_value(stored.metadata_payload["price_kind_basis"])
    assert cell_value(stored.metadata_payload["attachment_discovery_methods"]) == ["detail_page_xlsx_anchor"]
    assert client.get_text_calls[-1] == detail_url
    assert client.downloaded == [xlsx_url]
    assert db_session.query(FileProcessing).count() == 0


def _linxia_list_payload():
    html = """
    <ul class='ajax-ul'>
      <li class='cf'><a class='fl' href='/lxz/zwgk/bmxxgkpt/lxzzjj/fdzdgknr/bmwj/art/2026/art_bef1ab6fd37b42eeb06832fd7c82c0d7.html'>关于印发《临夏州住建系统2026年法治建...</a><span class='fr'>2026-06-25</span></li>
      <li class='cf'><a class='fl' href='/lxz/zwgk/bmxxgkpt/lxzzjj/fdzdgknr/bmwj/art/2026/art_ad11ac42d05a440ca88b05cd72968a1f.html'>关于发布临夏州2026年3—4月建设工程...</a><span class='fr'>2026-05-20</span></li>
      <li class='cf'><a class='fl' href='/lxz/zwgk/bmxxgkpt/lxzzjj/fdzdgknr/bmwj/art/2026/art_0f91b139e71345d88ebf188bd3d73073.html'>关于发布临夏州2026年1—2月建设工程...</a><span class='fr'>2026-03-12</span></li>
      <li class='cf'><a class='fl' href='/lxz/zwgk/bmxxgkpt/lxzzjj/fdzdgknr/bmwj/art/2026/art_fcd956074ee546d39102a11e26bf8e15.html'>关于对临夏州房屋建筑和市政基础设施建设工程...</a><span class='fr'>2026-01-20</span></li>
    </ul>
    """
    return json.dumps({"success": True, "data": {"html": html}}, ensure_ascii=False)


def _linxia_detail_html():
    return """
    <html><head>
      <meta name="ArticleTitle" content="关于发布临夏州2026年3—4月建设工程一类材料信息价的通知">
      <meta name="PubDate" content="2026-05-20 16:34">
      <meta name="ContentSource" content="临夏州住房和城乡建设局">
    </head><body>
      <div class="dqwz">
        <a href="/lxz/index.html">首页</a>
        <a href="javascript:doZoom(18)">大</a>
      </div>
      <h3>关于发布临夏州2026年3—4月建设工程一类材料信息价的通知</h3>
      <div class="neirong">
        <p>此信息价并非“政府定价”或者“政府指导价”，仅作为编制建设工程概预算、招投标控制价等的计价参考。</p>
        <p>附件：<a href="/api-gateway/jpaas-web-server/front/document/download?fileUrl=encoded&fileName=%E4%B8%B4%E5%A4%8F%E5%B7%9E2026%E5%B9%B43%7E4%E6%9C%88.xlsx">临夏州2026年3~4月建设工程一类材料信息价.xlsx</a></p>
      </div>
    </body></html>
    """


def _create_linxia_source(db_session, *, config):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="临夏州住房和城乡建设局-信息价",
        data_domain="cost_info",
        province="甘肃省",
        city="临夏回族自治州",
        region_code="622900",
        config=config,
    )
