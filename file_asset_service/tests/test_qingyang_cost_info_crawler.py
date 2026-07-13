from app.collection import create_data_source
from app.models import Archive, ArchiveFile, FileAsset, FileProcessing, IngestEvent
from app.qingyang_cost_info import (
    QINGYANG_COST_INFO_PARSER_VERSION,
    QINGYANG_LIST_URL,
    ingest_qingyang_cost_info_issue,
    list_qingyang_cost_info_issues,
    qingyang_cost_info_source_config,
)
from app.storage import FakeObjectStore


def cell_value(cell):
    return cell["value"]


class FakeQingyangCostInfoClient:
    def __init__(self, *, list_html="", detail_html_by_url=None, bytes_by_url=None):
        self.list_html = list_html
        self.detail_html_by_url = detail_html_by_url or {}
        self.bytes_by_url = bytes_by_url or {}
        self.get_text_calls = []
        self.downloaded = []

    def get_text(self, url):
        self.get_text_calls.append(url)
        if url == QINGYANG_LIST_URL:
            return self.list_html
        if url in self.detail_html_by_url:
            return self.detail_html_by_url[url]
        raise AssertionError(f"unexpected GET: {url}")

    def get_bytes(self, url):
        self.downloaded.append(url)
        if url in self.bytes_by_url:
            return self.bytes_by_url[url], "application/pdf"
        if url.lower().endswith(".pdf"):
            return b"%PDF qingyang cost info bytes", "application/pdf"
        raise AssertionError(f"unexpected download: {url}")


def test_qingyang_source_config_marks_city_issue_based_pdf_source():
    config = qingyang_cost_info_source_config()
    parser = config["parser"]["parsers"][QINGYANG_COST_INFO_PARSER_VERSION]
    target = config["coverage_expectation"]["target_regions"][0]

    assert config["stable"]["site_id"] == "cost_info.gs.qingyang.zjj.price_info"
    assert config["stable"]["region_code"] == "621000"
    assert config["stable"]["coverage_region_code"] == "621000"
    assert config["stable"]["publisher_scope"] == "city"
    assert config["stable"]["publisher_name"] == "庆阳市住房和城乡建设局"
    assert config["stable"]["publisher_type"] == "official_housing_urban_rural_development"
    assert config["stable"]["period_kind"] == "issue_based"
    assert config["price_coordinates"]["price_kind"] == "guidance"
    assert parser["adapter_kind"] == "qingyang_pdf"
    assert parser["list_strategy"] == "static_paginated_list_detail_pdf_anchor"
    assert parser["detail_strategy"] == "article_detail_pdf_anchor_or_data_powerurl"
    assert parser["period"]["kind"] == "issue_based"
    assert config["source_shape"]["source_attachment_mode"] == "pdf_attachment"
    assert target["region_code"] == "621000"
    assert target["region_name"] == "庆阳市"
    assert target["target_level"] == "city"


def test_qingyang_list_parses_static_list_and_keeps_material_and_labor_price_notices():
    client = FakeQingyangCostInfoClient(list_html=_qingyang_list_html())
    parser = qingyang_cost_info_source_config()["parser"]["parsers"][QINGYANG_COST_INFO_PARSER_VERSION]

    rows = list_qingyang_cost_info_issues(client, parser=parser, max_items=5)

    assert rows == [
        {
            "source_item_key": "59550",
            "title": "庆阳市2026年第2期建设工程材料信息价和机械租赁信息价",
            "source_title": "关于公布庆阳市2026年第二期建设工程材料信息价和机械租赁信息价的通知",
            "period": "2026年第2期",
            "period_year": 2026,
            "period_issue_no": 2,
            "price_subtype": "material_and_machine",
            "publish_date": "2026-04-30",
            "detail_url": "https://zhujianju.zgqingyang.gov.cn/zwgk/bmwj/zcwj/content_59550",
        },
        {
            "source_item_key": "59549",
            "title": "庆阳市2026年第1期建设工程人工市场信息价",
            "source_title": "关于公布庆阳市2026年第一期建设工程人工市场信息价的通知",
            "period": "2026年第1期",
            "period_year": 2026,
            "period_issue_no": 1,
            "price_subtype": "labor",
            "publish_date": "2026-04-30",
            "detail_url": "https://zhujianju.zgqingyang.gov.cn/zwgk/bmwj/zcwj/content_59549",
        },
        {
            "source_item_key": "344931",
            "title": "庆阳市2026年第1期建设工程材料信息价和机械租赁信息价",
            "source_title": "关于公布庆阳市2026年第一期建设工程材料信息价和机械租赁信息价的通知",
            "period": "2026年第1期",
            "period_year": 2026,
            "period_issue_no": 1,
            "price_subtype": "material_and_machine",
            "publish_date": "2026-03-11",
            "detail_url": "https://zhujianju.zgqingyang.gov.cn/zwgk/bmwj/zcwj/content_344931",
        },
    ]


def test_qingyang_detail_pdf_anchor_ingests_material_archive_without_processing(db_session):
    detail_url = "https://zhujianju.zgqingyang.gov.cn/zwgk/bmwj/zcwj/content_59550"
    pdf_url = (
        "https://zhujianju.zgqingyang.gov.cn/upload/zjj2/contentmanage/share/file/2026/04/30/"
        "59f3c8659554453bb267c0621514e23a.pdf"
    )
    client = FakeQingyangCostInfoClient(
        list_html=_qingyang_list_html(),
        detail_html_by_url={detail_url: _qingyang_anchor_detail_html(pdf_url)},
        bytes_by_url={pdf_url: b"%PDF qingyang 2026 issue 2 material"},
    )
    config = qingyang_cost_info_source_config()
    parser = config["parser"]["parsers"][QINGYANG_COST_INFO_PARSER_VERSION]
    source = _create_qingyang_source(db_session, config=config)
    row = list_qingyang_cost_info_issues(client, parser=parser, max_items=5)[0]

    archive = ingest_qingyang_cost_info_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=row,
        actor_id="qingyang-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    event = db_session.query(IngestEvent).filter_by(source_item_key=row["source_item_key"]).one()
    files = (
        db_session.query(ArchiveFile, FileAsset)
        .join(FileAsset, ArchiveFile.file_id == FileAsset.file_id)
        .filter(ArchiveFile.archive_id == archive.archive_id)
        .all()
    )

    assert stored.source_item_key == "59550"
    assert event.source_item_key == stored.source_item_key
    assert stored.region_code == "621000"
    assert stored.period_kind == "issue_based"
    assert stored.price_kind == "guidance"
    assert cell_value(stored.metadata_payload["period"]) == "2026年第2期"
    assert cell_value(stored.metadata_payload["period_raw"]) == "2026年第2期"
    assert cell_value(stored.metadata_payload["period_year"]) == 2026
    assert cell_value(stored.metadata_payload["period_issue_no"]) == 2
    assert cell_value(stored.metadata_payload["price_subtype"]) == "material_and_machine"
    assert cell_value(stored.metadata_payload["source_title"]) == row["source_title"]
    assert cell_value(stored.metadata_payload["publisher_type"]) == "official_housing_urban_rural_development"
    assert cell_value(stored.metadata_payload["source_attachment_mode"]) == "pdf_attachment"
    assert cell_value(stored.metadata_payload["publication_mode"]) == "DETAIL_PAGE_ATTACHMENT"
    assert cell_value(stored.metadata_payload["attachment_discovery_methods"]) == ["detail_page_pdf_anchor"]
    assert [(mounted.file_role, asset.file_ext) for mounted, asset in files] == [("main_document", ".pdf")]
    assert client.downloaded == [pdf_url]
    assert db_session.query(FileProcessing).count() == 0


def test_qingyang_detail_data_powerurl_ingests_labor_archive_without_processing(db_session):
    detail_url = "https://zhujianju.zgqingyang.gov.cn/zwgk/bmwj/zcwj/content_344931"
    pdf_url = (
        "https://zhujianju.zgqingyang.gov.cn/Upload/zjj2/pdf/2026/03/11/"
        "%E5%85%B3%E4%BA%8E%E5%85%AC%E5%B8%83%E5%BA%86%E9%98%B3%E5%B8%822026%E5%B9%B4%E7%AC%AC%E4%B8%80%E6%9C%9F%E5%BB%BA%E8%AE%BE%E5%B7%A5%E7%A8%8B%E6%9D%90%E6%96%99%E4%BF%A1%E6%81%AF%E4%BB%B7%E5%92%8C%E6%9C%BA%E6%A2%B0%E7%A7%9F%E8%B5%81%E4%BF%A1%E6%81%AF%E4%BB%B7%E7%9A%84%E9%80%9A%E7%9F%A5.pdf"
    )
    client = FakeQingyangCostInfoClient(
        list_html=_qingyang_list_html(),
        detail_html_by_url={detail_url: _qingyang_data_powerurl_detail_html()},
        bytes_by_url={pdf_url: b"%PDF qingyang 2026 issue 1 material"},
    )
    config = qingyang_cost_info_source_config()
    parser = config["parser"]["parsers"][QINGYANG_COST_INFO_PARSER_VERSION]
    source = _create_qingyang_source(db_session, config=config)
    row = list_qingyang_cost_info_issues(client, parser=parser, max_items=5)[2]

    archive = ingest_qingyang_cost_info_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=row,
        actor_id="qingyang-test",
    )

    stored = db_session.get(Archive, archive.archive_id)

    assert stored.source_item_key == "344931"
    assert cell_value(stored.metadata_payload["period"]) == "2026年第1期"
    assert cell_value(stored.metadata_payload["price_subtype"]) == "material_and_machine"
    assert cell_value(stored.metadata_payload["attachment_discovery_methods"]) == ["detail_page_pdf_data_powerurl"]
    assert client.downloaded == [pdf_url]
    assert db_session.query(FileProcessing).count() == 0


def _qingyang_list_html():
    return """
    <ul class="newsList">
      <li>
        <a href="/zwgk/bmwj/zcwj/content_59550" title="标题：关于公布庆阳市2026年第二期建设工程材料信息价和机械租赁信息价的通知
点击数：1378
发表时间：2026-04-30">关于公布庆阳市2026年第二期建设工程材料信息价和机械租赁信息价的通知</a>
        <span class="dateRight"><label>发布时间：</label>2026-04-30 16:12:11</span>
      </li>
      <li>
        <a href="/zwgk/bmwj/zcwj/content_59549" title="标题：关于公布庆阳市2026年第一期建设工程人工市场信息价的通知
点击数：715
发表时间：2026-04-30">关于公布庆阳市2026年第一期建设工程人工市场信息价的通知</a>
        <span class="dateRight"><label>发布时间：</label>2026-04-30 16:04:16</span>
      </li>
      <li>
        <a href="/zwgk/bmwj/zcwj/content_344931" title="标题：关于公布庆阳市2026年第一期建设工程材料信息价和机械租赁信息价的通知
点击数：1284
发表时间：2026-03-11">关于公布庆阳市2026年第一期建设工程材料信息价和机械租赁信息价的通知</a>
        <span class="dateRight"><label>发布时间：</label>2026-03-11 09:56:08</span>
      </li>
      <li>
        <a href="/zwgk/bmwj/zcwj/content_337981" title="标题：关于印发《庆阳市住宅小区公共收益管理使用指导意见》的通知">住宅收益通知</a>
        <span class="dateRight"><label>发布时间：</label>2025-10-29 17:03:56</span>
      </li>
    </ul>
    """


def _qingyang_anchor_detail_html(pdf_url):
    return f"""
    <html><head>
      <meta name="ArticleTitle" content="关于公布庆阳市2026年第二期建设工程材料信息价和机械租赁信息价的通知" />
      <meta name="PubDate" content="2026-04-30 16:12" />
      <meta name="ContentSource" content="庆阳市住房和城乡建设局" />
    </head><body>
      <div class="downfile">
        <a class="photoPlay" href="{pdf_url}">关于公布庆阳市2026年第二期建设工程材料信息价和机械租赁信息价的通知.pdf</a>
      </div>
    </body></html>
    """


def _qingyang_data_powerurl_detail_html():
    return """
    <html><head>
      <meta name="ArticleTitle" content="关于公布庆阳市2026年第一期建设工程材料信息价和机械租赁信息价的通知" />
      <meta name="PubDate" content="2026-03-11 09:56" />
      <meta name="ContentSource" content="庆阳市住房和城乡建设局" />
    </head><body>
      <div class="conTxt">
        <p><img class="pdfiswidthequals" data-uitype="pdf"
          data-powerurl="/Upload/zjj2/pdf/2026/03/11/关于公布庆阳市2026年第一期建设工程材料信息价和机械租赁信息价的通知.pdf"
          src="/Upload/zjj2/pdf/2026/03/11/关于公布庆阳市2026年第一期建设工程材料信息价和机械租赁信息价的通知_new.png"></p>
      </div>
    </body></html>
    """


def _create_qingyang_source(db_session, *, config):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="庆阳市住房和城乡建设局-信息价",
        data_domain="cost_info",
        province="甘肃省",
        city="庆阳市",
        region_code="621000",
        config=config,
    )
