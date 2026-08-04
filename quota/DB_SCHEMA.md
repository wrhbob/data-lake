# Database Schema（file-asset 服务）

> 用途：给运维一份可直接拿到 PostgreSQL 跑的 schema 总览 + 关键 DDL + quota_parser 新加列的 ALTER TABLE 脚本。
>
> 配套文档：
> - 业务层：[`quota/README.md`](README.md)、[`quota/INTEGRATION_PLAN.md`](INTEGRATION_PLAN.md)
> - DDL 权威源：`file_asset_service/app/models.py`（SQLAlchemy 2.0 declarative）
> - 一键导出：`python -c "from app.models import Base; print(Base.metadata)"` → 用 `Base.metadata.create_all(engine)` 生成完整 CREATE TABLE
>
> **v0.4 更新**（2026-07-29）：
> 1. 新增 §3.9 `quota_parse_job` 队列表（DB 队列 + 单 worker 串行消费）
> 2. §3.10 4 个新 `archive_file.file_role`：`parse_markdown` / `parse_html` / `parse_candidate_xlsx` / `parse_final_xlsx`
> 3. §5 ALTER TABLE 增 §5.3 改 `ck_archive_file_role` CheckConstraint + §5.4 新增 `quota_parse_job` 表
> 4. §6 验证 SQL 加 4 项

---

## 1. 表清单（23 张）

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
| 24 | **`quota_parse_job`**（v0.4 新增） | `QuotaParseJob` | **解析任务队列表（v0.4 web 集成）** |

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

### 3.9 `quota_parse_job`（v0.4 新增 · 解析任务队列表）

> web 端点 `POST /parse` 只写库不入进程——所有真实解析任务由独立 `quota_parser_worker` 进程单 worker 串行消费本表。
> 背景与决策见 `quota/INTEGRATION_PLAN.md` §1.2 与 `quota/web-frontend/SPEC.md` §8.2。

```sql
CREATE TABLE quota_parse_job (
    job_id           VARCHAR(36) PRIMARY KEY,
    archive_id       VARCHAR(36) NOT NULL REFERENCES archive(archive_id) ON DELETE CASCADE,
    profile          VARCHAR(32) NOT NULL,  -- sichuan / chongqing
    status           VARCHAR(16) NOT NULL DEFAULT 'queued',  -- queued/running/done/failed/cancelled
    attempt          INTEGER NOT NULL DEFAULT 0,  -- 已尝试次数（transient 重试计数）
    max_attempts     INTEGER NOT NULL DEFAULT 3,  -- transient 失败最大重试
    worker_pid       INTEGER,  -- 抢到该 job 的 worker 进程 PID
    worker_hostname  VARCHAR(128),  -- 抢到该 job 的 worker 所在主机（多机部署用）
    enqueued_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    started_at       TIMESTAMP WITH TIME ZONE,  -- 抢到时写
    finished_at      TIMESTAMP WITH TIME ZONE,  -- done/failed/cancelled 时写
    error_code       VARCHAR(32),  -- transient / failed_permanent / failed_user（与 Archive.parse_error_code 同枚举）
    error_message    TEXT,
    parse_task_id    VARCHAR(64),  -- 关联 Archive.parse_task_id，便于 join 查
    created_by       VARCHAR(128),  -- 哪个 web 用户触发了这次解析
    metadata         JSONB NOT NULL DEFAULT '{}'::jsonb,

    CONSTRAINT ck_quota_parse_job_status CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
    -- v0.8: profile 与 quota_api._VALID_PROFILES 同步（32 个 GB/T 28039 拼音长名）。
    -- 入库 ≠ 解析：所有省级单位（含深圳）都能入库并写入 profile，但 worker
    -- has_parser_for() 守卫决定能否真正解析。详见 quota/README.md §8 铁律 3。
    CONSTRAINT ck_quota_parse_job_profile CHECK (profile IN (
        'beijing','tianjin','hebei','shanxi','neimenggu','liaoning','jilin','heilongjiang',
        'shanghai','jiangsu','zhejiang','anhui','fujian','jiangxi','shandong','henan',
        'hubei','hunan','guangdong','guangxi','hainan','chongqing','sichuan','guizhou',
        'yunnan','xizang','shaanxi','gansu','qinghai','ningxia','xinjiang','shenzhen'
    )),
    CONSTRAINT ck_quota_parse_job_attempt CHECK (attempt >= 0 AND attempt <= max_attempts)
);

-- worker 抢单查询：status='queued' ORDER BY enqueued_at LIMIT 1 FOR UPDATE SKIP LOCKED
CREATE INDEX ix_quota_parse_job_status_enqueued ON quota_parse_job (status, enqueued_at);
-- 查某档案的解析历史
CREATE INDEX ix_quota_parse_job_archive_id ON quota_parse_job (archive_id);
-- 失败排查：error_code 非空的最近 50 条
CREATE INDEX ix_quota_parse_job_status_error ON quota_parse_job (status, error_code) WHERE error_code IS NOT NULL;
```

**关键不变量**：
1. 同一时刻**至多一条** `status='running'` 记录——worker 全局只启 1 个，进程级 `fcntl.flock` 二次保护（防运维误启多 worker）。
2. `archive_id + status='queued' or 'running'` 唯一索引建议（v0.5 再加）——目前靠 `POST /parse` 端点 SELECT FOR UPDATE 防并发入队。
3. `attempt <= max_attempts` 触发后自动转 `failed_user`（不再 transient）。
4. 删除 Archive 时 `ON DELETE CASCADE` 同步删 `quota_parse_job`；删 #9「删除解析结果」也物理删 `quota_parse_job`；删 #10「删除档案」通过 CASCADE 自然清理。

### 3.10 `archive_file.file_role` 4 个新枚举（v0.4 扩展）

`ck_archive_file_role` CheckConstraint 在 v0.3 是 23 个 role；v0.4 加 4 个解析中间产物 role：

| 新 role | 含义 | 谁写 | 对应 MinIO key | 谁读 |
|---|---|---|---|---|
| `parse_markdown` | MinerU 解析出的 markdown（OCR + 表格结构） | worker 阶段 A | `quota/<archive_id>/artifacts/result.md` | 详情页 / 列表 ⋯ 下拉下载 |
| `parse_html` | MinerU 解析出的 html（带 `<table>` 嵌入） | worker 阶段 A | `quota/<archive_id>/artifacts/result.html` | 详情页 / 列表 ⋯ 下拉下载 |
| `parse_candidate_xlsx` | 阶段 A 输出的待审核 xlsx | worker 阶段 A | `quota/<archive_id>/candidate.xlsx`（与 `Archive.candidate_xlsx_key` 同 key） | #3 下载 candidate |
| `parse_final_xlsx` | 阶段 B 输出的最终 xlsx | worker 阶段 B | `quota/<archive_id>/final.xlsx`（与 `Archive.final_xlsx_key` 同 key） | #5 下载 final |

**幂等保证**：role 值与 `Archive.candidate_xlsx_key` / `final_xlsx_key` 列冗余，但 list 端点 `archive.file_count` **只数 `file_role IN ('main_document', 'priced_source')` 不数 4 类 parse_***，避免把同一物理文件重复计入。

`file_id` 字段语义：4 个 parse_* role 共享 `Archive` 的同一 file_id（`Archive` 的主 file），但通过 `file_role` 区分逻辑用途；这样 `archive_file` 行有 1 个 `file_asset.file_id` 但 4 条 `file_role` 行（materialized view 视角下是 1 PDF 物理文件 + 4 逻辑角色）。

> 不为 4 个新 role 加 FK / 强约束——`file_role` 是受控词表（CheckConstraint 白名单），不需要额外关系。

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

-- ============================================================
-- v0.4 新增（2026-07-29）— 中间产物入 archive_file + 解析队列表
-- ============================================================

-- 5.3 ck_archive_file_role CheckConstraint：加 4 个新 role
-- PostgreSQL 改 CheckConstraint 必须先 drop 再 add（无法直接 ALTER）
ALTER TABLE archive_file DROP CONSTRAINT IF EXISTS ck_archive_file_role;
ALTER TABLE archive_file ADD CONSTRAINT ck_archive_file_role CHECK (file_role IN (
    'main_document', 'attachment', 'priced_source', 'qingdan_package',
    'drawing', 'geological', 'tender_doc',
    'web_snapshot', 'zip_package', 'preview',
    'quota_db', 'bill_standard', 'quota_supplement', 'quota_interpretation',
    'atlas_document', 'standard_document', 'scan_image',
    'policy_document', 'policy_attachment', 'cover',
    'table_of_contents', 'appendix', 'release_announcement', 'other',
    -- v0.4 新增：
    'parse_markdown', 'parse_html', 'parse_candidate_xlsx', 'parse_final_xlsx'
));

-- 5.3 状态：已跑 (2026-07-30)
--   之前 v0.5 commit 改了 archive_rules.py:ARCHIVE_FILE_ROLES set 但漏跑本 ALTER,
--   导致 worker 写入 parse_markdown 时被 DB CheckConstraint 拒绝 → IntegrityError → failed_permanent
--   修复后跑了一次,DB 验证 28 个 role 全部生效。

-- 5.4 新增 quota_parse_job 队列表（v0.4 web 集成 · MinerU 单 worker 串行）
CREATE TABLE IF NOT EXISTS quota_parse_job (
    job_id           VARCHAR(36) PRIMARY KEY,
    archive_id       VARCHAR(36) NOT NULL REFERENCES archive(archive_id) ON DELETE CASCADE,
    profile          VARCHAR(32) NOT NULL,
    status           VARCHAR(16) NOT NULL DEFAULT 'queued',
    attempt          INTEGER NOT NULL DEFAULT 0,
    max_attempts     INTEGER NOT NULL DEFAULT 3,
    worker_pid       INTEGER,
    worker_hostname  VARCHAR(128),
    enqueued_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
    started_at       TIMESTAMP WITH TIME ZONE,
    finished_at      TIMESTAMP WITH TIME ZONE,
    error_code       VARCHAR(32),
    error_message    TEXT,
    parse_task_id    VARCHAR(64),
    created_by       VARCHAR(128),
    metadata         JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT ck_quota_parse_job_status CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled')),
    -- v0.8: profile 与 quota_api._VALID_PROFILES 同步（32 个 GB/T 28039 拼音长名）。
    -- 入库 ≠ 解析：所有省级单位（含深圳）都能入库并写入 profile，但 worker
    -- has_parser_for() 守卫决定能否真正解析。详见 quota/README.md §8 铁律 3。
    CONSTRAINT ck_quota_parse_job_profile CHECK (profile IN (
        'beijing','tianjin','hebei','shanxi','neimenggu','liaoning','jilin','heilongjiang',
        'shanghai','jiangsu','zhejiang','anhui','fujian','jiangxi','shandong','henan',
        'hubei','hunan','guangdong','guangxi','hainan','chongqing','sichuan','guizhou',
        'yunnan','xizang','shaanxi','gansu','qinghai','ningxia','xinjiang','shenzhen'
    )),
    CONSTRAINT ck_quota_parse_job_attempt CHECK (attempt >= 0 AND attempt <= max_attempts)
);
CREATE INDEX IF NOT EXISTS ix_quota_parse_job_status_enqueued ON quota_parse_job (status, enqueued_at);
CREATE INDEX IF NOT EXISTS ix_quota_parse_job_archive_id ON quota_parse_job (archive_id);
CREATE INDEX IF NOT EXISTS ix_quota_parse_job_status_error ON quota_parse_job (status, error_code) WHERE error_code IS NOT NULL;
```

**幂等保证**：
- §5.1 用 `ADD COLUMN IF NOT EXISTS`；
- §5.3 `DROP CONSTRAINT IF EXISTS` + `ADD CONSTRAINT`（PG 8.0+）；
- §5.4 `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS`。
- 反复跑这段 SQL 不会报错。

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

-- 6.3 quota 6 张表都存在
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name LIKE 'quota_%'
ORDER BY table_name;
-- 期望 6 行：quota_archive_profile / quota_dictionary / quota_parse_job (v0.4) /
--            quota_projection_candidate / quota_publication_relation / quota_publication_set

-- 6.4 file_processing 表存在
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public' AND table_name = 'file_processing';
-- 期望 1 行

-- ============================================================
-- v0.4 新增验证
-- ============================================================

-- 6.5 ck_archive_file_role CheckConstraint 含 4 个 parse_* role
SELECT conname, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conname = 'ck_archive_file_role';
-- 期望 definition 包含: 'parse_markdown' / 'parse_html' / 'parse_candidate_xlsx' / 'parse_final_xlsx'

-- 6.6 quota_parse_job 表存在 + 3 个约束
SELECT conname, contype FROM pg_constraint
WHERE conrelid = 'quota_parse_job'::regclass
ORDER BY conname;
-- 期望 3 行：ck_quota_parse_job_attempt / ck_quota_parse_job_profile / ck_quota_parse_job_status

-- 6.7 quota_parse_job 3 个索引存在
SELECT indexname FROM pg_indexes WHERE tablename = 'quota_parse_job' ORDER BY indexname;
-- 期望 3 行：ix_quota_parse_job_archive_id / ix_quota_parse_job_status_enqueued / ix_quota_parse_job_status_error
```

---

## 7. 创建顺序建议

1. **空库** → 跑 §2 脚本一次性 `create_all` → 跑 §5 ALTER（幂等 no-op）
2. **已有库**（archive 表已存在）→ 跑 §2 脚本（自动跳过 archive 表已存在的部分）→ 跑 §5 ALTER 补 13 列 + §5.3 改 CheckConstraint + §5.4 新表
3. 跑 §6 验证 SQL 确认

---

## 8. 不在本文件范围内

- **MinIO bucket**：quota 解析产物**复用** cost 域的 `cost-extract` / `cost-report` 桶（quota/README.md §12.G 决策 4）。key 前缀 `quota/<archive_id>/{candidate,final}.xlsx` 与 cost 域 `cost/...` 区分。v0.4 扩展：md/html 进 `cost-extract` 桶，路径 `quota/<archive_id>/artifacts/result.{md,html}`。详见 `quota/INTEGRATION_PLAN.md` §2.3。
- **quota_parser worker 进程**：v0.4 改用 `quota_parse_job` 表（§3.9）+ 独立 `quota_parser_worker.py` 进程单 worker 串行；不再用 `file_processing` 表（v0.3 §3.1 决策作废）。
- **历史数据回填**：13 列默认 NULL，无需回填；解析任务状态从零开始。

---

**锁版本**：v2（2026-07-29，v0.4 web 集成）。
后续加 quota 域新表（`quota_item` / `consumption` / `qa_printed_price` 等）时，更新本文档。