# SPEC-QA-001 · P0-5 quota 补录界面改造 · 工作计划 v0.1

**状态：`PLAN_ONLY`（先提交计划，暂不写代码；待 ChatGPT 审核通过再开工）。**

## 0. 范围与铁律（本轮硬约束）
- 仅改 `domain_type=quota` 的补录界面；其他域（`cost_info`/`trading`/`policy_regulation`/`standard_atlas`）现有上传弹窗与逻辑**零改动**。
- **不使用 Mock**；API 无数据显示真实空状态。
- 后端 P0-4 接口未就绪前，**新表单不得接到旧 Archive 接口**（禁止走 `/api/archives/from-ingest-event`、`/api/archives/{id}/files` 的 cost_info 补录链路）。
- UI 结构开发可与 Legacy Blob（P0-3.5）**并行**；但**真实提交接线依赖 P0-4**，未就绪时"保存并提交核验"禁用。
- 底部按钮统一：**取消 / 保存草稿 / 保存并提交核验**；缺主文件或关键字段时**只允许保存草稿**。

## 1. 现状（改造基线）
- 单一弹窗 `#manualUploadModal` + 状态 `state.manualUpload`，三模式 `create/supplement/edit`，字段为 cost_info 专用（价格适用地区级联、期次 period、发布主体、标题、操作人），角色仅 `main_document/cover/attachment`。
- 提交 `submitManualUpload()`（`app/ui/app.js:1614`）**强制单主**：只取一个 `main_document` 建**一个** Archive，其余主件被降级为 `attachment`（`app.js:1663`）——这正是本轮要禁止的"多主 PDF 全挂一个 Archive"。
- Rail 已有 `data-domain="quota"` 入口（`index.html:36`）；header「新增档案」`data-action="open-upload"` → `openManualUploadDialog()` 固定走 cost_info。
- 技术栈：原生 JS 渲染函数 + 事件委托（无框架），沿用现有风格，不引入新依赖。

## 2. 目标：quota 补录入口拆为三个独立动作（SPEC §11.3 / §8）
| 动作 | 语义 | 三级关系落点 |
|---|---|---|
| A. 新建定额体系 | 新建 Publication Set（可后续挂分册） | Publication Set |
| B. 向已有体系新增分册 | 选已有 Set → 建分册 Archive + 其文件 | Set → Archive(分册) → Files |
| C. 向已有档案补充文件 | 选已有 Archive → 追加文件并设角色 | Archive → Files |

三动作入口：header「新增档案」在 `domain=quota` 时改为**下拉三选**（新建定额体系 / 新增分册 / 补充文件），其余域保持原「新增档案」单按钮。

### 2.1 新建定额体系 · 两条条件路径（SPEC §6.3 / §9.4）
- 路径①**建筑工程定额**：`quota_system_type=construction_regional` → 地区 `jurisdiction_level(national/province/city)+jurisdiction_code` → 年份 `edition_year`。
- 路径②**专业工程定额**：`quota_system_type=industry_specialty` → 行业体系 `industry_sector_code`（来自 `quota_dictionary`，不硬编码）→ 年份 `edition_year`。
- 切换路径必须**清空**另一路径的地区/行业、年份、分册专业条件。
- **删除**（cost_info 遗留，不属于 quota）：价格适用地区、期次 period、手填操作人。
- **新增/保留字段**：体系名称 `title`、版本 `edition_label`+`edition_year`、发布单位 `issuer_name`、实施日期 `effective_date`（及 `publish_date/legal_status/standard_or_quota_code/expected_volume_count` 选填）。约束沿用 P0-2：`construction_regional` 不填 sector、`industry_specialty` 必填 sector。

### 2.2 文件区：一套体系 → 多分册 Archive → 每分册多文件
- 结构：Publication Set（1）→ 分册 Archive（N）→ 每分册 Archive File（N）。
- 文件角色（映射 P0-2 `ARCHIVE_FILE_ROLES` / `DOCUMENT_ROLES`）：**正文** `main_document`、**封面** `cover`、**目录** `table_of_contents`、**附录** `appendix`、**发布公告** `release_announcement`、**其他** `other`。
- **关键规则**：批量选择的多个"正文/主 PDF"**不得全部挂到同一个 Archive**。每个主文件必须归属**一个独立分册 Archive**；UI 以"分册卡片"承载：新增主文件时提示归入哪个分册或新建分册，一个分册仅允许一个 `main_document`（多余主件必须拆到新分册，不再静默降级为附件）。

## 3. 修改 / 新增文件
| 文件 | 动作 | 说明 |
|---|---|---|
| `app/ui/index.html` | 改 | 新增 quota 专属弹窗容器 `#quotaComposeModal` + header 下拉入口标记；**不动** `#manualUploadModal` |
| `app/ui/app.js` | 改（新增独立段落） | 新增 quota compose 模块（渲染/状态/校验/草稿）；仅在 `state.domain==='quota'` 时接管「新增档案」；旧 `openManualUploadDialog/submitManualUpload*` 原样保留 |
| `app/ui/styles.css` | 改 | quota compose 专属样式（分册卡片、路径切换、文件角色行、footer 三键）；不覆盖既有 class |
| `docs/quota_supplement_ui_plan_v0.1.md` | 新增 | 本计划 |
| `tests/`（前端验收） | 新增 | Playwright/脚本用例 + 截图（见 §9） |

> 不新建平行 JS 文件以符合现有单文件约定；quota 逻辑以清晰命名前缀 `quotaCompose*` 隔离在 `app.js` 独立区块。

## 4. 组件拆分（渲染函数）
- `renderQuotaEntryMenu()`：header 下拉三入口（仅 quota）。
- `renderQuotaComposeModal()`：外壳 + 按 action 分派。
- `renderQuotaSetPathSelector()`：两条路径 + 地区级联 / 行业体系 + 年份。
- `renderQuotaSetForm()`：体系名称/版本/发布单位/实施日期等。
- `renderQuotaVolumeList()`：分册卡片列表（分册专业 discipline/volume_title/document_role + 该分册文件区）。
- `renderQuotaFileRow()`：单文件 + 角色下拉（六角色）+ 归属分册。
- `renderQuotaComposeFooter()`：取消 / 保存草稿 / 保存并提交核验（含禁用态与原因提示）。
- `renderQuotaSupplementPicker()`：动作 C 的目标 Archive 选择 + 文件区。
- 校验：`validateQuotaCompose(state)`；草稿：`saveQuotaDraft()/loadQuotaDraft()`。

## 5. 表单状态模型（`state.quotaCompose`）
```text
state.quotaCompose = {
  open: bool,
  action: 'new_set' | 'add_volume' | 'supplement',
  systemType: 'construction_regional' | 'industry_specialty' | null,
  path: { jurisdiction_level, jurisdiction_code } | { industry_sector_code },
  editionYear: number | null,
  set: { title, edition_label, issuer_name, effective_date, publish_date,
         legal_status, standard_or_quota_code, expected_volume_count },
  targetSetId: string | null,      // add_volume / 选已有体系
  targetArchiveId: string | null,  // supplement
  volumes: [ { tempId, discipline_code|discipline_label, volume_title,
               document_role, files: [ { tempId, file, role } ] } ],
  draftId: string | null,
  errors: { path, set, volumes, files },
  submitting: bool,
  apiReady: bool                   // 由能力探测/开关决定
}
```
- 状态与 `state.manualUpload` **完全独立**，互不读写。

## 6. API 依赖（P0-4，SPEC §10；当前未就绪）
| 动作 | 依赖端点（建议前缀 `/api/data-lake/quota`） |
|---|---|
| 选已有体系/树 | `GET /publication-sets`、`GET /publication-sets/{id}/tree` |
| A 新建体系 | `POST /publication-sets` |
| B 新增分册 | `POST /archives`（带 `publication_set_id`） |
| A/B 挂文件 | 原件入湖走既有 `POST /api/file-assets/ingest`（须指向 `data_domain=quota` 的 DataSource）→ `POST /archives/{id}/files` |
| C 补充文件 | `POST /archives/{id}/files` |
| 级联/字典 | `GET /facets`（级联）、`GET /dictionaries`（industry_sector/discipline/jurisdiction） |
- **禁止**复用 cost_info 的 `/api/archives/from-ingest-event` 与其 `buildManualUploadArchivePayload`（会写 `domain_type=cost_info`、强制单主）。

## 7. 无接口时的处理（P0-4 未就绪）
- UI 结构、路径切换、分册/文件区、校验、草稿**全部可开发并演示**（纯前端，无网络写）。
- **能力探测**：启动时探测 quota P0-4 端点是否存在（`GET /publication-sets` 返回 404/未注册 → `apiReady=false`）。
- `apiReady=false` 时：
  - 「保存并提交核验」**禁用**，悬浮提示"P0-4 接口未就绪，暂只能保存草稿"；
  - 「保存草稿」写入**本地草稿**（localStorage，键含 domain=quota），不发任何写请求；
  - 字典/地区可先用 `GET /dictionaries` 或既有只读源；无则显示真实空状态，**不 Mock**。
- 严禁在此期把表单降级接到旧 Archive 接口。

## 8. 校验规则
- 通用 footer：**取消 / 保存草稿 / 保存并提交核验**。
- **只允许保存草稿**（禁用"保存并提交核验"）当：
  - 缺关键字段：A 缺 `systemType`/路径必填项（地区或行业）/`edition_year`/`title`/`issuer_name`；B 缺 `targetSetId` 或分册专业；C 缺 `targetArchiveId`；
  - **缺主文件**：任一待建分册没有恰好一个 `main_document`；
  - 违反"单分册单主"：某分册出现 ≥2 个 `main_document`（要求拆分到新分册）。
- 提交核验前置：`apiReady=true` 且校验全过。
- 错误呈现对齐 SPEC §10.6 语义（`REQUIRED_FIELD_MISSING`/`PRIMARY_FILE_REQUIRED`/`BIZ_KEY_CONFLICT` 等）。

## 9. 对其他域的隔离
- quota compose 只在 `state.domain==='quota'` 渲染/接管；切换到其他域立即关闭并清空 `state.quotaCompose`。
- header「新增档案」按 domain 分派：quota→下拉三入口；其余域→原 `openManualUploadDialog()` 不变。
- 独立弹窗 `#quotaComposeModal`，不复用 `#manualUploadModal` 的 DOM / 状态 / 提交函数。
- CSS 使用 `quota-` 前缀，避免污染既有样式。

## 10. 测试与截图验收
**回归（其他域不受影响）**
- cost_info「新增档案/补充文件/编辑」弹窗与提交流程逐项不变（截图对比）。
- quota compose 过程中**不得**发起对 `/api/archives/from-ingest-event` 的请求（网络断言）。

**quota 功能**
- 入口下拉三动作可见且互斥打开。
- A：两条路径切换清空对侧条件；缺字段仅可存草稿。
- 文件区：一个分册仅一个主文件；批量选 2 个主 PDF 必须落到 2 个分册（不得同挂一个 Archive）。
- footer 三态：字段/主文件齐全且 `apiReady` → "保存并提交核验"可用；否则仅"保存草稿"。
- `apiReady=false` 时保存草稿只写本地、无网络写；无数据显示真实空状态（无 Mock）。

**截图**：三动作各 1 张、两路径各 1 张、单分册单主校验 1 张、footer 禁用态 1 张、其他域回归 1 张。

## 11. 交付顺序（审核通过后）
1. index.html 容器 + header 分派（不动旧弹窗）；
2. 状态模型 + 渲染骨架 + 空状态；
3. 两路径 + 字段 + 校验 + 草稿（本地）；
4. 分册/文件区 + 单分册单主规则；
5. 能力探测 + footer 三态；
6. 提交接线**留空/禁用**待 P0-4；
7. 测试 + 截图。

> 本计划为 `PLAN_ONLY`。请 ChatGPT 审核；通过后再开工，且真实提交接线仍等待 P0-4 接口就绪。
