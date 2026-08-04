from collections.abc import Generator
import json
import logging
import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy import inspect, select, text, update
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)

from app.archive_rules import ARCHIVE_FILE_ROLES
from app.config import get_settings
from app.models import (
    AdministrativeDivision,
    Base,
    Blob,
    DataSource,
    Outbox,
    QuotaArchiveProfile,
    QuotaDictionary,
    QuotaProjectionCandidate,
    QuotaPublicationRelation,
    QuotaPublicationSet,
)
from quota_lake.db.migrations import migrate_quota_lake_tables
from app.quota_taxonomy import (
    DICT_TYPE_DISCIPLINE,
    DICT_TYPE_INDUSTRY_SECTOR,
    DISCIPLINE_SEED,
    INDUSTRY_SECTOR_SEED,
)


class QuotaMigrationBlocked(RuntimeError):
    """Raised when a quota P0-2 migration guard finds pre-existing data that would
    violate a new constraint. The migration stops and reports; it never deletes data
    (SPEC-QA-001 revisions #4/#5, §18 stop condition).
    """

    def __init__(self, code: str, offenders: list[dict]) -> None:
        self.code = code
        self.offenders = offenders
        super().__init__(f"{code}: {len(offenders)} offending group(s): {offenders[:20]}")

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None

# 本系统当前仅覆盖中国大陆和新疆生产建设兵团。保留既有记录以便历史
# 档案可追溯，但不再将港澳台作为可选行政区划或覆盖矩阵目标。
EXCLUDED_ADMINISTRATIVE_DIVISION_CODES = frozenset({"710000", "810000", "820000"})


def get_engine(database_url: str | None = None) -> Engine:
    global _engine
    if _engine is None:
        url = database_url or get_settings().database_url
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, future=True, connect_args=connect_args)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), future=True, expire_on_commit=False)
    return _session_factory


def init_db() -> None:
    engine = get_engine()
    # Several crawler-loop processes can be started together.  Schema repair is
    # intentionally idempotent, but some backfill UPDATEs acquire relation locks
    # in a different order and can deadlock when run concurrently.  Serialize
    # only the startup migration section on PostgreSQL; task workers themselves
    # remain fully concurrent afterwards.
    if engine.dialect.name == "postgresql":
        with engine.connect() as lock_connection:
            lock_connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": 805_150_001})
            lock_connection.commit()
            try:
                _init_db_unlocked(engine)
            finally:
                lock_connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": 805_150_001})
                lock_connection.commit()
        return
    _init_db_unlocked(engine)


def _init_db_unlocked(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    migrate_blob_columns(engine)
    migrate_ingest_event_t2_columns(engine)
    migrate_collection_ledger_columns(engine)
    migrate_archive_columns(engine)
    migrate_archive_file_columns(engine)
    migrate_archive_event_columns(engine)
    migrate_crawl_lineage_columns(engine)
    migrate_outbox_table(engine)
    migrate_audit_log_columns(engine)
    migrate_archive_jsonb_columns(engine)
    migrate_coverage_gap_columns(engine)
    migrate_quota_tables(engine)
    migrate_administrative_division_table(engine)
    migrate_archive_parse_columns(engine)
    migrate_quota_lake_tables(engine)

def migrate_administrative_division_table(engine: Engine) -> None:
    """P0-4A · 公共行政区划字典 (GB/T 2260-2024).

    Creates the table and seeds from a versioned JSON file.
    Idempotent: re-runs don't create duplicate rows or overwrite manual edits.
    Legacy Hong Kong, Macao and Taiwan records are retained but disabled so
    existing archive references stay traceable while the regions disappear
    from the system's selectable mainland scope.
    """
    AdministrativeDivision.__table__.create(engine, checkfirst=True)

    seed_path = os.path.join(os.path.dirname(__file__), "data", "administrative_divisions.json")
    if not os.path.isfile(seed_path):
        return  # 种子文件不存在时不阻塞启动（允许后续补放）

    try:
        with open(seed_path, "r", encoding="utf-8") as fh:
            seed_data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        raise RuntimeError(f"行政区划种子文件解析失败: {seed_path} — {exc}") from exc

    items = seed_data.get("items", [])
    if not items:
        return

    meta = seed_data.get("meta", {})
    version_tag = meta.get("source", "GB/T2260-2024")

    factory = sessionmaker(bind=engine, future=True)
    with factory() as session:
        existing_codes = set(
            row[0] for row in session.execute(select(AdministrativeDivision.code)).all()
        )
        added = 0
        for item in items:
            code = item.get("code", "").strip()
            if not code or code in existing_codes:
                continue
            session.add(
                AdministrativeDivision(
                    code=code,
                    name=item.get("name", "").strip(),
                    level=item.get("level", "city"),
                    parent_code=item.get("parent_code") or None,
                    enabled=item.get("enabled", True),
                    version=version_tag,
                    sort_order=item.get("sort_order", 0),
                )
            )
            existing_codes.add(code)
            added += 1
        disabled = session.execute(
            update(AdministrativeDivision)
            .where(AdministrativeDivision.code.in_(EXCLUDED_ADMINISTRATIVE_DIVISION_CODES))
            .where(AdministrativeDivision.enabled.is_(True))
            .values(enabled=False)
        ).rowcount
        if added or disabled:
            session.commit()

def migrate_blob_columns(engine: Engine) -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "blob" not in table_names:
        Blob.__table__.create(engine, checkfirst=True)
    if "file_asset" not in table_names:
        return

    existing_columns = {column["name"] for column in inspector.get_columns("file_asset")}
    dialect = engine.dialect.name
    with engine.begin() as connection:
        if "blob_hash" not in existing_columns:
            connection.execute(text("ALTER TABLE file_asset ADD COLUMN blob_hash VARCHAR(64)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_file_asset_blob_hash ON file_asset (blob_hash)"))
        if "sha256" in existing_columns:
            connection.execute(text(
                "UPDATE file_asset SET blob_hash = sha256 "
                "WHERE blob_hash IS NULL "
                "AND sha256 IN (SELECT blob_hash FROM blob)"
            ))
        if dialect == "postgresql":
            connection.execute(text("ALTER TABLE file_asset DROP CONSTRAINT IF EXISTS uq_file_asset_object_version"))


def migrate_ingest_event_t2_columns(engine: Engine) -> None:
    inspector = inspect(engine)
    if "ingest_event" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("ingest_event")}
    dialect = engine.dialect.name
    datetime_type = "TIMESTAMP WITH TIME ZONE" if dialect == "postgresql" else "DATETIME"
    json_type = "JSONB" if dialect == "postgresql" else "JSON"
    column_defs = {
        "source_id": "VARCHAR(36)",
        "task_id": "VARCHAR(36)",
        "source_item_key": "TEXT",
        "source_modified_at": datetime_type,
        "metadata": json_type,
    }

    with engine.begin() as connection:
        for column_name, column_type in column_defs.items():
            if column_name not in existing_columns:
                connection.execute(text(f"ALTER TABLE ingest_event ADD COLUMN {column_name} {column_type}"))

        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_ingest_event_source_id ON ingest_event (source_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_ingest_event_task_id ON ingest_event (task_id)"))


def migrate_collection_ledger_columns(engine: Engine) -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    dialect = engine.dialect.name
    json_type = "JSONB" if dialect == "postgresql" else "JSON"

    if "data_source" in table_names:
        existing_columns = {column["name"] for column in inspector.get_columns("data_source")}
        source_defs = {
            "url": "TEXT",
            "url_alt": "TEXT",
            "province": "VARCHAR(64)",
            "city": "VARCHAR(64)",
            "format": "VARCHAR(64)",
            "downloadable": "BOOLEAN",
            "bucket": "VARCHAR(32)",
            "owner": "VARCHAR(64)",
            "reviewer": "VARCHAR(64)",
            "remark": "TEXT",
            "frequency": "VARCHAR(32)",
        }
        with engine.begin() as connection:
            for column_name, column_type in source_defs.items():
                if column_name not in existing_columns:
                    connection.execute(text(f"ALTER TABLE data_source ADD COLUMN {column_name} {column_type}"))
            if "config" not in existing_columns:
                connection.execute(text(f"ALTER TABLE data_source ADD COLUMN config {json_type}"))
            if dialect == "postgresql":
                connection.execute(text("ALTER TABLE data_source DROP CONSTRAINT IF EXISTS uq_data_source_identity"))
                connection.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS uq_data_source_identity_region "
                        "ON data_source (source_scope, asset_tenant_code, source_type, province, city, name)"
                    )
                )
            for column_name in [
                "province",
                "city",
                "format",
                "downloadable",
                "bucket",
                "owner",
                "reviewer",
                "frequency",
            ]:
                connection.execute(
                    text(f"CREATE INDEX IF NOT EXISTS ix_data_source_{column_name} ON data_source ({column_name})")
                )

    if "collection_task" in table_names:
        existing_columns = {column["name"] for column in inspector.get_columns("collection_task")}
        task_defs = {
            "period_raw": "VARCHAR(64)",
            "period_start": "VARCHAR(7)",
            "period_end": "VARCHAR(7)",
            "period_note": "VARCHAR(64)",
            "worker_id": "VARCHAR(128)",
            "lease_expires_at": "TIMESTAMP WITH TIME ZONE" if dialect == "postgresql" else "DATETIME",
            "heartbeat_at": "TIMESTAMP WITH TIME ZONE" if dialect == "postgresql" else "DATETIME",
        }
        with engine.begin() as connection:
            for column_name, column_type in task_defs.items():
                if column_name not in existing_columns:
                    connection.execute(text(f"ALTER TABLE collection_task ADD COLUMN {column_name} {column_type}"))
            for column_name in task_defs:
                connection.execute(
                    text(f"CREATE INDEX IF NOT EXISTS ix_collection_task_{column_name} ON collection_task ({column_name})")
                )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_collection_task_status_lease_expires_at "
                    "ON collection_task (status, lease_expires_at)"
                )
            )


def archive_jsonb_migration_statements() -> list[str]:
    return [
        "ALTER TABLE archive ALTER COLUMN metadata TYPE JSONB USING metadata::jsonb",
        "ALTER TABLE archive ALTER COLUMN field_sources TYPE JSONB USING field_sources::jsonb",
    ]


def migrate_archive_columns(engine: Engine) -> None:
    inspector = inspect(engine)
    if "archive" not in inspector.get_table_names():
        return
    existing_columns = {column["name"] for column in inspector.get_columns("archive")}
    dialect = engine.dialect.name
    with engine.begin() as connection:
        if "collection_method" not in existing_columns:
            connection.execute(
                text("ALTER TABLE archive ADD COLUMN collection_method VARCHAR(32) NOT NULL DEFAULT 'auto'")
            )
        if "price_kind" not in existing_columns:
            connection.execute(
                text("ALTER TABLE archive ADD COLUMN price_kind VARCHAR(32) NOT NULL DEFAULT 'unspecified'")
            )
        if "period_kind" not in existing_columns:
            connection.execute(
                text("ALTER TABLE archive ADD COLUMN period_kind VARCHAR(32) NOT NULL DEFAULT 'monthly'")
            )
        if "metadata_schema_version" not in existing_columns:
            connection.execute(
                text("ALTER TABLE archive ADD COLUMN metadata_schema_version VARCHAR(32) NOT NULL DEFAULT 'v1'")
            )
        if "preview_status" not in existing_columns:
            connection.execute(text("ALTER TABLE archive ADD COLUMN preview_status VARCHAR(32) NOT NULL DEFAULT 'none'"))
        if dialect == "postgresql" and "publish_date" in existing_columns:
            connection.execute(text("ALTER TABLE archive ALTER COLUMN publish_date TYPE DATE USING publish_date::date"))
        if dialect == "postgresql" and "period_kind" in existing_columns:
            connection.execute(text("ALTER TABLE archive DROP CONSTRAINT IF EXISTS ck_archive_period_kind"))
            connection.execute(
                text(
                    "ALTER TABLE archive ADD CONSTRAINT ck_archive_period_kind "
                    "CHECK (period_kind in ('monthly', 'issue_based', 'bimonthly'))"
                )
            )
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_archive_collection_method ON archive (collection_method)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_archive_price_kind ON archive (price_kind)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_archive_period_kind ON archive (period_kind)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_archive_preview_status ON archive (preview_status)"))
        beijing_region_clause = "business_key LIKE '%:110000:%'"
        if "region_code" in existing_columns:
            beijing_region_clause = f"(region_code LIKE '11%' OR {beijing_region_clause})"
        connection.execute(
            text(
                "UPDATE archive "
                "SET price_kind = 'guidance' "
                "WHERE domain_type = 'cost_info' "
                "AND price_kind = 'unspecified' "
                f"AND {beijing_region_clause} "
                "AND CAST(metadata AS TEXT) LIKE '%beijing.zjw-main-pdf-list.v1%'"
            )
        )


def migrate_archive_file_columns(engine: Engine) -> None:
    inspector = inspect(engine)
    if "archive_file" not in inspector.get_table_names():
        return
    existing_columns = {column["name"] for column in inspector.get_columns("archive_file")}
    with engine.begin() as connection:
        if "created_at" in existing_columns and "added_at" not in existing_columns:
            connection.execute(text("ALTER TABLE archive_file RENAME COLUMN created_at TO added_at"))
            existing_columns = {column["name"] for column in inspect(engine).get_columns("archive_file")}
        if "added_at" not in existing_columns:
            datetime_type = "TIMESTAMP WITH TIME ZONE" if engine.dialect.name == "postgresql" else "DATETIME"
            connection.execute(text(f"ALTER TABLE archive_file ADD COLUMN added_at {datetime_type}"))
        if "representation_role" not in existing_columns:
            connection.execute(
                text("ALTER TABLE archive_file ADD COLUMN representation_role VARCHAR(32) NOT NULL DEFAULT 'primary'")
            )
        if "fetch_status" not in existing_columns:
            connection.execute(text("ALTER TABLE archive_file ADD COLUMN fetch_status VARCHAR(32) NOT NULL DEFAULT 'FETCHED'"))
        if engine.dialect.name == "postgresql":
            connection.execute(text("ALTER TABLE archive_file ALTER COLUMN file_id DROP NOT NULL"))
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_archive_file_representation_role ON archive_file (representation_role)")
        )
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_archive_file_fetch_status ON archive_file (fetch_status)"))
    # F-M3 根治 (2026-08-03): 把 ck_archive_file_role 同步挪到独立函数,
    # 自身 try/except 隔离, 不再依赖手动 ALTER。web 启动 / worker 启动都会调。
    sync_archive_file_role_constraint(engine)
    # v0.8: ck_quota_parse_job_profile 同样做幂等同步（从 sichuan/chongqing → 32 省）。
    # 任一进程入口都自愈, 不依赖手动 ALTER。
    sync_quota_parse_job_profile_constraint(engine)


def archive_file_role_check_sql() -> str:
    quoted_roles = ", ".join(f"'{role}'" for role in sorted(ARCHIVE_FILE_ROLES))
    return f"file_role in ({quoted_roles})"


def sync_archive_file_role_constraint(engine: Engine) -> int | None:
    """幂等同步 ck_archive_file_role CheckConstraint 到 Python 侧 ARCHIVE_FILE_ROLES。

    行为:
      - DROP IF EXISTS + ADD (从 sorted(ARCHIVE_FILE_ROLES) 重建 CHECK)
      - 仅 PG (SQLite 由模型层自己管)
      - 异常被吞, 不阻断其他迁移 (F-M3 教训: 28/24 不一致会反复踩到)
      - 返回新的 role 数, 失败返回 None

    调用方: init_db (web 启动) / worker 启动时自检前都可调一次。
    任何进程入口跑一次都能自愈 DB 与 Python 的漂移, 不依赖手动 ALTER。
    """
    if engine.dialect.name != "postgresql":
        return None
    expected_count = len(ARCHIVE_FILE_ROLES)
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE archive_file DROP CONSTRAINT IF EXISTS ck_archive_file_role"))
            conn.execute(
                text(f"ALTER TABLE archive_file ADD CONSTRAINT ck_archive_file_role CHECK ({archive_file_role_check_sql()})")
            )
        logger.info("ck_archive_file_role 已同步 (%d roles)", expected_count)
        return expected_count
    except Exception as e:
        logger.warning("ck_archive_file_role 同步失败 (%s) — 继续启动, 后续写入可能被 CheckViolation 拒绝", e)
        return None


# v0.8: profile 字段语义（quota/README.md §8）放宽到 32 个 GB/T 28039 拼音长名。
# 历史 DB ck_quota_parse_job_profile 硬编码 sichuan/chongqing，与 quota_api._VALID_PROFILES (32 项)
# 漂移会导致 CHECK Violation — 上传深圳 PDF 时直接 500。
# 同步逻辑与 ck_archive_file_role 完全同构：init_db / worker 启动都会跑一次。
def quota_parse_job_profile_check_sql() -> str:
    from app.quota_api import _VALID_PROFILES  # 避免循环 import
    quoted = ", ".join(f"'{p}'" for p in sorted(_VALID_PROFILES))
    return f"profile in ({quoted})"


def sync_quota_parse_job_profile_constraint(engine: Engine) -> int | None:
    """幂等同步 ck_quota_parse_job_profile 到 quota_api._VALID_PROFILES (32 个 pinyin 长名)。

    行为:
      - DROP IF EXISTS + ADD (从 sorted(_VALID_PROFILES) 重建 CHECK)
      - 仅 PG (SQLite 由模型层自己管)
      - 异常被吞, 不阻断其他迁移
      - 返回新的 profile 数, 失败返回 None
    """
    if engine.dialect.name != "postgresql":
        return None
    try:
        from app.quota_api import _VALID_PROFILES
        expected_count = len(_VALID_PROFILES)
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE quota_parse_job DROP CONSTRAINT IF EXISTS ck_quota_parse_job_profile"))
            conn.execute(
                text(f"ALTER TABLE quota_parse_job ADD CONSTRAINT ck_quota_parse_job_profile CHECK ({quota_parse_job_profile_check_sql()})")
            )
        logger.info("ck_quota_parse_job_profile 已同步 (%d profiles)", expected_count)
        return expected_count
    except Exception as e:
        logger.warning("ck_quota_parse_job_profile 同步失败 (%s) — 继续启动, 后续写入可能被 CheckViolation 拒绝", e)
        return None


def migrate_archive_event_columns(engine: Engine) -> None:
    inspector = inspect(engine)
    if "archive_event" not in inspector.get_table_names():
        return
    existing_columns = {column["name"] for column in inspector.get_columns("archive_event")}
    json_type = "JSONB" if engine.dialect.name == "postgresql" else "JSON"
    with engine.begin() as connection:
        if "before_payload" not in existing_columns:
            connection.execute(text(f"ALTER TABLE archive_event ADD COLUMN before_payload {json_type}"))
        if "after_payload" not in existing_columns:
            connection.execute(text(f"ALTER TABLE archive_event ADD COLUMN after_payload {json_type}"))
        if "payload" in existing_columns:
            connection.execute(text("UPDATE archive_event SET after_payload = payload WHERE after_payload IS NULL"))


def migrate_crawl_lineage_columns(engine: Engine) -> None:
    inspector = inspect(engine)
    if "crawl_lineage" not in inspector.get_table_names():
        return
    existing_columns = {column["name"] for column in inspector.get_columns("crawl_lineage")}
    with engine.begin() as connection:
        if "parser_version" not in existing_columns:
            connection.execute(text("ALTER TABLE crawl_lineage ADD COLUMN parser_version VARCHAR(128)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_crawl_lineage_parser_version ON crawl_lineage (parser_version)"))


def migrate_outbox_table(engine: Engine) -> None:
    inspector = inspect(engine)
    if "outbox" not in inspector.get_table_names():
        Outbox.__table__.create(engine, checkfirst=True)


def migrate_audit_log_columns(engine: Engine) -> None:
    inspector = inspect(engine)
    if "audit_log" not in inspector.get_table_names():
        return
    existing_columns = {column["name"] for column in inspector.get_columns("audit_log")}
    with engine.begin() as connection:
        if "error_code" not in existing_columns:
            connection.execute(text("ALTER TABLE audit_log ADD COLUMN error_code VARCHAR(64)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_audit_log_error_code ON audit_log (error_code)"))


def migrate_archive_jsonb_columns(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    inspector = inspect(engine)
    if "archive" not in inspector.get_table_names():
        return
    existing_columns = {column["name"] for column in inspector.get_columns("archive")}
    with engine.begin() as connection:
        for statement in archive_jsonb_migration_statements():
            column_name = statement.split(" ALTER COLUMN ", 1)[1].split(" ", 1)[0]
            if column_name in existing_columns:
                connection.execute(text(statement))


def migrate_coverage_gap_columns(engine: Engine) -> None:
    """Add coverage-gap columns + cell-idempotency index; (Postgres) create v_coverage_gap view.

    Columns are also declared on the ORM models so fresh DBs (and tests via
    ``Base.metadata.create_all``) get them automatically; this migration covers
    *existing* databases with ALTER TABLE. The partial unique index is recreated
    here for existing Postgres/SQLite DBs where ``create_all`` won't add a new
    index to an already-present table.
    """
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

    if "archive" in table_names:
        existing = {column["name"] for column in inspector.get_columns("archive")}
        with engine.begin() as connection:
            if "coverage_region_code" not in existing:
                connection.execute(text("ALTER TABLE archive ADD COLUMN coverage_region_code VARCHAR(32)"))
            if "coverage_period" not in existing:
                connection.execute(text("ALTER TABLE archive ADD COLUMN coverage_period VARCHAR(7)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_archive_coverage_region_code ON archive (coverage_region_code)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_archive_coverage_period ON archive (coverage_period)"))

    if "collection_task" in table_names:
        existing = {column["name"] for column in inspector.get_columns("collection_task")}
        with engine.begin() as connection:
            if "coverage_region_code" not in existing:
                connection.execute(text("ALTER TABLE collection_task ADD COLUMN coverage_region_code VARCHAR(32)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_collection_task_coverage_region_code ON collection_task (coverage_region_code)"))
            # Cross-dialect partial unique index (SQLite + Postgres both support WHERE).
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_collection_task_cell_active "
                    "ON collection_task (coverage_region_code, period_start, data_domain) "
                    "WHERE status IN ('pending', 'running') AND coverage_region_code IS NOT NULL"
                )
            )

    # city_period_scheme: created by Base.metadata.create_all (checkfirst) for fresh and
    # existing DBs alike, so no ALTER needed here.

    # v_coverage_gap: Postgres only. Guarded so an unvalidated DDL never breaks startup;
    # explicit creation/re-validation is via `python -m app.coverage_gap_setup create-view`.
    if engine.dialect.name == "postgresql" and "archive" in table_names:
        with engine.begin() as connection:
            try:
                connection.execute(text(v_coverage_gap_ddl()))
            except Exception:  # noqa: BLE001 - view DDL must not break app startup
                pass


def v_coverage_gap_ddl() -> str:
    """Return the ``CREATE OR REPLACE VIEW v_coverage_gap`` DDL (Postgres).

    The view's CASE expressions mirror ``app.coverage_gap_contract.classify_gap`` exactly.
    Cell universe = covered archives ∪ declared target_regions×declared_periods ∪ task cells.
    Validated against ``classify_gap`` via ``app.coverage_gap_setup ... check-view-consistency``.
    """
    return """
CREATE OR REPLACE VIEW v_coverage_gap AS
WITH
covered AS (
  SELECT a.coverage_region_code, a.coverage_period AS period, 'cost_info' AS domain_type,
         count(*) AS archive_count,
         array_agg(DISTINCT a.source_id) AS source_ids
  FROM archive a
  WHERE a.domain_type = 'cost_info'
    AND a.status IN ('pending_tag','collected','archived','ready_for_governance')
    AND a.is_current AND NOT a.is_withdrawn
    AND a.coverage_region_code IS NOT NULL AND a.coverage_period IS NOT NULL
  GROUP BY a.coverage_region_code, a.coverage_period
),
decl AS (
  SELECT (t.region ->> 'region_code') AS coverage_region_code,
         p.period AS period,
         bool_or((t.region ->> 'source_completeness_status') = 'source_blocked') AS declared_blocked
  FROM data_source s
  CROSS JOIN LATERAL jsonb_array_elements(s.config -> 'coverage_expectation' -> 'target_regions') AS t(region)
  CROSS JOIN LATERAL jsonb_array_elements_text(t.region -> 'declared_periods') AS p(period)
  WHERE s.data_domain = 'cost_info' AND s.source_type = 'info_price' AND s.source_scope = 'platform_public'
    AND t.region ->> 'region_code' IS NOT NULL
  GROUP BY 1, 2
),
task_cells AS (
  SELECT coverage_region_code, period_start AS period, data_domain
  FROM collection_task
  WHERE coverage_region_code IS NOT NULL AND period_start IS NOT NULL
    AND data_domain = 'cost_info' AND task_type IN ('crawl_incremental', 'crawl_issue')
),
cells AS (
  SELECT coverage_region_code, period, domain_type FROM covered
  UNION SELECT coverage_region_code, period, 'cost_info' FROM decl
  UNION SELECT coverage_region_code, period, domain_type FROM task_cells
),
bounds AS (
  SELECT coverage_region_code, min(period) AS earliest, max(period) AS latest
  FROM (
    SELECT coverage_region_code, period FROM covered
    UNION SELECT coverage_region_code, period FROM decl
  ) u
  GROUP BY coverage_region_code
),
latest_task AS (
  SELECT DISTINCT ON (coverage_region_code, period_start, data_domain)
         coverage_region_code, period_start AS period, data_domain,
         task_id, status, error_code, attempt
  FROM collection_task
  WHERE coverage_region_code IS NOT NULL AND period_start IS NOT NULL
    AND data_domain = 'cost_info' AND task_type IN ('crawl_incremental', 'crawl_issue')
  ORDER BY coverage_region_code, period_start, data_domain, created_at DESC
),
region_source AS (
  SELECT (t.region ->> 'region_code') AS coverage_region_code,
         bool_or(s.status = 'active') AS has_active_source,
         bool_or((t.region ->> 'source_completeness_status') = 'source_blocked') AS source_blocked
  FROM data_source s
  CROSS JOIN LATERAL jsonb_array_elements(s.config -> 'coverage_expectation' -> 'target_regions') AS t(region)
  WHERE s.data_domain = 'cost_info' AND s.source_type = 'info_price' AND s.source_scope = 'platform_public'
    AND t.region ->> 'region_code' IS NOT NULL
  GROUP BY 1
),
primary_file AS (
  SELECT DISTINCT ON (a.coverage_region_code, a.coverage_period)
         a.coverage_region_code, a.coverage_period AS period, af.file_id
  FROM archive a
  JOIN archive_file af ON af.archive_id = a.archive_id
  WHERE a.domain_type = 'cost_info'
    AND a.coverage_region_code IS NOT NULL AND a.coverage_period IS NOT NULL
    AND af.file_id IS NOT NULL
  ORDER BY a.coverage_region_code, a.coverage_period, af.is_primary DESC, af.sort_order, af.added_at
)
SELECT
  c.coverage_region_code,
  c.period,
  c.domain_type,
  CASE
    WHEN co.archive_count > 0 OR d.coverage_region_code IS NOT NULL THEN 'covered'
    WHEN rs.source_blocked IS TRUE
      OR rs.has_active_source IS NOT TRUE
      OR c.period > b.latest
      OR (b.earliest IS NOT NULL AND c.period < b.earliest)
    THEN 'pending_verify'
    ELSE 'missing'
  END AS gap_type,
  CASE
    WHEN co.archive_count > 0 OR d.coverage_region_code IS NOT NULL THEN NULL
    WHEN rs.source_blocked IS TRUE OR rs.has_active_source IS NOT TRUE THEN 'no_source'
    WHEN c.period > b.latest THEN 'not_published'
    WHEN lt.status = 'failed' THEN 'failed'
    ELSE 'not_attempted'
  END AS gap_reason,
  CASE WHEN lt.status = 'failed' THEN
    CASE lt.error_code
      WHEN 'DOWNLOAD_TIMEOUT' THEN 'download_timeout'
      WHEN 'HOST_UNREACHABLE' THEN 'host_unreachable'
      WHEN 'PARSE_ERROR' THEN 'parse_error'
      WHEN 'COST_INFO_CRAWL_TASK_FAILED' THEN 'crawl_failed'
      WHEN 'MAX_ATTEMPTS_EXCEEDED' THEN 'max_attempts_exceeded'
      ELSE NULL END
  END AS failed_stage,
  lt.task_id AS task_id,
  lt.status AS latest_task_status,
  lt.attempt AS latest_task_attempt,
  CASE WHEN pf.file_id IS NOT NULL THEN '/api/file-assets/' || pf.file_id || '/download' END AS download_url,
  co.source_ids AS source_ids,
  sch.expected_publish_day AS expected_publish_day
FROM cells c
LEFT JOIN covered co ON co.coverage_region_code = c.coverage_region_code AND co.period = c.period
LEFT JOIN decl d ON d.coverage_region_code = c.coverage_region_code AND d.period = c.period
LEFT JOIN bounds b ON b.coverage_region_code = c.coverage_region_code
LEFT JOIN latest_task lt ON lt.coverage_region_code = c.coverage_region_code AND lt.period = c.period
LEFT JOIN region_source rs ON rs.coverage_region_code = c.coverage_region_code
LEFT JOIN primary_file pf ON pf.coverage_region_code = c.coverage_region_code AND pf.period = c.period
LEFT JOIN city_period_scheme sch ON sch.region_code = c.coverage_region_code AND sch.domain_type = c.domain_type;
"""


def migrate_quota_tables(engine: Engine) -> None:
    """P0-2 · SPEC-QA-001 quota schema: create the five quota tables, extend the
    shared ``archive_file`` table non-destructively, and seed the controlled
    dictionary. Idempotent and additive; guards abort (never delete) on pre-existing
    data that would violate the new uniqueness rules.
    """
    for model in (
        QuotaPublicationSet,
        QuotaArchiveProfile,
        QuotaProjectionCandidate,
        QuotaPublicationRelation,
        QuotaDictionary,
    ):
        model.__table__.create(engine, checkfirst=True)

    if "archive_file" in set(inspect(engine).get_table_names()):
        _migrate_archive_file_quota_columns(engine)

    _seed_quota_dictionary(engine)
    # Legacy installations may predate the crawler/data-source schema. The
    # quota manual-upload seed depends on this table, so create it additively
    # before attempting the idempotent seed.
    DataSource.__table__.create(engine, checkfirst=True)
    _seed_quota_manual_upload_source(engine)


def _seed_quota_manual_upload_source(engine: Engine) -> None:
    """Ensure a stable DataSource for quota manual uploads."""
    factory = sessionmaker(bind=engine, future=True)
    with factory() as session:
        existing = session.get(DataSource, "quota-manual-upload")
        if existing is not None:
            return
        source = DataSource(
            source_id="quota-manual-upload",
            source_scope="platform_public",
            managed_by="platform",
            source_type="manual",
            connector_type="manual_upload",
            name="Quota 手工上传",
            base_url=None,
            url=None,
            url_alt=None,
            province=None,
            city=None,
            region_code=None,
            data_domain="quota",
            auth_secret_ref=None,
            format="pdf",
            downloadable=None,
            bucket=None,
            owner=None,
            reviewer=None,
            remark="P0-4A · 清单定额手工上传入口",
            frequency=None,
            config={},
            schedule_policy=None,
            status="active",
            created_by="system",
            asset_tenant_code="platform_public",
        )
        session.add(source)
        session.commit()


def _migrate_archive_file_quota_columns(engine: Engine) -> None:
    dialect = engine.dialect.name
    existing = {column["name"] for column in inspect(engine).get_columns("archive_file")}
    with engine.begin() as connection:
        if "page_range" not in existing:
            connection.execute(text("ALTER TABLE archive_file ADD COLUMN page_range VARCHAR(32) NOT NULL DEFAULT ''"))
        if "link_source" not in existing:
            connection.execute(
                text("ALTER TABLE archive_file ADD COLUMN link_source VARCHAR(32) NOT NULL DEFAULT 'import'")
            )
        if "linked_by" not in existing:
            connection.execute(text("ALTER TABLE archive_file ADD COLUMN linked_by VARCHAR(128)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_archive_file_link_source ON archive_file (link_source)"))

    # Guard existing data BEFORE creating the new unique constraint. Never auto-delete.
    role_dups = _find_archive_file_role_duplicates(engine)
    if role_dups:
        raise QuotaMigrationBlocked("ARCHIVE_FILE_ROLE_DUPLICATES", role_dups)

    with engine.begin() as connection:
        if dialect == "postgresql":
            connection.execute(text("ALTER TABLE archive_file DROP CONSTRAINT IF EXISTS uq_archive_file_role"))
            connection.execute(
                text(
                    "ALTER TABLE archive_file ADD CONSTRAINT uq_archive_file_role "
                    "UNIQUE (archive_id, file_id, file_role, page_range)"
                )
            )
        else:
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_archive_file_role "
                    "ON archive_file (archive_id, file_id, file_role, page_range)"
                )
            )
    # SPEC-QA-001 revision #5 (single primary per archive) is intentionally NOT a global
    # DB constraint: existing cost_info archives legitimately carry multiple is_primary=true
    # representation rows, so a global partial unique index would break other domains (§13.1).
    # The invariant is enforced quota-scoped in the application layer (P0-4).


def _find_archive_file_role_duplicates(engine: Engine) -> list[dict]:
    statement = text(
        "SELECT archive_id, file_id, file_role, page_range, COUNT(*) AS row_count "
        "FROM archive_file WHERE file_id IS NOT NULL "
        "GROUP BY archive_id, file_id, file_role, page_range HAVING COUNT(*) > 1"
    )
    with engine.connect() as connection:
        return [dict(row._mapping) for row in connection.execute(statement)]


def _seed_quota_dictionary(engine: Engine) -> None:
    seeds = [(DICT_TYPE_INDUSTRY_SECTOR, code, label) for code, label in INDUSTRY_SECTOR_SEED]
    seeds += [(DICT_TYPE_DISCIPLINE, code, label) for code, label in DISCIPLINE_SEED]
    factory = sessionmaker(bind=engine, future=True)
    with factory() as session:
        existing = {
            (dict_type, code)
            for dict_type, code in session.execute(
                select(QuotaDictionary.dict_type, QuotaDictionary.code)
            ).all()
        }
        changed = False
        for order, (dict_type, code, label) in enumerate(seeds):
            if (dict_type, code) in existing:
                continue
            session.add(
                QuotaDictionary(dict_type=dict_type, code=code, label=label, sort_order=order * 10)
            )
            changed = True
        if changed:
            session.commit()


def get_db_session() -> Generator[Session]:
    with get_session_factory()() as session:
        yield session


# ═══════════════════════════════════════════════════════════════════════════
# P0-3.5A  Blob 完整性诊断（只读，零写库）
# ═══════════════════════════════════════════════════════════════════════════

def diagnose_blob_integrity(engine: Engine) -> dict:
    """Read-only diagnostic.  No INSERT / UPDATE / DELETE / DDL."""

    dialect = engine.dialect.name
    report: dict = {"dialect": dialect, "sections": []}

    def _append_section(title: str, data: dict) -> None:
        data["_section"] = title
        report["sections"].append(data)
        return data

    def _query_scalar(sql: str, **params) -> object:
        with engine.connect() as conn:
            return conn.execute(text(sql), params).scalar()

    def _query_rows(sql: str, limit: int = 5, **params) -> list[dict]:
        with engine.connect() as conn:
            rows = conn.execute(text(sql), params).mappings().fetchmany(limit)
            return [dict(r) for r in rows]

    # ── S1: FileAsset 总量 ──────────────────────────────────────────
    total_fa = int(_query_scalar("SELECT COUNT(*) FROM file_asset") or 0)
    s1 = _append_section("FileAsset 概况", {"total": total_fa})

    # ── S2: blob_hash 字段状态 ─────────────────────────────────────
    blob_hash_null = int(_query_scalar(
        "SELECT COUNT(*) FROM file_asset WHERE blob_hash IS NULL OR blob_hash = ''"
    ) or 0)
    blob_hash_set = total_fa - blob_hash_null
    blob_exists = int(_query_scalar(
        "SELECT COUNT(*) FROM file_asset fa "
        "JOIN blob b ON b.blob_hash = fa.blob_hash"
    ) or 0)
    blob_orphan = int(_query_scalar(
        "SELECT COUNT(*) FROM file_asset fa "
        "LEFT JOIN blob b ON b.blob_hash = fa.blob_hash "
        "WHERE fa.blob_hash IS NOT NULL AND fa.blob_hash != '' AND b.blob_hash IS NULL"
    ) or 0)
    s2 = _append_section("blob_hash 字段", {
        "blob_hash_set": blob_hash_set,
        "blob_hash_null": blob_hash_null,
        "blob_exists": blob_exists,
        "blob_orphan": blob_orphan,
    })

    # ── S3: orphan 样本（最多10条）──────────────────────────────────
    orphan_samples = _query_rows(
        "SELECT fa.file_id, fa.file_name, fa.blob_hash, fa.sha256, "
        "fa.file_size, fa.bucket, fa.object_key "
        "FROM file_asset fa "
        "LEFT JOIN blob b ON b.blob_hash = fa.blob_hash "
        "WHERE fa.blob_hash IS NOT NULL AND fa.blob_hash != '' AND b.blob_hash IS NULL "
        "LIMIT 10"
    )
    _append_section("orphan 样本 (≤10)", {
        "count": len(orphan_samples),
        "samples": orphan_samples,
    })

    # ── S4: sha256 为空 / 格式异常 ─────────────────────────────────
    sha_empty = int(_query_scalar(
        "SELECT COUNT(*) FROM file_asset WHERE sha256 IS NULL OR sha256 = ''"
    ) or 0)
    sha_length_ok = int(_query_scalar(
        "SELECT COUNT(*) FROM file_asset WHERE LENGTH(sha256) = 64"
    ) or 0)
    sha_short = int(_query_scalar(
        "SELECT COUNT(*) FROM file_asset WHERE sha256 IS NOT NULL AND sha256 != '' AND LENGTH(sha256) < 64"
    ) or 0)
    sha_malformed = sha_short if sha_short else 0  # non-hex etc would need regex, skip for now
    s4 = _append_section("sha256 完整性", {
        "sha256_empty": sha_empty,
        "sha256_length_64": sha_length_ok,
        "sha256_short_or_malformed": sha_malformed,
    })

    # ── S5: 同 sha256 → 多个 FileAsset ────────────────────────────
    dup_sha_rows = _query_rows(
        "SELECT sha256, COUNT(*) AS cnt, "
        "ARRAY_AGG(file_id) OVER (PARTITION BY sha256) AS file_ids "
        "FROM file_asset WHERE sha256 IS NOT NULL AND sha256 != '' "
        "GROUP BY sha256 HAVING COUNT(*) > 1 ORDER BY cnt DESC LIMIT 10",
        limit=10,
    ) if dialect == "postgresql" else []
    if dialect != "postgresql":
        dup_sql = (
            "SELECT sha256, COUNT(*) AS cnt FROM file_asset "
            "WHERE sha256 IS NOT NULL AND sha256 != '' "
            "GROUP BY sha256 HAVING COUNT(*) > 1 ORDER BY cnt DESC LIMIT 10"
        )
        dup_sha_rows = _query_rows(dup_sql, limit=10)
    total_dup_sha = int(_query_scalar(
        "SELECT COUNT(*) FROM ("
        "  SELECT sha256 FROM file_asset "
        "  WHERE sha256 IS NOT NULL AND sha256 != '' "
        "  GROUP BY sha256 HAVING COUNT(*) > 1"
        ") sub"
    ) or 0)
    _append_section("同 sha256 多 FileAsset", {
        "duplicate_sha_groups": total_dup_sha,
        "top_10_groups": dup_sha_rows,
    })

    # ── S6: 同 sha 但 size/bucket/key 冲突 ────────────────────────
    if dialect == "postgresql":
        conflict_rows = _query_rows(
            "SELECT sha256, COUNT(DISTINCT file_size) AS size_variants, "
            "COUNT(DISTINCT bucket) AS bucket_variants, "
            "COUNT(DISTINCT object_key) AS key_variants, "
            "STRING_AGG(DISTINCT file_id, ', ') AS sample_file_ids "
            "FROM file_asset WHERE sha256 IS NOT NULL AND sha256 != '' "
            "GROUP BY sha256 HAVING COUNT(DISTINCT file_size) > 1 "
            "   OR COUNT(DISTINCT bucket) > 1 "
            "LIMIT 10",
            limit=10,
        )
    else:
        conflict_rows = _query_rows(
            "SELECT sha256, COUNT(DISTINCT file_size) AS size_variants FROM file_asset "
            "WHERE sha256 IS NOT NULL AND sha256 != '' "
            "GROUP BY sha256 HAVING COUNT(DISTINCT file_size) > 1 LIMIT 10",
            limit=10,
        )
    _append_section("同 sha 元数据冲突", {
        "conflict_groups_estimate": len(conflict_rows),
        "samples": conflict_rows,
    })

    # ── S7: 已有 Blob 但元数据冲突 ─────────────────────────────────
    blob_meta_conflict = _query_rows(
        "SELECT b.blob_hash, b.byte_size AS blob_byte_size, b.storage_bucket AS blob_bucket, "
        "b.blob_storage_key AS blob_key, "
        "fa.file_id, fa.file_size AS fa_file_size, fa.bucket AS fa_bucket, "
        "fa.object_key AS fa_key "
        "FROM blob b JOIN file_asset fa ON fa.blob_hash = b.blob_hash "
        "WHERE (b.byte_size != fa.file_size) "
        "   OR (b.storage_bucket != fa.bucket) "
        "   OR (b.blob_storage_key != fa.object_key) "
        "LIMIT 10",
        limit=10,
    )
    _append_section("Blob-FileAsset 元数据冲突", {
        "conflict_count_sample": len(blob_meta_conflict),
        "samples": blob_meta_conflict,
    })

    # ── S8: quotas 8 个文件检查 ────────────────────────────────────
    quota_fa = _query_rows(
        "SELECT fa.file_id, fa.file_name, fa.file_size, fa.sha256, "
        "fa.blob_hash, fa.bucket, fa.object_key, ds.name AS data_source_name "
        "FROM file_asset fa "
        "JOIN ingest_event ie ON ie.file_id = fa.file_id "
        "JOIN data_source ds ON ds.source_id = ie.source_id "
        "WHERE ds.data_domain = 'quota' "
        "ORDER BY fa.file_name "
        "LIMIT 50",
        limit=50,
    )
    quota_total = int(_query_scalar(
        "SELECT COUNT(*) FROM file_asset fa "
        "JOIN ingest_event ie ON ie.file_id = fa.file_id "
        "JOIN data_source ds ON ds.source_id = ie.source_id "
        "WHERE ds.data_domain = 'quota'"
    ) or 0)
    _append_section("quota 域 FileAsset", {
        "total": quota_total,
        "samples": quota_fa,
    })

    # ── S9: FK 状态 ───────────────────────────────────────────────
    fk_info: dict = {"exists": False}
    if dialect == "postgresql":
        fk_rows = _query_rows(
            "SELECT con.conname AS constraint_name, "
            "pg_get_constraintdef(con.oid) AS definition, "
            "con.convalidated AS validated, "
            "con.confdeltype AS on_delete "
            "FROM pg_constraint con "
            "JOIN pg_class rel ON rel.oid = con.conrelid "
            "WHERE rel.relname = 'file_asset' AND con.contype = 'f' "
            "LIMIT 5",
            limit=5,
        )
        blob_fks = [r for r in fk_rows if "blob" in str(r.get("definition", "")).lower()]
        if blob_fks:
            fk_info = {
                "exists": True,
                "constraints": blob_fks,
            }
    elif dialect == "sqlite":
        fk_rows = _query_rows(
            "PRAGMA foreign_key_list('file_asset')", limit=20
        )
        blob_fks = [r for r in fk_rows if "blob" in str(r.get("table", "")).lower()]
        if blob_fks:
            fk_info = {"exists": True, "constraints": blob_fks}
    _append_section("FK 状态 (file_asset.blob_hash → blob)", fk_info)

    return report


def diagnose_blob_integrity_with_storage(engine: Engine, storage: object) -> dict:
    """Extends diagnose_blob_integrity with MinIO reachability checks (S10)."""

    report = diagnose_blob_integrity(engine)

    # sample up to 50 FileAsset rows with non-null object_key
    with engine.connect() as conn:
        sample_rows = conn.execute(
            text(
                "SELECT file_id, file_name, bucket, object_key, file_size "
                "FROM file_asset WHERE object_key IS NOT NULL AND object_key != '' "
                "ORDER BY random() LIMIT 50"
            )
        ).mappings().all()

    exists_ok = 0
    missing = 0
    size_mismatch = 0
    access_error = 0
    samples: list[dict] = []
    for r in sample_rows:
        bucket = r["bucket"] or "cost-raw"
        key = r["object_key"]
        entry = {
            "file_id": r["file_id"],
            "file_name": r["file_name"],
            "bucket": bucket,
            "object_key": key,
            "fa_file_size": r["file_size"],
        }
        try:
            stat = storage.stat_object(bucket, key)
            if stat is None:
                entry["status"] = "missing"
                missing += 1
            elif stat.byte_size != r["file_size"]:
                entry["status"] = "size_mismatch"
                entry["object_size"] = stat.byte_size
                size_mismatch += 1
            else:
                entry["status"] = "ok"
                exists_ok += 1
        except Exception:
            entry["status"] = "error"
            access_error += 1
        if len(samples) < 15:
            samples.append(entry)

    report["sections"].append({
        "_section": "MinIO 可达性 (随机样本 ≤50)",
        "sample_total": len(sample_rows),
        "exists_ok": exists_ok,
        "missing": missing,
        "size_mismatch": size_mismatch,
        "access_error": access_error,
        "first_15_samples": samples,
    })
    return report


# ── CLI 入口 ─────────────────────────────────────────────────────────────

def _print_compact_report(report: dict) -> None:
    for sec in report.get("sections", []):
        title = sec.pop("_section", "?")
        print(f"\n══ {title}")
        for k, v in sec.items():
            if isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict):
                print(f"   {k}: [{len(v)} items]")
                for idx, item in enumerate(v[:3]):
                    id_str = item.get("file_id", item.get("blob_hash", "?"))
                    print(f"       [{idx}] {id_str}")
            else:
                print(f"   {k}: {v}")


def migrate_archive_parse_columns(engine: Engine) -> None:
    """P0-4B · quota_parser integration (2026-07-28): 为 archive 表加 13 列 parse_* 字段。

    幂等：检查已存在的列，缺的 ALTER TABLE 加；CREATE INDEX IF NOT EXISTS。
    dialect 兼容：PG 用 JSONB + TIMESTAMP WITH TIME ZONE；SQLite 用 TEXT + TIMESTAMP。

    见 quota/INTEGRATION_PLAN.md §2.2 —— 字段与 Manifest（quota-parser-result/v1）一一对齐，
    parse_status 独立于 status（不污染 cost_info 域的 ck_archive_status CheckConstraint）。
    """
    inspector = inspect(engine)
    if "archive" not in inspector.get_table_names():
        return
    existing_columns = {column["name"] for column in inspector.get_columns("archive")}
    dialect = engine.dialect.name
    json_type = "JSONB" if dialect == "postgresql" else "TEXT"
    datetime_type = "TIMESTAMP WITH TIME ZONE" if dialect == "postgresql" else "TIMESTAMP"

    columns = [
        ("parse_status", "VARCHAR(32)"),
        ("parse_profile", "VARCHAR(32)"),
        ("parse_task_id", "VARCHAR(64)"),
        ("parse_phase", "VARCHAR(16)"),
        ("parse_parser_version", "VARCHAR(32)"),
        ("parse_started_at", datetime_type),
        ("parse_finished_at", datetime_type),
        ("parse_metrics", json_type),
        ("parse_warnings", json_type),
        ("parse_error_code", "VARCHAR(32)"),
        ("parse_error_message", "TEXT"),
        ("candidate_xlsx_key", "VARCHAR(512)"),
        ("final_xlsx_key", "VARCHAR(512)"),
    ]

    with engine.begin() as connection:
        for name, type_def in columns:
            if name not in existing_columns:
                connection.execute(text(f"ALTER TABLE archive ADD COLUMN {name} {type_def}"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_archive_parse_status ON archive (parse_status)"))


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2 and sys.argv[1] == "diagnose_blob":
        engine = get_engine()
        report = diagnose_blob_integrity(engine)
        print(f"dialect={report['dialect']} sections={len(report['sections'])}")
        _print_compact_report(report)
