"""幂等迁移 — 使用 checkfirst=True 创建所有 Layer 1 表."""

from sqlalchemy.engine import Engine

from quota_lake.db.models import (
    ChapterNote,
    CoeffRule,
    Consumption,
    QAPrintedPrice,
    QuotaBook,
    QuotaItem,
    ResourceMaster,
)

_LAYER1_MODELS = [
    QuotaBook,
    QuotaItem,
    ResourceMaster,
    Consumption,
    QAPrintedPrice,
    ChapterNote,
    CoeffRule,
]


def migrate_quota_lake_tables(engine: Engine) -> None:
    """幂等创建所有 Layer 1 表。checkfirst=True 保证重复调用安全。"""
    for model in _LAYER1_MODELS:
        model.__table__.create(engine, checkfirst=True)
