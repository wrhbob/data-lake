from app.archive_rules import metadata_cell
from app.collection import create_data_source
from app.models import Archive, CollectionTask, CrawlLineage, FileProcessing
from app.source_registry import CostInfoAttachment, CostInfoDiscoveredItem, ingest_cost_info_registry_item
from app.storage import FakeObjectStore


def cell_value(cell):
    return cell["value"]


def test_ingest_cost_info_registry_item_reads_config_and_builds_archive(db_session):
    source = create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name="新疆工程造价信息网",
        data_domain="cost_info",
        region_code="650000",
        config={
            "registry_schema_version": "source_registry.v1",
            "stable": {
                "site_id": "cost_info.xj.xjzj",
                "domain_type": "cost_info",
                "region_code": "650000",
                "producer": "新疆维吾尔自治区工程造价总站",
                "publisher": "新疆工程造价信息网",
                "publisher_scope": "province",
                "publisher_region_code": "650000",
                "publisher_name": "新疆工程造价信息网",
                "publisher_type": "cost_station_small_site",
                "entry_url": "https://example.gov.cn/price/",
            },
            "price_coordinates": {
                "price_source_type": "info_price",
                "tax_type": None,
            },
            "source_shape": {
                "publication_mode": "DIRECT_PDF",
                "source_attachment_mode": "mixed",
                "parsability": "structured",
            },
            "parser": {
                "active_parser_version": "xjzj.price-list.v1",
                "parsers": {
                    "xjzj.price-list.v1": {
                        "scope": "list_detail_html_only",
                        "period": {"source": "title", "regex": "(20\\d{2})年(\\d{1,2})月"},
                    }
                },
            },
            "ops": {"enabled": True, "crawl_strategy_tier": "tier1_static_html"},
        },
    )

    archive = ingest_cost_info_registry_item(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        item=CostInfoDiscoveredItem(
            title="2026年8月 新疆建设工程综合价格信息",
            publish_date="2026-08-16",
            detail_url="https://example.gov.cn/price/detail?id=202608&utm_source=wechat",
            discovered_at="2026-08-16T09:00:00+08:00",
            fetched_at="2026-08-16T09:05:00+08:00",
            attachments=[
                CostInfoAttachment(
                    file_name="2026年8月新疆建设工程综合价格信息.pdf",
                    content=b"%PDF-1.4 cost info",
                    url="https://example.gov.cn/files/202608.pdf?spm=track",
                    content_type="application/pdf",
                ),
                CostInfoAttachment(
                    file_name="2026年8月新疆建设工程综合价格信息.xlsx",
                    content=b"xlsx bytes are stored but not parsed",
                    url="https://example.gov.cn/files/202608.xlsx?spm=track",
                    content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            ],
        ),
        actor_id="registry-runner-test",
    )

    task = db_session.query(CollectionTask).filter_by(source_id=source.source_id).one()
    stored = db_session.get(Archive, archive.archive_id)
    lineages = db_session.query(CrawlLineage).filter_by(archive_id=archive.archive_id).all()

    assert task.config_override["parser_version"] == "xjzj.price-list.v1"
    assert task.config_override["config_digest"].startswith("sha256:")
    assert stored.business_key == (
        f"cost_info:{source.source_id}:650000:2026-08:2026年8月 新疆建设工程综合价格信息"
    )
    assert cell_value(stored.metadata_payload["period"]) == "2026-08"
    assert cell_value(stored.metadata_payload["period_start"]) == "2026-08"
    assert cell_value(stored.metadata_payload["coverage_region_code"]) == "650000"
    assert cell_value(stored.metadata_payload["price_source_type"]) == "info_price"
    assert stored.metadata_payload["tax_type"]["value"] is None
    assert stored.metadata_payload["tax_type"]["source_level"] == "crawler"
    assert cell_value(stored.metadata_payload["producer"]) == "新疆维吾尔自治区工程造价总站"
    assert cell_value(stored.metadata_payload["publisher"]) == "新疆工程造价信息网"
    assert cell_value(stored.metadata_payload["publisher_scope"]) == "province"
    assert cell_value(stored.metadata_payload["publisher_region_code"]) == "650000"
    assert cell_value(stored.metadata_payload["publisher_name"]) == "新疆工程造价信息网"
    assert cell_value(stored.metadata_payload["parsability"]) == "structured"
    assert cell_value(stored.metadata_payload["publication_mode"]) == "DIRECT_PDF"
    assert cell_value(stored.metadata_payload["source_attachment_mode"]) == "mixed"
    assert {lineage.parser_version for lineage in lineages} == {"xjzj.price-list.v1"}
    assert db_session.query(FileProcessing).count() == 0


def test_ingest_cost_info_registry_item_preserves_coordinate_metadata_cells(db_session):
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
        config={
            "registry_schema_version": "source_registry.v1",
            "stable": {
                "site_id": "cost_info.sc.mianyang",
                "domain_type": "cost_info",
                "region_code": "510700",
                "producer": "绵阳市住房和城乡建设委员会",
                "publisher": "绵阳市住房和城乡建设委员会",
                "publisher_scope": "city",
                "publisher_region_code": "510700",
                "publisher_name": "绵阳市住房和城乡建设委员会",
                "entry_url": "https://zjw.my.gov.cn/myszjj/c101133/list.shtml",
            },
            "price_coordinates": {
                "price_source_type": "info_price",
                "tax_type": None,
            },
            "source_shape": {
                "publication_mode": "DIRECT_WEB",
                "source_attachment_mode": "xls_attachment",
                "parsability": "structured",
            },
            "parser": {
                "active_parser_version": "mianyang.xls-list.v1",
                "parsers": {
                    "mianyang.xls-list.v1": {
                        "scope": "list_detail_html_only",
                        "period": {"source": "title", "regex": "(20\\d{2})年(\\d{1,2})月"},
                    }
                },
            },
        },
    )

    archive = ingest_cost_info_registry_item(
        db_session,
        FakeObjectStore(),
        source_id=source.source_id,
        item=CostInfoDiscoveredItem(
            title="绵阳市区2026年4月材料价格信息",
            publish_date="2026-05-22",
            detail_url="https://zjw.my.gov.cn/detail.shtml",
            discovered_at="2026-05-22T16:02:00+08:00",
            fetched_at="2026-05-22T16:03:00+08:00",
            attachments=[
                CostInfoAttachment(
                    file_name="绵阳市区2026年04月材料价格信息.xls",
                    content=b"xls bytes are stored but not parsed",
                    url="https://zjw.my.gov.cn/file.xls",
                    content_type="application/vnd.ms-excel",
                ),
            ],
            metadata={
                "coverage_region_code": metadata_cell("510700-市区", source_level="manual", tagged_by="ops:coverage"),
                "tax_type": metadata_cell("tax_exclusive", source_level="manual", tagged_by="ops:site-level"),
            },
        ),
        actor_id="registry-runner-test",
    )

    stored = db_session.get(Archive, archive.archive_id)
    assert stored.metadata_payload["coverage_region_code"]["value"] == "510700-市区"
    assert stored.metadata_payload["coverage_region_code"]["source_level"] == "manual"
    assert stored.metadata_payload["tax_type"]["value"] == "tax_exclusive"
    assert stored.metadata_payload["tax_type"]["source_level"] == "manual"
    assert db_session.query(FileProcessing).count() == 0
