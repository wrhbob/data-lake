"""quota_lake 测试 fixtures."""

import pytest


@pytest.fixture()
def db_session():
    """SQLite in-memory session with all Layer 0 + Layer 1 tables."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from quota_lake.db.migrations import migrate_quota_lake_tables

    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    migrate_quota_lake_tables(engine)
    Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    with Session() as session:
        yield session


@pytest.fixture()
def sample_grid_aa0001():
    """模拟 AA0001-AA0004 的 HTML 表格 (匹配真实 PDF 表格结构).

    真实表格结构:
    - 列 0-3: 行标签区 (标头 / category / name / unit / price)
    - 列 4+: 编号+数据区 (codes + per-code quantities)

    编号行: ["定额编号", "", "", "", "AA0001", "AA0002", "AA0003", "AA0004"]
    资源行: ["人工", "混合工", "工日", "115.0", "1.817", "2.051", "2.805", "3.039"]
    """
    return [
        ["定额编号", "", "", "", "AA0001", "AA0002", "AA0003", "AA0004"],
        ["项目", "", "", "", "一、二类土", "一、二类土", "三类土", "三类土"],
        ["", "", "", "", "深2m以内", "深4m以内", "深2m以内", "深4m以内"],
        ["单位:10m3", "", "", "", "", "", "", ""],
        ["基价(元)", "", "", "", "208.98", "235.90", "322.62", "349.54"],
        ["人工费(元)", "", "", "", "208.98", "235.90", "322.62", "349.54"],
        ["人工", "混合工", "工日", "115.0", "1.817", "2.051", "2.805", "3.039"],
    ]


@pytest.fixture()
def sample_items():
    """从 sample_dinge.json 加载的 4 个黄金测试 item."""
    import json
    import os

    sample_path = os.path.join(
        os.path.dirname(__file__),
        "..", "..", "..", "quota", "四川2025", "sample_dinge.json",
    )
    # Normalize path to absolute
    sample_path = os.path.abspath(sample_path)
    if os.path.isfile(sample_path):
        with open(sample_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("items", [])
    return []
