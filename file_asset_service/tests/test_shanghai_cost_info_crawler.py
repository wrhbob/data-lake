from app.collection import create_data_source
from app.models import Archive, FileProcessing, IngestEvent
from app.shanghai_cost_info import (
    SHANGHAI_COST_INFO_PARSER_VERSION,
    SHANGHAI_DOWNLOAD_URL,
    SHANGHAI_INFO_PRICE_CATEGORY_ID,
    SHANGHAI_LIST_URL,
    ingest_shanghai_cost_info_issue,
    list_shanghai_cost_info_issues,
    shanghai_cost_info_source_config,
)
from app.storage import FakeObjectStore


def cell_value(cell):
    return cell["value"]


class FakeShanghaiCostInfoClient:
    def __init__(self, *, rows=None, bytes_by_file_id=None):
        self.rows = rows or []
        self.bytes_by_file_id = bytes_by_file_id or {}
        self.posted_forms = []
        self.downloaded = []

    def post_form_json(self, url, data):
        self.posted_forms.append((url, dict(data)))
        return {
            "total": len(self.rows),
            "rows": self.rows,
            "code": 200,
            "msg": "查询成功",
        }

    def post_form_bytes(self, url, data):
        self.downloaded.append((url, dict(data)))
        file_id = str(data["id"])
        if file_id in self.bytes_by_file_id:
            return self.bytes_by_file_id[file_id], "application/octet-stream;charset=utf-8"
        return b"\xd0\xcf\x11\xe0 shanghai info price xls bytes", "application/octet-stream;charset=utf-8"


def test_shanghai_source_config_marks_monthly_guidance_excel_source():
    config = shanghai_cost_info_source_config()
    parser = config["parser"]["parsers"][SHANGHAI_COST_INFO_PARSER_VERSION]
    target = config["coverage_expectation"]["target_regions"][0]

    assert config["stable"]["site_id"] == "cost_info.sh.ciac.info_price"
    assert config["stable"]["region_code"] == "310000"
    assert config["stable"]["publisher_name"] == "上海市建筑建材业市场管理总站"
    assert config["stable"]["publisher_type"] == "cost_station_public_institution"
    assert config["stable"]["period_kind"] == "monthly"
    assert config["price_coordinates"]["price_kind"] == "guidance"
    assert parser["category_code"] == "003002"
    assert parser["category_id"] == SHANGHAI_INFO_PRICE_CATEGORY_ID
    assert parser["list_api_url"] == SHANGHAI_LIST_URL
    assert parser["attachments"]["allowed"] == ["xls"]
    assert target["region_code"] == "310000"
    assert target["requires_city_source"] is False


def test_shanghai_list_discovers_monthly_info_price_xls_and_filters_other_rows():
    parser = shanghai_cost_info_source_config()["parser"]["parsers"][SHANGHAI_COST_INFO_PARSER_VERSION]
    client = FakeShanghaiCostInfoClient(
        rows=[
            _row("985470040545427456", "2026年06月信息价", "989178665503817728", "2026-06-22"),
            _row("973246707481444352", "2026年05月信息价", "977204136401436672", "2026-05-20"),
            _row("bad-title", "二○二六年六月主要材料行情分析", "111", "2026-06-22", xxlb="003004001"),
            _row("missing-file", "2026年04月信息价", None, "2026-04-20"),
            _row("quarterly-labor", "2026年1季度上海市建筑安装工种劳务信息价", "222", "2026-04-21", xxlb="003003002"),
        ]
    )

    rows = list_shanghai_cost_info_issues(client, parser=parser, page=1, page_size=20)

    assert [row["period"] for row in rows] == ["2026-06", "2026-05"]
    assert rows[0]["title"] == "2026年06月信息价"
    assert rows[0]["source_item_key"] == "003002/985470040545427456"
    assert rows[0]["period_year"] == 2026
    assert rows[0]["period_month"] == 6
    assert rows[0]["publish_date"] == "2026-06-22"
    assert rows[0]["detail_url"] == "https://ciac.zjw.sh.gov.cn/JGBXMGCZJInterWeb/pc/#/hyxxHynr?bmCode=003002"
    assert rows[0]["attachments"] == [
        {
            "file_id": "989178665503817728",
            "file_name": "2026年06月信息价.xls",
            "url": "https://ciac.zjw.sh.gov.cn/JGBXMGCZJInterWeb/interWeb/currently/bdFileDownload?id=989178665503817728",
            "discovery_method": "hyxx_list_wjid",
        }
    ]
    assert client.posted_forms == [
        (
            SHANGHAI_LIST_URL,
            {
                "zdId": SHANGHAI_INFO_PRICE_CATEGORY_ID,
                "pageNum": 1,
                "pageSize": 20,
                "bt": "",
                "fbsjStart": "",
                "fbsjEnd": "",
            },
        )
    ]


def test_shanghai_monthly_excel_ingests_layer0_archive_without_processing(db_session):
    parser = shanghai_cost_info_source_config()["parser"]["parsers"][SHANGHAI_COST_INFO_PARSER_VERSION]
    client = FakeShanghaiCostInfoClient(
        rows=[_row("985470040545427456", "2026年06月信息价", "989178665503817728", "2026-06-22")],
        bytes_by_file_id={
            "989178665503817728": b"\xd0\xcf\x11\xe0 shanghai 2026-06 info price xls",
        },
    )
    config = shanghai_cost_info_source_config()
    source = _create_shanghai_source(db_session, config=config)
    row = list_shanghai_cost_info_issues(client, parser=parser)[0]

    archive = ingest_shanghai_cost_info_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=row,
        actor_id="shanghai-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    event = db_session.query(IngestEvent).filter_by(source_item_key="003002/985470040545427456").one()

    assert stored.source_item_key == "003002/985470040545427456"
    assert event.source_item_key == "003002/985470040545427456"
    assert stored.period_kind == "monthly"
    assert stored.price_kind == "guidance"
    assert cell_value(stored.metadata_payload["period"]) == "2026-06"
    assert cell_value(stored.metadata_payload["period_raw"]) == "2026年06月信息价"
    assert cell_value(stored.metadata_payload["period_start"]) == "2026-06"
    assert cell_value(stored.metadata_payload["period_year"]) == 2026
    assert cell_value(stored.metadata_payload["period_month"]) == 6
    assert cell_value(stored.metadata_payload["publisher_type"]) == "cost_station_public_institution"
    assert cell_value(stored.metadata_payload["publisher_scope"]) == "province"
    assert cell_value(stored.metadata_payload["source_attachment_mode"]) == "excel_only"
    assert cell_value(stored.metadata_payload["publication_mode"]) == "DIRECT_EXCEL_DOWNLOAD"
    assert cell_value(stored.metadata_payload["price_source_type"]) == "info_price"
    assert client.downloaded == [
        (SHANGHAI_DOWNLOAD_URL, {"id": "989178665503817728"}),
    ]
    assert db_session.query(FileProcessing).count() == 0


def _row(row_id, title, file_id, publish_date, *, xxlb="003002"):
    return {
        "id": row_id,
        "bt": title,
        "xxbt": title,
        "fbsj": publish_date,
        "jgzq": f"{title[:4]}-{title[5:7]}-01" if "月信息价" in title else None,
        "xxlb": xxlb,
        "wjid": file_id,
        "ycwj": None,
        "yy": "信息价发布",
    }


def _create_shanghai_source(db_session, *, config):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="上海市建设市场信息服务平台-人工材料机械信息价",
        data_domain="cost_info",
        province="上海市",
        region_code="310000",
        config=config,
    )
