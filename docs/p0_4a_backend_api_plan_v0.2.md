# P0-4A 后端 API 首期实施计划 v0.2

**状态：`PLAN_ONLY`**（审核通过前不开发）
**取代**：`p0_4a_backend_api_plan_v0.1.md`

---

## 0. 版本变更说明（v0.1 → v0.2）

| # | v0.1 问题 | v0.2 修正 |
| --- | --- | --- |
| 1 | 行政区划用 quota 专属静态清单 | 改为公共 `administrative_division` 表 + 公共端点 |
| 2 | capabilities 在后端制造"五态" | 后端只返回 `ready` / `unavailable` 两种确定性状态 |
| 3 | stats 口径模糊 | 冻结所有字段定义 + SQL 聚合规则 |
| 4 | archives 简单代理通用接口 | JOIN Archive→Profile→PublicationSet→ArchiveFile |
| 5 | facets 未明确三条路径 | 清单规范/建筑工程/专业工程三条路径全覆盖 |
| 6 | reconciliation 只返回 counts | 补齐分页/字段/来源/置信度/重复信息 |
| 7 | 测试仅"每端点一例" | 11 类测试场景明细 |
| 8 | 未披露 Legacy Blob 阻塞 | 明确标注 `init_db()` 阻塞风险 |

---

## 1. 架构变更：行政区划上升为公共能力

### 1.1 为什么不能在 quota 下硬编码

- 信息价（`cost_info`）、招投标（`trading`）、公共资源等域同样需要行政区划
- 地市级定额（如"成都市建设工程计价定额"）需要完整地市列表
- "31 省 + 省会/计划单列市"无法覆盖全国地市数据需求

### 1.2 公共表设计

```text
administrative_division
├── code         VARCHAR(6)  PK    e.g. "510100"
├── name         VARCHAR(64)       e.g. "成都市"
├── level        VARCHAR(16)       country | province | city
├── parent_code  VARCHAR(6)  FK    self-reference, e.g. "510000"
├── enabled      BOOLEAN    DEFAULT TRUE
├── version      VARCHAR(32)       e.g. "GB/T2260-2024"
├── sort_order   INTEGER    DEFAULT 0
└── UNIQUE(code)
└── INDEX(parent_code, enabled)
```

### 1.3 首版数据范围

| 层级 | 数量 | 说明 |
| --- | --- | --- |
| 全国 | 1 | `CN` |
| 省级 | 31 | 省/自治区/直辖市 |
| 地市级 | ~330 | 城市/地区/自治州/盟（完整，非仅省会） |

**数据来源**：GB/T 2260《中华人民共和国行政区划代码》2024 版。历史撤并地区 `enabled=FALSE`，保留 code 不回收。

### 1.4 公共端点

```text
GET /api/data-lake/administrative-divisions
GET /api/data-lake/administrative-divisions?level=province
GET /api/data-lake/administrative-divisions?parent_code=510000
GET /api/data-lake/administrative-divisions?q=成都
```

响应：

```json
{
  "items": [
    { "code": "510000", "name": "四川省", "level": "province", "parent_code": "CN" },
    { "code": "510100", "name": "成都市", "level": "city", "parent_code": "510000" }
  ]
}
```

### 1.5 quota facets 如何使用

`GET /api/data-lake/quota/facets?primary=construction_regional` 返回的 `jurisdictions` 列表**只包含 `quota_publication_set` 中实际有档案的地区**：

```sql
SELECT DISTINCT p.jurisdiction_code AS code, ad.name AS label, COUNT(*) AS count
FROM quota_publication_set p
LEFT JOIN administrative_division ad ON ad.code = p.jurisdiction_code
WHERE p.jurisdiction_code IS NOT NULL
GROUP BY p.jurisdiction_code, ad.name
ORDER BY ad.sort_order;
```

公共行政字典提供全量，facets 提供实际子集。

---

## 2. capabilities：后端只返回两种确定性状态

### 2.1 设计原则

- `unknown` / `error` / `unauthorized` 是**前端 HTTP 请求的状态**，不是后端能力
- 后端只返回 `ready`（功能可用）或 `unavailable`（尚未实现/不适用）
- 不通过 HTTP self-probe 判定可达性

### 2.2 判定逻辑

| Feature | 判定 |
| --- | --- |
| `stats` | 路由注册 → `ready` |
| `facets` | 路由注册 → `ready` |
| `archives` | 路由注册 → `ready` |
| `reconciliation` | 路由注册 → `ready` |
| `dictionaries` | 路由注册 → `ready` |
| `jurisdictions` | 路由注册 → `ready` |
| `compose` | P0-4B 才实现 → `unavailable` |
| `archiveFiles` | P0-4B 才实现 → `unavailable` |
| `coverage` | P0-4B 才实现 → `unavailable` |
| `publicationSets` | P0-4B 才实现 → `unavailable` |

### 2.3 响应格式

```json
{
  "schema_version": "quota-r1",
  "features": {
    "stats": "ready",
    "facets": "ready",
    "archives": "ready",
    "reconciliation": "ready",
    "dictionaries": "ready",
    "jurisdictions": "ready",
    "compose": "unavailable",
    "archiveFiles": "unavailable",
    "coverage": "unavailable",
    "publicationSets": "unavailable"
  }
}
```

前端 `quota-api.js` 已有的 `probeCapabilities` 逻辑将 `ready`/`unavailable` 作为初始态，`unknown`/`error`/`unauthorized` 仍由前端根据 HTTP 响应状态码生成。

---

## 3. stats：冻结口径

### 3.1 字段定义

| 字段 | SQL 来源 | 备注 |
| --- | --- | --- |
| `systems` | `COUNT(*) FROM quota_publication_set` | Publication Set 总数 |
| `archived` | `COUNT(*) FROM archive WHERE domain_type='quota'` | 已归档档案数 |
| `raw_total` | 两链路 UNION 后 `COUNT(DISTINCT file_asset_id)` | P0-3 口径：`IngestEvent→DataSource.data_domain='quota'` UNION `Archive.domain_type='quota'→ArchiveFile` |
| `accessible` | `quota_storage_audit` 最近一次核验的可访问文件数 | 无核验记录 → `null`，禁止默认等于 `raw_total` |
| `pending` | `COUNT(*) FROM quota_projection_candidate WHERE status='pending'` | |
| `linked` | `COUNT(*) FROM quota_projection_candidate WHERE status='linked'` | |
| `duplicate` | `COUNT(*) FROM quota_projection_candidate WHERE status='duplicate'` | |
| `invalid` | `COUNT(*) FROM quota_projection_candidate WHERE status='invalid'` | |
| `ignored` | `COUNT(*) FROM quota_projection_candidate WHERE status='ignored'` | |
| `storage_audit_at` | `MAX(created_at) FROM storage_audit WHERE scope='quota'` | null if never run |

### 3.2 SQL 聚合规则（不可全表加载后 Python 统计）

- 每个计数使用单独 `SELECT COUNT(*) ...` 查询（避免全表扫描后 Python 统计）
- 或使用单条 SQL 的 `COUNT(CASE WHEN ...)` 聚合——但跨表字段需子查询
- `raw_total` 的 UNION 必须用子查询或 CTE 去重后 `COUNT(DISTINCT)`

### 3.3 响应格式

```json
{
  "systems": 0,
  "archived": 0,
  "raw_total": 8,
  "accessible": null,
  "pending": 8,
  "linked": 0,
  "duplicate": 0,
  "invalid": 0,
  "ignored": 0,
  "storage_audit_at": null
}
```

---

## 4. archives：关联查询（非简单代理）

### 4.1 必须 JOIN 五张表

```sql
SELECT
  a.archive_id, a.title, a.domain_type, a.status,
  a.business_key, a.region_code, a.province,
  qap.volume_title, qap.discipline_code, qap.document_role,
  qap.metadata_status AS profile_metadata_status,
  qap.completeness_score,
  qps.title AS publication_set_title,
  qps.quota_system_type, qps.jurisdiction_code,
  qps.industry_sector_code, qps.edition_year,
  qps.edition_label, qps.metadata_status AS set_metadata_status,
  COUNT(af.file_id) AS file_count
FROM archive a
LEFT JOIN quota_archive_profile qap ON qap.archive_id = a.archive_id
LEFT JOIN quota_publication_set qps ON qps.publication_set_id = qap.publication_set_id
LEFT JOIN archive_file af ON af.archive_id = a.archive_id
WHERE a.domain_type = 'quota'
GROUP BY a.archive_id, qap.volume_title, ...
```

### 4.2 响应字段（对齐前端 `LIST_COLUMNS`）

| 字段 | 来源 |
| --- | --- |
| `archive_id` | `archive.archive_id` |
| `title` | `archive.title` |
| `publication_set_title` | `quota_publication_set.title` |
| `quota_system_type` | `quota_publication_set.quota_system_type` |
| `jurisdiction_code` | `quota_publication_set.jurisdiction_code` |
| `industry_sector_code` | `quota_publication_set.industry_sector_code` |
| `edition_year` | `quota_publication_set.edition_year` |
| `edition_label` | `quota_publication_set.edition_label` |
| `volume_title` | `quota_archive_profile.volume_title` |
| `discipline_code` | `quota_archive_profile.discipline_code` |
| `standard_or_quota_code` | `quota_publication_set.standard_or_quota_code` |
| `file_count` | `COUNT(archive_file.file_id)` |
| `metadata_status` | `quota_archive_profile.metadata_status` |
| `archive_status` | `archive.status` |

### 4.3 过滤支持

| 参数 | SQL |
| --- | --- |
| `q` | `WHERE a.title ILIKE '%' || :q || '%'` |
| `primary=construction_regional` | `WHERE qps.quota_system_type = 'construction_regional'` |
| `primary=boq_standard` | `WHERE qps.material_type = 'boq_standard'` |
| `jurisdiction_code` | `WHERE qps.jurisdiction_code = :code` |
| `edition_year` | `WHERE qps.edition_year = :year` |
| `edition_label` | `WHERE qps.edition_label = :label` |
| `discipline_code` | `WHERE qap.discipline_code = :code` |
| `page` / `page_size` | `LIMIT` / `OFFSET` |
| `sort_by` / `sort_order` | `ORDER BY`（白名单校验：title / edition_year / created_at） |

---

## 5. facets：三条路径全覆盖

### 5.1 按 `?primary=` 参数决定返回维度

| `primary` 值 | 返回 |
| --- | --- |
| `construction_regional` | `jurisdictions` + `years` |
| `industry_specialty` | `industries` + `years` |
| `boq_standard` | `scopes` + `years` |
| `all` / 不传 | 只返回 `years` |

### 5.2 统一项格式

```json
{ "code": "510000", "label": "四川省", "level": "province", "count": 3 }
```

### 5.3 年份和版次

年份从 `quota_publication_set.edition_year` 聚合：

```sql
SELECT edition_year AS value, edition_year AS label, COUNT(*) AS count
FROM quota_publication_set
WHERE edition_year IS NOT NULL
GROUP BY edition_year ORDER BY edition_year DESC
```

版次嵌套在年份的 `editions` 数组中，按需展开：

```sql
SELECT edition_label AS value, edition_label AS label, COUNT(*) AS count
FROM quota_publication_set
WHERE edition_year = :year AND edition_label IS NOT NULL
GROUP BY edition_label
```

**版次必须从正式 `quota_publication_set` 聚合，不能混入 `quota_projection_candidate` 的 `suggested_metadata`。**

### 5.4 完整响应示例

```json
{
  "jurisdictions": [
    { "code": "510000", "label": "四川省", "level": "province", "count": 8 }
  ],
  "industries": [],
  "scopes": [],
  "years": [
    { "value": "2025", "label": "2025", "count": 8, "editions": [
      { "value": "2025版", "label": "2025版", "count": 7 },
      { "value": "第一版", "label": "第一版", "count": 1 }
    ]}
  ],
  "disciplines": [],
  "materialTypes": [],
  "cities": [],
  "issuers": [],
  "metadataStatuses": [],
  "archiveStatuses": [],
  "sourceChannels": [],
  "fileFormats": []
}
```

---

## 6. reconciliation：补齐合同

### 6.1 端点

```
GET /api/data-lake/quota/reconciliation
GET /api/data-lake/quota/reconciliation?projection_status=pending
GET /api/data-lake/quota/reconciliation?page=1&page_size=20
```

### 6.2 响应 items 字段

| 字段 | 来源 |
| --- | --- |
| `candidate_id` | `quota_projection_candidate.candidate_id` |
| `file_id` | `quota_projection_candidate.file_id` |
| `file_name` | `file_asset.original_filename` |
| `file_size` | `file_asset.file_size` |
| `file_hash` | `file_asset.sha256_hex` |
| `file_type` | `file_asset.mime_type` |
| `projection_status` | `quota_projection_candidate.projection_status` |
| `suggested_metadata` | `quota_projection_candidate.suggested_metadata` (JSON) |
| `suggestion_confidence` | `quota_projection_candidate.suggestion_confidence` |
| `matched_rules` | `quota_projection_candidate.matched_rules` (JSON) |
| `duplicate_of_file_id` | `quota_projection_candidate.duplicate_of_file_id` |
| `source_name` | `data_source.name` |
| `source_type` | `data_source.source_type` |
| `ingested_at` | `ingest_event.created_at` |

### 6.3 总览聚合（与 items 同级）

```json
{
  "pending": 8,
  "linked": 0,
  "duplicate": 0,
  "invalid": 0,
  "ignored": 0,
  "total": 8,
  "page": 1,
  "page_size": 20,
  "items": [...]
}
```

### 6.4 P0-4A 只读约束

- 不支持 `PATCH` 处置（`resolved_archive_id` / `resolution_note` 变更）
- 返还 `duplicate_of_file_id` 供前端展示重复关系
- 处置写接口留待 P0-4B

---

## 7. 端点汇总

| # | 方法 | 路径 | 说明 |
| --- | --- | --- | --- |
| — | GET | `/api/data-lake/administrative-divisions` | **公共端点**（非 quota 专属） |
| 1 | GET | `/api/data-lake/quota/capabilities` | 能力探测 |
| 2 | GET | `/api/data-lake/quota/stats` | 统计聚合 |
| 3 | GET | `/api/data-lake/quota/dictionaries` | 受控字典 |
| 4 | GET | `/api/data-lake/quota/facets` | 级联 facets（三路径） |
| 5 | GET | `/api/data-lake/quota/archives` | 档案列表（五表 JOIN） |
| 6 | GET | `/api/data-lake/quota/reconciliation` | 待归档候选列表 |
| — | GET | `/api/data-lake/quota/dictionaries/jurisdictions` | **废弃**（由公共端点取代） |

quota facets 调用公共行政端点获取全量字典，再过滤为实际有数据的子集。

---

## 8. 文件变更清单

### 新增文件

| 文件 | 说明 |
| --- | --- |
| `app/quota_api.py` | Quota router：6 个 quota 端点 + 请求/响应模型 |
| `app/administrative_division.py` | 公共行政区划：数据模型 + seed + API 端点 |

### 修改文件

| 文件 | 变更 |
| --- | --- |
| `app/main.py` | 注册 2 个新 router（quota + administrative-division） |
| `app/database.py` | `migrate_administrative_division_table()` 建表 + seed |
| `app/models.py` | 新增 `AdministrativeDivision` ORM model |
| `app/quota_projection.py` | 新增 `reconciliation_items(session, ...)` 返回详单 |
| `tests/test_quota_api.py` | **新增**：7 端点 pytest（含行政区划） |
| `tests/test_administrative_division.py` | **新增**：行政区划模型/查询/父子关系测试 |

### 不改文件

- `models.py`（quota 部分） — 数据模型已完整
- `quota_taxonomy.py` — 受控字典已定义
- `archive_service.py` — 复用 list_archives 但不代理，改用独立 JOIN
- 所有 UI 文件

---

## 9. 测试计划（11 类覆盖）

| # | 测试场景 | 所属文件 |
| --- | --- | --- |
| T1 | 行政区划 code 唯一、省市父子关系正确（`510100.parent_code==510000`） | `test_administrative_division.py` |
| T2 | 不完整行政区划 seed 失败（orphan parent_code → 事务回滚） | `test_administrative_division.py` |
| T3 | 行政区划端点 level / parent_code 过滤 | `test_administrative_division.py` |
| T4 | capabilities 不误报 `compose`/`coverage` 为 ready | `test_quota_api.py` |
| T5 | stats 多 IngestEvent 同 file → `raw_total` 只计 1（UNION DISTINCT） | `test_quota_api.py` |
| T6 | stats `accessible` 无核验记录 → null | `test_quota_api.py` |
| T7 | facets 三路径各自返回正确的维度集合 + 年份含 editions 数组 | `test_quota_api.py` |
| T8 | archives JOIN 返回 Publication Set / Profile 字段不全 null | `test_quota_api.py` |
| T9 | reconciliation 分页 / 状态筛选 / 返回 FileAsset + DataSource 信息 | `test_quota_api.py` |
| T10 | 空库返回真实 0 / 空数组（不报 500） | `test_quota_api.py` |
| T11 | tenant / visibility 沿用现有权限模型，quota API 不影响其他域端点 | `test_quota_api.py` |

---

## 10. 实施步骤

| 步骤 | 内容 | 依赖 |
| --- | --- | --- |
| B1 | `AdministrativeDivision` model + migration + seed（GB/T 2260） | — |
| B2 | `GET /api/data-lake/administrative-divisions` 端点 | B1 |
| B3 | `quota_api.py`：capabilities / dictionaries / stats 端点（三端点） | — |
| B4 | `quota_api.py`：facets 端点（三路径级联） | B1 (行政区划为准) |
| B5 | `quota_api.py`：archives 端点（五表 JOIN） | — |
| B6 | `quota_projection.py`：`reconciliation_items()` 详单函数 | — |
| B7 | `quota_api.py`：reconciliation 端点 | B6 |
| B8 | `main.py`：注册 router | B1-B7 |
| B9 | `test_administrative_division.py`：T1-T3 | B2 |
| B10 | `test_quota_api.py`：T4-T11 | B8 |
| B11 | 前端对接验证：启动服务 → capabilities／stats／facets 链路 | B8 + seed run |

---

## 11. 已知阻塞：Legacy Blob

**问题**：当前 `init_db()` 在 legacy blob migration 阶段可能因表缺失而失败，导致整个 `create_app(init_schema=True)` 启动失败。

**影响**：P0-4A 可以开发并在**隔离测试**中验证所有 6 个 quota 端点，但在 `init_db()` 恢复正常前：
- 无法在真实环境完成 API 验收
- 不得不使用 `--skip-init` 或独立 DB 启动服务

**处置**：
- P0-4A 的 `migrate_administrative_division_table()` 放在 `init_db()` 中 quota migration 之后、独立于 legacy blob 之前
- 测试使用独立的 SQLite 内存库，不依赖 `init_db()` 全链路
- 真实环境验收阻塞记录在案，不阻塞开发

---

> **本计划为 `PLAN_ONLY`。v0.2 修正了行政区划公共归属、capabilities 二态、stats 口径冻结、archives JOIN、facets 三路径、reconciliation 全字段、测试计划（11 类）和 Legacy Blob 阻塞披露。请审核。**
