from app.collection import create_data_source
from app.jinan_cost_info import (
    JINAN_COST_INFO_PARSER_VERSION,
    JINAN_ENTRY_URL,
    ingest_jinan_cost_info_issue,
    jinan_cost_info_source_config,
    list_jinan_cost_info_issues,
)
from app.models import Archive, FileProcessing, IngestEvent
from app.storage import FakeObjectStore


def cell_value(cell):
    return cell["value"]


class FakeJinanCostInfoClient:
    def __init__(self, *, last_period_id, periods, downloads_by_period_id, bytes_by_url=None):
        self.last_period_id = last_period_id
        self.periods = periods
        self.downloads_by_period_id = downloads_by_period_id
        self.bytes_by_url = bytes_by_url or {}
        self.get_json_calls = []
        self.post_json_calls = []
        self.downloaded = []

    def get_json(self, url, params=None):
        self.get_json_calls.append((url, dict(params or {})))
        if url.endswith("/build/period/getLastPeriodId"):
            return {"code": 0, "data": self.last_period_id}
        if url.endswith("/build/period/selectPeriodList"):
            return {"code": 0, "data": self.periods}
        raise AssertionError(f"unexpected GET JSON: {url}")

    def post_json(self, url, payload):
        self.post_json_calls.append((url, dict(payload)))
        period_id = int(payload["periodId"])
        return {
            "code": 0,
            "data": {
                "records": self.downloads_by_period_id.get(period_id, []),
                "total": len(self.downloads_by_period_id.get(period_id, [])),
            },
        }

    def get_bytes(self, url):
        self.downloaded.append(url)
        if url in self.bytes_by_url:
            return self.bytes_by_url[url], "application/pdf"
        if url.endswith(".pdf"):
            return b"%PDF jinan monthly issue bytes", "application/pdf"
        raise AssertionError(f"unexpected download: {url}")


def test_jinan_source_config_marks_city_monthly_guidance_official_source():
    config = jinan_cost_info_source_config()
    parser = config["parser"]["parsers"][JINAN_COST_INFO_PARSER_VERSION]
    target = config["coverage_expectation"]["target_regions"][0]

    assert config["stable"]["site_id"] == "cost_info.sd.jinan.zjj.price_info"
    assert config["stable"]["region_code"] == "370100"
    assert config["stable"]["coverage_region_code"] == "370100"
    assert config["stable"]["publisher_scope"] == "city"
    assert config["stable"]["publisher_name"] == "济南市住房和城乡建设局"
    assert config["stable"]["publisher_type"] == "official_housing_urban_rural_development"
    assert config["stable"]["period_kind"] == "monthly"
    assert config["price_coordinates"]["price_kind"] == "guidance"
    assert "最高投标限价" in config["price_coordinates"]["price_kind_basis"]
    assert parser["list_strategy"] == "vue_spa_public_api_periods"
    assert parser["download_strategy"] == "public_download_column_pdf_attachment"
    assert parser["excluded_download_titles"] == ["2020人工材料机械汇总表"]
    assert target["region_code"] == "370100"
    assert target["region_name"] == "济南市"
    assert target["target_level"] == "city"


def test_jinan_list_uses_public_period_api_and_filters_download_column_to_main_journal_pdf():
    client = FakeJinanCostInfoClient(
        last_period_id=811589252355269,
        periods=_jinan_periods(),
        downloads_by_period_id={
            811589252355269: [
                _fixed_2020_summary_record(),
                _jinan_download_record(
                    record_id=819376379319493,
                    period_id=811589252355269,
                    title="2026年第5期济南工程造价信息",
                    attach_url="/all/50485054485450532288000/510e01fd13d6403f9bc750465c81bbc4.pdf",
                    publish_time="2026-06-25",
                ),
            ],
            800251371103429: [
                _jinan_download_record(
                    record_id=809149379650757,
                    period_id=800251371103429,
                    title="2026年第4期济南工程造价信息",
                    attach_url="/all/50485054485350551746360/fffd69c474dc4aafa527dc21f1815852.pdf",
                    publish_time="2026-05-30",
                ),
                _jinan_download_record(
                    record_id=999,
                    period_id=800251371103429,
                    title="2026年第4期济南绿色节能材料价格",
                    attach_url="/all/not-main.pdf",
                    publish_time="2026-05-30",
                ),
            ],
        },
    )
    parser = jinan_cost_info_source_config()["parser"]["parsers"][JINAN_COST_INFO_PARSER_VERSION]

    rows = list_jinan_cost_info_issues(client, parser=parser, max_periods=2)

    assert [row["period"] for row in rows] == ["2026-05", "2026-04"]
    assert rows[0]["title"] == "2026年05月济南工程造价信息"
    assert rows[0]["source_title"] == "2026年第5期济南工程造价信息"
    assert rows[0]["source_item_key"] == "819376379319493"
    assert rows[0]["period_year"] == 2026
    assert rows[0]["period_month"] == 5
    assert rows[0]["publish_date"] == "2026-06-25"
    assert rows[0]["attachments"] == [
        {
            "file_name": "2026年第5期济南工程造价信息.pdf",
            "url": "http://jnxxj.jngczjxh.com:5020/cj/all/50485054485450532288000/510e01fd13d6403f9bc750465c81bbc4.pdf",
            "discovery_method": "download_column_pdf_attachment",
        }
    ]
    assert client.get_json_calls == [
        (
            "http://jnxxj.jngczjxh.com:5020/cj/api/build/period/getLastPeriodId",
            {},
        ),
        (
            "http://jnxxj.jngczjxh.com:5020/cj/api/build/period/selectPeriodList",
            {"periodId": 811589252355269},
        ),
    ]
    assert [call[1]["periodId"] for call in client.post_json_calls] == [811589252355269, 800251371103429]


def test_jinan_monthly_issue_ingests_layer0_pdf_archive_without_processing(db_session):
    pdf_url = "http://jnxxj.jngczjxh.com:5020/cj/all/50485054485450532288000/510e01fd13d6403f9bc750465c81bbc4.pdf"
    client = FakeJinanCostInfoClient(
        last_period_id=811589252355269,
        periods=_jinan_periods()[:1],
        downloads_by_period_id={
            811589252355269: [
                _jinan_download_record(
                    record_id=819376379319493,
                    period_id=811589252355269,
                    title="2026年第5期济南工程造价信息",
                    attach_url="/all/50485054485450532288000/510e01fd13d6403f9bc750465c81bbc4.pdf",
                    publish_time="2026-06-25",
                ),
            ]
        },
        bytes_by_url={pdf_url: b"%PDF jinan 2026-05"},
    )
    config = jinan_cost_info_source_config()
    parser = config["parser"]["parsers"][JINAN_COST_INFO_PARSER_VERSION]
    source = _create_jinan_source(db_session, config=config)
    row = list_jinan_cost_info_issues(client, parser=parser, max_periods=1)[0]

    archive = ingest_jinan_cost_info_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=row,
        actor_id="jinan-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    event = db_session.query(IngestEvent).filter_by(source_item_key=row["source_item_key"]).one()

    assert stored.source_item_key == "819376379319493"
    assert event.source_item_key == stored.source_item_key
    assert stored.region_code == "370100"
    assert stored.period_kind == "monthly"
    assert stored.price_kind == "guidance"
    assert cell_value(stored.metadata_payload["period"]) == "2026-05"
    assert cell_value(stored.metadata_payload["period_raw"]) == "2026年05月济南工程造价信息"
    assert cell_value(stored.metadata_payload["period_start"]) == "2026-05"
    assert cell_value(stored.metadata_payload["period_year"]) == 2026
    assert cell_value(stored.metadata_payload["period_month"]) == 5
    assert cell_value(stored.metadata_payload["source_title"]) == "2026年第5期济南工程造价信息"
    assert cell_value(stored.metadata_payload["publisher_type"]) == "official_housing_urban_rural_development"
    assert cell_value(stored.metadata_payload["publisher_scope"]) == "city"
    assert cell_value(stored.metadata_payload["source_attachment_mode"]) == "pdf_attachment"
    assert cell_value(stored.metadata_payload["publication_mode"]) == "SPA_API_DOWNLOAD_COLUMN_ATTACHMENT"
    assert cell_value(stored.metadata_payload["price_source_type"]) == "info_price"
    assert "最高投标限价" in cell_value(stored.metadata_payload["price_kind_basis"])
    assert cell_value(stored.metadata_payload["attachment_discovery_methods"]) == ["download_column_pdf_attachment"]
    assert client.downloaded == [pdf_url]
    assert db_session.query(FileProcessing).count() == 0


def _jinan_periods():
    return [
        {
            "id": 811589252355269,
            "periodName": "2026年05月材料价格信息",
            "statusName": "已发布",
            "startTime": "2026-06-01 00:00:00",
        },
        {
            "id": 800251371103429,
            "periodName": "2026年04月材料价格信息",
            "statusName": "已发布",
            "startTime": "2026-04-30 00:00:00",
        },
    ]


def _fixed_2020_summary_record():
    return {
        "id": None,
        "title": "2020人工材料机械汇总表",
        "attachName": "2020人工材料机械汇总表",
        "attachUrl": "/all/50485053485350551309770/de95e39d91b243398bd7ec0773c1585a.pdf",
        "publishTime": None,
    }


def _jinan_download_record(*, record_id, period_id, title, attach_url, publish_time):
    return {
        "id": record_id,
        "periodId": period_id,
        "title": title,
        "periodName": "2026年 05期",
        "type": 8,
        "typeName": "PDF文件",
        "year": title[:4],
        "statusName": "已发布",
        "attachName": f"{title}.pdf",
        "attachUrl": attach_url,
        "publishTime": publish_time,
    }


def _create_jinan_source(db_session, *, config):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="济南市住房和城乡建设局-济南工程造价信息",
        data_domain="cost_info",
        province="山东省",
        city="济南市",
        region_code="370100",
        config=config,
    )
