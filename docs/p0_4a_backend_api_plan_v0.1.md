# P0-4A 后端 API 首期实施计划 v0.1

**状态：`PLAN_ONLY`**（审核通过前不开发）

## 0. 背景

P0-4（quota 后端 API）目前**零实现**。前端 `quota-api.js` 已冻结 9 个端点，全部返回 404。但基础设施已就绪：

| 已就绪 | 状态 |
| --- | --- |
| 5 张 quota 表（models.py）自动建表 | OK |
| `quota_dictionary` 预填 13 行（12 industry_sector + 1 discipline） | OK |
| `quota_seed.py` / `quota_projection.py` CLI 可执行 | OK |
| `archive_rules.py` 含 `build_quota_business_key()` | OK |
| `archive_service.py` 含 CRUD（可复用，domain_type="quota"） | OK |
| 前端 9 个端点定义已冻结 | OK |

**P0-4A 只做 7 项最急迫的后端接口**，其余（compose、coverage、archive-files）留待 P0-4B。

---

## 1. 交付范围（P0-4A）

| # | 端点 | 方法 | 路径 | 说明 |
| --- | --- | --- | --- | --- |
| 1 | 行政区划字典 | GET | `/api/data-lake/quota/dictionaries/jurisdictions` | 省份/城市列表 |
| 2 | capabilities | GET | `/api/data-lake/quota/capabilities` | 能力探测，返回各 feature 状态 |
| 3 | 统计 | GET | `/api/data-lake/quota/stats` | 原件/可访问/待归档/重复/异常计数 |
| 4 | 字典 | GET | `/api/data-lake/quota/dictionaries` | 行业/专业/性质等受控字典 |
| 5 | 级联 facets | GET | `/api/data-lake/quota/facets` | 地区→年份→分册专业，行业→年份→分册专业 |
| 6 | 档案列表 | GET | `/api/data-lake/quota/archives` | quota 域档案分页列表 |
| 7 | 待归档候选 | GET | `/api/data-lake/quota/reconciliation` | 8 条投影候选查询 |

### 不纳入 P0-4A

- `POST /compose` — 事务型提交，依赖完整的校验链和 publication set 创建逻辑，留待 P0-4B
- `GET /publication-sets` — 依赖 publication set 已创建，seed 尚未 run
- `GET /coverage` — 依赖 publication set + archive 均已落库
- `POST /archives/{id}/files` — 可复用现有 `/api/archives/{id}/files`，暂不重复封装

---

## 2. 端点详细设计

### 2.1 行政区划字典 `GET /api/data-lake/quota/dictionaries/jurisdictions`

**用途**：前端"适用地区"下拉的数据源。

**数据来源**：采用**公共静态清单**（硬编码在 Python 模块中），不查 DB。原因：
- 省市行政区划是国家级公开数据，变动频率极低（年为单位）
- 不依赖 seed 是否已跑、DB 是否已有数据
- 前端需要此字典才能打开补录表单，必须零依赖

**响应格式**：

```json
{
  "items": [
    { "code": "110000", "label": "北京市", "level": "province" },
    { "code": "510000", "label": "四川省", "level": "province" },
    { "code": "510100", "label": "成都市", "level": "city" },
    ...
  ]
}
```

**实现**：新建 `quota_jurisdiction.py`，包含 31 省级 + 省会/计划单列市的 `code/label/level` 三元组列表。支持 `?level=province` 过滤。

---

### 2.2 能力探测 `GET /api/data-lake/quota/capabilities`

**用途**：前端 capability 五态模型的真实后端数据源。

**逻辑**：每个 feature 的状态由后端实际能力决定：

| Feature | 判定逻辑 |
| --- | --- |
| `stats` | 数据库可连接 → ready |
| `facets` | 数据库可连接 + 有配额数据 → ready，否则 unavailable |
| `archives` | 复用 `/api/archives?domain_type=quota` 返回 200 → ready |
| `reconciliation` | `quota_projection_candidate` 表存在 → ready |
| `publicationSets` | `quota_publication_set` 表存在 → ready（即使为空） |
| `coverage` | `publicationSets` 就绪且有数据 → ready，否则 unavailable |
| `compose` | 留待 P0-4B → unavailable |
| `archiveFiles` | 留待 P0-4B → unavailable |

**响应格式**（对齐前端 `quota-api.js` `probeCapabilities` 期望）：

```json
{
  "features": {
    "stats": "ready",
    "facets": "unavailable",
    "archives": "ready",
    "reconciliation": "ready",
    "publicationSets": "ready",
    "coverage": "unavailable",
    "compose": "unavailable",
    "archiveFiles": "unavailable"
  }
}
```

**实现**：在 `quota_api.py`（新建）中实现 `get_capabilities()` 函数，通过检查 DB 表存在性 + 现有 archive 端点可达性来决策。

---

### 2.3 统计 `GET /api/data-lake/quota/stats`

**用途**：前端 Header 概览条（"N 套资料体系 · N 份已归档 · N 份原件待归档"）和健康条（原件/可访问/待归档/重复/异常）。

**查询逻辑**：

```sql
-- 原件总数（quota 域所有 file_asset）
SELECT COUNT(DISTINCT fa.file_id)
FROM file_asset fa
JOIN ingest_event ie ON fa.ingest_event_id = ie.ingest_event_id
JOIN data_source ds ON ie.data_source_id = ds.data_source_id
WHERE ds.data_domain = 'quota';

-- 已归档
SELECT COUNT(*) FROM archive WHERE domain_type = 'quota';

-- 待归档（projection_status = 'pending'）
SELECT COUNT(*) FROM quota_projection_candidate WHERE projection_status = 'pending';

-- 重复 / 异常
SELECT projection_status, COUNT(*)
FROM quota_projection_candidate
GROUP BY projection_status;
```

"可访问"（物理文件在 MinIO 中可读）暂返回与"原件"相同值（P0-4A 不接入 storage-audit 全链路），标注 `accessible: null` 让前端显示 `—`。

**响应格式**：

```json
{
  "systems": 0,
  "archived": 0,
  "rawTotal": 8,
  "accessible": null,
  "pendingRaw": 8,
  "duplicate": 0,
  "invalid": 0
}
```

**注意**：`systems` / `archived` 当前返回 0（seed 未跑，projection 未执行），值由实际 DB 查询决定，禁止写死。`pendingRaw` / `duplicate` / `invalid` 只应在 `quota_projection_candidate` 有数据后返回真实值；表为空时返回 0。

---

### 2.4 字典 `GET /api/data-lake/quota/dictionaries`

**用途**：前端"行业分类"下拉、"分册专业"选择、以及其他需要受控字典的地方。

**支持 `?type=` 参数过滤**：

| type 值 | 返回内容 |
| --- | --- |
| `industry_sector` | 12 个行业（水利/电力/电网/铁路/公路/石油/石化/煤炭/光伏/水运港口/有色金属/信息通信） |
| `discipline` | 分册专业列表（当前仅 1 条 "general/单一通册"，实际数据由 projection 产生后追加） |
| 不传 | 返回所有类型 |

**响应格式**（对齐前端 expected schema）：

```json
{
  "items": [
    { "dict_type": "industry_sector", "code": "water_resources", "label": "水利工程定额" },
    { "dict_type": "industry_sector", "code": "power_grid", "label": "电网工程定额" },
    ...
  ]
}
```

**实现**：直接查 `quota_dictionary` 表，`WHERE enabled = true ORDER BY sort_order, label`。

---

### 2.5 级联 facets `GET /api/data-lake/quota/facets`

**用途**：前端筛选区的二级标签数据源。

**级联规则**（对齐前端 `PRIMARY_FILTERS` 的 `secondary` 映射）：

| 一级分类 | 返回维度 | 数据来源 |
| --- | --- | --- |
| `construction_regional` | `jurisdictions` + `years` | `quota_publication_set.jurisdiction_code` + `edition_year` |
| `industry_specialty` | `industries` + `years` | `quota_publication_set.industry_sector_code` + `edition_year` |
| `boq_standard` | `scopes` + `years` | `quota_publication_set.title`（提取适用范围）+ `edition_year` |
| 不传 / `all` | 不返回级联维度 | — |

**响应格式**：

```json
{
  "jurisdictions": [
    { "code": "510000", "label": "四川省" },
    { "code": "110000", "label": "北京市" }
  ],
  "industries": [
    { "code": "power_grid", "label": "电网工程" }
  ],
  "scopes": [],
  "years": [
    { "value": "2025", "label": "2025", "editions": [
      { "value": "2025版", "label": "2025版" }
    ]}
  ],
  "disciplines": [],
  "materialTypes": [
    { "value": "quota_base", "label": "主定额" }
  ],
  "cities": [],
  "issuers": [],
  "metadataStatuses": [],
  "archiveStatuses": [],
  "sourceChannels": [],
  "fileFormats": []
}
```

**年份 → 版次数**：`editions` 数组长度决定前端是否显示版次筛选。

**`?primary=` 参数**：控制返回哪些维度（前端切换一级分类时传入）。

**查询逻辑**：
- 当表为空（种子未跑、尚无 publication set）时返回空数组，前端应显示"待接入"提示而非报错
- 维度的实际值来自 `quota_publication_set` 的 DISTINCT 查询
- `editions` 来自同一年份下 `edition_label` 的去重聚合

---

### 2.6 档案列表 `GET /api/data-lake/quota/archives`

**用途**：档案列表页签的数据源。

**实现**：**复用现有 `GET /api/archives?domain_type=quota`**，封装为 quota 专属端点。

支持分页参数：`?page=1&page_size=20`，响应头包含 `X-Total-Count`。

**响应格式**（reuse `_serialize_archive_base`）：

```json
{
  "items": [
    {
      "archive_id": "...",
      "title": "四川省2025建设工程计价定额——房屋建筑工程",
      "domain_type": "quota",
      "status": "archived",
      "file_count": 1,
      "primary_file_name": "房屋建筑工程.pdf",
      "region_code": "510000",
      "province": "四川省",
      "publication_set_id": "...",
      "volume_title": "房屋建筑工程",
      "discipline_code": null,
      "created_at": "..."
    }
  ],
  "total": 0
}
```

**过滤参数**：

| 参数 | 说明 |
| --- | --- |
| `q` | 全文搜索（标题） |
| `primary` | 一级分类：`all` / `boq_standard` / `construction_regional` / `industry_specialty` |
| `jurisdiction_code` | 地区编码 |
| `industry_sector_code` | 行业编码 |
| `edition_year` | 年份 |
| `edition_label` | 版次 |
| `discipline_code` | 分册专业 |
| `page` / `page_size` | 分页 |
| `sort_by` / `sort_order` | 排序 |

**注意**：当 quota 域尚无 Archive 时返回空列表，不报错。

---

### 2.7 待归档候选 `GET /api/data-lake/quota/reconciliation`

**用途**：待归档页签数据源，展示 projection 候选列表供用户处置。

**数据来源**：`quota_projection_candidate` 表。

**依赖**：
1. `quota_seed.py --apply` 已执行（有 FileAsset / IngestEvent 数据）
2. `quota_projection.py run` 已执行（projection candidates 已生成）

**若以上两项未执行**：返回空列表 + `pending: 0`。

**响应格式**：

```json
{
  "pending": 8,
  "duplicate": 0,
  "invalid": 0,
  "items": [
    {
      "candidate_id": "...",
      "file_id": "...",
      "file_name": "房屋建筑工程.pdf",
      "projection_status": "pending",
      "suggested_metadata": {
        "volume_title": "房屋建筑工程",
        "document_role": "main_volume",
        "discipline_code": null
      },
      "suggestion_confidence": 0.75,
      "matched_rules": ["filename_volume_pattern"],
      "ingested_at": "2025-01-01T00:00:00Z"
    }
  ]
}
```

**分页**：`?page=1&page_size=20`，默认按 `suggestion_confidence DESC`。

---

## 3. 文件变更清单

### 新增文件

| 文件 | 说明 |
| --- | --- |
| `app/quota_api.py` | 7 个端点的路由注册 + 请求/响应模型 |
| `app/quota_jurisdiction.py` | 省市行政区划静态字典（31 省 + 省会/计划单列市） |

### 修改文件

| 文件 | 变更 |
| --- | --- |
| `app/main.py` | 注册 `/api/data-lake/quota` 路由，挂载 `quota_api.router` |
| `app/quota_projection.py` | 补充 `reconciliation_items()` 函数（当前仅有 count 聚合） |
| `tests/test_quota_api.py` | 新增（7 端点的 pytest 用例） |

### 不改文件

- `models.py` — 数据模型已完整
- `database.py` — migration 已就绪
- `quota_taxonomy.py` — 受控字典已定义
- `archive_service.py` — 复用，不修改
- 所有 UI 文件 — 前端不变

---

## 4. 实施步骤

| 步骤 | 内容 | 前置 |
| --- | --- | --- |
| A1 | `quota_jurisdiction.py` — 行政区划静态字典 | — |
| A2 | `quota_api.py` — capabilities + stats + dictionaries + facets + archives（查询骨架） | A1 |
| A3 | `quota_api.py` — reconciliation 候选列表 | A2 |
| A4 | `main.py` — 注册 quota router | A2 |
| A5 | `tests/test_quota_api.py` — 7 端点测试（含空表/有数据场景） | A4 |
| A6 | 对接前端：启动服务，访问 `/ui`，验证 capabilities → stats → archives reconciliation 链路 | A5 |

每步完成后运行 `pytest` + `node --test` 确认不回归。

---

## 5. 技术约束

| 约束 | 说明 |
| --- | --- |
| **不硬编码数据** | 统计数字只来自 DB 查询；字典从 `quota_dictionary` 查；行政区划用静态清单但标注来源 |
| **空表友好** | 实体表为空时返回空列表 + 零计数，不报 500 |
| **content-type** | `application/json; charset=utf-8` |
| **错误格式** | 沿用现有 `archive_http_error` 模式（`raise ValueError` → `HTTPException` 映射） |
| **DB 会话** | FastAPI `Depends(get_db_session)`，无连接池泄漏 |
| **不引入新依赖** | 只用 FastAPI + SQLAlchemy（已在 requirements.txt 中） |
| **不修改前端** | 响应格式对齐前端 `quota-api.js` 已冻结的 schema |

---

> **本计划为 `PLAN_ONLY`。P0-4A 优先覆盖 capabilities / stats / dictionaries / jurisdictions / facets / 档案列表 / 待归档候选 7 项。审核通过后按 A1→A6 顺序实施。**
