"""Layer 1 quota data lake ORM models.

These models map to the DDL defined in 全链路技术规格 §3.3. They represent the
parsed, structured layer of the quota knowledge graph -- quota books, items,
consumption lines, printed prices, resource masters, chapter notes, and
coefficient rules.

All models use ``app.models.Base`` as the declarative base for consistency with
the Layer 0 archive models.
"""

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base, new_id, utcnow


# ---------------------------------------------------------------------------
# 1. QuotaBook -- 定额册 (the container / publication unit)
# ---------------------------------------------------------------------------

class QuotaBook(Base):
    __tablename__ = "quota_book"
    __table_args__ = (
        UniqueConstraint("province", "domain", "edition_year", "title", name="uq_quota_book_natural"),
        CheckConstraint("cost_scope in ('comprehensive','direct','other')", name="ck_quota_book_cost_scope"),
        CheckConstraint("book_kind in ('base','adjust','fee','mapping','errata')", name="ck_quota_book_book_kind"),
        Index("ix_quota_book_province_year", "province", "edition_year"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    publication_set_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("quota_publication_set.publication_set_id"), nullable=True, index=True
    )
    province: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    edition_year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    doc_number: Mapped[str | None] = mapped_column(String(128))
    effective_date: Mapped[date | None] = mapped_column(Date)
    cost_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    book_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    parent_book: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("quota_book.id"), nullable=True, index=True
    )
    superseded_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("quota_book.id"), nullable=True, index=True
    )
    parse_profile: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    # relationships
    items: Mapped[list["QuotaItem"]] = relationship(back_populates="book")
    chapter_notes: Mapped[list["ChapterNote"]] = relationship(back_populates="book")
    coeff_rules: Mapped[list["CoeffRule"]] = relationship(back_populates="book")
    publication_set = relationship("QuotaPublicationSet")
    parent = relationship("QuotaBook", remote_side=[id], foreign_keys=[parent_book])
    superseded = relationship("QuotaBook", remote_side=[id], foreign_keys=[superseded_by])


# ---------------------------------------------------------------------------
# 2. QuotaItem -- 定额子目 (the atomic quota line-item)
# ---------------------------------------------------------------------------

class QuotaItem(Base):
    __tablename__ = "quota_item"
    __table_args__ = (
        UniqueConstraint("book_id", "code", name="uq_quota_item_book_code"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    book_id: Mapped[str] = mapped_column(String(36), ForeignKey("quota_book.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    name_parts: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    unit_base: Mapped[str | None] = mapped_column(String(32))
    unit_std: Mapped[str | None] = mapped_column(String(32))
    unit_display: Mapped[str | None] = mapped_column(String(32))
    work_content: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    chapter_path: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    list_code_prefix: Mapped[str | None] = mapped_column(String(32))
    source_page: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    # relationships
    book: Mapped["QuotaBook"] = relationship(back_populates="items")
    consumption_lines: Mapped[list["Consumption"]] = relationship(back_populates="quota_item")
    printed_prices: Mapped[list["QAPrintedPrice"]] = relationship(back_populates="quota_item")


# ---------------------------------------------------------------------------
# 3. Consumption -- 消耗量 (quantity-per-unit; 量价分离原则)
# ---------------------------------------------------------------------------

class Consumption(Base):
    __tablename__ = "consumption"
    __table_args__ = (
        UniqueConstraint("quota_id", "resource_id", name="uq_consumption_quota_resource"),
    )

    quota_id: Mapped[str] = mapped_column(String(36), ForeignKey("quota_item.id"), primary_key=True)
    resource_id: Mapped[str] = mapped_column(String(36), ForeignKey("resource_master.id"), primary_key=True)
    quantity: Mapped[float] = mapped_column(Float, nullable=True)
    in_paren: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_page: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    # relationships
    quota_item: Mapped["QuotaItem"] = relationship(back_populates="consumption_lines")
    resource: Mapped["ResourceMaster"] = relationship(back_populates="consumption_lines")


# ---------------------------------------------------------------------------
# 4. QAPrintedPrice -- 印刷基价 (QA checksum only, never drives business price)
# ---------------------------------------------------------------------------

class QAPrintedPrice(Base):
    __tablename__ = "qa_printed_price"
    __table_args__ = (
        UniqueConstraint("quota_id", "field", name="uq_qa_printed_price_quota_field"),
    )

    quota_id: Mapped[str] = mapped_column(String(36), ForeignKey("quota_item.id"), primary_key=True)
    field: Mapped[str] = mapped_column(String(32), primary_key=True)  # "base", "labor", "material", "machine"
    printed: Mapped[float] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    # relationships
    quota_item: Mapped["QuotaItem"] = relationship(back_populates="printed_prices")


# ---------------------------------------------------------------------------
# 5. ResourceMaster -- 资源主数据 (canonical labour/material/machinery registry)
# ---------------------------------------------------------------------------

class ResourceMaster(Base):
    __tablename__ = "resource_master"
    __table_args__ = (
        UniqueConstraint(
            "category", "name_std", "spec_std", "unit_std", "province_scope",
            name="uq_resource_master_natural",
        ),
        CheckConstraint("category in ('人工','材料','机械')", name="ck_resource_master_category"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    category: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    name_std: Mapped[str] = mapped_column(String(255), nullable=False)
    spec_std: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    unit_std: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    province_scope: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    aliases: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    # relationships
    consumption_lines: Mapped[list["Consumption"]] = relationship(back_populates="resource")


# ---------------------------------------------------------------------------
# 6. ChapterNote -- 章节说明 (chapter-level scope / calc rule / other notes)
# ---------------------------------------------------------------------------

class ChapterNote(Base):
    __tablename__ = "chapter_note"
    __table_args__ = (
        CheckConstraint("kind in ('scope','calc_rule','other')", name="ck_chapter_note_kind"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    book_id: Mapped[str] = mapped_column(String(36), ForeignKey("quota_book.id"), nullable=False, index=True)
    chapter_path: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="other")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source_page: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    # relationships
    book: Mapped["QuotaBook"] = relationship(back_populates="chapter_notes")


# ---------------------------------------------------------------------------
# 7. CoeffRule -- 系数规则 (adjustment coefficient rules extracted from text)
# ---------------------------------------------------------------------------

class CoeffRule(Base):
    __tablename__ = "coeff_rule"
    __table_args__ = ()

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    book_id: Mapped[str] = mapped_column(String(36), ForeignKey("quota_book.id"), nullable=False, index=True)
    trigger: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    action: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    scope: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_text: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    # relationships
    book: Mapped["QuotaBook"] = relationship(back_populates="coeff_rules")
