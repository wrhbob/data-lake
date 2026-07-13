# SPEC-QA-001 · 清单定额档案台 V0.1-R1

> 文档状态：**FROZEN / IMPLEMENTATION-READY**  
> 规格负责人：ChatGPT  
> 实施负责人：Cascade  
> 日期：2026-07-11  
> 目标路径：`docs/quota_archive_spec_v0.1.md`  
> 适用范围：数据湖 `domain_type = quota`  
> R1 修订：区分“建筑工程定额的地区轴”与“专业工程定额的行业轴”，年份改为依赖分类的动态维度  

---

## 0. 执行规则

### 0.1 文档权威性

本文件是清单定额档案台 V0.1-R1 的唯一产品与业务规格。Cascade 必须：

1. 用本文件**整文件覆盖**此前自行创建的同名文档；
2. 不擅自补写、扩展或改变业务边界；
3. 可依据现有仓库约定调整表名、类名、路由前缀和目录，但不得改变本文定义的实体语义、状态语义、交互结果和验收标准；
4. 若现有统一 Archive 模型与本文存在实质冲突，停止相关实现，输出冲突清单，不得自行建立平行模型绕过；
5. 可另建《实施报告》《迁移报告》《测试报告》，但不得把实施判断反写成本规格的新需求。

本文使用以下约束词：

- **MUST**：必须实现；
- **MUST NOT**：禁止实现；
- **SHOULD**：原则上应实现，若受现有架构限制需在实施报告说明；
- **MAY**：可选优化，不属于 V0.1 验收阻断项。

### 0.2 规格优先级

发生冲突时按以下顺序处理：

1. 本 SPEC 的业务边界、数据语义和验收标准；
2. 现有数据湖统一 Archive、审计、权限、错误码等公共契约；
3. 现有仓库技术与命名规范；
4. 当前页面和历史 Mock 行为。

当前页面仅是参考现状，不构成高于本 SPEC 的产品契约。

### 0.3 P0-1 现状摸底裁决

根据 Cascade 的 P0-1 报告，正式裁决如下：

1. **统一 Archive、ArchiveFile、ArchiveEvent、FileAsset、Blob、IngestEvent、Outbox、AuditLog、field_sources、预览/下载能力全部复用**，不得新建平行底座；
2. **C1 状态词表不构成阻断**：按 §8.1 映射现有公共状态，不新增 DRAFT/REVIEW/ARCHIVED 同义值；
3. **C2 的“2058 quota 原件”假设作废**：2058 是全域 FileAsset 统计，quota inventory 必须重新按领域归属建立；
4. `quota/四川2025` 下已定位的八个 PDF 被确定为首批 seed batch，可通过现有公共入湖链路复制入 MinIO；不得移动、覆盖或删除源文件；
5. GB 50500-2013/2024 原件未定位，不得伪造主文件或把元数据空壳标成已归档；
6. Cascade 在应用本 R1 分类字段后可继续 P0-2；P0-3 按 §9 的新 inventory 定义执行。

---

## 1. 背景与问题定义

当前“清单定额档案台”显示：

- NAS 原件：2058 / 2058；
- 文件可访问率：100%；
- 清单定额档案：0 份。

P0-1 已确认：上述 2058 是 `storage-audit` 返回的**全域 FileAsset 总数**，并非 `domain_type = quota` 的原件数；在 quota 页面展示该数属于统计口径错误。当前仓库中 quota 域 FileAsset / Archive 基本为 0，已定位的首批候选源文件为工作区 `quota/四川2025` 下 8 个 PDF，尚未进入 MinIO / FileAsset。

因此 V0.1 的核心任务不是围绕2058伪造 quota 档案，而是先建立 quota 专属 inventory，再把真实原件转换为可检索、可预览、可补录、可追溯的业务档案。

必须坚持：

> Raw Object 不等于 Archive；文件可访问不等于档案完整；L0 已入湖不等于已经语义化或可用于智能组价。

---

## 2. V0.1 目标与非目标

### 2.1 必须达成的目标

V0.1 MUST 完成：

1. 建立 `domain_type = quota` 的真实原件 inventory；以已定位的四川2025八个 PDF 作为首批 seed batch，后续新增原件动态纳入；
2. 建立清单定额资料的“资料体系 → 分册档案 → 物理文件”三级关系；
3. 建立原件到档案的可重复、可审计投影流程；
4. 支持档案新增、向已有档案补充文件、元数据补录和人工核验；
5. 支持按资料类型、定额体系、地区/行业分类、年份、分册专业、版本、发布单位、状态检索；
6. 支持文件列表、预览、下载权限复用、来源与审计追溯；
7. 支持清单规范与定额资料两种业务视图；
8. 支持版本体系与覆盖矩阵；
9. 跑通清单规范、建筑工程定额、专业工程定额三条真实样本链路；
10. 为后续解析/语义加工发出稳定的“档案已归档”事件，但 V0.1 不实现下游加工。

### 2.2 明确禁止的范围

V0.1 MUST NOT 实现：

- PDF 内容解析、OCR、表格抽取；
- 清单条目结构化；
- 定额子目、人材机结构化；
- 清单语义化 Skill；
- 定额语义化 Skill；
- 清单与定额匹配；
- 标准清单库、定额语义索引库；
- 智能组价、套定额、价格查询；
- 向量库、知识图谱、问答；
- 用 LLM 自动决定关键业务元数据；
- 新建一套与统一 Archive 平行的数据湖模型；
- 页面无真实数据时自动回退到 Mock；
- 为了展示效果伪造档案数量、覆盖率或成熟度。

允许预留下游事件和引用字段，但不得在本期展开下游能力。

---

## 3. 业务边界与术语

### 3.1 本域中的“清单”

本台的“清单”仅指规范性资料，例如：

- 国家、行业、地方工程量清单计价标准；
- 工程量计算规范；
- 配套说明、解释、修订和勘误。

以下内容不属于本域：

- 招标工程量清单；
- 控制价清单；
- 历史项目清单；
- CJZ、GBQ、GCFX、Excel 项目文件；
- 项目清单综合单价。

上述项目数据应继续归入公共资源或企业项目数据域。

### 3.2 本域中的“定额”

包括：

- 各省、各年代、各专业定额；
- 定额分册、上下册、章节册；
- 补充定额；
- 定额解释；
- 修订、勘误、发布公告及配套资料。

定额资料必须进一步区分两套组织体系：

1. **建筑工程定额（CONSTRUCTION_REGIONAL）**：以全国通用/省级/地市适用范围为主轴，再按年份组织；选中某省后，只显示该省实际存在的版本年份；
2. **专业工程定额（INDUSTRY_SPECIALTY）**：以行业专业体系为主轴，再按年份组织，例如水利、电力、电网、铁路、公路、石油、石化、煤炭、光伏、水运港口、有色金属、信息通信等。

二者不是同一个“专业”字段的不同取值。建筑工程定额的第二级是**地区**，专业工程定额的第二级是**行业专业体系**；两者下面才可以继续出现分册专业。

### 3.3 核心术语

| 术语 | 定义 |
|---|---|
| Raw Object | NAS / MinIO 中的物理对象，具有 object key、hash、大小、格式和来源信息 |
| Publication Set | 一套有明确适用范围和版本口径的资料体系，例如“四川省2020建设工程工程量清单计价定额” |
| Archive | 可独立描述、检索和预览的业务档案，通常对应某一专业或分册 |
| Archive File | Archive 与 Raw Object 的关联记录，用于表达正文、封面、目录、附录等文件角色 |
| Projection Candidate | 尚未完成业务归档的原件及其候选元数据 |
| Metadata Status | 元数据缺失、部分完整、完整或已核验的状态 |
| Archive Status | 草稿、待核验、已归档、已停用的业务状态 |
| File Health | 文件是否可访问、缺失、损坏或格式不支持 |
| Preview Status | 预览生成是否待处理、可用、失败或不支持 |
| Quota System Type | 定额组织体系：建筑工程定额或专业工程定额 |
| Jurisdiction | 全国、省、市等地理适用范围 |
| Industry Sector | 水利、电网、铁路等行业专业体系，仅专业工程定额使用 |
| Discipline | 资料体系内部的分册专业，例如房屋建筑与装饰、安装、线路、变电等 |

### 3.4 清单与定额的关系

清单规范与定额资料在 L0 共用统一 Archive 底座和页面框架；资料类型、字段模板、覆盖矩阵和后续加工管线分流。

不得把清单规范与定额子目强制塞入同一业务表，也不得在 V0.1 建立“清单编码 → 定额编码”硬映射。

---

## 4. 产品原则

1. **原件永不覆盖**：补录和纠错只改业务元数据，不改 Raw Object 原始字节、hash 和 object key。
2. **一套体系、多份档案、多份文件**：不得继续采用“一份文件就是一套定额”的简化模型。
3. **状态轴分离**：档案状态、元数据状态、文件健康和预览状态不得合并为一个“正常/异常”。
4. **来源可追溯**：自动带出的字段必须记录来源；人工修改必须记录操作者、时间和前后值。
5. **自动建议、人工定案**：文件名和路径规则可以生成候选，但不能直接把不确定结果发布为正式档案。
6. **幂等投影**：同一批原件重复运行投影，不得产生重复 Publication Set、Archive 或关联记录。
7. **真实空状态**：API 无数据就展示真实空状态，不允许页面回退 Mock。
8. **不冒充成熟度**：文件可访问率、元数据完整度、档案覆盖率和后续语义成熟度必须分开展示。

---

## 5. 信息架构

清单定额档案台 V0.1 包含四个一级页签：

1. **档案列表**：按 Archive 浏览和管理；
2. **版本体系**：按 Publication Set → Archive → Archive File 树形浏览；
3. **覆盖矩阵**：分别查看“地区 × 年份”和“行业专业体系 × 年份”的实际覆盖情况；
4. **待归档**：处理 quota 专属 inventory 中原件的归档、补录、重复和异常。

页面标题保留“清单定额档案台”，副标题应明确：

> Layer 0 · Quota Archive · 原件归档与版本体系

### 5.1 顶部指标

必须展示五个相互独立的指标：

- 资料体系数；
- 业务档案数；
- Raw Object 原件数；
- 待归档数；
- 异常数。

quota 页面不得继续展示未按 domain 过滤的“2058/2058”。页面必须分别展示：

- **清单定额原件数**：只统计能通过 DataSource / IngestEvent / Archive 归属链明确归入 `domain_type = quota` 的去重 FileAsset；
- **清单定额文件可访问率**：分母使用同一 quota inventory；
- 全域 FileAsset 健康指标只能出现在数据湖总览，不得混入 quota 页面。

当 quota inventory 尚未建立时显示 0 和明确说明，不得回退全域统计。

---

## 6. 逻辑数据模型

### 6.1 总体关系

```text
Raw Object
    │
    ├── Projection Candidate
    │
    └── Archive File ── Archive ── Quota Archive Profile
                              │
                              └── Publication Set
                                        │
                                        └── Publication Relation
```

实际表名可按仓库规范调整，但逻辑关系必须保留。

### 6.2 统一 Archive 的复用要求

若仓库已有以下能力，MUST 复用：

- Archive 主表；
- Raw Object / Blob / File Object 主表；
- Archive 与文件关联表；
- 事件表、审计表；
- 软删除或停用机制；
- 权限与租户字段；
- 文件预览与下载接口。

清单定额域应采用 1:1 扩展表或 domain metadata，不得复制公共字段建立第二套 Archive 主表。

### 6.3 Publication Set

表示一套有统一版本口径的资料体系。

| 字段 | 必填 | 说明 |
|---|---:|---|
| id | 是 | 稳定主键 |
| biz_key | 是 | 幂等业务键，唯一 |
| publication_family_code | 是 | 跨版本稳定的资料家族编码，例如 `GB_50500` |
| title | 是 | 资料体系标准名称 |
| material_type | 是 | 资料类型枚举 |
| quota_system_type | 条件必填 | 定额资料必填：CONSTRUCTION_REGIONAL / INDUSTRY_SPECIALTY；清单规范可为空 |
| jurisdiction_level | 是 | NATIONAL / PROVINCE / CITY，纯地理维度 |
| jurisdiction_code | 是 | 全国或行政区划受控编码；全国使用 CN |
| industry_sector_code | 条件必填 | INDUSTRY_SPECIALTY 必填；其他类型为空 |
| issuer_name | 是 | 发布单位；确实未知时显式标记 unknown |
| standard_or_quota_code | 否 | 标准号、定额号或发布文号 |
| edition_label | 是 | 如 2013版、2020定额、2024标准 |
| edition_year | 条件必填 | 定额资料必填；用于级联筛选，不代替 edition_label |
| publish_date | 否 | 发布日期 |
| effective_date | 否 | 实施日期 |
| repeal_date | 否 | 废止日期 |
| legal_status | 是 | UNKNOWN / PENDING / EFFECTIVE / REPEALED |
| expected_volume_count | 否 | 已知应有分册数量；未知时必须为 null |
| metadata_status | 是 | MISSING / PARTIAL / COMPLETE / VERIFIED |
| created_by / updated_by | 是 | 操作者 |
| created_at / updated_at | 是 | 时间戳 |

`biz_key` 不得使用文件 hash 代替。建议组成：

```text
quota:{material_type}:{quota_system_type_or_standard}:{jurisdiction_code}:{industry_sector_code_or_na}:{publication_family_code}:{edition_label}
```

若关键字段尚未确定，可使用临时业务键并保持 `metadata_status = MISSING/PARTIAL`；核验后再生成正式业务键，变更过程必须审计。

### 6.4 Archive 与 Quota Archive Profile

Archive 保存公共档案字段；清单定额专属字段放入扩展 Profile。

公共 Archive 至少应表达：

- id；
- domain_type = quota；
- biz_key；
- title；
- archive_status；
- tenant / visibility（若公共模型已有）；
- created_by、updated_by、created_at、updated_at。

Quota Archive Profile 字段：

| 字段 | 必填 | 说明 |
|---|---:|---|
| archive_id | 是 | 关联统一 Archive |
| publication_set_id | 是 | 所属资料体系 |
| document_role | 是 | MAIN_VOLUME / EXPLANATION / AMENDMENT / ERRATA / NOTICE / OTHER |
| discipline_code | 条件必填 | 体系内部的分册专业；单一通册可使用 GENERAL；清单规范可为空 |
| volume_code | 否 | 分册受控编码 |
| volume_title | 条件必填 | 有分册时必须有 |
| part_no | 否 | 上册、下册、第N册等排序值 |
| language | 是 | 默认 zh-CN |
| metadata_status | 是 | 元数据状态 |
| completeness_score | 是 | 0–100，仅用于提示，不代替阻断校验 |
| completeness_blockers | 是 | 缺失的关键字段数组 |
| notes | 否 | 人工说明 |

建议 Archive 业务键：

```text
{publication_set.biz_key}:{discipline_code_or_general}:{volume_code_or_main}:{document_role}
```

### 6.5 Archive File

Archive 与 Raw Object 通过关联实体连接。必须支持一个 Archive 对应多份文件；实现上 SHOULD 使用关联表，以兼容同一合订 PDF 通过页码范围支持多个档案。

| 字段 | 必填 | 说明 |
|---|---:|---|
| id | 是 | 主键 |
| archive_id | 是 | 业务档案 |
| raw_object_id | 是 | 原始对象 |
| file_role | 是 | MAIN_DOCUMENT / COVER / CONTENTS / APPENDIX / ANNOUNCEMENT / OTHER |
| is_primary | 是 | 是否主文件 |
| sequence_no | 是 | 展示顺序 |
| page_range | 否 | 合订文件可用，例如 1-220 |
| link_source | 是 | AUTO_EXACT / MANUAL / IMPORT |
| linked_by / linked_at | 是 | 关联操作者和时间 |

约束：

- 同一 Archive 至少有一个 `MAIN_DOCUMENT` 才能进入“已归档”业务阶段；
- 同一 Archive 最多一个 `is_primary = true`；
- 同一 `(archive_id, raw_object_id, file_role, page_range)` 不得重复；
- 相同 hash 被关联到不同 Archive 时必须警告，但合订文件或公共公告经人工确认后允许关联；
- “附件”是 file_role，不是 material_type。

### 6.6 Projection Candidate

每个进入 quota 域但未完成处理的 Raw Object 必须有唯一候选记录。

| 字段 | 必填 | 说明 |
|---|---:|---|
| id | 是 | 主键 |
| raw_object_id | 是 | 唯一，防止重复候选 |
| projection_status | 是 | PENDING / LINKED / DUPLICATE / INVALID / IGNORED |
| suggested_publication_set_id | 否 | 建议所属体系 |
| suggested_archive_id | 否 | 建议所属档案 |
| suggested_metadata | 是 | 候选字段 JSON |
| suggestion_confidence | 否 | 0–1，仅表示建议强度 |
| matched_rules | 是 | 命中的确定性规则数组 |
| duplicate_of_raw_object_id | 否 | 重复对象指向 |
| resolved_archive_id | 否 | 最终档案 |
| resolution_note | 否 | 重复、忽略、异常必须填写原因 |
| resolved_by / resolved_at | 否 | 人工处理信息 |
| created_at / updated_at | 是 | 时间戳 |

五种 `projection_status` 必须互斥，满足：

```text
原件总数 = PENDING + LINKED + DUPLICATE + INVALID + IGNORED
```

### 6.7 Publication Relation

用于表达资料体系之间的版本和业务关系：

- SUPERSEDES：替代旧版本；
- SUPPLEMENTS：补充；
- EXPLAINS：解释；
- AMENDS：修订；
- CORRECTS：勘误；
- RELATED：一般关联。

关系必须有 source、target、relation_type、evidence/source_ref、created_by、created_at。

### 6.8 字段来源

以下来源类型必须可记录：

- MANUAL_VERIFIED；
- OFFICIAL_SOURCE；
- CRAWLER_DB；
- INGEST_MANIFEST；
- NAS_PATH；
- FILENAME_RULE；
- LEGACY_IMPORT。

若公共审计系统支持字段级审计，应直接复用。否则至少保存：字段名、来源类型、来源引用、置信度、最后修改人和时间。

字段来源优先级：

```text
人工已核验 > 官方来源元数据 > 爬虫数据库 > 入湖清单 > NAS路径 > 文件名规则
```

低优先级来源不得静默覆盖高优先级值。

---

## 7. 枚举与字典

### 7.1 material_type

- BOQ_STANDARD：清单规范；
- QUOTA_BASE：定额库；
- QUOTA_SUPPLEMENT：补充定额；
- QUOTA_EXPLANATION：定额解释；
- AMENDMENT_ERRATA：修订/勘误；
- RELATED_NOTICE：发布公告或关联通知。

### 7.2 quota_system_type

- CONSTRUCTION_REGIONAL：建筑工程定额；
- INDUSTRY_SPECIALTY：专业工程定额。

该字段只描述定额的组织体系，不替代 material_type、jurisdiction、industry_sector 或 discipline。

### 7.3 行业专业体系字典 industry_sector

专业工程定额的第二级分类必须来自后端受控字典。首版至少兼容当前资料中已出现的：

- WATER_RESOURCES：水利工程定额；
- ELECTRIC_POWER：电力工程定额；
- POWER_GRID：电网工程定额；
- RAILWAY：铁路工程定额；
- HIGHWAY：公路工程定额；
- PETROLEUM：石油工程定额；
- PETROCHEMICAL：石化工程定额；
- COAL：煤炭工程定额；
- PHOTOVOLTAIC：光伏发电工程定额；
- WATER_TRANSPORT_PORT：水运港口工程定额；
- NONFERROUS_METALS：有色金属工业定额；
- INFORMATION_COMMUNICATION：信息通信工程定额；
- OTHER：其他专业工程定额。

这些 code 是“行业专业体系”，不是分册专业。UI 标签和启用状态来自字典，实际可用年份来自真实 Publication Set 聚合，不得在前端硬编码。

### 7.4 分册专业字典 discipline

`discipline_code` 表示某套定额内部的专业或分册类别。例如：

- 建筑工程定额：房屋建筑与装饰、通用安装、市政、园林绿化、仿古建筑、房屋修缮等；
- 电网工程定额：变电、架空线路、电缆、调试等实际存在的分册；
- 单一通册：GENERAL。

discipline 必须来自后端受控字典，不得与 industry_sector 共用同一 code 字段。

### 7.5 地理适用范围 jurisdiction

UI 使用“适用范围”，但存储必须拆为纯地理字段：

- NATIONAL：全国，jurisdiction_code = CN；
- PROVINCE：省级；
- CITY：地市级。

`INDUSTRY` 不属于地理层级，不得继续作为 jurisdiction_level；行业维度应写入 `industry_sector_code`。省市必须使用统一行政区划编码并级联，不得仅保存中文名称。

### 7.6 两条分类路径

建筑工程定额：

```text
CONSTRUCTION_REGIONAL → 全国/省/市 → 年份 → 分册专业 → 分册/文件
```

专业工程定额：

```text
INDUSTRY_SPECIALTY → 行业专业体系 → 年份 → 分册专业 → 分册/文件
```

年份是依赖上一级分类的动态 facet：

- 选择四川定额，只返回四川实际存在的年份；
- 选择水利工程定额，只返回水利实际存在的年份；
- 选择电网工程定额，只返回电网实际存在的年份；
- 不得维护一个对所有分类共用的静态年份数组；
- 同一年存在多个修订版时，不得合并成同一 Publication Set，列表中继续使用 edition_label 区分。

---

## 8. 状态机

### 8.1 Archive Status 与现有仓库状态映射

P0-1 已确认仓库公共状态为：`discovered / collecting / collect_failed / quarantined / collected / pending_tag / archived / ready_for_governance`。V0.1-R1 MUST 复用该公共状态机，不得为了 quota 新增 DRAFT、REVIEW、ARCHIVED 三套同义枚举。

产品语义按下表映射：

| UI业务阶段 | Archive.status | metadata_status | 说明 |
|---|---|---|---|
| 草稿 | pending_tag | MISSING / PARTIAL | 仍在补录 |
| 待核验 | pending_tag | COMPLETE | 关键字段完整，等待人工核验 |
| 已归档 | archived 或现有流程后续的 ready_for_governance | VERIFIED | 正式 L0 档案 |
| 已停用 | 复用公共软停用能力；若仓库确无此能力，只允许增量增加通用 disabled/disabled_at | 保持原值 | 不再默认使用但保留历史引用 |

`collected` 以前的状态仍表示物理采集过程，不应伪装成已形成业务档案。禁止把 `quarantined` 当作“已停用”，二者语义不同。

禁止硬删除已经归档的档案。除确有必要的通用 `disabled` 外，不扩展公共 CHECK 约束加入 quota 专属同义状态。

进入“已归档”业务阶段的最低条件：

1. title、material_type、jurisdiction、edition_label 完整；定额资料同时满足 quota_system_type 及其条件字段；
2. 来源可追溯；
3. 至少一个可访问的 MAIN_DOCUMENT；
4. 定额主册具有 discipline_code，或明确使用 GENERAL；
5. completeness_blockers 为空；
6. 操作人执行核验动作。

### 8.2 Metadata Status

- MISSING：关键业务身份无法确定；
- PARTIAL：可识别但仍缺少字段；
- COMPLETE：关键字段完整，尚未人工核验；
- VERIFIED：已经人工核验。

### 8.3 File Health

- AVAILABLE；
- MISSING；
- DAMAGED；
- UNSUPPORTED。

### 8.4 Preview Status

- PENDING；
- READY；
- FAILED；
- UNSUPPORTED。

页面不得把上述四套状态压缩成一个“状态”颜色。列表可展示主要状态，详情必须分开显示。

---

## 9. 原件投影与对账流程

### 9.1 输入

投影输入的权威口径是：**已经进入统一 FileAsset / IngestEvent，并能通过 DataSource、IngestEvent 或已有关联 Archive 明确归属到 `domain_type = quota` 的去重 Raw Object 快照**。如果 FileAsset 本身没有 domain_type，不得为图省事直接统计全表；必须通过现有归属关系查询，必要时给 IngestEvent/DataSource 增加非破坏性的领域关联。全域 FileAsset 数量、NAS目录文件数或页面未过滤统计均不得作为 quota inventory。

首批 seed batch 的处理规则：

1. 将已定位的 `quota/四川2025` 下 8 个 PDF 通过现有 `quota_registry.ingest_quota_file` / 公共入湖链路复制入 MinIO 并建立 FileAsset、IngestEvent；
2. 保留原始文件名、原路径、sha256、文件大小和 batch_id；
3. 不移动、不覆盖、不删除工作区原件；
4. 入湖成功后才进入 Projection Candidate；
5. 若八个文件存在重复、损坏或一个文件拆出多个派生对象，以实际 FileAsset 快照为准并在报告解释，不硬编码8；
6. GB 50500-2013/2024 原件尚未定位时，样本 A 标记为“等待原件”，不得建立没有主文件却显示 ARCHIVED 的假档案。

投影时还应尽可能联合：

- MinIO object key、hash、格式、大小、上传时间；
- NAS 原始路径；
- 原始文件名；
- 爬虫数据库或采集日志；
- 入湖登记表 / manifest；
- 既有人工补录元数据。

不得修改或搬移原始对象来适配新模型。

### 9.2 流程

1. 扫描 quota 域 Raw Object；
2. 以 hash 做物理重复识别；
3. 合并来源元数据并保留字段来源；
4. 使用确定性规则生成 Publication Set / Archive 候选；
5. 精确命中已有业务键时可自动关联；
6. 仅靠文件名、路径或模糊匹配时只生成建议，不得自动归档；
7. 人工选择：关联已有档案、新建档案、标记重复、标记异常或忽略；
8. 满足条件后归档并发出事件；
9. 输出本轮投影对账报告。

### 9.3 自动化边界

允许自动关联的条件：

- manifest / crawler DB 提供明确 archive biz_key；或
- raw_object 已存在经核验的稳定映射；或
- hash 与已归档文件精确一致，且业务处理为“重复”。

其他情况只允许生成 suggestion。V0.1 禁止调用 LLM 完成归档决策。

### 9.4 幂等性

同一输入重复执行必须满足：

- Raw Object 数不变；
- Projection Candidate 不重复；
- Publication Set 不重复；
- Archive 不重复；
- Archive File 关联不重复；
- 仅新增新的事件记录或更新运行时间；
- 对人工核验字段不得被低优先级规则覆盖。

### 9.5 对账报告

每次运行必须输出：

- raw_total；
- linked；
- pending；
- duplicate；
- invalid；
- ignored；
- newly_created_publication_sets；
- newly_created_archives；
- newly_linked_files；
- rule_errors；
- invariant_check_result。

每次 quota 快照必须满足：

```text
quota_raw_total = pending + linked + duplicate + invalid + ignored
```

2058 只可作为历史全域统计参考，禁止成为 quota 守恒常量。

---

## 10. API 合同

路由前缀可适配仓库规范，以下语义和能力必须保留。建议前缀：

```text
/api/data-lake/quota
```

### 10.1 档案与体系

- `GET /archives`：分页、筛选、排序；
- `GET /archives/{id}`：档案详情；
- `POST /archives`：新增档案；
- `PATCH /archives/{id}`：更新草稿/待核验档案；
- `POST /archives/{id}/submit-review`；
- `POST /archives/{id}/archive`；
- `POST /archives/{id}/disable`；
- `POST /archives/{id}/files`：向已有档案补充文件；
- `DELETE /archives/{id}/files/{linkId}`：仅解除关联，需权限与审计；已归档档案解除主文件前必须阻断；
- `GET /publication-sets`；
- `GET /publication-sets/{id}/tree`；
- `POST /publication-sets`；
- `PATCH /publication-sets/{id}`；
- `POST /publication-relations`。

### 10.2 待归档与投影

- `POST /projection-runs`：启动幂等投影；
- `GET /projection-runs/{id}`：运行结果；
- `GET /reconciliation`：待归档列表；
- `GET /reconciliation/{id}`：候选、原件和证据详情；
- `POST /reconciliation/{id}/link-existing`；
- `POST /reconciliation/{id}/create-archive`；
- `POST /reconciliation/{id}/mark-duplicate`；
- `POST /reconciliation/{id}/mark-invalid`；
- `POST /reconciliation/{id}/ignore`。

批量操作只能处理具有同一确定性规则和相同目标的候选；执行前必须二次确认，并返回逐项结果，禁止“部分失败但整体200且无明细”。

### 10.3 查询与辅助

- `GET /coverage`；
- `GET /facets`：资料类型、定额体系、地区、行业专业体系、分册专业、年份、版本、发布单位、状态计数；必须支持依赖上游选择的级联 facets；
- `GET /dictionaries`；
- `GET /raw-objects/{id}/preview`：复用公共预览能力；
- `GET /archives/{id}/events`：来源与审计。

### 10.4 档案列表筛选

至少支持：

- q：标题、标准/定额编号、发布单位、原始文件名；
- material_type[]；
- quota_system_type[]；
- jurisdiction_level / jurisdiction_code[]；
- industry_sector_code[]；
- discipline_code[]；
- edition_year[]；
- issuer_name；
- archive_status[]；
- metadata_status[]；
- source_channel[]（高级筛选）；
- page、page_size、sort_by、sort_order。

级联规则：

- `quota_system_type = CONSTRUCTION_REGIONAL` 时，返回有数据的 jurisdiction 列表；选择 jurisdiction 后再聚合该地区实际存在的 edition_year；
- `quota_system_type = INDUSTRY_SPECIALTY` 时，返回有数据的 industry_sector 列表；选择 sector 后再聚合该行业实际存在的 edition_year；
- 切换 quota_system_type 时必须清空旧的地区/行业、年份和 discipline 条件；
- facets 的计数必须来自同一查询口径，不得由前端静态推算。

V0.1 的 q 仅搜索元数据和原始文件名，不得伪装成文件正文全文检索。

### 10.5 列表返回字段

每行至少返回：

```json
{
  "id": "archive-id",
  "title": "房屋建筑与装饰工程上册",
  "material_type": "QUOTA_BASE",
  "publication_set": {
    "id": "set-id",
    "title": "四川省2020建设工程工程量清单计价定额",
    "edition_label": "2020定额",
    "edition_year": 2020,
    "quota_system_type": "CONSTRUCTION_REGIONAL",
    "industry_sector": null
  },
  "jurisdiction": {
    "level": "PROVINCE",
    "code": "510000",
    "name": "四川省"
  },
  "discipline": {
    "code": "BUILDING_DECORATION",
    "name": "房屋建筑与装饰"
  },
  "standard_or_quota_code": null,
  "effective_date": null,
  "file_count": 3,
  "primary_file_health": "AVAILABLE",
  "preview_status": "READY",
  "metadata_status": "VERIFIED",
  "completeness_score": 100,
  "archive_status": "archived",
  "source_channels": ["NAS", "LEGACY_IMPORT"]
}
```

### 10.6 错误语义

至少提供稳定错误码：

- REQUIRED_FIELD_MISSING；
- INVALID_STATUS_TRANSITION；
- BIZ_KEY_CONFLICT；
- RAW_OBJECT_NOT_FOUND；
- DUPLICATE_FILE_LINK；
- PRIMARY_FILE_REQUIRED；
- HASH_DUPLICATE_REQUIRES_CONFIRMATION；
- ARCHIVED_RECORD_IMMUTABLE_FIELD；
- BATCH_PARTIAL_FAILURE。

---

## 11. 前端规格

### 11.1 通用要求

- 复用现有 React / Ant Design / AG Grid 组件体系；
- 使用真实 API；
- 不引入第二套 UI 框架；
- 服务端分页、筛选和排序；
- 必须实现 loading、empty、error、partial-success 四种状态；
- 生产页面不得 import Mock 数据或在 API 失败时回退 Mock；
- 测试 fixture 只能存在于测试目录。

### 11.2 档案列表

当前筛选区调整为：

- 资料类型；
- 定额体系（选择定额类资料时出现）；
- 二级分类（根据定额体系动态显示地区或行业专业体系）；
- 年份（依赖二级分类动态生成）；
- 分册专业；
- 版本/版次；
- 发布单位；
- 档案状态；
- 高级筛选：入湖通道、文件格式、元数据状态、预览状态。

“文件类型”改为“资料类型”；“附件”从资料类型中移除。

定额类资料的筛选交互必须遵循：

#### 建筑工程定额

```text
建筑工程定额 → 全国通用/北京/上海/…/四川/… → 实际年份 → 分册专业
```

- 二级分类展示全国通用和有数据的省/市；
- 选择四川后，年份只显示四川已有版本；
- 年份按降序排列；
- 不得默认生成2013—2025的连续年份。

#### 专业工程定额

```text
专业工程定额 → 水利/电力/电网/铁路/公路/… → 实际年份 → 分册专业
```

- 二级分类展示行业专业体系字典中实际有数据的分类；
- 选择水利后，只显示水利已有年份；
- 选择电网后，只显示电网已有年份，例如真实档案存在时显示2022、2020、2018；
- 行业标签可以来自字典，年份必须来自 API facets；
- 切换二级分类必须清除不再适用的年份选择。

列表列定义：

1. 档案标题；
2. 资料类型；
3. 定额体系 / 分类；
4. 适用范围 / 分册专业；
5. 标准 / 定额编号；
6. 版本；
7. 实施日期；
8. 文件数；
9. 完整度；
10. 档案状态；
11. 操作。

操作至少包括：查看、预览、编辑元数据、补充文件、查看来源。停用只在详情页或更多菜单中出现并二次确认。

### 11.3 新增入口

“新增档案”改为下拉双入口：

1. **新增档案**：新建 Publication Set 或选择已有体系，再建立 Archive；
2. **补充文件**：必须先选择已有 Archive，再选择/上传 Raw Object 并设置 file_role。

两种操作不得共用一个含糊的“上传即建档”流程。

### 11.4 档案详情

列表点击进入详情页或宽抽屉，必须包含：

- 基本信息；
- 文件与预览；
- 版本/资料关系；
- 来源与审计。

“文件与预览”显示原始文件名、格式、大小、hash 摘要、文件角色、文件健康、预览状态和来源渠道。

### 11.5 版本体系

底层仍使用三级业务实体，但 UI 在其上增加虚拟分类节点：

```text
建筑工程定额
  └─ 四川定额
       └─ 2020年 / Publication Set
            └─ Archive / 分册
                 └─ Archive File / 原件

专业工程定额
  └─ 电网工程定额
       └─ 2022年 / Publication Set
            └─ Archive / 分册
                 └─ Archive File / 原件
```

分类和年份节点是查询分组，不新建冗余业务实体。树节点展示版本、地区/行业分类、分册专业、文件数和状态。点击节点在右侧展示详情，不得用文件夹目录结构代替业务体系。

### 11.6 待归档工作台

采用“队列 + 工作区 + 证据”的交互：

- 左侧：原件队列与状态筛选；
- 右侧：候选归属、元数据表单、已有体系/档案搜索；
- 底部或右侧抽屉：原始文件名、NAS路径、object key、hash、crawler/manifest 记录、命中规则和操作历史。

每条原件必须提供五种结果：

- 关联已有档案；
- 新建档案；
- 标记重复；
- 标记异常；
- 忽略并填写原因。

批量处理只用于确定性高、目标一致的原件，默认不勾选。

### 11.7 覆盖矩阵

覆盖矩阵顶部切换：

- 清单规范；
- 建筑工程定额；
- 专业工程定额。

建筑工程定额默认：

- 行：全国通用/省/市；
- 列：年份；
- 单元格：Publication Set 数、已归档分册数/预期分册数、状态；
- 点击单元格下钻查看分册专业和文件。

专业工程定额默认：

- 行：行业专业体系；
- 列：年份；
- 单元格：Publication Set 数、已归档分册数/预期分册数、状态；
- 点击单元格下钻查看分册专业和文件。

清单规范默认：

- 行：规范家族；
- 列：版本；
- 单元格：标准号、实施状态、主文件和关联资料完整情况。

若 `expected_volume_count` 未知，单元格必须显示“待核验”，不得因为现存文件全部可访问而显示“完整”。

覆盖状态：

- NONE：无资料；
- PARTIAL：有资料但关键分册或文件缺失；
- COMPLETE：预期数量已知且全部归档；
- UNVERIFIED：已有资料但预期范围未知。

---

## 12. 审计、权限与事件

### 12.1 审计

以下行为必须记录审计事件：

- 建立/修改 Publication Set；
- 建立/修改 Archive；
- 补充或解除文件关联；
- 提交核验、归档、退回、停用；
- 标记重复、异常、忽略；
- 更改业务键、版本关系和关键元数据；
- 批量操作。

事件至少记录 actor、action、entity、before、after、source、timestamp、request/trace id。

### 12.2 权限

沿用数据湖公共权限。至少区分：

- 浏览；
- 补录/编辑草稿；
- 核验/归档；
- 停用；
- 投影运行与批量处理。

浏览权限不得隐式获得修改能力。

### 12.3 下游事件

Archive 成功进入“已归档”业务阶段时发出公共事件，建议：

```text
archive.archived
```

payload 至少包含：

- archive_id；
- domain_type = quota；
- publication_set_id；
- material_type；
- edition_label；
- file_refs；
- archived_at；
- trace_id。

V0.1 只负责稳定发出事件，不实现解析/语义消费者。

---

## 13. 数据迁移与兼容策略

### 13.1 迁移原则

- 迁移必须为增量、可回滚、非破坏性；
- 不允许改变现有 Raw Object 的 object key、hash 或存储位置；
- 不允许删除其他数据域记录；
- `domain_type = quota` 的新索引和约束不得影响 cost_info、trading、policy、standard 等域；
- 若现有表已有同义字段，优先映射和回填，不重复造字段；
- 所有回填脚本必须可重复执行。

### 13.2 必要索引

按实际数据库实现等价索引：

- Publication Set.biz_key 唯一；
- Archive.biz_key 唯一或在 domain/tenant 范围内唯一；
- Projection Candidate.raw_object_id 唯一；
- Archive File 复合唯一约束；
- material_type、quota_system_type、jurisdiction_code、industry_sector_code、discipline_code、edition_year、archive_status、metadata_status 查询索引；
- raw_object hash 索引；
- publication relation source/target 索引。

### 13.3 Mock 清理

必须检查当前页面、store、API adapter 和 fallback：

- 删除生产代码中的仿真清单定额档案；
- 删除 API 失败时的 Mock fallback；
- 保留测试 fixture 时必须迁入测试目录；
- 页面展示数量必须来自 API；
- `0份档案` 必须代表真实数据库结果，而非接口未接通后的默认值。

---

## 14. 三条样本链路

### 14.1 样本 A：清单规范版本链

资料家族：GB 50500。

至少建立：

1. `建设工程工程量清单计价规范 GB 50500-2013`；
2. `建设工程工程量清单计价标准 GB/T 50500-2024`；
3. 两个版本各自的 Archive 与主文件；
4. 2024 对 2013 的 `SUPERSEDES` 关系；
5. 发布单位、版本、标准号、实施状态和来源；
6. 列表、版本体系、详情、预览、覆盖矩阵全链路可见。

本样本验证：清单规范、多版本、替代关系。

### 14.2 样本 B：四川2020定额

资料体系：四川省2020建设工程工程量清单计价定额——房屋建筑与装饰工程。

至少建立：

1. Publication Set；
2. 实际存在的上/下册或分册 Archive，不得凭空伪造册数；
3. 每册的 MAIN_DOCUMENT 及实际存在的封面、目录、附录等文件角色；
4. `quota_system_type = CONSTRUCTION_REGIONAL`、四川省、2020年、房屋建筑与装饰 discipline 等元数据；
5. 列表、版本体系、详情、预览、覆盖矩阵全链路可见。

本样本只验证 L0 档案，不抽取 A–E 章，不运行定额语义化 Skill。

### 14.3 样本 C：专业工程定额多年份

优先使用现有资料中能够形成多年份对照的“电网工程定额”；当前页面示例年份为2022、2020、2018，最终必须以真实 FileAsset / Publication Set 为准。

至少验证：

1. `quota_system_type = INDUSTRY_SPECIALTY`；
2. `industry_sector_code = POWER_GRID`；
3. 各实际年份分别形成独立 Publication Set；
4. 选择“电网工程定额”后，年份 facet 只返回电网实际存在的年份；
5. 切换到水利等其他行业后，年份随数据重新计算，不继承电网年份；
6. 能继续下钻到实际分册专业、Archive 和主文件。

若真实专业工程定额文件尚未入湖，本样本允许暂时标记 BLOCKED，但数据模型、API 级联 facets 和自动化 fixture 必须先通过；不得用生产 Mock 冒充真实样本。

---

## 15. 验收标准

### A. quota 原件对账

- [ ] quota 域原件清单实际读取成功；
- [ ] 每个 Raw Object 恰好对应一个 Projection Candidate；
- [ ] `quota_raw_total = PENDING + LINKED + DUPLICATE + INVALID + IGNORED`；
- [ ] 每个 DUPLICATE 指向重复源；
- [ ] 每个 INVALID / IGNORED 有原因；
- [ ] 对账报告可导出；
- [ ] 重复运行投影，核心数量不重复增长。

首次运行必须说明全域2058统计与 quota 专属 raw_total 的差异。任何环境都禁止为了通过验收硬编码2058。

### B. 数据模型

- [ ] 一个 Publication Set 可包含多个 Archive；
- [ ] 一个 Archive 可关联多个 Raw Object；
- [ ] 相同文件不能在同一 Archive 重复关联；
- [ ] 清单规范与定额资料使用同一 Archive 底座；
- [ ] 建筑工程定额与专业工程定额使用不同分类轴；
- [ ] jurisdiction 与 industry_sector 为正交字段；
- [ ] industry_sector 与 discipline 不共用一个字段；
- [ ] “附件”作为 file_role，而不是 material_type；
- [ ] 元数据来源和人工修改可追溯；
- [ ] 状态机非法跳转被阻断；
- [ ] 已归档记录不能被硬删除。

### C. API

- [ ] 档案列表支持分页、筛选、排序和 facets；
- [ ] 地区/行业分类与年份 facets 可级联且来自真实数据；
- [ ] 档案详情返回体系、分册、文件、关系和审计；
- [ ] 新增档案与补充文件是两个明确接口/动作；
- [ ] 待归档五种处理结果全部可用；
- [ ] 批量部分失败返回逐项结果；
- [ ] 投影接口幂等；
- [ ] 归档成功发出 `archive.archived` 事件；
- [ ] OpenAPI 或等价 API 契约更新。

### D. 前端

- [ ] 四个页签可访问；
- [ ] 页面只使用真实 API，无生产 Mock fallback；
- [ ] 当前筛选中的“文件类型”已改为“资料类型”；
- [ ] “附件”不再与定额库/清单规范平级；
- [ ] “地区”改为“适用范围”；
- [ ] 建筑工程定额按地区→年份筛选；
- [ ] 专业工程定额按行业专业体系→年份筛选；
- [ ] 年份选项不在前端硬编码；
- [ ] 列表字段符合 §11.2；
- [ ] 新增档案/补充文件双入口可用；
- [ ] 详情可查看文件角色、预览、来源和审计；
- [ ] 待归档工作台可逐条处理；
- [ ] 覆盖矩阵不把文件可访问率冒充业务完整度；
- [ ] loading、empty、error、partial-success 状态完整。

### E. 样本与非回归

- [ ] 样本 A 全链路跑通；
- [ ] 样本 B 全链路跑通；
- [ ] 样本 C 数据链路跑通，或在缺少真实原件时明确标记 BLOCKED 且模型/API测试通过；
- [ ] 2013 → 2024 替代关系可见；
- [ ] 四川2020真实分册结构可见；
- [ ] 未创建语义条目、定额子目或清单定额映射；
- [ ] 其他数据湖域页面和数据不受影响；
- [ ] 自动化测试及迁移测试通过。

---

## 16. 实施顺序

Cascade 必须按以下顺序施工：

### P0-1 · 现状摸底

- 定位现有 Archive、Raw Object、文件关联、审计、预览和权限实现；
- 定位当前页面的 Mock、API adapter、store 和路由；
- 输出“复用点 / 缺口 / 冲突”短报告；
- 若存在实质冲突，先报告，不继续造平行模型。

### P0-2 · 数据模型与迁移

- 增量迁移；
- 唯一约束和索引；
- 字典与枚举；
- 迁移与回滚测试。

### P0-3 · 原件盘点、投影和对账

- 接入真实 quota inventory；
- 建 Projection Candidate；
- 确定性规则和 hash 重复识别；
- 幂等运行；
- 对账报告。

### P0-4 · API

- 档案、体系、文件关联、版本关系；
- 待归档处理；
- facets、覆盖矩阵、审计；
- 状态校验和稳定错误码。

### P0-5 · 前端

- 移除生产 Mock fallback；
- 四个页签；
- 列表、详情、双入口；
- 待归档工作台；
- 版本体系和覆盖矩阵。

### P0-6 · 三条样本与验收

- 样本 A；
- 样本 B；
- 样本 C；
- 端到端测试；
- 非回归测试；
- 实施报告与截图。

不得先做漂亮的空壳页面，再回头补数据投影。

---

## 17. Cascade 交付物

Cascade 最终必须提交：

1. 数据模型与迁移代码；
2. 可回滚/可重复的回填和投影脚本；
3. API 与 OpenAPI/等价契约；
4. 前端改造；
5. 自动化测试；
6. 三条样本链路结果；
7. quota 专属原件对账报告，并解释其与全域2058统计的差异；
8. `docs/quota_archive_implementation_report_v0.1.md`，至少包含：
   - 复用的公共能力；
   - 实际表/类/路由映射；
   - 迁移结果；
   - 对账守恒结果；
   - 样本截图；
   - 测试命令与结果；
   - 未解决问题和风险。

---

## 18. 停止条件

遇到以下情况，Cascade 必须停止对应工作并报告：

- 需要破坏性修改或搬移 NAS / MinIO 原件；
- 现有 Archive 模型无法表达一档多文件；
- quota 专属 inventory 的来源定义或 FileAsset 过滤口径无法建立；
- 原始文件名、NAS路径、爬虫数据库均无法取得，导致大部分业务身份不可识别；
- 需要改变其他数据域公共契约才能继续；
- 必须引入 LLM、OCR 或语义化才能满足当前页面；
- 真实资料与样本命名、版本或册数冲突；
- 迁移无法回滚或幂等验证失败。

停止后应提交事实、日志、影响面和建议选项，等待规格负责人/用户裁决，不得自行扩大范围。

---

## 19. V0.1 完成定义

V0.1 完成不以“页面能打开”为标准，而以以下结果为标准：

> 真实 quota 原件全部进入可守恒的待归档/已归档体系；清单规范、建筑工程定额和专业工程定额能按各自分类轴、年份、分册和文件被检索、预览、补录和追溯；三条样本链路得到真实结果或明确的原件阻断结论；并且没有越界进入语义化、标准化或智能组价。
