"""Quota archive 批量删除工具（CLI）。

设计目标
--------
1. 默认 DRY-RUN：不带 --apply 不会改任何东西，只打印「将要删什么 + 影响行数」。
2. 按 FK 链反向级联删除：outbox → archive_event → archive_file → archive。
3. 不动 file_asset 与 MinIO 原件（保留可重传的原始 PDF）。
4. 仅作用于 quota domain（默认）；--domain-type 切换但需显式声明。
5. --title-pattern 或 --archive-ids 二选一；为空报错退出。
6. 用 .env 里的 FILE_ASSET_DATABASE_URL（与主服务一致）。

用法
----
# 1) 看哪些会被删（dry-run）
python scripts/quota_archive_delete.py --title-pattern 'smoke%'

# 2) 按 archive_id 列表删（dry-run）
python scripts/quota_archive_delete.py \
    --archive-ids 105067e0-f65d-4b97-a6b6-042e57269e47,14b3a93e-633c-4488-8471-8044e6614830

# 3) 实际执行
python scripts/quota_archive_delete.py --title-pattern 'smoke%' --apply

# 4) 删非 quota 域（必须显式声明，且建议 dry-run 先验）
python scripts/quota_archive_delete.py \
    --title-pattern 'foo%' --domain-type cost_info --apply

FK 链（参考 models.py / docs/db_schema.sql）
-------------------------------------------
archive
 ├── archive_event (FK archive_id)        ← outbox.event_id → archive_event.event_id
 │    └── outbox  (FK event_id)
 ├── archive_file (FK archive_id)
 ├── quota_archive_profile (FK archive_id) ← quota 域特有；domain_type=quota 时删
 └── quota_publication_set  ← 仅当 pubset 不再被任何 profile 引用时才删（不在本工具范围）

本脚本只处理 quota 域；其它域如有需要，复用 _cascade_delete_archive() 即可。
"""
from __future__ import annotations

import argparse
import os
import sys
from contextlib import contextmanager
from typing import Iterable

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker


ENV_FILE_CANDIDATES = (".env", "../.env", "file_asset_service/../.env")


def _load_env_file(path: str) -> None:
    """最小 .env 加载（避免引外部依赖）。只覆盖未设置的变量。"""
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def load_env() -> str | None:
    for cand in ENV_FILE_CANDIDATES:
        _load_env_file(cand)
    return os.environ.get("FILE_ASSET_DATABASE_URL")


def make_engine(db_url: str) -> Engine:
    return create_engine(db_url, future=True)


@contextmanager
def session_scope(factory):
    s = factory()
    try:
        yield s
    finally:
        s.close()


def find_target_archives(
    session,
    *,
    domain_type: str,
    title_pattern: str | None,
    archive_ids: Iterable[str] | None,
) -> list[dict]:
    """返回将被删除的 archive 行（含 archive_id / title / status / parse_status / 关联计数）。"""
    if title_pattern:
        rows = session.execute(
            text(
                """
                SELECT a.archive_id,
                       a.title,
                       a.status,
                       a.parse_status,
                       a.region_code,
                       a.created_at,
                       (SELECT COUNT(*) FROM archive_file af WHERE af.archive_id = a.archive_id) AS file_count,
                       (SELECT COUNT(*) FROM archive_event ae WHERE ae.archive_id = a.archive_id) AS event_count,
                       (SELECT COUNT(*) FROM quota_archive_profile qp WHERE qp.archive_id = a.archive_id) AS profile_count
                  FROM archive a
                 WHERE a.domain_type = :dt
                   AND a.title LIKE :pat
                 ORDER BY a.created_at
                """
            ),
            {"dt": domain_type, "pat": title_pattern},
        ).all()
    else:
        id_list = list(archive_ids or [])
        if not id_list:
            return []
        rows = session.execute(
            text(
                """
                SELECT a.archive_id,
                       a.title,
                       a.status,
                       a.parse_status,
                       a.region_code,
                       a.created_at,
                       (SELECT COUNT(*) FROM archive_file af WHERE af.archive_id = a.archive_id) AS file_count,
                       (SELECT COUNT(*) FROM archive_event ae WHERE ae.archive_id = a.archive_id) AS event_count,
                       (SELECT COUNT(*) FROM quota_archive_profile qp WHERE qp.archive_id = a.archive_id) AS profile_count
                  FROM archive a
                 WHERE a.domain_type = :dt
                   AND a.archive_id = ANY(:ids)
                 ORDER BY a.created_at
                """
            ),
            {"dt": domain_type, "ids": id_list},
        ).all()
    return [dict(r._mapping) for r in rows]


def count_impacted_rows(session, archive_ids: list[str]) -> dict[str, int]:
    """统计下游表将被删的行数（用于 dry-run 展示）。"""
    if not archive_ids:
        return {"outbox": 0, "archive_event": 0, "archive_file": 0, "quota_archive_profile": 0}
    out = {}
    out["archive_event"] = session.execute(
        text("SELECT COUNT(*) FROM archive_event WHERE archive_id = ANY(:ids)"),
        {"ids": archive_ids},
    ).scalar()
    out["outbox"] = session.execute(
        text(
            "SELECT COUNT(*) FROM outbox WHERE event_id IN "
            "(SELECT event_id FROM archive_event WHERE archive_id = ANY(:ids))"
        ),
        {"ids": archive_ids},
    ).scalar()
    out["archive_file"] = session.execute(
        text("SELECT COUNT(*) FROM archive_file WHERE archive_id = ANY(:ids)"),
        {"ids": archive_ids},
    ).scalar()
    out["quota_archive_profile"] = session.execute(
        text("SELECT COUNT(*) FROM quota_archive_profile WHERE archive_id = ANY(:ids)"),
        {"ids": archive_ids},
    ).scalar()
    return out


def cascade_delete_archive(
    session_factory,
    archive_ids: list[str],
    *,
    include_quota_profile: bool,
) -> dict[str, int]:
    """按 FK 链反向级联删除，返回各表删除行数。

    链顺序：outbox → archive_event → archive_file → [quota_archive_profile] → archive
    """
    if not archive_ids:
        return {"outbox": 0, "archive_event": 0, "archive_file": 0,
                "quota_archive_profile": 0, "archive": 0}

    deleted = {}
    with session_scope(session_factory) as s, s.begin():
        # 1. outbox (FK → archive_event.event_id)
        r = s.execute(
            text(
                "DELETE FROM outbox WHERE event_id IN "
                "(SELECT event_id FROM archive_event WHERE archive_id = ANY(:ids))"
            ),
            {"ids": archive_ids},
        )
        deleted["outbox"] = r.rowcount

        # 2. archive_event
        r = s.execute(
            text("DELETE FROM archive_event WHERE archive_id = ANY(:ids)"),
            {"ids": archive_ids},
        )
        deleted["archive_event"] = r.rowcount

        # 3. archive_file
        r = s.execute(
            text("DELETE FROM archive_file WHERE archive_id = ANY(:ids)"),
            {"ids": archive_ids},
        )
        deleted["archive_file"] = r.rowcount

        # 4. quota_archive_profile (optional, quota 域特有)
        if include_quota_profile:
            r = s.execute(
                text("DELETE FROM quota_archive_profile WHERE archive_id = ANY(:ids)"),
                {"ids": archive_ids},
            )
            deleted["quota_archive_profile"] = r.rowcount
        else:
            deleted["quota_archive_profile"] = 0

        # 5. archive
        r = s.execute(
            text("DELETE FROM archive WHERE archive_id = ANY(:ids)"),
            {"ids": archive_ids},
        )
        deleted["archive"] = r.rowcount
    return deleted


def _print_table(rows: list[dict], columns: list[tuple[str, str]]) -> None:
    """columns: [(key, header), ...]"""
    if not rows:
        print("  (无匹配记录)")
        return
    widths = {h: max(len(h), max(len(str(r.get(k, ""))[:48]) for r in rows)) for k, h in columns}
    print("  " + " | ".join(h.ljust(widths[h]) for _, h in columns))
    print("  " + "-+-".join("-" * widths[h] for _, h in columns))
    for r in rows:
        print("  " + " | ".join(str(r.get(k, ""))[:48].ljust(widths[h]) for k, h in columns))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Quota archive 批量删除工具（默认 dry-run，加 --apply 才真删）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--title-pattern", help="LIKE 模式，如 'smoke%%'")
    g.add_argument(
        "--archive-ids",
        help="逗号分隔的 archive_id 列表（UUID）",
    )
    parser.add_argument(
        "--domain-type",
        default="quota",
        help="限定 domain_type（默认 quota）。切到其它域必须显式声明。",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际执行删除；不加则只做 dry-run 展示。",
    )
    parser.add_argument(
        "--keep-quota-profile",
        action="store_true",
        help="quota 域默认会顺手删 quota_archive_profile；加此 flag 则保留。",
    )
    args = parser.parse_args(argv)

    db_url = load_env()
    if not db_url:
        print("ERROR: FILE_ASSET_DATABASE_URL 未配置。请确认 .env 在项目根或 file_asset_service/..", file=sys.stderr)
        return 2

    if args.domain_type != "quota":
        print(f"⚠️  domain_type={args.domain_type!r} ≠ 'quota'。本工具默认仅服务 quota 域。", file=sys.stderr)
        print("   仍会继续，但请确认这是你想要的。", file=sys.stderr)

    engine = make_engine(db_url)
    Session = sessionmaker(bind=engine, future=True)

    ids: list[str] | None = None
    if args.archive_ids:
        ids = [s.strip() for s in args.archive_ids.split(",") if s.strip()]
        if not ids:
            print("ERROR: --archive-ids 解析后为空", file=sys.stderr)
            return 2

    with session_scope(Session) as s:
        targets = find_target_archives(
            s,
            domain_type=args.domain_type,
            title_pattern=args.title_pattern,
            archive_ids=ids,
        )

    if not targets:
        print(f"[dry-run] domain_type={args.domain_type!r} 无匹配 archive。"
              f"{' title LIKE ' + (args.title_pattern or '') if args.title_pattern else ''}"
              f"{' ids=' + ','.join(ids) if ids else ''}")
        return 0

    target_ids = [t["archive_id"] for t in targets]
    print(f"[{'APPLY' if args.apply else 'DRY-RUN'}] domain_type={args.domain_type!r}, "
          f"命中 archive 数: {len(targets)}")
    _print_table(
        targets,
        [
            ("archive_id", "archive_id"),
            ("title", "title"),
            ("region_code", "region_code"),
            ("status", "status"),
            ("parse_status", "parse_status"),
            ("file_count", "files"),
            ("event_count", "events"),
            ("profile_count", "profiles"),
        ],
    )

    with session_scope(Session) as s:
        impacted = count_impacted_rows(s, target_ids)

    print("\n下游表将受影响行数：")
    for k, v in impacted.items():
        print(f"  {k}: {v}")
    if impacted["quota_archive_profile"] and not args.keep_quota_profile:
        print("  (默认会一并删除 quota_archive_profile；--keep-quota-profile 可保留)")

    print("\n保留不动：file_asset、MinIO 原件、其它域的 archive。")

    if not args.apply:
        print("\n未执行删除（dry-run）。加 --apply 真正执行。")
        return 0

    print("\n开始执行...")
    try:
        deleted = cascade_delete_archive(
            Session,
            target_ids,
            include_quota_profile=(not args.keep_quota_profile),
        )
    except SQLAlchemyError as e:
        print(f"❌ 删除失败：{e}", file=sys.stderr)
        return 1

    print("✓ 删除完成。各表实际删除行数：")
    for k, v in deleted.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())