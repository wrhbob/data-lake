import json

import pytest

from app.collection import create_data_source
from app.models import ArchiveFile, FileAsset
from app.sichuan_trading import (
    SICHUAN_TRADING_SEARCH_API_URL,
    SichuanNoticeSummary,
    discover_sichuan_trading_notices,
    ingest_sichuan_trading_notice,
    run_sichuan_trading_channels,
    sichuan_trading_source_config,
)
from app.storage import FakeObjectStore
from app.trading_registry import FETCH_STATUS_PENDING


DETAIL_HTML = """
<html><body>
  <h2 class="detailed-title" id="title">德阳市示范工程施工招标公告</h2>
  <p>发布时间：<span id="date">2026-07-08 10:52:14</span></p>
  <div id="newsText">
    <div class="notice-body">
      <p>项目编号：DY-2026-001</p><p>招标人为 德阳建设单位。</p>
      <p>项目概况：本工程包含道路、管网及配套设施建设，现对施工进行公开招标，计划工期为一百八十日历天。</p>
      <p>投标人应具备相应施工资质，并在规定时间内通过公共资源交易平台递交投标文件。</p>
    </div>
    <a href="https://wcf.dyggzy.com:2899/Bid.FileTransfer.Interfaces.IDocumentDownloadManager/Document/ABC123">下载招标文件</a>
  </div>
  <div class="attach_content">
    附件:<a class="attachUrl" href="/WebBuilder/WebbuilderMIS/attach/downloadZtbAttach.jspx?attachGuid=official-file&amp;siteGuid=sc">工程量清单.ZBID</a>
  </div>
  <a class="attachUrl" href="/WebBuilder/WebbuilderMIS/attach/downloadZtbAttach.jspx?attachGuid={{arrGuid}}">{{attFileName}}</a>
</body></html>
"""

SHELL_DETAIL_HTML = """
<html><body>
  <h2 class="detailed-title" id="title">旧公告壳页</h2>
  <p>发布时间：<span id="date">2013-06-18 11:22:00</span></p>
  <div id="newsText">见公告</div>
  <a id="originurl_a" data-value="0" href="javascript:void(0)">原文链接</a>
</body></html>
"""

SHELL_WITH_ORIGIN_DETAIL_HTML = """
<html><body>
  <h2 class="detailed-title" id="title">成都公告壳页</h2>
  <p>发布时间：<span id="date">2026-07-15 10:52:14</span></p>
  <div id="newsText">见公告</div>
  <a id="originurl_a" data-value="https://city.example.gov.cn/notice/real.html" href="javascript:void(0)">原文链接</a>
</body></html>
"""

SEARCH_API_NOTICE_BODY = """
第一章 招标公告。某工程项目已具备招标条件，现对该工程施工进行公开招标。
项目建设地点位于四川省某市，建设规模包括道路、管网及配套设施，计划工期 180 日历天。
投标人应当具备相应资质，并在规定时间内通过公共资源交易平台递交投标文件。
"""


def _record(*, item_id: str, source_code: str, city: str, webdate: str) -> dict:
    return {
        "id": item_id,
        "categorynum": "002001001",
        "titlenew": f"{city}示范工程施工",
        "title": f"{city}示范工程施工",
        "webdate": webdate,
        "tradingsourcevalue": source_code,
        "zhuanzai": f"{city}公共资源交易服务中心",
        "content": "官方公告正文",
        "linkurl": f"/jyxx/002001/002001001/20260708/{item_id}.html",
    }


class FakeSichuanClient:
    def __init__(self, *, detail_html=DETAIL_HTML, detail_html_by_url=None, detail_errors_by_url=None):
        self.calls = []
        self.byte_calls = []
        self.detail_html = detail_html
        self.detail_html_by_url = detail_html_by_url or {}
        self.detail_errors_by_url = detail_errors_by_url or {}

    def post_json(self, url, payload):
        self.calls.append((url, payload))
        source_code = payload["condition"][1]["equal"]
        records = {
            "S003": [_record(item_id="deyang-001", source_code="S003", city="德阳", webdate="2026-07-08 10:52:14")],
            "S004": [_record(item_id="mianyang-001", source_code="S004", city="绵阳", webdate="2026-07-09 10:52:14")],
        }.get(source_code, [])
        return {"result": {"totalcount": len(records), "records": records}}

    def get_text(self, url):
        self.calls.append(("GET_TEXT", url))
        if url in self.detail_errors_by_url:
            raise self.detail_errors_by_url[url]
        return self.detail_html_by_url.get(url, self.detail_html)

    def get_bytes(self, url):
        self.byte_calls.append(url)
        return b"official attachment", "application/octet-stream"


def _source(db_session):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="public_resource_exchange",
        connector_type="source_registry",
        name="四川省公共资源交易信息网（工程建设）",
        data_domain="trading",
        base_url="https://ggzyjy.sc.gov.cn",
        url="https://ggzyjy.sc.gov.cn",
        province="四川",
        city=None,
        region_code="510000",
        frequency="daily",
        config=sichuan_trading_source_config(),
        schedule_policy={"enabled": True},
        status="active",
        created_by="test",
    )


def _cell_value(cell):
    return cell["value"]


def test_sichuan_config_models_every_official_city_source_code_as_a_separate_channel():
    config = sichuan_trading_source_config()
    parser = config["parser"]["parsers"]["scggzy.jsgc.v2"]
    channels = {channel["source_code"]: channel for channel in parser["channels"]}

    assert config["stable"]["site_id"] == "trading.scggzy.jsgc"
    assert len(channels) == 22
    assert channels["S003"]["city_name"] == "德阳市"
    assert channels["S004"]["city_name"] == "绵阳市"
    assert channels["S011"]["city_name"] == "宜宾市"
    assert channels["S013"]["city_name"] == "泸州市"
    assert parser["search_api_url"] == SICHUAN_TRADING_SEARCH_API_URL
    assert parser["sort"] == {"ordernum": "0", "webdate": "0"}
    assert parser["incremental_lookback_days"] == 10
    assert config["ops"]["verification"]["channel_ids"] == [
        "tender_notice_s003",
        "tender_notice_s004",
        "tender_notice_s011",
        "tender_notice_s013",
    ]


def test_sichuan_discovery_posts_city_source_code_and_uses_offset_pagination():
    client = FakeSichuanClient()

    summaries = discover_sichuan_trading_notices(
        client,
        category_num="002001001",
        source_code="S004",
        notice_type_raw="招标公告",
        channel_id="tender_notice_s004",
        notice_family="tender",
        offset=12,
        page_size=50,
        start_date="2026-07-06",
        end_date="2026-07-15",
    )

    assert len(summaries) == 1
    assert summaries[0].source_item_key == "mianyang-001"
    assert summaries[0].city_name == "绵阳市"
    assert summaries[0].city_region_code == "510700"
    assert summaries[0].detail_url.endswith("/mianyang-001.html")
    payload = client.calls[0][1]
    assert client.calls[0][0] == SICHUAN_TRADING_SEARCH_API_URL
    assert payload["pn"] == 12
    assert payload["rn"] == 12
    assert json.loads(payload["sort"]) == {"ordernum": "0", "webdate": "0"}
    assert payload["condition"][0]["equal"] == "002001001"
    assert payload["condition"][1] == {
        "fieldName": "tradingsourcevalue",
        "equal": "S004",
        "notEqual": None,
        "equalList": None,
        "notEqualList": None,
    }
    assert payload["time"][0]["startTime"] == "2026-07-06 00:00:00"


def test_sichuan_ingest_snapshots_notice_and_mounts_public_attachment_links_pending(db_session):
    source = _source(db_session)
    client = FakeSichuanClient()
    summary = SichuanNoticeSummary(
        source_item_key="deyang-001",
        detail_url="https://ggzyjy.sc.gov.cn/jyxx/002001/002001001/20260708/deyang-001.html",
        title="德阳示范工程施工",
        publish_date="2026-07-08",
        publish_time="2026-07-08 10:52:14",
        category_num="002001001",
        source_code="S003",
        city_name="德阳市",
        city_region_code="510600",
        exchange_name="德阳市公共资源交易服务中心",
        content="官方公告正文",
        notice_type_raw="招标公告",
        channel_id="tender_notice_s003",
        notice_family="tender",
    )

    archive = ingest_sichuan_trading_notice(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        summary=summary,
    )
    files = db_session.query(ArchiveFile).filter_by(archive_id=archive.archive_id).all()
    files_by_name = {file.display_name: file for file in files}

    assert archive.business_key == f"trading:{source.source_id}:notice:deyang-001"
    assert archive.title == "德阳市示范工程施工招标公告"
    assert archive.region_code == "510600"
    assert _cell_value(archive.metadata_payload["trading_source_code"]) == "S003"
    assert _cell_value(archive.metadata_payload["city_region_code"]) == "510600"
    assert len(files) == 3
    assert files_by_name["deyang-001.html"].file_role == "web_snapshot"
    assert files_by_name["工程量清单.ZBID"].file_role == "qingdan_package"
    assert files_by_name["工程量清单.ZBID"].fetch_status == FETCH_STATUS_PENDING
    assert files_by_name["下载招标文件"].file_role == "tender_doc"
    assert files_by_name["下载招标文件"].fetch_status == FETCH_STATUS_PENDING
    assert client.byte_calls == []


def test_sichuan_rejects_legacy_shell_without_notice_body(db_session):
    source = _source(db_session)
    summary = SichuanNoticeSummary(
        source_item_key="legacy-shell",
        detail_url="https://ggzyjy.sc.gov.cn/jyxx/002001/002001001/20130618/legacy-shell.html",
        title="旧公告壳页",
        publish_date="2013-06-18",
        publish_time="2013-06-18 11:22:00",
        category_num="002001001",
        source_code="S004",
        city_name="绵阳市",
        city_region_code="510700",
        exchange_name="绵阳市公共资源交易服务中心",
        content="见公告",
        notice_type_raw="招标公告",
        channel_id="tender_notice_s004",
        notice_family="tender",
    )

    with pytest.raises(ValueError, match="SICHUAN_TRADING_NOTICE_BODY_UNAVAILABLE"):
        ingest_sichuan_trading_notice(
            db_session,
            FakeObjectStore(),
            source_id=source.source_id,
            client=FakeSichuanClient(detail_html=SHELL_DETAIL_HTML),
            summary=summary,
        )

    assert db_session.query(ArchiveFile).count() == 0


def test_sichuan_uses_official_search_api_body_when_detail_is_only_a_shell(db_session):
    source = _source(db_session)
    summary = SichuanNoticeSummary(
        source_item_key="search-api-content",
        detail_url="https://ggzyjy.sc.gov.cn/jyxx/002001/002001001/20260715/search-api-content.html",
        title="绵阳市示范工程施工招标公告",
        publish_date="2026-07-15",
        publish_time="2026-07-15 10:52:14",
        category_num="002001001",
        source_code="S004",
        city_name="绵阳市",
        city_region_code="510700",
        exchange_name="绵阳市公共资源交易服务中心",
        content=SEARCH_API_NOTICE_BODY,
        notice_type_raw="招标公告",
        channel_id="tender_notice_s004",
        notice_family="tender",
    )
    storage = FakeObjectStore()

    archive = ingest_sichuan_trading_notice(
        db_session,
        storage,
        source_id=source.source_id,
        client=FakeSichuanClient(detail_html=SHELL_DETAIL_HTML),
        summary=summary,
    )

    assert _cell_value(archive.metadata_payload["detail_resolution"]) == "official_search_api_content"
    mounted = db_session.query(ArchiveFile).filter_by(archive_id=archive.archive_id).one()
    asset = db_session.get(FileAsset, mounted.file_id)
    assert asset is not None
    assert "某工程项目已具备招标条件" in storage.get_object(asset.bucket, asset.object_key).decode("utf-8")


def test_sichuan_uses_official_search_api_body_when_city_origin_tls_fails(db_session):
    source = _source(db_session)
    summary = SichuanNoticeSummary(
        source_item_key="search-api-after-origin-tls-error",
        detail_url="https://ggzyjy.sc.gov.cn/jyxx/002001/002001001/20260715/search-api-after-origin-tls-error.html",
        title="成都市示范工程施工招标公告",
        publish_date="2026-07-15",
        publish_time="2026-07-15 10:52:14",
        category_num="002001001",
        source_code="S002",
        city_name="成都市",
        city_region_code="510100",
        exchange_name="成都市公共资源交易服务中心",
        content=SEARCH_API_NOTICE_BODY,
        notice_type_raw="招标公告",
        channel_id="tender_notice_s002",
        notice_family="tender",
    )
    city_origin_url = "https://city.example.gov.cn/notice/real.html"

    archive = ingest_sichuan_trading_notice(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=FakeSichuanClient(
            detail_html_by_url={summary.detail_url: SHELL_WITH_ORIGIN_DETAIL_HTML},
            detail_errors_by_url={city_origin_url: OSError("TLS handshake failed")},
        ),
        summary=summary,
    )

    assert _cell_value(archive.metadata_payload["detail_resolution"]) == "official_search_api_content_after_detail_fetch_error"
    assert "TLS handshake failed" in _cell_value(archive.metadata_payload["detail_fetch_error"])


def test_sichuan_runner_keeps_city_channels_independent(db_session):
    source = _source(db_session)
    client = FakeSichuanClient()

    report = run_sichuan_trading_channels(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        channel_ids=["tender_notice_s003", "tender_notice_s004"],
        max_pages=1,
        page_size=1,
        max_items_per_channel=1,
        as_of_date="2026-07-15",
    )

    assert report["ingested_count"] == 2
    assert [channel["channel_id"] for channel in report["channels"]] == [
        "tender_notice_s003",
        "tender_notice_s004",
    ]
    source_codes = [call[1]["condition"][1]["equal"] for call in client.calls if call[0] != "GET_TEXT"]
    assert source_codes == ["S003", "S004"]
    request_windows = [call[1]["time"][0] for call in client.calls if call[0] != "GET_TEXT"]
    assert request_windows == [
        {"fieldName": "webdate", "startTime": "2026-07-05 00:00:00", "endTime": "2026-07-15 23:59:59"},
        {"fieldName": "webdate", "startTime": "2026-07-05 00:00:00", "endTime": "2026-07-15 23:59:59"},
    ]
