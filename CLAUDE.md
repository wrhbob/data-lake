# CLAUDE.md

## Schema authority (防止 CC 在两份文档间反复横跳)

本项目存在三套 schema 描述，**不是竞争关系，是分层关系**。权威源如下：

| # | 名称 | 位置 | 状态 | 权威级别 |
|---|------|------|------|----------|
| 1 | **全链路技术规格** | `quota/四川2025/定额数据湖全链路技术规格.md` | Target spec | **数据库 DDL 权威源** |
| 2 | SPEC-QA-001 | `file_asset_service/app/models.py` | Implemented | 全链路规格 Layer 0 的当前实现 |
| 3 | Skill SPEC-011/012 | `.claude/skills/quota-pdf-extractor/SKILL.md` 引用 | External (Xiaojiang repo, 不在本仓库) | **仅作参考，非权威** |

### 权威裁定

1. **数据库 DDL → 全链路技术规格是权威源。** 包括 Layer 1 的 `quota_item`, `consumption`, `qa_printed_price`, `coeff_rule`, `resource_master` 以及 Layer 2/3 的所有表。如果 SPEC-QA-001 (models.py) 与全链路规格冲突，以全链路规格为准，models.py 应向其靠拢。

2. **解析物 (artifact) 规范 → skill 的 workflow-contract.md 定义了 JSONL 中间产物的不可变性与审计门禁**（candidate → reviewed → import）。但 JSONL 的具体字段结构如果与全链路规格的 DB 表不能对齐，**以 DB 表为准调整 JSONL**——JSONL 是 transient work product，DB 是 serving contract。

3. **SPEC-011/012 不在本仓库。** skill 中引用的 `cost_price/specs/10-assets/SPEC-011-source-file-registry.md` 和 `SPEC-012-parsed-quota-schema.md` 属于 Xiaojiang 仓库，可能已与本仓库的全链路规格分叉。**不允许假设那两个文件的内容对本仓库有约束力。**

### 两层之间的映射关系

skill 的 JSONL artifact 层（parse output）→ 全链路规格的 DB 层（serving tables）：

```
JSONL artifact (skill)              →   DB table (全链路规格)
──────────────────────────              ──────────────────────────
chapters.jsonl                     →   (无直接表对 — 章节层级在 quota_item.chapter_path 中以 JSONB 承载)
quota_items.jsonl                  →   quota_item
resource_lines.jsonl               →   consumption (需先对齐 resource_master)
cost_lines.jsonl                   →   qa_printed_price
conversion_groups/options.jsonl    →   qty_conversion
rules.jsonl                        →   coeff_rule
corrections.jsonl                  →   (无直接表对 — 审计/修正轨迹另存)
```

**关键约束：**
- `consumption.quantity` 存书面原值，不做数值修约；量价分离原则下消耗量是定额本体
- `qa_printed_price` 仅用于校验和 (checksum)，绝不写入业务价格字段
- 所有 parser 输出默认 candidate/review_required，审定后才可导入 DB

### 实施优先级

当前 models.py (SPEC-QA-001) 实现了 Layer 0（档案层），尚未实现 Layer 1（解析层 DB 表）。下一步新增 `quota_item`, `consumption`, `qa_printed_price` 等表时，**DDL 必须与全链路技术规格 §3.3 的定义一致**，不可从 skill 的 JSONL 结构直接推导数据库 schema。

---

## 项目概览

定额数据湖交接项目 (data_lake_handoff)。将各省建设工程定额 PDF（扫描版）入湖 → 解析为结构化定额库 → 支撑"清单自动套定额"。

技术栈: Python 3.11, FastAPI, SQLAlchemy 2.0, 共享 NAS PostgreSQL（运行时；SQLite 仅限测试）, PaddleOCR PP-StructureV3

## 运行环境事实核验（共享 NAS / 数据湖）

**共享服务的地址、端口和数据位置必须以当前生效配置与实际连通性为准；历史方案、部署文档、模板文件只可作为背景，不能据此断言生产环境事实。**

1. 回答或修改数据库 / MinIO 连接信息前，先读取当前运行环境的 `../.env`（不得输出口令、access key、secret），并通过一次真实连接确认数据库名和服务端信息。
2. 必须区分**客户端连接入口**与 Docker / NAS 内部端口。公司电脑应使用 `.env` 中可访问的入口，不能把容器内 PostgreSQL 端口或旧的本机隧道端口写进部署说明。
3. Git 分支只包含程序代码；档案元数据在共享 PostgreSQL，原件在 MinIO。`.env` 被 Git 忽略，因此新电脑必须单独配置同一套共享连接，不能把 `git pull` 当作数据同步。
4. 发现部署文档、`.env.example` 与实际生效配置不一致时，先用实测结果修正模板和文档，再对外给出运行指引。

> 2026-07-15 已验证的运行方式：以共享服务的实际入口（当前为 `djtsoft.x3322.net:5433`）连接 `file_asset`；服务端看到的 Docker 内部 PostgreSQL 端口不是同事机器的部署端口。

## 核心设计原则（来自全链路规格）

1. **量价分离** — 子目表只存消耗量，不存价格；印刷基价只入 qa_printed_price 做校验和
2. **工序桥** — 清单 ↔ 标准工序 ↔ 定额子目，中间层是受控工序词表
3. **统一模型 + 省级 Profile** — 全国统一 Schema，省际差异仅收敛到 Profile 类
4. **确定性优先** — 硬坐标 > 结构化槽位 > 向量语义；LLM 只在无法确定性求解时介入

## 代码结构

```
file_asset_service/app/
├── models.py          # Layer 0 DDL (SPEC-QA-001, implemented)
├── schemas.py         # Pydantic API schemas
├── archive_service.py # Archive CRUD
├── archive_rules.py   # Business key + file role rules
├── quota_taxonomy.py  # 受控词表枚举
├── quota_api.py       # /api/data-lake/quota 路由
├── quota_registry.py  # 定额入库逻辑
├── quota_projection.py # P0-3 投影与对账
├── quota_seed.py      # P0-3 种子批次
└── ui/                # 前端 (audit.html, review.html)

quota/四川2025/        # 种子 PDF + 规格书
docs/                  # 实现报告与计划
```

## 入湖纪律：单一受控入口 (Ingestion Discipline)

### 核心规则

**入湖必须是单一受控路径。"新增档案"是唯一入口，上传只是它流程里的一步。**

全链路规格和 skill 都把"源文件注册"定为第一道门。必填元数据包括：

| 字段 | 来源 | 说明 |
|------|------|------|
| 省份/直辖市 | `jurisdiction_code` | GB/T 2260，不允许为空 |
| 专业 | `discipline_code` | 房屋建筑/装饰/安装/市政/园林… |
| 版本年 | `edition_year` | 如 2020、2025 |
| 分册/卷 | `volume_code` + `volume_title` | 一册可对应多个 PDF（多卷） |
| 费用口径 | `cost_scope` | comprehensive（综合基价）\| direct（仅人材机），**不允许默认值** |
| 资料类型 | `book_kind` | base（基准定额）\| adjust（调价文件）\| fee（费用定额）\| mapping（清单定额对照）\| errata（勘误），**不允许默认值** |
| 来源权属 | `source rights/status` | 解析可先于权属确认，但 publish/import 必须等待权属清除 |

### 为什么砍掉"上传定额"按钮

当前代码中存在一条绕开注册的路径——`quota-ui.js` 的"上传定额"按钮 → `main.py` 的 `POST /api/data-lake/quota/upload`。这条路径的问题是：

1. **制造了第二条入口。** 用户上传文件时不填省/专业/版本/口径/book_kind，文件落为无主 blob，只能堆进"待归档"候选堆。注册这道门从入口被挪到了入口之后。

2. **跳过必填元数据。** 端点使用硬编码的 `source_id = "quota-manual-upload"`，title 从文件名推导，business_key 用空参数生成，material_type 从前端分类粗略映射。全部缺少 `QuotaPublicationSet` 关联、`cost_scope`、`book_kind`。

3. **状态直接跳到 `archived`。** 跳过了 `uploaded → registered → parsing → parsed → qa_passed → usable` 状态机。

4. **必然形成腐化。** 两条入口并存的系统，时间一长一定出现一批"传了但没人认领"的无主文件。界面上的"待归档"计数会不断增长，变成只增不减的垃圾堆。

### 正确的动线

```
新增档案(唯一入口)
  → 表单: 必填 省/专业/版本年/口径/book_kind（口径和类型不允许默认值）
  → 表单内: 上传一个或多个 PDF（一册多卷）
  → 提交: 算 SHA-256 hash → 查重
  → 落状态机: uploaded → registered
```

上传动作被包在登记事务里，不存在无主文件。

### 批量入湖（替代裸上传的正确方案）

31 省 × 多专业 × 多版本，逐份填表太慢。解法是 **"批量新增档案"**，入口仍是一个：

```
上传一个文件夹/压缩包 + 一张元数据清单(CSV)
  → 逐行校验: 必填字段(省/专业/版本年/口径/book_kind/文件名匹配)
  → 逐行: 算 hash → 查重
  → 逐行: 创建 PublicationSet + Archive + ArchiveFile
  → 报告: 成功/跳过(重复)/失败(校验不通过) 计数
```

**入口仍然是一个，纪律不破。**

### 代码中需调整的位置（按优先级）

**P0 — 砍掉裸上传入口：**

| 文件 | 位置 | 内容 |
|------|------|------|
| `file_asset_service/app/ui/quota-ui.js` | L196-199 | `quotaUploadToggle` 按钮渲染 |
| `file_asset_service/app/ui/quota-ui.js` | L1310-1373 | 上传对话框事件 + fetch 调用 |
| `file_asset_service/app/ui/index.html` | L132-158 | `quotaUploadDialog` HTML |
| `file_asset_service/app/main.py` | L1085 | `POST /api/data-lake/quota/upload` 端点 |
| `file_asset_service/app/database.py` | L605-634 | `_seed_quota_manual_upload_source()` |

**P1 — 界面语义对齐：**

| 项 | 当前语义 | 应调整为 |
|----|----------|----------|
| "待归档" tab (`quota-ui.js` L30) | "传了没登记的文件" | "登记了但没走完解析/质检的档案" — 与状态机 `registered → parsing → parsed → qa_passed` 对齐 |
| 统计栏 `pendingRaw` (`quota-ui.js` L179) | "份原件待归档" | 改为统计状态机中间态的档案数 |
| `quota-ui.js` L169-172 调试标签 | `Archive API / domain_type: quota` | 已限定 dev 环境，保留；正式版确认 `isDev()` 生产环境返回 false |
| `index.html` L90 调试标签 | `domainPill` | 生产环境隐藏，归入开发者模式 |

**P2 — 批量入湖：**
- CSV 模板定义（列: province/discipline/edition_year/cost_scope/book_kind/filename）
- 批量校验逻辑（复用 `archive_rules.py` 的 business key 构建）
- 批量导入报告（每行成功/跳过/失败 + 原因）

---

## 常用命令

```bash
cd file_asset_service
pip install -e ".[dev]"
python serve.py                          # 启动开发服务器
python -m pytest tests/ -x -v            # 运行测试
```

---

## 服务运维 SOP（启动 / 停止 / 彻底杀干净）

> 适用于本机开发 + 单机部署。**所有命令必须在仓库根目录（main 仓根目录）下执行**。分发到他人机器后，把下面各条 `ROOT=` 改成实际放置路径即可。

### 1. 三个进程的角色

| 进程 | 角色 | 启动入口 | 端口 | 是否常驻 |
|---|---|---|---|---|
| **sweeper** | 独立扫孤儿 running job（30/15 min 超时阈值） | `quota/parser/quota_parser_sweeper.py` | — | 是 |
| **worker** | 常驻消费 `QuotaParseJob`，调 minerU OCR + 跑 pipeline | `quota/parser/quota_parser_worker.py` | — | 是 |
| **web** | FastAPI + uvicorn，对外 HTTP 服务 | `file_asset_service/serve.py` | **8010** | 是 |

### 2. Python 环境

**必须**用 `file-asset` conda 环境（不是 DLSE）。解释器路径：

```
/d/miniconda3/envs/file-asset/python.exe
```

### 3. PID 文件 + 日志约定

```
logs/
├── sweeper.pid / sweeper.log.<YYYYMMDD_HHMMSS>
├── worker.pid  / worker.log.<YYYYMMDD_HHMMSS>
└── web.pid     / web.log                       ← web 没有专用启动脚本,需手起
```

### 4. 启动顺序（**重要：sweeper → worker → web**）

```
sweeper → worker → web
```

**为什么这个顺序**：sweeper 必须在 worker 之前活着 — 它会吸收 worker 启动/重启期间遗留的孤儿 running job（`last_heartbeat_at` 超时判定）。如果 sweeper 后启，worker 已经 claim 的 job 在它眼里没人兜底。

### 5. 启动 SOP

```bash
ROOT="/d/工程造价学习/data_lake0714/main"
cd "$ROOT"

# 1) sweeper — 后台守护,常驻
bash file_asset_service/scripts/run_quota_parser_sweeper.sh
#   → 写 logs/sweeper.pid
#   → log → logs/sweeper.log.<TS>

# 2) worker — 后台守护,常驻
bash file_asset_service/scripts/run_quota_parser_worker.sh
#   启动时会跑 schema 自检:
#     一致 → 日志输出 "schema 自检 ✅: ARCHIVE_FILE_ROLES (28) ↔ ck_archive_file_role 一致"
#     不一致 → fail-fast 退出码 2（参考 quota/DB_SCHEMA.md §5.3 / scripts/verify_db_schema.py）
#   → 写 logs/worker.pid
#   → log → logs/worker.log.<TS>

# 3) web — nohup 后台(没有专用脚本)
PY=/d/miniconda3/envs/file-asset/python.exe
nohup "$PY" -u file_asset_service/serve.py > logs/web.log 2>&1 &
echo $! > logs/web.pid
#   → 端口 8010 (FILE_ASSET_PORT 环境变量可改)
```

### 6. 验证服务是否真活着

```bash
ROOT="/d/工程造价学习/data_lake0714/main"
cd "$ROOT"

# 进程状态
bash file_asset_service/scripts/run_quota_parser_sweeper.sh status
bash file_asset_service/scripts/run_quota_parser_worker.sh status
[ -f logs/web.pid ] && kill -0 "$(cat logs/web.pid)" 2>/dev/null && echo "web alive pid=$(cat logs/web.pid)"

# HTTP 探活
curl -sS -o /dev/null -w "HTTP %{http_code}\n" http://127.0.0.1:8010/docs

# 端口监听
netstat -ano | grep ":8010.*LISTENING"

# python.exe 残留清单
tasklist //FI "IMAGENAME eq python.exe"
```

### 7. 标准停止（优雅退出）

```bash
ROOT="/d/工程造价学习/data_lake0714/main"
cd "$ROOT"

bash file_asset_service/scripts/run_quota_parser_worker.sh stop    # worker
bash file_asset_service/scripts/run_quota_parser_sweeper.sh stop  # sweeper

# web: 没有 stop 脚本,手杀
WPID=$(cat logs/web.pid 2>/dev/null)
[ -n "$WPID" ] && kill "$WPID" && rm -f logs/web.pid
```

### 8. 彻底杀干净（应急 / 重启前兜底）

```bash
ROOT="/d/工程造价学习/data_lake0714/main"
cd "$ROOT"

# 1) 优雅停（即使已死也不报错）
bash file_asset_service/scripts/run_quota_parser_worker.sh stop    2>/dev/null || true
bash file_asset_service/scripts/run_quota_parser_sweeper.sh stop  2>/dev/null || true
WPID=$(cat logs/web.pid 2>/dev/null)
[ -n "$WPID" ] && kill "$WPID" 2>/dev/null || true

# 2) 兜底:用 taskkill 杀残留 python.exe（不带 //F = SIGTERM, 让 psycopg2 正常 close）
#    先 tasklist 看一眼,确认没有别的项目 python 在跑,再 taskkill
tasklist //FI "IMAGENAME eq python.exe"
#    默认 SIGTERM（不带 //F）—— 让 psycopg2 发 FIN, PG 端连接正常关闭
taskkill //IM python.exe 2>&1 | head -10

# 3) 清空所有 pidfile(即便进程已死)
rm -f logs/web.pid logs/worker.pid logs/sweeper.pid

# 4) 验证真的全死
netstat -ano | grep ":8010.*LISTENING" || echo "8010 无人监听 ✅"
tasklist //FI "IMAGENAME eq python.exe"
#   预期输出:"没有运行的任务匹配指定标准"

# 5) ⭐ 清 PG 端僵尸连接（2026-07-31 教训补充）
#    如果步骤 2 里有用过 taskkill //IM python.exe //F (SIGKILL),
#    或者进程是被 Python 之外的信号杀死的, PG 端 TCP 连接会残留
#    (idle in transaction 1+ 小时), 阻塞下次 init_db 的 migrate_blob_columns.
#    症状: web 起不来, init_db 卡在 migrate_blob_columns, DB 端健康, sweeper/worker 正常.
#    必须从本机 psycopg2 连接主动 pg_terminate_backend() 这些 zombie.
PY=/d/miniconda3/envs/file-asset/python.exe
"$PY" -u -c "
import os, sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv('.env', override=False)
sys.path.insert(0, 'file_asset_service')
from app.database import get_engine
from sqlalchemy import text

with get_engine().connect() as c:
    my_ip = c.execute(text('SELECT inet_client_addr()')).scalar()
    rows = c.execute(text('''
        SELECT pid, state, now() - xact_start AS xact_age, substring(query, 1, 60) AS q
        FROM pg_stat_activity
        WHERE datname = current_database()
          AND pid <> pg_backend_pid()
          AND COALESCE(client_addr, inet('127.0.0.1')) = COALESCE(:my_ip, inet('127.0.0.1'))
          AND (
            state IN ('idle in transaction', 'idle in transaction (aborted)')
            OR (state = 'active' AND wait_event_type = 'Lock' AND now() - state_change > interval '1 min')
          )
        ORDER BY xact_start NULLS LAST
    '''), {'my_ip': my_ip}).all()
    if not rows:
        print(f'本机({my_ip}) 无 zombie PG 连接 ✅')
    else:
        print(f'本机({my_ip}) stale PG 连接 {len(rows)} 个, 准备 pg_terminate_backend:')
        for r in rows:
            print(f'  pid={r.pid:>5} state={r.state:<28} xact_age={r.xact_age}  q={r.q}')
        for r in rows:
            ok = c.execute(text('SELECT pg_terminate_backend(:p)'), {'p': r.pid}).scalar()
            print(f'  pg_terminate_backend({r.pid}) -> {ok}')
"
#   预期: stale 连接数清零, 下次 web 启动 init_db 顺利过 migrate_blob_columns
```

> ⚠️ **Windows 进程模型注意点**：worker/web 启动后 spawn 一个 reloader 子进程（pid 不同）。脚本里看到的 `pid=1515` 是 launcher，ps 看到的是 `pid=21112`。kill launcher 通常会让子进程跟着退出，但**应急情况**用 `taskkill //IM python.exe` 兜底（默认 SIGTERM，不要带 //F）。
>
> ⚠️ **不要**用 `kill -9` / `taskkill //F` 杀当前 Claude Code 自己跑着的 python 子进程（可能是其他工具的子进程，会破坏用户的 session）。
>
> ⚠️ **杀 web/worker 用 SIGKILL 的副作用**（2026-07-31 教训）：`taskkill //IM python.exe //F` 是 SIGKILL，psycopg2 没机会发 FIN 包，**PG 端 TCP 连接残留**（TCP keepalive 默认 7200s 才超时）。症状：下一次 web 启动，`init_db()` 卡在 `migrate_blob_columns` 永远不出，DB 端健康，sweeper/worker 正常。**必须**跑 §8 步骤 5 清掉本机 zombie PG 连接。**优先**用 `taskkill //IM python.exe`（不带 //F，默认 SIGTERM）让 psycopg2 优雅关闭。

### 9. 启动失败排查清单

| 现象 | 排查 |
|---|---|
| worker 启动后立刻退出,exit code 2 | `ARCHIVE_FILE_ROLES` 与 DB `ck_archive_file_role` 不一致。看 `logs/worker.log.<TS>` 第一行,跑 `python scripts/verify_db_schema.py` 看修复 SQL |
| worker 启动后 claim job,但 OCR 卡死 | 看 `logs/worker.log.<TS>` 是否 `poll 404 (N/3): ...`,N 到 3 会自动 `PollTaskLost` 退出（v0.7+） |
| web 启动后 `curl /docs` 500 | 看 `logs/web.log`,大概率是 `.env` 没加载（`get_settings()` 报 `database_url=...`） |
| 端口 8010 已被占用 | `netstat -ano | grep ":8010"` → 找到占用 pid → `taskkill //PID <pid> //F` |
| sweeper "另一个 sweeper 已占 lockfile" | `/tmp/quota_parser_sweeper.lock` 残留,删掉重启（Windows 跳过 flock,不会撞这条） |
| **web 启动后 init_db 卡住不退出（无 `[serve] shared NAS database ready`）** | **PG 端僵尸连接阻塞锁**：本机 client_addr 有 idle in transaction 1min+ 或 active wait=Lock。跑 §8 步骤 5 主动 `pg_terminate_backend()` 清掉, 再重启 web。诊断 SQL: `SELECT pid, state, now()-xact_start AS xact_age, wait_event_type FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid() AND (state LIKE 'idle in transaction%' OR wait_event_type = 'Lock');` |

---

## 分发准备（发给他人前的检查清单）

### 1. Python 环境（必读）

**必须用 `file-asset` conda 环境**（不是 DLSE）。解释器：

```
/d/miniconda3/envs/file-asset/python.exe   # 本机
```

接收方需在自己机器上准备同名环境，并装齐下方依赖（本机已全装，可 `pip list` 对照）。

### 2. 依赖清单（file-asset 环境实测版）

| 包 | 版本 | 用途 |
|---|---|---|
| fastapi / uvicorn / starlette | 0.140 / 0.51 / 1.3 | web 服务 |
| sqlalchemy / psycopg | 2.0.51 / 3.3.4 | PostgreSQL（共享 NAS） |
| pydantic / python-dotenv | 2.13 / 1.2 | 配置 + 校验 |
| requests / httpx | 2.34 / 0.28 | MinerU OCR API 调用 |
| openpyxl | 3.1.5 | xlsx 读写（信息价 + 定额） |
| pymupdf (fitz) | 1.28.0 | PDF 分块 / 页数统计 |
| pyyaml | 6.0.3 | 成都 step2 城市模板加载 |
| boto3 | 1.43.56 | MinIO / S3 对象存储 |
| python-multipart | 0.0.32 | 文件上传 |

### 3. 信息价脚本已内迁（相对路径，无需配绝对路径）

信息价解析脚本已从仓库外的 `--v2-main` **内迁到 `info_price_pipeline/`**（6 城：成都/重庆/北京/武汉/湖北/广州）。

`info_price_parse.py` 里 `PIPELINE_BASE` 用 `Path(__file__)` 推导到 `main/info_price_pipeline`；`.env` 里**不再需要** `INFO_PRICE_PIPELINE_BASE`（仅自定义布局时才覆盖）。换机器不会再有「文件夹找不到」问题。

### 4. .env 配置（分发后必填）

复制 `.env.example` → `.env`，填共享连接（值不写这里，见 `.env.example` 占位符）：

- `FILE_ASSET_DATABASE_URL` — 共享 NAS PostgreSQL（当前入口 `djtsoft.x3322.net:5433`）
- `FILE_ASSET_S3_*` — MinIO 连接（endpoint / access key / secret / region）
- `FILE_ASSET_RAW_BUCKET` / `FILE_ASSET_EXTRACT_BUCKET` / `FILE_ASSET_REPORT_BUCKET`
- `INFO_PRICE_MINERU_API_URL` — MinerU OCR 端点（当前 `http://172.16.20.23:8000`）

> ⚠️ `.env` 含真实口令，**被 Git 忽略**。分发时只带 `.env.example`，不带 `.env`；接收方单独配置同一套共享连接（`git pull` 不会同步数据，也不应把 `.env` 入库）。

### 5. 分发步骤

1. 拷贝整个 `main/` 目录（含 `info_price_pipeline/`、`file_asset_service/`、`quota/`）
2. 准备 `file-asset` conda 环境（装 §2 依赖）
3. 复制 `.env.example` → `.env`，填共享 NAS / MinIO / MinerU 连接
4. 按 §服务运维 SOP 启动：sweeper → worker → web（端口 8010）

### 6. 不打包的内容

- `info_price_pipeline/*/3_中间产物`、`4_输出`、`5_日志`（运行时产物，OCR 缓存 / 日志）
- `.env`（真实口令）
- `logs/`（本机日志 + pid）
- `*.pyc` / `__pycache__` / `_baseline` / `*.bak*`（缓存 + 备份）
