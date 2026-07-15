from app.collection import create_data_source
from app.guizhou_cost_info import (
    GUIZHOU_COST_INFO_PARSER_VERSION,
    GUIZHOU_COST_INFO_POLICY_GUID,
    guizhou_cost_info_source_config,
    ingest_guizhou_cost_info_issue,
    list_guizhou_cost_info_issues,
)
from app.models import Archive, FileProcessing, IngestEvent
from app.storage import FakeObjectStore


def cell_value(cell):
    return cell["value"]


class FakeGuizhouCostInfoClient:
    def __init__(self, *, rows=None, bytes_by_url=None):
        self.rows = rows or []
        self.bytes_by_url = bytes_by_url or {}
        self.posted = []
        self.downloaded = []

    def post_form_json(self, url, data):
        self.posted.append((url, dict(data)))
        return {"Rows": self.rows, "Total": len(self.rows), "FJString": ""}

    def get_bytes(self, url):
        self.downloaded.append(url)
        if url in self.bytes_by_url:
            return self.bytes_by_url[url], "application/pdf"
        if url.endswith(".pdf"):
            return b"%PDF guizhou issue bytes", "application/pdf"
        raise AssertionError(f"unexpected download: {url}")


def test_guizhou_source_config_marks_association_issue_based_guidance_source_with_month_projection():
    config = guizhou_cost_info_source_config()
    parser = config["parser"]["parsers"][GUIZHOU_COST_INFO_PARSER_VERSION]
    target = config["coverage_expectation"]["target_regions"][0]

    assert config["stable"]["site_id"] == "cost_info.gz.gzszj.association"
    assert config["stable"]["region_code"] == "520000"
    assert config["stable"]["publisher_type"] == "industry_association"
    assert config["stable"]["publisher_name"] == "贵州省建设工程造价管理协会"
    assert config["stable"]["period_kind"] == "issue_based"
    assert config["price_coordinates"]["price_kind"] == "guidance"
    assert parser["list_api_url"].endswith("/Home/GetPoliciesListBy")
    assert parser["list_guid"] == GUIZHOU_COST_INFO_POLICY_GUID
    assert parser["period"]["kind"] == "issue_based"
    assert target["region_code"] == "520000"
    assert target["requires_city_source"] is False


def test_guizhou_list_discovery_keeps_main_issue_and_filters_mixed_rows():
    client = FakeGuizhouCostInfoClient(
        rows=[
            _policy_row(1792, "贵州省建设工程造价信息 2026年第5期", "ad08891c/贵州省建设工程造价信息-2026第5期.pdf"),
            _policy_row(1773, "浙江康帕斯流体技术股份有限公司", "873f6e7c/浙江康帕斯流体技术股份有限公司.pdf"),
            _policy_row(1888, "贵州省交通建设工程造价管理信息 2026年第1期", "jt/交通造价.pdf"),
            _policy_row(1889, "关于开展工程造价咨询企业培训的通知", "notice/培训通知.pdf"),
        ]
    )
    parser = guizhou_cost_info_source_config()["parser"]["parsers"][GUIZHOU_COST_INFO_PARSER_VERSION]

    rows = list_guizhou_cost_info_issues(client, parser=parser, page=1, page_size=20)

    assert [row["title"] for row in rows] == ["贵州省建设工程造价信息 2026年第5期"]
    assert rows[0]["source_item_key"] == "PoliciesDetail/1792"
    assert rows[0]["period"] == "2026年第5期"
    assert rows[0]["period_year"] == 2026
    assert rows[0]["period_issue_no"] == 5
    assert rows[0]["period_start"] == "2026-05"
    assert rows[0]["period_month"] == 5
    assert rows[0]["publish_date"] == "2026-06-10"
    assert rows[0]["detail_url"] == "http://www.gzszj.com/Home/PoliciesDetail/1792"
    assert rows[0]["attachments"] == [
        {
            "file_name": "贵州省建设工程造价信息-2026第5期.pdf",
            "url": "http://www.gzszj.com/Upload/File/ad08891c/贵州省建设工程造价信息-2026第5期.pdf",
            "discovery_method": "list_api_attachment",
        }
    ]


def test_guizhou_issue_ingests_layer0_archive_with_issue_period_and_no_processing(db_session):
    pdf_url = "http://www.gzszj.com/Upload/File/ad08891c/贵州省建设工程造价信息-2026第5期.pdf"
    client = FakeGuizhouCostInfoClient(
        rows=[_policy_row(1792, "贵州省建设工程造价信息 2026年第5期", "ad08891c/贵州省建设工程造价信息-2026第5期.pdf")],
        bytes_by_url={pdf_url: b"%PDF guizhou 2026 issue 5"},
    )
    config = guizhou_cost_info_source_config()
    parser = config["parser"]["parsers"][GUIZHOU_COST_INFO_PARSER_VERSION]
    source = _create_guizhou_source(db_session, config=config)
    row = list_guizhou_cost_info_issues(client, parser=parser)[0]

    archive = ingest_guizhou_cost_info_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=row,
        actor_id="guizhou-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    event = db_session.query(IngestEvent).filter_by(source_item_key="PoliciesDetail/1792").one()

    assert stored.source_item_key == "PoliciesDetail/1792"
    assert event.source_item_key == "PoliciesDetail/1792"
    assert stored.period_kind == "issue_based"
    assert stored.price_kind == "guidance"
    assert cell_value(stored.metadata_payload["period"]) == "2026年第5期"
    assert cell_value(stored.metadata_payload["period_raw"]) == "2026年第5期"
    assert cell_value(stored.metadata_payload["period_start"]) == "2026-05"
    assert cell_value(stored.metadata_payload["period_year"]) == 2026
    assert cell_value(stored.metadata_payload["period_issue_no"]) == 5
    assert cell_value(stored.metadata_payload["period_month"]) == 5
    assert cell_value(stored.metadata_payload["publisher_type"]) == "industry_association"
    assert cell_value(stored.metadata_payload["publisher_scope"]) == "province"
    assert cell_value(stored.metadata_payload["source_attachment_mode"]) == "pdf_only"
    assert "issue_based" not in stored.business_key
    assert client.downloaded == [pdf_url]
    assert db_session.query(FileProcessing).count() == 0


def _policy_row(policy_id, title, file_url):
    return {
        "ID": policy_id,
        "Name": title,
        "EntryDate": "/Date(1781053689730)/",
        "MenuType": GUIZHOU_COST_INFO_POLICY_GUID,
        "PoliciesAttachmentDTOS": [
            {
                "ID": policy_id + 1000,
                "Name": file_url.rsplit("/", 1)[-1],
                "FileUrl": file_url,
                "PoliciesID": policy_id,
            }
        ],
    }


def _create_guizhou_source(db_session, *, config):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="贵州省建设工程造价管理协会-造价信息",
        data_domain="cost_info",
        province="贵州省",
        region_code="520000",
        config=config,
    )
