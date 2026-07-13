"""Reversible teardown for the P0-2 quota schema (SPEC-QA-001 §13.1, decision D4).

Safeguards:
- dev mode (default): refuse when any quota table is non-empty or ``archive_file``'s
  new quota columns already carry business values; only when clean does it drop the
  new indexes, columns and tables. Protects development/test data from loss.
- prod mode (``--prod``): non-destructive. Keep every new table and column; only drop
  the newly added lossless indexes so an application version can be reverted cleanly.

Usage::

    python -m app.quota_migration downgrade           # dev (guarded, destructive when clean)
    python -m app.quota_migration downgrade --prod     # prod (index-only, non-destructive)
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.database import get_engine

# FK-safe drop order (children first).
QUOTA_TABLES = [
    "quota_publication_relation",
    "quota_projection_candidate",
    "quota_archive_profile",
    "quota_dictionary",
    "quota_publication_set",
]
NEW_ARCHIVE_FILE_COLUMNS = ["page_range", "link_source", "linked_by"]
# Lossless indexes added by the P0-2 migration (safe to drop in any mode).
NEW_ARCHIVE_FILE_INDEXES = ["ix_archive_file_link_source"]
# quota_dictionary is migration-owned reference/seed data, not user business data, so it
# does not trip the dev-mode non-empty guard (it is still dropped on a clean dev downgrade).
BUSINESS_QUOTA_TABLES = [table for table in QUOTA_TABLES if table != "quota_dictionary"]


class DowngradeRefused(RuntimeError):
    """Raised in dev mode when the target DB still holds quota business data."""


def _table_names(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def _quota_tables_present(engine: Engine) -> list[str]:
    present = _table_names(engine)
    return [table for table in BUSINESS_QUOTA_TABLES if table in present]


def _archive_file_business_rows(engine: Engine) -> int:
    if "archive_file" not in _table_names(engine):
        return 0
    columns = {column["name"] for column in inspect(engine).get_columns("archive_file")}
    clauses = []
    if "page_range" in columns:
        clauses.append("page_range <> ''")
    if "link_source" in columns:
        clauses.append("link_source <> 'import'")
    if "linked_by" in columns:
        clauses.append("linked_by IS NOT NULL")
    if not clauses:
        return 0
    statement = text("SELECT COUNT(*) FROM archive_file WHERE " + " OR ".join(clauses))
    with engine.connect() as connection:
        return int(connection.execute(statement).scalar() or 0)


def downgrade(engine: Engine, *, mode: str = "dev") -> dict:
    if mode not in {"dev", "prod"}:
        raise ValueError("mode must be 'dev' or 'prod'")
    return _downgrade_prod(engine) if mode == "prod" else _downgrade_dev(engine)


def _downgrade_prod(engine: Engine) -> dict:
    with engine.begin() as connection:
        for index in NEW_ARCHIVE_FILE_INDEXES:
            connection.execute(text(f"DROP INDEX IF EXISTS {index}"))
    return {
        "mode": "prod",
        "dropped_indexes": list(NEW_ARCHIVE_FILE_INDEXES),
        "kept_tables": list(QUOTA_TABLES),
        "kept_columns": list(NEW_ARCHIVE_FILE_COLUMNS),
    }


def _downgrade_dev(engine: Engine) -> dict:
    present = _quota_tables_present(engine)
    with engine.connect() as connection:
        nonempty = {
            table: int(connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)
            for table in present
        }
    nonempty = {table: count for table, count in nonempty.items() if count > 0}
    business_rows = _archive_file_business_rows(engine)
    if nonempty or business_rows:
        raise DowngradeRefused(
            f"REFUSED_NONEMPTY: quota_tables={nonempty}, archive_file_business_rows={business_rows}"
        )

    dialect = engine.dialect.name
    with engine.begin() as connection:
        for index in NEW_ARCHIVE_FILE_INDEXES:
            connection.execute(text(f"DROP INDEX IF EXISTS {index}"))
        if dialect == "postgresql":
            connection.execute(text("ALTER TABLE archive_file DROP CONSTRAINT IF EXISTS uq_archive_file_role"))
            connection.execute(
                text(
                    "ALTER TABLE archive_file ADD CONSTRAINT uq_archive_file_role "
                    "UNIQUE (archive_id, file_id, file_role)"
                )
            )
            for column in NEW_ARCHIVE_FILE_COLUMNS:
                connection.execute(text(f"ALTER TABLE archive_file DROP COLUMN IF EXISTS {column}"))
        else:
            connection.execute(text("DROP INDEX IF EXISTS uq_archive_file_role"))
        for table in QUOTA_TABLES:
            connection.execute(text(f"DROP TABLE IF EXISTS {table}"))

    dropped_columns: list[str] = []
    if dialect == "postgresql":
        dropped_columns = list(NEW_ARCHIVE_FILE_COLUMNS)
    else:
        # SQLite: DROP COLUMN can fail when a leftover unique constraint references the
        # column (fresh create_all path). Attempt per-statement; tolerate + report.
        for column in NEW_ARCHIVE_FILE_COLUMNS:
            try:
                with engine.begin() as connection:
                    connection.execute(text(f"ALTER TABLE archive_file DROP COLUMN {column}"))
                dropped_columns.append(column)
            except Exception:  # noqa: BLE001 - best-effort on SQLite; never delete row data
                continue

    return {
        "mode": "dev",
        "dropped_tables": list(QUOTA_TABLES),
        "dropped_indexes": list(NEW_ARCHIVE_FILE_INDEXES),
        "dropped_columns": dropped_columns,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.quota_migration")
    subparsers = parser.add_subparsers(dest="command", required=True)
    downgrade_parser = subparsers.add_parser("downgrade", help="tear down the P0-2 quota schema")
    downgrade_parser.add_argument(
        "--prod",
        action="store_true",
        help="production mode: index-only, keep tables and columns (non-destructive)",
    )
    args = parser.parse_args(argv)

    engine = get_engine()
    if args.command == "downgrade":
        try:
            result = downgrade(engine, mode="prod" if args.prod else "dev")
        except DowngradeRefused as exc:
            print(f"DOWNGRADE_REFUSED: {exc}", file=sys.stderr)
            return 2
        print(result)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
