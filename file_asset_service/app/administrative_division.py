"""
P0-4A · 公共行政区划字典 / Administrative Divisions (GB/T 2260-2024).

Layer-0 shared dictionary endpoint used by quota, cost_info, trading and
other domains that need province/city lookup.
"""

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.database import get_db_session
from app.models import AdministrativeDivision

router = APIRouter(prefix="/api/data-lake/administrative-divisions", tags=["administrative-divisions"])


def _query_divisions(
    session: Session,
    level: str | None = None,
    parent_code: str | None = None,
    q: str | None = None,
) -> list[AdministrativeDivision]:
    stmt = select(AdministrativeDivision).where(AdministrativeDivision.enabled.is_(True))
    if level:
        stmt = stmt.where(AdministrativeDivision.level == level)
    if parent_code:
        stmt = stmt.where(AdministrativeDivision.parent_code == parent_code)
    if q:
        stmt = stmt.where(AdministrativeDivision.name.ilike(f"%{q}%"))
    stmt = stmt.order_by(AdministrativeDivision.sort_order, AdministrativeDivision.code)
    return list(session.execute(stmt).scalars().all())


def _serialize(div: AdministrativeDivision) -> dict:
    return {
        "code": div.code,
        "name": div.name,
        "level": div.level,
        "parent_code": div.parent_code,
        "enabled": div.enabled,
        "version": div.version,
        "sort_order": div.sort_order,
    }


@router.get("")
def list_administrative_divisions(
    response: Response,
    level: str | None = Query(None, description="country | province | city"),
    parent_code: str | None = Query(None, description="parent code, e.g. 510000 for Sichuan"),
    q: str | None = Query(None, description="name search (ilike)"),
    session: Session = Depends(get_db_session),
) -> dict:
    items = _query_divisions(session, level=level, parent_code=parent_code, q=q)
    response.headers["Content-Type"] = "application/json; charset=utf-8"
    return {
        "items": [_serialize(d) for d in items],
        "total": len(items),
    }
