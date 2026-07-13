from __future__ import annotations

from pathlib import Path

from app.collection import create_data_source
from app.models import ArchiveFile, CrawlLineage, FileProcessing
from app.quota_registry import QuotaDiscoveredFile, ingest_quota_file, quota_source_config
from app.storage import FakeObjectStore


QUOTA_DB_CANDIDATES = [
    Path("/Users/tms/Desktop/林草/丽江市营造林工程消耗量定额.pdf"),
    Path("/Users/tms/Desktop/0506/0528/0528/raw/丽江市营造林工程消耗量定额.pdf"),
]
BILL_STANDARD_CANDIDATES = [
    Path("/Users/tms/Desktop/cloudSchool/kpiEngineV3/tool/24qdImport/2024建设工程工程量清单计价规范.xlsx"),
    Path("/Users/tms/Desktop/cloudSchool/src/kpiEngineV3/tool/24qdImport/2024建设工程工程量清单计价规范.xlsx"),
]


def _existing_file(candidates: list[Path], label: str) -> Path:
    for path in candidates:
        if path.exists() and path.is_file():
            return path
    raise AssertionError(f"REAL_QUOTA_SAMPLE_MISSING: {label}: {[str(path) for path in candidates]}")


def _cell_value(cell: dict) -> object:
    return cell["value"]


def _create_quota_source(db_session):
    return create_data_source(
        db_session,
        source_scope="platform_public",
        tenant_code=None,
        managed_by="platform",
        source_type="quota_registry",
        connector_type="source_registry",
        name="本地清单定额样本源",
        data_domain="quota",
        region_code="530700",
        config=quota_source_config(
            site_id="quota.local.samples",
            publisher_name="本地清单定额样本源",
            entry_url="file:///local/quota-samples",
            region_code="530700",
            parser_version="quota.local.samples.v1",
        ),
    )


def test_quota_thin_adapter_ingests_quota_db_and_bill_standard_without_parsing(db_session):
    source = _create_quota_source(db_session)
    storage = FakeObjectStore()
    quota_db_path = _existing_file(QUOTA_DB_CANDIDATES, "丽江市营造林工程消耗量定额.pdf")
    bill_standard_path = _existing_file(BILL_STANDARD_CANDIDATES, "2024建设工程工程量清单计价规范.xlsx")

    quota_archive = ingest_quota_file(
        db_session,
        storage,
        source_id=source.source_id,
        item=QuotaDiscoveredFile(
            source_item_key="quota-db-lijiang-afforestation",
            title="丽江市营造林工程消耗量定额",
            file_name=quota_db_path.name,
            content=quota_db_path.read_bytes(),
            source_url=quota_db_path.as_uri(),
            region_code="530700",
            metadata={
                "region_raw": "530700",
                "quota_code_raw": "LJ-YZL",
                "version": "2018",
                "effective_date": "2018-01-01",
                "specialty_raw": "营造林",
            },
            discovered_at="2026-06-21T09:00:00+08:00",
            fetched_at="2026-06-21T09:01:00+08:00",
        ),
        actor_id="quota-registry-test",
    )
    bill_archive = ingest_quota_file(
        db_session,
        storage,
        source_id=source.source_id,
        item=QuotaDiscoveredFile(
            source_item_key="bill-standard-gb50500-2024",
            title="2024建设工程工程量清单计价规范",
            file_name=bill_standard_path.name,
            content=bill_standard_path.read_bytes(),
            source_url=bill_standard_path.as_uri(),
            metadata={"standard_no": "GB50500", "version": "2024"},
            discovered_at="2026-06-21T09:10:00+08:00",
            fetched_at="2026-06-21T09:11:00+08:00",
        ),
        actor_id="quota-registry-test",
    )

    db_session.refresh(quota_archive)
    db_session.refresh(bill_archive)
    quota_file = db_session.query(ArchiveFile).filter_by(archive_id=quota_archive.archive_id).one()
    bill_file = db_session.query(ArchiveFile).filter_by(archive_id=bill_archive.archive_id).one()

    assert quota_archive.domain_type == "quota"
    assert bill_archive.domain_type == "quota"
    assert quota_archive.channel_type == "batch_import"
    assert bill_archive.channel_type == "batch_import"
    assert quota_archive.collection_method == "legacy_batch_import"
    assert bill_archive.collection_method == "legacy_batch_import"

    assert quota_file.file_role == "quota_db"
    assert bill_file.file_role == "bill_standard"
    assert quota_archive.business_key == "quota:530700:LJ-YZL:2018:2018-01-01"
    assert bill_archive.business_key == "quota:GB50500:2024"

    assert _cell_value(quota_archive.metadata_payload["quota_code_raw"]) == "LJ-YZL"
    assert _cell_value(quota_archive.metadata_payload["specialty_raw"]) == "营造林"
    assert "standard_no" not in quota_archive.metadata_payload

    assert _cell_value(bill_archive.metadata_payload["standard_no"]) == "GB50500"
    assert _cell_value(bill_archive.metadata_payload["version"]) == "2024"
    assert "specialty_raw" not in bill_archive.metadata_payload

    assert quota_archive.field_sources["business_key"]["source_level"] == "manual"
    assert bill_archive.field_sources["business_key"]["source_level"] == "manual"
    assert db_session.query(FileProcessing).count() == 0

    lineages = db_session.query(CrawlLineage).order_by(CrawlLineage.created_at).all()
    assert len(lineages) == 2
    assert {lineage.parser_version for lineage in lineages} == {"quota.local.samples.v1"}
    assert {lineage.source_item_key for lineage in lineages} == {
        "quota-db-lijiang-afforestation",
        "bill-standard-gb50500-2024",
    }
    assert lineages[0].source_metadata["discovered_at"].startswith("2026-06-21T")
