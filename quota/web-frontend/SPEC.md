# quota-parser 配套网站前端 SPEC（v0.2）

> 配套文档：[`quota/parser/SPEC.md`](../parser/SPEC.md)（v0.2，Implementation Locked）。
> 本 SPEC 描述**网站侧**：UI 行为、前端消费的 HTTP API 契约、以及 Worker 尚未上线前的 Mock 策略。
>
> 跨文档核对：见 §11，逐字段对照 `quota/parser/SPEC.md`。两份 SPEC 必须一起审阅；后端 SPEC 是后端数据形态的权威源，本 SPEC 是 UI 行为与 HTTP API 暴露面的权威源。

---

## 0. 文档元信息

- **状态**：Draft v0.2（已与 parser/SPEC.md v0.2 + AI 评审意见对齐）
- **配套**：[`quota/parser/SPEC.md`](../parser/SPEC.md) v0.2
- **范围**：清单定额档案台（`quota-ui.js`）内新增的「解析任务」tab + 档案详情内的「解析」区块。
- **读者**：前端实现者、parser 维护者（用于跨文档核对）。

---

## 1. 目标与范围

### 1.1 目标

1. 让用户通过 Web UI 触发、监控、并处置任何 quota 档案的解析任务。
2. 严格遵循 [`quota/parser/SPEC.md`](../parser/SPEC.md) §5 / §6 / §11 / §12 定义的数据契约——前端只消费 parser 产出的内容，不擅自转换字段。
3. 复用现有清单定额档案台的壳、能力开关与 `quota-api.js` 模式；不引入新的全局 UI 框架。

### 1.2 范围（v0.1 必做）

- 清单定额档案台内新增「**解析任务**」tab。
- 档案详情视图内新增「**解析**」区块（按 archive.status 状态自适应渲染）。
- 7 个新 HTTP 端点（见 §5），由 `quota-parse-api.js` 统一封装。
- 复用现有 `archive.status` 状态机（`registered → parsing → parsed → qa_passed → usable`），不在 Archive 上加新状态列。
- 下载 `candidate.xlsx` / `final.xlsx`；上传 `reviewed.xlsx`。
- 内联展示 Manifest 摘要 + QA 报告摘要。
- Mock 模式（`QUOTA_PARSE_MOCK=1`），让 UI 在 Worker 未上线时即可开发与演示。

### 1.3 非范围

- 浏览器内 XLSX 编辑（按你的决策：用户下载后用 Excel 编辑再上传——即 [`parser/SPEC.md`](../parser/SPEC.md) §10 选项 B）。
- Worker 健康检查 / 调度 / 多 Worker 编排。
- 在档案详情之外的全局「提交 PDF」页面（v0.1 不做）。
- 审计日志 / 复核轨迹（落在 [`parser/SPEC.md`](../parser/SPEC.md) 的 Worker 日志里）。
- v0.1 的「解析任务」tab 不含批量操作，仅可筛选。

---

## 2. 命名与术语

| 名词 | 含义 | 对应 parser/SPEC.md |
|---|---|---|
| **解析任务（Parse Run）** | 一次 PDF → XLSX 的完整流水线调用；隐式承载在 Archive 一行内 | §2 "Worker"，§5.1 "stage A" |
| **candidate** | OCR + 抽取 + autofinalize 之后的中间 XLSX | §5.1 `candidate_xlsx_path`，§6.1 schema |
| **reviewed** | 用户在本地 Excel 中编辑过的 XLSX | §5.2 `finalize_reviewed_xlsx` 输入，§6.2 |
| **final** | 阶段 B 直接落盘的最终 XLSX（不再跑 autofinalize） | §5.2 `final_xlsx_path` |
| **Manifest** | parser 返回的结构化结果 `task-result.json` | §6.2 |
| **QA 报告** | `qa_report.json` + `qa_report.md`；结构与数值校验 | §12.2 |
| **Profile** | 省级解析规则集（`sichuan` / `chongqing`），入湖时已冻结 | §7 |
| **解析状态** | 给用户看的逻辑状态，由 `archive.status` 推导 | §11 + §6.2 status 枚举 |

---

## 3. 业务流（用户视角）

```
[档案详情]
  status = registered
    ↓ 用户点「开始解析」
[status: parsing]    POST /parse（立即返回）
    ↓ Worker 跑 OCR + 抽取
    ↓ 前端每 3s 轮询档案详情
[status: parsed]
    ↓ 用户点「下载 candidate.xlsx」
    ↓ 用户在本地 Excel 编辑
    ↓ 用户点「上传 reviewed.xlsx」
[status: qa_passed]   POST /reviewed
    ↓ 用户点「下载 final.xlsx」
[完成]
```

异常分支（以 banner 形式呈现）：

| Worker 结果（`error_code`） | UI 表现 |
|---|---|
| `transient` | 黄色 banner「Worker 重试中（N/M）」+ 继续轮询 |
| `failed_permanent` | 红色 banner「解析失败，请联系运维」+ 「重新解析」按钮 |
| `failed_user`（最常见：reviewed.xlsx 不合法） | 红色 banner + 来自 `InvalidXlsxStructureError` 的内联原因 |

---

## 4. 文件结构（新增 / 改动）

```
quota/web-frontend/
├── SPEC.md                    ★ 本文档
└── README.md                  （后续）快速上手指南

file_asset_service/app/ui/
├── quota-parse.js             ★ 新建：tab 视图 + 档案详情内解析区块
├── quota-parse-api.js         ★ 新建：HTTP 客户端 + mock 模式开关
├── quota-parse-mock.js        ★ 新建：浏览器内 mock fixture 与假轮询
├── quota-ui.js                ✎ 改：新增「parse」tab；档案详情加解析区块
├── quota-api.js               ✎ 改：暴露 `archive.parse` 字段格式化器
└── index.html                 ✎ 改：注册三个新脚本

file_asset_service/app/
├── quota_api.py               ✎ 改：7 个新端点（mock 分支由 QUOTA_PARSE_MOCK=1 启用）
├── models.py                  ✎ 改：Archive 增加 `parse_*` 列（见 §6.1）
├── schemas.py                 ✎ 改：ArchiveDetailResponse 增加 `parse` 子对象
└── mock_parse_runner.py       ★ 新建：模拟 Worker（sleep + 状态推进）
```

> 前端 SPEC 只对前端目录负责。后端目录的改动列在这里，是为了 parser 维护者能一并审阅 API 契约；后端实现属于另一个工作流。

---

## 5. HTTP API 契约（前端消费）

所有端点统一挂在 `/api/data-lake/quota/` 下，与现有 `quota_api.py` 保持一致。除非特别说明，请求/响应均为 `application/json; charset=utf-8`。所有写端点直接返回更新后的 `ArchiveDetail`，前端无需额外 GET 即可重新渲染。

### 5.1 `POST /archives/{archive_id}/parse`

触发解析（阶段 A）。

- **请求体**：`{ "profile": "sichuan" | "chongqing" | null }`
  - `null` → 使用档案上已有的 profile（入湖时已确定）。
- **副作用**：`archive.status` 从 `registered` 推进到 `parsing`。若已是 `{parsing, parsed, qa_passed}` 之一，返回 `409 ALREADY_PARSING_OR_DONE`。
- **成功响应 200**：`ArchiveDetail`（见 §6.1）。
- **错误**：
  - `404 ARCHIVE_NOT_FOUND`
  - `409 ALREADY_PARSING_OR_DONE`
  - `422 INVALID_PROFILE`（显式 profile 不在注册表内）

对照：parser/SPEC.md §5.1 `run_quota_pipeline`。

### 5.2 `GET /archives/{archive_id}`（扩展）

现有端点返回中追加 `parse` 子对象（见 §6.1）。**不为状态轮询单独开端点**——前端在 `archive.status == parsing` 时，每 3 s 拉一次现有详情接口即可（含 `volumes` / `files` join，体积在可接受范围；不做轻量裁剪）。

对照：parser/SPEC.md §6.2 Manifest——前端从 `archive.parse` 字段直接读 Manifest 内容，零翻译。

### 5.3 `GET /archives/{archive_id}/candidate.xlsx`

下载 `candidate.xlsx`。

- **响应 200**：`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` 二进制流，attachment filename = `<archive.title>-candidate.xlsx`。
- **错误**：
  - `404 CANDIDATE_NOT_READY`（archive.status ∉ {parsed, qa_passed, usable}）
  - `404 ARCHIVE_NOT_FOUND`

对照：parser/SPEC.md §5.1 `candidate_xlsx_path`。

### 5.4 `POST /archives/{archive_id}/reviewed`

上传用户人工编辑过的 `reviewed.xlsx`（阶段 B）。

- **请求体**：`multipart/form-data; file=<reviewed.xlsx>`。
- **浏览器侧预校验**（前端、上传前——见 §6.3）仅作辅助提示；**后端始终按 parser/SPEC.md §6.2 重新校验**。
- **副作用**：成功 → `archive.status` 从 `parsed` 推进到 `qa_passed`，并写入 `final_xlsx_key`；失败 → `parsed` 推进到 `failed_user`（体现为 `archive.status` 不变 + `parse.error_code=failed_user`）。
- **成功响应 200**：`ArchiveDetail`。
- **错误**：
  - `422 INVALID_XLSX_STRUCTURE`，body 含 `{ reason: string, sheet: string, column_index?: int }`，精确指出违反的校验规则（parser/SPEC.md §6.2 禁止：改名 Sheet1、改列数、改列序、加列）。

对照：parser/SPEC.md §5.2 `finalize_reviewed_xlsx`、§6.2 校验规则。

### 5.5 `GET /archives/{archive_id}/final.xlsx`

下载 `final.xlsx`。格式同 §5.3，attachment filename = `<archive.title>-final.xlsx`。

- **错误**：`404 FINAL_NOT_READY`（archive.status ∉ {qa_passed, usable}）。

### 5.6 `GET /archives/{archive_id}/manifest`

返回 parser 产出的 Manifest JSON 对象原样（parser/SPEC.md §6.2 schema `quota-parser-result/v1`）。

- **响应 200**：Manifest 对象（线协议见 §6.1）。
- **错误**：`404 MANIFEST_NOT_FOUND`（尚未跑过，或已被 GC）。

### 5.7 `GET /archives/{archive_id}/qa-report`

返回 QA 报告。默认 JSON 格式；`?format=md` 返回人类可读 markdown。

- **响应 200（JSON）**：parser/SPEC.md §12.2 `qa_report.json` 完整结构。
- **响应 200（md）**：`text/markdown; charset=utf-8`。
- **错误**：`404 QA_REPORT_NOT_FOUND`。

对照：parser/SPEC.md §12。

### 5.8 `POST /archives/{archive_id}/parse/cancel`（**v0.1 仅占位**）

取消正在进行的解析（阶段 A 中）。**v0.1 不实现**，端点存在但仅返回 `501 NOT_IMPLEMENTED`，让前端可以提前写好取消按钮（UI 始终显示、调用即被服务端拒），v0.2 替换为真实实现。

- **请求体**：无。
- **错误**：
  - `501 NOT_IMPLEMENTED`（v0.1 固定返回）
  - `404 ARCHIVE_NOT_FOUND`

> 实施顺序：v0.1 上线时前端按钮**始终可点**（按设计意图展现），点击后服务端返回 501，前端弹 toast「取消功能 v0.2 上线」并保持原状态；v0.2 改为真实取消：服务端把 `archive.status` 推到 `cancelled` 终态、清理 MinIO 半成品、释放 Worker 槽位。

---

## 6. 数据契约

### 6.1 `ArchiveDetail.parse` 子对象

追加到现有 `GET /archives/{id}` 响应。字段名与 Manifest（parser/SPEC.md §6.2）一一对应，前端无需做翻译。

```json
{
  "archive": { /* 不变 */ },
  "publication_set": { /* 不变 */ },
  "volumes": [ /* 不变 */ ],
  "files": [ /* 不变 */ ],
  "parse": {
    "profile": "sichuan",
    "task_id": "qp_20260727_xxxx",
    "phase": "stage_a",
    "status": "candidate_ready",
    "started_at": "2026-07-27T10:00:00Z",
    "finished_at": "2026-07-27T10:15:30Z",
    "parser_version": "0.2.0",
    "metrics": {
      "pages": 436,
      "ocr_seconds": 920,
      "candidate_rows": 12580
    },
    "warnings": ["OCR detected low-confidence page 87"],
    "error_code": null,
    "error_message": null,
    "candidate_xlsx_key": "quota/<archive_id>/candidate.xlsx",
    "final_xlsx_key": null,
    "manifest": {
      "$schema": "quota-parser-result/v1",
      "task_id": "qp_20260727_xxxx",
      "phase": "stage_a",
      "status": "candidate_ready",
      "parser_version": "0.2.0",
      "profile": "sichuan",
      "province": "sichuan",
      "ocr_api_url": "http://172.16.20.23:8000",
      "source_pdf_sha256": "...",
      "candidate_xlsx_sha256": "...",
      "artifacts": [
        { "kind": "ocr_markdown",    "path": "ocr/source.md" },
        { "kind": "ocr_result_json", "path": "ocr/result.json" },
        { "kind": "candidate_xlsx",  "path": "candidate/candidate.xlsx" },
        { "kind": "qa_report_json",  "path": "qa/qa_report.json" },
        { "kind": "qa_report_md",    "path": "qa/qa_report.md" }
      ],
      "metrics": { "pages": 436, "ocr_seconds": 920, "candidate_rows": 12580, "warnings": 3 },
      "warnings": ["OCR detected low-confidence page 87"]
    }
  }
}
```

`Archive` 表新增列（一个档案只保留最新一次运行——按你的决策，不存历史）：

| 列名 | 类型 | 对应 Manifest 字段 |
|---|---|---|
| `parse_profile` | `String(32)` | `manifest.profile` |
| `parse_task_id` | `String(64)` | `manifest.task_id` |
| `parse_phase` | `String(16)` | `manifest.phase`（`stage_a` / `stage_b`） |
| `parse_status` | `String(32)` | `manifest.status` |
| `parse_started_at` | `DateTime` | Worker 侧（Manifest 对象里没有） |
| `parse_finished_at` | `DateTime` | — |
| `parse_parser_version` | `String(32)` | `manifest.parser_version` |
| `parse_metrics` | `JSON` | `manifest.metrics` |
| `parse_warnings` | `JSON`（数组） | `manifest.warnings` |
| `parse_error_code` | `String(32)` | `transient` / `failed_permanent` / `failed_user` / null |
| `parse_error_message` | `Text` | — |
| `candidate_xlsx_key` | `String(512)` | `artifacts[kind=candidate_xlsx].path`（MinIO key） |
| `final_xlsx_key` | `String(512)` | — |

> `archive.status` 仍是**主驱动状态机**（`registered → parsing → parsed → qa_passed → usable`）。`parse_status` 是 **parser 侧的逻辑状态**，是对 `archive.status` 的进一步细化。

UI 状态映射表：

| `archive.status` | `parse.status` 显示 | UI 可用操作 |
|---|---|---|
| `registered` | `pending`（暂无数据） | 「开始解析」 |
| `parsing` | `running` | 转圈 + 「取消」（v0.2：真实现；v0.1：调 §5.8 → 501） |
| `parsed` | `candidate_ready` | 下载 / 上传 |
| `qa_passed` | `final_ready` | 下载 |
| `usable` | `final_ready` | 下载 + 绿色 badge |
| `cancelled`（v0.2 引入） | `cancelled` | 「重新解析」（终态，不再轮询） |
| `parsing` + `error_code=transient` | `running` | 黄色「重试中（N/M）」 |
| `parsing` + `error_code=failed_permanent` | `failed` | 红色 banner + 「重新解析」 |
| `parsed` + `error_code=failed_user` | `failed` | 红色 banner + 内联原因 |

`cancelled` 终态：服务端取消成功后 `archive.status` 落到此值，`MinIO` 半成品被清理；前端停止轮询、按「重新解析」回到 `registered` → `parsing` 流程。

Manifest 视图（§7.1 内「查看 Manifest」链接打开的弹窗）需展示三行头部元数据：

```
Profile:     sichuan
parser_version: 0.2.0      ← 来自 manifest.parser_version（必显示，用于排错）
Task ID:     qp_20260727_xxxx
```

对照：parser/SPEC.md §11（异常映射）。

### 6.2 XLSX 渲染契约（前端只读）

权威源：[parser/SPEC.md §6.1](../parser/SPEC.md)。前端渲染规则：

- **Sheet1「定额条目」**（必须第 1 个 sheet，10 列，无 header 行）：

  | 列 | 显示字段名 | 备注 |
  |---|---|---|
  | 0 | 类型 | 枚举 → 行类型配色（见下） |
  | 1 | 项目编码 | 等宽字体 |
  | 2 | 名称 | 文本 |
  | 3 | 项目特征 | 文本，允许换行 |
  | 4 | 计量单位 | 文本，窄列 |
  | 5 | 消耗量 | 右对齐，等宽 |
  | 6 | 基价/单价 | 右对齐，等宽 |
  | 7 | 验证 | 右对齐，等宽（QA 标志） |
  | 8 | 标准换算 | 文本 |
  | 9 | 标准换算来源 | 文本 |

  行类型配色（parser/SPEC.md §6.1 枚举）：
  - `段` / `定` → 加粗，浅黄底（章节 / 定额定义行）
  - `工` → 浅蓝（人工）
  - `料` → 浅绿（材料）
  - `配` → 浅橙（配比）
  - `机` → 浅紫（机械）
  - `综` → 加粗，浅灰（综合）
  - `主材` → 灰色（主材，无消耗量）

- **Sheet2「册说明」**：单格 A1，用 `<pre>` 块渲染。
- **Sheet3+ 名为 A / B / C / ...**：sheet 名作为标题 + 单格 A1 内容用 `<pre>` 块渲染。

### 6.3 Reviewed XLSX 预校验（浏览器侧）

浏览器端上传 `reviewed.xlsx` 前，用 SheetJS / JSZip 做轻量结构检查（只读结构，不读业务内容；openpyxl 是 Python 专用）。任一规则不通过则上传按钮保持禁用，并显示内联原因。

| 规则 | 失败原因 |
|---|---|
| 文件扩展名必须是 `.xlsx` | `INVALID_EXTENSION` |
| ZIP 中央目录必须含 `xl/workbook.xml` | `CORRUPT_XLSX` |
| `xl/workbook.xml` 必须声明名为「定额条目」的 sheet | `MISSING_SHEET_QUOTA_ENTRIES` |
| sheet「定额条目」必须位于 sheet 索引 0 | `SHEET_ORDER_WRONG`（必须第一个 sheet） |
| sheet「定额条目」必须恰好 10 列（从 dimension / row 1 cell 数判断） | `COLUMN_COUNT_WRONG` |

> 此校验**仅作提示**。后端按 parser/SPEC.md §6.2 用 openpyxl 重新校验——后端返回的 `INVALID_XLSX_STRUCTURE`（映射到 parser/SPEC.md §11 的 `failed_user`）才是权威。

---

## 7. UI 行为

### 7.1 档案详情 — 解析区块

在现有 `renderArchiveDetailView` 内追加。渲染规则：

```
[解析区块]
  - archive.status == registered：
      大按钮「开始解析」（调 §5.1）
  - archive.status == parsing：
      转圈 + 「Worker 正在解析…」+ 「取消」（调 §5.8；v0.1：服务端返回 501，前端弹 toast「取消功能 v0.2 上线」并保持原状态）
      若 parse.error_code == transient：黄色 banner「重试中（N/M）」
  - archive.status == parsed：
      两个按钮：「下载 candidate」 | 「上传 reviewed」
      内联摘要卡（页数、candidate_rows、警告数）
      「查看 Manifest」链接（§5.6，新页签或弹窗；头部展示 Profile / parser_version / Task ID）
      「查看 QA 报告」链接（§5.7）
  - archive.status == qa_passed：
      一个按钮：「下载 final」
      内联 QA 摘要（parser status: ok / warning / failed）
      警告列表（如有）
      「查看 Manifest」/「查看 QA 报告」链接
  - archive.status == usable：
      同 qa_passed + 绿色「Usable」徽章
  - archive.status == cancelled（v0.2）：
      灰色 banner「已取消」+「重新解析」按钮（回到 §5.1）
  - 任何状态若 parse.error_code != null：
      区块顶部红色 banner，显示 parse.error_message
```

若 `parse.status` 为 `pending` 且 `archive.status == registered`（初始态）——区块隐藏「开始解析」按钮之外的所有内容，按钮始终保留。

### 7.2 解析任务 tab

清单定额档案台内的新顶层 tab。列出所有 `archive.status ∈ {parsing, parsed, qa_passed, usable}` 的档案。

列：

| 列 | 数据源 |
|---|---|
| 档案标题 | `archive.title` |
| 解析状态 | §6.1 状态映射表对应的徽章 |
| Profile | `parse.profile` |
| 开始时间 | `parse.started_at` |
| 完成时间 | `parse.finished_at`（运行中显示「—」） |
| 行数 | `parse.metrics.candidate_rows` |
| 警告 | `parse.warnings.length`（>0 显示红色徽章） |
| 行动 | 「查看」（跳转到档案详情） |

筛选：状态、Profile、时间区间。默认排序：`started_at desc`。

能力开关：复用 `quota-api.js` 现有 `archives` 能力标志，**不引入新标志**。

### 7.3 Reviewed XLSX 上传交互

1. 用户点「上传 reviewed」。
2. 弹出模态框（单个 `<input type="file">` + 拖拽区 + 格式提示「请下载 candidate 后用 Excel 编辑，保留 Sheet1「定额条目」结构」）。
3. 选完文件后：
   - 跑 §6.3 浏览器侧预校验。
   - 不通过：红色内联错误，上传按钮禁用。
   - 通过：绿色对勾，上传按钮启用。
4. 用户点「上传」。前端调 §5.4。
5. 成功：模态框关闭，档案详情重渲染，`archive.status = qa_passed`。
6. 收到 `422 INVALID_XLSX_STRUCTURE`：模态框**保持打开**，且按下表重置内部控件状态——用户可改完文件再上传，不必关掉重来：

| 控件 | 后端 422 后状态 |
|---|---|
| 文件选择器 / 拖拽区 | **重置为空**（`input.value = ""`、清掉拖拽高亮、移除文件名显示） |
| 「上传」按钮 | **保持禁用**（无文件时 §6.3 预校验无文件可跑，按钮天然禁用） |
| 内联原因区域（红色） | 显示后端 `reason` 文本（中文，parser 后端产出）+ `sheet` / `column_index` 等结构化字段（如有） |
| 「取消」/关闭按钮（`×`） | **保持可点**——用户随时可关闭模态框退出 |

> 设计意图：422 通常是结构性问题（Sheet 改名、列数错），用户改完想再传；若强制关闭会丢失后端反馈，重开又看不到刚才那条 reason。

---

## 8. Mock 策略（Worker 未上线前的开发模式）

由环境变量 `QUOTA_PARSE_MOCK=1` 控制（`quota_api.py` 与 `quota-parse-mock.js` 都读取）。开启后：

- **POST /parse**：端点**必须声明为 `async def`**（不是 `def`），同步把 archive 推到 `parsing`；用 `asyncio.create_task(...)` 启后台任务跑 `run_mock_pipeline(archive_id)`：sleep 2–10 s 后推到 `parsed`，把 §6.1 字段填好（fake 值），往 MinIO 写一个 10 行假的 `candidate.xlsx`，路径 `quota/<archive_id>/candidate.xlsx`。
- **POST /reviewed**：mock 模式下跳过 openpyxl 校验，任何 `.xlsx` 都接受，sleep 2–3 s 后推到 `qa_passed`。
- **GET /candidate.xlsx** / **GET /final.xlsx**：返回 fixture。
- **GET /manifest** / **GET /qa-report**：返回静态 fixture，结构**完全符合** parser/SPEC.md §6.2 / §12.2——确保 Worker 真实上线后前端代码无需改动。

**硬约束（v0.1 必须满足，否则 mock 模式会在第一波前端联调就翻车）**：

1. **端点必须 `async def`**：`POST /parse` 必须用 `async def`，把耗时操作放进 `asyncio.create_task` 后台跑，立即返回 200。若端点写成同步 `def`，FastAPI 会把请求丢到线程池阻塞，主线程会被 mock 的 `sleep` 占住，前端第二轮轮询会卡死。
2. **后台任务必须持有独立的 DB session**：FastAPI 的 `Depends(get_db_session)` 默认是请求作用域（`scope="function"`），请求返回后 session 关闭；后台任务若继续用同一个 session 引用，写库时会抛 `DetachedInstanceError` / `Session is closed`。`run_mock_pipeline` 必须自己 `SessionLocal()` 拿新 session 写 `parse_*` 列与状态推进；mock runner 不持有原请求的 session 引用。
3. **MinIO 写也要在后台**：写 `candidate.xlsx` 的 I/O 与 `sleep` 一起在后台 task 里跑，不阻塞端点返回。
4. **`asyncio.create_task` 的引用必须保留**：否则事件循环可能在请求返回前就 GC 掉 task，导致后台跑到一半被吞。实现里把 task 引用存到模块级 `set()` 里，等后台 task 完成后回调 `discard`。

`mock_parse_runner.py` 提供单个函数 `run_mock_pipeline(archive_id)`（内部自己 `SessionLocal()`）。`quota_api.py` 的 mock 分支 `async def` 调用 `asyncio.create_task(run_mock_pipeline(...))`，替代真正的 Worker 入口。

Worker 真正上线时，只需移除 `if QUOTA_PARSE_MOCK: ...` 分支并指向 `quota_parser.worker.serve_worker`——前端不动。

---

## 9. 与现有 UI 的集成

### 9.1 `quota-ui.js`

- `TABS` 数组（当前 `archives / versionSystem / coverage / pending`）追加第 5 项：`{ view: "parse", label: "解析任务", icon: "play" }`，插入在 `archives` 与 `versionSystem` 之间。
- `TAB_CAPABILITY` 追加 `"parse": "archives"`（复用现有能力标志，不新增）。
- `renderBody()` 增加 `case "parse"`，委托给 `QuotaParse.renderParseRunsView()`。
- `renderArchiveDetailView()` 末尾追加 §7.1 解析区块，调用 `QuotaParse.renderParseSection(detail.data.parse)`。

### 9.2 `index.html`

在现有 `quota-ui.js` 之后追加三个 `<script>`：

```html
<script defer src="/ui-assets/quota-parse-mock.js?v=..."></script>
<script defer src="/ui-assets/quota-parse-api.js?v=..."></script>
<script defer src="/ui-assets/quota-parse.js?v=..."></script>
```

### 9.3 能力开关

**不引入新的能力标志**。「解析任务」tab 复用现有 `archives` 能力——若 `archives` 是 `unavailable`，parse 也不可用。保持 v0.1 标志体系稳定。

---

## 10. 验收标准

- [ ] Mock 模式（`QUOTA_PARSE_MOCK=1`）下：
  - 通过现有「新增档案」弹窗上传一份 PDF。
  - 进入档案详情，解析区块显示「开始解析」。
  - 点击；状态推到 `parsing`；10 s 内推到 `parsed`。
  - 点「下载 candidate」，用 Excel 打开：Sheet1 = 「定额条目」、Sheet2 = 「册说明」、（可选）一个章节 sheet。
  - 编辑一行，保存，点「上传 reviewed」；状态推到 `qa_passed`。
  - 点「下载 final」，文件能正常打开。
- [ ] 故意破坏 reviewed.xlsx（把 Sheet1 改名 `Foo`）：浏览器侧预校验必须拒绝，**且**后端返回 `422 INVALID_XLSX_STRUCTURE`，reason 字符串与 parser/SPEC.md §6.2 校验规则一一对应。
- [ ] 「解析任务」tab 列出该档案，状态徽章与 metrics 正确。
- [ ] 后端报告 `parse.error_code = failed_user` 时，红色 banner 原样显示后端 `parse.error_message` 文本。
- [ ] `QUOTA_PARSE_MOCK=0`（真实 Worker 模式）下，同样的 UI 跑通无改动。（跨实现验收：同一前端对接 parser/SPEC.md §5.1 / §5.2 端点即可。）

---

## 11. 跨文档核对清单：parser/SPEC.md v0.2 ↔ 本 SPEC v0.1

| parser/SPEC.md 章节 | 本 SPEC | 状态 |
|---|---|---|
| §2 术语表 | §2 | ✅ 对齐 |
| §5.1 `run_quota_pipeline` | §5.1 `POST /parse` | ✅ 对齐 |
| §5.2 `finalize_reviewed_xlsx` | §5.4 `POST /reviewed` | ✅ 对齐 |
| §6.1 XLSX schema | §6.2 渲染契约 | ✅ 对齐 |
| §6.2 Manifest schema | §6.1 `parse.manifest` | ✅ 对齐（字段名原样） |
| §6.2 reviewed XLSX 校验 | §6.3 浏览器预校验 + §5.4 后端再校验 | ✅ 对齐 |
| §6.3 SHA-256 | 不暴露给前端（后端计算，通过 `final_xlsx_key` 间接引用） | ✅ 设计上对齐 |
| §7 Province Profile | §5.1 请求体 `profile` 参数 + §6.1 `parse.profile` | ✅ 对齐 |
| §11 异常分类 | §7.1 banner + §6.1 `parse.error_code` 映射 | ✅ 对齐 |
| §12 QA 报告 | §5.7 `GET /qa-report` | ✅ 对齐 |

---

## 12. 待 parser 维护者确认的开放问题

1. **MinIO bucket/key 布局** — 当前假设 `quota/<archive_id>/{candidate,final}.xlsx`。请与运维确认此前缀不与现有 `data-source` / `crawler` bucket 冲突。
2. **`parse_task_id` 唯一性** — parser 生成 `qp_<YYYYMMDD>_<run>`，DB 列没有唯一约束。要不要加？当前立场：不要，因为 `archive_id ↔ parse run` 是 1:1（按你的决策）。
3. **QA 报告保留** — parser 是同时保留 `qa_report.json` 与 `qa_report.md`，还是只保留一个？本 SPEC 假设两者都保留，§5.7 用 `?format=md` 切换。
4. **Manifest 保留期** — 假设与档案同生命周期；确认没有 GC 策略会丢弃它。

---

## 13. 版本

- **v0.1**（本文档）：初稿，待与 `parser/SPEC.md` v0.2 核对。包含：7 个核心端点（§5.1–§5.7）+ 1 个占位端点 `POST /parse/cancel`（§5.8，固定 501）、复用 `archive.status` 状态机、Mock 模式（§8，含 4 条硬约束）。
- **v0.2**（未来）：
  1. 实现 §5.8 真实取消（`cancelled` 终态落地、MinIO 半成品清理、Worker 槽位释放）。
  2. 加 PDF 页级 OCR 置信度视图（Manifest `metrics` 扩字段，对接后端新增的 `page_confidence[]`）。
  3. 「解析任务」tab 加批量操作（批量取消、批量下载）。