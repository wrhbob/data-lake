# SPEC-QA-001 · P0-3 增量复核结果 v0.1

针对 ChatGPT「P0-3 主体通过，暂不关闭」的 3 项收尾修正的落地说明（不重写整份报告，配合
`docs/quota_archive_p0_3_implementation_report_v0.1.md` 阅读）。

**测试总结：`python -m pytest tests/test_quota_migration.py tests/test_quota_projection.py -q` → 24 passed**
（迁移 11 项 + 投影/seed 13 项）。

---

## 修正 1 · 补 `edition_year=2025` 低置信建议（已完成）

- 新增证据链推导（`app/quota_projection.py::_edition_year_for`），**优先级顺序**：
  1. `IngestEvent.source_url`（完整原路径，留给未来真实爬虫）；
  2. `DataSource.name`（seed batch / 来源名，如 `四川2025清单定额-seed`）；
  3. `IngestEvent.source_item_key`（相对路径）。
- 写入候选：`{"edition_year": {"value": 2025, "source": "nas_path", "status": "suggested"}}`，`matched_rules` 记录实际命中的证据层级。
- 对**已存在且仍为 `pending`** 的候选提供幂等回填（`_enrich_pending_candidate`）：
  - 只回填缺失的 `edition_year`，**不覆盖已有值、不改动非 pending（人工处置）候选、不新建候选**；
  - `run_projection` 报告新增字段 `enriched_candidates`。
- 新增测试：
  - `test_edition_year_from_datasource_name_when_absent_from_source_item_key`——年份**只在根目录/DataSource 名、不在 `source_item_key`** 时仍能得到 2025（正是本批真实情况）；
  - `test_pending_candidate_edition_year_backfilled_on_rerun_without_recreating`——重跑 `newly_created=0`、`enriched=1`、候选数不变、发布集/档案仍 0，且人工处置候选不被覆盖。

> **真实库 enrich 重跑：按你选择的「暂缓，计划先行」推迟。** 原因见下方修正 2 触发的新发现——真实库 `archive_file` 缺 P0-2 列，schema 预检（正确）拦截写入。真实库现存 8 条候选暂缺 `edition_year`，待 schema 补齐后重跑即产出 8×2025（逻辑已由上述两项测试证明）。

---

## 修正 2 · `--skip-init` 增加 Schema 预检（已完成）

`app/quota_seed.py::verify_quota_schema` + `QuotaSchemaIncomplete`：

- `--skip-init` **仅显式启用，绝非默认**（两个 CLI 均为 `action="store_true"`）；
- **写 MinIO/DB 前**强制预检：P0-2 五张 quota 表存在、各自关键列齐全、`archive_file` 具备 P0-2 列 `page_range/link_source/linked_by`、`quota_projection_candidate` 具备 `UNIQUE(file_id)` 幂等约束；
- schema 不完整**立即拒绝**（在 `apply_seed` 建 DataSource / 写对象之前）；
- 错误信息明确提示：**「--skip-init skips MIGRATION, NOT schema compatibility checks」**；
- `apply_seed`（MinIO 写入方）与 `quota_projection --skip-init`（DB 写入方）均接入预检。
- 新增测试：
  - `test_verify_quota_schema_raises_when_table_missing`；
  - `test_skip_init_apply_refuses_and_writes_nothing_when_schema_incomplete`——缺表时 `--skip-init` 路径在写任何对象前失败，`FileAsset=0`、`DataSource=0`。

### 预检在真实库暴露的新发现（重要）
真实库 `archive_file` **缺少 P0-2 列** `page_range`/`link_source`/`linked_by`。
成因链：`init_db()` 顺序 `create_all`（已建 5 张 quota 新表）→ `migrate_blob_columns`（**因 legacy blob 问题中断**）→ `migrate_quota_tables`（负责给 `archive_file` 加这 3 列，**从未执行**）。
即真实库 **P0-2 仅部分部署**，是同一 legacy blob 阻塞的**下游症状**。首轮真实 apply/projection 能成功是因当时未引用这些列；预检上线后按要求正确拒绝。→ 已按选项 2 并入 **P0-3.5** 一并处置。

---

## 修正 3 · 补批准偏差的防回归测试（已完成）

新增 `tests/test_quota_migration.py::test_migration_allows_multiple_primary_and_creates_no_global_single_primary_index`：

- 非 quota 档案（`a-cost`）允许存在**多个 `is_primary=true` 表示行**，迁移不得阻断；
- 断言迁移**未创建**任何"全局单主文件"唯一索引/约束（不存在仅以 `archive_id` 为列的 unique index/constraint）；
- quota「恰好一个 `main_document`」仍留 **P0-4 应用层**校验。

这防止未来误加该全局约束（对应删除的 2 项正向用例的反向保护）。

---

## 关于 1960 条 Legacy Blob（措辞已更正，不在本轮修复）

准确表述：

> **1960 条历史 `FileAsset` 缺少可满足新外键（`file_asset_blob_hash_fkey` → `blob.blob_hash`）的 Blob 映射；物理对象是否可访问仍需单独核验。**

不称"1960 个原件丢失"。这不是 P0-3 造成，但已使正常 `init_db()` 无法完成，是进入真实 API 阶段前的**基础设施阻断**，并已连带导致 P0-2 `archive_file` 列在真实库未部署。

---

## 状态与下一步（严格按你的门禁）

- 3 项修正**全部完成**，24 passed；
- **P0-3 仍未关闭**——待本增量复核结果经 ChatGPT 复核通过；
- 通过后顺序执行：
  1. 关闭 P0-3；
  2. **不直接提交 P0-4 计划**；
  3. 先提交 **「P0-3.5 Legacy Blob 完整性诊断与修复计划」**（范围覆盖：① 1960 条孤儿 FileAsset↔Blob 映射诊断；② 真实库 `archive_file` P0-2 列补齐路径），**先只读盘点，任何真实数据/schema 修复均需该计划审核通过后执行**；
  4. P0-3.5 落地后再进入 P0-4。
