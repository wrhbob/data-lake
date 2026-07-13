# 清单定额档案台 V0.1 · 实施报告（SPEC-QA-001）

> 实施负责人：Cascade · 规格：`docs/quota_archive_spec_v0.1.md`（V0.1-R1）
> 本文件累积各 P0 阶段的实施结果。**当前进度：P0-2 完成，待 ChatGPT 复核。**

---

## P0-2 · 数据模型与迁移（完成）

范围严格限定：模型、增量迁移、约束/索引、受控字典、迁移测试。**未触及** API、投影、MinIO 入湖、前端。

### 1. 复用的公共能力（未新建平行底座，符合 §0.3.1 / §6.2）

- 统一 `Archive` / `ArchiveFile` / `ArchiveEvent`、`FileAsset` / `Blob` / `IngestEvent`、`Outbox` / `AuditLog`、`field_sources`、预览/下载接口——全部复用，未复制公共字段。
- quota 专属能力以 **1:1 扩展表 + 新表** 承载。

### 2. 逻辑模型 → 仓库实体映射

| SPEC 逻辑实体 | 实际表 / 类 | 说明 |
|---|---|---|
| Publication Set (§6.3) | `quota_publication_set` / `QuotaPublicationSet` | 资料体系 |
| Quota Archive Profile (§6.4) | `quota_archive_profile` / `QuotaArchiveProfile` | 1:1 扩展 `Archive`（PK=archive_id FK） |
| Projection Candidate (§6.6) | `quota_projection_candidate` / `QuotaProjectionCandidate` | `file_id` 唯一 |
| Publication Relation (§6.7) | `quota_publication_relation` / `QuotaPublicationRelation` | 版本/业务关系 |
| 受控字典 (§7 · D1) | `quota_dictionary` / `QuotaDictionary` | 唯一 `(dict_type, code)` |
| Archive File (§6.5) | 复用 `archive_file` / `ArchiveFile` | 增列 `page_range` / `link_source` / `linked_by` |
| 枚举 (§7) | `app/quota_taxonomy.py` | 全部小写；CHECK 由此生成 |

新表统一 `quota_` 前缀（强制修订 #1）。`archive_file` 外键沿用真实列名 `file_id`（#2）。

### 3. 枚举与字典（小写；#9 / D1）

- 固定枚举（CHECK 约束）：`material_type`、`quota_system_type`(`construction_regional`/`industry_specialty`)、`jurisdiction_level`、`legal_status`、`metadata_status`、`document_role`、`relation_type`、`projection_status`、`link_source`。
- 开放字典（不做 CHECK，随真实资料增长）：`industry_sector`、`discipline`。
- 首批 seed：`industry_sector` 12 类（water_resources…information_communication）+ `discipline` 仅 `general`（D1：不猜造其他 discipline）。`other` 未预置为“真实行业体系”。

### 4. 迁移机制（无 Alembic，沿用 `init_db` 幂等风格）

- `app/database.py::migrate_quota_tables()` 挂入 `init_db()`：新表 `create(checkfirst=True)`；`archive_file` 幂等 `ALTER ADD COLUMN` + `CREATE INDEX IF NOT EXISTS`；可重复执行。
- 回滚：`python -m app.quota_migration downgrade`（D4）
  - dev（默认）：业务表非空或 `archive_file` 新列含业务值 → 拒绝（`DowngradeRefused`），保护数据；干净时删索引/列/表。`quota_dictionary`（迁移自带 seed）不计入非空守卫，但会随 dev 回滚删除。
  - prod（`--prod`）：非破坏，仅撤无损索引，保留表与列。

### 5. 约束与索引（#3/#5/#6/#7 落实情况）

- **#3** `page_range NOT NULL DEFAULT ''`，唯一键 `uq_archive_file_role = (archive_id, file_id, file_role, page_range)`。
- **#4** 建唯一约束前扫描 `(archive_id,file_id,file_role,page_range)` 重复（排除 NULL file_id）；发现即抛 `QuotaMigrationBlocked` 停止，**不删数据**。
- **#6** `completeness_score∈[0,100]`；`suggestion_confidence∈[0,1] or null`；`expected_volume_count>=0 or null`；关系表禁自指 `source<>target`、唯一 `(source,target,relation_type)`；`industry_specialty` 必须有 `industry_sector_code`、`construction_regional` 必须无（跨 SQLite/PG 的 `<>`/`or` 写法，未用 `IS DISTINCT FROM`）。
- **#7** 组合索引 `(quota_system_type,jurisdiction_code,edition_year)`、`(quota_system_type,industry_sector_code,edition_year)`、`(publication_set_id,discipline_code)`。
- **#8** `tenant_code VARCHAR(64) NOT NULL` / `visibility_scope VARCHAR(32) NOT NULL DEFAULT 'public'`，复用 `Archive` 的类型/默认/命名。

### 6. ⚠ 与 R1 强制修订 #5 的偏差（需 ChatGPT 复核裁决）

**问题**：#5 要求全局“一个 Archive 最多一个主文件”约束。但现有 `cost_info` 通过 `_apply_representation_roles`（`app/archive_service.py:131-147`）会给**同一档案的多个 Excel 表示行**同时置 `is_primary=true`，且重排序时存在两行瞬时同真。全局部分唯一索引 `(archive_id) WHERE is_primary` 会**破坏 cost_info**（实测 3 个 cost_info 用例 IntegrityError）。

**处置**：这属于 §13.1“不得影响其他域”/§0.3 红线，且 #5 允许“**或等价保护**”。故 P0-2 **不建全局 DB 约束**，改为 **P0-4 在 quota 应用层做 quota 范围内的单主文件保护**（代码注释已标注于 `models.py` / `database.py`）。角色重复守卫（#4）保留，不受影响。

**请裁决**：认可“quota 范围内应用层保护”替代全局 DB 约束？若坚持 DB 级，需要在 `archive_file` 增加去规范化 `domain_type` 列并回填后做条件部分唯一索引——超出 P0-2 最小范围，需单独批准。

### 7. 测试命令与结果

```
python -m pytest tests/test_quota_migration.py -q
# 12 passed

python -m pytest tests/test_quota_migration.py tests/test_archive_service.py \
  tests/test_database.py tests/test_archive_models.py tests/test_storage_audit.py -q
# 61 passed

python -m pytest -q
# 528 passed, 3 skipped, 22 failed
```

`test_quota_migration.py`（12 项）覆盖 #10 全部要点：fresh SQLite 建表+seed、旧库升级（旧数据可读/连续两次幂等/其他域数量不变）、重复数据阻断约束创建且不删数据、dev 回滚遇业务数据拒绝、prod 回滚保表；另含 #6 约束用例。

**22 项失败全部为既有环境问题，与 P0-2 无关**（本机 Windows + Python 3.14，仓库 `.venv` 基础解释器失效，改用系统 Python）：

| 失败簇 | 根因 |
|---|---|
| `test_ui_filter_behavior.*`(13)、`test_observation_api.*`(4) | 用 `subprocess node -e` 执行 `app.js`，Windows `gbk` 解码 node stdout 报 `UnicodeDecodeError` |
| `test_quota_registry`(1) | 引用 macOS 真实样本路径 `/Users/tms/Desktop/...`，本机不存在 |
| `test_info_price_acceptance`(1) | `encoding='locale'` 读文件遇 `gbk` 解码失败 |
| `test_file_mirror_api`(1) | Windows 路径分隔符 `\` vs `/` 断言 |
| `test_sichuan_pdf_cost_info_crawler`(1) | `ModuleNotFoundError: tests.test_info_price_coverage`（测试导入路径） |

> 说明：本机无 PostgreSQL，PG 专属迁移分支（`ADD CONSTRAINT` / `DROP CONSTRAINT` / `DROP COLUMN IF EXISTS`）按 SQLite 等价路径与代码审阅验证；SQLite 的 dev 回滚 `DROP COLUMN` 为尽力而为（create_all 路径下遗留唯一约束可能阻止删列，已 try/except 容错，绝不删行数据）。

### 8. 未解决问题与风险

- 单主文件全局约束偏差（见 §6）待裁决。
- 无 git 仓库，无法用 stash 做基线 diff；22 项失败以逐条根因确认为既有环境问题。
- PG 分支未在本机实跑（无 PG 实例）。
