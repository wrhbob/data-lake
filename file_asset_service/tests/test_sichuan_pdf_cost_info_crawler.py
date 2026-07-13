from app.collection import create_data_source
from app.info_price_coverage import build_coverage_matrix
from app.models import Archive, ArchiveFile, FileAsset, FileProcessing
from urllib.error import HTTPError
from app.sichuan_pdf_cost_info import (
    DAZHOU_PARSER_VERSION,
    DEYANG_PARSER_VERSION,
    LUZHOU_PARSER_VERSION,
    SICHUAN_PDF_CITY_SOURCE_CONFIGS,
    dazhou_cost_info_source_config,
    deyang_cost_info_source_config,
    discover_cost_info_attachments,
    ingest_sichuan_pdf_issue,
    leshan_cost_info_source_config,
    list_sichuan_pdf_issues,
    luzhou_cost_info_source_config,
    panzhihua_cost_info_source_config,
    run_sichuan_pdf_incremental,
    suining_cost_info_source_config,
)
from app.storage import FakeObjectStore


def cell_value(cell):
    return cell["value"]


class FakeSichuanPdfClient:
    def __init__(self, *, texts=None, ajax_payloads=None, bytes_by_url=None):
        self.texts = texts or {}
        self.ajax_payloads = ajax_payloads or {}
        self.bytes_by_url = bytes_by_url or {}
        self.downloaded = []
        self.posted = []

    def get_text(self, url):
        if url in self.texts:
            return self.texts[url]
        raise AssertionError(f"unexpected text fetch: {url}")

    def post_form_json(self, url, data):
        self.posted.append((url, data))
        if url in self.ajax_payloads:
            return self.ajax_payloads[url]
        raise AssertionError(f"unexpected ajax fetch: {url}")

    def get_bytes(self, url):
        self.downloaded.append(url)
        if url in self.bytes_by_url:
            return self.bytes_by_url[url], "application/pdf"
        if url.endswith(".pdf"):
            return b"%PDF real-ish original bytes, not parsed", "application/pdf"
        raise AssertionError(f"unexpected download: {url}")


def test_sichuan_pdf_source_configs_are_city_level_file_sources():
    expected = {
        "panzhihua": ("510400", "攀枝花市住房和城乡建设局"),
        "luzhou": ("510500", "泸州市住房和城乡建设局"),
        "deyang": ("510600", "德阳市人民政府"),
        "suining": ("510900", "遂宁市住房和城乡建设局"),
        "leshan": ("511100", "乐山市住房和城乡建设局"),
        "dazhou": ("511700", "达州市住房和城乡建设局"),
    }

    configs = {
        "panzhihua": panzhihua_cost_info_source_config(),
        "luzhou": luzhou_cost_info_source_config(),
        "deyang": deyang_cost_info_source_config(),
        "suining": suining_cost_info_source_config(),
        "leshan": leshan_cost_info_source_config(),
        "dazhou": dazhou_cost_info_source_config(),
    }

    assert sorted(SICHUAN_PDF_CITY_SOURCE_CONFIGS) == sorted(expected)
    for city_key, config in configs.items():
        region_code, publisher = expected[city_key]
        expected_attachment_mode = "mixed_file_attachment" if city_key == "luzhou" else "pdf_only"
        expected_parsability = "mixed" if city_key == "luzhou" else "text_pdf"
        expected_extensions = ["pdf", "xls", "xlsx"] if city_key == "luzhou" else ["pdf"]
        parser_version = config["parser"]["active_parser_version"]
        parser = config["parser"]["parsers"][parser_version]
        assert config["stable"]["site_id"] == f"cost_info.sc.{city_key}"
        assert config["stable"]["region_code"] == region_code
        assert config["stable"]["coverage_region_code"] == region_code
        assert config["stable"]["publisher_scope"] == "city"
        assert config["stable"]["publisher"] == publisher
        assert config["price_coordinates"]["price_source_type"] == "info_price"
        assert config["source_shape"]["source_attachment_mode"] == expected_attachment_mode
        assert config["source_shape"]["parsability"] == expected_parsability
        assert parser["attachments"]["allowed"] == expected_extensions
        assert "source_priority" not in config["stable"]

    assert panzhihua_cost_info_source_config()["price_coordinates"]["tax_type"] == "tax_exclusive"
    assert luzhou_cost_info_source_config()["price_coordinates"]["tax_type"] is None


def test_discover_cost_info_attachments_scans_a_iframe_embed_object_and_js_urls():
    html = """
    <a href="/files/a.pdf">附件A</a>
    <iframe src="/files/b.pdf"></iframe>
    <embed src="./c.pdf" />
    <object data="../d.pdf"></object>
    <script>javascript:LookFile('/files/e.pdf'); fetch("/api/file/f.pdf?token=1")</script>
    """

    attachments = discover_cost_info_attachments(
        html,
        detail_url="https://zjj.example.gov.cn/news/show/202605.html",
        allowed_extensions=["pdf"],
    )

    assert [item["url"] for item in attachments] == [
        "https://zjj.example.gov.cn/files/a.pdf",
        "https://zjj.example.gov.cn/files/b.pdf",
        "https://zjj.example.gov.cn/news/show/c.pdf",
        "https://zjj.example.gov.cn/news/d.pdf",
        "https://zjj.example.gov.cn/files/e.pdf",
        "https://zjj.example.gov.cn/api/file/f.pdf?token=1",
    ]
    assert {item["discovery_method"] for item in attachments} == {"a", "iframe", "embed", "object", "js_url"}


def test_luzhou_static_list_detail_pdf_ingests_without_processing(db_session):
    list_url = "https://zjj.luzhou.gov.cn/hyfw/gczjxx/"
    detail_url = "https://zjj.luzhou.gov.cn/hyfw/gczjxx/content_202605.html"
    pdf_url = "https://zjj.luzhou.gov.cn/upload/202605/luzhou-2026-05.pdf"
    client = FakeSichuanPdfClient(
        texts={
            list_url: f"""
            <li>
              <a href="/hyfw/gczjxx/content_202605.html" title="泸州市2026年5月建设工程材料价格信息">泸州市2026年5月建设工程材料价格信息</a>
              <span>2026-06-12</span>
            </li>
            """,
            detail_url: f'<p>附件：<a href="{pdf_url}">泸州市2026年5月建设工程材料价格信息.pdf</a></p>',
        },
    )
    config = luzhou_cost_info_source_config()
    parser = config["parser"]["parsers"][LUZHOU_PARSER_VERSION]
    source = create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="泸州市住房和城乡建设局-工程造价信息",
        data_domain="cost_info",
        region_code="510500",
        config=config,
    )

    rows = list_sichuan_pdf_issues(client, parser=parser)
    archive = ingest_sichuan_pdf_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=rows[0],
        parser=parser,
        actor_id="luzhou-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    files = (
        db_session.query(ArchiveFile, FileAsset)
        .join(FileAsset, ArchiveFile.file_id == FileAsset.file_id)
        .filter(ArchiveFile.archive_id == archive.archive_id)
        .all()
    )

    assert rows[0]["detail_url"] == detail_url
    assert stored.business_key == f"cost_info:{source.source_id}:510500:2026-05:泸州市2026年5月建设工程材料价格信息"
    assert cell_value(stored.metadata_payload["period"]) == "2026-05"
    assert cell_value(stored.metadata_payload["coverage_region_code"]) == "510500"
    assert cell_value(stored.metadata_payload["price_source_type"]) == "info_price"
    assert stored.metadata_payload["tax_type"]["value"] is None
    assert cell_value(stored.metadata_payload["source_attachment_mode"]) == "pdf_only"
    assert cell_value(stored.metadata_payload["parsability"]) == "text_pdf"
    assert [asset.file_ext for _, asset in files] == [".pdf"]
    assert client.downloaded == [pdf_url]
    assert db_session.query(FileProcessing).count() == 0


def test_luzhou_static_detail_xls_ingests_without_processing(db_session):
    list_url = "https://zjj.luzhou.gov.cn/hyfw/gczjxx/"
    detail_url = "https://zjj.luzhou.gov.cn/hyfw/gczjxx/content_1133971"
    xls_url = "https://zjj.luzhou.gov.cn/upload/zjj/contentmanage/article/file/2026/02/12/住建网泸州市市区材料价格信息2026年1月.xls?t=1770859774595"
    client = FakeSichuanPdfClient(
        texts={
            list_url: f"""
            <li>
              <a href="/hyfw/gczjxx/content_1133971" title="2026年1月泸州市市区材料价格信息">2026年1月泸州市市区材料价格信息</a>
              <span>2026-02-12</span>
            </li>
            """,
            detail_url: f"""
            <div id="fontzoom">
              <p>
                <img class="attachment-icon" src="/content/_common/assets/ueditor/dialogs/attachment/filetypeimages/icon_xls.gif">
                <a href="/upload/zjj/contentmanage/article/file/2026/02/12/住建网泸州市市区材料价格信息2026年1月.xls?t=1770859774595"
                   title="住建网泸州市市区材料价格信息2026年1月.xls">2026年1月泸州市市区材料价格信息.xls</a>
              </p>
            </div>
            """,
        },
        bytes_by_url={xls_url: b"\xd0\xcf\x11\xe0luzhou xls original bytes"},
    )
    config = luzhou_cost_info_source_config()
    parser = config["parser"]["parsers"][LUZHOU_PARSER_VERSION]
    source = create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="泸州市住房和城乡建设局-工程造价信息",
        data_domain="cost_info",
        region_code="510500",
        config=config,
    )

    rows = list_sichuan_pdf_issues(client, parser=parser)
    archive = ingest_sichuan_pdf_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=rows[0],
        parser=parser,
        actor_id="luzhou-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    files = (
        db_session.query(ArchiveFile, FileAsset)
        .join(FileAsset, ArchiveFile.file_id == FileAsset.file_id)
        .filter(ArchiveFile.archive_id == archive.archive_id)
        .all()
    )

    assert stored.business_key == f"cost_info:{source.source_id}:510500:2026-01:2026年1月泸州市市区材料价格信息"
    assert cell_value(stored.metadata_payload["period"]) == "2026-01"
    assert cell_value(stored.metadata_payload["coverage_region_code"]) == "510500"
    assert cell_value(stored.metadata_payload["source_attachment_mode"]) == "xls_attachment"
    assert cell_value(stored.metadata_payload["parsability"]) == "structured"
    assert cell_value(stored.metadata_payload["attachment_discovery_methods"]) == ["a"]
    assert [asset.file_ext for _, asset in files] == [".xls"]
    assert client.downloaded == [xls_url]
    assert db_session.query(FileProcessing).count() == 0


def test_deyang_static_column_paginates_and_filters_material_price_rows():
    config = deyang_cost_info_source_config()
    parser = config["parser"]["parsers"][DEYANG_PARSER_VERSION]
    parser = {
        **parser,
        "pagination": {
            **parser["pagination"],
            "max_pages": 2,
        },
    }
    list_url = parser["list_url"]
    page2_url = parser["pagination"]["url_template"].format(page=2)
    client = FakeSichuanPdfClient(
        texts={
            list_url: """
            <li>
              <a href="/gk/fjbm/fzjj/fadingnarong/gqgg/1956445.htm">关于发布德阳市2026年04月建筑材料市场价格信息的通知</a>
              <span>2026-06-02</span>
            </li>
            <li>
              <a href="/gk/fjbm/fzjj/fadingnarong/gqgg/1956000.htm">关于公开征求城市更新管理办法意见的公告</a>
              <span>2026-05-30</span>
            </li>
            """,
            page2_url: """
            <li>
              <a href="/gk/fjbm/fzjj/fadingnarong/gqgg/1947973.htm">关于发布德阳市2026年02月建筑材料市场价格信息的通知</a>
              <span>2026-03-30</span>
            </li>
            """,
        }
    )

    rows = list_sichuan_pdf_issues(client, parser=parser)

    assert rows == [
        {
            "title": "关于发布德阳市2026年04月建筑材料市场价格信息的通知",
            "publish_date": "2026-06-02",
            "detail_url": "https://www.deyang.gov.cn/gk/fjbm/fzjj/fadingnarong/gqgg/1956445.htm",
            "source_item_id": "https://www.deyang.gov.cn/gk/fjbm/fzjj/fadingnarong/gqgg/1956445.htm",
        },
        {
            "title": "关于发布德阳市2026年02月建筑材料市场价格信息的通知",
            "publish_date": "2026-03-30",
            "detail_url": "https://www.deyang.gov.cn/gk/fjbm/fzjj/fadingnarong/gqgg/1947973.htm",
            "source_item_id": "https://www.deyang.gov.cn/gk/fjbm/fzjj/fadingnarong/gqgg/1947973.htm",
        },
    ]
    assert parser["list_url"] == "https://www.deyang.gov.cn/info/iList.jsp?node_id=GKdyszf&cat_id=25723"


def test_deyang_static_column_keeps_first_page_when_later_page_is_rate_limited():
    config = deyang_cost_info_source_config()
    parser = {
        **config["parser"]["parsers"][DEYANG_PARSER_VERSION],
        "pagination": {
            **config["parser"]["parsers"][DEYANG_PARSER_VERSION]["pagination"],
            "max_pages": 2,
            "stop_on_error_after_rows": True,
        },
    }
    list_url = parser["list_url"]
    page2_url = parser["pagination"]["url_template"].format(page=2)

    class RateLimitedPageClient(FakeSichuanPdfClient):
        def get_text(self, url):
            if url == page2_url:
                raise HTTPError(url, 503, "Service Unavailable", hdrs=None, fp=None)
            return super().get_text(url)

    client = RateLimitedPageClient(
        texts={
            list_url: """
            <li>
              <a href="/gk/fjbm/fzjj/fadingnarong/gqgg/1956445.htm">关于发布德阳市2026年04月建筑材料市场价格信息的通知</a>
              <span>2026-06-02</span>
            </li>
            """
        }
    )

    rows = list_sichuan_pdf_issues(client, parser=parser)

    assert rows == [
        {
            "title": "关于发布德阳市2026年04月建筑材料市场价格信息的通知",
            "publish_date": "2026-06-02",
            "detail_url": "https://www.deyang.gov.cn/gk/fjbm/fzjj/fadingnarong/gqgg/1956445.htm",
            "source_item_id": "https://www.deyang.gov.cn/gk/fjbm/fzjj/fadingnarong/gqgg/1956445.htm",
        }
    ]


def test_deyang_static_detail_pdf_ingests_without_processing(db_session):
    list_url = "https://www.deyang.gov.cn/info/iList.jsp?node_id=GKdyszf&cat_id=25723"
    detail_url = "https://www.deyang.gov.cn/gk/fjbm/fzjj/fadingnarong/gqgg/1956445.htm"
    pdf_url = "https://www.deyang.gov.cn/wcm.files/upload/GKdyszf/eupload/ueditor/file/20260602/1780384437452077195.pdf"
    client = FakeSichuanPdfClient(
        texts={
            list_url: """
            <li>
              <a href="/gk/fjbm/fzjj/fadingnarong/gqgg/1956445.htm">关于发布德阳市2026年04月建筑材料市场价格信息的通知</a>
              <span>2026-06-02</span>
            </li>
            """,
            detail_url: """
            <p>附件：
              <a href="/wcm.files/upload/GKdyszf/eupload/ueditor/file/20260602/1780384437452077195.pdf">
                德阳市2026年04月建筑材料市场价格信息.pdf
              </a>
            </p>
            """,
        },
    )
    config = deyang_cost_info_source_config()
    parser = {
        **config["parser"]["parsers"][DEYANG_PARSER_VERSION],
        "pagination": {
            **config["parser"]["parsers"][DEYANG_PARSER_VERSION]["pagination"],
            "max_pages": 1,
        },
    }
    source = create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="德阳市人民政府-公示公告",
        data_domain="cost_info",
        region_code="510600",
        config=config,
    )

    rows = list_sichuan_pdf_issues(client, parser=parser)
    archive = ingest_sichuan_pdf_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=rows[0],
        parser=parser,
        actor_id="deyang-test",
    )

    stored = db_session.get(Archive, archive.archive_id)

    assert stored.business_key == f"cost_info:{source.source_id}:510600:2026-04:关于发布德阳市2026年04月建筑材料市场价格信息的通知"
    assert cell_value(stored.metadata_payload["period"]) == "2026-04"
    assert cell_value(stored.metadata_payload["coverage_region_code"]) == "510600"
    assert cell_value(stored.metadata_payload["attachment_discovery_methods"]) == ["a"]
    assert client.downloaded == [pdf_url]
    assert db_session.query(FileProcessing).count() == 0


def test_dazhou_ajax_list_iframe_pdf_ingests_without_processing(db_session):
    ajax_url = "http://zjj.dazhou.gov.cn/api-ajax_list-1.html"
    detail_url = "http://zjj.dazhou.gov.cn/news-show-zaojiaxinxi-202605.html"
    pdf_url = "http://zjj.dazhou.gov.cn/uploadfile/2026/06/dazhou-2026-05.pdf"
    client = FakeSichuanPdfClient(
        ajax_payloads={
            ajax_url: {
                "total": 1,
                "data": [
                    {
                        "title": "达州市2026年第5期建设工程造价信息",
                        "inputtime": "2026-06-18",
                        "url": detail_url,
                        "id": "dz-2026-05",
                    }
                ],
            }
        },
        texts={
            detail_url: f'<iframe src="/uploadfile/2026/06/dazhou-2026-05.pdf" width="100%" height="900"></iframe>'
        },
    )
    config = dazhou_cost_info_source_config()
    parser = config["parser"]["parsers"][DAZHOU_PARSER_VERSION]
    source = create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="达州市住房和城乡建设局-造价信息",
        data_domain="cost_info",
        region_code="511700",
        config=config,
    )

    rows = list_sichuan_pdf_issues(client, parser=parser)
    archive = ingest_sichuan_pdf_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=rows[0],
        parser=parser,
        actor_id="dazhou-test",
    )

    stored = db_session.get(Archive, archive.archive_id)

    assert rows[0]["source_item_id"] == "dz-2026-05"
    assert client.posted == [(ajax_url, parser["ajax"]["form_data"])]
    assert stored.business_key == f"cost_info:{source.source_id}:511700:2026-05:达州市2026年第5期建设工程造价信息"
    assert cell_value(stored.metadata_payload["period"]) == "2026-05"
    assert cell_value(stored.metadata_payload["coverage_region_code"]) == "511700"
    assert stored.metadata_payload["tax_type"]["value"] is None
    assert cell_value(stored.metadata_payload["attachment_discovery_methods"]) == ["iframe"]
    assert client.downloaded == [pdf_url]
    assert db_session.query(FileProcessing).count() == 0


def test_run_sichuan_pdf_incremental_stops_on_existing_before_download(db_session):
    list_url = "https://zjj.luzhou.gov.cn/hyfw/gczjxx/"
    detail_url = "https://zjj.luzhou.gov.cn/hyfw/gczjxx/content_202605.html"
    client = FakeSichuanPdfClient(
        texts={
            list_url: f"""
            <li>
              <a href="/hyfw/gczjxx/content_202605.html" title="泸州市2026年5月建设工程材料价格信息">泸州市2026年5月建设工程材料价格信息</a>
              <span>2026-06-12</span>
            </li>
            """,
            detail_url: '<a href="/upload/202605/luzhou-2026-05.pdf">PDF</a>',
        },
    )
    config = luzhou_cost_info_source_config()
    source = create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="泸州市住房和城乡建设局-工程造价信息",
        data_domain="cost_info",
        region_code="510500",
        config=config,
    )

    run_sichuan_pdf_incremental(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        max_items=1,
        as_of_date="2026-06-22",
    )
    second_report = run_sichuan_pdf_incremental(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        max_items=1,
        as_of_date="2026-06-22",
    )

    assert second_report["listed_count"] == 1
    assert second_report["ingested_count"] == 0
    assert second_report["skipped_existing_count"] == 1
    assert second_report["stopped_on_existing"] is True
    assert db_session.query(Archive).filter_by(domain_type="cost_info").count() == 1


def test_sichuan_first_batch_coverage_matrix_expands_file_present_city_count_to_nine(db_session):
    configs = [
        ("成都信息价人工上传", "510100", {"stable": {"publisher_scope": "city", "coverage_region_code": "510100"}}),
        ("绵阳市住房和城乡建设委员会", "510700", {"stable": {"publisher_scope": "city", "coverage_region_code": "510700"}}),
        ("自贡市人民政府-材料价格", "510300", {"stable": {"publisher_scope": "city", "coverage_region_code": "510300"}}),
        ("攀枝花市住房和城乡建设局", "510400", panzhihua_cost_info_source_config()),
        ("泸州市住房和城乡建设局", "510500", luzhou_cost_info_source_config()),
        ("德阳市住房和城乡建设局", "510600", deyang_cost_info_source_config()),
        ("遂宁市住房和城乡建设局", "510900", suining_cost_info_source_config()),
        ("乐山市住房和城乡建设局", "511100", leshan_cost_info_source_config()),
        ("达州市住房和城乡建设局", "511700", dazhou_cost_info_source_config()),
    ]
    for name, region_code, config in configs:
        source = create_data_source(
            db_session,
            source_scope="platform_public",
            tenant_code=None,
            managed_by="platform",
            source_type="info_price",
            connector_type="source_registry",
            name=name,
            data_domain="cost_info",
            region_code=region_code,
            province="四川",
            city=name[:3],
            config={
                **config,
                "stable": {
                    "site_id": config.get("stable", {}).get("site_id", f"cost_info.sc.{region_code}"),
                    "domain_type": "cost_info",
                    "region_code": region_code,
                    "coverage_region_code": region_code,
                    "publisher_scope": "city",
                    "publisher_region_code": region_code,
                    "publisher_name": name,
                    **config.get("stable", {}),
                },
            },
        )
        from tests.test_info_price_coverage import add_evidence

        add_evidence(db_session, source, period="2026-05", coverage_region_code=region_code)

    rows = build_coverage_matrix(db_session, start_period="2026-05", end_period="2026-05", province_code="510000")
    present_rows = [row for row in rows if row.source_completeness_status == "city_source_present"]

    assert sorted(row.coverage_region_code for row in present_rows) == [
        "510100",
        "510300",
        "510400",
        "510500",
        "510600",
        "510700",
        "510900",
        "511100",
        "511700",
    ]
    assert len(present_rows) == 9
