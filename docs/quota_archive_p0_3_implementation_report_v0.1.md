# SPEC-QA-001 · P0-3 实施报告 v0.1

> **增量更新（见 `docs/quota_archive_p0_3_review_result_v0.1.md`）**：已按 ChatGPT 复核补 3 项修正——
> ① `edition_year=2025` 低置信建议（证据链 source_url→DataSource 名→source_item_key + pending 回填）；
> ② `--skip-init` 增加 Schema 预检；③ 补"允许多主文件 / 不建全局单主索引"防回归测试。
> 本报告 §8.5「无 edition_year」与 §9-L2 已被修正 1 取代；真实库 enrich 重跑因预检发现的
> `archive_file` 缺 P0-2 列而按批准暂缓，并入 P0-3.5。

**状态：`EXECUTED`（真实 MinIO + PostgreSQL 已完成 dry-run / apply / projection / 守恒对账）。**
P0-3 **尚未关闭**——需 ChatGPT 复核通过后方可关闭，且关闭前不得进入 P0-4。

范围：四川2025 seed batch 原件盘点、原件入湖（raw-only）、投影候选、确定性去重、对账。
**未触及**：Archive/发布集自动建档、OCR/语义/智能计价、API、前端（均属后续阶段）。

---

## 1. 实际修改 / 新增文件

| 文件 | 类型 | 说明 |
|---|---|---|
| `file_asset_service/app/quota_seed.py` | 新增 | seed batch 原件入湖：`plan_seed`（dry-run）/`apply_seed`（写入）/CLI；同键 hash 冲突检测 |
| `file_asset_service/app/quota_projection.py` | 新增 | inventory 盘点、投影候选、去重 canonical、分类建议、对账报告、`snapshot_counts`；CLI（`run`/`report`/`snapshot`，`--skip-init`） |
| `file_asset_service/tests/test_quota_projection.py` | 新增 | 9 项 P0-3 测试 |

P0-3 **未修改** `models.py` / `database.py` / `archive_service.py` 等 P0-2 已定型文件（仅复用其能力）。

---

## 2. raw-only 入湖链路

复用既有 `app/assets.register_asset()`，参数固定为：

- `create_ingest_event=True`：记录来源与 `source_item_key`；
- `derive_tasks=False`：**L0 原件层**，不派生 OCR/解析/任何处理任务；
- `source_id` = quota seed 专用 `DataSource`（`data_domain='quota'`）。

产物仅：`DataSource` → `Blob` → `FileAsset` → `IngestEvent`。
**从不创建 `Archive` / `quota_publication_set` / `quota_archive_profile`**（建档属 P0-4，需人工确认）。

quota seed `DataSource` 稳定身份（幂等锚点之一）：
`source_scope=platform_public`、`asset_tenant_code=platform_public`、`source_type=quota_registry`、`data_domain=quota`、`name=四川2025清单定额-seed`、`region_code=510000`。

---

## 3. dry-run 与 `--apply` 安全边界

- **默认 dry-run**：只读磁盘计算 `sha256`/大小、查询是否已入湖，**不写存储/不写库**；先打印计划（文件清单 + hash + 目标 DataSource + 预计新增数 + hash 冲突列表）。
- **仅 `--apply` 写入**：把原件 **复制** 进 MinIO（绝不移动/覆盖/删除源文件）。
- **幂等**：稳定 DataSource 身份 + `source_item_key = 相对路径` 作锚点；重跑不新增 `DataSource`/`FileAsset`/`IngestEvent`。
- **同键 hash 冲突保护**（对应审核步骤 3）：同一 `source_item_key` 已入湖但内容 hash 不同的文件，`apply_seed` **拒绝静默覆盖**，计入 `hash_conflicts` 并跳过，交人工处置。
- `--skip-init`：见 §9 已知限制（真实库 legacy 迁移阻塞时的运维开关，已完整披露）。

---

## 4. inventory 两条归属链 + 重复 canonical 规则

### 4.1 盘点分母（distinct file_asset_id，两链路并集）
```
quota_raw_total = DISTINCT file_asset_id ∈ (
    IngestEvent → DataSource(data_domain='quota') → file_id
  ∪ Archive(domain_type='quota') → ArchiveFile → file_id
)
```
- 同一 `FileAsset` 被多条 `IngestEvent` 指向 → 仍计 **1**（保留全部来源）；
- SHA 仅用于**标记重复**，**绝不缩减分母**；
- 历史"全域 ~2058 份 `FileAsset`"**不是** quota 分母（真实全域现为 2066，quota 仅 8）。

### 4.2 重复 canonical 规则（确定性）
同 `sha256` 分组内选唯一主记录：
1. 优先"已关联正式档案（存在 `ArchiveFile` 行）"者；
2. 再取 `created_at` 最早；
3. 再按 `file_id` 字典序兜底。
非主记录 `projection_status='duplicate'` 且 `duplicate_of_file_id=canonical`；**只自动标记 `pending` 候选，绝不覆盖人工已处置状态**。

---

## 5. 分类建议规则（仅建议，绝不落正式字典/建档）

| 命中 | 产出（`suggested_metadata`） |
|---|---|
| 文件名含"附录" | `file_role=appendix`（**不做专业判定**） |
| 其他 | `file_role=main_document` |
| 文件名含"四川" | `jurisdiction_level=province` + `jurisdiction_code=510000` |
| 含"计价定额" | `quota_system_type=construction_regional`；`material_type=quota_base` |
| 尾部标签含 装配式/智能建造/更新改造 | `volume_title_candidate=<标签>`（**不作专业**） |
| 其他专业标签，命中受控字典 | `discipline_code=<code>` |
| 其他专业标签，未命中字典 | `discipline_label_candidate=<标签>`（**不自动扩充字典、不生成 code**） |
| NAS 路径含 4 位年份 | `edition_year={value, source:nas_path, status:suggested}`（低置信，P3-D2） |

所有候选 `suggestion_confidence=0.3`，`matched_rules` 记录命中规则来源，`projection_status=pending`。

---

## 6. 测试名称与结果

### 6.1 迁移测试数量差异说明（P0-2 报告 12 → 现 10）

> **结论：不是删除或放宽 P0-2 测试换取通过。** 减少的 2 项是"全局单主文件唯一索引"的专属用例，随该**约束本身按已批准偏差被移除**而移除。

- P0-2 报告（`docs/quota_archive_implementation_report_v0.1.md` §7）当时含 2 项针对"全局部分唯一索引 `(archive_id) WHERE is_primary`"的用例：一项断言该索引被创建、一项断言其拒绝第二个主文件。
- 该全局 DB 约束会破坏 `cost_info`（合法多主表示行），经**用户批准偏差**改为 **P0-4 quota 域应用层校验**；约束已从 `models.py`/`database.py` 移除（见 `app/database.py:583-586` 注释）。
- 约束不复存在，其 2 项专属用例随之移除；**其余 10 项 P0-2 迁移测试全部保留、断言未放宽**。
- 说明：本仓库非 git 版本管理，无法从历史精确回放被删用例名；以上依据设计变更与 `database.py:583-586` 现存注释重建，如需可补写"断言全局单主索引**不存在**"的反向用例。

### 6.2 现有 P0-2 迁移测试（10 项，`tests/test_quota_migration.py`，全部 PASS）
```
test_fresh_create_all_registers_quota_schema_and_seed
test_migrate_quota_tables_is_idempotent
test_legacy_upgrade_preserves_data_and_other_domains
test_duplicate_role_blocks_migration_without_deleting
test_downgrade_dev_drops_schema_when_clean
test_downgrade_dev_refuses_when_business_data_present
test_downgrade_prod_keeps_tables_and_data_drops_indexes
test_publication_set_industry_sector_orthogonality
test_publication_relation_self_and_triple_unique
test_completeness_and_confidence_bounds
```

### 6.3 新增 P0-3 测试（9 项，`tests/test_quota_projection.py`，全部 PASS）
```
test_seed_dry_run_lists_files_without_writing                       # dry-run 不写入
test_seed_apply_is_raw_only_and_idempotent                         # raw-only + 幂等（无 Archive/发布集）
test_seed_same_key_different_hash_is_flagged_not_overwritten       # 同键 hash 冲突保护
test_inventory_counts_distinct_file_asset_across_multiple_ingest_events  # 多 IngestEvent → 计 1
test_two_file_assets_same_sha_marks_one_duplicate_to_canonical     # 确定性 canonical
test_appendix_is_file_role_not_discipline                          # 附录≠专业
test_unknown_discipline_label_is_candidate_not_code                # 未命中字典→候选，不生成 code
test_edition_year_is_low_confidence_suggestion_only                # 年份仅建议
test_projection_is_idempotent_and_creates_no_sets_or_archives      # 幂等 + 零副作用
```

### 6.4 测试命令与结果
```
python -m pytest tests/test_quota_migration.py tests/test_quota_projection.py -q
# 19 passed  (10 迁移 + 9 投影/seed)
```

---

## 7. FakeObjectStore 验证结果

- 全部 9 项 P0-3 测试在内存 SQLite + `FakeObjectStore` 下通过；
- CLI 与测试**共用同一核心函数** `apply_seed` / `run_projection` / `snapshot_counts`（无平行实现），因此 FakeObjectStore 路径与真实 CLI 行为一致。

---

## 8. 真实 MinIO / PostgreSQL 执行状态（`EXECUTED`）

环境：PostgreSQL `127.0.0.1:15432`、MinIO `http://djtsoft.x3322.net:9000`（桶 `cost-raw`）。
执行严格按审核指定顺序：dry-run → 记录清单/hash → 同键冲突检查 → 显式 `--apply` → projection → 对账。

### 8.1 dry-run 记录（8 个原件，`四川2025/`，`expected_new=8`，`hash_conflicts=[]`）
| 文件 | sha256 | 字节 | file_id（apply 后） |
|---|---|---|---|
| ——园林绿化工程 | `74b68ffa…68560` | 3,981,932 | `0fdd70a3-62e0-4e4b-ac22-7ba546b9d163` |
| ——房屋建筑更新改造工程 | `5d6d857e…3bda1` | 8,835,571 | `325c86ca-9fba-4239-90fb-2eb3028d047b` |
| ——智能建造 | `ed4adab4…d32fc` | 559,319 | `40118d4a-52b6-47a9-8fda-a3cc2e595f1c` |
| ——装配式建筑工程 | `8aa9a793…24bfa` | 2,479,391 | `2576d575-6b39-4e99-8a9d-66fba5b392fe` |
| ——附录 | `9980a772…46dd0` | 3,903,792 | `9b2230de-cd0e-4e72-a23a-9e3efc6ee141` |
| —市政工程 | `801bf34e…2897d` | 16,078,107 | `e9de4929-2a16-45f0-8271-7696c0b5a76f` |
| —房屋建筑工程 | `f45d128e…57abc` | 8,854,561 | `75e50305-4b55-4d39-8783-2c8be104561e` |
| ——装饰工程 | `74208f6a…3a737` | 9,237,241 | `a386d1a9-72ce-4e31-8ba5-c80776359cf7` |

批内 8 个 sha256 全不相同 → 无重复；无同键 hash 冲突。
quota seed `DataSource`（apply 创建）：`1b8485bd-88cf-4718-ab3b-288edaad5195`。

### 8.2 执行前后计数（`snapshot`）
| 实体 | 执行前 | 执行后 | Δ |
|---|---|---|---|
| DataSource（全域） | 337 | 338 | +1 |
| DataSource（quota） | 0 | 1 | +1 |
| FileAsset（全域） | 2058 | 2066 | +8 |
| FileAsset（quota 归属） | 0 | 8 | +8 |
| IngestEvent（全域） | 99 | 107 | +8 |
| Projection Candidate | 0 | 8 | +8 |
| **Publication Set** | 0 | 0 | **+0** ✅ |
| **Archive（全域）** | 98 | 98 | **+0** ✅ |
| Archive（quota） | 0 | 0 | +0 ✅ |

**Publication Set 新增 = 0、Archive 新增 = 0**，满足验收硬约束。

### 8.3 守恒对账（projection run 真实输出）
```
quota_raw_total = 8
pending=8  linked=0  duplicate=0  invalid=0  ignored=0
newly_created_candidates=8  newly_created_publication_sets=0  newly_created_archives=0
duplicates_marked=0  invariant_ok=true
all_domain_file_asset_count=2066
```
守恒式成立：**`quota_raw_total(8) = pending(8) + linked(0) + duplicate(0) + invalid(0) + ignored(0)`** ✅

### 8.4 幂等复跑（真实库）
- projection 复跑：`newly_created_candidates=0`、`invariant_ok=true`、发布集/档案仍 0；
- seed 复跑（`--apply`）：`source_created=false`、`newly_ingested_count=0`、`skipped_count=8`、`hash_conflict_count=0`。

### 8.5 真实分类结果（8 条候选，均 `pending`，`confidence=0.3`）
| 文件 | 关键建议 |
|---|---|
| 园林绿化工程 | `file_role=main_document`，`discipline_label_candidate=园林绿化工程` |
| 市政工程 | `discipline_label_candidate=市政工程` |
| 房屋建筑工程 | `discipline_label_candidate=房屋建筑工程` |
| 装饰工程 | `discipline_label_candidate=装饰工程` |
| 房屋建筑更新改造工程 | `volume_title_candidate=房屋建筑更新改造工程` |
| 智能建造 | `volume_title_candidate=智能建造` |
| 装配式建筑工程 | `volume_title_candidate=装配式建筑工程` |
| 附录 | `file_role=appendix`（**无 discipline**） |

全部 8 条均含 `jurisdiction_code=510000` / `quota_system_type=construction_regional` / `material_type=quota_base`；**无任何 `discipline_code`**（未命中字典即不生成，未扩充字典）；**无 `edition_year`**（见 §9-L2）。

---

## 9. 已知限制与下一步

### 已知限制
- **L1（环境阻塞，非 P0-3 引起）**：真实库共享迁移 `migrate_blob_columns` 因**既有 legacy 数据不一致**失败——`file_asset` 中 **1960/2058** 行 `blob_hash` 为空且其 `sha256` 在 `blob` 表无对应行（FK `file_asset_blob_hash_fkey`），使 `init_db()` 整体中断。该问题属 `cost_info` 历史数据，与 quota 无关。**未做任何数据修复/删除**。因 quota 5 张表在真实库**已存在**（P0-2 schema 早已部署），本次以正当运维开关 `--skip-init` 绕过该不相关 legacy 迁移完成 P0-3，**已在本报告完整披露**。
  - 建议：由数据 owner 单独处置 1960 条孤儿 `file_asset`（补 `blob` 行或标记），恢复 `init_db()` 正常路径；不在 P0-3 范围内。
- **L2（edition_year 未产出）**：seed 根目录为 `四川2025`，8 个原件直接位于其下，`source_item_key`（相对路径）= 纯文件名，**不含年份**，故本批 `edition_year` 建议为空。P3-D2 本就要求年份仅作低置信建议且人工确认；下一步可在候选处置阶段从 seed 批次配置或根目录名注入 `edition_year=2025(suggested)`。
- **L3**：分类为确定性文件名规则，非语义识别（符合"不越界 OCR/语义"红线）；`discipline` 受控字典当前仅 `general`，四川各专业均落 `discipline_label_candidate`，待字典按真实资料扩充后再由人工确认为 `discipline_code`。

### 下一步（P0-3 关闭前不启动 P0-4）
1. 提交本报告供 **ChatGPT 复核**；
2. 复核通过后关闭 P0-3；
3. 再输出 **P0-4 工作计划**（API：级联 facets、覆盖矩阵、quota 域应用层单主文件保护、审计）。
