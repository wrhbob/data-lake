from app.collection import create_data_source
from app.jiangxi_cost_info import (
    JIANGXI_COST_INFO_PARSER_VERSION,
    JIANGXI_QUERY_LIST_URL,
    ingest_jiangxi_cost_info_issue,
    jiangxi_cost_info_source_config,
    list_jiangxi_cost_info_issues,
)
from app.models import Archive, FileProcessing, IngestEvent
from app.storage import FakeObjectStore


def cell_value(cell):
    return cell["value"]


class FakeJiangxiCostInfoClient:
    def __init__(self, *, results=None, bytes_by_url=None):
        self.results = results or []
        self.bytes_by_url = bytes_by_url or {}
        self.posted_forms = []
        self.downloaded = []

    def post_form_json(self, url, data):
        self.posted_forms.append((url, dict(data)))
        return {
            "data": {
                "page": data.get("current"),
                "rows": data.get("pageSize"),
                "total": len(self.results),
                "results": self.results,
            }
        }

    def get_bytes(self, url):
        self.downloaded.append(url)
        if url in self.bytes_by_url:
            return self.bytes_by_url[url], "application/pdf"
        if url.endswith(".pdf"):
            return b"%PDF jiangxi cost info bytes", "application/pdf"
        raise AssertionError(f"unexpected download: {url}")


def test_jiangxi_source_config_marks_province_issue_based_guidance_source():
    config = jiangxi_cost_info_source_config()
    parser = config["parser"]["parsers"][JIANGXI_COST_INFO_PARSER_VERSION]
    target = config["coverage_expectation"]["target_regions"][0]

    assert config["stable"]["site_id"] == "cost_info.jx.zjt.material_reference"
    assert config["stable"]["region_code"] == "360000"
    assert config["stable"]["publisher_name"] == "江西省城镇发展服务中心"
    assert config["stable"]["publisher_type"] == "cost_station_public_institution"
    assert config["stable"]["period_kind"] == "issue_based"
    assert config["price_coordinates"]["price_kind"] == "guidance"
    assert parser["list_api_url"] == JIANGXI_QUERY_LIST_URL
    assert parser["period"]["kind"] == "issue_based"
    assert parser["title_regex"] == r"江西省材料价格参考信息(?P<period>(?P<year>20\d{2})年第(?P<issue>\d{1,2})期)"
    assert target["region_code"] == "360000"
    assert target["requires_city_source"] is False


def test_jiangxi_query_list_discovers_issue_pdfs_and_filters_non_main_or_missing_pdf_rows():
    parser = jiangxi_cost_info_source_config()["parser"]["parsers"][JIANGXI_COST_INFO_PARSER_VERSION]
    client = FakeJiangxiCostInfoClient(
        results=[
            _result(
                "2062796646723690496",
                "江西省材料价格参考信息2026年第5期",
                "/doc/ucap/1749973237782081536/document/20260605/iGNhYG0T.pdf",
                "江西省材料价格参考信息2026年第5期.pdf",
                pub_date="2026-06-05 15:19",
            ),
            _result(
                "2052608492608471040",
                "江西省材料价格参考信息2026年第4期",
                "/doc/ucap/1749973237782081536/document/20260508/yNA7wAxA.pdf",
                "江西省材料价格参考信息2026年第4期.pdf",
                pub_date="2026-05-08 12:31",
            ),
            _result(
                "1999",
                "江西省各设区市建筑人工机械设备信息参考价2026年第1期",
                "/doc/ucap/not-main.pdf",
                "人工机械.pdf",
            ),
            _result("2000", "江西省材料价格参考信息2026年第3期", "", ""),
        ]
    )

    rows = list_jiangxi_cost_info_issues(client, parser=parser, page=1, page_size=20)

    assert [row["period"] for row in rows] == ["2026年第5期", "2026年第4期"]
    assert rows[0]["title"] == "江西省材料价格参考信息2026年第5期"
    assert rows[0]["source_item_key"] == "gqcyc/2062796646723690496"
    assert rows[0]["period_year"] == 2026
    assert rows[0]["period_issue_no"] == 5
    assert rows[0]["publish_date"] == "2026-06-05"
    assert rows[0]["detail_url"] == (
        "http://zjt.jiangxi.gov.cn/jxszfhcxjst/gqcyc/pc/content/content_2062796646723690496.html"
    )
    assert rows[0]["attachments"] == [
        {
            "file_name": "江西省材料价格参考信息2026年第5期.pdf",
            "url": "http://zjt.jiangxi.gov.cn/doc/ucap/1749973237782081536/document/20260605/iGNhYG0T.pdf",
            "discovery_method": "query_list_article_files",
        }
    ]
    assert client.posted_forms == [
        (
            JIANGXI_QUERY_LIST_URL,
            {"current": 1, "pageSize": 20, "webSiteCode": "jxszfhcxjst", "channelCode": "gqcyc"},
        )
    ]


def test_jiangxi_issue_ingests_layer0_archive_with_issue_period_and_no_processing(db_session):
    pdf_url = "http://zjt.jiangxi.gov.cn/doc/ucap/1749973237782081536/document/20260605/iGNhYG0T.pdf"
    parser = jiangxi_cost_info_source_config()["parser"]["parsers"][JIANGXI_COST_INFO_PARSER_VERSION]
    client = FakeJiangxiCostInfoClient(
        results=[
            _result(
                "2062796646723690496",
                "江西省材料价格参考信息2026年第5期",
                "/doc/ucap/1749973237782081536/document/20260605/iGNhYG0T.pdf",
                "江西省材料价格参考信息2026年第5期.pdf",
            )
        ],
        bytes_by_url={pdf_url: b"%PDF jiangxi 2026 issue 5"},
    )
    config = jiangxi_cost_info_source_config()
    source = _create_jiangxi_source(db_session, config=config)
    row = list_jiangxi_cost_info_issues(client, parser=parser)[0]

    archive = ingest_jiangxi_cost_info_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=row,
        actor_id="jiangxi-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    event = db_session.query(IngestEvent).filter_by(source_item_key="gqcyc/2062796646723690496").one()

    assert stored.source_item_key == "gqcyc/2062796646723690496"
    assert event.source_item_key == "gqcyc/2062796646723690496"
    assert stored.period_kind == "issue_based"
    assert stored.price_kind == "guidance"
    assert cell_value(stored.metadata_payload["period"]) == "2026年第5期"
    assert cell_value(stored.metadata_payload["period_raw"]) == "2026年第5期"
    assert cell_value(stored.metadata_payload["period_start"]) is None
    assert cell_value(stored.metadata_payload["period_year"]) == 2026
    assert cell_value(stored.metadata_payload["period_issue_no"]) == 5
    assert "period_month" not in stored.metadata_payload
    assert cell_value(stored.metadata_payload["publisher_type"]) == "cost_station_public_institution"
    assert cell_value(stored.metadata_payload["publisher_scope"]) == "province"
    assert cell_value(stored.metadata_payload["source_attachment_mode"]) == "pdf_only"
    assert cell_value(stored.metadata_payload["publication_mode"]) == "DIRECT_PDF"
    assert cell_value(stored.metadata_payload["price_source_type"]) == "info_price"
    assert client.downloaded == [pdf_url]
    assert db_session.query(FileProcessing).count() == 0


def _result(row_id, title, pdf_url, file_name, *, pub_date="2026-06-05 15:19"):
    article_files = []
    if pdf_url:
        article_files.append(
            {
                "fileName": file_name,
                "url": pdf_url,
                "filePath": f"/jxszfhcxjst/gqcyc/{row_id}/file.pdf",
                "domainName": "http://zjt.jiangxi.gov.cn",
                "fileId": f"{row_id}01",
            }
        )
    return {
        "id": row_id,
        "source": {
            "id": row_id,
            "title": title,
            "pubDate": pub_date,
            "contentSource": "江西省城镇发展服务中心",
            "urls": '{"pc":"/jxszfhcxjst/gqcyc/pc/content/content_%s.html"}' % row_id,
            "articleFiles": __import__("json").dumps(article_files, ensure_ascii=False),
        },
    }


def _create_jiangxi_source(db_session, *, config):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="江西省住房和城乡建设厅-材料价格参考信息",
        data_domain="cost_info",
        province="江西省",
        region_code="360000",
        config=config,
    )
