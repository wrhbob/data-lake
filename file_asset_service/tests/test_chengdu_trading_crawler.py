from sqlalchemy import inspect

from app.chengdu_trading import (
    CHENGDU_TRADING_PARSER_VERSION,
    ChengduAttachment,
    ChengduNoticeSummary,
    UrllibChengduTradingClient,
    _chengdu_attachment_file_role,
    _parse_notice_detail,
    chengdu_trading_source_config,
    discover_chengdu_trading_notices,
    ingest_chengdu_trading_notice,
    run_chengdu_trading_channels,
)
from app.collection import create_data_source
from app.models import Archive, ArchiveEvent, ArchiveFile, AuditLog, CrawlLineage, FileProcessing, Outbox
from app.storage import FakeObjectStore


LIST_HTML = """
<html><body>
<form id="form1">
  <input type="hidden" name="__VIEWSTATE" value="state" />
  <input type="hidden" name="__VIEWSTATEGENERATOR" value="generator" />
  <input type="hidden" name="displaytypeval" id="displaytypeval" value="0" />
  <input type="hidden" name="displaystateval" id="displaystateval" value="0" />
  <input type="hidden" name="dealaddressval" id="dealaddressval" value="0" />
  <input type="hidden" name="divpudate" id="divpudate" value="0" />
  <input type="hidden" name="hidCrunt" id="hidCrunt" value="1" />
  <input type="hidden" name="hidLimit" id="hidLimit" value="10" />
  <input type="hidden" name="hidReload" id="hidReload" value="false" />
  <div class="list-row">
    <div class="list-item">【双流区】</div>
    <div class="list-item" title="东升47.79亩住宅项目配套道路招标公告">
      <a href="https://www.cdggzy.com/sitenew/notice/JSGC/NoticeContent.aspx?id=NOTICE001">东升47.79亩住宅项目配套道路招标公告</a>
    </div>
    <div class="list-item"><div class="dot"></div>待开标</div>
    <div class="list-item">2026-06-18</div>
  </div>
  <div class="list-row">
    <div class="list-item">【双流区】</div>
    <div class="list-item" title="双流区道路改造项目施工招标公告">
      <a href="https://www.cdggzy.com/sitenew/notice/JSGC/NoticeContent.aspx?id=NOTICE002">双流区道路改造项目施工招标公告</a>
    </div>
    <div class="list-item"><div class="dot"></div>待开标</div>
    <div class="list-item">2026-06-18</div>
  </div>
</form>
</body></html>
"""


DETAIL_HTML_1 = """
<html><head>
<meta name="ArticleTitle" content="东升47.79亩住宅项目配套道路招标公告" />
<meta name="PubDate" content="2026-06-18 18:35" />
<meta name="ContentSource" content="成都市公共资源交易服务中心" />
</head><body>
<input type="hidden" id="ContentPlaceHolder1_hidNoticeid" value="NOTICE001" />
<div class="left-process">
  <div class="process-item" post-url="https://www.cdggzy.com/sitenew/notice/JSGC/NoticeContent.aspx?id=PLAN001">
    <div class="process-title">招标计划</div><div class="process-date">2026-03-17</div>
  </div>
  <div class="process-item" post-url="https://www.cdggzy.com/sitenew/notice/JSGC/NoticeContent.aspx?id=NOTICE001">
    <div class="process-title title-active">招标公告</div><div class="process-date">2026-06-18</div>
  </div>
</div>
<div class="download-title">招标资料下载</div>
<div class="notice-fields">
  <p>项目名称：东升47.79亩住宅项目配套道路</p>
  <p>项目编号：CD2026-001</p>
  <p>招标编号：E51010020260618001</p>
  <p>标的角色：总包</p>
  <p>招标人：成都空港建设管理有限公司</p>
  <p>招标代理机构：四川良友建设咨询有限公司</p>
  <p>招标控制价：32658042.17元</p>
</div>
<div class="file-list">
  <div class="lot-name">标段名称：东升47.79亩住宅项目配套道路施工/标段</div>
  <div class="file-item"><span class="file-icon"></span>[招标文件]<a href="javascript:void(0);" onclick="DownloadFile('https://sys.cdggzy.com/files/20260618163236-招标文件.CDZ')" class="file-name">招标文件.CDZ</a></div>
  <div class="file-item"><span class="file-icon"></span>[工程图]<a href="javascript:void(0);" onclick="DownloadFile('https://sys.cdggzy.com/files/20260618163147-东升47.79亩住宅项目配套道路（签章版）.zip')" class="file-name">东升47.79亩住宅项目配套道路（签章版）.zip</a></div>
  <div class="file-item"><span class="file-icon"></span>[工程图]<a href="javascript:void(0);" onclick="DownloadFile('https://sys.cdggzy.com/files/20260618163234-图纸.pdf')" class="file-name">图纸.pdf</a></div>
</div>
</body></html>
"""


DETAIL_HTML_2 = """
<html><head>
<meta name="ArticleTitle" content="双流区道路改造项目施工招标公告" />
<meta name="PubDate" content="2026-06-18 16:20" />
</head><body>
<input type="hidden" id="ContentPlaceHolder1_hidNoticeid" value="NOTICE002" />
<div class="file-list">
  <div class="file-item"><span class="file-icon"></span>[招标文件]<a href="javascript:void(0);" onclick="DownloadFile('https://sys.cdggzy.com/files/notice002-招标文件.CDZ')" class="file-name">招标文件.CDZ</a></div>
</div>
</body></html>
"""


TERMINATION_LIST_HTML = """
<html><body>
<form id="form1">
  <input type="hidden" name="__VIEWSTATE" value="state" />
  <input type="hidden" name="__VIEWSTATEGENERATOR" value="generator" />
  <input type="hidden" name="displaytypeval" id="displaytypeval" value="14" />
  <input type="hidden" name="displaystateval" id="displaystateval" value="0" />
  <input type="hidden" name="dealaddressval" id="dealaddressval" value="0" />
  <input type="hidden" name="divpudate" id="divpudate" value="0" />
  <input type="hidden" name="hidCrunt" id="hidCrunt" value="1" />
  <input type="hidden" name="hidLimit" id="hidLimit" value="10" />
  <input type="hidden" name="hidReload" id="hidReload" value="false" />
  <div class="list-row">
    <div class="list-item">【新津区】</div>
    <div class="list-item" title="2025年新都区普通国道整治提升工程施工招标终止公告">
      <a href="https://www.cdggzy.com/sitenew/notice/JSGC/NoticeContent.aspx?id=TERM001">2025年新都区普通国道整治提升工程施工招标终止公告</a>
    </div>
    <div class="list-item"></div>
    <div class="list-item">2026-06-19</div>
  </div>
</form>
</body></html>
"""


TERMINATION_DETAIL_HTML = """
<html><head>
<meta name="ArticleTitle" content="2025年新都区普通国道整治提升工程施工招标终止公告" />
<meta name="PubDate" content="2026-06-19 10:20" />
</head><body>
<input type="hidden" id="ContentPlaceHolder1_hidNoticeid" value="TERM001" />
<div class="file-list">
  <div class="file-item"><span class="file-icon"></span>[公告附件]<a href="javascript:void(0);" onclick="DownloadFile('https://sys.cdggzy.com/files/term001.pdf')" class="file-name">终止公告.pdf</a></div>
</div>
</body></html>
"""


CHANNEL_FIXTURES = {
    "4": ("CHANGE001", "东升47.79亩住宅项目配套道路招标变更公告", "2026-06-19", "双流区"),
    "18": ("AWARDRESULT001", "东升47.79亩住宅项目配套道路中标结果公布", "2026-06-20", "双流区"),
    "19": ("PLAN001", "东升47.79亩住宅项目配套道路招标计划", "2026-06-17", "双流区"),
    "23": ("PREDISCLOSE001", "东升47.79亩住宅项目配套道路招标文件提前公示", "2026-06-16", "双流区"),
    "2": ("EVALPUBLIC001", "东升47.79亩住宅项目配套道路评标结果公开", "2026-06-20", "双流区"),
    "12": ("EVALNOTICE001", "东升47.79亩住宅项目配套道路评标结果公示", "2026-06-20", "双流区"),
}


def _single_notice_list_html(source_item_key: str, title: str, publish_date: str, region: str) -> str:
    return f"""
<html><body>
<form id="form1">
  <input type="hidden" name="__VIEWSTATE" value="state" />
  <input type="hidden" name="__VIEWSTATEGENERATOR" value="generator" />
  <input type="hidden" name="displaytypeval" id="displaytypeval" value="0" />
  <input type="hidden" name="displaystateval" id="displaystateval" value="0" />
  <input type="hidden" name="dealaddressval" id="dealaddressval" value="0" />
  <input type="hidden" name="divpudate" id="divpudate" value="0" />
  <input type="hidden" name="hidCrunt" id="hidCrunt" value="1" />
  <input type="hidden" name="hidLimit" id="hidLimit" value="10" />
  <input type="hidden" name="hidReload" id="hidReload" value="false" />
  <div class="list-row">
    <div class="list-item">【{region}】</div>
    <div class="list-item" title="{title}">
      <a href="https://www.cdggzy.com/sitenew/notice/JSGC/NoticeContent.aspx?id={source_item_key}">{title}</a>
    </div>
    <div class="list-item"></div>
    <div class="list-item">{publish_date}</div>
  </div>
</form>
</body></html>
"""


def _generic_detail_html(source_item_key: str, title: str, publish_date: str) -> str:
    return f"""
<html><head>
<meta name="ArticleTitle" content="{title}" />
<meta name="PubDate" content="{publish_date} 09:30" />
</head><body>
<input type="hidden" id="ContentPlaceHolder1_hidNoticeid" value="{source_item_key}" />
<div class="file-list">
  <div class="file-item"><span class="file-icon"></span>[公告附件]<a href="javascript:void(0);" onclick="DownloadFile('https://sys.cdggzy.com/files/{source_item_key}.pdf')" class="file-name">{source_item_key}.pdf</a></div>
</div>
</body></html>
"""


class FakeChengduClient:
    def __init__(self):
        self.calls = []

    def get_text(self, url):
        self.calls.append(("GET_TEXT", url, None))
        if url.endswith("/JSGC/List.aspx"):
            return LIST_HTML
        for source_item_key, title, publish_date, _region in CHANNEL_FIXTURES.values():
            if url.endswith(f"id={source_item_key}"):
                return _generic_detail_html(source_item_key, title, publish_date)
        if url.endswith("NOTICE001"):
            return DETAIL_HTML_1
        if url.endswith("NOTICE002"):
            return DETAIL_HTML_2
        if url.endswith("TERM001"):
            return TERMINATION_DETAIL_HTML
        raise AssertionError(f"unexpected text url: {url}")

    def post_form(self, url, data):
        self.calls.append(("POST_FORM", url, dict(data)))
        assert url.endswith("/JSGC/List.aspx")
        assert data["hidCrunt"] == "1"
        if data["displaytypeval"] == "14":
            return TERMINATION_LIST_HTML
        if data["displaytypeval"] in CHANNEL_FIXTURES:
            return _single_notice_list_html(*CHANNEL_FIXTURES[data["displaytypeval"]])
        assert data["displaytypeval"] == "1"
        return LIST_HTML

    def get_bytes(self, url):
        self.calls.append(("GET_BYTES", url, None))
        if "招标文件.CDZ" in url:
            return b"opaque cdz bytes, do not decompress", "application/octet-stream"
        if "签章版" in url:
            return b"opaque zip bytes, do not decompress", "application/x-zip-compressed"
        if url.endswith("图纸.pdf"):
            return b"%PDF opaque drawing bytes", "application/pdf"
        if "notice002" in url:
            return b"notice 2 opaque cdz bytes", "application/octet-stream"
        if url.endswith("term001.pdf"):
            return b"%PDF opaque tombstone bytes", "application/pdf"
        if url.endswith(".pdf"):
            return b"%PDF opaque channel bytes", "application/pdf"
        raise AssertionError(f"unexpected file url: {url}")


class FirstAttachmentTimeoutClient(FakeChengduClient):
    def get_bytes(self, url):
        self.calls.append(("GET_BYTES", url, None))
        if "20260618163236-招标文件.CDZ" in url:
            raise TimeoutError("ATTACHMENT_DOWNLOAD_TIMEOUT: total timeout 0.1s")
        return super().get_bytes(url)


def cell_value(cell):
    return cell["value"]


def create_chengdu_source(db_session):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="public_resource_exchange",
        connector_type="source_registry",
        name="成都市公共资源交易服务中心",
        data_domain="trading",
        region_code="510100",
        config=chengdu_trading_source_config(),
    )


def test_chengdu_trading_source_config_records_aspx_channels_and_boundaries():
    config = chengdu_trading_source_config()
    parser = config["parser"]["parsers"][CHENGDU_TRADING_PARSER_VERSION]
    channels = {
        channel["channel_id"]: (
            channel["displaytypeval"],
            channel["notice_type_raw"],
            channel["notice_family"],
        )
        for channel in parser["channels"]
    }

    assert config["stable"]["site_id"] == "trading.cdggzy.jsgc"
    assert config["stable"]["region_code"] == "510100"
    assert config["ops"]["crawl_strategy_tier"] == "tier1_aspx_form_post"
    assert config["ops"]["tls"]["openssl_ciphers"] == "DEFAULT:@SECLEVEL=1"
    assert parser["field_rules"]["project_name_raw"] == {"source": "list_or_detail_text"}
    assert channels == {
        "tender_plan": ("19", "招标计划", "tender_plan"),
        "tender_notice": ("1", "招标公告", "tender"),
        "change_notice": ("4", "变更公告", "change"),
        "award_result": ("18", "中标结果公布", "award"),
        "tender_file_pre_disclosure": ("23", "招标文件提前公示", "tender_pre_disclosure"),
        "evaluation_result_public": ("2", "评标结果公开", "award"),
        "evaluation_result_notice": ("12", "评标结果公示", "award"),
        "termination_notice": ("14", "流标或终止公告", "tombstone"),
    }
    assert ".cdz" in parser["attachment_rules"]["opaque_container_extensions"]
    assert parser["red_lines"] == [
        "do_not_decompress_container",
        "do_not_parse_priced_source",
        "do_not_group_project_code",
        "gated_metadata_only",
    ]


def test_chengdu_client_exposes_polite_throttle_controls():
    client = UrllibChengduTradingClient(timeout=15, min_interval_seconds=1.25, jitter_seconds=0.5)

    assert client.timeout == 15
    assert client.min_interval_seconds == 1.25
    assert client.jitter_seconds == 0.5


def test_chengdu_discover_posts_custom_publish_date_range():
    client = FakeChengduClient()

    discover_chengdu_trading_notices(
        client,
        notice_type_value="1",
        notice_type_raw="招标公告",
        channel_id="tender_notice",
        notice_family="tender",
        page=1,
        start_date="2026-06-22",
        end_date="2026-06-23",
    )

    post_call = next(call for call in client.calls if call[0] == "POST_FORM")
    payload = post_call[2]
    assert payload["divpudate"] == "5"
    assert payload["inputTime1"] == "2026-06-22"
    assert payload["inputTime2"] == "2026-06-23"


def test_chengdu_attachment_role_uses_source_label_for_qingdan_zip():
    assert (
        _chengdu_attachment_file_role(
            ChengduAttachment(file_name="附件.zip", url="https://sys.cdggzy.com/files/list.zip", category_raw="工程量清单")
        )
        == "qingdan_package"
    )


def test_chengdu_attachment_role_prefers_geological_when_filename_mentions_dikan():
    assert (
        _chengdu_attachment_file_role(
            ChengduAttachment(
                file_name="图纸及地勘报告.zip",
                url="https://sys.cdggzy.com/files/drawing-geological.zip",
                category_raw="工程图",
            )
        )
        == "geological"
    )


def test_chengdu_attachment_role_marks_design_task_docx_as_tender_doc():
    assert (
        _chengdu_attachment_file_role(
            ChengduAttachment(
                file_name="设计任务书-同乐公园.docx",
                url="https://sys.cdggzy.com/files/design-task.docx",
                category_raw="工程图",
            )
        )
        == "tender_doc"
    )


def test_chengdu_detail_l1_hooks_handle_unit_labels_without_body_prose_capture():
    detail = _parse_notice_detail(
        """
        <html><head>
        <meta name="ArticleTitle" content="青羊区学校改扩建项目招标公告" />
        <meta name="PubDate" content="2026-06-20 09:30" />
        </head><body>
        <input type="hidden" id="ContentPlaceHolder1_hidNoticeid" value="HOOK001" />
        <p>正文说明本项目名称：这句话不是结构化字段。</p>
        <p>项目名称：青羊区学校改扩建项目</p>
        <p>招标控制价（元）：12800000.00</p>
        </body></html>
        """,
        summary=ChengduNoticeSummary(
            source_item_key="HOOK001",
            title="青羊区学校改扩建项目招标公告",
            detail_url="https://www.cdggzy.com/sitenew/notice/JSGC/NoticeContent.aspx?id=HOOK001",
            publish_date="2026-06-20",
            region_raw="青羊区",
            status_raw="待开标",
            notice_type_raw="招标公告",
            channel_id="tender_notice",
            notice_family="tender",
            displaytypeval="1",
        ),
    )

    assert detail["project_name_raw"] == "青羊区学校改扩建项目"
    assert detail["amount_raw"] == "12800000.00"


def test_chengdu_detail_l1_hooks_parse_word_html_phrases_and_section_role():
    detail = _parse_notice_detail(
        """
        <html><head>
        <meta name="ArticleTitle" content="同乐公园(一期)项目勘察设计招标公告" />
        <meta name="PubDate" content="2026-06-23 09:30" />
        </head><body>
        <input type="hidden" id="ContentPlaceHolder1_hidNoticeid" value="WORD001" />
        <p><span><font face="宋体">批准建设，项目业主为</font></span><u><span><font face="宋体">成都兴华生态建设开发有限公司</font></span></u><span>，建设资金来自财政资金，招标人为</span><u><span>成都兴华生态建设开发有限公司</span></u><span>。</span></p>
        <p><span>招标人选择的招标代理机构是</span><u><span>四川标诚工程项目管理有限公司</span></u><span>。</span></p>
        <div class="file-list"><div class="lot-name">标段名称：同乐公园(一期)项目勘察设计/标段</div></div>
        </body></html>
        """,
        summary=ChengduNoticeSummary(
            source_item_key="WORD001",
            title="同乐公园(一期)项目勘察设计招标公告",
            detail_url="https://www.cdggzy.com/sitenew/notice/JSGC/NoticeContent.aspx?id=WORD001",
            publish_date="2026-06-23",
            region_raw="成华区",
            status_raw="待开标",
            notice_type_raw="招标公告",
            channel_id="tender_notice",
            notice_family="tender",
            displaytypeval="1",
        ),
    )

    assert detail["project_name_raw"] == "同乐公园(一期)项目"
    assert detail["section_no_raw"] == "同乐公园(一期)项目勘察设计/标段"
    assert detail["bid_subject_role_raw"] == "勘察设计"
    assert detail["tenderer_raw"] == "成都兴华生态建设开发有限公司"
    assert detail["agency_raw"] == "四川标诚工程项目管理有限公司"


def test_chengdu_detail_l1_hooks_treat_slash_agency_as_empty():
    detail = _parse_notice_detail(
        """
        <html><head><meta name="ArticleTitle" content="老旧小区项目设计/标段招标公告" /></head><body>
        <input type="hidden" id="ContentPlaceHolder1_hidNoticeid" value="WORD002" />
        <p><span>项目业主为</span><u><span>成都市金牛国有资产投资经营集团有限公司</span></u><span>。</span></p>
        <p><span>招标人选择的招标代理机构是</span><u><span>/</span></u><span>。</span></p>
        <p><u><span><font face="宋体">2.1.项目名称：2026年金牛区老旧小区主体工程改造项目。</font></span></u></p>
        <div class="file-list"><div class="lot-name">标段名称：2026年金牛区老旧小区主体工程改造项目设计/标段</div></div>
        </body></html>
        """,
        summary=ChengduNoticeSummary(
            source_item_key="WORD002",
            title="老旧小区项目设计/标段招标公告",
            detail_url="https://www.cdggzy.com/sitenew/notice/JSGC/NoticeContent.aspx?id=WORD002",
            publish_date="2026-06-23",
            region_raw="金牛区",
            status_raw="待开标",
            notice_type_raw="招标公告",
            channel_id="tender_notice",
            notice_family="tender",
            displaytypeval="1",
        ),
    )

    assert detail["project_name_raw"] == "2026年金牛区老旧小区主体工程改造项目"
    assert detail["bid_subject_role_raw"] == "设计"
    assert detail["tenderer_raw"] == "成都市金牛国有资产投资经营集团有限公司"
    assert detail["agency_raw"] is None


def test_chengdu_tender_notice_ingests_real_site_shape_without_decompressing_or_grouping(db_session):
    source = create_chengdu_source(db_session)
    client = FakeChengduClient()
    summaries = discover_chengdu_trading_notices(
        client,
        notice_type_value="1",
        notice_type_raw="招标公告",
        channel_id="tender_notice",
        notice_family="tender",
        page=1,
    )

    archive = ingest_chengdu_trading_notice(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        summary=summaries[0],
        actor_id="chengdu-trading-test",
    )
    db_session.refresh(archive)

    files = db_session.query(ArchiveFile).filter_by(archive_id=archive.archive_id).all()
    lineages = db_session.query(CrawlLineage).filter_by(archive_id=archive.archive_id).all()
    events = db_session.query(ArchiveEvent).filter_by(archive_id=archive.archive_id).all()

    assert [call[0] for call in client.calls] == [
        "GET_TEXT",
        "POST_FORM",
        "GET_TEXT",
        "GET_BYTES",
        "GET_BYTES",
        "GET_BYTES",
    ]
    assert archive.business_key == f"trading:{source.source_id}:notice:notice001"
    assert archive.source_item_key == "NOTICE001"
    assert "project" not in archive.business_key.lower()
    assert "东升47.79亩住宅项目配套道路" not in archive.business_key
    assert "CD2026-001" not in archive.business_key
    assert "E51010020260618001" not in archive.business_key
    assert cell_value(archive.metadata_payload["notice_type_raw"]) == "招标公告"
    assert cell_value(archive.metadata_payload["source_item_key"]) == "NOTICE001"
    assert cell_value(archive.metadata_payload["project_name_raw"]) == "东升47.79亩住宅项目配套道路"
    assert cell_value(archive.metadata_payload["project_code_raw"]) == "CD2026-001"
    assert cell_value(archive.metadata_payload["tender_code_raw"]) == "E51010020260618001"
    assert cell_value(archive.metadata_payload["section_no_raw"]) == "东升47.79亩住宅项目配套道路施工/标段"
    assert cell_value(archive.metadata_payload["bid_subject_role_raw"]) == "总包"
    assert cell_value(archive.metadata_payload["tenderer_raw"]) == "成都空港建设管理有限公司"
    assert cell_value(archive.metadata_payload["agency_raw"]) == "四川良友建设咨询有限公司"
    assert cell_value(archive.metadata_payload["amount_raw"]) == "32658042.17元"
    assert archive.field_sources["business_key"]["source_level"] == "crawler"

    roles_by_name = {mounted.display_name: mounted.file_role for mounted in files}
    assert roles_by_name["NOTICE001.html"] == "web_snapshot"
    assert roles_by_name["招标文件.CDZ"] == "qingdan_package"
    assert roles_by_name["东升47.79亩住宅项目配套道路（签章版）.zip"] == "drawing"
    assert roles_by_name["图纸.pdf"] == "drawing"
    assert db_session.query(FileProcessing).count() == 0

    assert {lineage.parser_version for lineage in lineages} == {CHENGDU_TRADING_PARSER_VERSION}
    assert lineages[0].source_item_key == "NOTICE001"
    assert lineages[0].source_metadata["project_name_raw"] == "东升47.79亩住宅项目配套道路"
    assert lineages[0].source_metadata["project_code_raw"] == "CD2026-001"
    assert lineages[0].source_metadata["tender_code_raw"] == "E51010020260618001"
    assert lineages[0].source_metadata["section_no_raw"] == "东升47.79亩住宅项目配套道路施工/标段"
    assert lineages[0].source_metadata["bid_subject_role_raw"] == "总包"
    assert lineages[0].source_metadata["tenderer_raw"] == "成都空港建设管理有限公司"
    assert lineages[0].source_metadata["agency_raw"] == "四川良友建设咨询有限公司"
    assert lineages[0].source_metadata["amount_raw"] == "32658042.17元"
    assert lineages[0].source_metadata["discovered_at"] == "2026-06-18T00:00:00+08:00"
    assert db_session.query(Outbox).count() == len(events)
    assert "trading_project" not in inspect(db_session.get_bind()).get_table_names()


def test_chengdu_same_region_same_notice_type_remains_independent_archives(db_session):
    source = create_chengdu_source(db_session)
    client = FakeChengduClient()
    summaries = discover_chengdu_trading_notices(
        client,
        notice_type_value="1",
        notice_type_raw="招标公告",
        channel_id="tender_notice",
        notice_family="tender",
        page=1,
    )

    first = ingest_chengdu_trading_notice(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        summary=summaries[0],
    )
    second = ingest_chengdu_trading_notice(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        summary=summaries[1],
    )

    assert first.archive_id != second.archive_id
    assert db_session.query(Archive).filter_by(domain_type="trading").count() == 2
    assert first.metadata_payload["project_code_raw"]["value"] == "CD2026-001"
    assert "CD2026-001" not in first.business_key
    assert second.metadata_payload["project_code_raw"]["value"] is None


def test_chengdu_channel_runner_ingests_tender_and_tombstone_without_propagation(db_session):
    source = create_chengdu_source(db_session)
    client = FakeChengduClient()

    report = run_chengdu_trading_channels(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        channel_ids=["tender_notice", "termination_notice"],
        max_pages=1,
        max_items_per_channel=1,
        actor_id="chengdu-channel-test",
    )

    archives = db_session.query(Archive).filter_by(domain_type="trading").order_by(Archive.publish_date).all()

    assert report["ingested_count"] == 2
    assert report["skipped_existing_count"] == 0
    assert [archive.metadata_payload["notice_family"]["value"] for archive in archives] == ["tender", "tombstone"]
    assert archives[1].metadata_payload["notice_type_raw"]["value"] == "流标或终止公告"
    assert archives[1].metadata_payload["project_code_raw"]["value"] is None
    assert "trading_project" not in inspect(db_session.get_bind()).get_table_names()
    assert db_session.query(FileProcessing).count() == 0


def test_chengdu_channel_runner_records_attachment_timeout_and_continues_without_half_archive(db_session):
    source = create_chengdu_source(db_session)
    client = FirstAttachmentTimeoutClient()

    report = run_chengdu_trading_channels(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        channel_ids=["tender_notice"],
        max_pages=1,
        max_items_per_channel=2,
        actor_id="chengdu-download-guard-test",
    )

    archives = db_session.query(Archive).filter_by(domain_type="trading").all()
    audit = db_session.query(AuditLog).filter_by(action="TRADING_ATTACHMENT_DOWNLOAD_FAILED").one()

    assert report["ingested_count"] == 1
    assert report["failed_count"] == 1
    assert report["failed_items"][0]["error_kind"] == "attachment_timeout"
    assert report["failed_items"][0]["source_item_key"] == "NOTICE001"
    assert report["failed_items"][0]["file_name"] == "招标文件.CDZ"
    assert [archive.source_item_key for archive in archives] == ["NOTICE002"]
    assert db_session.query(Archive).filter_by(source_item_key="NOTICE001").count() == 0
    assert audit.target_type == "data_source"
    assert audit.target_id == source.source_id
    assert audit.error_code == "CHENGDU_ATTACHMENT_DOWNLOAD_TIMEOUT"
    assert audit.after_payload["source_item_key"] == "NOTICE001"
    assert audit.after_payload["title"] == "东升47.79亩住宅项目配套道路招标公告"
    assert audit.after_payload["file_name"] == "招标文件.CDZ"
    assert audit.after_payload["attachment_url"].endswith("招标文件.CDZ")
    assert "failed_at" in audit.after_payload
    assert db_session.query(FileProcessing).count() == 0


def test_chengdu_channel_runner_ingests_all_displaytype_channels_without_cross_channel_grouping(db_session):
    source = create_chengdu_source(db_session)
    client = FakeChengduClient()

    report = run_chengdu_trading_channels(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        max_pages=1,
        max_items_per_channel=1,
        actor_id="chengdu-all-channel-test",
        as_of_date="2026-06-21",
    )

    archives = db_session.query(Archive).filter_by(domain_type="trading").all()
    notice_types_by_channel = {
        archive.metadata_payload["channel_id"]["value"]: archive.metadata_payload["notice_type_raw"]["value"]
        for archive in archives
    }
    channel_reports = {channel["channel_id"]: channel for channel in report["channels"]}

    assert report["ingested_count"] == 8
    assert report["skipped_existing_count"] == 0
    assert notice_types_by_channel == {
        "tender_plan": "招标计划",
        "tender_notice": "招标公告",
        "change_notice": "变更公告",
        "award_result": "中标结果公布",
        "tender_file_pre_disclosure": "招标文件提前公示",
        "evaluation_result_public": "评标结果公开",
        "evaluation_result_notice": "评标结果公示",
        "termination_notice": "流标或终止公告",
    }
    tender_archive = next(archive for archive in archives if archive.metadata_payload["channel_id"]["value"] == "tender_notice")
    assert tender_archive.metadata_payload["project_code_raw"]["value"] == "CD2026-001"
    assert all("CD2026-001" not in archive.business_key for archive in archives)
    assert all("project_code" not in archive.business_key for archive in archives)
    assert channel_reports["termination_notice"]["notice_family"] == "tombstone"
    assert channel_reports["termination_notice"]["cursor_source_item_key"] == "TERM001"
    assert channel_reports["termination_notice"]["cursor_publish_date"] == "2026-06-19"
    assert channel_reports["termination_notice"]["crawl_lag_days"] == 2
    tombstone = next(
        archive for archive in archives if archive.metadata_payload["channel_id"]["value"] == "termination_notice"
    )
    assert "is_withdrawn" not in tombstone.metadata_payload
    assert "trading_project" not in inspect(db_session.get_bind()).get_table_names()
    assert db_session.query(FileProcessing).count() == 0


def test_chengdu_incremental_stops_on_existing_business_key_before_detail_download(db_session):
    source = create_chengdu_source(db_session)
    client = FakeChengduClient()
    summaries = discover_chengdu_trading_notices(
        client,
        notice_type_value="1",
        notice_type_raw="招标公告",
        channel_id="tender_notice",
        notice_family="tender",
        page=1,
    )
    ingest_chengdu_trading_notice(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        summary=summaries[0],
    )
    client.calls.clear()

    report = run_chengdu_trading_channels(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        channel_ids=["tender_notice"],
        max_pages=1,
        actor_id="chengdu-incremental-test",
        as_of_date="2026-06-21",
    )

    assert report["ingested_count"] == 0
    assert report["skipped_existing_count"] == 1
    assert report["channels"][0]["cursor_source_item_key"] == "NOTICE001"
    assert report["channels"][0]["cursor_publish_date"] == "2026-06-18"
    assert report["channels"][0]["crawl_lag_days"] == 3
    assert report["channels"][0]["stopped_on_existing"] is True
    assert db_session.query(Archive).filter_by(domain_type="trading").count() == 1
    assert [call[0] for call in client.calls] == ["GET_TEXT", "POST_FORM"]
