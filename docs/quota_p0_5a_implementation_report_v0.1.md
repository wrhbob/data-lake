# 清单定额档案台前端 P0-5A 实施报告 v0.1

SPEC: SPEC-QA-001 · 阶段 P0-5A（前端骨架 + 补录表单 + 能力门控，禁止 Mock/写死数据）

## 1. 范围与铁律遵循

本阶段只交付**前端骨架 + 补录（四类新增）表单 + 能力状态门控**，不接入真实 quota 写入（等待 P0-4）。

- **API 前缀冻结**：所有请求走 `/api/data-lake/quota/*`（`quota-api.js` 中 `QUOTA_API_BASE`），未使用旧 `/api/archives`。
- **能力五态**：`unknown / ready / unavailable / unauthorized / error`，禁止用布尔表达能力。`401/403`、`404`、网络错误三者明确区分（见测试）。
- **无 Mock / 无写死**：统计未知一律显示 `—`（非 `0`、非写死 `8`）；无能力区块显示单一原因，不各处堆叠"建设中"。
- **Feature gate**：生产环境忽略 URL `?quotaFlags=`，仅由服务端能力决定；开发环境（localhost/127.0.0.1）允许 URL flag 联调。
- **其他数据域零改动**：`cost_info / trading / policy / construction` 渲染与加载逻辑未改；quota 由独立 `#quotaShell` 接管，`app.js` 仅新增入口调用与守卫。

## 2. 交付物

| 文件 | 说明 |
| --- | --- |
| `file_asset_service/app/ui/quota-api.js` | 能力五态模型、`/api/data-lake/quota` 客户端、能力探测、feature gate（生产禁 URL flag）。 |
| `file_asset_service/app/ui/quota-compose.js` | 四类新增表单状态、字段/单主校验、本地草稿（schema+tenant+user+draftId，不存 File）。 |
| `file_asset_service/app/ui/quota-ui.js` | quota 主页面、专属 Header、四页签、筛选骨架、列表骨架、四项新增菜单、补录弹窗、域分派。 |
| `file_asset_service/app/ui/index.html` | 挂载三个新脚本 + `#quotaShell` / `#quotaComposeModal` / `#quotaToast` 容器；资产版本号升级。 |
| `file_asset_service/app/ui/app.js` | 仅注入：`renderDomain/renderAll/loadCurrentView/loadStorageAudit` 的 quota 守卫 + `setDomain` 中 `QuotaUI.activate/deactivate`。 |
| `file_asset_service/app/ui/styles.css` | 追加 `quota-` 前缀样式（Header/统计条/页签/筛选/分册卡片/文件角色/弹窗/footer）。 |
| `file_asset_service/tests/quota_ui/*.test.js` | node:test 用例（api/compose/ui）。 |
| `file_asset_service/tests/quota_ui/fixtures/capabilities_partial.json` | 能力字段映射 fixture（仅测试目录）。 |
| `file_asset_service/tests/test_quota_ui_frontend.py` | pytest 包装，调用 `node --test` 运行前端用例。 |

## 3. 功能实现要点

### 3.1 专属 Header 与统计
- 标题「清单定额档案台」+ 概览：`— 套资料体系 · — 份已归档 · — 份原件待归档`（未就绪显示 `—`）。
- 统计条（原件/可访问/待归档/重复/异常）来源 `/stats`，未就绪显示 `—` 并给出单一原因。
- 调试胶囊 `Archive API / domain_type: quota` 仅开发环境显示。

### 3.2 四页签（能力门控）
- 档案列表 → `archives`；版本体系 → `publicationSets`；覆盖矩阵 → `coverage`；待归档 → `reconciliation`。
- 能力非 `ready` 时页签 `disabled` 且 `title` 给出原因；待归档在有候选时显示角标计数。

### 3.3 多维筛选骨架
- 第一层：全部 / 清单规范 / 建筑工程定额 / 专业工程定额。
- 级联维度（地区或行业、地市、年份、版次、分册专业）在 `facets` 未就绪时以禁用占位呈现，并注明「待接入 facets」，**不提供静态选项**。

### 3.4 补录（四类新增）
四个入口：新增清单规范 / 新建定额体系 / 向已有体系新增分册 / 向已有档案补充文件。

- **两条定额路径**：`construction_regional`（建筑工程定额：全国/省份/地市）与 `industry_specialty`（专业工程定额：行业体系）。切换路径会清空另一路径的地区/行业、年份、分册专业。
- **体系字段**：`material_type`、`title`、`edition_year`、`edition_label`、`issuer_name`、`effective_date`、`publish_date`、`legal_status`、`standard_or_quota_code`。
- **分册卡片**：可增删；每册字段（名称、`discipline_code`、未命中字典 `discipline_label`）；**每册恰好一个 `main_document`**，出现多个正文时提示「拆分到新分册」，缺正文时提示「缺少正文」——不静默降级。
- **六文件角色**：`main_document / cover / table_of_contents / appendix / release_announcement / other`。
- **关联体系字段**：`material_type ∈ {quota_supplement, quota_explanation, amendment_errata}` 时必须填写 `related_publication_set_id` 与 `relation_type`。
- **目标选择**：新增分册/补充文件的目标需 `publication-sets` / `archives` 查询 API，P0-4 未就绪时显示占位，**禁止静态选项**。

### 3.5 本地草稿
- 草稿键：`quota_compose_draft::<schemaVersion>::<tenant>::<user>::<draftId>`。
- 序列化**仅保留元数据 + 文件名/大小/类型/角色 + 分册归属，剥离 File 对象**。
- 恢复草稿后文件标记 `missingContent`，UI 提示「需重新选择」，并给出统一告警文案。
- `compose` 能力未就绪时：`保存并提交核验` 禁用（附原因），仅允许「保存本地草稿」。

## 4. 域隔离与回归保护
- quota 通过独立 `<main id="quotaShell">` 渲染，进入 quota 时隐藏旧 `main.app-shell`，离开时恢复。
- `app.js` 守卫：`renderAll/loadCurrentView/loadStorageAudit` 在 `domain==="quota"` 时提前返回，避免误发 `/api/archives`、`/api/storage-audit`。
- quota 交互使用 `data-quota-action` / `data-qfield` 等专属属性，且事件监听以 `QuotaUI.active` 守卫，不与旧 `data-action` 冲突。
- 保留 `domainConfigs.quota`（含 `quota_code_raw` 等），既有前端契约测试不受影响。

## 5. 测试与验证

- **node:test（26 例，全部通过）**：
  - `quota-api.test.js`：状态码分类、feature gate（生产禁 URL flag / 开发允许覆盖）、能力探测（404/401/网络错误/fixture 字段映射）。
  - `quota-compose.test.js`：默认值、必填校验、单册单主规则、三态动作、关联字段、草稿键/序列化剥离 File/往返恢复、六文件角色、目标未选报错。
  - `quota-ui.test.js`（DOM stub）：主页渲染统计 `—`、页签禁用+原因、补录弹窗渲染、deactivate 清理；**并新增两条统计门禁守卫**：`statVal` 未就绪一律 `—`、仅 `ready` 才显示真实数字（`0`/`8` 只能在 ready 出现，ready 但字段缺失仍 `—`）；`pendingCount` 未就绪返回 `null`（页签角标不显示），仅 `ready` 返回数字。这两条直接锁定「未 ready 前禁止写死 0/8」的强制修正。
- **pytest 包装通过**：`tests/test_quota_ui_frontend.py::test_quota_ui_node_tests_pass`（沿用仓库 node 子进程方式，无新依赖）。
- **既有契约测试通过**：`test_ui_script_uses_archive_api_and_does_not_define_project_fields`。
- **语法检查**：`node --check` 对 `app.js` 与三个 quota 脚本均通过。

运行命令：
```bash
# 前端单测
node --test file_asset_service/tests/quota_ui/quota-api.test.js \
             file_asset_service/tests/quota_ui/quota-compose.test.js \
             file_asset_service/tests/quota_ui/quota-ui.test.js
# 经 pytest 运行（含既有契约测试）
python -m pytest file_asset_service/tests/test_quota_ui_frontend.py \
  "file_asset_service/tests/test_observation_api.py::test_ui_script_uses_archive_api_and_does_not_define_project_fields" -q
```

## 5A. 四个其他数据域回归结果（本次复核实跑）

- **结论：P0-5A 对其他域零回归。** `test_ui_filter_behavior.py`（cost_info / trading 筛选级联）+ `test_quota_ui_frontend.py` 共 **18 passed**。
- **重要环境注意**：旧 UI 测试 `test_ui_filter_behavior.py` / `test_observation_api.py` 的 `subprocess.run(text=True)` 未显式 `encoding="utf-8"`，在本机 Windows GBK locale 下解码 node 的 UTF-8 中文输出会失败（`stdout=None → TypeError`）。须以 `PYTHONUTF8=1` 运行：
  ```bash
  # Windows PowerShell
  $env:PYTHONUTF8=1; python -m pytest tests/test_ui_filter_behavior.py tests/test_quota_ui_frontend.py -q
  ```
  （`test_quota_ui_frontend.py` 已显式 `encoding="utf-8"`，不受影响。）
- **`test_observation_api.py` 3 项失败与 P0-5A 无关**，均为 **cost_info 既有测试漂移**（该文件仅经 vm 加载 `app.js` 单文件，从不加载 quota-*.js）：
  1. `test_manual_upload_defaults_prefill_from_cost_info_filters`：`manualUploadDefaults` 已不再返回 `price_source_type`（cost_info 变更）；
  2. `test_submit_manual_upload_calls_existing_backend_chain`：`submitManualUpload` 已改为**数组入参**（多文件上传特性），旧用例仍传单文件对象；
  3. `test_coverage_matrix_filter_panel_only_keeps_region_and_city_selector`：覆盖矩阵现含 `period_year` 筛选。
  以上属先前 cost_info 特性演进遗留的 stale 用例，**本阶段不在 P0-5A 范围内修改其他域代码/测试**，仅如实记录待其归口维护。

## 6. 人工走查建议（截图说明）
启动服务后访问 `/ui`，点击左侧导航「清单定额档案台」（`data-domain="quota"`）：

1. **主页**：Header 概览显示 `—`（非 0/8）；统计条给出「P0-4 接口未就绪」原因。建议截图 Header + 统计条。
2. **四页签**：均为禁用态并可 hover 看到原因；档案列表展示空态 + 「前往待归档」按钮。建议截图页签行。
3. **新增菜单**：点击「新增档案」展开四项。建议截图下拉。
4. **补录弹窗**：分别打开「新建定额体系」（含分册卡片、单主校验提示、六角色下拉）与「向已有档案补充文件」（目标占位）。建议截图弹窗与「多个正文/缺少正文」校验提示、footer「保存并提交核验」禁用态。
5. **域回归**：切回信息价/招投标等域，确认列表与筛选照常加载（未受影响）。

## 7. 待 P0-4 接线的后续项
- `/capabilities`、`/stats`、`/facets`、`/archives`、`/reconciliation`、`/publication-sets`、`/coverage` 真实实现后，能力将自动转 `ready` 并点亮对应页签/维度。
- `/compose`、`/archives/{id}/files` 就绪后放开真实提交（当前仅本地草稿）。
- 目标选择器（Publication Set / Archive 查询）接入后替换占位，仍禁止静态选项。
