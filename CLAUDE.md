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
