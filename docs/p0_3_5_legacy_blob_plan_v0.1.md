# P0-3.5 Legacy Blob 完整性诊断与修复计划 v0.1

**状态：`PLAN_ONLY`**（审核通过前不实施）

## 0. 背景与阻塞关系

P0-4A 的 7 个后端端点代码已开发完成，但无法在真实环境验收，原因是 `init_db()` 在 legacy blob migration 阶段存在数据完整性缺陷。下游阻塞链：

```text
init_db() 不可靠
  → 真实 PostgreSQL 服务无法启动
    → P0-4A 端点无法实机验证
      → P0-4A 标记 IMPLEMENTED_NOT_VERIFIED
      → P0-4B 无法开工
```

## 1. 问题诊断

### 1.1 关键代码路径

`init_db()` 调用链（`database.py:62-77`）：

```text
Base.metadata.create_all(engine)              # 1. 创建所有 ORM 表
migrate_blob_columns(engine)                  # 2. blob 表 + file_asset.blob_hash
migrate_ingest_event_t2_columns(engine)       # 3. ingest_event 新列
...                                           # 4-13. 其他迁移
migrate_quota_tables(engine)                  # 14. quota 5 表
migrate_administrative_division_table(engine) # 15. 行政区划（P0-4A 新增）
```

共 15 个顺序操作，无整体 try/except。

### 1.2 四个具体缺陷

| 缺陷 | 代码位置 | 表现 | 严重度 |
| --- | --- | --- | --- |
| **A. 孤儿 blob_hash** | `migrate_blob_columns()`, line 145: `UPDATE file_asset SET blob_hash = sha256 WHERE blob_hash IS NULL` | 回填 blob_hash 但不创建 Blob 行，每条 legacy file_asset 的 blob_hash 指向不存在的记录 | 高 |
| **B. FK 缺失** | 同上，line 142: `ALTER TABLE ADD COLUMN blob_hash VARCHAR(64)` | Legacy DB 的 blob_hash 列无 FK 约束，与 ORM 定义的 `ForeignKey("blob.blob_hash")` 不一致 | 中 |
| **C. FK 回填冲突** | 同上，line 145 | 若 blob_hash 列已有 FK 约束（如先前部分迁移遗留），`UPDATE` 会因引用不存在而失败 | 高 |
| **D. 无错误恢复** | `init_db()`, lines 62-77 | 任何一步抛异常 → 整个 init_db 崩溃，无回滚/跳过/诊断机制 | 中 |

### 1.3 根因总结

`migrate_blob_columns()` 是一次**单向结构迁移**（加列 + 回填 + 建索引），但**缺少数据完整性补齐**（创建对应 Blob 行 + 建 FK）。而 `init_db()` 的**全量顺序执行 + 无异常恢复**设计放大了这个问题：一个迁移步骤的失败导致整个 DB 初始化不可用。

## 2. 修复方案

### 2.1 补全 Blob 行（修复 A）

在 `UPDATE file_asset SET blob_hash = sha256` 之后，立即插入匹配的 Blob 行：

```sql
-- 为每个回填的 blob_hash 创建 Blob 行（若不存在）
INSERT INTO blob (blob_id, blob_hash, storage_bucket, blob_storage_key, byte_size)
SELECT
    encode(gen_random_bytes(16), 'hex'),  -- PostgreSQL UUID 生成
    fa.blob_hash,
    COALESCE(fa.bucket, 'cost-raw'),
    COALESCE(fa.object_key, ''),
    COALESCE(fa.file_size, 0)
FROM file_asset fa
WHERE fa.blob_hash IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM blob b WHERE b.blob_hash = fa.blob_hash);
```

实现为 `_backfill_blob_rows(engine)` 辅助函数，与 `migrate_blob_columns()` 解耦，可独立调用/测试。

### 2.2 补齐 FK 约束（修复 B）

回填 Blob 行后，在 `file_asset.blob_hash` 上添加 FK（仅当不存在时）：

```sql
-- PostgreSQL
ALTER TABLE file_asset
  ADD CONSTRAINT IF NOT EXISTS fk_file_asset_blob_hash
  FOREIGN KEY (blob_hash) REFERENCES blob(blob_hash);
```

```sql
-- SQLite（不支持 ADD CONSTRAINT，通过重建表实现）
-- 或使用 PRAGMA foreign_keys=ON + 严格插入顺序保证
```

实现为 `_ensure_blob_fk(engine)` 辅助函数。

### 2.3 诊断优先于修复

在 `migrate_blob_columns()` 开头加入**无副作用的诊断查询**：

```python
def diagnose_blob_integrity(engine: Engine) -> dict:
    """Return counts, no writes."""
    with engine.connect() as conn:
        orphan_count = conn.scalar(text(
            "SELECT COUNT(*) FROM file_asset fa "
            "LEFT JOIN blob b ON b.blob_hash = fa.blob_hash "
            "WHERE fa.blob_hash IS NOT NULL AND b.blob_hash IS NULL"
        ))
        blob_count = conn.scalar(text("SELECT COUNT(*) FROM blob"))
        fa_count = conn.scalar(text("SELECT COUNT(*) FROM file_asset WHERE blob_hash IS NOT NULL"))
        return {
            "file_asset_with_blob_hash": fa_count,
            "blob_rows": blob_count,
            "orphan_references": orphan_count,
            "needs_repair": orphan_count > 0,
        }
```

诊断结果决定是否执行修复逻辑。修复只处理 `orphan_references > 0` 的情况。

### 2.4 init_db() 容错加固（修复 D）

将 `init_db()` 改为**分步 + 日志**模式：

```python
def init_db() -> None:
    engine = get_engine()
    migrations = [
        ("create_all", lambda: Base.metadata.create_all(engine)),
        ("blob", lambda: migrate_blob_columns(engine)),
        # ... 其余 14 步 ...
    ]
    for name, fn in migrations:
        try:
            fn()
        except Exception as exc:
            log_init_failure(name, exc)
            raise InitDbError(f"init_db failed at step '{name}': {exc}") from exc
```

关键行为：
- 每步失败时记录步骤名 + 异常信息
- **不做部分回滚**（每个 `fn` 内部有独立的 `engine.begin()` 事务）
- **不静默跳过**（失败后明确报错，不掩盖）

### 2.5 migrate_quota_tables() 与 migrate_administrative_division 的隔离

P0-4A 新增的 `migrate_administrative_division_table()` 排在最后，不依赖 blob 迁移。但需确保：
- quota 表和 admin_division 表的创建不因 blob 步骤失败而受阻
- 在 `migrate_quota_tables()` 中的 `QuotaMigrationBlocked` 是明确的数据守护（`archive_file` 重复角色检测），不应被降级

## 3. 文件变更清单

| 文件 | 变更 |
| --- | --- |
| `app/database.py` | 新增 `diagnose_blob_integrity()`、`_backfill_blob_rows()`、`_ensure_blob_fk()`；`migrate_blob_columns()` 调用诊断→修复链；`init_db()` 加分步日志 |
| `tests/test_database.py` | 新增 blob 诊断/回填/FK 测试；`migrate_blob_columns` 的带数据测试 |

### 不改文件

- `models.py` — Blob/FileAsset 模型不变
- `main.py` — init_db 调用方不变
- `quota_api.py` / `administrative_division.py` — P0-4A 端点不变
- 所有 UI 文件

## 4. 测试计划

| # | 场景 | 说明 |
| --- | --- | --- |
| T1 | `diagnose_blob_integrity()` — 干净 DB（无 orphan） | `needs_repair=False` |
| T2 | `diagnose_blob_integrity()` — 有 orphan（fa.blob_hash 存在但 blob 表为空） | `needs_repair=True` |
| T3 | `_backfill_blob_rows()` — 空 file_asset → 无操作 | 0 行写入 |
| T4 | `_backfill_blob_rows()` — 10 行 fa 各有唯一 blob_hash | 10 行 blob 写入 |
| T5 | `_backfill_blob_rows()` — 重复 blob_hash → 只插一条 | 去重验证 |
| T6 | `_backfill_blob_rows()` — 已有部分 blob 行 | 只补缺失，不覆盖 |
| T7 | `_ensure_blob_fk()` — FK 不存在时创建 | 检查 constraint 存在 |
| T8 | `_ensure_blob_fk()` — FK 已存在时不报错 | 幂等 |
| T9 | `migrate_blob_columns()` 全链路（有旧数据的 file_asset） | 迁移后 orphan=0, FK 存在 |
| T10 | `init_db()` 某步骤失败 → 明确报错步骤名 | 不静默 |
| T11 | quota migration 边界：`migrate_quota_tables()` 不被 blob 步骤阻塞 | quota 表正确创建 |

## 5. 实施步骤

| 步骤 | 内容 | 产出 |
| --- | --- | --- |
| S1 | `diagnose_blob_integrity()` + 测试 T1-T2 | 诊断函数 |
| S2 | `_backfill_blob_rows()` + 测试 T3-T6 | Blob 行补齐 |
| S3 | `_ensure_blob_fk()` + 测试 T7-T8 | FK 补齐 |
| S4 | 整合进 `migrate_blob_columns()` + 测试 T9 | 修复后迁移 |
| S5 | `init_db()` 分步日志 + 测试 T10-T11 | 容错加固 |
| S6 | 实机验证：PostgreSQL 启动 → init_db → admin division 376 条 → quota 表存在 | 真实环境验收 |

## 6. 成功标准

完成 P0-3.5 后，以下全部成立：

- [ ] `init_db()` 在已有 file_asset 数据的 PostgreSQL 上不报错
- [ ] 迁移前 orphan blob_hash 全部补齐为有效 Blob 行
- [ ] `file_asset.blob_hash` 有 FK 约束 `REFERENCES blob(blob_hash)`
- [ ] 重复运行 `init_db()` 幂等（不创建重复 Blob）
- [ ] 任一步骤失败时报出步骤名，不静默
- [ ] 此前 P0-4A 的 15 个端点可在真实环境验证

---

> **本计划为 `PLAN_ONLY`。P0-3.5 不涉及任何 UI 或 quota API 变更，仅修复数据库迁移层的数据完整性和容错性。请审核。**
