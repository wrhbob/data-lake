from app.catalog_cleanup_apply import apply_cleanup_report
from app.catalog_cleanup_report import build_cleanup_report
from app.models import Archive, ArchiveEvent, ArchiveFile, AuditLog, FileAsset, IngestEvent


def _asset(db_session, *, file_id: str, sha256: str, file_name: str) -> FileAsset:
    asset = FileAsset(
        file_id=file_id,
        tenant_code="platform_public",
        bucket="cost-raw",
        object_key=f"objects/{sha256[:2]}/{file_name}",
        sha256=sha256,
        file_name=file_name,
        file_ext=".pdf",
        mime_type="application/pdf",
        file_size=100,
    )
    db_session.add(asset)
    return asset


def _archive(
    db_session,
    *,
    archive_id: str,
    source_id: str,
    title: str,
    period: str,
    source_url: str,
) -> Archive:
    archive = Archive(
        archive_id=archive_id,
        domain_type="cost_info",
        channel_type="crawler",
        collection_method="legacy_batch_import" if source_id.startswith("legacy") else "auto",
        business_key=f"cost_info:{source_id}:{period}:{title}",
        title=title,
        region_code="110000",
        coverage_period=period,
        source_id=source_id,
        source_url=source_url,
        tenant_code="platform_public",
        visibility_scope="public",
        status="archived",
        metadata_payload={},
        field_sources={},
    )
    db_session.add(archive)
    return archive


def test_apply_cleanup_report_withdraws_duplicate_and_relinks_unique_orphan(db_session):
    canonical_asset = _asset(
        db_session,
        file_id="valid-canonical-file",
        sha256="a" * 64,
        file_name="北京信息价.pdf",
    )
    recovery_asset = _asset(
        db_session,
        file_id="valid-recovery-file",
        sha256="b" * 64,
        file_name="克州信息价.pdf",
    )
    canonical = _archive(
        db_session,
        archive_id="canonical-archive",
        source_id="crawler-source",
        title="2025年12月北京工程造价信息",
        period="2025-12",
        source_url="https://example.test/beijing.pdf",
    )
    duplicate = _archive(
        db_session,
        archive_id="duplicate-archive",
        source_id="legacy-source",
        title="2025年12月北京工程造价信息",
        period="2025-12",
        source_url="https://example.test/beijing.pdf",
    )
    recoverable = _archive(
        db_session,
        archive_id="recoverable-archive",
        source_id="legacy-recovery-source",
        title="2024年10月克州工程造价信息",
        period="2024-10",
        source_url="https://example.test/kezhou.pdf",
    )
    db_session.flush()
    db_session.add_all(
        [
            ArchiveFile(
                archive_id=canonical.archive_id,
                file_id=canonical_asset.file_id,
                file_role="main_document",
                is_primary=True,
            ),
            ArchiveFile(
                archive_id=duplicate.archive_id,
                file_id="missing-duplicate-file",
                file_role="main_document",
                is_primary=True,
            ),
        ]
    )
    recovery_event = IngestEvent(
        event_id="recovery-event",
        file_id=recovery_asset.file_id,
        source_type="batch_import",
        batch_id=f"manifest_rebuild:{recovery_asset.sha256[:12]}",
        source_url="https://example.test/kezhou.pdf",
        original_name=recovery_asset.file_name,
        source_item_key="kezhou",
    )
    db_session.add(recovery_event)
    db_session.add(
        ArchiveFile(
            archive_id=recoverable.archive_id,
            file_id="missing-recovery-file",
            file_role="main_document",
            is_primary=True,
            metadata_payload={"source_event_id": recovery_event.event_id},
        )
    )
    db_session.commit()

    report = build_cleanup_report(db_session)
    assert report["summary"]["action_counts"] == {"relink_orphan": 1, "withdraw_duplicate": 1}

    receipt = apply_cleanup_report(db_session, report, approved_report_sha256="f" * 64)
    db_session.commit()

    db_session.refresh(duplicate)
    assert duplicate.is_withdrawn is True
    assert duplicate.is_current is False
    relinked = db_session.query(ArchiveFile).filter_by(archive_id=recoverable.archive_id).one()
    assert relinked.file_id == recovery_asset.file_id
    assert relinked.linked_by == "catalog_cleanup_apply"
    assert receipt["action_counts"] == {"relink_orphan": 1, "withdraw_duplicate": 1}
    assert db_session.query(ArchiveEvent).filter_by(event_type="ARCHIVE_WITHDRAWN").count() == 1
    assert db_session.query(AuditLog).filter_by(action="ARCHIVE_FILE_RELINKED").count() == 1
