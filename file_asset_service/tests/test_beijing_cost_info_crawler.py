from app.beijing_cost_info import (
    BEIJING_COST_INFO_PARSER_VERSION,
    BEIJING_ENTRY_URL,
    BEIJING_MORE_URL,
    beijing_cost_info_source_config,
    ingest_beijing_cost_info_issue,
    list_beijing_cost_info_issues,
)
from app.collection import create_data_source
from app.info_price_coverage import build_coverage_matrix
from app.models import Archive, ArchiveFile, FileAsset, FileProcessing
from app.storage import FakeObjectStore


def cell_value(cell):
    return cell["value"]


class FakeBeijingCostInfoClient:
    def __init__(self, *, texts=None, bytes_by_url=None):
        self.texts = texts or {}
        self.bytes_by_url = bytes_by_url or {}
        self.downloaded = []

    def get_text(self, url):
        if url in self.texts:
            return self.texts[url]
        raise AssertionError(f"unexpected text fetch: {url}")

    def get_bytes(self, url):
        self.downloaded.append(url)
        if url in self.bytes_by_url:
            return self.bytes_by_url[url], "application/pdf"
        if url.endswith(".pdf"):
            return b"%PDF beijing official monthly issue", "application/pdf"
        raise AssertionError(f"unexpected download: {url}")


def test_beijing_source_config_is_official_province_level_pdf_source():
    config = beijing_cost_info_source_config()
    parser = config["parser"]["parsers"][BEIJING_COST_INFO_PARSER_VERSION]
    target = config["coverage_expectation"]["target_regions"][0]

    assert config["stable"]["site_id"] == "cost_info.bj.zjw.main"
    assert config["stable"]["region_code"] == "110000"
    assert config["stable"]["coverage_region_code"] == "110000"
    assert config["stable"]["publisher_name"] == "北京市住房和城乡建设委员会"
    assert config["stable"]["publisher_scope"] == "province"
    assert config["stable"]["publisher_region_code"] == "110000"
    assert config["stable"]["entry_url"] == BEIJING_ENTRY_URL
    assert config["price_coordinates"]["price_source_type"] == "info_price"
    assert config["price_coordinates"]["price_kind"] == "guidance"
    assert config["price_coordinates"]["tax_type"] is None
    assert config["source_shape"]["publication_mode"] == "DIRECT_PDF"
    assert config["source_shape"]["source_attachment_mode"] == "pdf_only"
    assert config["source_shape"]["parsability"] == "text_pdf"
    assert parser["list_urls"] == [BEIJING_ENTRY_URL, BEIJING_MORE_URL]
    assert parser["min_period"] == "2026-01"
    assert target["region_code"] == "110000"
    assert target["region_name"] == "北京市"
    assert target["target_level"] == "province"
    assert target["requires_city_source"] is False


def test_beijing_list_parser_keeps_main_issue_and_filters_reference_price_rows():
    pdf_url = "https://zjw.beijing.gov.cn/bjjs/resource/cms/article/743943530/744045772/2026062214251496508.pdf"
    jan_pdf_url = "https://zjw.beijing.gov.cn/bjjs/resource/cms/article/10872402/743909782/2026012216363680378.pdf"
    client = FakeBeijingCostInfoClient(
        texts={
            BEIJING_ENTRY_URL: f"""
            <ul>
              <li><a href="{pdf_url}">2026年06月北京工程造价信息</a><span>2026-06-22</span></li>
              <li><a href="/market/202606.pdf">2026年06月北京工程造价信息-市场参考价</a><span>2026-06-22</span></li>
              <li><a href="/factory/202606.pdf">2026年06月北京工程造价信息-厂家参考、绿色建材推广及助推花园城市建设</a><span>2026-06-22</span></li>
              <li><a href="/repair/202606.pdf">2026年06月北京工程造价信息（房屋修缮工程）</a><span>2026-06-22</span></li>
              <li><a href="/association/202606.pdf">2026年06月北京工程造价协会信息价</a><span>2026-06-22</span></li>
            </ul>
            """,
            BEIJING_MORE_URL: f"""
            <ul>
              <li><a href="{jan_pdf_url}">2026年01月北京工程造价信息</a><span>2026-01-22</span></li>
              <li><a href="/old/202512.pdf">2025年12月北京工程造价信息</a><span>2025-12-23</span></li>
            </ul>
            """,
        }
    )
    config = beijing_cost_info_source_config()
    parser = config["parser"]["parsers"][BEIJING_COST_INFO_PARSER_VERSION]

    rows = list_beijing_cost_info_issues(client, parser=parser)

    assert [row["title"] for row in rows] == [
        "2026年06月北京工程造价信息",
        "2026年01月北京工程造价信息",
    ]
    assert rows[0]["period"] == "2026-06"
    assert rows[0]["publish_date"] == "2026-06-22"
    assert rows[0]["detail_url"] == pdf_url
    assert rows[0]["attachments"] == [
        {
            "file_name": "2026年06月北京工程造价信息.pdf",
            "url": pdf_url,
            "discovery_method": "list_direct",
        }
    ]


def test_beijing_direct_pdf_ingests_layer0_archive_without_processing(db_session):
    pdf_url = "https://zjw.beijing.gov.cn/bjjs/resource/cms/article/743943530/744045772/2026062214251496508.pdf"
    client = FakeBeijingCostInfoClient(
        texts={
            BEIJING_ENTRY_URL: f"""
            <ul>
              <li><a href="{pdf_url}">2026年06月北京工程造价信息</a><span>2026-06-22</span></li>
            </ul>
            """,
            BEIJING_MORE_URL: "",
        },
        bytes_by_url={pdf_url: b"%PDF beijing 2026-06 official original bytes"},
    )
    config = beijing_cost_info_source_config()
    parser = config["parser"]["parsers"][BEIJING_COST_INFO_PARSER_VERSION]
    source = _create_beijing_source(db_session, config=config)

    rows = list_beijing_cost_info_issues(client, parser=parser)
    archive = ingest_beijing_cost_info_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=rows[0],
        parser=parser,
        actor_id="beijing-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    files = _archive_files(db_session, archive.archive_id)

    assert stored.business_key == f"cost_info:{source.source_id}:110000:2026-06:2026年06月北京工程造价信息"
    assert stored.price_kind == "guidance"
    assert "price_kind" not in stored.metadata_payload
    assert cell_value(stored.metadata_payload["period"]) == "2026-06"
    assert cell_value(stored.metadata_payload["coverage_region_code"]) == "110000"
    assert cell_value(stored.metadata_payload["price_source_type"]) == "info_price"
    assert stored.metadata_payload["tax_type"]["value"] is None
    assert cell_value(stored.metadata_payload["source_attachment_mode"]) == "pdf_only"
    assert cell_value(stored.metadata_payload["parsability"]) == "text_pdf"
    assert cell_value(stored.metadata_payload["attachment_discovery_methods"]) == ["list_direct"]
    assert [asset.file_ext for _, asset in files] == [".pdf"]
    assert client.downloaded == [pdf_url]
    assert db_session.query(FileProcessing).count() == 0


def test_beijing_archive_marks_110000_period_covered_in_matrix(db_session):
    pdf_url = "https://zjw.beijing.gov.cn/bjjs/resource/cms/article/743943530/744045772/2026062214251496508.pdf"
    client = FakeBeijingCostInfoClient(
        texts={
            BEIJING_ENTRY_URL: f"""
            <ul>
              <li><a href="{pdf_url}">2026年06月北京工程造价信息</a><span>2026-06-22</span></li>
            </ul>
            """,
            BEIJING_MORE_URL: "",
        }
    )
    config = beijing_cost_info_source_config()
    parser = config["parser"]["parsers"][BEIJING_COST_INFO_PARSER_VERSION]
    source = _create_beijing_source(db_session, config=config)
    row = list_beijing_cost_info_issues(client, parser=parser)[0]

    ingest_beijing_cost_info_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=row,
        parser=parser,
        actor_id="beijing-test",
    )

    matrix = build_coverage_matrix(
        db_session,
        start_period="2026-06",
        end_period="2026-06",
        province_code="110000",
    )

    assert len(matrix) == 1
    assert matrix[0].coverage_region_code == "110000"
    assert matrix[0].period == "2026-06"
    assert matrix[0].business_coverage_status == "covered"
    assert matrix[0].source_completeness_status == "province_source_only"
    assert matrix[0].province_source_count == 1
    assert matrix[0].city_source_count == 0


def _create_beijing_source(db_session, *, config):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="北京市住房和城乡建设委员会-北京工程造价信息",
        data_domain="cost_info",
        province="北京市",
        region_code="110000",
        config=config,
    )


def _archive_files(db_session, archive_id):
    return (
        db_session.query(ArchiveFile, FileAsset)
        .join(FileAsset, ArchiveFile.file_id == FileAsset.file_id)
        .filter(ArchiveFile.archive_id == archive_id)
        .all()
    )
