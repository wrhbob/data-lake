import json

from app.collection import create_data_source
from app.models import Archive, ArchiveFile, FileAsset, FileProcessing, IngestEvent
from app.pingliang_cost_info import (
    PINGLIANG_COST_INFO_PARSER_VERSION,
    PINGLIANG_LIST_API_URL,
    ingest_pingliang_cost_info_issue,
    list_pingliang_cost_info_issues,
    pingliang_cost_info_source_config,
)
from app.storage import FakeObjectStore


def cell_value(cell):
    return cell["value"]


class FakePingliangCostInfoClient:
    def __init__(self, *, list_payload="", detail_html_by_url=None, bytes_by_url=None):
        self.list_payload = list_payload
        self.detail_html_by_url = detail_html_by_url or {}
        self.bytes_by_url = bytes_by_url or {}
        self.get_text_calls = []
        self.downloaded = []

    def get_text(self, url):
        self.get_text_calls.append(url)
        if url.startswith(PINGLIANG_LIST_API_URL):
            return self.list_payload
        if url in self.detail_html_by_url:
            return self.detail_html_by_url[url]
        raise AssertionError(f"unexpected GET: {url}")

    def get_bytes(self, url):
        self.downloaded.append(url)
        if url in self.bytes_by_url:
            return self.bytes_by_url[url], "application/pdf"
        if "document/download" in url:
            return b"%PDF pingliang material price bytes", "application/pdf"
        raise AssertionError(f"unexpected download: {url}")


def test_pingliang_source_config_marks_city_issue_based_pdf_source():
    config = pingliang_cost_info_source_config()
    parser = config["parser"]["parsers"][PINGLIANG_COST_INFO_PARSER_VERSION]
    target = config["coverage_expectation"]["target_regions"][0]

    assert config["stable"]["site_id"] == "cost_info.gs.pingliang.zjj.price_info"
    assert config["stable"]["region_code"] == "620800"
    assert config["stable"]["coverage_region_code"] == "620800"
    assert config["stable"]["publisher_scope"] == "city"
    assert config["stable"]["publisher_name"] == "平凉市住房和城乡建设局"
    assert config["stable"]["publisher_type"] == "official_housing_urban_rural_development"
    assert config["stable"]["period_kind"] == "issue_based"
    assert config["price_coordinates"]["price_kind"] == "guidance"
    assert parser["adapter_kind"] == "pingliang_pdf"
    assert parser["list_strategy"] == "jpaas_unit_list_detail_pdf_anchor"
    assert parser["detail_strategy"] == "article_detail_filtered_pdf_anchor"
    assert parser["period"]["kind"] == "issue_based"
    assert config["source_shape"]["source_attachment_mode"] == "pdf_attachment"
    assert target["region_code"] == "620800"
    assert target["region_name"] == "平凉市"
    assert target["target_level"] == "city"


def test_pingliang_list_parses_jpaas_payload_and_filters_non_material_price_notices():
    client = FakePingliangCostInfoClient(list_payload=_pingliang_list_payload())
    parser = pingliang_cost_info_source_config()["parser"]["parsers"][PINGLIANG_COST_INFO_PARSER_VERSION]

    rows = list_pingliang_cost_info_issues(client, parser=parser, max_items=5)

    assert rows == [
        {
            "source_item_key": "675f634918124726aa6103f0d96960d7",
            "title": "平凉市2026年第2期建设工程材料信息价格",
            "source_title": "平凉市住房和城乡建设局 关于发布平凉市2026年第二期建设工程造价信息的通知",
            "period": "2026年第2期",
            "period_year": 2026,
            "period_issue_no": 2,
            "publish_date": "2026-04-29",
            "detail_url": (
                "https://zjj.pingliang.gov.cn/zfxxgk/fdzdgknr/lzyj/sjbjzc/art/2026/"
                "art_675f634918124726aa6103f0d96960d7.html"
            ),
        },
        {
            "source_item_key": "6041e31da68e496c9a2fa47a11233fd3",
            "title": "平凉市2026年第1期建设工程材料信息价格",
            "source_title": "平凉市住房和城乡建设局 关于发布平凉市2026年第一期建设工程造价信息的通知",
            "period": "2026年第1期",
            "period_year": 2026,
            "period_issue_no": 1,
            "publish_date": "2026-03-06",
            "detail_url": (
                "https://zjj.pingliang.gov.cn/zfxxgk/fdzdgknr/lzyj/sjbjzc/art/2026/"
                "art_6041e31da68e496c9a2fa47a11233fd3.html"
            ),
        },
        {
            "source_item_key": "659fc9430e764d0db06fefe1f7612aa1",
            "title": "平凉市2025年第6期建设工程材料信息价格",
            "source_title": "平凉市住房和城乡建设局 关于发布平凉市2025年第六期建设工程材料信息价格的通知",
            "period": "2025年第6期",
            "period_year": 2025,
            "period_issue_no": 6,
            "publish_date": "2025-12-30",
            "detail_url": (
                "https://zjj.pingliang.gov.cn/zfxxgk/fdzdgknr/lzyj/sjbjzc/art/2026/"
                "art_659fc9430e764d0db06fefe1f7612aa1.html"
            ),
        },
    ]


def test_pingliang_detail_pdf_attachment_filters_non_price_pdf_and_ingests_archive(db_session):
    detail_url = (
        "https://zjj.pingliang.gov.cn/zfxxgk/fdzdgknr/lzyj/sjbjzc/art/2026/"
        "art_6041e31da68e496c9a2fa47a11233fd3.html"
    )
    material_pdf_url = (
        "https://zjj.pingliang.gov.cn/api-gateway/jpaas-web-server/front/document/download?"
        "fileUrl=material&fileName=%E5%B9%B3%E5%87%89%E5%B8%822026%E5%B9%B4%E7%AC%AC%E4%B8%80%E6%9C%9F"
        "%E5%BB%BA%E8%AE%BE%E5%B7%A5%E7%A8%8B%E6%9D%90%E6%96%99%E4%BF%A1%E6%81%AF%E4%BB%B7%E6%A0%BC"
        "%E6%B1%87%E6%80%BB%E8%A1%A8.pdf"
    )
    index_pdf_url = (
        "https://zjj.pingliang.gov.cn/api-gateway/jpaas-web-server/front/document/download?"
        "fileUrl=index&fileName=%E5%B9%B3%E5%87%89%E5%B8%822026%E5%B9%B4%E4%B8%8A%E5%8D%8A%E5%B9%B4"
        "%E6%88%BF%E5%B1%8B%E5%BB%BA%E7%AD%91%E5%AE%89%E8%A3%85%E9%80%A0%E4%BB%B7%E6%8C%87%E6%A0%87.pdf"
    )
    client = FakePingliangCostInfoClient(
        list_payload=_pingliang_list_payload(),
        detail_html_by_url={detail_url: _pingliang_detail_html(material_pdf_url, index_pdf_url)},
        bytes_by_url={material_pdf_url: b"%PDF pingliang 2026 issue 1 material price"},
    )
    config = pingliang_cost_info_source_config()
    parser = config["parser"]["parsers"][PINGLIANG_COST_INFO_PARSER_VERSION]
    source = _create_pingliang_source(db_session, config=config)
    row = list_pingliang_cost_info_issues(client, parser=parser, max_items=5)[1]

    archive = ingest_pingliang_cost_info_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=row,
        actor_id="pingliang-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    event = db_session.query(IngestEvent).filter_by(source_item_key=row["source_item_key"]).one()
    files = (
        db_session.query(ArchiveFile, FileAsset)
        .join(FileAsset, ArchiveFile.file_id == FileAsset.file_id)
        .filter(ArchiveFile.archive_id == archive.archive_id)
        .all()
    )

    assert stored.source_item_key == "6041e31da68e496c9a2fa47a11233fd3"
    assert event.source_item_key == stored.source_item_key
    assert stored.region_code == "620800"
    assert stored.period_kind == "issue_based"
    assert stored.price_kind == "guidance"
    assert cell_value(stored.metadata_payload["period"]) == "2026年第1期"
    assert cell_value(stored.metadata_payload["period_raw"]) == "2026年第1期"
    assert cell_value(stored.metadata_payload["period_year"]) == 2026
    assert cell_value(stored.metadata_payload["period_issue_no"]) == 1
    assert cell_value(stored.metadata_payload["source_title"]) == row["source_title"]
    assert cell_value(stored.metadata_payload["publisher_type"]) == "official_housing_urban_rural_development"
    assert cell_value(stored.metadata_payload["publisher_scope"]) == "city"
    assert cell_value(stored.metadata_payload["source_attachment_mode"]) == "pdf_attachment"
    assert cell_value(stored.metadata_payload["publication_mode"]) == "DETAIL_PAGE_ATTACHMENT"
    assert cell_value(stored.metadata_payload["price_source_type"]) == "info_price"
    assert "参考" in cell_value(stored.metadata_payload["price_kind_basis"])
    assert cell_value(stored.metadata_payload["attachment_discovery_methods"]) == ["detail_page_filtered_pdf_anchor"]
    assert [(mounted.file_role, asset.file_ext) for mounted, asset in files] == [("main_document", ".pdf")]
    assert client.get_text_calls[-1] == detail_url
    assert client.downloaded == [material_pdf_url]
    assert db_session.query(FileProcessing).count() == 0


def _pingliang_list_payload():
    html = """
    <div id="信息公开内容2" class="zfxxgknr-content">
      <div class="page-content">
        <ul>
          <li><a href="/zfxxgk/fdzdgknr/lzyj/sjbjzc/art/2026/art_675f634918124726aa6103f0d96960d7.html" title="平凉市住房和城乡建设局
关于发布平凉市2026年第二期建设工程造价信息的通知">平凉市住房和城乡建设局
关于发布平凉市2026年第二期建设工程造价信息的...</a><span>2026-04-29</span></li>
          <li><a href="/zfxxgk/fdzdgknr/lzyj/sjbjzc/art/2026/art_9635066bc8aa4016be0cb85b420f93f2.html" title="平凉市住宅物业服务招标投标管理办法">平凉市住宅物业服务招标投标管理办法</a><span>2026-04-10</span></li>
          <li><a href="/zfxxgk/fdzdgknr/lzyj/sjbjzc/art/2026/art_6041e31da68e496c9a2fa47a11233fd3.html" title="平凉市住房和城乡建设局
关于发布平凉市2026年第一期建设工程造价信息的通知">平凉市住房和城乡建设局
关于发布平凉市2026年第一期建设工程造价信息的...</a><span>2026-03-06</span></li>
          <li><a href="/zfxxgk/fdzdgknr/lzyj/sjbjzc/art/2026/art_b5b86648c7b34a71aaa30ddd354e7a07.html" title="平凉市住房和城乡建设局
关于发布《平凉市2025年下半年建设工程人工市场信息价》的通知">平凉市住房和城乡建设局
关于发布《平凉市2025年下半年建设工程人工市场...</a><span>2025-12-30</span></li>
          <li><a href="/zfxxgk/fdzdgknr/lzyj/sjbjzc/art/2026/art_659fc9430e764d0db06fefe1f7612aa1.html" title="平凉市住房和城乡建设局
关于发布平凉市2025年第六期建设工程材料信息价格的通知">平凉市住房和城乡建设局
关于发布平凉市2025年第六期建设工程材料信息价...</a><span>2025-12-30</span></li>
        </ul>
      </div>
    </div>
    """
    return json.dumps({"success": True, "data": {"html": html}}, ensure_ascii=False)


def _pingliang_detail_html(material_pdf_url, index_pdf_url):
    return f"""
    <html><head>
      <meta name="ArticleTitle" content="平凉市住房和城乡建设局 关于发布平凉市2026年第一期建设工程造价信息的通知">
      <meta name="PubDate" content="2026-03-07 08:59">
      <meta name="ContentSource" content="平凉市住房和城乡建设局">
      <meta name="Description" content="相关附件下载：平凉市2026年第一期建设工程材料信息价格汇总表.pdf 平凉市2026年上半年房屋建筑安装造价指标.pdf">
    </head><body>
      <div class="article-content">
        <p>相关附件下载：</p>
        <p><a href="{material_pdf_url}">平凉市2026年第一期建设工程材料信息价格汇总表.pdf</a></p>
        <p><a href="{index_pdf_url}">平凉市2026年上半年房屋建筑安装造价指标.pdf</a></p>
      </div>
    </body></html>
    """


def _create_pingliang_source(db_session, *, config):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="平凉市住房和城乡建设局-信息价",
        data_domain="cost_info",
        province="甘肃省",
        city="平凉市",
        region_code="620800",
        config=config,
    )
