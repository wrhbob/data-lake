from datetime import UTC, datetime

from app.adapters import get_adapter
from app.archive_rules import build_cost_info_business_key
from app.collection import create_collection_task, create_data_source
from app.models import Archive, ArchiveFile, FileAsset, FileProcessing
from app.storage import FakeObjectStore


class FakeStaticListClient:
    def __init__(
        self,
        *,
        list_url="https://zjj.sm.gov.cn/ztzl/qszycljg/",
        list_html="",
        detail_html_by_url=None,
        bytes_by_url=None,
    ):
        self.list_url = list_url
        self.list_html = list_html
        self.detail_html_by_url = detail_html_by_url or {}
        self.bytes_by_url = bytes_by_url or {}
        self.get_text_calls = []
        self.downloaded = []

    def get_text(self, url):
        self.get_text_calls.append(url)
        if url == self.list_url:
            return self.list_html
        if url in self.detail_html_by_url:
            return self.detail_html_by_url[url]
        raise AssertionError(f"unexpected GET: {url}")

    def get_bytes(self, url):
        self.downloaded.append(url)
        if url in self.bytes_by_url:
            content_type = "application/pdf" if url.lower().endswith(".pdf") else "application/x-rar-compressed"
            return self.bytes_by_url[url], content_type
        raise AssertionError(f"unexpected download: {url}")


def cell_value(cell):
    return cell["value"]


def test_static_list_detail_package_adapter_is_registered():
    assert get_adapter("static_list_detail_package").adapter_kind == "static_list_detail_package"


def test_static_list_detail_package_discovers_sanming_rar_issues(db_session):
    source = _create_sanming_source(db_session)
    task = _create_task(db_session, source, max_items_per_run=2)
    client = FakeStaticListClient(list_html=_sanming_list_html())

    issues = get_adapter("static_list_detail_package").discover(source, task, client)

    assert [issue.source_item_key for issue in issues] == ["2207254", "2206075"]
    assert [issue.period_raw for issue in issues] == [
        "三明市建设工程材料综合价格发布(2026年4月) (市区、各县)",
        "三明市建设工程材料综合价格发布(2026年3月) (市区、各县)",
    ]
    assert issues[0].publish_date == "2026-05-14"
    assert issues[0].detail_url == "https://zjj.sm.gov.cn/ztzl/qszycljg/202605/t20260515_2207254.htm"


def test_static_list_detail_package_ingests_sanming_rar_without_processing(db_session):
    detail_url = "https://zjj.sm.gov.cn/ztzl/qszycljg/202605/t20260515_2207254.htm"
    rar_url = "https://zjj.sm.gov.cn/ztzl/qszycljg/202605/P020260515617912622667.rar"
    source = _create_sanming_source(db_session)
    task = _create_task(db_session, source, max_items_per_run=1)
    client = FakeStaticListClient(
        list_html=_sanming_list_html(),
        detail_html_by_url={detail_url: _sanming_detail_html()},
        bytes_by_url={rar_url: b"Rar!\x1a\x07\x00 sanming 2026-04 package"},
    )
    adapter = get_adapter("static_list_detail_package")
    issue = adapter.discover(source, task, client)[0]

    adapter.ingest(source, task, issue, FakeObjectStore())

    stored = db_session.query(Archive).filter_by(source_id=source.source_id).one()
    files = (
        db_session.query(ArchiveFile, FileAsset)
        .join(FileAsset, ArchiveFile.file_id == FileAsset.file_id)
        .filter(ArchiveFile.archive_id == stored.archive_id)
        .all()
    )

    assert stored.business_key == build_cost_info_business_key(
        source_id=source.source_id,
        region_code="350400",
        period="2026-04",
        title="三明市建设工程材料综合价格发布(2026年4月) (市区、各县)",
    )
    assert stored.region_code == "350400"
    assert stored.period_kind == "monthly"
    assert cell_value(stored.metadata_payload["period"]) == "2026-04"
    assert cell_value(stored.metadata_payload["period_start"]) == "2026-04"
    assert cell_value(stored.metadata_payload["coverage_region_code"]) == "350400"
    assert cell_value(stored.metadata_payload["source_attachment_mode"]) == "rar_package"
    assert cell_value(stored.metadata_payload["publication_mode"]) == "DETAIL_ATTACHMENT"
    assert cell_value(stored.metadata_payload["parsability"]) == "opaque_package"
    assert cell_value(stored.metadata_payload["opaque_package"]) is True
    assert cell_value(stored.metadata_payload["attachment_discovery_methods"]) == ["detail_page_package_anchor"]
    assert [(mounted.file_role, asset.file_ext) for mounted, asset in files] == [("zip_package", ".rar")]
    assert client.get_text_calls == ["https://zjj.sm.gov.cn/ztzl/qszycljg/", detail_url]
    assert client.downloaded == [rar_url]
    assert db_session.query(FileProcessing).count() == 0


def test_static_list_detail_package_discovers_zhangzhou_pdf_issue_titles(db_session):
    source = _create_zhangzhou_source(db_session)
    task = _create_task(db_session, source, max_items_per_run=2)
    client = FakeStaticListClient(list_url=_zhangzhou_list_url(), list_html=_zhangzhou_list_html())

    issues = get_adapter("static_list_detail_package").discover(source, task, client)

    assert [issue.source_item_key for issue in issues] == ["1788470572", "1222672042"]
    assert [issue.period_raw for issue in issues] == [
        "漳州建设工程材料信息（2026）第五期",
        "漳州建设工程材料信息（2026）第四期",
    ]
    assert issues[0].publish_date == "2026-06-10"
    assert issues[0].detail_url == (
        "http://jsj.zhangzhou.gov.cn/cms/html/zzszfhcxjsj/2026-06-10/1788470572.html"
    )
    assert get_adapter("static_list_detail_package").business_key(source, issues[0]) == build_cost_info_business_key(
        source_id=source.source_id,
        region_code="350600",
        period="2026-05",
        title="漳州建设工程材料信息（2026）第五期",
    )


def test_static_list_detail_package_ingests_zhangzhou_pdf_without_processing(db_session):
    detail_url = "http://jsj.zhangzhou.gov.cn/cms/html/zzszfhcxjsj/2026-06-10/1788470572.html"
    pdf_url = (
        "http://jsj.zhangzhou.gov.cn/cms/pages/20686985192640004/attachments/"
        "%E6%BC%B3%E5%B7%9E%E5%BB%BA%E8%AE%BE%E5%B7%A5%E7%A8%8B%E6%9D%90%E6%96%99"
        "%E4%BF%A1%E6%81%AF%EF%BC%882026%EF%BC%89%E7%AC%AC%E4%BA%94%E6%9C%9F.pdf"
    )
    source = _create_zhangzhou_source(db_session)
    task = _create_task(db_session, source, max_items_per_run=1)
    client = FakeStaticListClient(
        list_url=_zhangzhou_list_url(),
        list_html=_zhangzhou_list_html(),
        detail_html_by_url={detail_url: _zhangzhou_detail_html()},
        bytes_by_url={pdf_url: b"%PDF zhangzhou 2026 issue 5"},
    )
    adapter = get_adapter("static_list_detail_package")
    issue = adapter.discover(source, task, client)[0]

    adapter.ingest(source, task, issue, FakeObjectStore())

    stored = db_session.query(Archive).filter_by(source_id=source.source_id).one()
    files = (
        db_session.query(ArchiveFile, FileAsset)
        .join(FileAsset, ArchiveFile.file_id == FileAsset.file_id)
        .filter(ArchiveFile.archive_id == stored.archive_id)
        .all()
    )

    assert stored.business_key == build_cost_info_business_key(
        source_id=source.source_id,
        region_code="350600",
        period="2026-05",
        title="漳州建设工程材料信息（2026）第五期",
    )
    assert stored.publish_date.isoformat() == "2026-06-10"
    assert stored.region_code == "350600"
    assert stored.period_kind == "monthly"
    assert cell_value(stored.metadata_payload["period"]) == "2026-05"
    assert cell_value(stored.metadata_payload["period_start"]) == "2026-05"
    assert cell_value(stored.metadata_payload["coverage_region_code"]) == "350600"
    assert cell_value(stored.metadata_payload["source_attachment_mode"]) == "pdf_attachment"
    assert cell_value(stored.metadata_payload["publication_mode"]) == "DETAIL_ATTACHMENT"
    assert cell_value(stored.metadata_payload["parsability"]) == "image_pdf"
    assert cell_value(stored.metadata_payload["attachment_discovery_methods"]) == ["detail_page_pdf_anchor"]
    assert [(mounted.file_role, asset.file_ext) for mounted, asset in files] == [("main_document", ".pdf")]
    assert client.get_text_calls == [_zhangzhou_list_url(), detail_url]
    assert client.downloaded == [pdf_url]
    assert db_session.query(FileProcessing).count() == 0


def test_static_list_detail_package_discovers_ningde_direct_pdf_issues(db_session):
    source = _create_ningde_source(db_session)
    task = _create_task(db_session, source, max_items_per_run=2)
    client = FakeStaticListClient(list_url=_ningde_list_url(), list_html=_ningde_list_html())

    issues = get_adapter("static_list_detail_package").discover(source, task, client)

    assert [issue.source_item_key for issue in issues] == ["P020260629320555499814", "P020260528323208740463"]
    assert [issue.period_raw for issue in issues] == [
        "《宁德工程造价信息》2026年第6期",
        "《宁德工程造价信息》2026年第5期",
    ]
    assert issues[0].publish_date == "2026-06-29"
    assert issues[0].detail_url == "https://zjj.ningde.gov.cn/zwgk/ndsgczjxx/202606/P020260629320555499814.pdf"
    assert issues[0].attachment_urls == [
        "https://zjj.ningde.gov.cn/zwgk/ndsgczjxx/202606/P020260629320555499814.pdf"
    ]
    assert get_adapter("static_list_detail_package").business_key(source, issues[0]) == build_cost_info_business_key(
        source_id=source.source_id,
        region_code="350900",
        period="2026-06",
        title="《宁德工程造价信息》2026年第6期",
    )


def test_static_list_detail_package_ingests_ningde_direct_pdf_without_detail_page(db_session):
    pdf_url = "https://zjj.ningde.gov.cn/zwgk/ndsgczjxx/202606/P020260629320555499814.pdf"
    source = _create_ningde_source(db_session)
    task = _create_task(db_session, source, max_items_per_run=1)
    client = FakeStaticListClient(
        list_url=_ningde_list_url(),
        list_html=_ningde_list_html(),
        bytes_by_url={pdf_url: b"%PDF ningde 2026 issue 6"},
    )
    adapter = get_adapter("static_list_detail_package")
    issue = adapter.discover(source, task, client)[0]

    adapter.ingest(source, task, issue, FakeObjectStore())

    stored = db_session.query(Archive).filter_by(source_id=source.source_id).one()
    files = (
        db_session.query(ArchiveFile, FileAsset)
        .join(FileAsset, ArchiveFile.file_id == FileAsset.file_id)
        .filter(ArchiveFile.archive_id == stored.archive_id)
        .all()
    )

    assert stored.business_key == build_cost_info_business_key(
        source_id=source.source_id,
        region_code="350900",
        period="2026-06",
        title="《宁德工程造价信息》2026年第6期",
    )
    assert stored.publish_date.isoformat() == "2026-06-29"
    assert stored.region_code == "350900"
    assert stored.period_kind == "monthly"
    assert cell_value(stored.metadata_payload["period"]) == "2026-06"
    assert cell_value(stored.metadata_payload["period_start"]) == "2026-06"
    assert cell_value(stored.metadata_payload["coverage_region_code"]) == "350900"
    assert cell_value(stored.metadata_payload["source_attachment_mode"]) == "pdf_attachment"
    assert cell_value(stored.metadata_payload["publication_mode"]) == "DIRECT_ATTACHMENT"
    assert cell_value(stored.metadata_payload["parsability"]) == "image_pdf"
    assert cell_value(stored.metadata_payload["attachment_discovery_methods"]) == ["list_page_pdf_anchor"]
    assert [(mounted.file_role, asset.file_ext) for mounted, asset in files] == [("main_document", ".pdf")]
    assert client.get_text_calls == [_ningde_list_url()]
    assert client.downloaded == [pdf_url]
    assert db_session.query(FileProcessing).count() == 0


def _create_sanming_source(db_session):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="三明市住房和城乡建设局",
        data_domain="cost_info",
        base_url="https://zjj.sm.gov.cn/ztzl/qszycljg/",
        url="https://zjj.sm.gov.cn/ztzl/qszycljg/",
        province="福建省",
        city="三明市",
        region_code="350400",
        config=_sanming_config(),
        schedule_policy={"enabled": True, "max_items_per_run": 2},
        status="active",
    )


def _create_zhangzhou_source(db_session):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="漳州市住房和城乡建设局",
        data_domain="cost_info",
        base_url=_zhangzhou_list_url(),
        url=_zhangzhou_list_url(),
        province="福建省",
        city="漳州市",
        region_code="350600",
        config=_zhangzhou_config(),
        schedule_policy={"enabled": True, "max_items_per_run": 2},
        status="active",
    )


def _create_ningde_source(db_session):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="宁德市住房和城乡建设局",
        data_domain="cost_info",
        base_url=_ningde_list_url(),
        url=_ningde_list_url(),
        province="福建省",
        city="宁德市",
        region_code="350900",
        config=_ningde_config(),
        schedule_policy={"enabled": True, "max_items_per_run": 2},
        status="active",
    )


def _create_task(db_session, source, *, max_items_per_run):
    task = create_collection_task(
        db_session,
        source_id=source.source_id,
        operator_type="system",
        task_type="crawl_incremental",
        trigger_type="manual",
        data_domain="cost_info",
        config_override={"max_items_per_run": max_items_per_run},
    )
    task.scheduled_at = datetime(2026, 6, 29, 1, 0, tzinfo=UTC)
    db_session.commit()
    return task


def _sanming_config():
    return {
        "registry_schema_version": "source_registry.v1",
        "stable": {
            "site_id": "cost_info.fujian.350400.pending",
            "domain_type": "cost_info",
            "region_code": "350400",
            "coverage_region_code": "350400",
            "province": "福建省",
            "city": "三明市",
            "publisher_name": "三明市住房和城乡建设局",
            "publisher_scope": "city",
            "entry_url": "https://zjj.sm.gov.cn/ztzl/qszycljg/",
        },
        "price_coordinates": {"price_source_type": "info_price", "price_kind": "guidance", "tax_type": None},
        "source_shape": {
            "source_attachment_mode": "rar_package",
            "publication_mode": "DETAIL_ATTACHMENT",
            "parsability": "opaque_package",
            "period_kind": "monthly",
        },
        "parser": {
            "active_parser_version": "sanming.zjj-rar-list.v1",
            "parsers": {
                "sanming.zjj-rar-list.v1": {
                    "adapter_kind": "static_list_detail_package",
                    "list_url": "https://zjj.sm.gov.cn/ztzl/qszycljg/",
                    "list_item_selector": "a[title*='材料综合价格发布']",
                    "detail_attachment_selector": "a[href$='.rar']",
                    "period": {"source": "title", "kind": "monthly", "regex": r"(20\d{2})年(\d{1,2})月"},
                    "attachments": {"allowed": ["rar"], "opaque": True},
                }
            },
        },
    }


def _zhangzhou_config():
    return {
        "registry_schema_version": "source_registry.v1",
        "stable": {
            "site_id": "cost_info.fujian.350600.pending",
            "domain_type": "cost_info",
            "region_code": "350600",
            "coverage_region_code": "350600",
            "province": "福建省",
            "city": "漳州市",
            "publisher_name": "漳州市住房和城乡建设局",
            "publisher_scope": "city",
            "entry_url": _zhangzhou_list_url(),
        },
        "price_coordinates": {"price_source_type": "info_price", "price_kind": "guidance", "tax_type": None},
        "source_shape": {
            "source_attachment_mode": "pdf_attachment",
            "publication_mode": "DETAIL_ATTACHMENT",
            "parsability": "image_pdf",
            "period_kind": "monthly",
        },
        "parser": {
            "active_parser_version": "zhangzhou.jsj-pdf-list.v1",
            "parsers": {
                "zhangzhou.jsj-pdf-list.v1": {
                    "adapter_kind": "static_list_detail_package",
                    "list_url": _zhangzhou_list_url(),
                    "list_item_selector": "a[title*='建设工程材料信息']",
                    "detail_attachment_selector": "a[href$='.pdf']",
                    "period": {"source": "title", "kind": "monthly", "regex": r"（(?P<year>20\d{2})）第(?P<month>[一二三四五六七八九十]{1,3}|\d{1,2})期"},
                    "attachments": {"allowed": ["pdf"], "opaque": True},
                }
            },
        },
    }


def _ningde_config():
    return {
        "registry_schema_version": "source_registry.v1",
        "stable": {
            "site_id": "cost_info.fujian.350900.pending",
            "domain_type": "cost_info",
            "region_code": "350900",
            "coverage_region_code": "350900",
            "province": "福建省",
            "city": "宁德市",
            "publisher_name": "宁德市住房和城乡建设局",
            "publisher_scope": "city",
            "entry_url": _ningde_list_url(),
        },
        "price_coordinates": {"price_source_type": "info_price", "price_kind": "guidance", "tax_type": None},
        "source_shape": {
            "source_attachment_mode": "pdf_attachment",
            "publication_mode": "DIRECT_ATTACHMENT",
            "parsability": "image_pdf",
            "period_kind": "monthly",
        },
        "parser": {
            "active_parser_version": "ningde.zjj-direct-pdf-list.v1",
            "parsers": {
                "ningde.zjj-direct-pdf-list.v1": {
                    "adapter_kind": "static_list_detail_package",
                    "list_url": _ningde_list_url(),
                    "list_item_selector": "a[title*='宁德工程造价信息'][href$='.pdf']",
                    "publication_mode": "DIRECT_ATTACHMENT",
                    "period": {"source": "title", "kind": "monthly", "regex": r"(?P<year>20\d{2})年第(?P<month>\d{1,2})期"},
                    "attachments": {"allowed": ["pdf"], "opaque": True},
                }
            },
        },
    }


def _zhangzhou_list_url():
    return "http://jsj.zhangzhou.gov.cn/cms/html/zzszfhcxjsj/xydt/index.html"


def _ningde_list_url():
    return "https://zjj.ningde.gov.cn/zwgk/ndsgczjxx/"


def _sanming_list_html():
    return """
    <ul>
      <li>
        <a title="三明市建设工程材料综合价格发布(2026年4月) (市区、各县)" href="./202605/t20260515_2207254.htm">
          三明市建设工程材料综合价格发布(2026年4月) (市区、各县)
        </a>
        <span>2026-05-14</span>
      </li>
      <li>
        <a title="三明市建设工程材料综合价格发布(2026年3月) (市区、各县)" href="./202605/t20260509_2206075.htm">
          三明市建设工程材料综合价格发布(2026年3月) (市区、各县)
        </a>
        <span>2026-05-09</span>
      </li>
      <li>
        <a title="造价政策通知" href="./202605/t20260509_0000000.htm">造价政策通知</a>
        <span>2026-05-09</span>
      </li>
    </ul>
    """


def _sanming_detail_html():
    return """
    <html><head>
      <meta name="PubDate" content="2026-05-15 17:09">
      <meta name="ContentSource" content="三明市住房和城乡建设局">
    </head><body>
      <p>附件：<a href="./P020260515617912622667.rar">4月综合价.rar</a></p>
    </body></html>
    """


def _zhangzhou_list_html():
    return """
    <ul id="resources">
      <li>
        <a title="漳州建设工程材料信息（2026）第五期"
           href="/cms/html/zzszfhcxjsj/2026-06-10/1788470572.html">
           漳州建设工程材料信息（2026）第五期
        </a>
        <span>2026-06-10</span>
      </li>
      <li>
        <a title="漳州建设工程材料信息（2026）第四期"
           href="/cms/html/zzszfhcxjsj/2026-05-09/1222672042.html">
           漳州建设工程材料信息（2026）第四期
        </a>
        <span>2026-05-09</span>
      </li>
      <li>
        <a title="漳州市住建局行业动态" href="/cms/html/zzszfhcxjsj/2026-05-09/000000.html">行业动态</a>
        <span>2026-05-09</span>
      </li>
    </ul>
    """


def _zhangzhou_detail_html():
    return """
    <html><head>
      <meta name="ContentSource" content="漳州市住房和城乡建设局">
      <script>document.cookie='traditionalChinese=false;expires=2021-10-08 10:30:47;path=/';</script>
    </head><body>
      <span class="rq">日期：2026-06-10</span>
      <p>附件：<a href="/cms/pages/20686985192640004/attachments/%E6%BC%B3%E5%B7%9E%E5%BB%BA%E8%AE%BE%E5%B7%A5%E7%A8%8B%E6%9D%90%E6%96%99%E4%BF%A1%E6%81%AF%EF%BC%882026%EF%BC%89%E7%AC%AC%E4%BA%94%E6%9C%9F.pdf">漳州建设工程材料信息（2026）第五期</a></p>
    </body></html>
    """


def _ningde_list_html():
    return """
    <ul class="xw-list-1 gl_list border">
      <li>
        <span class="bf-pass">2026-06-29</span><i></i>
        <a href="./202606/P020260629320555499814.pdf" target="_blank" title="《宁德工程造价信息》2026年第6期">《宁德工程造价信息》2026年第6期</a>
      </li>
      <li>
        <span class="bf-pass">2026-05-28</span><i></i>
        <a href="./202605/P020260528323208740463.pdf" target="_blank" title="《宁德工程造价信息》2026年第5期">《宁德工程造价信息》2026年第5期</a>
      </li>
      <li>
        <span class="bf-pass">2026-02-09</span><i></i>
        <a href="../../xxgkzl/zc/qtzdgkwj/202602/t20260209_2441152.htm" target="_blank" title="宁德市住房和城乡建设局关于发布2026年春节期间建设工程人工费动态指数的通知">宁德市住房和城乡建设局关于发布2026年春节期间建设工程人工费动态指数的通知</a>
      </li>
    </ul>
    """
