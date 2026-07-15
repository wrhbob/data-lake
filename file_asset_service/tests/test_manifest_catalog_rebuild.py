from __future__ import annotations

import csv

from sqlalchemy import func, select

from app.manifest_catalog_rebuild import SourceMatcher, rebuild_from_manifest
from app.models import Archive, ArchiveFile, DataSource, FileAsset


def test_manifest_rebuild_skips_valid_duplicate_owned_by_another_source(db_session, tmp_path, monkeypatch):
    canonical_source = add_source(
        db_session,
        source_id="canonical-beijing-source",
        name="北京市住房和城乡建设委员会",
    )
    rebuild_source = add_source(
        db_session,
        source_id="ledger-beijing-source",
        name="北京市住房和城乡建设委员会-工程造价信息",
    )
    original_asset = FileAsset(
        tenant_code="platform_public",
        bucket="cost-raw",
        object_key="objects/original-beijing.pdf",
        sha256="a" * 64,
        file_name="2025年12月北京工程造价信息.pdf",
        file_ext=".pdf",
        mime_type="application/pdf",
        file_size=100,
    )
    db_session.add(original_asset)
    db_session.flush()
    original_archive = Archive(
        domain_type="cost_info",
        channel_type="crawler",
        collection_method="auto",
        business_key="cost_info:canonical-beijing-source:110000:2025-12:2025年12月北京工程造价信息",
        title="2025年12月北京工程造价信息",
        region_code="110000",
        source_id=canonical_source.source_id,
        source_url="https://zjw.beijing.gov.cn/files/2025-12.pdf",
        tenant_code="platform_public",
        visibility_scope="public",
        status="collected",
        metadata_payload={"period_start": cell("2025-12")},
        field_sources={},
    )
    db_session.add(original_archive)
    db_session.flush()
    db_session.add(
        ArchiveFile(
            archive_id=original_archive.archive_id,
            file_id=original_asset.file_id,
            file_role="main_document",
            source_url=original_archive.source_url,
            display_name=original_asset.file_name,
            is_primary=True,
        )
    )
    db_session.commit()

    manifest = tmp_path / "parse_manifest.csv"
    write_manifest(
        manifest,
        sha256="b" * 64,
        source_url=original_archive.source_url,
    )
    monkeypatch.setattr(
        SourceMatcher,
        "match",
        lambda self, region_code, name: (rebuild_source.source_id, "region", "platform_public"),
    )

    result = rebuild_from_manifest(db_session, manifest)

    assert result.considered == 1
    assert result.skipped_cross_source_duplicate == 1
    assert result.archives_created == 0
    assert db_session.scalar(select(func.count()).select_from(Archive)) == 1
    assert db_session.scalar(select(func.count()).select_from(FileAsset)) == 1


def add_source(db_session, *, source_id: str, name: str) -> DataSource:
    source = DataSource(
        source_id=source_id,
        source_scope="platform_public",
        tenant_code=None,
        asset_tenant_code="platform_public",
        managed_by="platform",
        source_type="info_price",
        connector_type="source_registry",
        name=name,
        region_code="110000",
        data_domain="cost_info",
        status="active",
        config={"stable": {"site_id": "cost_info.bj.zjw.main"}},
    )
    db_session.add(source)
    db_session.commit()
    return source


def write_manifest(path, *, sha256: str, source_url: str) -> None:
    row = {
        "domain_types": "cost_info",
        "parse_ready": "true",
        "sha256": sha256,
        "object_key": f"objects/{sha256}.pdf",
        "bucket": "cost-raw",
        "byte_size": "100",
        "file_ext": ".pdf",
        "mime_type": "application/pdf",
        "etag": "etag",
        "archive_titles": "2025年12月北京工程造价信息",
        "region_codes": "110000",
        "period_starts": "2025-12",
        "period_ends": "2025-12",
        "period_raws": "2025-12",
        "publish_dates": "2025-12-23",
        "original_names": "2025年12月北京工程造价信息.pdf",
        "file_names": "2025年12月北京工程造价信息.pdf",
        "source_urls": source_url,
        "source_names": "北京市住房和城乡建设委员会-工程造价信息",
    }
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)


def cell(value: str) -> dict[str, str]:
    return {
        "value": value,
        "source_level": "crawler",
        "tagged_by": "test",
        "tagged_at": "2026-07-14T00:00:00+08:00",
    }
