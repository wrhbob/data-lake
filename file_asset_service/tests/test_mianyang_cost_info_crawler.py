from app.collection import create_data_source
from app.models import Archive, ArchiveFile, FileAsset, FileProcessing
from app.mianyang_cost_info import (
    MIANYANG_PARSER_VERSION,
    ingest_mianyang_issue,
    list_mianyang_issues,
    mianyang_cost_info_source_config,
    run_mianyang_incremental,
)
from app.storage import FakeObjectStore


def cell_value(cell):
    return cell["value"]


class FakeMianyangClient:
    def __init__(self, *, list_html: str = "", detail_html: str = ""):
        self.list_html = list_html
        self.detail_html = detail_html
        self.downloaded = []

    def get_text(self, url):
        if url.endswith("list.shtml"):
            return self.list_html
        return self.detail_html

    def get_bytes(self, url):
        self.downloaded.append(url)
        if url.endswith(".xls"):
            return b"XLS original bytes, not parsed", "application/vnd.ms-excel"
        raise AssertionError(f"unexpected download: {url}")


def test_mianyang_source_config_marks_city_center_xls_source_with_unknown_tax_type():
    config = mianyang_cost_info_source_config()
    parser = config["parser"]["parsers"][MIANYANG_PARSER_VERSION]

    assert config["stable"]["site_id"] == "cost_info.sc.mianyang"
    assert config["stable"]["region_code"] == "510700"
    assert config["stable"]["coverage_region_code"] == "510700-市区"
    assert config["stable"]["publisher_scope"] == "city"
    assert config["price_coordinates"] == {"price_source_type": "info_price", "tax_type": None}
    assert config["source_shape"]["source_attachment_mode"] == "xls_attachment"
    assert config["source_shape"]["parsability"] == "structured"
    assert parser["list_url"] == "https://zjw.my.gov.cn/myszjj/c101133/list.shtml"
    assert "source_priority" not in config["stable"]


def test_list_mianyang_issues_parses_static_list_html():
    html = """
    <li>
      <span class="right date">2026-05-22</span>
      <a href="/myszjj/c101133/202605/4896a94807b5400999ac95437fddf5d7.shtml"
         title="绵阳市区2026年4月材料价格信息" class="left">
        <span>绵阳市区2026年4月材料价格信息</span>
      </a>
    </li>
    """
    client = FakeMianyangClient(list_html=html)
    parser = mianyang_cost_info_source_config()["parser"]["parsers"][MIANYANG_PARSER_VERSION]

    rows = list_mianyang_issues(client, parser=parser)

    assert rows == [
        {
            "title": "绵阳市区2026年4月材料价格信息",
            "publish_date": "2026-05-22",
            "detail_url": "https://zjw.my.gov.cn/myszjj/c101133/202605/4896a94807b5400999ac95437fddf5d7.shtml",
        }
    ]


def test_ingest_mianyang_issue_downloads_xls_original_without_processing(db_session):
    detail_html = """
    <p><img src="4896a94807b5400999ac95437fddf5d7/images/xls.gif">
      <a href="4896a94807b5400999ac95437fddf5d7/files/绵阳市区2026年04月材料价格信息.xls" target="_blank">
        绵阳市区2026年04月材料价格信息
      </a>
    </p>
    """
    client = FakeMianyangClient(detail_html=detail_html)
    config = mianyang_cost_info_source_config()
    source = create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="绵阳市住房和城乡建设委员会",
        data_domain="cost_info",
        region_code="510700",
        config=config,
    )

    archive = ingest_mianyang_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row={
            "title": "绵阳市区2026年4月材料价格信息",
            "publish_date": "2026-05-22",
            "detail_url": "https://zjw.my.gov.cn/myszjj/c101133/202605/4896a94807b5400999ac95437fddf5d7.shtml",
        },
        parser=config["parser"]["parsers"][MIANYANG_PARSER_VERSION],
        actor_id="mianyang-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    files = (
        db_session.query(ArchiveFile, FileAsset)
        .join(FileAsset, ArchiveFile.file_id == FileAsset.file_id)
        .filter(ArchiveFile.archive_id == archive.archive_id)
        .all()
    )

    assert stored.business_key == (
        f"cost_info:{source.source_id}:510700:2026-04:绵阳市区2026年4月材料价格信息"
    )
    assert cell_value(stored.metadata_payload["period"]) == "2026-04"
    assert cell_value(stored.metadata_payload["coverage_region_code"]) == "510700-市区"
    assert cell_value(stored.metadata_payload["price_source_type"]) == "info_price"
    assert stored.metadata_payload["tax_type"]["value"] is None
    assert cell_value(stored.metadata_payload["source_attachment_mode"]) == "xls_attachment"
    assert cell_value(stored.metadata_payload["parsability"]) == "structured"
    assert [asset.file_ext for _, asset in files] == [".xls"]
    assert client.downloaded == [
        "https://zjw.my.gov.cn/myszjj/c101133/202605/4896a94807b5400999ac95437fddf5d7/files/绵阳市区2026年04月材料价格信息.xls"
    ]
    assert db_session.query(FileProcessing).count() == 0


def test_run_mianyang_incremental_ingests_latest_city_center_issue(db_session):
    list_html = """
    <li>
      <span class="right date">2026-05-22</span>
      <a href="/myszjj/c101133/202605/4896a94807b5400999ac95437fddf5d7.shtml"
         title="绵阳市区2026年4月材料价格信息" class="left">绵阳市区2026年4月材料价格信息</a>
    </li>
    """
    detail_html = """
    <a href="4896a94807b5400999ac95437fddf5d7/files/绵阳市区2026年04月材料价格信息.xls">
      绵阳市区2026年04月材料价格信息
    </a>
    """
    client = FakeMianyangClient(list_html=list_html, detail_html=detail_html)
    config = mianyang_cost_info_source_config()
    source = create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="绵阳市住房和城乡建设委员会",
        data_domain="cost_info",
        region_code="510700",
        config=config,
    )

    report = run_mianyang_incremental(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        max_items=1,
        as_of_date="2026-06-22",
    )

    assert report["listed_count"] == 1
    assert report["ingested_count"] == 1
    assert report["cursor_source_item_key"].endswith("4896a94807b5400999ac95437fddf5d7.shtml")
    assert report["cursor_publish_date"] == "2026-05-22"
    assert db_session.query(Archive).filter_by(domain_type="cost_info").count() == 1
