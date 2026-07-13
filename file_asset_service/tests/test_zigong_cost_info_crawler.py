from app.collection import create_data_source
from app.models import Archive, ArchiveFile, FileAsset, FileProcessing
from app.storage import FakeObjectStore
from app.zigong_cost_info import (
    ZIGONG_PARSER_VERSION,
    ingest_zigong_issue,
    list_zigong_issues,
    run_zigong_incremental,
    zigong_cost_info_source_config,
)


def cell_value(cell):
    return cell["value"]


class FakeZigongClient:
    def __init__(self, *, list_html: str = ""):
        self.list_html = list_html
        self.downloaded = []

    def get_text(self, url):
        return self.list_html

    def get_bytes(self, url):
        self.downloaded.append(url)
        if url.endswith(".zip"):
            return b"ZIP image package bytes, not unzipped", "application/zip"
        if url.endswith(".rar"):
            return b"RAR image package bytes, not unpacked", "application/vnd.rar"
        raise AssertionError(f"unexpected download: {url}")


def zigong_list_html():
    return r'''
    <script>
      var listData = {
        articleList: [
          {
            "title":"材料价格信息（2026年5月）",
            "pubDate":"2026-05-25 11:06",
            "urls":"{\"pc\":\"/zgsrmzf/c00145/pc/content/content_2058746804822818816.html\"}",
            "contentSource":"自贡市住房和城乡建设局",
            "articleFiles":[
              {
                "fileName":"材料信息价2026年第5期（电子版）.zip",
                "domainName":"https://www.zg.gov.cn",
                "filePath":"/zgsrmzf/c00145/2058746804822818816/2FdILMqd.zip",
                "fileId":"2058746566540996608"
              }
            ],
            "id":"2058746804822818816"
          },
          {
            "title":"价格走势图（2026年5月）",
            "pubDate":"2026-05-25 11:06",
            "urls":"{\"pc\":\"/zgsrmzf/c00145/pc/content/content_curve.html\"}",
            "articleFiles":[
              {
                "fileName":"价格走势.zip",
                "domainName":"https://www.zg.gov.cn",
                "filePath":"/zgsrmzf/c00145/2058746804822818816/curve.zip",
                "fileId":"curve"
              }
            ],
            "id":"curve"
          }
        ]
      }
    </script>
    '''


def test_zigong_source_config_marks_zip_package_image_based_source():
    config = zigong_cost_info_source_config()
    parser = config["parser"]["parsers"][ZIGONG_PARSER_VERSION]

    assert config["stable"]["site_id"] == "cost_info.sc.zigong"
    assert config["stable"]["region_code"] == "510300"
    assert config["stable"]["coverage_region_code"] == "510300"
    assert config["stable"]["publisher_scope"] == "city"
    assert config["price_coordinates"] == {"price_source_type": "info_price", "tax_type": None}
    assert config["source_shape"]["source_attachment_mode"] == "zip_package"
    assert config["source_shape"]["parsability"] == "image_based"
    assert parser["list_url"] == "https://www.zg.gov.cn/zgsrmzf/c00145/pc/list.html"
    assert "source_priority" not in config["stable"]


def test_list_zigong_issues_reads_embedded_article_files_and_skips_curve_only_rows():
    client = FakeZigongClient(list_html=zigong_list_html())
    parser = zigong_cost_info_source_config()["parser"]["parsers"][ZIGONG_PARSER_VERSION]

    rows = list_zigong_issues(client, parser=parser)

    assert rows == [
        {
            "title": "材料价格信息（2026年5月）",
            "publish_date": "2026-05-25",
            "detail_url": "https://www.zg.gov.cn/zgsrmzf/c00145/pc/content/content_2058746804822818816.html",
            "source_item_id": "2058746804822818816",
            "attachments": [
                {
                    "file_name": "材料信息价2026年第5期（电子版）.zip",
                    "url": "https://www.zg.gov.cn/zgsrmzf/c00145/2058746804822818816/2FdILMqd.zip",
                    "file_id": "2058746566540996608",
                    "total_size": None,
                }
            ],
        }
    ]


def test_ingest_zigong_issue_stores_zip_package_without_processing_or_ocr(db_session):
    client = FakeZigongClient()
    config = zigong_cost_info_source_config()
    source = create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="自贡市人民政府-材料价格",
        data_domain="cost_info",
        region_code="510300",
        config=config,
    )

    archive = ingest_zigong_issue(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        row=list_zigong_issues(FakeZigongClient(list_html=zigong_list_html()), parser=config["parser"]["parsers"][ZIGONG_PARSER_VERSION])[0],
        parser=config["parser"]["parsers"][ZIGONG_PARSER_VERSION],
        actor_id="zigong-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    files = (
        db_session.query(ArchiveFile, FileAsset)
        .join(FileAsset, ArchiveFile.file_id == FileAsset.file_id)
        .filter(ArchiveFile.archive_id == archive.archive_id)
        .all()
    )

    assert stored.business_key == f"cost_info:{source.source_id}:510300:2026-05:材料价格信息 2026年5月"
    assert cell_value(stored.metadata_payload["period"]) == "2026-05"
    assert cell_value(stored.metadata_payload["coverage_region_code"]) == "510300"
    assert stored.metadata_payload["tax_type"]["value"] is None
    assert cell_value(stored.metadata_payload["source_attachment_mode"]) == "zip_package"
    assert cell_value(stored.metadata_payload["parsability"]) == "image_based"
    assert cell_value(stored.metadata_payload["opaque_package"]) is True
    assert [(mounted.file_role, asset.file_ext) for mounted, asset in files] == [("zip_package", ".zip")]
    assert client.downloaded == ["https://www.zg.gov.cn/zgsrmzf/c00145/2058746804822818816/2FdILMqd.zip"]
    assert db_session.query(FileProcessing).count() == 0


def test_run_zigong_incremental_ingests_latest_zip_package(db_session):
    client = FakeZigongClient(list_html=zigong_list_html())
    config = zigong_cost_info_source_config()
    source = create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="自贡市人民政府-材料价格",
        data_domain="cost_info",
        region_code="510300",
        config=config,
    )

    report = run_zigong_incremental(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        client=client,
        max_items=1,
        as_of_date="2026-06-22",
    )

    assert report["listed_count"] == 1
    assert report["ingested_count"] == 1
    assert report["cursor_source_item_key"] == "2058746804822818816"
    assert report["cursor_publish_date"] == "2026-05-25"
    assert db_session.query(Archive).filter_by(domain_type="cost_info").count() == 1
