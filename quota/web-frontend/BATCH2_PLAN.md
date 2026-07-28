# 批 2 实施计划 · v0.3.2（待执行）

> **状态**：待执行（前置：成功上传至少一份定额档案，能进入 `registered` 状态）
>
> **目标**：把解析入口集成进档案列表的"操作列"——用户不必进入档案详情即可触发解析、上传 reviewed、下载 final、看 Manifest/QA、删除结果。
>
> **本计划是 SPEC §3.1 / §5 / §7.4 / §8 的实施拆分**。SPEC 是权威源；本文件只放工作分解、文件改动表、验证策略与风险，**不**重写设计。

---

## 1. 前置条件（必须满足）

- ✅ 后端 `/api/data-lake/quota/archives/{archive_id}/...` 7 个解析端点已实现（SPEC §5.1–§5.7）
- ✅ 至少 1 条档案能成功上传 → 进入 `registered` 状态 → 出现在档案列表中
- ✅ `QUOTA_PARSE_MOCK=1` 模式可用（SPEC §8）—— 真实 Worker 未上线前，可走 mock 端到端跑通
- ✅ 状态机 `registered → parsing → parsed → qa_passed → usable / failed_*` 推进可见

> 前端批 2 **可以**只靠 mock 模式验证。只有在切换 Worker 真实模式时才需要 backend 对接。

---

## 2. 设计基线（直接来自 SPEC，本文件不重复）

| 项 | SPEC 章节 | 备注 |
|---|---|---|
| 5 状态枚举 | §6.1 / §3.1.2 | `未解析 / 解析中 / 待审核 / 已完成 / 解析失败` |
| 9 按钮清单 | §3.1.1 | `#1 开始`/`#2 PDF预览`/`#3 candidate`/`#4 上传 reviewed`/`#5 final`/`#6 Manifest`/`#7 QA`/`#8 重新解析`/`#9 删除结果`；v0.3.2 **无 #10 取消** |
| 5×9 矩阵（状态 × 按钮可见性） | §3.1.2 | 单一权威源；UI 代码用此矩阵做按钮可见性判断 |
| 智能图标 + ⋯ 下拉布局 | §7.4.2 | 每行 2 图标 + 1 ⋯ 下拉；2 个图标按"最关键动作"选 |
| 删除二次确认 modal | §7.4.3 + §3.1.4 | 必填档案标题确认；底部独立分隔条；红色危险项 |
| 解析中行级轮询 | §7.4.5 | 仅对 `parsing` 行每 3s 轮询详情；整张列表不轮询 |
| LIST_COLUMNS 7 列 | §7.4.6 | `档案标题 \| 资料分类 \| 文件数 \| 状态 \| 操作`（实际编码时只有 5 列，状态+操作两列就是新加的两列） |
| Mock 端点契约 | §8 | `async def` + 后台 task；4 条硬约束（见 SPEC） |
| 与档案详情关系 | §7.4.8 | 列表点 ⬇/↑/↻ 直接调端点，不打开详情；冲突以 `archive.status` 后端权威为准 |

### 2.1 智能图标矩阵（引用 SPEC §7.4.2）

| UI 状态 | 图标 1 | 图标 2 | ⋯ 下拉内容 |
|---|---|---|---|
| 未解析 (`registered`) | 👁 预览 | ▶ 开始解析 | — |
| 解析中 (`parsing`/`transient`) | 👁 预览 | ⏳ 转圈占位 | 看 Manifest |
| 待审核 (`parsed`) | ⬇ candidate | ⬆ 上传 reviewed | 看 Manifest、重新解析、删除结果 |
| 已完成 (`qa_passed`/`usable`) | ⬇ final | 👁 预览 | 看 Manifest、看 QA、重新解析、删除结果 |
| 解析失败 (`failed_*`) | ↻ 重新解析 | 👁 预览 | 看 Manifest、看 QA、删除结果 |

---

## 3. 工作分解（WBS）

### 3.1 W1 · status-badge 5 个样式 + ⋯ 下拉样式

**目标**：CSS 骨架先上（即便 quota-ui.js 还没渲染这些组件，class 已经存在，dev 工具栏能看到样式）。

**改动**：
- `file_asset_service/app/ui/styles.css`（或同名的 CSS 文件）：新增
  ```css
  .status-badge                { /* 基础胶囊样式 */ }
  .status-badge--pending       { /* 未解析 · 灰 */ }
  .status-badge--parsing       { /* 解析中 · 蓝 + 转圈 */ }
  .status-badge--review        { /* 待审核 · 橙 */ }
  .status-badge--done          { /* 已完成 · 绿 */ }
  .status-badge--failed        { /* 解析失败 · 红 */ }
  .status-badge__spinner       { /* 解析中旋转图标 */ }

  .quota-action-cell           { /* 操作列容器 */ }
  .quota-action-cell__icon     { /* 单个图标按钮 */ }
  .quota-action-cell__more     { /* ⋯ 按钮 */ }
  .quota-dropdown              { /* 下拉菜单容器 */ }
  .quota-dropdown__item        { /* 普通项 */ }
  .quota-dropdown__item--danger { /* 红色危险项 */ }
  .quota-dropdown__divider     { /* 下拉内分隔条 */ }
  ```

**验证**：
- 浏览器 dev 工具栏手工给某个元素加 class，看颜色对
- 用 [拟色板截图] 比对 SPEC §7.4.1 表格

---

### 3.2 W2 · LIST_COLUMNS 扩列 + 列表列结构调整

**目标**：`LIST_COLUMNS` 从 5 列扩到 7 列（实际是新增"状态"+"操作"两列），让状态徽章与操作列占位先出现。

**改动**：`file_asset_service/app/ui/quota-ui.js`
- `LIST_COLUMNS = Object.freeze([...])`：
  ```js
  ["档案标题", "资料分类", "文件数", "状态", "操作"]
  ```
  （SPEC §7.4.6 写"7 列"但实际只有 5 列名；第 4 列是状态、第 5 列是操作——SPEC 文档笔误，实际编码以 5 列名为准）
- `renderArchivesView()` 表头按新列渲染（已有逻辑只需 LIST_COLUMNS 长度变了）
- `renderArchiveRow(row)` 返回的 `<tr>` 增加 `<td>` 为 状态 + 操作两列；先放空 `<span>` / `<div>`，由 W3 + W4 填实

**先实现**：把列结构搭出来，但内容先 placeholder（"状态: …" + "操作: ⋯"），W3 / W4 替换之。

**验证**：
- 表格 header 多了「状态」「操作」两列
- 表格 row 多了两列空 cell，列对齐

---

### 3.3 W3 · 状态徽章渲染函数 + 智能图标渲染函数

**目标**：根据 `archive.status` 渲染徽章 + 智能 2 图标。

**改动**：`file_asset_service/app/ui/quota-ui.js`
- 新增 `_archiveUiState(archive)`：把 `archive.status` + `parse.error_code` 映射到 5 个 UI 状态枚举（`registered/parsing/parsed/done/failed` + 暂态 `transient`）
- 新增 `renderStatusBadge(archive)`：返回 `<span class="status-badge status-badge--xxx">[icon] [text]</span>`
- 新增 `renderStatusIcons(archive)`：返回 2 个图标按钮的 HTML（按 §7.4.2 矩阵）
- `renderArchiveRow()` 用上二者，替换 W2 的 placeholder

**数据源**：`archive.status`（已有，从 `/api/archives?domain_type=quota` 返回）。`parse.error_code`、`parse.profile`、`parse.parser_version` 等需要 `/archives/{id}` 详情（§5.2）—— 列表行级是否要拉详情，**待定**：MVP 阶段可先只用 status 字段，过渡期后再补解析字段。

**验证**：
- 一条 `registered` 档案 → 灰色徽章"未解析"，操作列 2 图标（👁+▶）
- 一条 `parsing` 档案 → 蓝色徽章"解析中"+ 转圈，操作列（👁+⏳）
- 各状态以 mock 模式推进后，徽章与图标自动跟随

---

### 3.4 W4 · ⋯ 下拉组件

**目标**：第三列图标按钮（`more-horizontal`），点击展开下拉菜单，点外部自动关闭。

**改动**：`file_asset_service/app/ui/quota-ui.js`
- `state.archiveMenuOpen` 全局 state 改成"逐行级"：用 `state.rowMenus = { [archiveId]: bool }` 或一个 `Set` —— **每个行可同时只有 1 个下拉打开**
- 新增 `renderActionMore(archive, position)`：返回 ⋯ 按钮 + 下拉菜单 HTML
- `bindEvents()` 加监听：
  - 点击 ⋯ 按钮 → toggle 该行下拉；阻止冒泡
  - 点击下拉内某项 → 触发对应动作 + 关闭下拉
  - 点击页面其他位置 → 关闭所有下拉（除非点击的目标也在某个下拉内）
- 下拉底部"删除解析结果"项用 `.quota-dropdown__item--danger`

**位置**：浮在按钮下方左对齐（CSS：`position: absolute; top: 100%; left: 0;`）

**验证**：
- 点击 ⋯ → 下拉出现；点 ⋯ 外的任意位置 → 下拉消失；再次点 ⋯ → 重新出现
- 同时打开两个不同行的 ⋯ → 只显示当前行的下拉（之前那个自动关）
- 下拉内每个 item 点 → 对应端点被调（用 dev 工具 network 面板确认）

---

### 3.5 W5 · 删除结果二次确认 modal

**目标**：用户在下拉点"删除解析结果"→ 弹出 modal → 必须输入档案标题才能确认。

**改动**：`file_asset_service/app/ui/quota-ui.js` + `index.html`
- 新增 `<div id="quotaDeleteConfirmModal">` 容器（参考已有 `#quotaUploadModal`）
- `state.deleteConfirm = { open, archiveId, archiveTitle, input, submitting }`
- `renderDeleteConfirmModal()`：标题固定"删除解析结果"、body 展示档案标题 + 输入框、footer 取消/确认（确认默认 disabled，直到输入 === archiveTitle）
- `handleAction("open-delete-confirm:archiveId")`：打开
- `handleAction("submit-delete-confirm")`：调 §5.9 端点（**端点路径待与后端 confirm** —— SPEC §12 标"待确认"，批 2 启动前先和后端对齐）
- 成功后 toast "已删除解析结果"；失败保留 modal + 显示错误
- 审计：`action='quota_parse_result_deleted'` 由后端写

**注意**：
- modal 关闭按钮（×）和取消按钮始终可点；不与 §8 共享样式
- modal 关闭后清空 state.deleteConfirm.input
- modal 阻止列表点击事件穿透

**验证**：
- 输入错误标题（不等于 archive.title） → 确认按钮 disabled
- 输入正确标题 → 确认按钮 enabled，点 → modal 关闭 + 列表行 status 退回 `registered`
- ESC 键关闭 modal（与现有 `quotaComposeModal` / `quotaUploadModal` 行为一致）

---

### 3.6 W6 · 解析中行级轮询

**目标**：仅对 `archive.status === 'parsing'`（含 `transient`）的行每 3 s 轮询该档案详情，状态切换后停止轮询。

**改动**：`file_asset_service/app/ui/quota-ui.js`
- `state.archivePolls = { [archiveId]: intervalId }`
- `load()` / 每次 `state.archives` 更新后：
  - 遍历 `state.archives.data`
  - 对 `status === 'parsing'` 且无轮询句柄的行 → `setInterval(... , 3000)`
  - 对不在 `parsing` 但有轮询句柄的行 → `clearInterval`
- 轮询回调：调 §5.2 `GET /archives/{id}`，更新 `state.archives.data[i]` + 重渲染
- `deactivate()` 时清掉所有轮询（防 memory leak + race）

**验证**：
- mock 模式点 ▶「开始解析」→ 2–10s 内状态从 `parsing` 推到 `parsed` → 轮询自动停
- 多行同时 `parsing`：每行独立轮询，互不干扰
- 页面切换 tab → 轮询清空（防止后台轮询污染其他视图）

---

### 3.7 W7 · 新建 `quota-parse.js` 包 7 个解析端点

**目标**：把 §5.1–§5.7 7 个端点的 API 客户端封装到一个独立 IIFE / 全局 `QuotaParse`，与 quota-api.js 同源风格。

**改动**：新建 `file_asset_service/app/ui/quota-parse.js`
- 暴露 `global.QuotaParse = { createParseApi, ... }`
- 7 个方法：
  | 方法 | 端点 | 返回 |
  |---|---|---|
  | `startParse(archiveId, opts)` | `POST /archives/{id}/parse` | `{status, data: {archive}, error}` |
  | `getParseStatus(archiveId)` | `GET /archives/{id}`（已存在 `state.api.getArchiveDetail`，可复用） | 同左 |
  | `downloadCandidate(archiveId, opts)` | `GET /archives/{id}/candidate.xlsx` | Blob → 触发下载 |
  | `uploadReviewed(archiveId, file, opts)` | `POST /archives/{id}/reviewed`（FormData 上传 .xlsx） | `{status, data, error}` |
  | `downloadFinal(archiveId, opts)` | `GET /archives/{id}/final.xlsx` | Blob |
  | `getManifest(archiveId)` | `GET /archives/{id}/manifest` | JSON（包含 `parser_version` 等） |
  | `getQaReport(archiveId, opts)` | `GET /archives/{id}/qa-report?format=md` / `json` | 文本或 JSON |
  | `deleteResult(archiveId)` | `POST /archives/{id}/parse/delete`（**路径待 backend confirm**） | `{status, data, error}` |

**特点**：
- 全部走现有的 `request()` 工具（quota-api.js 暴露），不重新发明错误处理
- 下载端点统一走 Blob → `URL.createObjectURL` + `<a download>` 触发
- 上传端点用 FormData + `Content-Type: multipart/form-data` 自动
- mock 模式与真实模式无差别调用（mock 是后端职责）

**`index.html`**：在 `quota-api.js` 之后插入 `<script defer src="/ui-assets/quota-parse.js">`

**验证**：
- 7 个方法在 mock 模式下均能调到（看网络面板）
- 错误处理：401/403/404/500 都能解到 `error` 字段，让 UI 显示 toast

---

## 4. 文件改动表

| 文件 | 改动 | WBS |
|---|---|---|
| `file_asset_service/app/ui/quota-ui.js` | LIST_COLUMNS；新增 `_archiveUiState/renderStatusBadge/renderStatusIcons/renderActionMore/`；行级轮询 state；删除 modal state + 渲染；事件绑定拓展；3.7 接口调用 | W2/W3/W4/W5/W6/W7 |
| `file_asset_service/app/ui/quota-parse.js` | **新建** —— 7 个解析端点封装 | W7 |
| `file_asset_service/app/ui/index.html` | 引入 `quota-parse.js`；新增 `<div id="quotaDeleteConfirmModal">` 容器 | W5/W7 |
| `file_asset_service/app/ui/styles.css` | 5 个状态徽章样式；操作列 / ⋯ 下拉样式；删除 modal 样式（可复用 upload modal） | W1/W4/W5 |
| `file_asset_service/app/ui/quota-api.js` | 不动（`state.api.getArchiveDetail` 可被轮询复用） | — |
| `quota/web-frontend/SPEC.md` | 批 2 完成后追加 §13 v0.3.2 版本项 + §15 实施批次记录（与批 1 同模板） | 收尾 |
| `quota/README.md` | 末尾"v0.3.2 进展"段 | 收尾 |

---

## 5. 验证策略（顺序与硬卡点）

1. **W1 收尾**：dev 工具给一个 `<span class="status-badge status-badge--done">已完成</span>` 截图，颜色符合 SPEC §7.4.1
2. **W2 收尾**：档案列表表头 + 行多两列
3. **W3 收尾**：mock 模式下：
   - 1 条 `registered` 档案 → 灰徽章 + 👁 / ▶ 图表确认
   - ▶「开始解析」 → 状态推到 `parsing` → 蓝徽章 + ⏳ 转圈
   - mock 跑完推到 `parsed` → 橙徽章 + ⬇ / ⬆ 图标确认
   - 上传假 `.xlsx` 推到 `qa_passed` → 绿徽章 + ⬇ / 👁 图标确认
4. **W4 收尾**：⋯ 下拉打开/关闭/外部点击关闭/多行互斥
5. **W5 收尾**：删除 modal 输入错误标题 disabled / 输入正确标题 enabled / 调端点成功状态退回
6. **W6 收尾**：开启浏览器 network throttling，看 `parsing` 行确实每 3s 调一次 `/archives/{id}`；状态切换后停止
7. **W7 收尾**：每个 7 个接口在 mock 模式 + 真实模式各跑一遍

> ⚠️ 真实 Worker 上线前，W3 / W5 / W6 只能靠 mock 模式验证；切真实 Worker 后必须**额外**跑一遍 W3–W6 验收。

---

## 6. 风险与依赖

| 风险 / 依赖 | 影响 | 缓解 |
|---|---|---|
| §5.9 删除端点路径未定（SPEC §12 开放问题） | W5 实现阻塞 | **批 2 启动前先 confirm**：是 `POST /parse/delete` 还是 `DELETE /parse/result` |
| `archive.status` 列表行级 vs 详情级一致性 | W3 状态徽章准确性 | 列表用 `archive.status` 直接渲染；详情用 `parse.*` 字段——必要时详情"→ 列表"方向同步 |
| 行级轮询规模 | 假设同一时间 `parsing` 行 ≤ 5；多 ≥ 50 行同时 `parsing` 会击穿 | 限速：每行独立的 3 s 间隔不会并发；50 行 = 每行独立错开。后端如有 rate limit，按 429 处理退避 |
| mock 模式下"删除结果"端点是否实现 | W5 在 mock 模式可能调不到 | 与后端同步：mock 端点集中放在 `quota_api.py` `if QUOTA_PARSE_MOCK:` 分支 |
| `parser_version` 强提示 | §3.1.5 要求在 parsed+ 状态弹窗头部显示 | W7 `getManifest()` 返回后由 UI 决定如何展示——批 2 先不展开，列入 §15 实施笔记 |
| 删除的端点方法 | 是 `POST /parse/delete` 还是 `DELETE /archives/{id}/result`（或别） | W7 启动前 confirm |
| Operations 列在不同分页宽度下的截断 | 行宽不够时 ⋯ 下拉自动多列 | CSS 弹性布局 + 最小化单图标的 padding |

---

## 7. 完成定义（Definition of Done）

- [ ] W1–W7 全部完成 + 各自单测（mock 模式）
- [ ] 真实 Worker 模式（如可用）跑一遍 W3–W6
- [ ] SPEC §3.1.2 5×9 矩阵的每个单元格都有 UI 表现（即便 ⋯ 下拉内）
- [ ] 行级轮询在 deactivate 时全部清理（dev 工具 console 无 setInterval 残留 warning）
- [ ] 删除 modal 在 ESC / 取消 / × 三种关闭路径都正确 reset `state.deleteConfirm`
- [ ] 所有新组件（status-badge / dropdown / delete-confirm modal）在 Safari / Chrome / Edge 三家主流桌面浏览器渲染正确
- [ ] `node --check quota-ui.js quota-parse.js` exit 0
- [ ] SPEC §13 v0.3.2 + §15 实施批次记录 + README v0.3.2 进展段更新到位

---

## 8. 不在批 2 范围（写明避免越界）

- 「解析任务」tab（SPEC §9.1 TABS 第 5 项）—— 单独批次
- 档案详情 §7.1「解析区块」内嵌的 sub-view —— 单独批次
- 批量操作（多选 → 批量解析 / 批量下载）—— SPEC §12 留作未来
- PDF 页级 OCR 置信度视图 —— v0.4
- 编辑 / 重做 reviewed XLSX —— 不在 v0.3 计划
