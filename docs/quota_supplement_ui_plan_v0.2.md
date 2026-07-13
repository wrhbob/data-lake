# SPEC-QA-001 · P0-5 quota 主页面 + 补录界面改造 · 工作计划 v0.2

**状态：`PLAN_ONLY`（v0.2 审核通过前不写代码）。** 取代 v0.1（v0.1 只有弹窗，未含主页面改造）。

## 0. 范围与铁律
- 仅改 `domain_type=quota` 的**主页面 + 新增弹窗**；其他域（cost_info/trading/policy_regulation/standard_atlas）页面与弹窗**零改动**。
- 不使用 Mock；API 无数据显示真实空状态。
- P0-4 未就绪前，新表单**不得接旧 Archive 接口**（禁用 `/api/archives/from-ingest-event` 等 cost_info 链路）。
- 拆分交付：**P0-5A**（页面结构/交互/本地草稿，无真实写入，可与 P0-3.5 并行）；**P0-5B**（P0-4 完成后真实 API 接线）。
- 底部按钮统一：取消 / 保存草稿（API 未就绪时文案「保存本地草稿」）/ 保存并提交核验；缺主文件或关键字段仅允许存草稿。

---

## 1. 主页面改造（quota 专属，SPEC §5 / §8 / §10.4–10.5）

### 1.1 顶部汇总
- 标题区改为 quota 专属汇总：**`0套资料体系 · 0份已归档 · 8份原件待归档`**（数值来自真实聚合：publication_set 计数、archive_status=archived 计数、reconciliation pending 计数）。
- **删除** quota 页面的全域「NAS原件 2058」展示（`#storageAuditBar` 当前为全域 storage-audit；quota 下不得复用）。
- 新增 quota 专属健康条：**原件 8 · 可访问 8 · 待归档 8 · 重复 0 · 异常 0**
  - 原件/可访问：quota 归属 FileAsset 数 + 物理可访问数（storage-audit 限定 quota 范围）；
  - 待归档/重复/异常：reconciliation 按 `projection_status` 计数（pending / duplicate / invalid）。
- **隐藏生产环境调试标签**：`#domainPill`（`domain_type: cost_info`）与任何「Archive API」字样在 quota（及生产）下隐藏，仅在显式 debug 开关下显示。

### 1.2 一级页签（当前仅「档案列表/覆盖矩阵」，需补齐为四个）
1. 档案列表；
2. **版本体系**（Publication Set → Archive → Archive File 树，`GET /publication-sets/{id}/tree`）；
3. 覆盖矩阵；
4. **待归档 `8`**（reconciliation；页签带 pending 计数徽标）。

### 1.3 空状态
- 列表空状态改为：**「已有 8 份真实原件等待归档」** + 按钮 **「前往待归档」**（切到待归档页签）。
- 不再显示 cost_info 的「调整筛选条件或等待采集落档」。

### 1.4 列表列 & 筛选（按 R1 §10.4/§10.5 重构）
- 列：标题、material_type、Publication Set（title/edition_label/edition_year/system_type/industry_sector）、jurisdiction、discipline、standard_or_quota_code、file_count、primary_file_health、preview_status、metadata_status、completeness_score、archive_status、source_channels。
- 筛选参数：q、material_type[]、quota_system_type[]、jurisdiction_level/jurisdiction_code[]、industry_sector_code[]、discipline_code[]、edition_year[]、issuer_name、archive_status[]、metadata_status[]、source_channel[]（高级）、page/page_size/sort_by/sort_order。
- **所有计数来自同一查询口径的 facets，前端不静态推算。**

---

## 2. 级联筛选（SPEC §10.4 级联规则）
- **一级分段**：全部 / 清单规范（`material_type=boq_standard`）/ 建筑工程定额（`quota_system_type=construction_regional`）/ 专业工程定额（`quota_system_type=industry_specialty`）。
- 建筑工程定额：**地区 → 实际年份 → 分册专业**（jurisdiction_code → edition_year → discipline_code）。
- 专业工程定额：**行业体系 → 实际年份 → 分册专业**（industry_sector_code → edition_year → discipline_code）。
- **年份来自 `GET /facets` 级联聚合**，不静态生成 2019–2026；切换一级时清空对侧地区/行业、年份、discipline。
- **高级筛选**：入湖通道 `source_channel`、资料性质 `material_type`（quota_base/quota_supplement/…细分）、元数据状态 `metadata_status`。

---

## 3. 新增入口：四个动作（修正 v0.1 丢失「清单规范」）
header「新增档案」在 `domain=quota` 时改为下拉四选（其余域不变）：
1. **新增清单规范**（`new_boq_standard`）；
2. **新建定额体系**（`new_set`）；
3. **向已有体系新增分册**（`add_volume`）；
4. **向已有档案补充文件**（`add_file`，由 v0.1 的 `supplement` 更名）。

> 「补充定额」（新增一套 quota_supplement 资料体系/档案并关联母体）与「补充文件」（向既有 Archive 追加 Raw Object）是两件事，不得混淆：前者走 `new_set`/`add_volume` + material_type + 关联关系；后者走 `add_file`。

---

## 4. 资料性质 material_type + 关联资料（修正 v0.1 缺失）
- `state.quotaCompose.materialType` ∈
  `boq_standard | quota_base | quota_supplement | quota_explanation | amendment_errata | related_notice`。
- 当 materialType ∈ {quota_supplement, quota_explanation, amendment_errata} 时，**必须选择关联的 Publication Set 及关系类型** `relation_type` ∈
  `supersedes | supplements | explains | amends | corrects | related`（写入 `quota_publication_relation`，SPEC §6.7）。
- UI：新建体系表单在选到上述性质时展开「关联母体资料」区（选已有 Set + 关系类型）。

---

## 5. 提交语义：事务型 compose（修正半成品风险）
- 「新建定额体系」提交核验 = **一次创建 Publication Set + 多个分册 Archive + 各分册文件**，全部成功或全部失败；不得由前端顺序调多个接口形成半成品。
- **P0-4 依赖新增事务型接口**（建议）：`POST /api/data-lake/quota/compose`
  - 入参：set + volumes[]（每个含 files[] 与角色）+ material_type + 可选 relations[]；
  - **idempotency key**（前端生成，重复提交不产生重复 Set/Archive/File）；
  - **原子性**：全成功或全失败；
  - 返回 **Set / Archives / Files 逐项结果**（对齐 §10 批量语义，禁止「整体 200 无明细」）。
- 保存草稿：可只建立**草稿 Set**（或仅本地草稿，见 §7）。
- 提交核验前置：**至少一个分册，且每个分册恰好一个 `main_document`**。

---

## 6. 组件拆分 & 修改文件
| 文件 | 动作 | 说明 |
|---|---|---|
| `app/ui/index.html` | 改 | quota 专属：汇总条、健康条、四页签、`#quotaComposeModal`、下拉四入口；不动 `#manualUploadModal`、`#storageAuditBar`（仅在 quota 下隐藏/替换渲染） |
| `app/ui/app.js` | 改（独立 `quota*` 区块） | 主页面 quota 渲染（汇总/健康/页签/空状态/列表列/级联筛选）+ compose 模块；旧 cost_info 函数原样保留 |
| `app/ui/styles.css` | 改 | `quota-` 前缀样式，不覆盖既有 class |
| `docs/quota_supplement_ui_plan_v0.2.md` | 新增 | 本计划 |
| `tests/test_quota_ui_*.py` | 新增 | 复用现有 `node -e` 纯函数测试方式（§9） |

渲染函数（骨架）：`renderQuotaSummaryBar / renderQuotaHealthStrip / renderQuotaTabs / renderQuotaArchiveList / renderQuotaCascadeFilter / renderQuotaEmptyState / renderQuotaEntryMenu / renderQuotaComposeModal / renderQuotaBoqStandardForm / renderQuotaSetForm / renderQuotaVolumeList / renderQuotaFileRow / renderQuotaRelationPicker / renderQuotaComposeFooter`；逻辑：`buildQuotaFacetQuery / validateQuotaCompose / serializeQuotaDraft / deserializeQuotaDraft / detectQuotaApiState`。

---

## 7. 本地草稿（修正 File 不可序列化）
- **localStorage 只存元数据**：`{ action, materialType, systemType, path, editionYear, set, volumes:[{..., files:[{name,size,type,lastModified,role,tempId}]}], relations }`——**不存 File 对象**。
- 刷新/重进后：文件行标记「需重新选择」，提示 **「请重新选择本地文件」**（角色/顺序保留，字节需重挂）。
- key：`quotaDraft:v{SCHEMA_VERSION}:{tenant_code}:{user_id}:{action}`，含 schema 版本 + tenant + user，**防止串用户/串版本**。
- API 未就绪时按钮文案 **「保存本地草稿」**（避免误以为已入湖）。

---

## 8. 能力探测（修正 bool 不足）
- `state.quotaApi ∈ { unknown | ready | unavailable | unauthorized | error }`（探测 `GET /publication-sets` 等）：
  - **404 / 未注册 → `unavailable`**（P0-4 未就绪）；
  - **401 / 403 → `unauthorized`**（无权限）；
  - **网络错误 → `error`**（连接失败）；
  - 2xx → `ready`；未探测 → `unknown`。
- 每种状态**独立文案与处置**，不得一律显示「P0-4 未就绪」：
  - unavailable：只读演示 + 只本地草稿；unauthorized：提示联系管理员授权；error：提示重试/检查网络；ready：启用「保存并提交核验」。

---

## 9. 表单状态模型（`state.quotaCompose`）
```text
state.quotaCompose = {
  open, action: 'new_boq_standard'|'new_set'|'add_volume'|'add_file',
  materialType,                       // §4 六值
  systemType,                         // construction_regional | industry_specialty | null(清单规范)
  path,                               // {jurisdiction_level,jurisdiction_code} | {industry_sector_code}
  editionYear,
  set: { title, edition_label, issuer_name, effective_date, publish_date,
         legal_status, standard_or_quota_code, expected_volume_count },
  boq: { title, standard_or_quota_code, edition_label, scope, files:[...] },  // 清单规范
  relations: [ { target_publication_set_id, relation_type } ],               // §4 关联
  targetSetId, targetArchiveId,
  volumes: [ { tempId, discipline_code|discipline_label, volume_title,
               document_role, files:[ {tempId, file|null, meta, role} ] } ],
  draftId, errors, submitting,
  quotaApi                            // §8 枚举
}
```
- 与 `state.manualUpload` 完全独立，互不读写。

---

## 10. 分动作校验（SPEC §10.6 错误码）
- **A `new_set`**：提交核验必须**至少一个分册**，且每分册恰好一个 `main_document`；缺则仅可存草稿（`PRIMARY_FILE_REQUIRED`）。
- **B `add_volume`**：`volume_title` 必填；`discipline` 必须为**受控 code 或 `general`**（未命中字典只能存 `discipline_label_candidate`，不能提交为正式 code）。
- **C `add_file`**：校验**目标档案现有文件 + 本次新增文件合计恰好一个 `main_document`**——**不要求本次必须新增主文件**（既有已有主件时只追加附件也合法）。
- **清单规范 `new_boq_standard`**：单独校验**主文件 + 标准号/版本（standard_or_quota_code/edition_label）+ 适用范围（scope）**。
- 通用：缺关键字段 → 禁用「保存并提交核验」，仅「保存/保存本地草稿」。

---

## 11. 对其他域的隔离
- quota 主页面与 compose 仅在 `state.domain==='quota'` 渲染；切域立即清空 `state.quotaCompose` 并复原其他域视图。
- 「新增档案」按 domain 分派：quota→四入口下拉；其余域→原 `openManualUploadDialog()` 不变。
- 独立 DOM `#quotaComposeModal`，不复用 `#manualUploadModal`；CSS 用 `quota-` 前缀。
- 汇总条/健康条/页签/空状态均为 quota 分支渲染，不改其他域的 `#storageAuditBar`、`#domainPill`、tabs。

---

## 12. 测试与验收（修正测试边界）
**测试依赖约束**
- 现有前端测试方式 = `subprocess.run(["node","-e", ...])` 执行 `app.js` 抽取的**纯函数**（见 `tests/test_ui_filter_behavior.py`）。仓库**无 Playwright/无 DOM 测试框架**用于 UI。
- **不引入任何新测试依赖**（不加 Playwright/jsdom）。

**可用 `node -e` 纯函数单测（P0-5A）**
- 级联筛选：`buildQuotaFacetQuery` 一级切换清空对侧、年份取自 facets 而非静态；
- 校验：`validateQuotaCompose` 四动作分支（§10），单分册单主、add_file 合计单主、boq 标准号/范围；
- 草稿：`serializeQuotaDraft/deserializeQuotaDraft` 不含 File、key 含 schema/tenant/user、反序列化标记「需重新选择」；
- 能力探测：`detectQuotaApiState` 对 404/401/403/网络错误/2xx 的映射；
- 入口：四动作枚举与 material_type/relation 展开逻辑。

**DOM 级交互（键盘/Esc 关闭/焦点回收/长表单滚动）**
- 无 DOM 测试框架，故以**手动 + 截图**验收，并把可判定逻辑（如 Esc→close 的处理器映射、关闭后焦点回到触发按钮的目标计算）抽为纯函数用 `node -e` 覆盖；不为此引入浏览器自动化（如需 Playwright 需先确认仓库已具备并单独批准）。

**回归**
- cost_info/其他域三弹窗与主页面逐项不变（截图对比）；
- quota compose 期间**不请求** `/api/archives/from-ingest-event`（断言）。

**截图**：四入口各 1、两路径各 1、单分册单主校验 1、add_file 合计单主 1、footer 三态/能力探测四态、空状态「前往待归档」、其他域回归。

**交付拆分**
- **P0-5A**：主页面结构 + 级联筛选 + 四入口 + compose 结构 + 本地草稿 + 校验 + 能力探测（**无真实写入**）；
- **P0-5B**：P0-4（尤其 `POST /compose`）就绪后的真实 API 接线与事务提交。

---

> 本计划为 `PLAN_ONLY`。请 ChatGPT 审核 v0.2；通过后先做 P0-5A（可与 P0-3.5 并行），P0-5B 待 P0-4 就绪。
