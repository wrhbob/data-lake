"""Apply an explicitly approved catalog cleanup report in one transaction."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.catalog_cleanup_report import build_cleanup_report
from app.database import get_engine
from app.models import Archive, ArchiveEvent, ArchiveFile, AuditLog, FileAsset, Outbox


CONFIRMATION = "APPLY_APPROVED_REPORT"
SUPPORTED_ACTIONS = {"withdraw_duplicate", "relink_orphan"}


class CleanupPreconditionError(RuntimeError):
    """Raised before commit when the approved plan no longer matches the database."""


def report_sha256(raw_report: bytes) -> str:
    return hashlib.sha256(raw_report).hexdigest()


def _validate_approved_report(report: dict[str, object]) -> list[dict[str, object]]:
    if report.get("mode") != "read_only_dry_run" or report.get("database_mutations") != 0:
        raise CleanupPreconditionError("REPORT_IS_NOT_A_READ_ONLY_DRY_RUN")
    actions = report.get("actions")
    if not isinstance(actions, list) or not actions:
        raise CleanupPreconditionError("REPORT_HAS_NO_ACTIONS")
    unsupported = sorted(
        {
            str(item.get("action")) if isinstance(item, dict) else type(item).__name__
            for item in actions
            if not isinstance(item, dict) or item.get("action") not in SUPPORTED_ACTIONS
        }
    )
    if unsupported:
        raise CleanupPreconditionError(f"REPORT_HAS_UNSUPPORTED_ACTIONS: {unsupported}")
    archive_ids = [str(item["archive_id"]) for item in actions]
    if len(archive_ids) != len(set(archive_ids)):
        raise CleanupPreconditionError("REPORT_HAS_DUPLICATE_ARCHIVE_ACTIONS")
    return actions


def _add_event(
    session: Session,
    *,
    archive_id: str,
    event_type: str,
    actor_id: str,
    before_payload: dict[str, object],
    after_payload: dict[str, object],
) -> None:
    event = ArchiveEvent(
        archive_id=archive_id,
        event_type=event_type,
        actor_type="system",
        actor_id=actor_id,
        before_payload=before_payload,
        after_payload=after_payload,
    )
    session.add(event)
    session.flush()
    session.add(Outbox(event_id=event.event_id, idempotency_key=f"archive_event:{event.event_id}"))


def apply_cleanup_report(
    session: Session,
    report: dict[str, object],
    *,
    approved_report_sha256: str,
    actor_id: str = "catalog_cleanup_apply",
    applied_at: datetime | None = None,
) -> dict[str, object]:
    """Validate and apply the report without committing the caller's transaction."""

    actions = _validate_approved_report(report)
    current_report = build_cleanup_report(session)
    if current_report["actions"] != actions:
        raise CleanupPreconditionError("DATABASE_PLAN_DRIFTED_FROM_APPROVED_REPORT")

    action_archive_ids = {str(item["archive_id"]) for item in actions}
    canonical_archive_ids = {
        str(item["canonical_archive_id"])
        for item in actions
        if item["action"] == "withdraw_duplicate"
    }
    all_archive_ids = action_archive_ids | canonical_archive_ids
    archives = {
        archive.archive_id: archive
        for archive in session.scalars(
            select(Archive).where(Archive.archive_id.in_(all_archive_ids)).with_for_update()
        ).all()
    }
    if set(archives) != all_archive_ids:
        raise CleanupPreconditionError("ACTION_OR_CANONICAL_ARCHIVE_DISAPPEARED")

    orphan_references = [
        reference
        for item in actions
        if item["action"] == "relink_orphan"
        for reference in item.get("orphan_references", [])
    ]
    archive_file_ids = {str(reference["archive_file_id"]) for reference in orphan_references}
    mounted_by_id = {
        mounted.archive_file_id: mounted
        for mounted in session.scalars(
            select(ArchiveFile).where(ArchiveFile.archive_file_id.in_(archive_file_ids)).with_for_update()
        ).all()
    }
    if set(mounted_by_id) != archive_file_ids:
        raise CleanupPreconditionError("ORPHAN_REFERENCE_DISAPPEARED")

    candidate_file_ids = {
        str(reference["candidate_file_ids"][0])
        for reference in orphan_references
        if reference.get("candidate_count") == 1
    }
    assets_by_id = {
        asset.file_id: asset
        for asset in session.scalars(
            select(FileAsset).where(FileAsset.file_id.in_(candidate_file_ids)).with_for_update()
        ).all()
    }
    if set(assets_by_id) != candidate_file_ids:
        raise CleanupPreconditionError("RECOVERY_FILE_ASSET_DISAPPEARED")

    now = applied_at or datetime.now(UTC)
    counts: Counter[str] = Counter()
    affected_archive_ids: list[str] = []
    relinked_archive_file_ids: list[str] = []

    for item in actions:
        action = str(item["action"])
        archive_id = str(item["archive_id"])
        archive = archives[archive_id]
        if archive.domain_type != "cost_info" or archive.is_withdrawn:
            raise CleanupPreconditionError(f"ACTION_ARCHIVE_NOT_ACTIVE_COST_INFO: {archive_id}")

        if action == "withdraw_duplicate":
            canonical_id = str(item["canonical_archive_id"])
            canonical = archives[canonical_id]
            if canonical.is_withdrawn or canonical.domain_type != "cost_info":
                raise CleanupPreconditionError(f"CANONICAL_ARCHIVE_NOT_ACTIVE: {canonical_id}")
            if canonical.tenant_code != archive.tenant_code:
                raise CleanupPreconditionError(f"CANONICAL_ARCHIVE_TENANT_MISMATCH: {archive_id}")

            before_payload = {
                "is_current": archive.is_current,
                "is_withdrawn": archive.is_withdrawn,
            }
            archive.is_current = False
            archive.is_withdrawn = True
            archive.updated_at = now
            after_payload = {
                "is_current": False,
                "is_withdrawn": True,
                "canonical_archive_id": canonical_id,
                "approved_report_sha256": approved_report_sha256,
            }
            _add_event(
                session,
                archive_id=archive_id,
                event_type="ARCHIVE_WITHDRAWN",
                actor_id=actor_id,
                before_payload=before_payload,
                after_payload=after_payload,
            )
            session.add(
                AuditLog(
                    actor_type="system",
                    actor_id=actor_id,
                    action="ARCHIVE_WITHDRAWN",
                    target_type="archive",
                    target_id=archive_id,
                    before_payload=before_payload,
                    after_payload=after_payload,
                    occurred_at=now,
                )
            )
            counts[action] += 1
            affected_archive_ids.append(archive_id)
            continue

        references = item.get("orphan_references", [])
        if not references:
            raise CleanupPreconditionError(f"RELINK_ACTION_HAS_NO_REFERENCES: {archive_id}")
        for reference in references:
            candidates = reference.get("candidate_file_ids", [])
            if reference.get("candidate_count") != 1 or len(candidates) != 1:
                raise CleanupPreconditionError(f"RELINK_CANDIDATE_NOT_UNIQUE: {archive_id}")
            archive_file_id = str(reference["archive_file_id"])
            mounted = mounted_by_id[archive_file_id]
            missing_file_id = str(reference["missing_file_id"])
            candidate_file_id = str(candidates[0])
            candidate = assets_by_id[candidate_file_id]
            if mounted.archive_id != archive_id or mounted.file_id != missing_file_id:
                raise CleanupPreconditionError(f"ORPHAN_REFERENCE_CHANGED: {archive_file_id}")
            if candidate.tenant_code != archive.tenant_code:
                raise CleanupPreconditionError(f"RECOVERY_FILE_ASSET_TENANT_MISMATCH: {archive_file_id}")

            before_payload = {
                "archive_file_id": archive_file_id,
                "file_id": mounted.file_id,
                "linked_by": mounted.linked_by,
            }
            mounted.file_id = candidate_file_id
            mounted.linked_by = actor_id
            archive.updated_at = now
            after_payload = {
                "archive_file_id": archive_file_id,
                "file_id": candidate_file_id,
                "linked_by": actor_id,
                "evidence": reference.get("evidence", []),
                "approved_report_sha256": approved_report_sha256,
            }
            _add_event(
                session,
                archive_id=archive_id,
                event_type="ARCHIVE_FILE_ATTACHED",
                actor_id=actor_id,
                before_payload=before_payload,
                after_payload=after_payload,
            )
            session.add(
                AuditLog(
                    actor_type="system",
                    actor_id=actor_id,
                    action="ARCHIVE_FILE_RELINKED",
                    target_type="archive_file",
                    target_id=archive_file_id,
                    before_payload=before_payload,
                    after_payload=after_payload,
                    occurred_at=now,
                )
            )
            counts[action] += 1
            relinked_archive_file_ids.append(archive_file_id)
        affected_archive_ids.append(archive_id)

    session.flush()
    return {
        "applied_at": now.isoformat(),
        "approved_report_sha256": approved_report_sha256,
        "action_counts": dict(sorted(counts.items())),
        "affected_archive_count": len(affected_archive_ids),
        "affected_archive_ids": affected_archive_ids,
        "relinked_archive_file_ids": relinked_archive_file_ids,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply one approved catalog cleanup report atomically.")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--confirm", required=True, choices=[CONFIRMATION])
    parser.add_argument("--actor-id", default="catalog_cleanup_apply")
    args = parser.parse_args()

    raw_report = args.report.read_bytes()
    actual_sha256 = report_sha256(raw_report)
    if actual_sha256 != args.expected_sha256:
        raise CleanupPreconditionError(
            f"REPORT_SHA256_MISMATCH: expected={args.expected_sha256} actual={actual_sha256}"
        )
    report = json.loads(raw_report)

    engine = get_engine()
    with engine.connect().execution_options(isolation_level="SERIALIZABLE") as connection:
        with Session(bind=connection, future=True, expire_on_commit=False) as session:
            with session.begin():
                receipt = apply_cleanup_report(
                    session,
                    report,
                    approved_report_sha256=actual_sha256,
                    actor_id=args.actor_id,
                )

    receipt.update(
        {
            "mode": "committed",
            "source_report": str(args.report),
            "transaction_isolation": "SERIALIZABLE",
        }
    )
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
