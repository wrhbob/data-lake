# quota-parser 配套网站前端 SPEC（v0.3）

> 配套文档：[`quota/parser/SPEC.md`](../parser/SPEC.md)（v0.2，Implementation Locked）。
> 本 SPEC 描述**网站侧**：UI 行为、前端消费的 HTTP API 契约、以及 Worker 尚未上线前的 Mock 策略。
>
> 跨文档核对：见 §11，逐字段对照 `quota/parser/SPEC.md`。两份 SPEC 必须一起审阅；后端 SPEC 是后端数据形态的权威源，本 SPEC 是 UI 行为与 HTTP API 暴露面的权威源。

---

## 0. 文档元信息

- **状态**：Draft v0.3（v0.2 基础上砍 cancelled 与 #2 按钮；5 状态收敛；新增 §7.4 档案列表操作列集成解析入口）
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

### 3.1 按钮清单（跨状态参考表）

> 来源：UI 设计对话（2026-07-28）。本节是 §7.1「按状态渲染」的速查母表；§7.1 给具体渲染细节，本节给"全按钮 × 全状态"总览。两节须一起审阅。

#### 3.1.1 主操作流（9 个按钮，按动线顺序；v0.3 移除 #2 取消）

> **v0.3 决策**：删除原 #2「取消解析」按钮与 §5.8 端点。设计意图：开始解析后不允许停止——若需中止，按"重新解析"走 §5.1，Worker 会清理 MinIO 上旧的 candidate / reviewed / final（避免半成品状态污染 MinIO 与 Worker 调度打架）。

| # | 按钮 | 触发端点 | 用户场景 | 备注 |
|---|---|---|---|---|
| 1 | **开始解析** | `POST /api/data-lake/quota/archives/{archive_id}/parse` | 档案详情页对已登记 PDF 发起第一轮解析 | registered 状态可见；与「重新解析」**共用同一端点**（§3.1.3） |
| 2 | **下载 PDF 原件** | `GET /api/data-lake/archives/{id}/files/{file_id}`（**复用现有端点**） | 任何时候要拿原件查看 | 任何状态可点；档案列表的「预览」图标复用此语义 |
| 3 | **下载 candidate XLSX** | `GET /api/data-lake/quota/archives/{archive_id}/candidate.xlsx` | parsed 后下载到本地用 Excel 改 | 待审核 及之后 |
| 4 | **上传 reviewed XLSX** | `POST /api/data-lake/quota/archives/{archive_id}/reviewed` | parsed 后把改好的 XLSX 传回去 | 仅待审核 / 解析失败；模态框交互见 §7.3 |
| 5 | **下载 final XLSX** | `GET /api/data-lake/quota/archives/{archive_id}/final.xlsx` | 已完成后拿最终结果 | 已完成 状态可见 |
| 6 | **查看 Manifest** | `GET /api/data-lake/quota/archives/{archive_id}/manifest` | 排错 / 核对 profile + parser_version | 待审核 及之后；弹窗头部强提示 `parser_version` |
| 7 | **查看 QA 报告** | `GET /api/data-lake/quota/archives/{archive_id}/qa-report` | 看阶段 A / B 自动 QA 输出 | 已完成 及之后（及解析失败） |
| 8 | **重新解析** | **复用** `POST /api/data-lake/quota/archives/{archive_id}/parse`（同 #1） | 用新 profile 或重跑 | 待审核 / 已完成 / 解析失败 任意状态；UI 层按 `archive.status` 切换 label/icon |
| 9 | **删除解析结果** | `POST /api/data-lake/quota/archives/{archive_id}/parse/delete`（**待确认**——见 §12） | 清掉 MinIO 产物 + Archive `parse_*` 字段 | 待审核 及之后任意状态；危险操作须二次确认 |

#### 3.1.2 状态 × 按钮可见性矩阵（5 状态 × 9 按钮）

> **v0.3 决策**：状态从 8 档收敛到 **5 档**（`cancelled` 删除；`failed_user` + `failed_permanent` 合并为"解析失败"；`qa_passed` + `usable` 合并为"已完成"）。

| UI 状态 | `archive.status` 覆盖 | #1<br>开始 | #2<br>下载 PDF | #3<br>下载 candidate | #4<br>上传 reviewed | #5<br>下载 final | #6<br>看 Manifest | #7<br>看 QA | #8<br>重新解析 | #9<br>删除结果 |
|---|---|---|---|---|---|---|---|---|---|---|
| **未解析** | `registered` | ✅ | ✅ | — | — | — | — | — | — | — |
| **解析中** | `parsing` + `transient` | — | ✅ | — | — | — | — | — | — | — |
| **待审核** | `parsed` | — | ✅ | ✅ | ✅ | — | ✅ | — | ✅ | ✅ |
| **已完成** | `qa_passed` + `usable` | — | ✅ | ✅ | — | ✅ | ✅ | ✅ | ✅ | ✅ |
| **解析失败** | `failed_user` + `failed_permanent` | — | ✅ | ✅ | ✅ 重做 | — | ✅ | ✅ | ✅ 换 profile | ✅ |

> `transient` 复用「解析中」渲染（黄色 banner「重试中（N/M）」叠加在状态徽章上），不单列。

#### 3.1.3 共用端点与 UI label 切换

- **#1 与 #8 共用同一后端端点** `POST /archives/{archive_id}/parse`：UI 层根据 `archive.status` 切换 button label 与 icon——`registered → 「开始解析」`，`parsed/qa_passed/usable/failed_* → 「重新解析」`。后端契约干净，UI 决策点单一。
- **后端语义差异**：第一次调用 `archive.status = registered → parsing`；后续调用从任何 `{parsed, qa_passed, usable, failed_*}` 回到 `parsing`，**Worker 会清理 MinIO 上旧的 candidate / reviewed / final**（避免多版本残留）。

#### 3.1.4 危险操作隔离

按钮 #10「删除解析结果」必须满足：

1. **二次确认 modal**：必填「输入档案标题确认」防误删。
2. **位置不与「重新解析」相邻**：CLAUDE.md 入湖纪律已强调两类删除要分开（删除解析结果 ≠ 删除原始档案）；视觉上保持至少 1 个普通按钮的间距。
3. **明确语义区分**：modal 标题必须是「删除解析结果」（不是「删除档案」或「删除 XLSX」），避免与原始档案的删除混淆。
4. **审计**：调用时记录到 `audit_log`（`action='quota_parse_result_deleted'`, `target_id=archive_id`）。

#### 3.1.5 UI 必要展示元素（非按钮，但与按钮同处一区块）

| 元素 | 数据源 | 何时显示 |
|---|---|---|
| Profile 名（`sichuan` / `chongqing`） | `parse.profile` | parsed 及之后 |
| `parser_version` | `parse.parser_version` | parsed 及之后，**强提示**（README §7 "排错用"） |
| Task ID | `parse.task_id` | parsing 起任何状态 |
| 状态进度条 + 文字 | `archive.status` + `parse.status` | 始终 |
| 异常 banner（3 类） | `parse.error_code` | `transient` / `failed_permanent` / `failed_user` |
| 行数差异 warning | `qa_report.json` | qa_passed 时若 reviewed 行数差过大 |
| 处理耗时 | `parse.started_at` / `finished_at` | 任意终态 |

---

## 3.2 主界面按钮视觉规范

> **目的**：把 §3.1 按钮清单（功能定义）落到具体视觉——每个按钮用什么 lucide 图标、什么文案、什么颜色、什么形态、放在哪、按什么顺序。实现者不再靠临场判断。
>
> **本节是 §3.1 与 §7 UI 行为之间的视觉契约层**。三节一起审阅；冲突时本节优先。

### 3.2.1 设计总则

| 维度 | 规范 | 实现位置 |
|---|---|---|
| 图标库 | [lucide](https://lucide.dev) 静态资源（已通过 `<i data-lucide="...">` 接入） | 全局 |
| 颜色 | 主色蓝 `--color-primary`、次色灰 `--color-bg-soft`、危险红 `--color-danger`、警告橙 `--color-warning`、成功绿 `--color-success`、中性文字 `--color-text-muted` | styles.css CSS 变量 |
| 形态 | 4 种：`icon-only`（方/圆 32×32，纯图标 hover 出 tooltip）、`icon-text`（lucide + 文字行内，详情/补录用）、`pill`（胶囊徽章，状态/标签用）、`text-link`（纯文字 + hover 下划线，用于空态 CTA） | styles.css |
| 圆角 | 统一 6 px（按钮）/ 9999 px（徽章/筛选 chip） | styles.css |
| tooltip | `title` 属性直接给中文短语；解析中/异常类另加 `aria-live="polite"` 提示 | JSX 直接写在按钮上 |
| 键盘 | Tab 聚焦 + Enter/Space 触发；ESC 关闭 modal；箭头键在 ⋯ 下拉内移动焦点（v0.3.2 计划） | 全局 |
| 危险操作 | §3.1.4：modal 标题必须是"删除解析结果"——**不是**"删除档案"或"删除 XLSX" | W5 |

### 3.2.2 现有按钮清单（按区域归类）

> 已存在于 `quota-ui.js` 与 `styles.css`；本节为新按钮建立参照模板。

#### A. Header 区（`renderHeader()`）

| 按钮 | 形态 | 图标 | 文字 | class | 颜色 | tooltip |
|---|---|---|---|---|---|---|
| 搜索框 | input+leading icon | `search` | placeholder="搜索资料体系、分册、标准/定额编号" | `.search-box` | 中性 | — |
| 刷新 | icon-only | `rotate-ccw` | — | `.icon-button` + `data-quota-action="refresh"` | 中性 | "刷新" |
| 新增档案 | icon-text | `plus` | "新增档案" | `.primary-button` | 主色蓝 | — |
| 新增档案弹出的下拉项 | text-only | — | "新增清单规范" / "新建定额体系" / "向已有体系新增分册" / "向已有档案补充文件" | `.quota-add-item` | 中性文字 | — |
| 开发调试 pill | text-only | — | "Archive API / domain_type: quota" | `.quota-debug-pill` | dev only，production 隐藏 | "调试信息，仅开发环境显示" |

#### B. 统计条（`renderStatsBar()`）

5 个 `.quota-stat`：原文 / 可访问 / 待归档 / 重复 / 异常；数值用 `statVal()`（未知显示 "—"）；小类名 `rawTotal / accessible / pendingRaw / duplicate / invalid`。

#### C. Tabs 区（`renderTabs()`）

| 按钮 | 图标 | 文字 | 角标条件 | 禁用态原因 |
|---|---|---|---|---|
| 档案列表 | `list` | "档案列表" | — | — |
| 版本体系 | `layers` | "版本体系" | — | `coverage` capability 不可用时 disabled + title 解释 |
| 覆盖矩阵 | `table-2` | "覆盖矩阵" | — | 同上 |
| 待归档 | `inbox` | "待归档" | `pending > 0` 时显示 `.quota-tab-badge` | `reconciliation` capability 不可用时 disabled |

#### D. 筛选区（`renderFilters()`）

| 控件 | 形态 | class | 说明 |
|---|---|---|---|
| 一级 chip | pill | `.filter-chip` | 4 个 primary；点击切换 |
| 二级 chip（地区/行业/适用范围） | pill | `.filter-chip` | "全部" + 各省/市 + 「展开更多」按钮 |
| 年份 chip | pill | `.filter-chip` | "全部" + 6 年倒序 |
| 版次 chip | pill | `.filter-chip` | 仅 ≥ 2 个版次时显示 |
| 高级筛选 toggle | pill | `.filter-chip.filter-toggle` | "高级筛选" → "收起高级筛选" |
| 高级筛选字段 | pill x 8 | `.filter-chip` | 8 维：分册专业/资料性质/地市/发布单位/元数据状态/档案状态/入湖通道/文件格式 |

#### E. 档案列表行（`renderArchiveRow()` · v0.3.1 现状）

每行 `<td>` 列结构 = `档案标题 | 资料分类 | 文件数 | 状态 | 操作`：

| 列 | 内容 | class | 备注 |
|---|---|---|---|
| 档案标题 | `archive.title` | `.quota-col-title` | 点击整行触发 `preview-archive` |
| 资料分类 | `CATEGORY_LABELS[meta.category.value]` | — | "建筑工程定额" / "专业工程定额" / "清单规范" / "—" |
| 文件数 | `archive.file_count` | `.quota-col-count` | "—" 当 null |
| 状态 | `archive.status` 直接展示 | `.quota-status-pill` | **v0.3.2 替换为 `.status-badge--xxx`**（批 2 W3） |
| 操作 | 预览按钮 | `.quota-preview-btn` = `<i data-lucide="eye">` + `<span>预览</span>` | **v0.3.2 替换为 2 智能图标 + ⋯ 下拉**（批 2 W3/W4） |

#### F. 补录 / 上传 modal（`renderComposeModal()` & `renderUploadModal()`）

| 控件 | 形态 | 图标 | 文字 | class | 颜色 |
|---|---|---|---|---|---|
| 关闭 × | icon-only | `x` | — | `.icon-button` | 中性 |
| 取消 | text-button | — | "取消" | `.secondary-button` | 灰 |
| 保存草稿 | text-button | — | 由 `Compose.resolveComposeActions()` 决定 label | `.secondary-button` | 灰 |
| 提交 | icon-text | `check` / spinner | "保存并提交核验" | `.primary-button` | 主色蓝（提交中变 spinner） |
| 上传 PDF | upload-zone | `upload-cloud` | "选择 PDF 文件" | `.file-picker` | 蓝边虚线框 |
| 上传并保存 | icon-text | `upload-cloud` | "上传并保存" | `.primary-button` | 主色蓝 |
| 删除分册 / 文件 | icon-only | `trash-2` / `x` | — | `.icon-button.danger-action` | 危险红 hover |

#### G. 档案详情 viewer（`renderArchiveDetailView()` · v0.3 现状）

| 控件 | 形态 | 说明 |
|---|---|---|
| 返回档案列表 | icon-text | `<i arrow-left>` + "返回档案列表"，`.secondary-button` |
| 状态 pill | pill | `.quota-status-pill`，v0.3.2 替换为 `.status-badge--xxx` |
| 主文件标记 | pill | `.quota-status-pill` 含 "主文件" |
| 当前分册标记 | pill | `.quota-status-pill` 含 "当前" |

### 3.2.3 批 2 计划按钮（v0.3.2 操作列 · W3/W4）

> 占位规范——批 2 实施时按此落地。如有偏差需先回 SPEC 修此节，再写代码。

#### H. 状态徽章（`.status-badge` · 5 个变体）

| UI 徽章 | archive.status 覆盖 | 图标 | 文字 | class | 颜色 |
|---|---|---|---|---|---|
| **未解析** | `registered` | — | "未解析" | `.status-badge--pending` | 灰 |
| **解析中** | `parsing` + `transient` | `loader` / 旋转 spinner | "解析中" | `.status-badge--parsing` | 蓝 |
| **待审核** | `parsed` | — | "待审核" | `.status-badge--review` | 橙 |
| **已完成** | `qa_passed` + `usable` | `check` | "已完成" | `.status-badge--done` | 绿 |
| **解析失败** | `failed_user` + `failed_permanent` | `alert-triangle` | "解析失败" | `.status-badge--failed` | 红 |

排版：`<span class="status-badge status-badge--xxx"><i data-lucide="..."></i><span>...</span></span>`。`usable` 状态额外显示绿色 `.status-badge--done` 小标签（spec §7.4.1）。

#### I. 操作列智能图标（`.quota-action-cell` · 每行 2 个）

| UI 状态 | 图标 1 | 图标 2 |
|---|---|---|
| 未解析 | `eye` 👁 预览（icon-only，tooltip "预览 PDF"） | `play` ▶ 开始解析（icon-only，tooltip "开始解析 / 重新解析时切 label"） |
| 解析中 | `eye` 👁 预览 | `loader-circle` ⏳ 占位（icon-only，disabled，永远转圈，无 tooltip） |
| 待审核 | `download` ⬇ 下载 candidate（icon-only，tooltip "下载 candidate.xlsx"） | `upload` ⬆ 上传 reviewed（icon-only，tooltip "上传 reviewed.xlsx"） |
| 已完成 | `download` ⬇ 下载 final（icon-only，tooltip "下载 final.xlsx"） | `eye` 👁 预览 |
| 解析失败 | `refresh-cw` ↻ 重新解析（icon-only，tooltip "重新解析"） | `eye` 👁 预览 |

按钮 class：`.quota-action-cell__icon`；hover 颜色随状态变化（解析类蓝、危险类红）。disabled 态 `opacity: 0.4 + cursor: not-allowed`。

#### J. ⋯ 下拉（`.quota-dropdown` · W4）

| 元素 | 形态 | 颜色 |
|---|---|---|
| 触发 ⋯ | icon-only | lucide `more-horizontal`，`.quota-action-cell__more`，悬停染主色蓝 |
| 普通项 | text+leading icon | 例如 `download` "下载 candidate.xlsx" / `upload` "上传 reviewed.xlsx" / `refresh-cw` "重新解析" / `file-text` "查看 Manifest" / `check-circle` "查看 QA 报告"；`.quota-dropdown__item`，hover 浅灰背景 |
| 分隔条 | — | `.quota-dropdown__divider`，1 px 高 + 上下内边距 4 px |
| 危险项 | text+leading icon `trash-2` "删除解析结果" | `.quota-dropdown__item--danger`，文字红 + hover 红背景 |

定位：`position: absolute; top: 100%; left: 0; min-width: 200 px;`；浮在 ⋯ 按钮下方左对齐；阴影 `box-shadow: 0 4px 12px rgba(0,0,0,.12)`。

#### K. 删除二次确认 modal（`.quota-delete-confirm-modal` · W5）

| 元素 | 规范 |
|---|---|
| modal 标题 | 固定 "删除解析结果"（§3.1.4） |
| 副标题 | "此操作将删除 MinIO 上的 candidate / reviewed / final 三件套与 Archive `parse_*` 字段；原始 PDF 不受影响。" |
| 档案标题展示 | `<p class="quota-delete-archive-title">[archive.title]</p>` 文字需让用户对照 |
| 输入框 | `<input type="text" placeholder="请输入档案标题确认">`，onChange 后判断输入与 `archive.title === ?` 才解锁确认按钮 |
| 取消 | `.secondary-button`，文字"取消"，始终 enabled |
| 关闭 × | `.icon-button`，lucide `x`，始终 enabled |
| 确认 | `.primary-button`，文字"删除解析结果"，disabled until `input === archive.title`；提交后变 spinner + 文字"删除中..." |

modal 复用 `#quotaUploadModal` 的样式与 DOM 结构（避免重复实现）；仅 innerHTML 替换为 K 段。

### 3.2.4 视觉细节参考（实现时遵循）

```css
/* 状态徽章: 5 变体 share base .status-badge */
.status-badge              { display:inline-flex; align-items:center; gap:4px;
                              padding:2px 10px; border-radius:9999px;
                              font-size:12px; line-height:20px; font-weight:500; }
.status-badge--pending     { background:#f1f5f9; color:#475569; }
.status-badge--parsing     { background:#dbeafe; color:#1d4ed8; }
.status-badge--review      { background:#fef3c7; color:#b45309; }
.status-badge--done        { background:#dcfce7; color:#15803d; }
.status-badge--failed      { background:#fee2e2; color:#b91c1c; }

/* 智能图标 + ⋯ 下拉容器 */
.quota-action-cell         { display:inline-flex; gap:4px; align-items:center; }
.quota-action-cell__icon   { width:32px; height:32px; display:inline-flex;
                              align-items:center; justify-content:center;
                              border-radius:6px; cursor:pointer; }
.quota-action-cell__icon:hover { background:var(--color-bg-soft); }
.quota-action-cell__icon:disabled { opacity:.4; cursor:not-allowed; }
.quota-action-cell__more   { /* 同 icon 样式 */ }
```

> 完整 CSS 在批 2 W1 实现时统一落地到 `styles.css`；本节只规定 class 名 + 颜色变量。

### 3.2.5 与现有 SPEC 章节的对应关系

| 本节子节 | 对应章节 |
|---|---|
| §3.2.2 A–G（已有按钮） | §7 UI 行为 → 实现时按本节视觉规范 |
| §3.2.3 H 状态徽章 | §7.4.1（本节细化图标/颜色） |
| §3.2.3 I 智能图标 | §7.4.2（本节细化每个 icon 名 + tooltip） |
| §3.2.3 J ⋯ 下拉 | §7.4.2 + §3.1.4（本节细化 class / 颜色 / 位置） |
| §3.2.3 K 删除 modal | §7.4.3 + §3.1.4（本节细化为完整表单） |

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

### 4.1 前端代码放在 `file_asset_service/app/ui/` 的理由（v0.2 决策）

3 个新前端模块（`quota-parse.js` / `quota-parse-api.js` / `quota-parse-mock.js`）**不**放到 `quota/web-frontend/src/`，而是与现有 `quota-ui.js` / `quota-compose.js` / `quota-api.js` 并列放进 `file_asset_service/app/ui/`：

1. **零迁移成本**：现有 FastAPI 主程序 `main.py` L177 已把 `file_asset_service/app/ui/` 整体挂载到 `/ui-assets`（`app.mount("/ui-assets", StaticFiles(directory=ui_dir))`）；新模块放同目录，`index.html` 加 3 行 `<script>` 即可。
2. **与现有惯例一致**：3 个现有 quota-* 模块都在这里；新加文件顺着命名空间排最自然。
3. **避免主程序变更**：`main.py` L177 的 StaticFiles 挂载是单一信任锚；为 3 个新文件扩展挂载点收益不抵改动。
4. **未来可平滑迁出**：若要把"quota 全部前端"自治到 `quota/web-frontend/src/`（与 `quota/parser/` 对称），届时把 6 个 quota-* 文件（含 3 个老的）一并迁出，扩展 `main.py` 加一条 `app.mount("/quota-assets", ...)` 即可——v0.2 不做这次重构。

**SPEC 文档**仍保留在 `quota/web-frontend/SPEC.md`（与 `quota/parser/SPEC.md` 平行），只搬代码，不搬文档。

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

> **v0.3 决策**：删除原 §5.8 取消解析端点。设计意图：开始解析后不允许停止（避免半成品状态污染 MinIO、避免与 Worker 调度逻辑打架）。如需中止，按"重新解析"走 §5.1——Worker 会清理 MinIO 上旧的 candidate / reviewed / final（见 §3.1.3）。

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
| `parsing` | `running` | 转圈 + 状态徽章（无取消按钮；详见 §3.1.1 v0.3 决策） |
| `parsed` | `candidate_ready` | 下载 / 上传 |
| `qa_passed` | `final_ready` | 下载 |
| `usable` | `final_ready` | 下载 + 绿色 badge |
| `parsing` + `error_code=transient` | `running` | 黄色「重试中（N/M）」 |
| `parsing` + `error_code=failed_permanent` | `failed` | 红色 banner + 「重新解析」 |
| `parsed` + `error_code=failed_user` | `failed` | 红色 banner + 内联原因 |

> **v0.3**：取消 `cancelled` 终态（详见 §3.1.1 决策）。异常落点收口到 `failed_permanent` / `failed_user` / `transient` 三类。

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
      转圈 + 「Worker 正在解析…」
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
  - 任何状态若 parse.error_code != null：
      区块顶部红色 banner，显示 parse.error_message
```

> **v0.3**：删除 `cancelled` 分支（开始解析后不允许停止，详见 §3.1.1）。

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

### 7.4 档案列表 — 操作列集成解析入口（v0.3 新增）

清单定额档案台的档案列表，每行操作列直接承载解析相关快捷操作——用户不必进入档案详情即可触发解析、上传 reviewed、下载 final、查看 Manifest/QA、删除结果。配合 §7.1 档案详情"解析区块"形成"行级快入口 + 详情全功能"双层入口。

#### 7.4.1 状态徽章（5 状态枚举）

在档案列表的「状态」列显示。状态值由 `archive.status` + `parse.error_code` 推导：

| UI 徽章 | `archive.status` 覆盖 | 颜色 | 备注 |
|---|---|---|---|
| **未解析** | `registered` | 灰色 | 初始态 |
| **解析中** | `parsing` + `transient`（`parse.error_code='transient'`） | 蓝色，配转圈图标 | 转圈 + 文字"解析中" |
| **待审核** | `parsed` | 橙色 | 文字"待审核" |
| **已完成** | `qa_passed` + `usable` | 绿色 | `usable` 状态额外显示绿色"Usable"小徽章 |
| **解析失败** | `failed_user` + `failed_permanent` | 红色 | 红色 banner 不显示在列表（避免污染行视觉），点档案详情看原因 |

> 徽章用现有 `.status-badge` 类（index.html L244）；新增 5 个状态样式（`.status-badge--pending / --parsing / --review / --done / --failed`）。

#### 7.4.2 操作列布局（智能图标 + ⋯ 下拉）

每行渲染 **2 个图标按钮 + 1 个 ⋯ 下拉按钮**。2 个图标按状态智能选取（按 §3.1.2 矩阵的"最关键动作"）；其余可用动作进 ⋯ 下拉。

| UI 状态 | 智能图标 1 | 智能图标 2 | ⋯ 下拉内容 |
|---|---|---|---|
| **未解析** | 👁 预览 | ▶ 开始解析 | （空） |
| **解析中** | 👁 预览 | ⏳ 转圈占位图标（无动作） | 看 Manifest |
| **待审核** | ⬇ 下载 candidate | ↑ 上传 reviewed | 看 Manifest、重新解析、删除结果 |
| **已完成** | ⬇ 下载 final | 👁 预览 | 看 Manifest、看 QA、重新解析、删除结果 |
| **解析失败** | ↻ 重新解析 | 👁 预览 | 看 Manifest、看 QA、删除结果 |

**智能图标规则**：
- 任何状态下必出"👁 预览"——除非已被另一关键动作挤占图标位（待审核状态用 ⬆ 上传 reviewed 替代预览，因为审核是这个状态下最关键的待办动作）
- "已完成"状态出 👁 预览而非"看 Manifest"——预览是用户最常见的"看一眼"
- "解析失败"状态出 ↻ 重新解析——恢复是首要动作

**⋆ 下拉触发**：
- 用现有 `<button class="icon-button">` + lucide `more-horizontal` 图标
- 下拉菜单浮在按钮下方左对齐，浅灰背景 + 阴影
- 点击下拉外部自动关闭（监听 document click）
- 下拉底部独立分隔条 + 红色文字 + 删除图标——危险操作隔离（按 §3.1.4）

#### 7.4.3 删除结果二次确认 modal

按 §3.1.4：标题固定「删除解析结果」、必填档案标题确认、取消/关闭按钮始终可点、审计日志 `action='quota_parse_result_deleted'`。模态框可复用现有 `quotaUploadModal` 模板样式。

#### 7.4.4 列表行点击行为

- 点击行任意非图标按钮区域：跳转到档案详情 viewer（现有行为）
- 点击图标按钮：阻止冒泡 + 执行图标对应动作（不走详情）
- ⋯ 下拉菜单项点击：阻止冒泡 + 执行对应动作 + 关闭下拉

#### 7.4.5 状态轮询

- 列表中 `archive.status ∈ {parsing}` 的行：前端每 3 s 轮询该档案详情（与 §5.2 一致）——**整张列表不轮询**，仅对解析中行做客户端级轮询（每行维护自己的轮询句柄）
- 转解析状态由用户点 ▶「开始解析」触发（不走轮询）
- 状态从 `parsing → parsed/qa_passed/failed_*`：前端立刻停止该行的轮询；徽章与图标自动重渲染

#### 7.4.6 排序与列结构

`LIST_COLUMNS`（quota-ui.js L55）从 5 列扩到 **7 列**：

```
档案标题 | 资料分类 | 文件数 | 状态 | 操作
```

**操作列**承载 §7.4.2 的智能图标 + ⋯ 下拉。**状态列**承载 §7.4.1 的徽章。

默认排序：`edition_year desc, created_at desc`（新版年度定额优先）。

#### 7.4.7 空态

- 筛选后无档案：「该条件下暂无定额档案」+「清除筛选」链接（省份 → 全部；年份行折叠）
- 解析中档案无进度条——按决策"OCR 无法显示有几页"，仅转圈 + 状态徽章

#### 7.4.8 与档案详情的关系

- **不重复入口**：操作列里的 ⬇ / ↑ / ↻ 按钮直接调端点（§5.1–§5.7），不打开档案详情；档案详情 §7.1「解析区块」作为全功能详情页保留
- **冲突解决**：用户在列表点了 ▶「开始解析」后，若在详情页打开同一档案，看到的状态应一致（同一 `archive.status`，由后端权威）

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

- **v0.1**（本文档）：初稿，待与 `parser/SPEC.md` v0.2 核对。包含：7 个核心端点（§5.1–§5.7）、复用 `archive.status` 状态机、Mock 模式（§8，含 4 条硬约束）。
- **v0.2**（中转）：新增 §3.1「按钮清单 + 状态矩阵」、§5.8 占位取消端点、UI 状态映射表加 `cancelled` 终态。
- **v0.3**（本文档）：
  1. 删除 §5.8 取消端点、`cancelled` 终态、§3.1.1 #2 按钮（开始解析后不允许停止）。
  2. 状态枚举从 8 档收敛到 5 档（`未解析 / 解析中 / 待审核 / 已完成 / 解析失败`）。
  3. §3.1 按钮从 10 个砍到 9 个。
  4. 新增 §7.4「档案列表操作列集成解析」——状态徽章、智能图标、⋯ 下拉、删除二次确认 modal、解析中行级轮询。
- **v0.4**（未来）：
  1. 加 PDF 页级 OCR 置信度视图（Manifest `metrics` 扩字段，对接后端新增的 `page_confidence[]`）。
  2. 「解析任务」tab 加批量操作（批量重新解析、批量下载）。
- **v0.3.1**（2026-07-28，筛选条实施批次，详细见 §14）：
  1. 后端 `/api/data-lake/quota/facets` 接受 `jurisdiction_code` 查询参数；主年份聚合 + 嵌套 `editions` 子查询都按省过滤。
  2. 前端 `_facetsParams()` helper：建筑工程定额 + 选中具体省份 → 透传 `jurisdiction_code`；`secondary="all"` 时不传（取所有省并集）。
  3. `set-primary` / `set-secondary` 处理器在切换时主动 `getFacets()` 重刷，避免后端 facets 缓存命中过期数据。
  4. `renderYearChips()` 移除 "secondary=all 时整行不渲染" 的短路——年份作为与省份正交的维度，"全部省份 + 某年"是合理筛法。
  5. `getFacetItems("jurisdictions")` 后端空时 → `PROVINCE_REGIONS` 静态兜底（31 省 + 深圳市 = 32 条，**不含**兵团 / 其他 4 个计划单列市），用 `short` 字段紧凑显示。
  6. `getFacetItems("years")` 后端空时 → 最近 6 年倒序 `[2026..2021]` 兜底。
  7. 新增 `PROVINCE_REGIONS` 静态数组，与 `JURISDICTION_REGIONS`（GB/T 2260 全集 37 条）明确分工：前者给筛选条 chip fallback，后者给 compose 弹窗的「省份/管辖」下拉。
  8. `renderChipGroup` 按 `field` 区分 `maxDefault`：主筛选维度（`jurisdiction/industry/scope/year/edition`）= 32，不折叠；高级筛选字段 = 8。

---

## 14. v0.3.1 实施批次记录（2026-07-28）

> **批次目标**：补齐建筑工程定额筛选条全路径（省份 → 年份）；后端 `/facets` 留按省过滤的口子；前端在接口未就绪时也能给出合理默认值。

### 14.1 改动文件清单

| 文件 | 改动 | 用途 |
|---|---|---|
| `file_asset_service/app/quota_api.py` | `_facet_years()` 增 `jurisdiction_code` 参数；`get_facets()` 增 query 参数 | 后端按省过滤年份聚合（主年份 + 嵌套 `editions` 子查询均生效） |
| `file_asset_service/app/ui/quota-ui.js` | `_facetsParams()` helper；`getFacetItems()` jurisdictions/years fallback；新增 `PROVINCE_REGIONS` 静态数组；`renderYearChips()` 移除 secondary=all 短路；`renderChipGroup()` 按 field 区分 maxDefault；`set-primary` / `set-secondary` handler 重刷 facets | 前端筛选条全链路 |

### 14.2 关键决策

1. **年份与省份正交**：选"全部省份 + 某年"是合理筛法（如"全国所有 2025 年定额"）。`renderYearChips()` 不再因 `secondary="all"` 就整行不渲染。
2. **`PROVINCE_REGIONS` ≠ `JURISDICTION_REGIONS`**：
   - `PROVINCE_REGIONS`（32 条 = 31 省 + 深圳市，code=440300）—— 筛选条 chip fallback，紧凑型 `short` label；
   - `JURISDICTION_REGIONS`（37 条 = GB/T 2260 全集，含 5 个计划单列市 + 兵团）—— 仍用于 compose 弹窗的"省份/管辖"下拉，**本批次不动**。
3. **后端 `/facets` 未就绪时**：前端不是显示"地区维度待接入"占位，而是用静态兜底展示完整省份集合；后端接好后接口数据自动让位。
4. **`renderChipGroup` `maxDefault` 条件化**：主筛选维度（地区 / 行业 / 适用范围 / 年份 / 版次）= 32 不折叠；高级筛选字段 = 8 保持折叠。

### 14.3 用户验证（全路径通过 2026-07-28）

| 场景 | 期望 | 实测 |
|---|---|---|
| 默认状态（primary=全部） | 仅 4 个一级 chip；无二级、无年份 | ✅ |
| primary=建筑工程定额, secondary=全部 | 地区行 32 chip + "全部"（短名，无"展开更多"，无大连 / 计划单列市）；年份行 6 chip + "全部" | ✅ |
| primary=建筑工程定额, secondary=具体省 | 地区行同 32 chip，"该省"高亮；年份行联动（接口按省过滤 或 fallback 6 年兜底） | ✅ |
| primary=清单规范 / 专业工程定额 | 一级 chip + 各二级行；年份与 secondary 正交 | ✅ |
| 切 primary → 选具体省份 → 切回"全部" | `getFacets()` 自动重刷，years 跟随 jurisdiction_code 正确联动 | ✅ |

### 14.4 留作批 2 / 后续批次

- 操作列集成（状态徽章 / 智能图标 / ⋯ 下拉 / 删除二次确认 modal）
- 新建 `quota-parse.js` 包 7 个解析端点
- 解析中行级轮询


