"""P0-3 projection & reconciliation for the quota domain (SPEC-QA-001 §9).

Builds one ``quota_projection_candidate`` per quota-attributable Raw Object, attaches
deterministic *suggestions only* (never auto-archives), performs hash-based duplicate
detection with a deterministic canonical record, and emits a reconciliation report.

Hard boundaries (approved P0-3 corrections):
- Inventory denominator = DISTINCT file_asset_id attributable to quota via the UNION of
  (IngestEvent -> DataSource.data_domain=quota -> FileAsset) and
  (Archive.domain_type=quota -> ArchiveFile -> FileAsset). SHA is used only to flag
  duplicates, never to shrink the denominator.
- This stage creates NO Publication Set and NO Archive (both counts stay 0).
- Filename rules never fabricate a controlled discipline: unknown labels are stored as
  ``discipline_label_candidate`` (or ``volume_title_candidate``), never as discipline_code.
"""

from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Archive,
    ArchiveFile,
    DataSource,
    FileAsset,
    IngestEvent,
    QuotaDictionary,
    QuotaProjectionCandidate,
    QuotaPublicationSet,
)

RECONCILIATION_STATUSES = ["pending", "linked", "duplicate", "invalid", "ignored"]
# "装配式 / 智能建造 / 更新改造" are volume/keyword hints, not disciplines (correction #3).
VOLUME_KEYWORDS = ("装配式", "智能建造", "更新改造")
SEED_JURISDICTION_CODE = "510000"
_YEAR_RE = re.compile(r"(20\d{2})")


def quota_inventory_file_ids(session: Session) -> list[str]:
    via_ingest = (
        select(IngestEvent.file_id)
        .join(DataSource, IngestEvent.source_id == DataSource.source_id)
        .where(DataSource.data_domain == "quota")
    )
    via_archive = (
        select(ArchiveFile.file_id)
        .join(Archive, ArchiveFile.archive_id == Archive.archive_id)
        .where(Archive.domain_type == "quota", ArchiveFile.file_id.is_not(None))
    )
    ids = set(session.scalars(via_ingest)) | set(session.scalars(via_archive))
    ids.discard(None)
    return sorted(ids)


def _extract_label(file_name: str) -> str | None:
    name = file_name.rsplit(".", 1)[0].replace("《", "").replace("》", "")
    for separator in ("——", "—", "--", "-"):
        if separator in name:
            tail = name.split(separator)[-1].strip()
            return tail or None
    return None


def _dict_match(session: Session, dict_type: str, label: str) -> str | None:
    row = session.scalar(
        select(QuotaDictionary.code).where(
            QuotaDictionary.dict_type == dict_type,
            QuotaDictionary.enabled.is_(True),
            (QuotaDictionary.code == label) | (QuotaDictionary.label == label),
        )
    )
    return row


def _edition_year_for(session: Session, file_id: str) -> tuple[int | None, str | None]:
    """Derive edition_year from NAS provenance (P3-D2), in evidence priority order:
    1) IngestEvent.source_url (full original path);
    2) DataSource.name (seed batch / source name, e.g. "四川2025清单定额-seed");
    3) IngestEvent.source_item_key (stored relative path).
    The year lives in the batch root dir name, which is NOT always in source_item_key.
    """
    row = session.execute(
        select(IngestEvent.source_url, IngestEvent.source_item_key, DataSource.name)
        .join(DataSource, IngestEvent.source_id == DataSource.source_id)
        .where(IngestEvent.file_id == file_id, DataSource.data_domain == "quota")
        .order_by(IngestEvent.ingested_at.asc())
    ).first()
    if row is None:
        return None, None
    source_url, source_item_key, datasource_name = row
    for value, evidence in (
        (source_url, "source_url"),
        (datasource_name, "datasource_name"),
        (source_item_key, "source_item_key"),
    ):
        match = _YEAR_RE.search(value or "")
        if match:
            return int(match.group(1)), evidence
    return None, None


def _suggest_metadata(session: Session, asset: FileAsset) -> dict:
    name = asset.file_name or ""
    rules: list[str] = []
    meta: dict[str, object] = {}

    if "附录" in name:
        meta["file_role"] = "appendix"
        rules.append("filename:附录->file_role=appendix")
    else:
        meta["file_role"] = "main_document"
        rules.append("filename:default->file_role=main_document")

    if "四川" in name:
        meta["jurisdiction_level"] = "province"
        meta["jurisdiction_code"] = SEED_JURISDICTION_CODE
        rules.append("filename:四川->jurisdiction=510000")
    meta["quota_system_type"] = "construction_regional"
    meta["material_type"] = "quota_base"
    rules.append("filename:计价定额->quota_system_type=construction_regional")

    label = _extract_label(name)
    if label and "附录" not in label:
        if any(keyword in label for keyword in VOLUME_KEYWORDS):
            meta["volume_title_candidate"] = label
            rules.append(f"filename:{label}->volume_title_candidate")
        else:
            code = _dict_match(session, "discipline", label)
            if code:
                meta["discipline_code"] = code
                rules.append(f"dict:{label}->discipline_code={code}")
            else:
                meta["discipline_label_candidate"] = label
                rules.append(f"filename:{label}->discipline_label_candidate(no dict match)")

    year, evidence = _edition_year_for(session, asset.file_id)
    if year:
        # P3-D2: directory/source-derived year is a low-confidence suggestion only.
        meta["edition_year"] = {"value": year, "source": "nas_path", "status": "suggested"}
        rules.append(f"{evidence}:{year}->edition_year(suggested,source=nas_path)")

    return {"metadata": meta, "rules": rules, "confidence": 0.3}


def _canonical_file_id(session: Session, group: list[str], assets: dict[str, FileAsset]) -> str:
    linked = set(
        session.scalars(select(ArchiveFile.file_id).where(ArchiveFile.file_id.in_(group)))
    )
    preferred = [file_id for file_id in group if file_id in linked] or list(group)
    return sorted(preferred, key=lambda fid: (assets[fid].created_at, fid))[0]


def _mark_duplicates(session: Session, file_ids: list[str], assets: dict[str, FileAsset]) -> int:
    by_sha: dict[str, list[str]] = {}
    for file_id in file_ids:
        by_sha.setdefault(assets[file_id].sha256, []).append(file_id)

    marked = 0
    for group in by_sha.values():
        if len(group) <= 1:
            continue
        canonical = _canonical_file_id(session, group, assets)
        for file_id in group:
            if file_id == canonical:
                continue
            candidate = session.scalar(
                select(QuotaProjectionCandidate).where(QuotaProjectionCandidate.file_id == file_id)
            )
            # Never override a human-resolved candidate; only auto-mark pending ones.
            if candidate is None or candidate.projection_status != "pending":
                continue
            candidate.projection_status = "duplicate"
            candidate.duplicate_of_file_id = canonical
            candidate.resolution_note = f"sha256 duplicate of canonical file {canonical}"
            marked += 1
    return marked


def run_projection(session: Session) -> dict:
    file_ids = quota_inventory_file_ids(session)
    assets: dict[str, FileAsset] = {}
    if file_ids:
        assets = {
            asset.file_id: asset
            for asset in session.scalars(select(FileAsset).where(FileAsset.file_id.in_(file_ids)))
        }

    existing: dict[str, QuotaProjectionCandidate] = {}
    if file_ids:
        existing = {
            candidate.file_id: candidate
            for candidate in session.scalars(
                select(QuotaProjectionCandidate).where(QuotaProjectionCandidate.file_id.in_(file_ids))
            )
        }

    newly_created = 0
    enriched = 0
    for file_id in file_ids:
        if file_id in existing:
            enriched += _enrich_pending_candidate(session, existing[file_id])
            continue
        suggestion = _suggest_metadata(session, assets[file_id])
        session.add(
            QuotaProjectionCandidate(
                file_id=file_id,
                projection_status="pending",
                suggested_metadata=suggestion["metadata"],
                matched_rules=suggestion["rules"],
                suggestion_confidence=suggestion["confidence"],
            )
        )
        newly_created += 1
    session.flush()

    duplicates_marked = _mark_duplicates(session, file_ids, assets)
    session.commit()

    return reconciliation_report(
        session,
        newly_created_candidates=newly_created,
        duplicates_marked=duplicates_marked,
        enriched_candidates=enriched,
    )


def _enrich_pending_candidate(session: Session, candidate: QuotaProjectionCandidate) -> int:
    """Backfill edition_year on a still-pending candidate. Never touches non-pending
    (human-resolved) candidates, and never overwrites an existing edition_year."""
    if candidate.projection_status != "pending":
        return 0
    meta = dict(candidate.suggested_metadata or {})
    if "edition_year" in meta:
        return 0
    year, evidence = _edition_year_for(session, candidate.file_id)
    if not year:
        return 0
    meta["edition_year"] = {"value": year, "source": "nas_path", "status": "suggested"}
    candidate.suggested_metadata = meta
    rules = list(candidate.matched_rules or [])
    rules.append(f"{evidence}:{year}->edition_year(suggested,source=nas_path,backfill)")
    candidate.matched_rules = rules
    return 1


def reconciliation_report(
    session: Session,
    *,
    newly_created_candidates: int = 0,
    duplicates_marked: int = 0,
    enriched_candidates: int = 0,
) -> dict:
    file_ids = quota_inventory_file_ids(session)
    counts = {status: 0 for status in RECONCILIATION_STATUSES}
    if file_ids:
        rows = session.execute(
            select(QuotaProjectionCandidate.projection_status, func.count())
            .where(QuotaProjectionCandidate.file_id.in_(file_ids))
            .group_by(QuotaProjectionCandidate.projection_status)
        ).all()
        for status, count in rows:
            counts[status] = counts.get(status, 0) + int(count)

    raw_total = len(file_ids)
    all_domain_file_assets = int(session.scalar(select(func.count()).select_from(FileAsset)) or 0)
    return {
        "quota_raw_total": raw_total,
        **counts,
        "newly_created_candidates": newly_created_candidates,
        "enriched_candidates": enriched_candidates,
        "newly_created_publication_sets": 0,
        "newly_created_archives": 0,
        "duplicates_marked": duplicates_marked,
        "invariant_ok": raw_total == sum(counts.values()),
        "all_domain_file_asset_count": all_domain_file_assets,
        "note": (
            "quota_raw_total = DISTINCT file_asset_id attributable to quota (UNION of "
            "IngestEvent->DataSource.data_domain=quota and Archive.domain_type=quota->ArchiveFile). "
            "The all-domain FileAsset count (historically ~2058) is NOT the quota denominator."
        ),
    }


def snapshot_counts(session: Session) -> dict:
    """Before/after acceptance counters (all-domain totals + quota-scoped subsets)."""
    quota_file_ids = quota_inventory_file_ids(session)
    quota_source_ids = list(
        session.scalars(select(DataSource.source_id).where(DataSource.data_domain == "quota"))
    )

    def _count(model) -> int:
        return int(session.scalar(select(func.count()).select_from(model)) or 0)

    return {
        "data_source_total": _count(DataSource),
        "data_source_quota": len(quota_source_ids),
        "file_asset_total": _count(FileAsset),
        "file_asset_quota": len(quota_file_ids),
        "ingest_event_total": _count(IngestEvent),
        "projection_candidate_total": _count(QuotaProjectionCandidate),
        "publication_set_total": _count(QuotaPublicationSet),
        "archive_total": int(session.scalar(select(func.count()).select_from(Archive)) or 0),
        "archive_quota": int(
            session.scalar(
                select(func.count()).select_from(Archive).where(Archive.domain_type == "quota")
            )
            or 0
        ),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    from app.database import get_session_factory, init_db

    parser = argparse.ArgumentParser(prog="app.quota_projection")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run", help="run idempotent projection + reconciliation")
    subparsers.add_parser("report", help="print the current reconciliation report only")
    subparsers.add_parser("snapshot", help="print before/after acceptance counters")
    parser.add_argument(
        "--skip-init",
        action="store_true",
        help="skip init_db(); use when the quota schema is already deployed and an unrelated "
        "legacy migration would otherwise block startup",
    )
    args = parser.parse_args(argv)

    if not args.skip_init:
        init_db()
    factory = get_session_factory()
    if args.skip_init:
        from app.quota_seed import verify_quota_schema

        verify_quota_schema(factory().get_bind())
    with factory() as session:
        if args.command == "run":
            result = run_projection(session)
        elif args.command == "snapshot":
            result = snapshot_counts(session)
        else:
            result = reconciliation_report(session)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
