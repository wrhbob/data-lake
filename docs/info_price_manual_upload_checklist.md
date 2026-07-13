# 信息价「补录」入口重构 — 实施 Checklist

> 本文件为团队/后续会话可追溯版本（`.claude/plans/` 下的计划是会话本地、不入库）。
> 与实施计划同源。执行顺序为硬约束：**后端任务 1-3 全绿并冻结 API → 前端任务 4 → 手测任务 5**。

## 背景与范围锁定

拆分「补录原件」弹窗（`app/ui/index.html` 的 `#manualUploadModal` + `app/ui/app.js` 的 manual-upload 逻辑），把「新增档案」与「补充文件」两个业务动作分开；支持一个档案挂多文件 + 文件角色；补录文件入湖并排队解析；地区改两级级联；修字段语义/查重体验。

保守边界（已确认）：
- **不动 `build_cost_info_business_key`**（`app/archive_rules.py:174`，被 30+ 爬虫/适配器/测试引用）。手动期次为 `type=month`，存 `YYYY-MM`（已归一化），手动路径无 period 去重漏洞。
- **不做全量键迁移、不改爬虫解析行为。**

## 阻断级执行隐患：channel_type 传参链（命门）

`derive_processors` 新增 `channel_type` 门控，链条必须端到端通，否则 `info_price` 仍返回 `[]`、功能静默失效。

- **前端环（已通）**：`app/ui/app.js:1639` 已在 `source_metadata` 写入 `channel_type: "manual_upload"`。
- **后端环（缺失，命门）**：`register_asset`（`app/assets.py:84`；全仓仅 `app/assets.py:166` 一处调用 `derive_processors`）须从 `source_metadata` 解出 `channel_type` 透传给 `derive_processors`。`main.py:942-957` 当前调用未传 channel_type。
- **额外坑**：前端 `app/ui/app.js:1635` 现强制 `derive_tasks:"false"`、`app.js:1643` 塞 `file_processing:0`；即便门控修好，`derive_tasks=false` 也不会建任务。任务 2 必须删除这两处（端点默认 `derive_tasks=True`，`main.py:930`）。
- **锁死方式**：任务 2 的四条强制断言覆盖全链路。

## 业务规则（开工前钉死）

- **原件角色唯一且置顶**：`is_primary` 的 `main_document`（原件）必须走首个 `from-ingest-event`；封面/附件走 `POST /api/archives/{id}/files`。UI 强制「原件」角色唯一、不可多选、置顶。
- **跨档案查重是一对多**：同一 sha256 可能挂在多个档案。`ingest` 响应返回 `existing_archives: [{archive_id, title}, ...]`（列表），前端文案「已挂在 N 个档案，最近：《…》」，不用单值误导。
- **无源市自动建源是真实写副作用**：`ensureManualUploadSource`（`app/ui/app.js:1591`）会为任意市新建 `DataSource`。UI 对「无源市」加二次确认；治理白名单待用户确认（默认先上二次确认）。

---

## 任务 0 — 落 docs/ 实施 checklist ✅（本文件）

## 任务 1 — cover 文件角色（后端，低风险，先打通链路）

- [x] `app/archive_rules.py:31` `ARCHIVE_FILE_ROLES` 集合加 `"cover"`。
- [x] `app/models.py:347-357` `ck_archive_file_role` CHECK 枚举加 `'cover'`（供全新建库）。
- [x] 约束迁移自动重建：`app/database.py:262` `archive_file_role_check_sql()` 从集合生成 SQL，下次 `init_db()` 生效（`database.py:255-258` drop+recreate）。无需手写迁移。
- [x] 推断逻辑：`infer_archive_file_role`（`app/archive_rules.py:256-324`）最终回退（`archive_rules.py:322` 前）加一条：图片扩展名 `.jpg/.jpeg/.png` 且名称含「封面」→ `"cover"`。

**验收**
- [x] `pytest tests/test_archive_models.py tests/test_database.py tests/test_archive_rules.py -q` 全绿。（52 passed）
- [x] 跑一次 `init_db()`，确认约束 SQL 含 `cover`。（迁移 SQL 与模型 CHECK 两条路径均含 `'cover'`）

## 任务 2 — 手动补录入队（后端，含命门，重点看断言）

- [x] `derive_processors(file_ext, source_type, channel_type=None)`（`app/processors.py:32`）：zip 分支保持最前不动；仅当 `source_type=="info_price"` 且 `channel_type=="manual_upload"` 且 `ext ∈ {.pdf,.xls,.xlsx}` → `["info_price_parse"]`；其余维持现状（`info_price` 仍 `[]`）。
- [x] `register_asset` 加 `channel_type: str | None = None` 参数（`app/assets.py:106`）透传 `derive_processors`（`assets.py:166`）；channel_type 由 `main.py` ingest 端点从 `source_metadata` 取出后显式传入（`main.py:942`，`(metadata_payload or {}).get("channel_type")`）——按护栏 #2，不在 register_asset 内部回落读 source_metadata，以保爬虫回归护栏严密。
- [x] 前端：删除 `app/ui/app.js` 的 `"derive_tasks":"false"` 与 `file_processing:0`（恢复默认 `derive_tasks=true`）。

**验收断言（四条必须全过）**
- [x] a. `manual_upload + info_price + .pdf` → `["info_price_parse"]`
- [x] b. `manual_upload + info_price + .zip` → `["unzip"]`（zip 优先级不被覆盖）
- [x] c. 爬虫 `info_price`（无 channel_type / channel_type="crawler"）→ 仍 `[]`（**回归护栏**）
- [x] d. `register_asset` 走 manual 通道后，DB 实存 `FileProcessing(processor="info_price_parse", status="pending")`；并锁「重复文件不建任务」（`processing_ids == []`）
  - ⚠️ 已用全新 content；重复 content 复用 asset 不建任务（`assets.py:148-170`）。

> 任务 2 相关套件全绿（`test_processor_derivation` + `test_register_asset` + `test_api` = 27 passed；`test_archive_service`/`test_archive_api`/`test_ingest_task_integration`/`test_collection_service` = 51 passed）。
> ⚠️ 附带发现（**与任务 2 无关、既有 Windows 问题**）：`test_file_mirror_api::test_archive_detail_reports_missing_and_mirrored_nas_directory_status` 失败——`file_mirror.py:73` 用 `"/".join` 生成 `mirror_relative_path`（正斜杠），而 `:107/:114` `str(self.root / Path(relative_path))` 在 Windows 输出反斜杠，`mirror_path.endswith(mirror_relative_path)` 因分隔符不一致而断。本期未碰 `file_mirror.py`。待用户定：是否一行改测试为分隔符无关（drive-by）或留作既有。

## 任务 3 — 查重 / attach 响应增强（后端，冻结契约）

- [x] `POST /api/file-assets/ingest`（`app/main.py` ingest 端点）：返回 `existing_archives: [{archive_id, title}, ...]`——join `ArchiveFile→Archive`，按 `ArchiveFile.added_at desc`、按 `archive_id` 去重（同一档案多角色只算一条），最近在首位；**始终返回 list**（新文件/空关联返 `[]` 非 null）。
- [x] `POST /api/archives/from-ingest-event`：service 拆为 `create_archive_from_ingest_event_with_flag → tuple[Archive, bool]`（cost_info attach 分支置 `True`、新建置 `False`），旧 `create_archive_from_ingest_event` 退化为薄包装（5 个 app 调用方+测试零破坏）；端点用 `_with_flag` 取 flag 写入响应。新增独立响应模型 `ArchiveFromIngestEventResponse(attached_to_existing: bool=False)`，不污染通用 `ArchiveDetailResponse`。
- [x] `ArchiveFileAttach` 已支持 `file_role`，`POST /api/archives/{archive_id}/files` 已存在——补充文件流无需新接口（任务 4 直接用）。

**冻结契约（任务 4 前端按此对接）**
- ingest 响应增 `existing_archives: list[{archive_id, title}]`（按 attach 时间倒序，可空）。
- from-ingest-event 响应增 `attached_to_existing: bool`。

**验收**
- [x] 新增 API 测试 `test_ingest_existing_archives_and_attached_to_existing_matrix`（新建/补挂 × 首传/重复 四格 + 空关联返[]）+ `test_existing_archives_one_to_many_most_recent_first`（一对多倒序）。**注**：用户原说"我出矩阵"，为达全绿我先按你画的四格 + 一对多起草，请抽验。
- 回归：`test_archive_api + test_archive_service + test_api + test_trading_registry_runner + test_cost_info_registry_runner` 全绿（70 passed）。`test_quota_registry` 1 失败 `REAL_QUOTA_SAMPLE_MISSING`——找 `/Users/tms/Desktop/...` 真实样本（他机 macOS 路径），进 archive 逻辑前即挂，与本任务无关、既有环境依赖。

> ▎ 任务 1-3 全绿，API 契约已冻结，可进前端任务 4。

## 任务 4 — 前端弹窗三 mode + 多文件角色 + 地区级联

以任务 3 冻结的契约为准。改 `app/ui/index.html` + `app/ui/app.js`：

- [x] **三 mode**：`state.manualUpload.mode ∈ {create, supplement, edit}`；create=新增档案、supplement=补充文件、edit=编辑元数据。`renderManualUploadModal` 按 mode 显隐 `#manualUploadTarget`/`#manualUploadFileSection`/`#manualUploadGrid`。
  - 新增档案：地区级联 + 期次(month) + 发布主体(预填) + 标题 + 文件清单；提交逐文件 ingest → 首个原件 `from-ingest-event`(`manual_denovo`) → 其余 `/archives/{id}/files` 带角色（原件唯一置顶，第二个原件降级为附件）。
  - 补充文件：行内「补充文件」按钮(`supplement-archive`) → 只读带出目标档案 + 选文件/角色；提交逐文件 ingest(`manual_recovery`) → `/archives/{id}/files`；已在档的重复文件自动跳过（避免唯一约束冲突）。
- [x] **地区两级级联**（替换原 `<select id="manualRegionCode">` 为 `#manualRegionCascader`）：省 select + 市 select + 搜索框，数据沿用 `regionTree`；默认市级、不开区县；存码显名 + 小字附码；无源市标「将新建源」+ 提交时 `window.confirm` 二次确认；选完市从 `config.stable.publisher_name` 预填 publisher（空值才填）。搜索仅刷新市列表、保住焦点。
- [x] **多文件 + 角色**：`<input multiple>`；`state.manualUpload.files=[{file,role}]`；每行角色下拉限 原件/封面/附件（不暴露 `priced_source`）；默认封面图→`cover`、其余→`main_document`；原件首个作 primary。
- [x] **查重 UX**：create 时 ingest 返回 `duplicated+existing_archives` → 抛错提示「已挂在《…》，改用补充文件」并阻断；`from-ingest-event` 返回 `attached_to_existing=true` → toast「该期已存在，已作为补充挂入」。
- [x] **字段清理**：删「来源=info_price」输入；`uploaded_by` 去必填改可选（标签「操作人」）；文案改「提交后将入湖并排队解析（状态：待解析）」，edit 提示去 `FileProcessing` 字样；标签去技术化（「价格适用地区」「价格口径」首项「未标注」）。顶栏按钮「补录原件」→「新增档案」。缓存版本号已 bump（`20260710-manual-upload-reflow`）。
- [x] **样式**：`styles.css` 追加 `.region-cascader`/`.upload-file-row`/`.manual-upload-filelist`/`.manual-upload-target-card` 最小样式，复用既有 token。

> ▎ 实现完成，`node --check` 通过、旧引用清零、三流程逐条过脑。**前端无自动化测试，验证依赖任务 5 手测。**

## 任务 5 — 端到端手测（启动 UI 见 memory local-run-setup）

- [ ] 1. 新增档案：省→市级联 + publisher 预填 + 无源标记；上传 1 原件 + 1 封面 → 档案建成，原件 `main_document` primary、封面 `cover`。
- [ ] 2. 重复文件：再传同一原件 → 「已挂在 N 个档案」而非撞库报错。
- [ ] 3. 同期重复：同地区同期再建 → 「已补充到现有档案」（`attached_to_existing`）。
- [ ] 4. 补充文件入口：行内「补充文件」，地区/期次只读带出，上传附件 → 挂入。
- [ ] 5. 排队解析（**直接命中任务 2 命门**）：新增后该档案文件有一条 `info_price_parse/pending`；对比爬虫信息价档案仍无该任务。
- [ ] 6. 地区控件：搜「眉山」定位；选省级码 `XX0000` 也可。

---

## 关键复用点（不要重造）

- 档案级去重 attach：`create_archive_from_ingest_event` cost_info 分支 `archive_service.py:687-720`。
- 挂文件：`attach_file` `archive_service.py:1046` + `POST /api/archives/{id}/files` `main.py:902` + `ArchiveFileAttach` `schemas.py:204`。
- 文件级查重：`register_asset` `assets.py:139-147` + `uq_file_asset_tenant_sha256` `models.py:183`。
- 角色推断/校验：`infer_archive_file_role` / `validate_archive_file_role` `archive_rules.py:256-348`。
- primary 自动裁定：`_apply_representation_roles` `archive_service.py:131`。
- 地区数据：`regionTree` `app.js:303`、`regionParts` `app.js:1269`、`manualUploadRegionOptions` `app.js:1871`。
- provenance wall（L0 禁 extracted）：`archive_rules.py:62,80`——新 metadata cell 一律 `source_level=manual`。

## 明确不在本期（已确认延后，入路线图）

- period 改自由文本 + `business_key` period 归一化（季度/年度/增刊）→ 需全量键迁移。
- `info_price_parse` 真实消费者（解出价格行）。
- 解析价格行的行级适用区县维度。
- `tax_type` 拆「价格口径 + 判定来源」UI、publisher 主数据、`normalized_title` 自动生成、真·按人上传者捕获。

## 风险与回滚

- **最大风险**：任务 2 `derive_processors` 改动可能波及爬虫——已用 `channel_type=="manual_upload"` 严格门控 + 回归断言（验收 c）。回滚 = 还原签名。
- 任务 1 cover：纯增量枚举，回滚 = 移除字符串 + 重跑 `init_db` 重建约束。
- 前端改动集中在 manual-upload 模块，与列表/coverage 视图解耦。
- channel_type 链断（静默失效）靠任务 2 四条断言锁死。
