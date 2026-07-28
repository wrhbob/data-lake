# Database Schema（file-asset 服务）

> 用途：给运维一份可直接拿到 PostgreSQL 跑的 schema 总览 + 关键 DDL + quota_parser 新加列的 ALTER TABLE 脚本。
>
> 配套文档：
> - 业务层：[`quota/README.md`](README.md)、[`quota/INTEGRATION_PLAN.md`](INTEGRATION_PLAN.md)
> - DDL 权威源：`file_asset_service/app/models.py`（SQLAlchemy 2.0 declarative）
> - 一键导出：`python -c "from app.models import Base; print(Base.metadata)"` → 用 `Base.metadata.create_all(engine)` 生成完整 CREATE TABLE

---

## 1. 表清单（22 张）

按模型在 `models.py` 中的出现顺序：

| # | 表名 | 模型 | 用途 |
|---|---|---|---|
| 1 | `data_source` | `DataSource` | 数据源注册（采集入口） |
| 2 | `collection_task` | `CollectionTask` | 单次采集任务（调度 + 重试） |
| 3 | `source_crawl_state` | `SourceCrawlState` | 数据源爬虫状态（next_run / cursor） |
| 4 | `crawl_campaign` | `CrawlCampaign` | 历史批量爬虫批次 |
| 5 | `crawl_item` | `CrawlItem` | 批次下的 item 清单 |
| 6 | `blob` | `Blob` | 物理 blob（去重 by sha256） |
| 7 | `file_asset` | `FileAsset` | 文件资产（业务可寻址入口） |
| 8 | `ingest_event` | `IngestEvent` | 入库事件（file ↔ source ↔ task 关联） |
| 9 | **`file_processing`** | `FileProcessing` | **worker 调度 + 产出 key（quota_parser 用）** |
| 10 | `file_relation` | `FileRelation` | 文件关系（关联引用） |
| 11 | **`archive`** | `Archive` | **档案（42 列，含 quota_parser 新加 13 列）** |
| 12 | `archive_file` | `ArchiveFile` | 档案与文件的关联（含 page_range / file_role） |
| 13 | `archive_event` | `ArchiveEvent` | 档案事件审计流 |
| 14 | `outbox` | `Outbox` | 事件 outbox（去重 + 重试） |
| 15 | `audit_log` | `AuditLog` | 审计日志 |
| 16 | `crawl_lineage` | `CrawlLineage` | 爬虫血缘（追溯链） |
| 17 | `city_period_scheme` | `CityPeriodScheme` | 月度节拍（每城市每月几号发布） |
| 18 | **`quota_publication_set`** | `QuotaPublicationSet` | **资料体系（SPEC-QA-001 §6.3）** |
| 19 | **`quota_archive_profile`** | `QuotaArchiveProfile` | **档案扩展（SPEC-QA-001 §6.4）** |
| 20 | **`quota_projection_candidate`** | `QuotaProjectionCandidate` | **待归档候选（SPEC-QA-001 §6.6）** |
| 21 | **`quota_publication_relation`** | `QuotaPublicationRelation` | **资料体系关系（SPEC-QA-001 §6.7）** |
| 22 | **`quota_dictionary`** | `QuotaDictionary` | **受控字典（SPEC-QA-001 §7 D1）** |
| 23 | **`administrative_division`** | `AdministrativeDivision` | **行政区划（GB/T 2260-2024）** |

> **粗体**表是与 quota_parser / quota 域直接相关的核心表，DDL 见 §3。

---

## 2. 一键导出完整 DDL（推荐）

用 SQLAlchemy 直接生成 PG 完整 CREATE TABLE（**不含**已存在表的 ALTER TABLE；那是 §5 的事）：

```bash
PY=/d/miniconda3/envs/file-asset/python.exe
$PY <<'PY'
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import postgresql
from app.models import Base

for table in Base.metadata.sorted_tables:
    ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    print(ddl + ";")
    print()
PY
```

把输出存到 `schema.sql` 后执行：

```bash
psql -h <host> -U <user> -d file_asset -f schema.sql
```

> 注意：`create_all` 默认 `checkfirst=True`，已存在表会跳过；**不会**做 ALTER TABLE 加新列。

---

## 3. 关键表 DDL（手写版，供核对）

### 3.1 `file_processing`（worker 调度表，quota_parser 直接读写）

```sql
CREATE TABLE file_processing (
    processing_id   VARCHAR(36) PRIMARY KEY,
    file_id         VARCHAR(36) NOT NULL REFERENCES file_asset(file_id),
    processor       VARCHAR(64) NOT NULL,  -- 例: 'quota_parser_v2'
    status          VARCHAR(32) NOT NULL DEFAULT 'pending',  -- pending/running/completed/failed
    attempt         INTEGER NOT NULL DEFAULT 0,
    output_bucket   VARCHAR(128),
    output_key      VARCHAR(512),
    error           TEXT,
    started_at      TIMESTAMP WITH TIME ZONE,
    finished_at     TIMESTAMP WITH TIME ZONE
);

CREATE INDEX ix_file_processing_file_id ON file_processing (file_id);
CREATE INDEX ix_file_processing_processor ON file_processing (processor);
```

> quota_parser 未来真 worker 用：`status='pending' AND processor='quota_parser_v2'` 拉任务。
> 今天 mock 模式不需要写这表（mock 跳过 FileProcessing 直接更新 Archive.parse_*）。

### 3.2 `archive`（档案主表，42 列，含 quota_parser 新加 13 列）

```sql
CREATE TABLE archive (
    archive_id              VARCHAR(36) PRIMARY KEY,
    domain_type             VARCHAR(64) NOT NULL,
    channel_type            VARCHAR(32) NOT NULL,
    collection_method       VARCHAR(32) NOT NULL DEFAULT 'auto',
    price_kind              VARCHAR(32) NOT NULL DEFAULT 'unspecified',
    period_kind             VARCHAR(32) NOT NULL DEFAULT 'monthly',
    business_key            TEXT NOT NULL,
    title                   TEXT NOT NULL,
    region_code             VARCHAR(32),
    publish_date            DATE,
    coverage_region_code    VARCHAR(32),
    coverage_period         VARCHAR(7),
    source_id               VARCHAR(36) NOT NULL REFERENCES data_source(source_id),
    task_id                 VARCHAR(36) REFERENCES collection_task(task_id),
    batch_id                VARCHAR(128),
    source_item_key         TEXT,
    source_url              TEXT,
    tenant_code             VARCHAR(64) NOT NULL,
    visibility_scope        VARCHAR(32) NOT NULL DEFAULT 'public',
    status                  VARCHAR(32) NOT NULL DEFAULT 'pending_tag',
    version                 INTEGER NOT NULL DEFAULT 1,
    is_current              BOOLEAN NOT NULL DEFAULT TRUE,
    is_withdrawn            BOOLEAN NOT NULL DEFAULT FALSE,
    metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata_schema_version VARCHAR(32) NOT NULL DEFAULT 'v1',
    field_sources           JSONB NOT NULL DEFAULT '{}'::jsonb,
    preview_status          VARCHAR(32) NOT NULL DEFAULT 'none',

    -- === quota_parser integration (v0.3, 2026-07-28) · §5 ALTER 段同款 ===
    parse_status            VARCHAR(32),       -- pending/parsing/parsed/qa_passed/usable/failed_user/failed_permanent/transient
    parse_profile           VARCHAR(32),       -- sichuan/chongqing
    parse_task_id           VARCHAR(64),
    parse_phase             VARCHAR(16),       -- stage_a/stage_b
    parse_parser_version    VARCHAR(32),
    parse_started_at        TIMESTAMP WITH TIME ZONE,
    parse_finished_at       TIMESTAMP WITH TIME ZONE,
    parse_metrics           JSONB,
    parse_warnings          JSONB,
    parse_error_code        VARCHAR(32),       -- transient/failed_permanent/failed_user
    parse_error_message     TEXT,
    candidate_xlsx_key      VARCHAR(512),      -- quota/<archive_id>/candidate.xlsx
    final_xlsx_key          VARCHAR(512),      -- quota/<archive_id>/final.xlsx

    created_at              TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at              TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),

    -- === CheckConstraints (与 models.py 一致) ===
    CONSTRAINT ck_archive_domain_type CHECK (domain_type IN ('cost_info', 'trading', 'policy_regulation', 'standard_atlas', 'quota')),
    CONSTRAINT ck_archive_channel_type CHECK (channel_type IN ('crawler', 'netdisk', 'system_api', 'manual_upload', 'batch_import', 'email_import')),
    CONSTRAINT ck_archive_collection_method CHECK (collection_method IN ('auto', 'manual_recovery', 'manual_denovo', 'legacy_batch_import')),
    CONSTRAINT ck_archive_price_kind CHECK (price_kind IN ('guidance', 'market_reference', 'manufacturer_reference', 'unspecified')),
    CONSTRAINT ck_archive_period_kind CHECK (period_kind IN ('monthly', 'issue_based', 'bimonthly')),
    CONSTRAINT ck_archive_visibility_scope CHECK (visibility_scope IN ('public', 'tenant', 'private')),
    CONSTRAINT ck_archive_status CHECK (status IN (
        'discovered', 'collecting', 'collect_failed', 'quarantined',
        'collected', 'pending_tag', 'archived', 'ready_for_governance'
    )),

    CONSTRAINT uq_archive_business_version UNIQUE (tenant_code, domain_type, business_key, version)
);

CREATE INDEX ix_archive_domain_type          ON archive (domain_type);
CREATE INDEX ix_archive_channel_type         ON archive (channel_type);
CREATE INDEX ix_archive_collection_method    ON archive (collection_method);
CREATE INDEX ix_archive_price_kind           ON archive (price_kind);
CREATE INDEX ix_archive_period_kind          ON archive (period_kind);
CREATE INDEX ix_archive_region_code          ON archive (region_code);
CREATE INDEX ix_archive_publish_date         ON archive (publish_date);
CREATE INDEX ix_archive_coverage_region_code ON archive (coverage_region_code);
CREATE INDEX ix_archive_coverage_period      ON archive (coverage_period);
CREATE INDEX ix_archive_source_id            ON archive (source_id);
CREATE INDEX ix_archive_task_id              ON archive (task_id);
CREATE INDEX ix_archive_batch_id             ON archive (batch_id);
CREATE INDEX ix_archive_tenant_code          ON archive (tenant_code);
CREATE INDEX ix_archive_visibility_scope     ON archive (visibility_scope);
CREATE INDEX ix_archive_status               ON archive (status);
CREATE INDEX ix_archive_preview_status       ON archive (preview_status);
-- quota_parser 新加：
CREATE INDEX ix_archive_parse_status         ON archive (parse_status);
```

> 注：13 列新字段都 nullable，无默认值；**不**加 CheckConstraint（与 quota/INTEGRATION_PLAN.md §2.1 方案 A 一致）。

### 3.3 `quota_publication_set`（资料体系，SPEC-QA-001 §6.3）

```sql
CREATE TABLE quota_publication_set (
    publication_set_id        VARCHAR(36) PRIMARY KEY,
    biz_key                   TEXT NOT NULL,
    publication_family_code   VARCHAR(128) NOT NULL,
    title                     TEXT NOT NULL,
    material_type             VARCHAR(32) NOT NULL,
    quota_system_type         VARCHAR(32),
    jurisdiction_level        VARCHAR(16) NOT NULL,
    jurisdiction_code         VARCHAR(32) NOT NULL,
    industry_sector_code      VARCHAR(64),
    issuer_name               VARCHAR(255) NOT NULL,
    standard_or_quota_code    VARCHAR(128),
    edition_label             VARCHAR(64) NOT NULL,
    edition_year              INTEGER,
    publish_date              DATE,
    effective_date            DATE,
    repeal_date               DATE,
    legal_status              VARCHAR(16) NOT NULL DEFAULT 'unknown',
    expected_volume_count     INTEGER,
    metadata_status           VARCHAR(16) NOT NULL DEFAULT 'missing',
    tenant_code               VARCHAR(64) NOT NULL,
    visibility_scope          VARCHAR(32) NOT NULL DEFAULT 'public',
    created_by                VARCHAR(128),
    updated_by                VARCHAR(128),
    created_at                TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at                TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),

    CONSTRAINT uq_quota_publication_set_biz_key UNIQUE (biz_key),
    CONSTRAINT ck_quota_publication_set_jurisdiction_level CHECK (jurisdiction_level IN ('national', 'provincial', 'municipal', 'county')),
    CONSTRAINT ck_quota_publication_set_legal_status CHECK (legal_status IN ('active', 'repealed', 'unknown')),
    CONSTRAINT ck_quota_publication_set_metadata_status CHECK (metadata_status IN ('missing', 'incomplete', 'verified'))
);

CREATE INDEX ix_quota_publication_set_material_type ON quota_publication_set (material_type);
CREATE INDEX ix_quota_publication_set_quota_system_type ON quota_publication_set (quota_system_type);
CREATE INDEX ix_quota_publication_set_jurisdiction_level ON quota_publication_set (jurisdiction_level);
CREATE INDEX ix_quota_publication_set_legal_status ON quota_publication_set (legal_status);
CREATE INDEX ix_quota_publication_set_metadata_status ON quota_publication_set (metadata_status);
CREATE INDEX ix_quota_publication_set_tenant_code ON quota_publication_set (tenant_code);
CREATE INDEX ix_quota_publication_set_visibility_scope ON quota_publication_set (visibility_scope);
CREATE INDEX ix_quota_publication_set_publication_family_code ON quota_publication_set (publication_family_code);
CREATE INDEX ix_quota_publication_set_edition_year ON quota_publication_set (edition_year);
CREATE INDEX ix_quota_publication_set_sys_juris_year ON quota_publication_set (quota_system_type, jurisdiction_code, edition_year);
CREATE INDEX ix_quota_publication_set_sys_sector_year ON quota_publication_set (quota_system_type, industry_sector_code, edition_year);
```

### 3.4 `quota_archive_profile`（档案扩展，1:1 with Archive）

```sql
CREATE TABLE quota_archive_profile (
    archive_id              VARCHAR(36) PRIMARY KEY REFERENCES archive(archive_id),
    publication_set_id      VARCHAR(36) NOT NULL REFERENCES quota_publication_set(publication_set_id),
    document_role           VARCHAR(32) NOT NULL,
    discipline_code         VARCHAR(64),
    volume_code             VARCHAR(64),
    volume_title            TEXT,
    part_no                 INTEGER,
    language                VARCHAR(16) NOT NULL DEFAULT 'zh-CN',
    metadata_status         VARCHAR(16) NOT NULL DEFAULT 'missing',
    completeness_score      INTEGER NOT NULL DEFAULT 0,
    completeness_blockers   JSONB NOT NULL DEFAULT '[]'::jsonb,
    notes                   TEXT,
    created_at              TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at              TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),

    CONSTRAINT ck_quota_archive_profile_document_role CHECK (document_role IN ('base', 'adjustment', 'fee', 'mapping', 'interpretation', 'errata', 'supplement')),
    CONSTRAINT ck_quota_archive_profile_metadata_status CHECK (metadata_status IN ('missing', 'incomplete', 'verified')),
    CONSTRAINT ck_quota_archive_profile_completeness CHECK (completeness_score BETWEEN 0 AND 100)
);

CREATE INDEX ix_quota_archive_profile_publication_set_id ON quota_archive_profile (publication_set_id);
CREATE INDEX ix_quota_archive_profile_document_role ON quota_archive_profile (document_role);
CREATE INDEX ix_quota_archive_profile_metadata_status ON quota_archive_profile (metadata_status);
CREATE INDEX ix_quota_archive_profile_discipline_code ON quota_archive_profile (discipline_code);
CREATE INDEX ix_quota_archive_profile_set_discipline ON quota_archive_profile (publication_set_id, discipline_code);
```

### 3.5 `quota_projection_candidate`（待归档候选）

```sql
CREATE TABLE quota_projection_candidate (
    candidate_id                  VARCHAR(36) PRIMARY KEY,
    file_id                       VARCHAR(36) NOT NULL REFERENCES file_asset(file_id),
    projection_status             VARCHAR(16) NOT NULL DEFAULT 'pending',
    suggested_publication_set_id  VARCHAR(36) REFERENCES quota_publication_set(publication_set_id),
    suggested_archive_id          VARCHAR(36) REFERENCES archive(archive_id),
    suggested_metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    suggestion_confidence         FLOAT,
    matched_rules                 JSONB NOT NULL DEFAULT '[]'::jsonb,
    duplicate_of_file_id          VARCHAR(36) REFERENCES file_asset(file_id),
    resolved_archive_id           VARCHAR(36) REFERENCES archive(archive_id),
    resolution_note               TEXT,
    resolved_by                   VARCHAR(128),
    resolved_at                   TIMESTAMP WITH TIME ZONE,
    created_at                    TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at                    TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),

    CONSTRAINT uq_quota_projection_candidate_file UNIQUE (file_id),
    CONSTRAINT ck_quota_projection_candidate_status CHECK (projection_status IN ('pending', 'auto_resolved', 'reviewed', 'applied', 'rejected')),
    CONSTRAINT ck_quota_projection_candidate_confidence CHECK (suggestion_confidence IS NULL OR (suggestion_confidence >= 0 AND suggestion_confidence <= 1))
);

CREATE INDEX ix_quota_projection_candidate_projection_status ON quota_projection_candidate (projection_status);
CREATE INDEX ix_quota_projection_candidate_suggested_publication_set_id ON quota_projection_candidate (suggested_publication_set_id);
CREATE INDEX ix_quota_projection_candidate_suggested_archive_id ON quota_projection_candidate (suggested_archive_id);
CREATE INDEX ix_quota_projection_candidate_duplicate_of_file_id ON quota_projection_candidate (duplicate_of_file_id);
CREATE INDEX ix_quota_projection_candidate_resolved_archive_id ON quota_projection_candidate (resolved_archive_id);
```

### 3.6 `quota_publication_relation`（资料体系关系）

```sql
CREATE TABLE quota_publication_relation (
    relation_id                  VARCHAR(36) PRIMARY KEY,
    source_publication_set_id    VARCHAR(36) NOT NULL REFERENCES quota_publication_set(publication_set_id),
    target_publication_set_id    VARCHAR(36) NOT NULL REFERENCES quota_publication_set(publication_set_id),
    relation_type                VARCHAR(32) NOT NULL,
    evidence                     TEXT,
    source_ref                   TEXT,
    created_by                   VARCHAR(128),
    created_at                   TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),

    CONSTRAINT uq_quota_publication_relation_triple UNIQUE (source_publication_set_id, target_publication_set_id, relation_type),
    CONSTRAINT ck_quota_publication_relation_type CHECK (relation_type IN ('replaced_by', 'adjusts', 'interprets', 'extends', 'compiles')),
    CONSTRAINT ck_quota_publication_relation_not_self CHECK (source_publication_set_id <> target_publication_set_id)
);

CREATE INDEX ix_quota_publication_relation_relation_type ON quota_publication_relation (relation_type);
```

### 3.7 `quota_dictionary`（受控字典）

```sql
CREATE TABLE quota_dictionary (
    dict_id     VARCHAR(36) PRIMARY KEY,
    dict_type   VARCHAR(64) NOT NULL,
    code        VARCHAR(64) NOT NULL,
    label       VARCHAR(255) NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 100,
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),

    CONSTRAINT uq_quota_dictionary_type_code UNIQUE (dict_type, code)
);

CREATE INDEX ix_quota_dictionary_dict_type ON quota_dictionary (dict_type);
```

### 3.8 `administrative_division`（行政区划 GB/T 2260-2024）

```sql
CREATE TABLE administrative_division (
    code        VARCHAR(6) PRIMARY KEY,
    name        VARCHAR(64) NOT NULL,
    level       VARCHAR(16) NOT NULL,
    parent_code VARCHAR(6) REFERENCES administrative_division(code),
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    version     VARCHAR(32) NOT NULL DEFAULT 'GB/T2260-2024',
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    updated_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

CREATE INDEX ix_administrative_division_level ON administrative_division (level);
CREATE INDEX ix_administrative_division_parent_code ON administrative_division (parent_code);
```

---

## 4. 其他 14 张表

完整 DDL 用 §2 的脚本导出。`models.py` 里有完整定义。下面给出与 quota_parser / quota 域的关系速查：

| 表名 | 与 quota 关系 |
|---|---|
| `data_source` | quota 域有专属 `data_domain='quota'` 的源；由 CollectionTask 调度 |
| `collection_task` | quota 域档案的采集入口（推送 quota 档案前必走） |
| `source_crawl_state` | quota 数据源调度状态 |
| `crawl_campaign` / `crawl_item` | 历史爬虫批次 / item；quota 域历史入湖路径 |
| `blob` | quota PDF 物理文件去重 |
| `file_asset` | quota PDF 的业务可寻址入口；worker 通过 file_asset 取 PDF 路径 |
| `ingest_event` | quota PDF 入库事件（file ↔ source ↔ task 关联） |
| `archive_file` | quota 档案的文件关联（page_range / file_role） |
| `archive_event` | quota 档案事件审计 |
| `outbox` | quota 事件去重 |
| `audit_log` | quota 解析动作审计（`quota_parse_result_deleted` 等） |
| `crawl_lineage` | quota PDF 血缘 |
| `city_period_scheme` | quota 月度节拍 |

---

## 5. quota_parser integration · ALTER TABLE（**必须手工跑**）

> `Base.metadata.create_all(engine)` **不会** ALTER 已存在的表。如果 `archive` 表已有数据，**必须**手工跑这一节 SQL（与 `migrate_archive_parse_columns` 函数等价）。

```sql
-- 5.1 13 列（均为 nullable，无默认值）
ALTER TABLE archive ADD COLUMN IF NOT EXISTS parse_status            VARCHAR(32);
ALTER TABLE archive ADD COLUMN IF NOT EXISTS parse_profile           VARCHAR(32);
ALTER TABLE archive ADD COLUMN IF NOT EXISTS parse_task_id           VARCHAR(64);
ALTER TABLE archive ADD COLUMN IF NOT EXISTS parse_phase             VARCHAR(16);
ALTER TABLE archive ADD COLUMN IF NOT EXISTS parse_parser_version    VARCHAR(32);
ALTER TABLE archive ADD COLUMN IF NOT EXISTS parse_started_at        TIMESTAMP WITH TIME ZONE;
ALTER TABLE archive ADD COLUMN IF NOT EXISTS parse_finished_at       TIMESTAMP WITH TIME ZONE;
ALTER TABLE archive ADD COLUMN IF NOT EXISTS parse_metrics           JSONB;
ALTER TABLE archive ADD COLUMN IF NOT EXISTS parse_warnings          JSONB;
ALTER TABLE archive ADD COLUMN IF NOT EXISTS parse_error_code        VARCHAR(32);
ALTER TABLE archive ADD COLUMN IF NOT EXISTS parse_error_message     TEXT;
ALTER TABLE archive ADD COLUMN IF NOT EXISTS candidate_xlsx_key      VARCHAR(512);
ALTER TABLE archive ADD COLUMN IF NOT EXISTS final_xlsx_key          VARCHAR(512);

-- 5.2 索引
CREATE INDEX IF NOT EXISTS ix_archive_parse_status ON archive (parse_status);
```

**幂等保证**：`IF NOT EXISTS` 让这段 SQL 反复跑也不会报错。

---

## 6. 验证 SQL（跑完后请执行）

```sql
-- 6.1 13 列是否都存在
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'archive'
  AND column_name LIKE 'parse_%' OR column_name LIKE '%_xlsx_key'
ORDER BY column_name;
-- 期望 13 行（含 candidate_xlsx_key / final_xlsx_key）

-- 6.2 索引
SELECT indexname FROM pg_indexes
WHERE tablename = 'archive' AND indexname = 'ix_archive_parse_status';
-- 期望 1 行

-- 6.3 quota 5 张表都存在
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name LIKE 'quota_%'
ORDER BY table_name;
-- 期望 5 行：quota_archive_profile / quota_dictionary / quota_projection_candidate /
--            quota_publication_relation / quota_publication_set

-- 6.4 file_processing 表存在
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public' AND table_name = 'file_processing';
-- 期望 1 行
```

---

## 7. 创建顺序建议

1. **空库** → 跑 §2 脚本一次性 `create_all` → 跑 §5 ALTER（幂等 no-op）
2. **已有库**（archive 表已存在）→ 跑 §2 脚本（自动跳过 archive 表已存在的部分）→ 跑 §5 ALTER 补 13 列
3. 跑 §6 验证 SQL 确认

---

## 8. 不在本文件范围内

- **MinIO bucket**：quota 解析产物**复用** cost 域的 `cost-extract` / `cost-report` 桶（quota/README.md §12.G 决策 4）。key 前缀 `quota/<archive_id>/{candidate,final}.xlsx` 与 cost 域 `cost/...` 区分。详见 `quota/INTEGRATION_PLAN.md` §2.3。
- **quota_parser worker 进程**：用 §3.1 `file_processing` 表调度；今天 mock 模式不写
- **历史数据回填**：13 列默认 NULL，无需回填；解析任务状态从零开始

---

**锁版本**：v1（2026-07-28）。
后续加 quota 域新表（`quota_item` / `consumption` / `qa_printed_price` 等）时，更新本文档。