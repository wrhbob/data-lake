# Coverage Gap Contract — `contracts-v0.1.0-rc3`

> 代码载体:`file_asset_service/app/coverage_gap_contract.py`。
> 本文件为人读契约文本,按 rc 格式撰写,供合入正式契约仓。rc1/rc2 的积压字段
> (`failed_stage` / `task_id` / `download_url`)与 `gap_type` 三态对齐、`gap_reason` 新枚举
> 一并在本版定稿(三批积压清结,不再滚 rc4)。

## 1. 单元格身份

一个覆盖单元格 = `(coverage_region_code, period, domain_type)`。

- `coverage_region_code`:字符串,GB6 行政区码或 `"<6位码>-<子区>"` 形态(与矩阵现状一致)。
- `period`:归一化月 `YYYY-MM`。
- `domain_type`:当前仅 `cost_info`。

## 2. gap_type(覆盖三态,与矩阵 UI / `v_coverage_gap` 三方对齐)

| 枚举 | 含义 | UI 标签 |
|---|---|---|
| `covered` | 该单元格有 ≥1 条 active 档案或声明覆盖 | 覆盖 |
| `pending_verify` | 未覆盖但属"待核验":来源受阻、未发布、或历史待回填 | 待核 |
| `missing` | 真正的缺口(有源、到期、未采)或无源 | 缺失 |

判定等价于矩阵历史 `_business_status`:`covered` ⇔ 有覆盖;否则若 `blocked 或 pending(publication/backfill)` ⇔ `pending_verify`;否则 `missing`。

## 3. gap_reason(可发起性,仅 `gap_type != covered` 时非空)

| 枚举 | 触发 | 可发起 | UI 标签 |
|---|---|---|---|
| `no_source` | 该城无可用源(`source_blocked` 或无注册 active 源) | 否 | 无源 |
| `not_published` | 期次晚于最近已覆盖期(未发布,未到时) | 否 | 未发布 |
| `failed` | 该单元格最近任务 `failed`(重试耗尽) | **是(重试)** | 失败 |
| `not_attempted` | 有源、未覆盖、无失败任务(全新缺口或历史待回填) | **是** | 待采集 |

**优先级**(非 covered 时,自上而下首个命中):`no_source` > `not_published` > `failed` > `not_attempted`。
`ACTIONABLE_REASONS = {not_attempted, failed}`。矩阵补采入口仅对 `actionable` 单元格开放。

> `source_blocked` 在 rc3 折叠进 `no_source`(无可用源)。若后续需独立枚举,在 rc4 前提出。

## 4. 字段(rc3 定稿)

### 4.1 `gap_type`
枚举,见 §2。每单元格必有。

### 4.2 `gap_reason`
枚举,见 §3。`covered` 时为 `null`。

### 4.3 `failed_stage`(积压项,rc3 定稿)
- 仅当 `gap_reason == failed` 时非空,否则 `null`。
- 枚举:`download_timeout | host_unreachable | parse_error | crawl_failed | max_attempts_exceeded`。
- 来源:worker 任务 `error_code` 的归一(见 `app/cost_info_worker.py:_classify_error`)。
  `DOWNLOAD_TIMEOUT→download_timeout; HOST_UNREACHABLE→host_unreachable; PARSE_ERROR→parse_error;
  COST_INFO_CRAWL_TASK_FAILED→crawl_failed; MAX_ATTEMPTS_EXCEEDED→max_attempts_exceeded`。
- 人读标签:下载超时 / 主机不可达 / 解析失败 / 采集失败 / 重试耗尽。

### 4.4 `task_id`(积压项,rc3 定稿)
- 字符串,可空。该单元格最近一条 `collection_task.task_id`(`task_type ∈ {crawl_incremental, crawl_issue}`, `data_domain=cost_info`, 按 `coverage_region_code`+`period_start` 匹配)。
- 非 covered 的可发起单元格与失败详情载荷均返回;covered 时可空。

### 4.5 `download_url`(积压项,rc3 定稿)
- 字符串,可空。covered 单元格主文件下载链接,格式 `/api/file-assets/{file_id}/download`。
- 非 covered 时为 `null`。(矩阵现 `primary_download_url` 字段正式入契约。)

### 4.6 `cell_status`(状态回写,功能二)
枚举:`in_lake | queued | crawling | failed | missing`。由 task/lineage/archive **派生**,不落新表:
`有档案→in_lake; 最近任务 pending→queued; running→crawling; failed→failed; 否则 missing`。

### 4.7 `expected_publish_day`
整数 1–31,可空。来自 `city_period_scheme.expected_publish_day`(数据推断初值 + D-③ 人工核实)。
用于"未发布"细化与异常 webhook(`expected_publish_day + N 天` 未入湖 → 推送)。

## 5. `v_coverage_gap` 视图(SQL 真相源)

Postgres 视图,逐单元格输出:`coverage_region_code, period, domain_type, gap_type, gap_reason,
failed_stage, task_id, download_url, expected_publish_day, source_ids`。

- 单元格全集 = 档案(`archive.coverage_region_code × coverage_period`) ∪
  声明(`data_source.config.coverage_expectation.target_regions × declared_periods`) ∪
  任务(`collection_task.coverage_region_code × period_start`)。
- `gap_type/gap_reason/failed_stage` 的 `CASE` 表达式必须与 `classify_gap()` 逐字一致
  (见 `app/coverage_gap_contract.py:classify_gap`)。`coverage_gap_setup.py --check-view-consistency` 校验。

矩阵 API(Postgres)读取本视图获取契约字段;SQLite/测试回退到 `classify_gap()`。

## 6. 一致性(承接 gap_type 悬案,DECISION-001)

矩阵 UI 三态 == `v_coverage_gap.gap_type` == 本契约 §2 枚举,**以本 rc3 为最终定义**。
`gap_reason` 同理由 §3 + `classify_gap` 唯一定义。

## 7. CHANGELOG

### contracts-v0.1.0-rc3
- `gap_type` 按矩阵三态对齐最终枚举(covered/pending_verify/missing)。
- 新增 `gap_reason` 枚举(no_source/not_published/not_attempted/failed)及优先级与 ACTIONABLE 集合。
- 定稿积压字段:`failed_stage`(error_code→stage 映射)、`task_id`、`download_url`。
- 新增 `cell_status`(派生)、`expected_publish_day`。
- 定义 `v_coverage_gap` SQL 视图为契约真相源;矩阵 API 读取之。
- 清结三批积压(gap_type 对齐 / gap_reason / failed_stage·task_id·download_url),不滚 rc4。

### (历史) rc1 / rc2
- 留位:由正式契约仓的既有 rc1/rc2 记录填充(本次合入时由契约仓维护者衔接)。
