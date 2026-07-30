# quota_parser 集成 Plan（v0.4 · 2026-07-29 锁定）

> 目标：把已有的 `quota_parser` Python 包（v0.3 冻结，见 [`parser/SPEC.md`](parser/SPEC.md)）
> 嵌入 file-asset service，让用户在网站上触发、监控、下载、上传解析任务全流程。
>
> 配套 SPEC：
> - 后端脚本契约：`quota/parser/SPEC.md` v0.2 + §21 v0.3 变更
> - 网站前端契约：`quota/web-frontend/SPEC.md` v0.4
> - DB 契约：`quota/DB_SCHEMA.md` v2
> - Worker 集成层决策：`quota/README.md` §12.G
>
> **v0.4 关键决策**（2026-07-29）：
> 1. 真实 worker 改用 `quota_parse_job` DB 队列表（§1.2）+ 独立 `quota_parser_worker.py` 进程单 worker 串行（**v0.3 计划用 `file_processing` 表作废**——MinerU 12GB 显存禁止并发，PG 队列表 + 进程级 flock 二次保护最稳）。
> 2. 中间产物（markdown / html / candidate.xlsx / final.xlsx）全部进 `archive_file` 表（4 个新 `file_role`），列表「文件数」只数原件 PDF。
> 3. 端点从 7 扩到 10（新增 §5.10 `DELETE /archives/{id}`、§5.9 `GET /parse-queue`、§5.8 删除解析结果强化）。
> 4. 两类删除分开：#9 删除解析结果（列表 ⋯ 下拉 + 详情页）vs #10 删除档案（**仅详情页**）。
> 5. 「已完成」态「重新解析」默认隐藏，详情页「高级操作」折叠区作为逃生口。

---

## 0. 现状（截至 2026-07-29）

| 项 | 状态 |
|---|---|
| `quota_parser` 包源码 | ✅ v0.3 已冻结（含 `run_quota_pipeline` / `finalize_reviewed_xlsx` / `cleanup_workspace`） |
| `quota_parser` 包打包 | ❌ 无 `pyproject.toml` / 无 `setup.py`；目前只能通过 `PYTHONPATH` 直接 import |
| file-asset 环境 `quota_parser` 可 import？ | ❌ 未安装（环境装的是 `file_asset_service` editable，site-packages 找不到 quota_parser） |
| `quota/parser/quota_parser/external/` 复用层 | ✅ 已迁入（`mineru_pdf_parse` / `quota_md_to_csv_v2` / `quota_csv_finalize`） |
| `Archive` 表 parse 子对象 | ✅ v0.3 已实施（13 列 `parse_*` + 索引） |
| `quota_api.py` 7 个解析端点 | ✅ v0.3 已实施（mock 模式可走通） |
| `file_asset_service/app/quota_parser/` adapter 子包 | ✅ v0.3 已实施（`service.py`） |
| mock 模式 (`QUOTA_PARSE_MODE=mock`) | ✅ v0.3 已实施 |
| 前端解析区块（`quota-parse.js / quota-parse-api.js / quota-parse-mock.js`） | ❌ 未实施（v0.4 工作量） |
| 真实 Worker 轮询 | ❌ v0.3 计划用 `file_processing` 表 → **v0.4 改用 `quota_parse_job` 表** |
| 列表行 ⋯ 下拉（5 状态智能图标） | ❌ v0.3 已 SPEC 但未实施 |
| 中间产物入 `archive_file` | ❌ v0.4 新增 |
| 删除档案端点 | ❌ v0.4 新增 |

**核心问题（v0.4）**：v0.3 mock 模式能跑，但真脚本（4 个 CLI skill）未在 web 端验证；DB 队列表 + 单 worker 串行架构未实施；详情页 / 列表行 UI 未动工。

---

## 1. 集成架构（数据流总览；v0.4 双模式）

```
用户在档案详情点「开始解析」/ 列表行点 ▶ 图标
  │
  │  HTTP POST /api/data-lake/quota/archives/{archive_id}/parse
  ▼
quota_api.py parse 端点
  │
  │  ┌─ 写 Archive.parse_status='parsing', parse_task_id=...
  │  │   ┌─ [v0.4 real] 写 quota_parse_job (status=queued)         ──┐
  │  │   └─ [v0.4 mock] asyncio.create_task(run_mock_pipeline(...)) ─┤
  │  └─ 由 QUOTA_PARSE_MODE 决定走哪条路
  ▼
┌─────────────────────────────────────────────────────────────────┐
│  解析引擎                                                        │
│                                                                 │
│  [MOCK 分支]  QUOTA_PARSE_MODE=mock (开发)                       │
│    → asyncio.create_task(run_mock_pipeline(archive_id))         │
│    → sleep 5-10s → 填 parse_* 13 列 + 写假 candidate.xlsx 到    │
│      MinIO + 同步在 archive_file 注册 4 个 parse_* 行          │
│                                                                 │
│  [真分支]  QUOTA_PARSE_MODE=real (生产)  v0.4 主线              │
│    → 独立 quota_parser_worker.py 进程（scripts/run_quota_      │
│      parser_worker.sh 常驻）                                    │
│    → fcntl.flock(/var/run/quota_parser_worker.lock) 防多启     │
│    → 轮询 quota_parse_job:                                      │
│        SELECT ... WHERE status='queued' ORDER BY enqueued_at    │
│        LIMIT 1 FOR UPDATE SKIP LOCKED                            │
│    → lease 改 status='running', 写 started_at + worker_pid      │
│    → 下载 PDF → subprocess 串行调 4 个 CLI skill:               │
│        mineru-pdf-parse → quota-md-to-csv-v2 →                  │
│        quota-csv-finalize → to_xlsx                             │
│    → 4 类中间产物上 MinIO + archive_file 注册 4 个 parse_* role │
│    → 写 Archive.parse_status='parsed' + candidate_xlsx_key      │
│    → 写 quota_parse_job status='done', finished_at              │
│    → cleanup_workspace(success=True)                            │
│                                                                 │
│  失败分类：                                                      │
│    transient  → attempt+1, 状态回 queued (3 次内重试)           │
│    failed_user / failed_permanent → status='failed'              │
└─────────────────────────────────────────────────────────────────┘
  │
  │  候选产物：candidate.xlsx / final.xlsx / manifest.json /
  │           qa_report.json / qa_report.md / artifacts/result.md
  │           / artifacts/result.html  → 全部上传 MinIO
  │  状态：Archive.parse_status + parse_* 13 列 + archive_file 4 行
  ▼
用户在档案详情（或列表操作列）看到解析状态推进：
  未解析 → 解析中 → 待审核 → 已完成
                ↓
            解析失败（红 banner + 错误原因）
```

**数据契约**（前端只读，不转换）：
- `GET /api/data-lake/quota/archives/{id}` 返回 ArchiveDetail，**追加 `parse` 子对象**（web-frontend SPEC §6.1）
- 10 个解析端点统一挂在 `/api/data-lake/quota/archives/{id}/*`（v0.4 是 10 个，v0.3 是 7 个）
- 字段名与 Manifest（`quota-parser-result/v1` schema）一一对齐

## 1.2 真实 worker 详细设计（v0.4 主线）

### 1.2.1 为什么不用 `file_processing` 表（v0.3 决策作废）

- `file_processing` 是通用 worker 调度表（任何 processor 都用），quota 域用要加 `processor='quota_parser_v2'` 字段。
- 缺点：抢单逻辑复杂（`SELECT ... FOR UPDATE SKIP LOCKED` 在 PG 上要 9.5+），且 quota 域**强串行**（单 worker 跑全 quota 队）——通用 worker 模型与 quota 强串行需求不匹配。
- v0.4 决策：**quota 域独立用 `quota_parse_job` 表**（专用 + 强串行），其他域继续用 `file_processing`。两表共存，不互相影响。

### 1.2.2 quota_parse_job 表设计

详见 `quota/DB_SCHEMA.md` §3.9。核心字段：
- `status`：`queued / running / done / failed / cancelled`（5 态）
- `attempt` + `max_attempts`：transient 重试计数
- `worker_pid` + `worker_hostname`：哪个 worker 进程抢到
- `error_code` + `error_message`：失败分类
- 索引：`(status, enqueued_at)` 抢单用；`(archive_id)` 历史查

### 1.2.3 `quota_parser_worker.py` 进程模型

```python
# scripts/run_quota_parser_worker.sh
# 启动方式：nohup scripts/run_quota_parser_worker.sh >> logs/worker.log 2>&1 &

import fcntl
from app.quota_parser_worker import run_worker

# 启动时：fcntl.flock 占 /var/run/quota_parser_worker.lock
# 主循环：
#   while True:
#     with session() as db:
#       job = db.execute(text("""
#         SELECT * FROM quota_parse_job
#         WHERE status='queued' ORDER BY enqueued_at
#         LIMIT 1 FOR UPDATE SKIP LOCKED
#       """)).first()
#       if job:
#         job.status='running'; job.started_at=now(); job.worker_pid=os.getpid()
#         db.commit()
#         run_one_job(job)  # subprocess 调 4 个 CLI skill
#       else:
#         time.sleep(2)  # 2s 轮询
```

### 1.2.4 串行保证（4 层）

1. **进程级 `fcntl.flock`**：防运维误启多 worker 进程。
2. **DB 行锁 `FOR UPDATE SKIP LOCKED`**：即使多 worker 进程（被 flock 挡了），DB 层也不会双抢。
3. **profile 限两省**：目前只 `sichuan` / `chongqing` 两 profile，避免 worker 跑异构任务。
4. **archive 唯一入队**：`POST /parse` 端点用 `SELECT ... FOR UPDATE` 防同一 archive 并发入队（v0.4 新加）。

### 1.2.5 4 个 CLI skill 的 subprocess 调用

worker 启动解析时，按 `quota/parser/SPEC.md` §5.1 流程串行 subprocess 调：

```bash
# 1. MinerU PDF → md/html/json（300s+）
$DLSE_PY quota/parser/external/mineru-pdf-parse/scripts/parse_pdf.py \
    "$PDF_PATH" --output-dir "$WORK_DIR/ocr"

# 2. md → 10 列 CSV（按 profile 分发 sc/cq）
$DLSE_PY quota/parser/external/quota-md-to-csv-v2/extract_quota.py \
    "$WORK_DIR/ocr/result.md" --province "$PROFILE" \
    --output "$WORK_DIR/csv/quota.csv"

# 3. CSV → xlsx（4 步流水线）
$DLSE_PY quota/parser/external/quota-csv-finalize/clean_empty_qty.py \
    "$WORK_DIR/csv/quota.csv"
$DLSE_PY quota/parser/external/quota-csv-finalize/fill_work_content.py \
    "$WORK_DIR/csv/quota_final.csv"
$DLSE_PY quota/parser/external/quota-csv-finalize/space_split_materials.py \
    "$WORK_DIR/csv/quota_final.csv"
$DLSE_PY quota/parser/external/quota-csv-finalize/to_xlsx.py \
    "$WORK_DIR/csv/quota_final.csv" --output "$WORK_DIR/xlsx/candidate.xlsx"
```

**为什么 subprocess 而不是直接 import**：
- 4 个 CLI skill 都用 DLSE 环境（`/d/miniconda3/envs/DLSE/python.exe`），file-asset 是另一个 env（`/d/miniconda3/envs/file-asset/python.exe`）——conda env 隔离，import 跨 env 会 `ImportError`。
- subprocess 沿用现有 skill 入口（不重写），加 4 个 parser hook 即可，零 skill 改动。
- 4 个 skill 各自有独立 SKILL.md 与 CLI 参数定义，subprocess 调是 contract-stable 的最低耦合路径。

**异常归类**（worker 侧）：
- subprocess 退出码 0 → 成功
- 退出码 1 + stderr 含「OOM」/「CUDA out of memory」→ `failed_permanent`（硬件问题，告警运维）
- 退出码 1 + 其他 → `transient`（attempt+1 重试，3 次后转 `failed_user`）
- 退出码 2 → `failed_user`（输入数据问题，user 修 PDF 后重传）

---

## 2. 关键数据模型决策

### 2.1 status 字段命名（开放问题 #1，已选方案）

**矛盾点**：
- web-frontend SPEC §6.1 描述：`archive.status` 是主驱动状态机（`registered → parsing → parsed → qa_passed → usable`）
- 现状：`Archive.status` CheckConstraint 已绑定 cost_info 域 8 个状态（`discovered / collecting / collect_failed / quarantined / collected / pending_tag / archived / ready_for_governance`）
- 决策方向冲突：扩展 CK 与 cost_info 共存 vs 新增 quota 专属字段

**方案 A（保守，**推荐**）**：新增 `parse_status` String 字段（无 CheckConstraint），独立于 `status`。

| 字段 | 含义 | 与 cost_info 冲突？ |
|---|---|---|
| `Archive.status`（已有） | cost_info 域主状态机 | — |
| `Archive.parse_status`（新加） | quota 域解析子状态机；枚举：`pending / parsing / parsed / qa_passed / usable / failed_user / failed_permanent / transient` | ❌ 零冲突 |

**方案 B（激进）**：扩展 `ck_archive_status` 加入 quota 7 个新状态。cost_info 与 quota 走同一 `status` 列，靠 `domain_type` 区分。与 SPEC §6.1 字面一致，但污染 cost_info 域的 CheckConstraint 语义。

**今天按方案 A 实施**。后续如要把 SPEC §6.1 / §7.1 改为 `archive.status`，再做方案 B 的迁移脚本。

### 2.2 Archive 新增 13 列（parse_*）

照搬 web-frontend SPEC §6.1：

| 列名 | 类型 | 对应 Manifest 字段 |
|---|---|---|
| `parse_status` | `String(32)`（独立于 `status`，见 §2.1） | `archive.status` 推进时的"子状态" |
| `parse_profile` | `String(32)` | `manifest.profile` |
| `parse_task_id` | `String(64)` | `manifest.task_id` |
| `parse_phase` | `String(16)` | `manifest.phase` |
| `parse_parser_version` | `String(32)` | `manifest.parser_version` |
| `parse_started_at` | `DateTime(timezone=True)` | Worker 侧 |
| `parse_finished_at` | `DateTime(timezone=True)` | Worker 侧 |
| `parse_metrics` | `JSON`（nullable） | `manifest.metrics` |
| `parse_warnings` | `JSON`（nullable） | `manifest.warnings` |
| `parse_error_code` | `String(32)` | `transient / failed_permanent / failed_user / null` |
| `parse_error_message` | `Text` | — |
| `candidate_xlsx_key` | `String(512)` | MinIO key：`quota/<archive_id>/candidate.xlsx` |
| `final_xlsx_key` | `String(512)` | MinIO key：`quota/<archive_id>/final.xlsx` |

注：原 SPEC §6.1 写 `parse_status` 用 `archive.status` 映射——这里改成新加独立字段（§2.1）。SPEC §6.1 表述待 step 4 后修订。

### 2.3 MinIO bucket / key 布局（**复用** cost 域决策；v0.4 加 md/html）

**2026-07-28 决策**：quota 解析产物**复用** cost 域已有的 `cost-extract` / `cost-report` 桶（quota/README.md §12.G 决策 4），不新增桶。靠 `key` 前缀 `quota/...` 与 cost 域产物 `cost/...` 区分。

| bucket | key | 写入方 | 生命周期 | v0.4 archive_file.role |
|---|---|---|---|---|
| `cost-extract`（**复用**） | `quota/<archive_id>/candidate.xlsx` | 阶段 A worker / mock | 与 archive 同生命周期 | `parse_candidate_xlsx` |
| `cost-extract`（**复用**） | `quota/<archive_id>/final.xlsx` | 阶段 B worker / mock | 与 archive 同生命周期 | `parse_final_xlsx` |
| `cost-extract`（**复用，v0.4 新增**） | `quota/<archive_id>/artifacts/result.md` | 阶段 A worker | 与 archive 同生命周期 | `parse_markdown` |
| `cost-extract`（**复用，v0.4 新增**） | `quota/<archive_id>/artifacts/result.html` | 阶段 A worker | 与 archive 同生命周期 | `parse_html` |
| `cost-report`（**复用**） | `quota/<archive_id>/manifest.json` | 阶段 A | 与 archive 同生命周期 | （不进 archive_file） |
| `cost-report`（**复用**） | `quota/<archive_id>/qa_report.json` | 阶段 A / B | 与 archive 同生命周期 | （不进 archive_file） |
| `cost-report`（**复用**） | `quota/<archive_id>/qa_report.md` | 阶段 A / B | 与 archive 同生命周期 | （不进 archive_file） |

adapter 层两个常量（`PARSE_BUCKET_CANDIDATE` / `PARSE_BUCKET_REPORT`），如未来要拆桶单点改即可。

**v0.4 新增约束**：md/html 落 MinIO 的同时**必须**在 `archive_file` 注册对应 role 行（`register_parse_artifact(archive_id, role, key, sha256)` helper）——否则前端 ⋯ 下拉下载入口找不到。`manifest.json` / `qa_report.*` 不进 archive_file（仅走 `GET /manifest` / `/qa-report` 端点，UI 弹窗展示）。

### 2.4 中间产物入 archive_file（v0.4 新增）

**v0.3 状态**：中间产物只在 `Archive.candidate_xlsx_key` / `final_xlsx_key` 列存 MinIO key，前端通过 `GET /candidate.xlsx` / `/final.xlsx` 端点下载。markdown / html **没有落点**。

**v0.4 决策**：扩展 `archive_file` CheckConstraint，加 4 个新 `file_role`：
- `parse_markdown`：MinerU 输出的 `<stem>.md`
- `parse_html`：MinerU 输出的 `<stem>.html`（含 `<table>` 嵌入）
- `parse_candidate_xlsx`：阶段 A 输出的 candidate.xlsx
- `parse_final_xlsx`：阶段 B 输出的 final.xlsx

4 个 role 的写入方：
- `quota_parser_worker.py` 阶段 A 跑完 MinerU 后 → 调 `register_parse_artifact(archive_id, 'parse_markdown', md_key, md_sha256)` + `register_parse_artifact(archive_id, 'parse_html', html_key, html_sha256)` + `register_parse_artifact(archive_id, 'parse_candidate_xlsx', xlsx_key, xlsx_sha256)`
- 阶段 B 跑完 finalize → `register_parse_artifact(archive_id, 'parse_final_xlsx', final_key, final_sha256)`
- mock 模式同样调（v0.3 mock 没注册——v0.4 mock 也要补，否则前端列表「文件数」会因 mock 跑过而不对）

**`Archive.candidate_xlsx_key` / `final_xlsx_key` 列保留**：与 `archive_file` 行的 4 个 parse_* role **冗余**（同一物理文件两个引用入口），保证历史 v0.3 端点契约不变。前端代码读 `candidate_xlsx_key` 列作为快速路径，`archive_file` 表作为带 audit / 显示名 / 排序的权威源。

**前端「文件数」语义澄清**：
- `archive.file_count` = `SELECT COUNT(*) FROM archive_file WHERE archive_id=? AND file_role IN ('main_document', 'priced_source')`（**只数原件 PDF**）
- 4 类 parse_* role 行**不算** file_count——避免列表显示「文件数 5」但只有 1 个 PDF 直观错乱
- 4 类 parse_* role 行的下载入口：列表行 ⋯ 下拉「下载 markdown」/「下载 html」/「下载 candidate」/「下载 final」按状态显示

---

## 3. v0.4 实施路线图（按依赖排序）

> v0.3 完成的（`parse_*` 13 列 / 7 端点 / mock runner / adapter service）已标 ✅，不再列重做。

### 阶段 1 · DB schema（v0.4 新增 2 步）

| # | 任务 | 状态 |
|---|---|---|
| **1a** | `models.py` 新增 `QuotaParseJob` 模型（DB_SCHEMA.md §3.9） | 🔵 v0.4 第二天 |
| **1b** | `models.py` 改 `ArchiveFile` 的 `ck_archive_file_role` CheckConstraint，加 4 个 parse_* role | 🔵 v0.4 第二天 |
| **1c** | 跑 §5 ALTER SQL（`quota/DB_SCHEMA.md`） | 🔵 v0.4 第二天 |

### 阶段 2 · 真实 worker 进程（v0.4 主线，5 步）

| # | 任务 | 状态 |
|---|---|---|
| **2a** | `quota_parser/worker.py` —— 单 worker 进程骨架：fcntl.flock 启动保护 + 主循环 `SELECT ... FOR UPDATE SKIP LOCKED` | 🔵 v0.4 第二天 |
| **2b** | `quota_parser/subprocess_runner.py` —— subprocess 调 4 个 CLI skill（含 DLSE env PATH）+ 退出码 → 异常分类 | 🔵 v0.4 第二天 |
| **2c** | `quota_parser/service.py` 加 `register_parse_artifact(archive_id, role, key, sha256)` helper | 🔵 v0.4 第二天 |
| **2d** | `scripts/run_quota_parser_worker.sh`（仿 `serve.py` onlogon）独立进程启动脚本 | 🔵 v0.4 第二天 |
| **2e** | 端到端跑 1 份四川 PDF：`POST /parse` → 看 `quota_parse_job` 推进 → 看 4 类 `archive_file` 行注册 | 🔵 v0.4 第二天 |

### 阶段 3 · quota_api.py 扩 3 端点（v0.4 新增）

| # | 任务 | 状态 |
|---|---|---|
| **3a** | 改 `POST /parse` 端点：real 模式写 `quota_parse_job` 代替 `asyncio.create_task`；mock 模式保持原行为 | 🔵 v0.4 第三天 |
| **3b** | 改 `POST /parse/delete` 端点：v0.4 强化版（§5.8 7 步） | 🔵 v0.4 第三天 |
| **3c** | 新增 `DELETE /archives/{id}` 端点（§5.10）：先调 #3b 清解析结果 → 删 profile → 删 pubset（若仅一份分册）→ 删 archive_file 全部 → 删 MinIO PDF → 删 Archive | 🔵 v0.4 第三天 |
| **3d** | 新增 `GET /parse-queue` 端点（§5.9）：返回 running/queued/recent_failed/worker_pid | 🔵 v0.4 第三天 |

### 阶段 4 · 前端 5 状态 × 10 按钮 UI（v0.4 主工作量）

| # | 任务 | 状态 |
|---|---|---|
| **4a** | `file_asset_service/app/ui/quota-parse.js`：tab + 档案详情解析区块（按 web-frontend SPEC §7.1 渲染） | 🔵 v0.4 第四天 |
| **4b** | `file_asset_service/app/ui/quota-parse-api.js`：10 个端点的 HTTP 客户端 + mock fixture | 🔵 v0.4 第四天 |
| **4c** | `file_asset_service/app/ui/quota-parse-mock.js`：浏览器内 mock fixture（5 状态样例数据） | 🔵 v0.4 第四天 |
| **4d** | `file_asset_service/app/ui/quota-ui.js`：列表行 ⋯ 下拉（5 状态智能图标）+ 详情页「高级操作」折叠区（#8 重新解析 + #9 删除解析结果 + #10 删除档案） | 🔵 v0.4 第四天 |
| **4e** | `file_asset_service/app/ui/index.html`：注册 3 个新 script | 🔵 v0.4 第四天 |
| **4f** | `file_asset_service/app/ui/styles.css`：§3.2.4 视觉规范（5 状态徽章 + 智能图标 + ⋯ 下拉 + 危险区颜色 + 删除 modal） | 🔵 v0.4 第四天 |

### 阶段 5 · 列表行「文件数」显示

| # | 任务 | 状态 |
|---|---|---|
| **5a** | 后端 `archive_service.get_file_count(archive_id)` helper：只数 `file_role IN ('main_document', 'priced_source')` | 🔵 v0.4 第四天 |
| **5b** | 列表端点 `GET /api/data-lake/quota/archives` 改用 helper（不改前端 schema） | 🔵 v0.4 第四天 |

### 阶段 6 · 测试（v0.4）

| # | 任务 | 状态 |
|---|---|---|
| **6a** | quota_parse_job 入队 / 抢单 / 完成 / 失败 4 类单测 | 🔵 v0.4 第五天 |
| **6b** | 改 §5 ALTER 幂等性单测（反复跑不报错） | 🔵 v0.4 第五天 |
| **6c** | 端到端 mock 模式跑 1 份四川 PDF（curl 10 端点 + 看 Archive.parse_status + 4 类 archive_file + quota_parse_job 推进） | 🔵 v0.4 第五天 |
| **6d** | E2E real 模式跑 1 份四川 PDF（启动 worker → POST /parse → 等 done → 验证 4 类 archive_file + 4 类 MinIO key） | 🔵 v0.4 第五天 |

### 阶段 7 · 文档同步（v0.4）

| # | 任务 | 状态 |
|---|---|---|
| **7a** | CLAUDE.md（**今天不同步**——阶段总结时合一次） | ⏸ 暂缓 |
| **7b** | quota/README.md：worker 启动命令 + `quota_parse_job` 表 + 4 类 archive_file 速查 | 🔵 v0.4 第五天 |
| **7c** | quota/web-frontend/SPEC.md / DB_SCHEMA.md / INTEGRATION_PLAN.md 三者状态对齐 | ✅ **今天已改完** |

---

## 4. 今天的前 4 步（详细化）

### Step 1 · Archive 加 parse_* 13 列

**改 `file_asset_service/app/models.py`**（`Archive` 类内部追加）：

```python
# --- quota_parser integration (v0.3, 2026-07-28) ---
parse_status: Mapped[str | None] = mapped_column(String(32), index=True)
parse_profile: Mapped[str | None] = mapped_column(String(32))
parse_task_id: Mapped[str | None] = mapped_column(String(64))
parse_phase: Mapped[str | None] = mapped_column(String(16))
parse_parser_version: Mapped[str | None] = mapped_column(String(32))
parse_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
parse_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
parse_metrics: Mapped[dict | None] = mapped_column(archive_json_type())
parse_warnings: Mapped[list | None] = mapped_column(archive_json_type())
parse_error_code: Mapped[str | None] = mapped_column(String(32))
parse_error_message: Mapped[str | None] = mapped_column(Text)
candidate_xlsx_key: Mapped[str | None] = mapped_column(String(512))
final_xlsx_key: Mapped[str | None] = mapped_column(String(512))
```

**注意**：
- 字段顺序：**不**加 CheckConstraint；与 §2.1 方案 A 一致（status 独立字段，不污染 ck_archive_status）
- `init_db()` 自动建表（SQLAlchemy declarative），不需要手写 ALTER TABLE
- 既有数据：所有新列默认 NULL，无迁移成本

**验证**：
```bash
PY=/d/miniconda3/envs/file-asset/python.exe
$PY -c "from app.models import Archive; print([c.name for c in Archive.__table__.columns if c.name.startswith('parse_') or c.name.endswith('_xlsx_key')])"
# → ['parse_status', 'parse_profile', 'parse_task_id', 'parse_phase', 'parse_parser_version',
#     'parse_started_at', 'parse_finished_at', 'parse_metrics', 'parse_warnings',
#     'parse_error_code', 'parse_error_message', 'candidate_xlsx_key', 'final_xlsx_key']
```

### Step 2 · quota_parser adapter 子包

**新建 `file_asset_service/app/quota_parser/__init__.py`**：

```python
"""quota_parser web-side adapter (DB ↔ MinIO ↔ quota_parser 包)

薄桥接层 —— 不复制 quota_parser 包的业务逻辑，只做：
  1. 解析阶段 A/B 状态机推进（写 Archive.parse_* 13 列）
  2. PDF 从 MinIO 临时下载到本地 work_root，调 run_quota_pipeline
  3. 产物（candidate.xlsx / final.xlsx / manifest.json / qa_report.*）上传 MinIO
  4. 异常 → parse_error_code 映射（parser/SPEC.md §11）
"""
from .service import (
    trigger_parse,
    upload_reviewed,
    build_parse_section,
    PARSE_BUCKET,
    PROFILES,
)

__all__ = [
    "trigger_parse",
    "upload_reviewed",
    "build_parse_section",
    "PARSE_BUCKET",
    "PROFILES",
]
```

**新建 `file_asset_service/app/quota_parser/service.py`**（核心）：

```python
"""DB ↔ MinIO ↔ quota_parser 桥

- 阶段 A: trigger_parse(archive_id, profile) → 写 parse_status='parsing'
          → asyncio.create_task → run_mock_pipeline 或 run_quota_pipeline
- 阶段 B: upload_reviewed(archive_id, xlsx_path) → 写 final_xlsx_key
- 序列化: build_parse_section(archive) → ArchiveDetail.parse 子对象
"""
from __future__ import annotations
import logging
from pathlib import Path
from typing import Literal

from sqlalchemy.orm import Session

from app.models import Archive
from app.storage import ObjectStore

logger = logging.getLogger(__name__)

PARSE_BUCKET = "quota-extract"  # §2.3 待运维拍板；adapter 层单点常量
PROFILES = ("sichuan", "chongqing")

# Manifest → Archive.parse_* 字段映射（与 web-frontend SPEC §6.1 对齐）
MANIFEST_TO_ARCHIVE = [
    "profile", "task_id", "phase", "status",
    "parser_version", "started_at", "finished_at",
    "metrics", "warnings",
]


def trigger_parse(archive: Archive, *, profile: str | None = None) -> Archive:
    """触发阶段 A：写 parse_status='parsing' + parse_task_id。

    Args:
        archive: 已加载的 Archive 实例（locked by caller for update）
        profile: 'sichuan' / 'chongqing' / None（None = 用档案已有的 profile）

    Returns:
        更新后的 Archive 实例（caller 负责 commit）

    Raises:
        ValueError: archive 不在 quota 域；profile 不合法
    """
    if archive.domain_type != "quota":
        raise ValueError(f"archive {archive.archive_id} 不是 quota 域（{archive.domain_type}）")
    if profile is not None and profile not in PROFILES:
        raise ValueError(f"profile {profile!r} 不在注册表 {PROFILES}")
    # 状态机校验：registered → parsing；parsed/qa_passed/usable 也允许（重新解析）
    if archive.parse_status in ("parsing",):
        raise ValueError(f"parse_status 已在 parsing，不可重入")
    archive.parse_status = "parsing"
    archive.parse_profile = profile or archive.parse_profile
    archive.parse_task_id = f"qp_{_ts()}_{archive.archive_id[:8]}"
    archive.parse_started_at = _now()
    archive.parse_finished_at = None
    archive.parse_error_code = None
    archive.parse_error_message = None
    return archive


def upload_reviewed(archive: Archive, reviewed_xlsx_path: Path) -> Archive:
    """触发阶段 B：upload reviewed.xlsx → 写 final_xlsx_key。

    不在本步调 finalize_reviewed_xlsx（mock 模式下 mock runner 调；真模式下 worker 调）。
    """
    # ...


def build_parse_section(archive: Archive) -> dict | None:
    """把 Archive.parse_* 字段打包成 web-frontend SPEC §6.1 的 `parse` 子对象。"""
    if archive.parse_status is None:
        return None
    return {
        "profile": archive.parse_profile,
        "task_id": archive.parse_task_id,
        "phase": archive.parse_phase,
        "status": archive.parse_status,
        "started_at": archive.parse_started_at.isoformat() if archive.parse_started_at else None,
        "finished_at": archive.parse_finished_at.isoformat() if archive.parse_finished_at else None,
        "parser_version": archive.parse_parser_version,
        "metrics": archive.parse_metrics,
        "warnings": archive.parse_warnings,
        "error_code": archive.parse_error_code,
        "error_message": archive.parse_error_message,
        "candidate_xlsx_key": archive.candidate_xlsx_key,
        "final_xlsx_key": archive.final_xlsx_key,
        "manifest": _reconstruct_manifest(archive),
    }


def _reconstruct_manifest(archive: Archive) -> dict | None:
    """从 Archive.parse_* 字段反推 Manifest（前端零翻译）。"""
    if archive.parse_status is None:
        return None
    return {
        "$schema": "quota-parser-result/v1",
        "task_id": archive.parse_task_id,
        "phase": archive.parse_phase,
        "status": archive.parse_status,
        "parser_version": archive.parse_parser_version,
        "profile": archive.parse_profile,
        "province": archive.parse_profile,
        "ocr_api_url": "http://172.16.20.23:8000",
        "source_pdf_sha256": None,  # archive 暂未存原 PDF sha256，留 None
        "candidate_xlsx_sha256": None,
        "artifacts": _reconstruct_artifacts(archive),
        "metrics": archive.parse_metrics or {},
        "warnings": archive.parse_warnings or [],
    }


def _reconstruct_artifacts(archive: Archive) -> list[dict]:
    out: list[dict] = []
    if archive.candidate_xlsx_key:
        out.append({"kind": "candidate_xlsx", "key": archive.candidate_xlsx_key})
    if archive.final_xlsx_key:
        out.append({"kind": "final_xlsx", "key": archive.final_xlsx_key})
    return out
```

**新建 `file_asset_service/app/mock_parse_runner.py`**（web-frontend SPEC §8 硬约束 1-4）：

```python
"""Mock 模式：当 QUOTA_PARSE_MOCK=1 时替代真 worker

- 端点必须 async def，asyncio.create_task 启动后台任务，立即返回
- 后台任务独立 SessionLocal（避免 DetachedInstanceError）
- task 引用存到模块级 set，防止被 GC
- 5-10s sleep 后填 Archive.parse_* 字段 + 写假 candidate.xlsx 到 MinIO
"""
from __future__ import annotations
import asyncio
import logging
from pathlib import Path

from app.database import SessionLocal
from app.models import Archive
from app.quota_parser.service import PARSE_BUCKET
from app.storage import get_object_store

logger = logging.getLogger(__name__)

# task 引用集合（防止被 GC；task done 时回调 discard）
_TASKS: set[asyncio.Task] = set()


async def run_mock_pipeline(archive_id: str, *, candidate_seconds: float = 5.0) -> None:
    """mock 模式下的"阶段 A"模拟。

    1. 拿新 session
    2. 加载 Archive
    3. sleep candidate_seconds（模拟 OCR 耗时）
    4. 写 Archive.parse_status='parsed' + 假 metrics/warnings
    5. 写假 candidate.xlsx 到 MinIO（最小可用的 xlsx 字节）
    """
    loop = asyncio.get_running_loop()
    try:
        await asyncio.sleep(candidate_seconds)
    except asyncio.CancelledError:
        logger.warning("mock pipeline cancelled archive_id=%s", archive_id)
        return

    with SessionLocal() as session:
        archive = session.get(Archive, archive_id)
        if archive is None:
            logger.error("mock pipeline: archive %s 不存在", archive_id)
            return
        archive.parse_status = "parsed"
        archive.parse_phase = "stage_a"
        archive.parse_parser_version = "0.2.0"  # 与 quota_parser 包的 PARSER_VERSION 对齐
        archive.parse_finished_at = _now()
        archive.parse_metrics = {
            "pages": 100,
            "ocr_seconds": int(candidate_seconds),
            "candidate_rows": 1000,
        }
        archive.parse_warnings = ["mock mode — metrics are fake"]
        archive.candidate_xlsx_key = f"quota/{archive_id}/candidate.xlsx"
        # 写假 xlsx 到 MinIO
        _upload_fake_xlsx(archive.candidate_xlsx_key, sheet_name="定额条目", rows=10)
        session.commit()
    logger.info("mock pipeline done archive_id=%s status=parsed", archive_id)


def _upload_fake_xlsx(key: str, *, sheet_name: str, rows: int) -> None:
    """写一个最小可用的 xlsx 到 MinIO（用 openpyxl 构造 10 行假数据）。"""
    import io
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    for i in range(rows):
        ws.append([f"mock-row-{i}"] * 10)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    store = get_object_store()
    store.put_object(PARSE_BUCKET, key, buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def schedule_mock(archive_id: str) -> asyncio.Task:
    """封装 asyncio.create_task，把 task 引用放进 _TASKS 集合。"""
    task = asyncio.create_task(run_mock_pipeline(archive_id))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


def _now():
    from datetime import datetime, UTC
    return datetime.now(UTC)
```

### Step 3 · quota_api.py 新增 7 个端点

**追加到 `file_asset_service/app/quota_api.py`**（与现有 router 共用 prefix `/api/data-lake/quota`）：

```python
# ── P0-4B · quota_parser integration (v0.3, 2026-07-28) ─────────────────

# env: QUOTA_PARSE_MOCK=1 → 用 mock runner；否则调用真 worker（未来）
import os as _os
_QUOTA_PARSE_MOCK = _os.environ.get("QUOTA_PARSE_MOCK", "0") == "1"

if _QUOTA_PARSE_MOCK:
    from app.mock_parse_runner import schedule_mock as _schedule_parse


@router.post("/archives/{archive_id}/parse")
async def trigger_parse(archive_id: str, body: dict | None = None, session: Session = Depends(get_db_session)):
    """触发阶段 A。QUOTA_PARSE_MOCK=1 时调 mock；否则立即返回 501（真 worker 待上线）。"""
    archive = session.get(Archive, archive_id)
    if archive is None:
        raise HTTPException(404, "ARCHIVE_NOT_FOUND")
    if archive.domain_type != "quota":
        raise HTTPException(400, "NOT_A_QUOTA_ARCHIVE")
    profile = (body or {}).get("profile")
    try:
        from app.quota_parser.service import trigger_parse as _trigger
        archive = _trigger(archive, profile=profile)
    except ValueError as e:
        raise HTTPException(422, str(e))
    session.commit()
    session.refresh(archive)
    if _QUOTA_PARSE_MOCK:
        _schedule_parse(archive_id)
    return _build_archive_detail(archive, session)


@router.get("/archives/{archive_id}/candidate.xlsx")
def get_candidate_xlsx(archive_id: str, session: Session = Depends(get_db_session)):
    """下载 candidate.xlsx。"""
    archive = session.get(Archive, archive_id)
    if archive is None:
        raise HTTPException(404, "ARCHIVE_NOT_FOUND")
    if not archive.candidate_xlsx_key:
        raise HTTPException(404, "CANDIDATE_NOT_READY")
    from app.storage import get_object_store
    from app.quota_parser.service import PARSE_BUCKET
    store = get_object_store()
    data = store.get_object(PARSE_BUCKET, archive.candidate_xlsx_key)
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{archive.title}-candidate.xlsx"'},
    )


@router.post("/archives/{archive_id}/reviewed")
async def upload_reviewed(
    archive_id: str,
    file: UploadFile = File(...),
    session: Session = Depends(get_db_session),
):
    """上传 reviewed.xlsx → 阶段 B → 写 final_xlsx_key。"""
    # 实现见 Step 3 详细代码（含 openpyxl 结构校验 + mock runner 路径分支）
    ...


@router.get("/archives/{archive_id}/final.xlsx")
def get_final_xlsx(...):
    """下载 final.xlsx。"""
    ...


@router.get("/archives/{archive_id}/manifest")
def get_manifest(...):
    """返回 Manifest JSON（与 quota-parser-result/v1 schema 对齐）。"""
    ...


@router.get("/archives/{archive_id}/qa-report")
def get_qa_report(...):
    """返回 QA 报告（JSON / ?format=md 切换）。"""
    ...


# 扩展 GET /archives/{archive_id}：在返回里追加 `parse` 子对象
def _build_archive_detail(archive: Archive, session: Session) -> dict:
    """现有 archive detail 构建函数追加 `parse` 子对象。"""
    from app.quota_parser.service import build_parse_section
    detail = _existing_archive_detail(archive, session)  # 现有逻辑
    detail["parse"] = build_parse_section(archive)
    return detail
```

> **完整代码在 Step 3 实施时落到 quota_api.py**（避免本文档膨胀）。本 plan 给接口签名 + 数据契约。

### Step 4 · main.py 注册 router + 启动验证

**改 `file_asset_service/app/main.py`**（L179 后追加一行）：

```python
from app.quota_parser_api import router as quota_parser_router  # 见下方

app.include_router(quota_router)
app.include_router(quota_parser_router)  # 新增
```

**注意**：现有 `quota_api.py` 的 router prefix 已经是 `/api/data-lake/quota`；新端点也挂同一 prefix，避免拆两个 router。可选择：

- **方案 1（推荐）**：把 7 个新端点直接追加到 `quota_api.py`（不拆 router）。改动量最小，import 不变。
- **方案 2**：拆 `quota_parser_api.py`，prefix 同 `/api/data-lake/quota`，在 main.py 多 `include_router` 一次。

**今天按方案 1 实施**：Step 3 在 `quota_api.py` 末尾追加 7 个端点 + `trigger_parse` / `_build_archive_detail` 等 helper。

**启动验证**：

```bash
PY=/d/miniconda3/envs/file-asset/python.exe
export QUOTA_PARSE_MOCK=1   # 默认开 mock，curl 可直接 demo
cd file_asset_service
$PY -m app.main serve  # 或 python serve.py

# 另一窗口
curl http://localhost:8000/healthz
curl http://localhost:8000/api/data-lake/quota/capabilities
curl http://localhost:8000/api/data-lake/quota/archives/<archive_id>
# → 应返回 ArchiveDetail，含 parse=null（未触发解析）

curl -X POST http://localhost:8000/api/data-lake/quota/archives/<archive_id>/parse \
  -H "Content-Type: application/json" -d '{"profile":"sichuan"}'
# → 200 + ArchiveDetail，parse.status="parsing"；5-10s 后 status="parsed"

curl http://localhost:8000/api/data-lake/quota/archives/<archive_id>/candidate.xlsx \
  -o /tmp/candidate.xlsx
# → mock 写入的假 xlsx（10 行 10 列）
```

---

## 5. 前置先决条件（**今天 step 1 前必须做**）

| # | 任务 | 阻塞 | 谁做 |
|---|---|---|---|
| P1 | `quota/parser/` 加 `pyproject.toml`（最小包声明，依赖 quota_parser 实际需要的库：openpyxl / requests / beautifulsoup4 / lxml） | step 2/3 端点要 `import quota_parser` | CC |
| P2 | file-asset 环境装 `quota_parser` editable：`pip install -e quota/parser/` | 同上 | CC |
| P3 | `quota_parser.config.PROFILE` 与 `external/` 复用层在 file-asset 环境也能 import（路径依赖 `D:/工程造价学习/...`） | mock 跑起来时避免 FileNotFoundError | CC + 验证 |

**P3 验证脚本**：

```bash
PY=/d/miniconda3/envs/file-asset/python.exe
cd /d/工程造价学习/定额解析   # 或 file-asset 的 cwd
$PY -c "from quota_parser import run_quota_pipeline, finalize_reviewed_xlsx; print('ok')"
```

注意：`quota_parser` 包的 `external/` 复用层依赖 `D:/工程造价学习/...` 绝对路径。在 file-asset 工作目录里跑时需要先确认 cwd。

---

## 6. 验收（今天 step 1-4 完成后的最小标准）

- [ ] `models.py` Archive 加了 13 列，`init_db()` 跑后 PG `archive` 表有新列
- [ ] `quota_parser` 包通过 `pyproject.toml` 在 file-asset 环境可 `import`
- [ ] `file_asset_service/app/quota_parser/` 子包就位（`__init__.py` + `service.py`）
- [ ] `mock_parse_runner.py` 实现完成（async def + 新 session + 假 xlsx 上传）
- [ ] `quota_api.py` 加 7 个端点 + `trigger_parse` helper；`GET /archives/{id}` 返回 `parse` 子对象
- [ ] `main.py` 启动后 `/healthz` 通；`QUOTA_PARSE_MOCK=1` 下 POST /parse → 5-10s 后状态变 `parsed`
- [ ] curl 7 个端点全部 200 / 预期错误码
- [ ] git commit 提交今天所有改动

---

## 7. 留作后续批次（不在今天 4 步内）

| 批次 | 内容 |
|---|---|
| **批次 2 · 真 Worker** | step 8/9 —— `serve_worker()` 真实轮询 FileProcessing + 启动脚本 |
| **批次 3 · 前端** | step 10-13 —— `quota-parse.js` / `quota-parse-api.js` / `quota-parse-mock.js` + `quota-ui.js` 改 + `index.html` 改 |
| **批次 4 · 测试 + 文档** | step 14-18 —— quota_parser 包单测 + quota_api.py 端点单测 + E2E（curl 7 端点 + 看 Archive.parse_status 推进）+ CLAUDE.md / README / SPEC 同步 |

---

## 8. 开放问题（待用户/运维确认）

| # | 问题 | 推荐方案 | 阻塞哪步 |
|---|---|---|---|
| 1 | `Archive.status` vs `parse_status` 命名冲突（cost_info 8 状态 vs quota 7 状态） | 新增独立 `parse_status` 字段（§2.1 方案 A） | step 1 |
| 2 | MinIO bucket 名 `quota-extract` 是否与运维约定冲突 | 单点常量 `PARSE_BUCKET`，1 行可改；先用 `quota-extract` | step 2/3 |
| 3 | `parse_task_id` 是否要唯一约束 | 不加（1 archive 1 run） | 无 |
| 4 | QA 报告保留 `json` 还是只 `md` | 两者都保留，`?format=md` 切换 | step 3 |
| 5 | archive 原 PDF sha256 是否要存到 Archive 表 | 暂不存（FileAsset.sha256 已有，跨表 join） | 无 |
| 6 | web-frontend SPEC §6.1 中 `archive.status == parsing` 的表述是否要改为 `archive.parse_status == "parsing"` | 改（与方案 A 对齐） | step 4 后单独改 SPEC |
| 7 | 前端解析区块何时做 | 批次 3（不在今天 4 步内） | 无 |
| 8 | 中间产物（md/html/xlsx）已落 MinIO 后，再次解析能否复用上轮产物 | 不复用（每次重跑 OCR 保证最终产物是最新；如需可重入再加 content hash 缓存） | step 2 |
| 9 | worker 回写 `parse_status` 后前端刷新机制 | 5-10s 轮询（沿用 mock 路径）；SSE/WebSocket 留待后续 | step 4 |
| 10 | worker 跑在哪个 conda env（`file-asset` 还是新增 `quota-parser`） | 新增 `quota-parser` env（与 web 隔离，防 PaddleOCR/transformers 拖垮 uvicorn） | step 2 |
| 11 | 失败可重试细分（`failed_user` vs `failed_permanent`） | 区分网络抖动/OOM → failed_user（自动重试 ≤2 次）；PDF 损坏/加密 → failed_permanent（人工决策） | step 2 |
| 12 | 解析超时与 OOM 保护 | 单 PDF 软超时 30 分钟 / 硬超时 45 分钟；GPU OOM 触发由 minerU 容器自身重启 | step 2 |
| 13 | intermediate ↔ final 反向溯源（QA 点行 → 原 PDF 页） | manifest.json 追加 `artifact_index`：`{page: int, block_id: str, md_offset: int}` | step 2 |
| 14 | `manifest.json` 字段清单（参考 `cost_info` manifest） | archive_id / parser_version / profile_hash / artifact_keys / timing / pdf_sha256 / chain_hash | step 2 |
| 15 | `trigger_quota_parse` 的 `body.profile` 语义 | 暂未定义（前端不传）；先固化默认 `profile=default`，第二批再开放给用户选 | step 4 |
| 16 | worker 写 `archive_event` 走哪条路径 | 走 `register_quota_event` helper（拒绝绕过状态机的裸 INSERT） | step 2 |
| 17 | 审计粒度（actor 区分 `user/system/worker`） | actor_type 枚举：`user` / `system` / `worker`；action 列表：`quota_parse_triggered` / `quota_parse_succeeded` / `quota_parse_failed` | step 2 |
| 18 | 前端「开始解析」按钮点击幂等 | 点完立刻 `disabled + spinner`；后端 409 时前端区分「已在跑」vs「已成功」分别 toast | step 4 |
| 19 | mock vs real 环境隔离（启动时强制打印） | `QUOTA_PARSE_MOCK` 在进程启动日志首行打印；生产误用 mock 必须告警 | step 2 |
| 20 | 解析进度反馈（百分比/页码） | 第一版只显示「解析中」徽章；SSE 进度推送留后续批次 | step 4 |
| 21 | 失败时前端展示 `parse_error_code` / `parse_error_message` | 详情页「失败原因」区块（折叠）；列表行 tooltip 简版 | step 4 |
| 22 | 「重新解析」清理范围（`archive_event` 旧事件 / `outbox` 旧事件 / `crawl_lineage`） | 仅清 `parse_*` 字段 + 4 个 parse_* role 的 archive_file 行；旧事件保留（审计需要） | step 2 |
| 23 | 7 个解析端点的真数据流回归（mock ≠ 真） | 端到端冒烟：1 份小 PDF 跑通 trigger → candidate.xlsx 下载 → reviewed 上传 → final 下载 | step 3 |

---

**锁版本**：v1（2026-07-28 讨论锁定）。

后续破坏性变更（拆 adapter 子包到独立子项目、改 `Archive.parse_status` 命名、加 DB 唯一约束等）必须走 v2。