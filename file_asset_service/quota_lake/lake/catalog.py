"""册元数据登记 — 创建 quota_book 行, 维护版本 lineage."""

from dataclasses import dataclass
from datetime import date
from typing import Optional
from uuid import uuid4

from sqlalchemy.orm import Session

from quota_lake.db.models import QuotaBook
from quota_lake.lake.state_machine import BookStatus


@dataclass
class BookRegistration:
    """用于登记的册信息."""
    province: str
    domain: str
    edition_year: int
    title: str
    cost_scope: str       # comprehensive | direct | other
    book_kind: str         # base | adjust | fee | mapping | errata
    doc_number: str | None = None
    effective_date: date | None = None
    publication_set_id: str | None = None
    parent_book_id: str | None = None
    parse_profile: str | None = None


def register_book(
    session: Session,
    reg: BookRegistration,
) -> QuotaBook:
    """在数据库中创建一个定额册.

    Args:
        session: SQLAlchemy Session.
        reg: 册登记信息 (cost_scope 和 book_kind 必填).

    Returns:
        QuotaBook 实例 (已 flush, 未 commit).

    Raises:
        ValueError: cost_scope 或 book_kind 无效.
    """
    valid_scopes = {"comprehensive", "direct", "other"}
    valid_kinds = {"base", "adjust", "fee", "mapping", "errata"}

    if reg.cost_scope not in valid_scopes:
        raise ValueError(f"cost_scope 无效: {reg.cost_scope}, 允许: {valid_scopes}")
    if reg.book_kind not in valid_kinds:
        raise ValueError(f"book_kind 无效: {reg.book_kind}, 允许: {valid_kinds}")

    book = QuotaBook(
        id=str(uuid4()),
        publication_set_id=reg.publication_set_id,
        province=reg.province,
        domain=reg.domain,
        edition_year=reg.edition_year,
        title=reg.title,
        doc_number=reg.doc_number,
        effective_date=reg.effective_date,
        cost_scope=reg.cost_scope,
        book_kind=reg.book_kind,
        parent_book=reg.parent_book_id,
        parse_profile=reg.parse_profile,
        status=BookStatus.DRAFT.value,
    )
    session.add(book)
    session.flush()
    return book


def supersede_book(
    session: Session,
    book_id: str,
    new_book_id: str,
) -> None:
    """标记旧册被新册取代 (维护 version lineage).

    Args:
        session: SQLAlchemy Session.
        book_id: 被取代的册 ID.
        new_book_id: 新册 ID.
    """
    book = session.get(QuotaBook, book_id)
    if book:
        book.superseded_by = new_book_id
        session.flush()
