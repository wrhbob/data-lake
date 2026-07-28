"""
P0-4A · Quota domain API endpoints.

capabilities / stats / dictionaries / facets / archives / reconciliation.

All counts computed via SQL aggregation; no all-table Python-side counting.
"""

from datetime import date as date_type, datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, func, select, case, desc, asc
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.archive_rules import build_quota_business_key, metadata_cell, now_iso
from app.archive_service import attach_file as _attach_archive_file, create_archive
from app.assets import register_asset
from app.database import get_db_session
from app.models import (
    AdministrativeDivision,
    Archive,
    ArchiveEvent,
    ArchiveFile,
    DataSource,
    FileAsset,
    IngestEvent,
    QuotaArchiveProfile,
    QuotaDictionary,
    QuotaProjectionCandidate,
    QuotaPublicationSet,
    QuotaPublicationRelation,
)
from app.storage import ObjectStore, get_object_store

router = APIRouter(prefix="/api/data-lake/quota", tags=["quota"])

# ── helpers ─────────────────────────────────────────────────────────────

_SCHEMA_VERSION = "quota-r1"
_FEATURES = {
    "stats": "ready",
    "facets": "ready",
    "archives": "ready",
    "reconciliation": "ready",
    "dictionaries": "ready",
    "jurisdictions": "ready",
    "compose": "ready",
    "archiveFiles": "unavailable",
    "coverage": "unavailable",
    "publicationSets": "unavailable",
}

MIME = {"Content-Type": "application/json; charset=utf-8"}


def _json_ok(response: Response, body: object) -> object:
    response.headers.update(MIME)
    return body


def _safe_int(val) -> int:
    return int(val) if val is not None else 0


# ── 1. capabilities ─────────────────────────────────────────────────────

@router.get("/capabilities")
def get_capabilities(response: Response) -> dict:
    return _json_ok(response, {
        "schema_version": _SCHEMA_VERSION,
        "features": dict(_FEATURES),
    })


# ── 2. stats ────────────────────────────────────────────────────────────

@router.get("/stats")
def get_stats(response: Response, session: Session = Depends(get_db_session)) -> dict:
    systems = _safe_int(session.scalar(select(func.count()).select_from(QuotaPublicationSet)))
    archived = _safe_int(session.scalar(
        select(func.count()).select_from(Archive).where(Archive.domain_type == "quota")
    ))

    # raw_total: DISTINCT file_asset_id via two-leg UNION
    leg1 = select(FileAsset.file_id).join(
        IngestEvent, IngestEvent.file_id == FileAsset.file_id
    ).join(
        DataSource, DataSource.source_id == IngestEvent.source_id
    ).where(DataSource.data_domain == "quota")

    leg2 = select(FileAsset.file_id).join(
        ArchiveFile, ArchiveFile.file_id == FileAsset.file_id
    ).join(
        Archive, ArchiveFile.archive_id == Archive.archive_id
    ).where(Archive.domain_type == "quota")

    union_cte = leg1.union(leg2).cte("quota_file_ids")
    raw_total = _safe_int(session.scalar(
        select(func.count()).select_from(select(union_cte.c.file_id).distinct().subquery())
    ))

    # projection status counts
    proj_rows = dict(session.execute(
        select(
            QuotaProjectionCandidate.projection_status,
            func.count(),
        ).group_by(QuotaProjectionCandidate.projection_status)
    ).all())

    pending = _safe_int(proj_rows.get("pending", 0))
    linked = _safe_int(proj_rows.get("linked", 0))
    duplicate = _safe_int(proj_rows.get("duplicate", 0))
    invalid = _safe_int(proj_rows.get("invalid", 0))
    ignored = _safe_int(proj_rows.get("ignored", 0))

    return _json_ok(response, {
        "systems": systems,
        "archived": archived,
        "raw_total": raw_total,
        "accessible": None,
        "pending": pending,
        "linked": linked,
        "duplicate": duplicate,
        "invalid": invalid,
        "ignored": ignored,
        "storage_audit_at": None,
    })


# ── 3. dictionaries ─────────────────────────────────────────────────────

@router.get("/dictionaries")
def get_dictionaries(
    response: Response,
    type: str | None = Query(None, alias="type"),
    session: Session = Depends(get_db_session),
) -> dict:
    stmt = select(QuotaDictionary).where(QuotaDictionary.enabled.is_(True))
    if type:
        stmt = stmt.where(QuotaDictionary.dict_type == type)
    stmt = stmt.order_by(QuotaDictionary.sort_order, QuotaDictionary.label)
    rows = list(session.execute(stmt).scalars().all())
    return _json_ok(response, {
        "items": [{
            "dict_type": r.dict_type,
            "code": r.code,
            "label": r.label,
            "sort_order": r.sort_order,
        } for r in rows],
    })


# ── 4. facets ───────────────────────────────────────────────────────────

def _facet_years(session: Session, primary: str | None = None, jurisdiction_code: str | None = None) -> list[dict]:
    stmt = select(
        QuotaPublicationSet.edition_year,
        func.count().label("cnt"),
    )
    if primary:
        if primary == "construction_regional":
            stmt = stmt.where(QuotaPublicationSet.quota_system_type == "construction_regional")
        elif primary == "industry_specialty":
            stmt = stmt.where(QuotaPublicationSet.quota_system_type == "industry_specialty")
        elif primary == "boq_standard":
            stmt = stmt.where(QuotaPublicationSet.material_type == "boq_standard")
    if jurisdiction_code:
        stmt = stmt.where(QuotaPublicationSet.jurisdiction_code == jurisdiction_code)
    stmt = stmt.where(QuotaPublicationSet.edition_year.isnot(None))
    stmt = stmt.group_by(QuotaPublicationSet.edition_year).order_by(desc(QuotaPublicationSet.edition_year))
    rows = session.execute(stmt).all()
    years = []
    for year, cnt in rows:
        y = str(year)
        # fetch editions for this year
        ed_stmt = select(
            QuotaPublicationSet.edition_label,
            func.count().label("ed_cnt"),
        ).where(
            QuotaPublicationSet.edition_year == y,
            QuotaPublicationSet.edition_label.isnot(None),
        )
        if jurisdiction_code:
            ed_stmt = ed_stmt.where(QuotaPublicationSet.jurisdiction_code == jurisdiction_code)
        ed_stmt = ed_stmt.group_by(QuotaPublicationSet.edition_label)
        ed_rows = session.execute(ed_stmt).all()
        editions = [{"value": str(el), "label": str(el), "count": int(ec)} for el, ec in ed_rows]
        years.append({"value": y, "label": y, "count": int(cnt), "editions": editions})
    return years


@router.get("/facets")
def get_facets(
    response: Response,
    primary: str | None = Query(None, description="all|construction_regional|industry_specialty|boq_standard"),
    jurisdiction_code: str | None = Query(None, description="filter years by jurisdiction (construction_regional only)"),
    session: Session = Depends(get_db_session),
) -> dict:
    jurisdictions: list[dict] = []
    industries: list[dict] = []
    scopes: list[dict] = []
    years: list[dict] = []

    if primary == "construction_regional":
        j_rows = session.execute(
            select(
                QuotaPublicationSet.jurisdiction_code,
                func.count().label("cnt"),
            ).where(
                QuotaPublicationSet.quota_system_type == "construction_regional",
                QuotaPublicationSet.jurisdiction_code.isnot(None),
            ).group_by(QuotaPublicationSet.jurisdiction_code)
        ).all()
        for code, cnt in j_rows:
            div = session.scalar(
                select(AdministrativeDivision.name).where(AdministrativeDivision.code == code)
            )
            label = div or str(code)
            jurisdictions.append({"code": str(code), "label": label, "count": int(cnt)})
        years = _facet_years(session, "construction_regional", jurisdiction_code=jurisdiction_code)

    elif primary == "industry_specialty":
        i_rows = session.execute(
            select(
                QuotaPublicationSet.industry_sector_code,
                func.count().label("cnt"),
            ).where(
                QuotaPublicationSet.quota_system_type == "industry_specialty",
                QuotaPublicationSet.industry_sector_code.isnot(None),
            ).group_by(QuotaPublicationSet.industry_sector_code)
        ).all()
        for code, cnt in i_rows:
            label_row = session.scalar(
                select(QuotaDictionary.label).where(
                    QuotaDictionary.dict_type == "industry_sector",
                    QuotaDictionary.code == code,
                )
            )
            label = label_row or str(code)
            industries.append({"code": str(code), "label": label, "count": int(cnt)})
        years = _facet_years(session, "industry_specialty")

    elif primary == "boq_standard":
        s_rows = session.execute(
            select(
                QuotaPublicationSet.title,
                func.count().label("cnt"),
            ).where(
                QuotaPublicationSet.material_type == "boq_standard",
                QuotaPublicationSet.title.isnot(None),
            ).group_by(QuotaPublicationSet.title)
        ).all()
        for title, cnt in s_rows:
            scopes.append({"code": str(title), "label": str(title), "count": int(cnt)})
        years = _facet_years(session, "boq_standard")

    else:
        years = _facet_years(session)

    return _json_ok(response, {
        "jurisdictions": jurisdictions,
        "industries": industries,
        "scopes": scopes,
        "years": years,
        "disciplines": [],
        "materialTypes": [],
        "cities": [],
        "issuers": [],
        "metadataStatuses": [],
        "archiveStatuses": [],
        "sourceChannels": [],
        "fileFormats": [],
    })


# ── 5. archives ─────────────────────────────────────────────────────────

@router.get("/archives")
def list_quota_archives(
    response: Response,
    q: str | None = Query(None, description="search title"),
    primary: str | None = Query(None, description="all|construction_regional|industry_specialty|boq_standard"),
    jurisdiction_code: str | None = Query(None),
    industry_sector_code: str | None = Query(None),
    edition_year: str | None = Query(None),
    edition_label: str | None = Query(None),
    discipline_code: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    sort_by: str | None = Query(None),
    sort_order: str | None = Query("desc"),
    session: Session = Depends(get_db_session),
) -> dict:
    # base: Archive with domain_type=quota
    stmt = (
        select(Archive, QuotaArchiveProfile, QuotaPublicationSet)
        .join(QuotaArchiveProfile, QuotaArchiveProfile.archive_id == Archive.archive_id, isouter=True)
        .join(QuotaPublicationSet, QuotaPublicationSet.publication_set_id == QuotaArchiveProfile.publication_set_id, isouter=True)
        .where(Archive.domain_type == "quota")
    )

    if q:
        stmt = stmt.where(Archive.title.ilike(f"%{q}%"))
    if primary:
        if primary == "construction_regional":
            stmt = stmt.where(QuotaPublicationSet.quota_system_type == "construction_regional")
        elif primary == "industry_specialty":
            stmt = stmt.where(QuotaPublicationSet.quota_system_type == "industry_specialty")
        elif primary == "boq_standard":
            stmt = stmt.where(QuotaPublicationSet.material_type == "boq_standard")
    if jurisdiction_code:
        stmt = stmt.where(QuotaPublicationSet.jurisdiction_code == jurisdiction_code)
    if industry_sector_code:
        stmt = stmt.where(QuotaPublicationSet.industry_sector_code == industry_sector_code)
    if edition_year:
        stmt = stmt.where(QuotaPublicationSet.edition_year == edition_year)
    if edition_label:
        stmt = stmt.where(QuotaPublicationSet.edition_label == edition_label)
    if discipline_code:
        stmt = stmt.where(QuotaArchiveProfile.discipline_code == discipline_code)

    # sort
    sort_col = Archive.created_at
    if sort_by == "title":
        sort_col = Archive.title
    elif sort_by == "edition_year":
        sort_col = QuotaPublicationSet.edition_year
    stmt = stmt.order_by(desc(sort_col) if sort_order == "desc" else asc(sort_col))

    # count (与 /stats 同源:默认不 join profile/pubset;filter 含相关字段时按需 join)
    needs_pubset = bool(primary or jurisdiction_code or industry_sector_code or edition_year or edition_label)
    needs_profile = bool(discipline_code) or needs_pubset
    count_stmt = select(func.count()).select_from(Archive).where(Archive.domain_type == "quota")
    if needs_profile:
        count_stmt = count_stmt.join(
            QuotaArchiveProfile, QuotaArchiveProfile.archive_id == Archive.archive_id, isouter=True
        )
    if needs_pubset:
        count_stmt = count_stmt.join(
            QuotaPublicationSet,
            QuotaPublicationSet.publication_set_id == QuotaArchiveProfile.publication_set_id,
            isouter=True,
        )
    if q:
        count_stmt = count_stmt.where(Archive.title.ilike(f"%{q}%"))
    if primary:
        if primary == "construction_regional":
            count_stmt = count_stmt.where(QuotaPublicationSet.quota_system_type == "construction_regional")
        elif primary == "industry_specialty":
            count_stmt = count_stmt.where(QuotaPublicationSet.quota_system_type == "industry_specialty")
        elif primary == "boq_standard":
            count_stmt = count_stmt.where(QuotaPublicationSet.material_type == "boq_standard")
    if jurisdiction_code:
        count_stmt = count_stmt.where(QuotaPublicationSet.jurisdiction_code == jurisdiction_code)
    if industry_sector_code:
        count_stmt = count_stmt.where(QuotaPublicationSet.industry_sector_code == industry_sector_code)
    if edition_year:
        count_stmt = count_stmt.where(QuotaPublicationSet.edition_year == edition_year)
    if edition_label:
        count_stmt = count_stmt.where(QuotaPublicationSet.edition_label == edition_label)
    if discipline_code:
        count_stmt = count_stmt.where(QuotaArchiveProfile.discipline_code == discipline_code)
    total = _safe_int(session.scalar(count_stmt))

    offset = (page - 1) * page_size
    rows = session.execute(stmt.offset(offset).limit(page_size)).all()

    items = []
    for archive, profile, pubset in rows:
        file_count = _safe_int(session.scalar(
            select(func.count()).select_from(ArchiveFile).where(ArchiveFile.archive_id == archive.archive_id)
        ))
        items.append({
            "archive_id": archive.archive_id,
            "title": archive.title,
            "domain_type": archive.domain_type,
            "status": archive.status,
            "business_key": archive.business_key,
            "region_code": archive.region_code,
            "publication_set_title": pubset.title if pubset else None,
            "quota_system_type": pubset.quota_system_type if pubset else None,
            "jurisdiction_code": pubset.jurisdiction_code if pubset else None,
            "industry_sector_code": pubset.industry_sector_code if pubset else None,
            "edition_year": pubset.edition_year if pubset else None,
            "edition_label": pubset.edition_label if pubset else None,
            "standard_or_quota_code": pubset.standard_or_quota_code if pubset else None,
            "volume_title": profile.volume_title if profile else None,
            "discipline_code": profile.discipline_code if profile else None,
            "document_role": profile.document_role if profile else None,
            "metadata_status": profile.metadata_status if profile else None,
            "completeness_score": profile.completeness_score if profile else None,
            "file_count": file_count,
        })

    response.headers.update({
        **MIME,
        "X-Total-Count": str(total),
        "X-Page": str(page),
        "X-Page-Size": str(page_size),
    })
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/archives/{archive_id}")
def get_quota_archive_detail(
    response: Response,
    archive_id: str,
    session: Session = Depends(get_db_session),
) -> dict:
    """Read-only archive detail: archive + publication_set + volumes + files.

    Does NOT advance the state machine. Status transitions happen elsewhere.
    """
    main_row = session.execute(
        select(Archive, QuotaArchiveProfile, QuotaPublicationSet)
        .join(
            QuotaArchiveProfile,
            QuotaArchiveProfile.archive_id == Archive.archive_id,
            isouter=True,
        )
        .join(
            QuotaPublicationSet,
            QuotaPublicationSet.publication_set_id == QuotaArchiveProfile.publication_set_id,
            isouter=True,
        )
        .where(Archive.archive_id == archive_id, Archive.domain_type == "quota")
    ).first()
    if main_row is None:
        raise HTTPException(status_code=404, detail="档案不存在或已删除")

    archive, profile, pubset = main_row

    file_count = _safe_int(session.scalar(
        select(func.count())
        .select_from(ArchiveFile)
        .where(ArchiveFile.archive_id == archive.archive_id)
    ))

    archive_obj = {
        "archive_id": archive.archive_id,
        "title": archive.title,
        "domain_type": archive.domain_type,
        "status": archive.status,
        "business_key": archive.business_key,
        "region_code": archive.region_code,
        "publication_set_id": profile.publication_set_id if profile else None,
        "volume_code": profile.volume_code if profile else None,
        "volume_title": profile.volume_title if profile else None,
        "discipline_code": profile.discipline_code if profile else None,
        "document_role": profile.document_role if profile else None,
        "metadata_status": profile.metadata_status if profile else None,
        "completeness_score": profile.completeness_score if profile else None,
        "file_count": file_count,
        "created_at": archive.created_at.isoformat() if archive.created_at else None,
        "updated_at": archive.updated_at.isoformat() if archive.updated_at else None,
    }

    publication_set_obj = None
    if pubset is not None:
        publication_set_obj = {
            "publication_set_id": pubset.publication_set_id,
            "biz_key": pubset.biz_key,
            "title": pubset.title,
            "material_type": pubset.material_type,
            "quota_system_type": pubset.quota_system_type,
            "jurisdiction_level": pubset.jurisdiction_level,
            "jurisdiction_code": pubset.jurisdiction_code,
            "industry_sector_code": pubset.industry_sector_code,
            "issuer_name": pubset.issuer_name,
            "standard_or_quota_code": pubset.standard_or_quota_code,
            "edition_label": pubset.edition_label,
            "edition_year": pubset.edition_year,
            "publish_date": pubset.publish_date.isoformat() if pubset.publish_date else None,
            "effective_date": pubset.effective_date.isoformat() if pubset.effective_date else None,
            "legal_status": pubset.legal_status,
            "metadata_status": pubset.metadata_status,
        }

    # ── volumes (sibling archives sharing the same publication_set_id) ──
    volumes: list[dict] = []
    if profile is not None and profile.publication_set_id:
        vol_rows = session.execute(
            select(Archive, QuotaArchiveProfile)
            .join(
                QuotaArchiveProfile,
                QuotaArchiveProfile.archive_id == Archive.archive_id,
                isouter=True,
            )
            .where(QuotaArchiveProfile.publication_set_id == profile.publication_set_id)
            .order_by(asc(QuotaArchiveProfile.part_no), asc(Archive.title))
        ).all()
        for a, p in vol_rows:
            fc = _safe_int(session.scalar(
                select(func.count())
                .select_from(ArchiveFile)
                .where(ArchiveFile.archive_id == a.archive_id)
            ))
            volumes.append({
                "archive_id": a.archive_id,
                "title": a.title,
                "status": a.status,
                "volume_code": p.volume_code if p else None,
                "volume_title": p.volume_title if p else None,
                "discipline_code": p.discipline_code if p else None,
                "document_role": p.document_role if p else None,
                "file_count": fc,
                "is_current": a.archive_id == archive.archive_id,
            })

    # ── files (ArchiveFile LEFT JOIN FileAsset) ────────────────────────
    files: list[dict] = []
    file_rows = session.execute(
        select(ArchiveFile, FileAsset)
        .join(FileAsset, FileAsset.file_id == ArchiveFile.file_id, isouter=True)
        .where(ArchiveFile.archive_id == archive.archive_id)
        .order_by(asc(ArchiveFile.sort_order), asc(ArchiveFile.added_at))
    ).all()
    for af, fa in file_rows:
        files.append({
            "archive_file_id": af.archive_file_id,
            "file_id": af.file_id,
            "display_name": af.display_name or (fa.file_name if fa else None),
            "file_role": af.file_role,
            "is_primary": af.is_primary,
            "fetch_status": af.fetch_status,
            "representation_role": af.representation_role,
            "page_range": af.page_range,
            "mime_type": fa.mime_type if fa else None,
            "size_bytes": fa.file_size if fa else None,
            "file_name": fa.file_name if fa else None,
        })

    # ── parse 子对象（quota_parser integration, 2026-07-28 · web-frontend SPEC §6.1） ──
    from app.quota_parser.service import build_parse_section

    parse_obj = build_parse_section(archive)

    return _json_ok(response, {
        "archive": archive_obj,
        "publication_set": publication_set_obj,
        "volumes": volumes,
        "files": files,
        "parse": parse_obj,
    })


# ── 6. reconciliation ──────────────────────────────────────────────────

@router.get("/reconciliation")
def get_reconciliation(
    response: Response,
    projection_status: str | None = Query(None, description="pending|linked|duplicate|invalid|ignored"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_db_session),
) -> dict:
    # summary counts
    count_rows = dict(session.execute(
        select(
            QuotaProjectionCandidate.projection_status,
            func.count(),
        ).group_by(QuotaProjectionCandidate.projection_status)
    ).all())
    pending = _safe_int(count_rows.get("pending", 0))
    linked = _safe_int(count_rows.get("linked", 0))
    duplicate = _safe_int(count_rows.get("duplicate", 0))
    invalid = _safe_int(count_rows.get("invalid", 0))
    ignored = _safe_int(count_rows.get("ignored", 0))

    # detail query: join file_asset, ingest_event, data_source
    stmt = (
        select(
            QuotaProjectionCandidate,
            FileAsset.file_name,
            FileAsset.file_size,
            FileAsset.sha256,
            FileAsset.mime_type,
            DataSource.name,
            DataSource.source_type,
            IngestEvent.ingested_at,
        )
        .join(FileAsset, FileAsset.file_id == QuotaProjectionCandidate.file_id, isouter=True)
        .join(IngestEvent, IngestEvent.file_id == FileAsset.file_id, isouter=True)
        .join(DataSource, DataSource.source_id == IngestEvent.source_id, isouter=True)
    )
    if projection_status:
        stmt = stmt.where(QuotaProjectionCandidate.projection_status == projection_status)
    stmt = stmt.order_by(desc(QuotaProjectionCandidate.suggestion_confidence))

    # count
    count_q = select(func.count()).select_from(QuotaProjectionCandidate)
    if projection_status:
        count_q = count_q.where(QuotaProjectionCandidate.projection_status == projection_status)
    total = _safe_int(session.scalar(count_q))

    offset = (page - 1) * page_size
    rows = session.execute(stmt.offset(offset).limit(page_size)).all()

    items = []
    for cand, filename, filesize, filehash, mimetype, src_name, src_type, ingested_at in rows:
        items.append({
            "candidate_id": cand.candidate_id,
            "file_id": cand.file_id,
            "file_name": filename,
            "file_size": filesize,
            "file_hash": filehash,
            "file_type": mimetype,
            "projection_status": cand.projection_status,
            "suggested_metadata": cand.suggested_metadata,
            "suggestion_confidence": float(cand.suggestion_confidence or 0),
            "matched_rules": cand.matched_rules,
            "duplicate_of_file_id": cand.duplicate_of_file_id,
            "source_name": src_name,
            "source_type": src_type,
            "ingested_at": ingested_at.isoformat() if ingested_at else None,
        })

    return _json_ok(response, {
        "pending": pending,
        "linked": linked,
        "duplicate": duplicate,
        "invalid": invalid,
        "ignored": ignored,
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
    })


# ── 7. compose (POST) ──────────────────────────────────────────────────


class _ComposeVolume(BaseModel):
    volume_title: str = ""
    discipline_code: str = ""
    files: list[dict] = []


class ComposeRequest(BaseModel):
    action: str
    material_type: str = "quota_base"
    systemType: str = ""
    path: dict = {}
    set: dict = {}
    relation: dict = {}
    targetKind: str = ""
    targetId: str = ""
    targetLabel: str = ""
    volumes: list[_ComposeVolume] = []
    unassignedFiles: list[dict] = []
    supplementFiles: list[dict] = []
    tenant: str = "platform_public"


@router.post("/compose")
def compose_quota(
    response: Response,
    body: ComposeRequest,
    session: Session = Depends(get_db_session),
) -> dict:
    try:
        # ── determine material_type from compose state ────────────────
        material_type = body.set.get("material_type") or body.material_type or "quota_base"
        quota_system_type = body.systemType or None

        # ── validate jurisdiction for construction_regional ───────────
        jurisdiction_code = body.path.get("jurisdiction_code", "") if body.path else ""
        jurisdiction_level = body.path.get("jurisdiction_level", "") if body.path else ""
        if quota_system_type == "construction_regional" and not jurisdiction_code:
            return _json_ok(response, {
                "ok": False,
                "error": "REGIONAL_REQUIRES_JURISDICTION",
                "detail": "地方定额必须选择省份",
            })

        # ── create or resolve QuotaPublicationSet ─────────────────────
        set_data = body.set or {}
        edition_year_raw = set_data.get("edition_year", "")
        edition_year = int(edition_year_raw) if edition_year_raw else None
        title = set_data.get("title", "") or "未命名定额体系"

        # Build biz_key: quota:{material_type}:{system_type}:{jurisdiction}:{sector}:{family}:{edition}
        family_code = _make_publication_family_code(body)
        biz_key_parts = [
            "quota",
            material_type,
            quota_system_type or "general",
            jurisdiction_code or "national",
            body.path.get("industry_sector_code", "") or "general",
            family_code,
            set_data.get("edition_label", edition_year_raw or ""),
        ]
        biz_key = ":".join(biz_key_parts)

        # Upsert publication set
        existing_pub_set = session.scalar(
            select(QuotaPublicationSet).where(QuotaPublicationSet.biz_key == biz_key)
        )
        if existing_pub_set is not None:
            pub_set = existing_pub_set
        else:
            pub_set = QuotaPublicationSet(
                publication_set_id=str(uuid4()),
                biz_key=biz_key,
                publication_family_code=family_code,
                title=title,
                material_type=material_type,
                quota_system_type=quota_system_type,
                jurisdiction_level=jurisdiction_level or "province",
                jurisdiction_code=jurisdiction_code or "000000",
                industry_sector_code=body.path.get("industry_sector_code") or None,
                issuer_name=set_data.get("issuer_name", "") or _default_issuer(jurisdiction_code, set_data),
                standard_or_quota_code=set_data.get("standard_or_quota_code") or None,
                edition_label=set_data.get("edition_label", "") or (str(edition_year) if edition_year else ""),
                edition_year=edition_year,
                publish_date=_parse_date(set_data.get("publish_date")),
                effective_date=_parse_date(set_data.get("effective_date")),
                legal_status=set_data.get("legal_status", "unknown") or "unknown",
                metadata_status="partial",
                tenant_code=body.tenant,
                visibility_scope="public",
            )
            session.add(pub_set)
            session.flush()

        # ── handle relations ──────────────────────────────────────────
        rel_data = body.relation or {}
        related_id = rel_data.get("related_publication_set_id")
        rel_type = rel_data.get("relation_type")
        if related_id and rel_type:
            existing_rel = session.scalar(
                select(QuotaPublicationRelation).where(
                    QuotaPublicationRelation.source_publication_set_id == pub_set.publication_set_id,
                    QuotaPublicationRelation.target_publication_set_id == related_id,
                    QuotaPublicationRelation.relation_type == rel_type,
                )
            )
            if existing_rel is None:
                session.add(QuotaPublicationRelation(
                    relation_id=str(uuid4()),
                    source_publication_set_id=pub_set.publication_set_id,
                    target_publication_set_id=related_id,
                    relation_type=rel_type,
                ))

        # ── create Archive + QuotaArchiveProfile per volume ───────────
        source_id = f"quota-compose-{pub_set.publication_set_id[:8]}"

        # Ensure DataSource exists before create_archive (it checks DataSource）
        _ensure_compose_source(session, source_id, body.tenant)
        session.flush()

        created_archives: list[dict] = []

        for i, vol in enumerate(body.volumes):
            vol_title = vol.volume_title or f"第{i + 1}分册"
            business_key = build_quota_business_key(
                source_id=source_id,
                title=f"{title}—{vol_title}",
                region=jurisdiction_code or None,
                quota_code=set_data.get("standard_or_quota_code") or None,
                version=str(edition_year) if edition_year else None,
                effective_date=set_data.get("effective_date") or None,
                standard_no=set_data.get("standard_or_quota_code") or None,
            )
            # create_archive internally calls session.commit() — service layer contract
            archive = create_archive(
                session,
                domain_type="quota",
                channel_type="manual_upload",
                business_key=business_key,
                title=f"{title}—{vol_title}",
                source_id=source_id,
                tenant_code=body.tenant,
                visibility_scope="public",
                status="pending_tag",
                metadata={"_compose_provenance": _provenance_cell(material_type)},
                field_sources=_build_archive_field_sources(
                    title=f"{title}—{vol_title}",
                    business_key=business_key,
                    region_code=jurisdiction_code or "",
                ),
            )
            # Upsert profile (session was committed by create_archive, so add needs fresh flush)
            existing_profile = session.get(QuotaArchiveProfile, archive.archive_id)
            if existing_profile is None:
                profile = QuotaArchiveProfile(
                    archive_id=archive.archive_id,
                    publication_set_id=pub_set.publication_set_id,
                    document_role=_map_action_to_document_role(body.action),
                    discipline_code=vol.discipline_code or None,
                    volume_code="",
                    volume_title=vol_title,
                    metadata_status="partial",
                    completeness_score=0,
                )
                session.add(profile)

            created_archives.append({
                "archive_id": archive.archive_id,
                "title": archive.title,
                "volume_title": vol_title,
                "file_count": len(vol.files),
            })

        session.commit()

        return _json_ok(response, {
            "ok": True,
            "publication_set_id": pub_set.publication_set_id,
            "biz_key": biz_key,
            "archives": created_archives,
        })
    except IntegrityError as e:
        session.rollback()
        return _json_ok(response, {
            "ok": False,
            "error": "INTEGRITY_ERROR",
            "detail": str(e.orig) if e.orig else str(e),
        })
    except Exception as e:
        session.rollback()
        return _json_ok(response, {
            "ok": False,
            "error": "COMPOSE_FAILED",
            "detail": str(e),
        })


# ── compose helpers ────────────────────────────────────────────────────


def _make_publication_family_code(body: ComposeRequest) -> str:
    """Derive a stable family code from jurisdiction + system_type + material_type."""
    jc = (body.path or {}).get("jurisdiction_code", "") or "national"
    st = body.systemType or "general"
    mt = ((body.set or {}).get("material_type") or body.material_type or "quota_base")
    return f"{jc}-{st}-{mt}"


def _default_issuer(jurisdiction_code: str, set_data: dict) -> str:
    """Guess issuer name when not provided."""
    name = (set_data.get("issuer_name") or "").strip()
    if name:
        return name
    if jurisdiction_code == "510000":
        return "四川省住房和城乡建设厅"
    return ""


def _parse_date(val: object) -> date_type | None:
    if not val:
        return None
    s = str(val).strip()
    if not s:
        return None
    try:
        return date_type.fromisoformat(s[:10])
    except (ValueError, TypeError):
        return None


def _build_archive_field_sources(
    *, title: str, business_key: str, region_code: str = ""
) -> dict:
    """Build field_sources for create_archive C-class fields."""
    ts = now_iso()
    sources: dict = {}
    for field_name in ("domain_type", "channel_type"):
        sources[field_name] = metadata_cell(
            "quota" if field_name == "domain_type" else "manual_upload",
            source_level="manual", tagged_by="system", tagged_at=ts,
        )
    if title:
        sources["title"] = metadata_cell(title, source_level="manual", tagged_by="system", tagged_at=ts)
    if business_key:
        sources["business_key"] = metadata_cell(business_key, source_level="manual", tagged_by="system", tagged_at=ts)
    if region_code:
        sources["region_code"] = metadata_cell(region_code, source_level="manual", tagged_by="system", tagged_at=ts)
    return sources


def _provenance_cell(value: object) -> dict:
    return {
        "source_level": "manual",
        "tagged_by": "system",
        "tagged_at": datetime.now(timezone.utc).isoformat(),
        "value": value,
    }


def _map_action_to_document_role(action: str) -> str:
    if action in ("new_set", "new_boq"):
        return "main_volume"
    if action == "add_volume":
        return "main_volume"
    if action == "supplement":
        return "other"
    return "main_volume"


def _ensure_compose_source(session: Session, source_id: str, tenant_code: str) -> None:
    existing = session.get(DataSource, source_id)
    if existing is not None:
        return
    ds = DataSource(
        source_id=source_id,
        source_scope="platform_public",
        managed_by="platform",
        source_type="manual",
        connector_type="manual_upload",
        name=f"Quota Compose ({source_id[-8:]})",
        data_domain="quota",
        format="pdf",
        config={},
        status="active",
        created_by="system",
        asset_tenant_code=tenant_code,
    )
    session.add(ds)


# ── 8. upload (POST /upload) · HOTFIX-QA-UPLOAD-001 ────────────────────
#
# 极简上传闭环：PDF → MinIO → FileAsset → Archive(domain=quota) →
# ArchiveFile(main_document, is_primary=True)。
#
# 设计约束（任务单明令）：
# - file_role 恒为 main_document，is_primary=True（资料分类 category 是另一维度，
#   只写入 Archive.metadata_payload，绝不映射到 file_role）。
# - business_key 基于 sha256 而非标题：同名异内容 → 不同 Archive；
#   异名同内容 → 同一 Archive（幂等）。
# - 严禁把 industry_quota（前端 category）写成 industry_specialty
#   （quota_system_type）；二者语义不同。
# - 单文件粒度事务：一文件失败不影响其它，逐项返回。
# - 防空 Archive：attach_file 失败时若 Archive 是本次新建的，回滚掉。

_QUOTA_UPLOAD_SOURCE_ID = "quota-manual-upload"

QUOTA_UPLOAD_CATEGORY_LABELS = {
    "construction_quota": "建筑工程定额",
    "industry_quota":     "专业工程定额",
    "boq_standard":       "清单规范",
}

_QUOTA_UPLOAD_VALID_CATEGORIES = set(QUOTA_UPLOAD_CATEGORY_LABELS.keys())


class QuotaUploadItem(BaseModel):
    filename: str
    status: str            # "created" | "duplicate" | "failed"
    file_id: str | None = None
    archive_id: str | None = None
    title: str | None = None
    already_exists: bool = False
    error: str | None = None


class QuotaUploadResponse(BaseModel):
    count: int
    succeeded: int
    failed: int
    items: list[QuotaUploadItem]


def _ensure_quota_upload_source(session: Session) -> str:
    """查询或创建稳定的 quota 手工上传 DataSource，返回 source_id。

    不假设 database.py 的启动种子化一定跑过；端点入口自洽查询/创建。
    """
    existing = session.get(DataSource, _QUOTA_UPLOAD_SOURCE_ID)
    if existing is not None:
        return _QUOTA_UPLOAD_SOURCE_ID
    ds = DataSource(
        source_id=_QUOTA_UPLOAD_SOURCE_ID,
        source_scope="platform_public",
        managed_by="platform",
        source_type="quota_manual_upload",
        connector_type="manual_upload",
        name="quota-manual-upload",
        data_domain="quota",
        format="pdf",
        config={},
        status="active",
        created_by="system",
        asset_tenant_code="platform_public",
    )
    session.add(ds)
    session.commit()
    return _QUOTA_UPLOAD_SOURCE_ID


def _cleanup_empty_archive(session: Session, archive_id: str) -> None:
    """attach_file 失败时删除本次新建的空 Archive（及其 ArchiveFile/ArchiveEvent/
    QuotaArchiveProfile 残骸）。

    FileAsset 由 register_asset 独立 commit，保留为原件（用户可重试或自行归档）。
    本补偿仅在"本次 create_archive 成功 + 后续 attach_file 失败"时触发，
    不触及历史数据。
    """
    session.execute(delete(ArchiveFile).where(ArchiveFile.archive_id == archive_id))
    session.execute(delete(ArchiveEvent).where(ArchiveEvent.archive_id == archive_id))
    session.execute(delete(QuotaArchiveProfile).where(QuotaArchiveProfile.archive_id == archive_id))
    session.execute(delete(Archive).where(Archive.archive_id == archive_id))
    session.commit()


@router.post("/upload", response_model=QuotaUploadResponse)
async def upload_quota_files(
    response: Response,
    files: list[UploadFile] = File(...),
    category: str = Form(...),
    session: Session = Depends(get_db_session),
    storage: ObjectStore = Depends(get_object_store),
) -> QuotaUploadResponse:
    """极简定额 PDF 上传：PDF → FileAsset → Archive(quota) → ArchiveFile(main_document)。

    单文件粒度处理，一文件失败不影响其它。重复文件（同 tenant + 同 sha256）幂等返回。
    """
    # category 校验：非法值直接 422（Pydantic response_model 不约束 Form 参数）
    if category not in _QUOTA_UPLOAD_VALID_CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=f"INVALID_CATEGORY: {category}. 必须为 {sorted(_QUOTA_UPLOAD_VALID_CATEGORIES)} 之一",
        )
    if not files:
        raise HTTPException(status_code=422, detail="NO_FILES_RECEIVED")

    source_id = _ensure_quota_upload_source(session)
    category_cell = metadata_cell(category, source_level="manual", tagged_by="ui:quota-upload")

    items: list[QuotaUploadItem] = []
    succeeded = 0
    failed = 0

    for f in files:
        filename = f.filename or "(unnamed)"

        # ── 1. 扩展名校验（仅接受 .pdf）──────────────────────────────
        ext = Path(filename).suffix.lower()
        if ext != ".pdf":
            items.append(QuotaUploadItem(
                filename=filename, status="failed", error="NON_PDF_REJECTED",
            ))
            failed += 1
            continue

        # ── 2. 读取内容 ────────────────────────────────────────────────
        try:
            content = await f.read()
        except Exception as read_exc:  # noqa: BLE001
            items.append(QuotaUploadItem(
                filename=filename, status="failed",
                error=f"READ_FAILED: {read_exc}",
            ))
            failed += 1
            continue

        # ── 3. register_asset（按 tenant+sha256 幂等）─────────────────
        try:
            result = register_asset(
                session,
                storage,
                tenant_code="platform_public",
                source_type="quota_manual_upload",
                batch_id=f"quota-upload-{uuid4().hex[:8]}",
                file_name=filename,
                content=content,
                source_id=source_id,
                create_ingest_event=True,
                derive_tasks=False,           # hotfix 不触发 OCR/解析任务
                mime_type="application/pdf",
            )
        except Exception as reg_exc:  # noqa: BLE001
            items.append(QuotaUploadItem(
                filename=filename, status="failed",
                error=f"REGISTER_FAILED: {reg_exc}",
            ))
            failed += 1
            continue

        # ── 4. 查 ArchiveFile JOIN Archive 是否已挂载到 quota 域 ────────
        existing_mount = session.scalar(
            select(ArchiveFile)
            .join(Archive, ArchiveFile.archive_id == Archive.archive_id)
            .where(
                ArchiveFile.file_id == result.file_id,
                Archive.domain_type == "quota",
            )
        )
        if existing_mount is not None:
            existing_archive = session.get(Archive, existing_mount.archive_id)
            items.append(QuotaUploadItem(
                filename=filename,
                status="duplicate",
                file_id=result.file_id,
                archive_id=existing_mount.archive_id,
                title=existing_archive.title if existing_archive else None,
                already_exists=True,
            ))
            succeeded += 1
            continue

        # ── 5. 构造 title 与 sha256-based business_key ────────────────
        title = Path(filename).stem or filename
        business_key = f"quota:manual-upload:{result.sha256}"

        # ── 6. create_archive + attach_file（含空 Archive 补偿）────────
        archive_created_by_us = False
        try:
            try:
                archive = create_archive(
                    session,
                    domain_type="quota",
                    channel_type="manual_upload",
                    collection_method="manual_denovo",
                    business_key=business_key,
                    title=title,
                    source_id=source_id,
                    tenant_code="platform_public",
                    visibility_scope="public",
                    status="pending_tag",
                    metadata={"category": category_cell},
                    field_sources=_build_archive_field_sources(
                        title=title, business_key=business_key),
                )
                archive_created_by_us = True
            except ValueError as ve:
                if "ARCHIVE_BUSINESS_KEY_EXISTS" in str(ve):
                    # 极小概率：sha256 相同但步骤 4 未命中（理论不应发生，防御）
                    archive = session.scalar(
                        select(Archive).where(Archive.business_key == business_key)
                    )
                    if archive is None:
                        raise
                else:
                    raise

            _attach_archive_file(
                session,
                archive_id=archive.archive_id,
                file_id=result.file_id,
                file_role="main_document",   # 恒为 main_document，不按 category 映射
                is_primary=True,
                sort_order=10,
                display_name=filename,
                actor_type="user",
                actor_id="ui:quota-upload",
            )
        except Exception as attach_exc:  # noqa: BLE001
            # 防空 Archive 补偿：attach 失败时若 Archive 是本次新建的，回滚掉
            if archive_created_by_us:
                try:
                    _cleanup_empty_archive(session, archive.archive_id)
                except Exception:  # noqa: BLE001
                    session.rollback()
            items.append(QuotaUploadItem(
                filename=filename, status="failed",
                error=f"ATTACH_FAILED: {attach_exc}",
            ))
            failed += 1
            continue

        items.append(QuotaUploadItem(
            filename=filename,
            status="created",
            file_id=result.file_id,
            archive_id=archive.archive_id,
            title=title,
            already_exists=False,
        ))
        succeeded += 1

    body = QuotaUploadResponse(
        count=len(items),
        succeeded=succeeded,
        failed=failed,
        items=items,
    )
    return _json_ok(response, body)


# ── P0-4B · quota_parser integration (2026-07-28, web-frontend SPEC §5) ─────
# 7 个新端点：
#   POST   /archives/{archive_id}/parse           触发解析（阶段 A）
#   GET    /archives/{archive_id}/candidate.xlsx  下载 candidate
#   POST   /archives/{archive_id}/reviewed        上传 reviewed（阶段 B）
#   GET    /archives/{archive_id}/final.xlsx      下载 final
#   GET    /archives/{archive_id}/manifest        拿 Manifest JSON
#   GET    /archives/{archive_id}/qa-report       拿 QA 报告（json/md）
#   POST   /archives/{archive_id}/parse/delete    删除解析结果（清 MinIO + parse_* 字段）
#
# Mock 模式（QUOTA_PARSE_MOCK=1）下：解析引擎走 app.mock_parse_runner（异步后台）；
# 否则走真 quota_parser worker（未来批次）。


@router.post("/archives/{archive_id}/parse")
async def trigger_quota_parse(
    response: Response,
    archive_id: str,
    body: dict | None = None,
    session: Session = Depends(get_db_session),
):
    """触发阶段 A 解析（异步后台跑）。

    QUOTA_PARSE_MOCK=1 → 后台 mock runner；否则 501（真 worker 尚未上线）。
    重新解析走同一端点（web-frontend SPEC §3.1.3 共用端点决策）。
    """
    from app.quota_parser import is_parse_mock, trigger_parse as _trigger

    archive = session.get(Archive, archive_id)
    if archive is None:
        raise HTTPException(404, detail="ARCHIVE_NOT_FOUND")
    if archive.domain_type != "quota":
        raise HTTPException(400, detail="NOT_A_QUOTA_ARCHIVE")

    profile = (body or {}).get("profile")
    try:
        archive = _trigger(archive, profile=profile)
    except ValueError as e:
        msg = str(e)
        if "profile" in msg:
            raise HTTPException(422, detail=f"INVALID_PROFILE: {msg}")
        raise HTTPException(409, detail=f"ALREADY_PARSING_OR_DONE: {msg}")

    session.commit()
    session.refresh(archive)

    # 触发后台解析（mock 模式下用 mock runner）
    if is_parse_mock():
        from app.mock_parse_runner import schedule_mock_a

        schedule_mock_a(archive_id)
    else:
        # 真 worker 上线前返回 501（避免静默失败）
        raise HTTPException(
            501,
            detail="REAL_WORKER_NOT_IMPLEMENTED: set QUOTA_PARSE_MOCK=1 to use mock",
        )

    from app.quota_parser.service import build_parse_section

    return _json_ok(response, {
        "archive_id": archive.archive_id,
        "status": archive.status,
        "parse": build_parse_section(archive),
    })


@router.get("/archives/{archive_id}/candidate.xlsx")
def get_candidate_xlsx(
    archive_id: str,
    session: Session = Depends(get_db_session),
):
    """下载 candidate.xlsx（QUOTA_PARSE_MOCK=1 时返回 mock fixture）。"""
    from urllib.parse import quote

    from app.quota_parser import PARSE_BUCKET_CANDIDATE
    from app.storage import get_object_store

    archive = session.get(Archive, archive_id)
    if archive is None:
        raise HTTPException(404, detail="ARCHIVE_NOT_FOUND")
    if not archive.candidate_xlsx_key:
        raise HTTPException(404, detail="CANDIDATE_NOT_READY")
    store = get_object_store()
    data = store.get_object(PARSE_BUCKET_CANDIDATE, archive.candidate_xlsx_key)
    # 用 archive_id 作 ascii-safe filename，中文 title 用 RFC 5987 扩展
    filename_ascii = f"{archive_id}-candidate.xlsx"
    filename_utf8 = quote(f"{archive.title}-candidate.xlsx")
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{filename_ascii}\"; "
                f"filename*=UTF-8''{filename_utf8}"
            ),
        },
    )


@router.post("/archives/{archive_id}/reviewed")
async def upload_reviewed_xlsx(
    response: Response,
    archive_id: str,
    file: UploadFile = File(...),
    session: Session = Depends(get_db_session),
):
    """上传 reviewed.xlsx → 阶段 B → 写 final_xlsx_key。

    QUOTA_PARSE_MOCK=1 → 后台 mock runner；否则调 quota_parser.finalize_reviewed_xlsx。
    """
    from app.quota_parser import is_parse_mock
    from app.quota_parser.service import (
        InvalidReviewedXlsxError,
        QuotaParserStageBError,
        build_parse_section,
        upload_reviewed as _upload,
    )

    archive = session.get(Archive, archive_id)
    if archive is None:
        raise HTTPException(404, detail="ARCHIVE_NOT_FOUND")
    if archive.domain_type != "quota":
        raise HTTPException(400, detail="NOT_A_QUOTA_ARCHIVE")
    if archive.parse_status != "parsed":
        raise HTTPException(
            409,
            detail=f"ARCHIVE_NOT_READY_FOR_REVIEW: parse_status={archive.parse_status}",
        )

    reviewed_bytes = await file.read()
    if not reviewed_bytes:
        raise HTTPException(422, detail="EMPTY_FILE")

    if is_parse_mock():
        # mock 模式：跳过结构校验，2-3s 后推到 qa_passed
        from app.mock_parse_runner import schedule_mock_b

        schedule_mock_b(archive_id, reviewed_bytes=reviewed_bytes)
        return _json_ok(response, {
            "archive_id": archive_id,
            "scheduled": True,
            "note": "mock mode — review 校验已跳过；2-3s 后 status=qa_passed",
        })

    # 真模式：调 quota_parser.finalize_reviewed_xlsx
    try:
        archive = _upload(archive, reviewed_bytes)
    except InvalidReviewedXlsxError as e:
        session.rollback()
        raise HTTPException(
            status_code=422,
            detail={
                "code": e.code,
                "reason": str(e),
                "sheet": e.sheet,
                "column_index": e.column_index,
            },
        )
    except QuotaParserStageBError as e:
        session.rollback()
        raise HTTPException(status_code=422, detail=f"FINALIZE_FAILED: {e}")

    session.commit()
    session.refresh(archive)
    return _json_ok(response, {
        "archive_id": archive.archive_id,
        "parse": build_parse_section(archive),
    })


@router.get("/archives/{archive_id}/final.xlsx")
def get_final_xlsx(
    archive_id: str,
    session: Session = Depends(get_db_session),
):
    """下载 final.xlsx。"""
    from urllib.parse import quote

    from app.quota_parser import PARSE_BUCKET_CANDIDATE
    from app.storage import get_object_store

    archive = session.get(Archive, archive_id)
    if archive is None:
        raise HTTPException(404, detail="ARCHIVE_NOT_FOUND")
    if not archive.final_xlsx_key:
        raise HTTPException(404, detail="FINAL_NOT_READY")
    store = get_object_store()
    data = store.get_object(PARSE_BUCKET_CANDIDATE, archive.final_xlsx_key)
    filename_ascii = f"{archive_id}-final.xlsx"
    filename_utf8 = quote(f"{archive.title}-final.xlsx")
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{filename_ascii}\"; "
                f"filename*=UTF-8''{filename_utf8}"
            ),
        },
    )


@router.get("/archives/{archive_id}/manifest")
def get_manifest(
    response: Response,
    archive_id: str,
    session: Session = Depends(get_db_session),
):
    """返回 Manifest JSON（quota-parser-result/v1 schema）。

    QUOTA_PARSE_MOCK=1 → 返回 mock fixture。
    """
    from app.quota_parser.service import _reconstruct_manifest
    from app.mock_parse_runner import fake_manifest
    from app.quota_parser import is_parse_mock

    archive = session.get(Archive, archive_id)
    if archive is None:
        raise HTTPException(404, detail="ARCHIVE_NOT_FOUND")
    if archive.parse_status is None:
        raise HTTPException(404, detail="MANIFEST_NOT_FOUND")

    manifest = (
        fake_manifest(archive_id, parse_status=archive.parse_status)
        if is_parse_mock()
        else _reconstruct_manifest(archive)
    )
    return _json_ok(response, manifest)


@router.get("/archives/{archive_id}/qa-report")
def get_qa_report(
    response: Response,
    archive_id: str,
    format: str = Query("json", pattern="^(json|md)$"),
    session: Session = Depends(get_db_session),
):
    """返回 QA 报告（json / md 切换）。

    QUOTA_PARSE_MOCK=1 → 返回 mock fixture。
    """
    from app.mock_parse_runner import fake_qa_report_json, fake_qa_report_md
    from app.quota_parser import is_parse_mock

    archive = session.get(Archive, archive_id)
    if archive is None:
        raise HTTPException(404, detail="ARCHIVE_NOT_FOUND")
    if archive.parse_status is None:
        raise HTTPException(404, detail="QA_REPORT_NOT_FOUND")

    if format == "md":
        body = fake_qa_report_md(archive_id) if is_parse_mock() else _format_qa_report_md(archive)
        return Response(content=body, media_type="text/markdown; charset=utf-8")

    body = fake_qa_report_json(archive_id) if is_parse_mock() else _format_qa_report_json(archive)
    return _json_ok(response, body)


@router.post("/archives/{archive_id}/parse/delete")
def delete_parse_result(
    response: Response,
    archive_id: str,
    session: Session = Depends(get_db_session),
):
    """删除解析结果：清 Archive.parse_* 字段（MinIO 产物留待运维 GC）。

    web-frontend SPEC §3.1.4 危险操作 — 前端 modal 必填档案标题确认 + 审计。

    注：今天 v0.3 不在 ObjectStore Protocol 里加 delete_object（避免破坏 S3ObjectStore）。
    MinIO 上的 candidate.xlsx / final.xlsx 由运维按 archive lifecycle 定期清理；
    这里只清 DB 侧产物指针（parse_* 13 列 + candidate_xlsx_key / final_xlsx_key）。
    后续批次给 ObjectStore 加 delete_object 后，改为同步清理 MinIO。
    """
    archive = session.get(Archive, archive_id)
    if archive is None:
        raise HTTPException(404, detail="ARCHIVE_NOT_FOUND")
    if archive.parse_status is None:
        raise HTTPException(404, detail="NOTHING_TO_DELETE")

    # 1. 写审计日志（best-effort）
    try:
        from app.models import AuditLog
        audit = AuditLog(
            actor_type="api",
            action="quota_parse_result_deleted",
            target_type="archive",
            target_id=archive.archive_id,
        )
        session.add(audit)
    except Exception:
        pass

    # 2. 清 Archive.parse_* 字段
    archive.parse_status = None
    archive.parse_profile = None
    archive.parse_task_id = None
    archive.parse_phase = None
    archive.parse_parser_version = None
    archive.parse_started_at = None
    archive.parse_finished_at = None
    archive.parse_metrics = None
    archive.parse_warnings = None
    archive.parse_error_code = None
    archive.parse_error_message = None
    archive.candidate_xlsx_key = None
    archive.final_xlsx_key = None
    session.commit()

    return _json_ok(response, {
        "deleted": True,
        "archive_id": archive_id,
        "note": "MinIO 上的 candidate/final.xlsx 留待运维 GC",
    })


def _format_qa_report_md(archive: Archive) -> str:
    """真模式下的 QA 报告 markdown（占位 — 真 worker 上线后由 quota_parser 包生成）。"""
    return (
        f"# QA Report\n\n"
        f"- archive_id: `{archive.archive_id}`\n"
        f"- parser_version: {archive.parse_parser_version or 'unknown'}\n"
        f"- status: {archive.parse_status}\n\n"
        f"_尚未实现真模式 QA 报告生成；待 quota_parser worker 上线。_\n"
    )


def _format_qa_report_json(archive: Archive) -> dict:
    """真模式下的 QA 报告 JSON（占位 — 真 worker 上线后由 quota_parser 包生成）。"""
    return {
        "$schema": "quota-parser-qa/v1",
        "task_id": archive.parse_task_id,
        "summary": "ok",
        "checks": [],
        "note": "尚未实现真模式 QA 报告生成；待 quota_parser worker 上线。",
    }
