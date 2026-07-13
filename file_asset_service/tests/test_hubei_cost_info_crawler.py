from app.collection import create_data_source
from app.hubei_cost_info import (
    HUBEI_COST_INFO_PARSER_VERSION,
    HUBEI_JOURNAL_LIST_API_URL,
    hubei_cost_info_source_config,
    ingest_hubei_cost_info_issue,
    list_hubei_cost_info_issues,
)
from app.models import Archive, FileProcessing, IngestEvent
from app.storage import FakeObjectStore


def cell_value(cell):
    return cell["value"]


class FakeHubeiCostInfoClient:
    def __init__(self, *, records=None, bytes_by_url=None):
        self.records = records or []
        self.bytes_by_url = bytes_by_url or {}
        self.posted_json = []
        self.downloaded = []

    def post_json(self, url, payload):
        self.posted_json.append((url, dict(payload)))
        return {
            "code": 200,
            "message": "成功",
            "data": {
                "records": self.records,
                "total": len(self.records),
                "size": payload.get("pageSize"),
                "current": payload.get("pageNum"),
                "pages": 1,
            },
        }

    def get_bytes(self, url):
        self.downloaded.append(url)
        if url in self.bytes_by_url:
            return self.bytes_by_url[url], "application/pdf"
        if url.endswith(".pdf"):
            return b"%PDF hubei issue bytes", "application/pdf"
        raise AssertionError(f"unexpected download: {url}")


def test_hubei_source_config_marks_cost_station_issue_based_guidance_source():
    config = hubei_cost_info_source_config()
    parser = config["parser"]["parsers"][HUBEI_COST_INFO_PARSER_VERSION]
    target = config["coverage_expectation"]["target_regions"][0]

    assert config["stable"]["site_id"] == "cost_info.hb.hbcic.periodical"
    assert config["stable"]["region_code"] == "420000"
    assert config["stable"]["publisher_type"] == "cost_station_public_institution"
    assert config["stable"]["publisher_name"] == "湖北省建设工程标准定额管理总站"
    assert config["stable"]["period_kind"] == "issue_based"
    assert config["price_coordinates"]["price_kind"] == "guidance"
    assert parser["list_api_url"] == HUBEI_JOURNAL_LIST_API_URL
    assert parser["period"]["kind"] == "issue_based"
    assert parser["journal_type"] == 1
    assert target["region_code"] == "420000"
    assert target["requires_city_source"] is False


def test_hubei_list_discovers_public_issue_journal_and_filters_non_main_rows():
    client = FakeHubeiCostInfoClient(
        records=[
            _journal_row(
                1921840662938880,
                "湖北工程造价管理",
                "2026",
                "第2期",
                "BusinessFile/1435137941065781248/CreditFile/202605/2058808850473635840.pdf",
                journal_type=1,
            ),
            _journal_row(
                1889105839883520,
                " 湖北工程造价管理 ",
                "2026",
                "第1期",
                "BusinessFile/1435137941065781248/CreditFile/202604/2042441450132164608.pdf",
                journal_type=1,
            ),
            _journal_row(
                2001,
                "企业价格信息",
                "2026",
                "第1期",
                "BusinessFile/company.pdf",
                journal_type=2,
            ),
            _journal_row(
                2002,
                "湖北工程造价管理",
                "2026",
                "第3期",
                "",
                journal_type=1,
            ),
        ]
    )
    parser = hubei_cost_info_source_config()["parser"]["parsers"][HUBEI_COST_INFO_PARSER_VERSION]

    rows = list_hubei_cost_info_issues(client, parser=parser, page=1, page_size=10)

    assert [row["period"] for row in rows] == ["2026年第2期", "2026年第1期"]
    assert rows[0]["title"] == "2026年第2期《湖北工程造价管理》"
    assert rows[0]["source_item_key"] == "bJournal/1921840662938880"
    assert rows[0]["period_year"] == 2026
    assert rows[0]["period_issue_no"] == 2
    assert rows[0]["publish_date"] == "2026-05-25"
    assert rows[0]["detail_url"] == (
        "http://bzcljg.hbcic.net.cn:8091/gcms-busi/#/materialMessageInquire/periodical?type=self"
    )
    assert rows[0]["attachments"] == [
        {
            "file_name": "2058808850473635840.pdf",
            "url": (
                "http://bzcljg.hbcic.net.cn:8091/basic/o/file/download-img"
                "?remoteFile=BusinessFile/1435137941065781248/CreditFile/202605/2058808850473635840.pdf"
            ),
            "discovery_method": "journal_list_api",
        }
    ]
    assert client.posted_json == [
        (
            HUBEI_JOURNAL_LIST_API_URL,
            {"pushYear": "", "region": "", "pageSize": 10, "pageNum": 1, "journalType": 1},
        )
    ]


def test_hubei_issue_ingests_layer0_archive_with_issue_period_and_no_processing(db_session):
    pdf_url = (
        "http://bzcljg.hbcic.net.cn:8091/basic/o/file/download-img"
        "?remoteFile=BusinessFile/1435137941065781248/CreditFile/202605/2058808850473635840.pdf"
    )
    client = FakeHubeiCostInfoClient(
        records=[
            _journal_row(
                1921840662938880,
                "湖北工程造价管理",
                "2026",
                "第2期",
                "BusinessFile/1435137941065781248/CreditFile/202605/2058808850473635840.pdf",
            )
        ],
        bytes_by_url={pdf_url: b"%PDF hubei 2026 issue 2"},
    )
    config = hubei_cost_info_source_config()
    parser = config["parser"]["parsers"][HUBEI_COST_INFO_PARSER_VERSION]
    source = _create_hubei_source(db_session, config=config)
    row = list_hubei_cost_info_issues(client, parser=parser)[0]

    archive = ingest_hubei_cost_info_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=row,
        actor_id="hubei-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    event = db_session.query(IngestEvent).filter_by(source_item_key="bJournal/1921840662938880").one()

    assert stored.source_item_key == "bJournal/1921840662938880"
    assert event.source_item_key == "bJournal/1921840662938880"
    assert stored.period_kind == "issue_based"
    assert stored.price_kind == "guidance"
    assert cell_value(stored.metadata_payload["period"]) == "2026年第2期"
    assert cell_value(stored.metadata_payload["period_raw"]) == "2026年第2期"
    assert cell_value(stored.metadata_payload["period_start"]) is None
    assert cell_value(stored.metadata_payload["period_year"]) == 2026
    assert cell_value(stored.metadata_payload["period_issue_no"]) == 2
    assert "period_month" not in stored.metadata_payload
    assert cell_value(stored.metadata_payload["publisher_type"]) == "cost_station_public_institution"
    assert cell_value(stored.metadata_payload["publisher_scope"]) == "province"
    assert cell_value(stored.metadata_payload["source_attachment_mode"]) == "pdf_only"
    assert cell_value(stored.metadata_payload["publication_mode"]) == "DIRECT_PDF"
    assert cell_value(stored.metadata_payload["price_source_type"]) == "info_price"
    assert client.downloaded == [pdf_url]
    assert db_session.query(FileProcessing).count() == 0


def _journal_row(row_id, journal_name, year, period, journal_url, *, journal_type=1):
    return {
        "id": row_id,
        "journalName": journal_name,
        "regionName": "湖北省",
        "region": "420000",
        "pushYear": year,
        "pushPeriod": period,
        "journalUrl": journal_url,
        "coverImageUrl": "/img/custom/cover.jpg",
        "createTime": "2026-05-25 15:14:20",
        "needLogin": None,
        "journalType": journal_type,
        "pdfPath": None,
        "pdfTotal": None,
        "pdfImages": None,
    }


def _create_hubei_source(db_session, *, config):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="湖北省建设工程标准定额管理总站-湖北工程造价管理",
        data_domain="cost_info",
        province="湖北省",
        region_code="420000",
        config=config,
    )
