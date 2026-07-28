# 四川档案"任意省份都能匹配"问题 — 评估与修复方案

> **状态**：评估完成 · 修复未动手 · 等待用户批准
> **关联 SPEC**：[quota/web-frontend/SPEC.md](SPEC.md) §3.1.2 筛选交互
> **关联代码**：[quota-ui.js:1213-1240](../file_asset_service/app/ui/quota-ui.js#L1213-L1240)、[main.py:998-1041](../file_asset_service/app/main.py#L998-L1041)、[schemas.py:333-366](../file_asset_service/app/schemas.py#L333-L366)
> **DB 实测**：用 `file-asset` 环境的 `psycopg` 直连共享 PostgreSQL（host=`.env` 中 `172.16.20.26:5433`）

---

## 1. 症状复现

数据库现有 1 条 quota 档案：
- **title**：`四川省房屋建筑工程清单计价定额—房屋建筑工程`
- **archive.region_code** = `'510000'`（四川，**已正确落库**）
- **publication_set.jurisdiction_code** = `'510000'`（**已正确落库**）
- **publication_set.quota_system_type** = `'construction_regional'`
- **publication_set.edition_year** = 2026
- **status** = `'pending_tag'`，`file_count` = 0（只有档案壳，尚未上传 PDF）

用户观察：**前端选任意省份 chip，这条档案都会出现**。看起来"它没省份字段"，实际不是。

---

## 2. 根本原因（不是数据问题，是端点选型问题）

[quota-ui.js:1216-1240](../file_asset_service/app/ui/quota-ui.js#L1216-L1240) 的 `loadQuotaArchivesGeneric()`：

```js
const response = await global.fetch("/api/archives?domain_type=quota&limit=500", {
```

**硬编码只传 `domain_type=quota&limit=500`，没有任何 province / year / edition / system_type 筛选参数**。

`reloadArchives()`（[quota-ui.js:1304-1314](../file_asset_service/app/ui/quota-ui.js#L1304-L1314)）也走同一个函数，所以"切换省份 chip → 重载列表"实际上是"重新拉全量 500 条，JS 端不筛选"。

注释（[quota-ui.js:1213-1215](../file_asset_service/app/ui/quota-ui.js#L1213-L1215)）里写了为什么走这个端点：

> "切换到通用 `/api/archives?domain_type=quota`：响应是裸数组 list[ArchiveSummaryResponse]，**含 primary_file{file_id,file_name,file_role}（预览所需）**，旧 `/api/data-lake/quota/archives` 无此字段。"

—— 这是**两个端点的取舍失衡**：

| 端点 | 优势 | 缺失能力 |
|---|---|---|
| `/api/data-lake/quota/archives` | 接受 `primary` / `jurisdiction_code` / `edition_year` / `edition_label` / `discipline_code` / `q` 全套筛选（已 JOIN `QuotaArchiveProfile` + `QuotaPublicationSet`） | 响应**不返回** `primary_file`（预览按钮要的字段） |
| `/api/archives?domain_type=quota` | 响应是裸数组，**含 `region_code` + `primary_file`** | **不支持** primary / year / edition / system_type 筛选（[main.py:998-1011](../file_asset_service/app/main.py#L998-L1011) 仅认 `domain_type`/`status`/`region_code`/`channel_type`/`tenant_code`/`source_id`/`search`） |

**前任开发者选了"有 primary_file"那一端**，代价是筛选失效。

---

## 3. 信息价档案台"按省分类"机制 — 我们能仿造吗

### 3.1 信息价档案台现状（读了 `chongqing_cost_info.py`、`national_cost_info_regions.py` 后）

| 维度 | 信息价档案台 |
|---|---|
| 省份字段落点 | `Archive.region_code`（nullable=True，indexed）— **直接落档案主表** |
| 模块组织 | `*_cost_info.py` 每省一份（chongqing / sichuan / hubei …） |
| 元数据来源 | `national_cost_info_regions.py` 从 CSV 加载 `province_code` + `target_region_code` + `target_level`（province / prefecture / municipality） |
| 入库键 | `_business_key_for_row(*, source_id, region_code, row)` — 用 `region_code` 拼 business key |
| 31 省 + 计划单列市 + 兵团 | 都支持（CSV 中 explicit 列出） |

### 3.2 定额档案台已有什么

| 维度 | 定额档案台（现状） |
|---|---|
| 省份字段落点 | `QuotaPublicationSet.jurisdiction_code`（String(32), **NOT NULL**, indexed）— 经 `QuotaArchiveProfile` 桥接到 `Archive` |
| 模块组织 | 不按省拆模块（业务上无必要；定额按"专业 + 体系类型"组织，与省份正交） |
| 元数据来源 | `_UPLOAD_PROVINCE_MAP`（[quota_api.py:1074](../file_asset_service/app/quota_api.py#L1074)）：`province → (jurisdiction_code, jurisdiction_label, default_profile, jurisdiction_level)`，31 省 + 5 计划单列市 + 兵团 |
| 入库键 | `archive.business_key` 由 quota_api 在 create 时拼（含 `publication_set.jurisdiction_code`） |
| DB 实测 | 四川档案的 `pubset.jurisdiction_code='510000'` 已**正确落库** |

### 3.3 结论：能仿造，但没必要

✅ **数据模型已具备按省分类的所有条件**：
1. `QuotaPublicationSet.jurisdiction_code` 已经在 DB 里存好（实测 `510000`）
2. `QuotaArchiveProfile` 已经把 Archive 关联到 PubSet
3. `_UPLOAD_PROVINCE_MAP` 已经管了 31+6 个行政区
4. 后端 `/api/data-lake/quota/archives` 已经支持 `jurisdiction_code=` query 参数

❌ **真正缺的是"端点响应"和"前端调用"**，不是 schema。

我们不必学信息价把 `region_code` 复制到 `Archive.region_code` —— 那样会破坏规范化（同一份档案的省份已经存在 PubSet 上）。**正确的修法是让前端调对的端点 + 让端点响应带上 `primary_file`**。

---

## 4. 修复方案（三选一）

### 方案 A · 临时：JS 端按 `archive.region_code` 过滤（**仅省份，年份/版次仍失效**）

`/api/archives?domain_type=quota` 已接受 `region_code=` query 参数，且响应里 `region_code` 字段也有。所以前端只需要把 `currentArchiveFilters().jurisdiction_code` 拼到 URL 即可。

**改动**：[quota-ui.js:1221](../file_asset_service/app/ui/quota-ui.js#L1221)
```js
// 旧
const response = await global.fetch("/api/archives?domain_type=quota&limit=500", {

// 新
var params = new URLSearchParams({ domain_type: "quota", limit: "500" });
var fx = currentArchiveFilters();
if (fx.jurisdiction_code) params.set("region_code", fx.jurisdiction_code);
if (fx.primary) params.set("channel_type", ...);  // 但 channel_type 不是 primary
const response = await global.fetch("/api/archives?" + params.toString(), {
```

**限制**：`/api/archives` 不接受 `primary` / `edition_year` / `edition_label` —— **年份 chip 切换仍无效**。

**适用**：1 天内能修好的最小变更，但**只能修省份，不能修年份**。

### 方案 B · 给 `/api/archives` 透传 province + year + edition（**通用，但侵入共享端点**）

扩展 [main.py:998-1011](../file_asset_service/app/main.py#L998-L1011)：

- 新增 query 参数 `primary` / `jurisdiction_code` / `edition_year` / `edition_label` / `quota_system_type` / `material_type`
- 当 `domain_type='quota'` 时，**条件性 JOIN** `quota_archive_profile` + `quota_publication_set` 并应用 where
- 让 `list_archives()` / `count_archives()` 在共享路径下分支

**风险**：`/api/archives` 是跨 domain 的共享端点（cost_info / quota 都用）。把 quota 专属的 Profile/PubSet JOIN 写到共享 service 里，会污染共用代码。需要在 service 层加 `if domain_type=='quota':` 分支，但仍然紧耦合。

**适用**：如果未来 cost_info 也要按省筛选，且共用 service 重构时一起做。**当前不建议**——这是"把 quota 私货塞进共享 service"。

### 方案 C · 推荐：让 `/api/data-lake/quota/archives` 响应补 `primary_file`，前端切回专用端点 ✅

**后端改动**（[quota_api.py:300-340](../file_asset_service/app/quota_api.py#L300-L340)）：

1. 给 stmt 增加一个 LEFT JOIN：
   ```python
   .outerjoin(ArchiveFile, and_(ArchiveFile.archive_id == Archive.archive_id, ArchiveFile.is_primary == True))
   .add_columns(ArchiveFile.file_id, ArchiveFile.file_name, ArchiveFile.file_role, ArchiveFile.mirror_status, ArchiveFile.mirror_relative_path, ArchiveFile.mirror_path, ArchiveFile.mirror_size)
   ```
2. 把 row 转成 `ArchiveSummaryResponse`（已有 schema，含 `primary_file`）。
3. 响应 schema 改成 `list[ArchiveSummaryResponse]`（与 `/api/archives` 对齐）。

**前端改动**（[quota-ui.js](../file_asset_service/app/ui/quota-ui.js)）：

1. **删除** `loadQuotaArchivesGeneric()` 函数（L1216-1240）
2. `load()` 和 `reloadArchives()` 改回 `state.api.getArchives(currentArchiveFilters())`
3. （`state.api.getArchives` 已存在，对应 `/api/data-lake/quota/archives`，已支持全 filter）

**验证**：
- 选「建筑工程定额 → 四川」→ 只显示 jurisdiction_code='510000' 的档案
- 切「建筑工程定额 → 江苏」→ 切到 320000
- 选「全部 → 2026」→ year=2026 的所有省档案
- 预览按钮正常工作（因 `primary_file` 在响应里）

**工作量**：后端 ~50 行（JOIN + projection），前端 ~20 行（删 + 改）。约 1 个迭代。

**优势**：
- **不污染共享端点**
- 复用 SPEC §3.1.2 既定的 5 维度筛选矩阵（primary / province / year / edition / discipline）
- 复用 `currentArchiveFilters()`（已写好、已传全部参数）

---

## 5. 推荐与下一步

**推荐方案 C**。理由：
1. 后端改动小、隔离性好（不影响 cost_info 共享端点）
2. 前端改动小、可逆
3. 完全契合 SPEC 既定的"按维度分流"设计

**实施步骤**（等待用户批准后启动）：
1. 后端：在 `/api/data-lake/quota/archives` 加 `ArchiveFile` LEFT JOIN（`is_primary=True`），返回 `ArchiveSummaryResponse` 列表
2. 前端：删 `loadQuotaArchivesGeneric()`，`load()` / `reloadArchives()` 改走 `state.api.getArchives(currentArchiveFilters())`
3. 验证：5 个省份各点一次 + 「建筑工程定额 → 全部 → 2026」+ 「专业工程定额 → 全部」三种场景

**预估风险**：
- 若后端 ArchiveSummaryResponse 序列化时 `primary_file` 字段在 quota 端点上不出现（schema 限制），改 schema 即可——`ArchiveSummaryResponse` 已经声明 `primary_file` 字段，pydantic 会按字段 drop 不存在的属性
- 列表响应从 `{items:[...]}` 变 `list[...]`，前端 `state.archives.data` 取值要相应调整（已经是数组，因为 `loadQuotaArchivesGeneric()` 已经做了 `Array.isArray(data) ? data : ((data && data.items) || [])` 兼容）

---

## 6. 不推荐的方案（写明避免越界）

| 方案 | 否决理由 |
|---|---|
| 把 `region_code` 从 PubSet 复制到 `Archive.region_code` | 破坏规范化（冗余字段）；且 PubSet 已存 |
| 新建一个"省份-档案"映射表 | 冗余；PubSet + Profile 桥接已经够用 |
| 把 `/api/archives` 改 domain-specific | 共享端点被 quota 绑架，cost_info 跟着改 |
| 等数据真实入了再修 | 不需要等数据；纯端点修复，无关样本数 |