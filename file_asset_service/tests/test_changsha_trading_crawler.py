from app.changsha_trading import (
    CHANGSHA_TRADING_API_URL,
    ChangshaNoticeSummary,
    changsha_trading_source_config,
    discover_changsha_trading_notices,
    ingest_changsha_trading_notice,
)
from app.collection import create_data_source
from app.models import ArchiveFile
from app.storage import FakeObjectStore


SECTION_ID = "78ae5d41-06d5-469b-a42d-779c7a9a23a9"
NOTICE_GUID = "b41f20b5-aa46-4e65-82db-239b661bc909"


class FakeChangshaClient:
    def __init__(self):
        self.json_calls = []
        self.byte_calls = []

    def get_json(self, path, params=None):
        self.json_calls.append((path, dict(params or {})))
        if path == "/constructionTender/listByFile":
            return {
                "success": True,
                "data": {
                    "records": [
                        {
                            "bidSectionId": SECTION_ID,
                            "tenderProjectName": "湘江科学城交易展示中心项目",
                            "noticeSendTime": "2026-07-14 14:51:03",
                            "noticeType": "ZHAOBIAO_NOTICE",
                            "isPublic": "0",
                            "name": "长沙市",
                        },
                        {
                            "bidSectionId": "old-section",
                            "tenderProjectName": "超出范围公告",
                            "noticeSendTime": "2026-07-01 14:51:03",
                            "noticeType": "ZHAOBIAO_NOTICE",
                        },
                        {
                            "bidSectionId": "misclassified-section",
                            "tenderProjectName": "官网误标的流标公告",
                            "noticeSendTime": "2026-07-14 13:51:03",
                            "noticeType": "CHONGXIN_ZHAOBIAO_NOTICE",
                        },
                        {
                            "bidSectionId": "change-section",
                            "tenderProjectName": "澄清公告",
                            "noticeSendTime": "2026-07-14 14:51:03",
                            "noticeType": "CHENGQING_NOTICE",
                        },
                    ]
                },
            }
        if path == "/constructionNotice/getBySectionId":
            return {
                "data": {
                    "noticeList": [
                        {
                            "id": NOTICE_GUID + SECTION_ID,
                            "constructionSectionId": SECTION_ID,
                            "bulletinType": "ZHAOBIAO_NOTICE",
                            "noticeName": "湘江科学城交易展示中心项目招标公告",
                            "noticeSendTime": "2026-07-14 14:51:03",
                            "noticeContent": "<div><p>公告正文</p></div>",
                        }
                    ]
                }
            }
        if path == "/constructionTender/getBySectionId":
            return {
                "data": {
                    "constructionTender": {
                        "tenderProjectCode": "HNCS-202607-001",
                        "tenderProjectName": "湘江科学城交易展示中心项目",
                        "tenderProjectType": "住建工程",
                        "tendererName": "长沙建设单位",
                        "tenderAgencyName": "长沙招标代理",
                    },
                    "constructionProject": {"projectCode": "CS-2026-001", "regionCode": "湖南省·长沙市"},
                    "constructionSectionList": [
                        {"id": SECTION_ID, "bidSectionNo": "HNCS-202607-00101", "tenderControlPrice": 100.5}
                    ],
                }
            }
        if path == "/attach/proxy/ListFile":
            assert params == {"gonggaoguid": NOTICE_GUID}
            return {
                "data": [
                    {
                        "AttachGuid": "6c1b6ab6-340e-491b-91dd-a721eac1a13c",
                        "attachfilename": "任务书.pdf",
                        "clienttype": "HNFile001",
                    }
                ]
            }
        raise AssertionError(f"unexpected request: {path} {params}")

    def get_bytes(self, url):
        self.byte_calls.append(url)
        return b"%PDF-1.7 fake official attachment", "application/pdf"


def _source(db_session):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="public_resource_exchange",
        connector_type="source_registry",
        name="长沙市公共资源交易服务平台",
        data_domain="trading",
        base_url="https://changsha.hnsggzy.com",
        url="https://changsha.hnsggzy.com",
        province="湖南",
        city="长沙",
        region_code="430100",
        frequency="daily",
        config=changsha_trading_source_config(),
        schedule_policy={"enabled": True},
        status="active",
        created_by="test",
    )


def test_changsha_config_records_public_api_and_local_history_boundary():
    config = changsha_trading_source_config()
    parser = config["parser"]["parsers"]["changsha.hnsggzy.jsgc.v1"]

    assert config["stable"]["site_id"] == "trading.changsha.hnsggzy.jsgc"
    assert parser["list_api"] == "/constructionTender/listByFile"
    assert parser["history_boundary"]["mode"] == "local_publish_date_window"
    assert parser["channels"][0]["notice_type_codes"] == ["ZHAOBIAO_NOTICE"]


def test_changsha_discovery_uses_public_page_api_and_filters_window_locally():
    client = FakeChangshaClient()

    summaries = discover_changsha_trading_notices(
        client,
        notice_type_codes=("ZHAOBIAO_NOTICE",),
        notice_type_raw="招标公告",
        channel_id="tender_notice",
        notice_family="tender",
        page=2,
        page_size=200,
        start_date="2026-07-06",
        end_date="2026-07-15",
    )

    assert len(summaries) == 1
    assert summaries[0].source_item_key == f"{SECTION_ID}|ZHAOBIAO_NOTICE|2026-07-14 14:51:03"
    assert client.json_calls == [
        (
            "/constructionTender/listByFile",
            {"current": 2, "size": 100, "regionCode": "430100", "tenderProjectType": "CONSTRUCTION"},
        )
    ]


def test_changsha_ingest_wraps_notice_html_and_downloads_private_public_attachment(db_session):
    source = _source(db_session)
    client = FakeChangshaClient()
    storage = FakeObjectStore()
    summary = ChangshaNoticeSummary(
        source_item_key=f"{SECTION_ID}|ZHAOBIAO_NOTICE|2026-07-14 14:51:03",
        bid_section_id=SECTION_ID,
        title="湘江科学城交易展示中心项目",
        publish_date="2026-07-14",
        publish_time="2026-07-14 14:51:03",
        notice_type_code="ZHAOBIAO_NOTICE",
        notice_type_raw="招标公告",
        channel_id="tender_notice",
        notice_family="tender",
        is_public="0",
        region_raw="长沙市",
        resource_state=None,
    )

    archive = ingest_changsha_trading_notice(
        db_session,
        storage,
        source_id=source.source_id,
        client=client,
        summary=summary,
    )

    files = db_session.query(ArchiveFile).filter(ArchiveFile.archive_id == archive.archive_id).all()
    assert len(files) == 2
    assert archive.title == "湘江科学城交易展示中心项目招标公告"
    assert archive.business_key.endswith("|zhaobiao_notice|2026-07-14 14:51:03")
    assert any("attachGuid=6c1b6ab6-340e-491b-91dd-a721eac1a13c" in url for url in client.byte_calls)
    assert any("isPublic=0" in url for url in client.byte_calls)
    assert all(url.startswith(CHANGSHA_TRADING_API_URL) for url in client.byte_calls)
