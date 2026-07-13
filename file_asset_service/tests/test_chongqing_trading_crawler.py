from sqlalchemy import inspect

from app.chongqing_trading import (
    CHONGQING_TRADING_BASE_URL,
    CHONGQING_TRADING_PARSER_VERSION,
    ChongqingAttachment,
    UrllibChongqingTradingClient,
    _chongqing_attachment_file_role,
    _parse_download_attach_attachments,
    chongqing_trading_source_config,
    discover_chongqing_trading_notices,
    ingest_chongqing_trading_notice,
    run_chongqing_pending_attachment_fetches,
    run_chongqing_trading_channels,
)
from app.collection import create_data_source
from app.models import Archive, ArchiveFile, AuditLog, CrawlLineage, FileAsset, FileProcessing
from app.storage import FakeObjectStore
from app.trading_registry import FETCH_STATUS_FETCHED, FETCH_STATUS_PENDING


def cell_value(cell):
    return cell["value"]


SEARCH_RESPONSE = {
    "code": 200,
    "content": """
    {
      "result": {
        "totalcount": 2,
        "records": [
          {
            "infoid": "e211e7b2-2d6f-44da-988c-bd06bcf2255d",
            "infoId": "e211e7b2-2d6f-44da-988c-bd06bcf2255d",
            "title": "轻型汽车国七排放试验能力建设项目",
            "categorynum": "014001001001",
            "categorytype": "工程招投标",
            "categorytype2": "招标公告",
            "pubinwebdate": "2026-06-23 16:39:43",
            "webdate": "2026-06-23 16:39:43",
            "infoc": "自主招标",
            "infod": "50000120260616025050101",
            "companyname": "中国汽车工程研究院股份有限公司"
          },
          {
            "infoid": "54aafe7b-ad81-4f12-917c-3aa95b5db0ae",
            "title": "荣昌区城市支线排水管网雨污分流改造整治工程（一期）—高新区片区",
            "categorynum": "014001001002",
            "categorytype": "工程招投标",
            "categorytype2": "招标公告",
            "pubinwebdate": "2026-06-23 14:15:00",
            "infoc": "荣昌区",
            "infod": "50022620260623001010101",
            "companyname": "重庆兴荣新成工程建设管理有限公司"
          }
        ]
      }
    }
    """,
}


DETAIL_CONTENT = """
<div class="notice">
  <p>轻型汽车国七排放试验能力建设项目招标公告</p>
  <a href="javascript:void(0)" onclick="downloadAttach('38ce52d3-56df-43d3-b78d-b9d641cc83d0','CQ007','e211e7b2-2d6f-44da-988c-bd06bcf2255d')">招标公告.pdf</a>
  <a href="javascript:void(0)" onclick="downloadAttach('8ca38ca6-0ec0-4d5b-91d6-e97d15ef7509','CQ012','e211e7b2-2d6f-44da-988c-bd06bcf2255d')">图纸-中国汽研国七项目.zip</a>
  <a href="javascript:void(0)" onclick="downloadAttach('75cc1efe-8a0a-4740-8538-456f69ec812e','J070','e211e7b2-2d6f-44da-988c-bd06bcf2255d')">工程量清单.pdf</a>
  <a href="javascript:void(0)" onclick="downloadAttach('87b562a6-201a-4258-96c3-6a17ff0dcd68','J070','e211e7b2-2d6f-44da-988c-bd06bcf2255d')">[50000120260616025050101].cqzf</a>
</div>
"""


DETAIL_RESPONSE = {
    "result": {
        "currentRecord": {
            "infoId": "e211e7b2-2d6f-44da-988c-bd06bcf2255d",
            "title": "轻型汽车国七排放试验能力建设项目",
            "categoryNum": "014001001001",
            "categoryType": "工程招投标",
            "categoryType2": "招标公告",
            "infoDate": "2026-06-23 16:39:43",
            "infoC": "自主招标",
            "infoD": "50000120260616025050101",
            "companyName": "中国汽车工程研究院股份有限公司",
            "daiLiDanWei": "重庆招标采购集团",
            "biaoDuanName": "轻型汽车国七排放试验能力建设项目施工",
            "infoContent": DETAIL_CONTENT,
            "attachVoList": [],
        }
    }
}


SECOND_DETAIL_RESPONSE = {
    "result": {
        "currentRecord": {
            "infoId": "54aafe7b-ad81-4f12-917c-3aa95b5db0ae",
            "title": "荣昌区城市支线排水管网雨污分流改造整治工程（一期）—高新区片区",
            "categoryNum": "014001001002",
            "categoryType": "工程招投标",
            "categoryType2": "招标公告",
            "infoDate": "2026-06-23 14:15:00",
            "infoC": "荣昌区",
            "infoD": "50022620260623001010101",
            "companyName": "重庆兴荣新成工程建设管理有限公司",
            "daiLiDanWei": "重庆天合工程技术咨询有限公司",
            "biaoDuanName": "荣昌区城市支线排水管网雨污分流改造整治工程（一期）一标段",
            "infoContent": """
            <a onclick="downloadAttach('6731f162-4ff8-45f7-bc14-8e19d6a36501','CQ007','54aafe7b-ad81-4f12-917c-3aa95b5db0ae')">招标公告.pdf</a>
            <a onclick="downloadAttach('b1d9e336-7677-4c9f-b471-8085973403be','CQ012','54aafe7b-ad81-4f12-917c-3aa95b5db0ae')">施工图.zip</a>
            <a onclick="downloadAttach('d849cb62-2f00-4cc8-9de8-50e1e897b513','J070','54aafe7b-ad81-4f12-917c-3aa95b5db0ae')">工程量清单.pdf</a>
            """,
            "attachVoList": [],
        }
    }
}


class FakeChongqingClient:
    def __init__(self, *, fail_first_attachment=False, fail_all_attachments=False, fail_drawing_download=False):
        self.calls = []
        self.fail_first_attachment = fail_first_attachment
        self.fail_all_attachments = fail_all_attachments
        self.fail_drawing_download = fail_drawing_download

    def post_json(self, url, payload):
        self.calls.append(("POST_JSON", url, payload))
        return SEARCH_RESPONSE

    def get_json(self, url):
        self.calls.append(("GET_JSON", url, None))
        if "e211e7b2" in url:
            return DETAIL_RESPONSE
        if "54aafe7b" in url:
            return SECOND_DETAIL_RESPONSE
        raise AssertionError(f"unexpected detail url: {url}")

    def get_bytes(self, url):
        self.calls.append(("GET_BYTES", url, None))
        if self.fail_all_attachments:
            raise TimeoutError("ATTACHMENT_DOWNLOAD_TIMEOUT: total timeout 300s")
        if self.fail_first_attachment and "38ce52d3" in url:
            raise TimeoutError("ATTACHMENT_DOWNLOAD_TIMEOUT: total timeout 300s")
        if "38ce52d3" in url or "6731f162" in url:
            return b"%PDF announcement", "application/pdf"
        if "8ca38ca6" in url or "b1d9e336" in url:
            if self.fail_drawing_download:
                raise AssertionError("drawing downloads should be deferred in the main batch")
            return b"PK opaque drawing zip bytes", "application/zip"
        if "75cc1efe" in url or "d849cb62" in url:
            return b"%PDF qingdan bytes", "application/pdf"
        if "87b562a6" in url:
            return b'<?xml version="1.0"?><ZBFile>opaque cqzf bytes</ZBFile>', "application/octet-stream"
        raise AssertionError(f"unexpected file url: {url}")


def create_chongqing_source(db_session):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="public_resource_exchange",
        connector_type="source_registry",
        name="重庆市公共资源交易中心",
        data_domain="trading",
        region_code="500000",
        config=chongqing_trading_source_config(),
    )


def test_chongqing_source_config_records_json_api_adapter_and_l0_boundaries():
    config = chongqing_trading_source_config()
    parser = config["parser"]["parsers"][CHONGQING_TRADING_PARSER_VERSION]
    channels = {channel["channel_id"]: channel for channel in parser["channels"]}

    assert config["stable"]["site_id"] == "trading.cqggzy.jsgc"
    assert config["stable"]["region_code"] == "500000"
    assert config["ops"]["crawl_strategy_tier"] == "tier2_json_search_api"
    assert parser["search_api_url"] == "https://www.cqggzy.com/api/v2/search-engine-page"
    assert parser["pagination"] == {"mode": "offset", "offset_field": "pn", "page_size_field": "rn"}
    assert parser["detail_api"] == "/api/page/{infoid}?categoryNum={categorynum}"
    assert parser["attachment_discovery"] == "infoContent.downloadAttach"
    assert channels["tender_notice"]["category_num"] == "014001001"
    assert "do_not_decompress_container" in parser["red_lines"]
    assert "do_not_group_project_code" in parser["red_lines"]


def test_chongqing_offpeak_client_uses_browser_context_headers():
    client = UrllibChongqingTradingClient(referer=f"{CHONGQING_TRADING_BASE_URL}/")
    headers = client._headers()

    assert headers["Referer"] == "https://www.cqggzy.com/"
    assert "Mozilla" in headers["User-Agent"]
    assert headers["Accept"]
    assert headers["Accept-Language"].startswith("zh-CN")


def test_chongqing_pending_attachment_fetch_runner_uses_offpeak_client(monkeypatch, db_session):
    captured = {}

    def fake_run_pending_trading_fetches(session, storage, *, source_id, client, limit, actor_id):
        captured["session"] = session
        captured["storage"] = storage
        captured["source_id"] = source_id
        captured["client"] = client
        captured["limit"] = limit
        captured["actor_id"] = actor_id
        return {"seen_count": 0, "fetched_count": 0, "failed_count": 0, "fetched_items": [], "failed_items": []}

    monkeypatch.setattr("app.chongqing_trading.run_pending_trading_fetches", fake_run_pending_trading_fetches)

    report = run_chongqing_pending_attachment_fetches(
        db_session,
        FakeObjectStore(),
        source_id="cq-source-id",
        limit=2,
        actor_id="cq-offpeak-test",
    )

    headers = captured["client"]._headers()
    assert report["seen_count"] == 0
    assert captured["source_id"] == "cq-source-id"
    assert captured["limit"] == 2
    assert captured["actor_id"] == "cq-offpeak-test"
    assert headers["Referer"] == "https://www.cqggzy.com/"
    assert captured["client"].read_total_timeout_seconds == 1200
    assert captured["client"].read_timeout_seconds == 1200


def test_chongqing_discover_posts_search_api_with_pn_as_offset_and_unwraps_content_json():
    client = FakeChongqingClient()

    summaries = discover_chongqing_trading_notices(
        client,
        category_num="014001001",
        notice_type_raw="招标公告",
        channel_id="tender_notice",
        notice_family="tender",
        offset=10,
        page_size=10,
        start_date="2026-06-23",
        end_date="2026-06-23",
    )

    assert len(summaries) == 2
    assert summaries[0].source_item_key == "e211e7b2-2d6f-44da-988c-bd06bcf2255d"
    assert summaries[0].category_num == "014001001001"
    assert summaries[0].publish_date == "2026-06-23"
    assert summaries[0].region_raw == "自主招标"
    assert summaries[0].project_code_raw == "50000120260616025050101"
    assert summaries[0].detail_url.endswith(
        "/trade/014001/e211e7b2-2d6f-44da-988c-bd06bcf2255d?categoryNum=014001001001"
    )

    post_call = client.calls[0]
    payload = post_call[2]
    assert post_call[0] == "POST_JSON"
    assert payload["pn"] == 10
    assert payload["rn"] == 10
    assert payload["condition"][0]["fieldName"] == "categorynum"
    assert payload["condition"][0]["equal"] == "014001001"
    assert payload["condition"][0]["isLike"] is True
    assert payload["time"][0]["fieldName"] == "webdate"
    assert payload["time"][0]["startTime"] == "2026-06-23 00:00:00"


def test_chongqing_parses_download_attach_links_from_info_content_not_attach_vo_list():
    attachments = _parse_download_attach_attachments(DETAIL_CONTENT)

    assert [attachment.file_name for attachment in attachments] == [
        "招标公告.pdf",
        "图纸-中国汽研国七项目.zip",
        "工程量清单.pdf",
        "[50000120260616025050101].cqzf",
    ]
    assert all("cQZBFileDownAttachAction.action" in attachment.url for attachment in attachments)
    assert all("ClientGuid=e211e7b2-2d6f-44da-988c-bd06bcf2255d" in attachment.url for attachment in attachments)
    assert _chongqing_attachment_file_role(attachments[0]) == "tender_doc"
    assert _chongqing_attachment_file_role(attachments[1]) == "drawing"
    assert _chongqing_attachment_file_role(attachments[2]) == "qingdan_package"
    assert _chongqing_attachment_file_role(attachments[3]) == "qingdan_package"
    assert (
        _chongqing_attachment_file_role(
            ChongqingAttachment(
                file_name="比选文件-凤凰镇杨家庙村公共服务中心建设项目定稿.docx",
                url="https://example.com/file.docx",
                file_code="CQ007",
                attach_guid="file-guid",
                client_guid="client-guid",
            )
        )
        == "tender_doc"
    )
    cq012_drawing = ChongqingAttachment(
        file_name="B区-01-06.rar",
        url="https://example.com/drawing.rar",
        file_code="CQ012",
        attach_guid="drawing-guid",
        client_guid="client-guid",
    )
    assert _chongqing_attachment_file_role(cq012_drawing) == "drawing"


def test_chongqing_tender_notice_ingests_download_attach_files_opaque_without_grouping(db_session):
    source = create_chongqing_source(db_session)
    client = FakeChongqingClient()
    summaries = discover_chongqing_trading_notices(
        client,
        category_num="014001001",
        notice_type_raw="招标公告",
        channel_id="tender_notice",
        notice_family="tender",
        start_date="2026-06-23",
        end_date="2026-06-23",
    )

    archive = ingest_chongqing_trading_notice(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        summary=summaries[0],
        actor_id="chongqing-trading-test",
    )
    db_session.refresh(archive)

    files = db_session.query(ArchiveFile).filter_by(archive_id=archive.archive_id).all()
    assets = db_session.query(FileAsset).order_by(FileAsset.file_name).all()
    lineages = db_session.query(CrawlLineage).filter_by(archive_id=archive.archive_id).all()

    assert [call[0] for call in client.calls] == [
        "POST_JSON",
        "GET_JSON",
        "GET_BYTES",
        "GET_BYTES",
        "GET_BYTES",
    ]
    assert archive.business_key == f"trading:{source.source_id}:notice:e211e7b2-2d6f-44da-988c-bd06bcf2255d"
    assert archive.source_item_key == "e211e7b2-2d6f-44da-988c-bd06bcf2255d"
    assert "50000120260616025050101" not in archive.business_key
    assert "轻型汽车国七排放试验能力建设项目" not in archive.business_key
    assert cell_value(archive.metadata_payload["notice_type_raw"]) == "招标公告"
    assert cell_value(archive.metadata_payload["project_name_raw"]) == "轻型汽车国七排放试验能力建设项目"
    assert cell_value(archive.metadata_payload["project_code_raw"]) == "50000120260616025050101"
    assert cell_value(archive.metadata_payload["section_no_raw"]) == "轻型汽车国七排放试验能力建设项目施工"
    assert cell_value(archive.metadata_payload["tenderer_raw"]) == "中国汽车工程研究院股份有限公司"
    assert cell_value(archive.metadata_payload["agency_raw"]) == "重庆招标采购集团"
    assert cell_value(archive.metadata_payload["attachment_count"]) == 4
    assert cell_value(archive.metadata_payload["qingdan_package_count"]) == 2

    roles_by_name = {mounted.display_name: mounted.file_role for mounted in files}
    mounted_by_name = {mounted.display_name: mounted for mounted in files}
    assert roles_by_name["e211e7b2-2d6f-44da-988c-bd06bcf2255d.html"] == "web_snapshot"
    assert roles_by_name["招标公告.pdf"] == "tender_doc"
    assert roles_by_name["图纸-中国汽研国七项目.zip"] == "drawing"
    assert roles_by_name["工程量清单.pdf"] == "qingdan_package"
    assert roles_by_name["[50000120260616025050101].cqzf"] == "qingdan_package"
    assert mounted_by_name["图纸-中国汽研国七项目.zip"].file_id is None
    assert mounted_by_name["图纸-中国汽研国七项目.zip"].fetch_status == FETCH_STATUS_PENDING
    assert mounted_by_name["工程量清单.pdf"].fetch_status == FETCH_STATUS_FETCHED
    assert mounted_by_name["[50000120260616025050101].cqzf"].fetch_status == FETCH_STATUS_FETCHED
    assert {asset.file_name: asset.parent_file_id for asset in assets}["[50000120260616025050101].cqzf"] is None
    assert "图纸-中国汽研国七项目.zip" not in {asset.file_name for asset in assets}
    assert db_session.query(FileProcessing).count() == 0

    assert {lineage.parser_version for lineage in lineages} == {CHONGQING_TRADING_PARSER_VERSION}
    assert lineages[0].source_metadata["project_code_raw"] == "50000120260616025050101"
    assert lineages[0].source_metadata["project_name_raw"] == "轻型汽车国七排放试验能力建设项目"
    assert "trading_project" not in inspect(db_session.get_bind()).get_table_names()


def test_chongqing_main_batch_defers_drawing_package_without_blocking_qingdan(db_session):
    source = create_chongqing_source(db_session)
    client = FakeChongqingClient(fail_drawing_download=True)
    summaries = discover_chongqing_trading_notices(
        client,
        category_num="014001001",
        notice_type_raw="招标公告",
        channel_id="tender_notice",
        notice_family="tender",
        start_date="2026-06-23",
        end_date="2026-06-23",
    )

    archive = ingest_chongqing_trading_notice(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        summary=summaries[0],
        actor_id="chongqing-main-batch-defers-large-test",
    )

    files = db_session.query(ArchiveFile).filter_by(archive_id=archive.archive_id).all()
    mounted_by_name = {mounted.display_name: mounted for mounted in files}

    assert [call[0] for call in client.calls] == [
        "POST_JSON",
        "GET_JSON",
        "GET_BYTES",
        "GET_BYTES",
        "GET_BYTES",
    ]
    assert mounted_by_name["图纸-中国汽研国七项目.zip"].file_role == "drawing"
    assert mounted_by_name["图纸-中国汽研国七项目.zip"].file_id is None
    assert mounted_by_name["图纸-中国汽研国七项目.zip"].fetch_status == FETCH_STATUS_PENDING
    assert mounted_by_name["图纸-中国汽研国七项目.zip"].metadata_payload["access_status"] == "PENDING_FETCH"
    assert mounted_by_name["工程量清单.pdf"].fetch_status == FETCH_STATUS_FETCHED
    assert mounted_by_name["[50000120260616025050101].cqzf"].fetch_status == FETCH_STATUS_FETCHED
    assert db_session.query(FileAsset).filter_by(file_name="图纸-中国汽研国七项目.zip").one_or_none() is None
    assert db_session.query(FileProcessing).count() == 0


def test_chongqing_channel_runner_records_attachment_timeout_and_continues_without_half_archive(db_session):
    source = create_chongqing_source(db_session)
    client = FakeChongqingClient(fail_first_attachment=True)

    report = run_chongqing_trading_channels(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        channel_ids=["tender_notice"],
        max_pages=1,
        max_items_per_channel=2,
        actor_id="chongqing-download-guard-test",
    )

    archives = db_session.query(Archive).filter_by(domain_type="trading").all()
    audit = db_session.query(AuditLog).filter_by(action="TRADING_ATTACHMENT_DOWNLOAD_FAILED").one()

    assert report["ingested_count"] == 1
    assert report["failed_count"] == 1
    assert report["failed_items"][0]["error_kind"] == "attachment_timeout"
    assert report["failed_items"][0]["source_item_key"] == "e211e7b2-2d6f-44da-988c-bd06bcf2255d"
    assert report["failed_items"][0]["file_name"] == "招标公告.pdf"
    assert [archive.source_item_key for archive in archives] == ["54aafe7b-ad81-4f12-917c-3aa95b5db0ae"]
    assert db_session.query(Archive).filter_by(source_item_key="e211e7b2-2d6f-44da-988c-bd06bcf2255d").count() == 0
    assert audit.error_code == "CHONGQING_ATTACHMENT_DOWNLOAD_TIMEOUT"
    assert audit.after_payload["attachment_url"].startswith("https://ggzydl.cqggzy.com/")
    assert db_session.query(FileProcessing).count() == 0


def test_chongqing_all_failed_run_persists_tombstone_even_without_successful_archive(db_session):
    source = create_chongqing_source(db_session)
    client = FakeChongqingClient(fail_all_attachments=True)

    report = run_chongqing_trading_channels(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        channel_ids=["tender_notice"],
        max_pages=1,
        page_size=1,
        max_items_per_channel=1,
        actor_id="chongqing-all-failed-test",
    )
    db_session.rollback()

    audits = db_session.query(AuditLog).filter_by(action="TRADING_ATTACHMENT_DOWNLOAD_FAILED").all()
    source_item_keys = {audit.after_payload["source_item_key"] for audit in audits}

    assert report["ingested_count"] == 0
    assert report["failed_count"] >= 1
    assert db_session.query(Archive).filter_by(source_item_key="e211e7b2-2d6f-44da-988c-bd06bcf2255d").count() == 0
    assert "e211e7b2-2d6f-44da-988c-bd06bcf2255d" in source_item_keys
    assert {audit.error_code for audit in audits} == {"CHONGQING_ATTACHMENT_DOWNLOAD_TIMEOUT"}
