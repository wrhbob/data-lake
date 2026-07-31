#!/usr/bin/env python3
"""verify_db_schema — 比对 Python 侧 ARCHIVE_FILE_ROLES 与 PG 侧 ck_archive_file_role CheckConstraint

防 v0.5 教训 (2026-07-30):改了 ARCHIVE_FILE_ROLES set 但漏跑 ALTER,
导致 worker 写 parse_markdown 时被 DB CheckConstraint 拒 → 整 job failed_permanent。
此脚本可手跑 / CI 跑,失败时打印完整修复 SQL 可直接复制。

用法:
  python scripts/verify_db_schema.py
退出码:
  0 = 一致
  1 = 不一致 (脚本已打印修复 SQL)
  2 = 读 DB 失败 (DB 连不上 / 表不存在)
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# 加载 .env (与 web/worker 启动方式一致)
ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
if ENV_FILE.exists():
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

# 加载 file_asset_service 包
sys.path.insert(0, str(ROOT / "file_asset_service"))


def _fetch_db_roles() -> set[str] | None:
    """从 PG 读 ck_archive_file_role 定义,解析出 role 集合。失败返回 None。"""
    try:
        from sqlalchemy import text
        from app.database import get_engine
    except Exception as e:
        print(f"[ERR] 加载 SQLAlchemy / database 失败: {e}")
        return None
    try:
        with get_engine().connect() as c:
            rows = c.execute(text("""
                SELECT pg_get_constraintdef(oid)
                FROM pg_constraint
                WHERE conname = 'ck_archive_file_role'
            """)).all()
    except Exception as e:
        print(f"[ERR] DB 查询失败: {e}")
        return None
    if not rows:
        print("[WARN] ck_archive_file_role 不存在 (新库?)")
        return set()
    defn = rows[0][0]
    roles = set(re.findall(r"'([a-z_]+)'::character varying", defn))
    roles |= set(re.findall(r"'([a-z_]+)'::varchar", defn))
    return roles


def _fetch_python_roles() -> set[str]:
    from app.archive_rules import ARCHIVE_FILE_ROLES
    return set(ARCHIVE_FILE_ROLES)


def _build_fix_sql(python_roles: set[str], db_roles: set[str]) -> str:
    """生成一段 DROP+ADD CheckConstraint SQL,统一到 Python 侧。"""
    all_sorted = sorted(python_roles)
    inner = ",\n            ".join(f"'{r}'" for r in all_sorted)
    return f"""-- verify_db_schema.py 自动生成的修复 SQL
-- 跑前请确认 Python 侧 ARCHIVE_FILE_ROLES 是最新版本 (git log app/archive_rules.py)
ALTER TABLE archive_file DROP CONSTRAINT IF EXISTS ck_archive_file_role;
ALTER TABLE archive_file ADD CONSTRAINT ck_archive_file_role CHECK (file_role IN (
            {inner}
));-- end"""


def main() -> int:
    print("=" * 60)
    print("verify_db_schema — 比对 ARCHIVE_FILE_ROLES ↔ ck_archive_file_role")
    print("=" * 60)
    print()

    py_roles = _fetch_python_roles()
    print(f"[Python] ARCHIVE_FILE_ROLES ({len(py_roles)} 个):")
    for r in sorted(py_roles):
        print(f"  - {r}")
    print()

    db_roles = _fetch_db_roles()
    if db_roles is None:
        return 2
    print(f"[DB]    ck_archive_file_role ({len(db_roles)} 个):")
    for r in sorted(db_roles):
        print(f"  - {r}")
    print()

    only_in_py = py_roles - db_roles
    only_in_db = db_roles - py_roles
    common = py_roles & db_roles

    print(f"[diff] 共同: {len(common)}")
    if only_in_py:
        print(f"[diff] Python 侧有 / DB 侧缺 ({len(only_in_py)}):")
        for r in sorted(only_in_py):
            print(f"  ❌ {r}")
    if only_in_db:
        print(f"[diff] DB 侧有 / Python 侧缺 ({len(only_in_db)}):")
        for r in sorted(only_in_db):
            print(f"  ⚠️  {r}")
    print()

    if not only_in_py and not only_in_db:
        print("✅ 一致,无需修复")
        return 0

    print("❌ 不一致!复制下面这段 SQL 跑:")
    print("-" * 60)
    print(_build_fix_sql(py_roles, db_roles))
    print("-" * 60)
    return 1


if __name__ == "__main__":
    sys.exit(main())