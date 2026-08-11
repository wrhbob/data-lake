# SPEC — 通用定额提取器（v0.4.3）

> **目的**：从各省定额 PDF/MD 自动提取为统一格式 XLSX，**无需为新省份写新 extractor 脚本**。
> 工作目录：`quota-unified/`（与 `quota/` 同级，新建）

## v0.4.3 更新要点（上海适配 sh-2016）

| 变化 | 内容 |
|---|---|
| **8 省** | 全文 7 省 → 8 省，新增 **sh（上海市市政工程预算定额第一册道路桥梁隧道 SHA1-31（01）-2016，363 页）** |
| **最相似 = bj-2021** | 上海「同北京」：无基价/费用、无单价（量价分离）。复制 bj 微调：P1 改 `^\d{2}-\d+-\d+-\d+$`（04-1-1-1 四段式）、P3 新增 `ZH_CE` 章节体系（册→章→节）、P2/P5/P6 同 bj（class-code-name-unit-qty + none） |
| P1 新格式 | `04-1-1-1`（专业-章-节-定额号，4 段数字）——SPEC 原 regex 之外的新形态 |
| P3 新枚举 `ZH_CE` | 上海 3 层：`第X册 → 第X章 → 1．节`（数字节直接挂章下，跳过 chinese4 的 `一、` 级）。`_extract_sections_zh_ce` 新抽取器 + `cur_vol` 册号跨表持久 + TOC 点线跳过 + 节头去重 |
| 上海 3 新 flag | `material_header_implicit`（材料区无「名称」表头，从项目块后首个 人工/材料/机械 行开始）、`project_unit_last_line`（无「计量单位」行，项目单位=项目块最后一行 `$m^3$`）、`dedup_section_ids`（OCR 每页重复节头 → 段行去重，仅 code 全局唯一的省可开） |
| 单位/分类 | 定行单位列=m³（项目块最后一行）；材料行 工日/台班/只/m³ 正常；人工→工/材料→料/机械→机 |
| 段行 | 254 个（册.章.节，如 `2.1.3 3． 抛石挤淤`），TOC 点线条目已滤，无重复 |
| features.py | 上海只检出 bracket_qty（量价分离下默认 False 即可，无歧义） |
| 回归 | sc/hu 0 diff（dedup 仅 zh-ce 开，sc/hu 段行 code 跨卷合法重复不受影响）；gd/bj 已知 TODO |

## v0.4.2 更新要点（河南适配 + §3.2 修正）

| 变化 | 内容 |
|---|---|
| **7 省** | 全文 6 省 → 7 省，新增 **ha（河南省房屋建筑与装饰工程预算定额 HA 01-31-2016，上册）**。实测适配走**纯 feature 注入**（baseline 只加 2 处极小兜底循环，零省逻辑改动） |
| §3.2 修正 | **"复制最相似 profile → 微调 flag"不成立**：网站运行时无 agent，相似度由**脚本**判断（features.py 的 detected/b_switches/p_conflicts 与 7 个预置 profile 对比取覆盖度最高）。agent 只在"新定额有特殊必注入特征"时才写 features.py。新 profile 先本地实测、不入库 |
| 河南 4 特征 | `labor_name_keywords`（综合工日→工+不计验证）、`force_emit_labor_fee`（人工费行强制 emit，覆盖 three-correspondence 隐藏）、`machine_unit_keywords`（台班→机）、`extra_cost_labels`（其他措施费/安文费/规费→各自综行）——全部为 profile 新增字段，默认空/False 不影响 6 省（§4.2 / §5 P8-P11） |
| `bracket_qty_is_unpriced` 扩省 | 原 yn-only → **yn/ha**：河南 46 个非综合工日括号行全部单价'-'（脚本核实 0 个带数字单价）= 未计价主材（预制桩等）。综合工日括号由 P8 先判为"工"，不进未计价判定（§5 P12） |
| features.py 扩 3 特征 | 除 bracket_qty 外新增 labor_name_keywords / machine_unit_keywords / extra_cost_labels 检测（**精确判别器**：col0 无分类标签 + 单位精确='台班' / 综合工日；label 费行 + 与 管理费/利润 平级同表。6 省 fixture 实测只有 ha 触发）。检测自足 → b_switches；bracket_qty 语义冲突 → p_conflicts 需 profile |
| B7 colspan 修复 | `_grid_to_html` 子表重建保留 colspan（相邻相同 cell 归组为 colspan=N）。河南 475/479 材料表 `名称 colspan=2`，B7 复合表拆分后若不还原 → 单位/单价整体错位一列（3-53 类表全部错位）。修复后河南验证和=基价 1282→1457/1539。安全：grid 往返逐格值不变，sc/hu/gd/bj 回归 0 diff |
| B1 补空格变体 | `composite_labels` 补 `"基 价(元)"`（河南 OCR 空格变体，31 表）。验证和=基价 多 98 项 |
| 验证和=基价 | 河南 1539 个定额 1457 精确匹配（94.7%）；剩余 82 = 77 个 OCR 合并材料行（多料拼一格，规则无法拆分）+ 2 个 PDF 表缺规费费行（基价含规费但表未列）+ 3 无验证，均为数据质量问题非解析 bug |

## v0.4 更新要点（相对 v0.3）

| 变化 | 内容 |
|---|---|
| **5 省** | 全文 4 省（sc/cq/gd/hu）→ 5 省，新增 **bj（北京 2021 预算消耗量标准，v0.15 已落地，extractor 2081 行为 hu 复制+扩展）**。§4/§9 纳入 bj 特征，**bj 细节已全部核实**（P1 编码=数字-数字、P2 无单价列、P3 mixed-zh、P4 深度 3、P6=hu 型） |
| B8 合并 | `continuation_table_skip` 从 baseline 移除，并入 P7 → **`cross_page_strategy`（none/join）**——跨页续表是同一现象、两种相反策略（gd/hu=join / sc+cq+bj=none），不应拆成 baseline + profile |
| §6 修正 | 显式声明 **P5/P6/P7 不可自动推断**，由 profile 直接设值（修正 §2 流程与 §6.2 的"全自动勾选"叙事矛盾） |
| §4.3 新增 | feature 依赖/互斥矩阵（B4↔B1、B6↔cost_rows、跨页↔seen_pids、P3↔P4、P2→B3） |
| P1 新增取值 | bj 编码格式 `数字-数字`（`^\d{1,2}-\d{1,3}$`），SPEC 原 3 种 regex 之外的第 4 种 |
| P2 新增取值 | bj 材料表 `class-code-name-unit-qty`（分类/编码/名称/单位/消耗量，**无单价列**）——量价分离，SPEC 原 3 种取值外的新布局 |
| P6 归型 | bj **P5/P6 均为 `none`**——北京消耗量标准无价类信息，extract_table_bj 自闭环不 emit 费行、无料-材料费自动行（v0.4 修正：此前"bj=hu 型"判断有误，那是通用 extract_table 的逻辑，bj 不走该函数） |
| §4.4 新增 | B 类"多关键字 OR"实测反例 2 条（全费用独立判定 / machine_labels 并集误伤），从 hu fixture 回归发现并修正（见 §4.4） |
| 行为开关进 profile | 原 FeatureContext 的 6 个行为开关（material_sort / filter_project_numeric / unit_last_line_override / filter_toc_dotted / strict_section_re2 / machine_labels）移入 profile（§4.2）——按省显式设值，不依赖 features.py 检测 |
| 删冗余开关 | B2 `material_fee_unit_percent` / B7 `composite_table_split` 检测自足（unit=="%"/col0="定额编号"≥2），开关从未被读取 → 删除（§5 B2/B7） |
| P7 收敛 | `cross_page_strategy` 删 `skip` 取值，收敛为 none/join——实测 gd/hu 走同一套 join 逻辑（§5 P7） |
| **6 省** | 全文 5 省 → 6 省，新增 **yn（云南园林绿化 2020，DBJ53/T-60-2020）**。实测适配：P2 新布局 `class-name-unit-price-qty`（分类\|名称跨2列\|单位\|单价\|数量，无编码）、2 个新 P 开关（`strict_section_downstream` 拒规则条目段行 / `bracket_qty_is_unpriced` 括号数量=未计价苗木）、4 个 baseline 修复（ctx 传入 / 人工分支仅 cq / quantities 跳过单价列 / rowspan 前段列补全）。详见 §5 |
| §7 未决 | schema 版本化倾向 → **采纳**：Pydantic 模型加 version 字段 |

## v0.3 更新要点（相对 v0.2）

| 变化 | 内容 |
|---|---|
| B4 修正 | "费用"独立识别，**必须排除"全费用"**（避免与 B1 的 composite_label 路径冲突） |
| P5 不合并 | 行为对比结论：**不一致**（sc 材料费/机械费 emit 条件 vs cq/gd/hu 的 three-correspondence 条件），保留为 flag |
| §5 补全 | B4 / B6 / B7 / B8 之前只出现在 §4.1 表格，§5 文字描述缺失，本版本补齐 |
| §9 新增 | 新省份验收清单（直接拿这个清单对新 PDF 跑一遍，验证 feature 分类是否准确） |

---

## 0. 目录与文件纪律

- `quota/` 整体保持原样，**不做任何修改**
- `quota-unified/` 是本次工作目录，从 `quota/` 复制所需文件起步
- 现有 5 省 `extractors/sc|cq|gd|hu|bj/extract_quota.py` 复制一份到 `quota-unified/extractors/<prov>/` 后**冻结**，仅作 "feature 组合参考样本"

---

## 1. 设计约束

### 1.1 自动识别算法（Point 1 ✅ 已锁定）

> **算法**：全局搜索 `<table>` 标签，查其上方 5 行内是否出现"工作内容"字样；如有则当作候选定额表。

依据：所有省份（sc/cq/gd/hu/bj）的定额表都紧跟在"工作内容：xxx"这一行下面（相距 ≤ 5 行）。

后续：扫出候选表 → 规则脚本判定各 feature → Web UI 让人确认/补充 → 跑抽取。

### 1.2 文件复制纪律（Point 3 ✅ 已锁定）

- `quota/` 不动
- 复制 `quota/` 中**用得到的**所有文件到 `quota-unified/`
- 修改、新增、删除都在 `quota-unified/` 内进行

### 1.3 图示约定 ✅ 已锁定

> 每个 feature 需要一个图示。
> **我写文本描述（让用户知道这个 feature 是什么、在 PDF 哪里出现、长什么样），用户自己截图**。
> 截图归档到 `quota-unified/docs/feature_screenshots/<feature_name>.png`。

### 1.4 规则 vs AI 边界 ✅ 已锁定

> 本系统的"自动识别"是 **规则脚本**（基于 markdown 文本扫描 + 启发式），**不是 LLM/AI**。
>
> 后果：
> - 不输出"为什么勾这个"的解释
> - 不显示"信心度"
> - 二元判定：勾 / 不勾
> - 脚本无能为力的 feature → 必须人工勾选 / 选 profile

### 1.5 特征并入基准的判定标准（Point 2 核心）

> **判定方法**：能"在同一段代码里同时支持多种省份的写法"且"输出在多种写法下都正确"，就属于**不冲突** → 可以并入 baseline。
>
> **反例**：差异会造成输出不同（如 emit 行不一致），且无法用统一逻辑兼顾 → 必须留为 **profile flag**（由 profile 设置具体值）。
>
> 用户原则：**任何疑似冲突，先 flag，不在 baseline 里耍聪明。**

---

## 2. 用户流程（A 修正版）

```
1. 用户上传 PDF
2. OCR 流水线（minerU → MD）
3. 规则脚本扫 MD，能明确判断的 feature 勾上
   - 二元判定，无解释、无信心度
4. Web UI 显示自动勾选结果 + 图示
   - 用户可增/删任何勾选
   - 脚本一个都没勾 → UI 跳到"手动选 profile"模式
5. 用户点"确认" → 跑完整流水线（5 步 autofinalize + XLSX）→ 用户下载
```

**约束**：
- profile 全局库 + 档案关联 profile
- 同一省份定额风格一致，无分段 profile
- 按用户决定来（用户全盘否定 AI 勾选也能跑下去）

---

## 3. Profile 设计

### 3.1 命名 ✅ 已锁定

格式：`{省}-{版本年}`

例：`sc-2018`、`cq-2018`、`gd-2018`、`hu-2018`、`bj-2021`、`yn-2020`、`ha-2016`、`sh-2016`

### 3.2 创建方式 ✅ 已锁定（v0.4.2 修正：脚本判相似，无运行时 agent）

- 系统预置 7 个（sc-2018 / cq-2018 / gd-2018 / hu-2018 / bj-2021 / yn-2020 / **ha-2016**）
- 新省份进来时：**由脚本判断最相似的预置 profile → 复制 → 改名 → 微调 flag**
- 不允许"从零自创"

> ⚠️ **v0.4.2 修正（SPEC §3.2）**："复制最相似 profile → 微调 flag"这句话曾暗示运行时由 agent 做
> 相似度判断——**网站建成后运行时不需要任何 agent**。正确分工：
> - **相似度判断 = 脚本**：比较新省份 MD 的检测结果与 7 个预置 profile 的 `detected`/`b_switches`/
>   `p_conflicts` 勾选集合，取覆盖度最高的 profile（`features.py` 产出 `FeatureDetectResult`，
>   profile 表是可枚举的静态数据）。这一步不允许调 LLM。
> - **features.py = 规则脚本**：只对**特殊的、必须注入的特征**，才由 agent 在本地写 features.py
>   （如河南 `labor_name_keywords` / `machine_unit_keywords` / `extra_cost_labels`，都是 baseline
>   默认行为覆盖不到、又无现成 B 关键字可复用的现象）。普通现象一律走 baseline 多关键字 OR。
> - **新省份 profile 先不写进数据库**：先在本地跑 `baseline.process_md_file` 验证 0 diff /
>   验证和=基价，多个省份实测通过后再入库。
> - **尽量不修改 baseline**：新特征优先走 feature 注入（新增 profile flag 字段 + features.py
>   检测），只有必须改的兜底逻辑才动 baseline。

### 3.3 profile vs 档案关联 ✅ 已锁定

档案元数据有"省份"和"年份"两个字段。遇到相同字段组合 → 自动应用对应 profile。

### 3.4 profile 内容 ✅ 已锁定

profile 是一组 feature flag 的命名快照。**只是 flag 字段，不是 Python 代码**。

```json
{
  "name": "sc-2018",
  "composite_label": "综合基价",
  "project_id_regex": "^[A-Z]{1,2}\\d{4}$",
  "section_system": "alphanumeric",
  "section_depth": 1,
  "material_header_layout": "name-unit-price-qty",
  "fee_emit_strategy": "always",
  "material_fee_auto_emit": "sc-style",
  ...
}
```

### 3.5 schema 实现

> 用 **Pydantic** 定义 feature flag schema，Python / 前端共用 single source of truth。

---

## 4. Feature flag 总览

### 4.1 进 baseline（规则脚本 + 多关键字 OR + 可选检测）

| # | feature | 含义 | 变体 |
|---|---|---|---|
| B1 | `composite_label_keywords` | 综合基价 label 关键字 | 综合基价 / 综合单价 / 全费用(元) / 基价(元) 等 OR |
| B2 | `material_fee_unit_modes` | 其他材料费单位 | 元 + 数字 / 百分比（**检测自足：`unit=="%"` 即比例行，无开关**） |
| B3 | `special_material_categories` | 特殊材料分类识别 | 未计价 / 附项 / 人工（**parse_material_row 读 `material_categories` 配置**） |
| B4 | `special_cost_rows` | 特殊费类识别 | 利润 / 管理费 / 一般风险费 / 增值税 / 费用（**排除"全费用"**） |
| B5 | `composite_row_value_check` | 综合基价行值模式 | label + 数字同行 |
| B6 | `multi_label_cell_split` | multi-label 单 cell 拆分 | gd A 分册独有场景 |
| B7 | `composite_table_split` | 复合表拆分 | 多张定额表合并到同 `<table>`（**检测自足：col0="定额编号" ≥2 即拆，无开关**） |
| ~~B8~~ | ~~`continuation_table_skip`~~ | ~~续表识别~~ | **v0.4 移除 → 并入 P7 `cross_page_strategy`（none/join）**。跨页续表是同一现象，实测 gd/hu 都走 join（extract_table 检测 continuation → process_md_file 拼接回主表），sc+cq+bj 无此现象，放 profile 才能按省设值 |

理由：这些都是**可选检测**或**多关键字 OR**——同段代码支持多种写法，输出在所有写法下都正确。

### 4.2 进 profile flag（每个 profile 设值）

| # | feature | 含义 | 取值 | 哪省不同 |
|---|---|---|---|---|
| P1 | `project_id_regex` | 项目编码 regex | `^[A-Z]{1,2}\d{4}$` / `^[A-Z]\d+-\d+-\d+$` / `^[A-Z]\d+-\d+$` | sc/cq / gd / hu |
| P2 | `material_header_layout` | 材料表头列结构 | `name-unit-price-qty` / `code-name-unit-price-qty` / `class-code-name-unit-price-qty` / **`class-name-unit-price-qty`（yn，分类+名称跨2列，无编码）** / **`class-code-name-unit-qty`（bj，无单价）** | sc+hu / cq / gd / yn / bj |
| P3 | `section_system` | 章节体系 | `alphanumeric` / `chinese4` / **`mixed-zh`（bj）** | sc+cq+gd / hu / bj |
| P4 | `section_depth` | 段行 emit 层级 | 1 / 3（bj） / 4 | sc+cq+gd / bj / hu |
| P5 | `fee_emit_strategy` | 费/类 emit 决策 | `always` / `three-correspondence` / **`none`（bj）** | sc / cq+gd+hu / bj |
| P6 | `material_fee_auto_emit` | 料-材料费自动行 emit | `none` / `sc-style` / `cq-style` / `gd-style` / `hu-style` | **bj=none（无料-材料费自动行）** / sc / cq / gd+hu |
| P7 | `cross_page_strategy` | 跨页续表策略 | `none` / `join` | (默认 none)；gd/hu=join / sc+cq+bj=none |

**P 类行为开关（v0.4 新增，原在 `FeatureContext`）**：以下字段影响输出结构/内容，按省设值、显式声明、不依赖 features.py 检测：

| # | 字段 | 含义 | 设 True 的省 |
|---|---|---|---|
| — | `material_sort` | 材料行 emit 排序（料→附项→机，gd/hu 附项语义） | gd/hu |
| — | `filter_project_numeric` | 项目名纯数字过滤（hu/cq "项目"block 数字污染） | hu |
| — | `unit_last_line_override` | "计量单位=见表"时用 block 最后一行覆盖单位列 | gd/bj |
| — | `filter_toc_dotted` | 段行名含点线页码/mermaid 语法则跳过（gd TOC 噪声） | gd |
| — | `strict_section_re2` | 段行 Pass4 用严格正则拒 mermaid | gd |
| — | `machine_labels`（B 类 override） | 机械费行 label 集合（SPEC §3.4 预留的 B-override 字段） | gd 追加 "机具费" |
| — | `strict_section_downstream` | 节段行验证遇同级节提前终止（拒绝工程量计算规则/总说明条款当段行） | yn |
| — | `bracket_qty_is_unpriced` | 数量带括号=未计价主材（yn/ha 规则；sc/hu 括号=比例行"配"） | yn/ha |
| — | `skip_price_col_in_qty` | 数量提取跳过单价列（yn 8 PID colspan=4 表防单价当数量；其余默认 False 保 sc 原版行为） | yn |
| — | `labor_name_keywords`（B 类 override） | 名称含关键字 → category="工" + 不计验证（河南"综合工日"无 col0 标签、单价无效） | ha（("综合工日",)） |
| — | `force_emit_labor_fee` | 人工费行强制 emit（河南综合工日无单价，人工费匹配不上明细 → 覆盖 three-correspondence 的隐藏逻辑） | ha |
| — | `machine_unit_keywords`（B 类 override） | 单位含关键字 → category="机"（河南材料明细区无 col0 分类标签，区分料/机靠单位="台班"） | ha（("台班",)） |
| — | `extra_cost_labels`（B 类 override） | 费行 label → 综行名（河南 9 费全分开：其他措施费/安文费/规费 各自独立成综行；baseline 默认只认 管理费/利润/费用/增值税/一般风险费） | ha（{"其他措施费","安文费","规费"}） |
| — | `material_header_implicit` | 材料区无「名称」表头行，从项目块后首个 人工/材料/机械 分类行开始（上海表直接 定额编号/项目/单位 → 分类行） | sh |
| — | `project_unit_last_line` | 项目单位 = 项目/子目名称 block 最后一行（上海无「计量单位」行，单位是项目块末行 `$m^3$`） | sh |
| — | `dedup_section_ids` | 段行 sec_id 全局去重（OCR 每页重复节头）。**仅当段行 code 全局唯一才可开**（zh-ce 册.章.节天然唯一；sc/hu code 跨卷合法重复不可开） | sh |

> `machine_labels` / `labor_name_keywords` / `machine_unit_keywords` / `extra_cost_labels` 是 B 类关键字集合，
> 因省差异按省覆盖（§4.4 并集误伤反例的通用对策）：**B 类并集必须满足"某省关键字不会在另一省作为普通文本误伤"**。
> 河南的 3 组关键字都收窄到河南省独有词（综合工日 / 台班 / 其他措施费·安文费·规费），默认空不影响 6 省。
> `strict_section_downstream` / `bracket_qty_is_unpriced` / `skip_price_col_in_qty` 是云南适配新增；`labor_name_keywords` /
> `force_emit_labor_fee` / `machine_unit_keywords` / `extra_cost_labels` 是河南适配新增（ha-2016 实测），见 §5。

理由：这些是**输出结构差异**——不同写法下 baseline 行为不同，必须由 profile 设值。

### 4.3 feature 依赖 / 互斥矩阵（v0.4 新增）

> feature 不是独立积木：以下依赖/互斥关系决定"排列组合"是否合法。组合测试（§8）必须覆盖这些关系，而不是逐个 feature 独立测。

| 关系 | 涉及 feature | 说明 |
|---|---|---|
| **B4 依赖 B1** | B4 `special_cost_rows` ← B1 `composite_label_keywords` | B4 识别"费用"行时必须**排除"全费用(元)"**——全费用是 B1 路径的 composite label（hu）。若 B1 的 label 集合改动，B4 的排除条件要同步调整 |
| **B6 影响 cost_rows 行号** | B6 `multi_label_cell_split` → P5 `fee_emit_strategy` | B6 把"其中"块 multi-label 单 cell 拆到 r..r+3 行，直接改变 P5 读取的行号。gd 触发 B6 + P5=three-correspondence 必须一起回归 |
| **跨页 feature 与 seen_pids 状态** | P7 `cross_page_strategy` + B7 `composite_table_split` | P7 依赖"已见 PID 集合"的跨表状态；与 B7（拆多张表）叠加会改变 PID 判重边界 |
| **P3 ↔ P4 联动** | P3 `section_system` ↔ P4 `section_depth` | 章节体系决定可 emit 的最大深度：alphanumeric 通常 depth=1，chinese4 可 depth=4，bj 混合体系 depth=3。非法组合如 alphanumeric + depth=4 应被 profile 校验拒绝 |
| **P2 影响 B3 检测** | P2 `material_header_layout` → B3 `special_material_categories` | B3 的"未计价/附项/人工"分类标签位置依赖 P2 列布局。P2=name-unit-price-qty（sc/hu）时 col0 无分类标签，B3 天然不触发 |

### 4.4 B 类多关键字 OR 的两个实测反例（v0.4 从 hu fixture 回归发现）

> B 类"多关键字 OR + 可选检测"并非无条件安全。以下 2 个 case 在 baseline 骨架
> 的 5 省 fixture 回归（§8 动作 6）时暴露，已修正。**新省份合并 feature 前先对照本条。**

| 反例 | 现象 | 根因 | baseline 对策 |
|---|---|---|---|
| **B1 全费用独立判定** | "全费用(元)"混入常规 composite_labels 后，hu 的 4 列全费用行（数字在 col3）漏判 composite | B5 值检查用 col4+（sc 假设），hu 全费用行只有 4 列 | 拆出 `full_cost_labels` 独立字段，独立判定：`(常规label AND col4+数字) OR (全费用label AND 全行任意数字)`——同一段代码两种判定互补，仍可进 baseline |
| **machine_labels 并集误伤** | machine_labels 并集含 gd 的"机具费"后，hu 材料名**"起重机具费"**被子串匹配误识别为机械费行 | 某省关键字在另一省作为**普通文本**出现（"机具费"⊂"起重机具费"） | baseline 默认 `machine_labels=("机械费","施工机具使用费")`（sc/cq/hu/bj 公共子集）；gd 的"机具费"作为 profile 的 B-override 字段直接设值（gd-2018 `machine_labels=("机械费","施工机具使用费","机具费")`），不依赖 features.py 检测追加 |

> 结论：B 类并集必须满足 SPEC §1.5——不仅"输出在多种写法下正确"，还要"某省关键字不会在另一省作为普通文本误伤"。遇到后者的关键字（如"机具费"），需收窄为公共子集 + 特征检测受控追加。

---

## 5. Feature flag 详细说明 + 图示文字描述

> **v0.4.3**：本文档为 8 省（sc/cq/gd/hu/bj/yn/ha/sh）。各 feature 的"典型写法"是已核实事实；**bj 已核实**：版本=2021、编码=`数字-数字`（`^\d{1,2}-\d{1,3}$`）、章节=章→节(第X节)→小节(X、) 3 层混合体系、composite label 复用 cq/sc 多关键字 OR、材料表**无单价列**（`分类|编码|名称|单位|消耗量`，量价分离，见 P2）、**无价类信息**——extract_table_bj 自闭环不 emit 费行、无料-材料费自动行 → P5/P6 均 `none`（见 §5 P5/P6）。**ha 已核实**：版本=2016、编码=`数字-数字`（`^\d+-\d+$`）、章节=chinese4、P5=three-correspondence + 河南 4 特征（见 §5 P8-P11）+ `bracket_qty_is_unpriced=True`（预制桩未计价主材）。**sh 已核实**：版本=2016、编码=`数字-数字-数字-数字`（`^\d{2}-\d+-\d+-\d+$`，04-1-1-1）、章节=**`ZH_CE`**（第一册→第一章→1．节，见 §5 P13-P15）、材料表无单价列（同 bj class-code-name-unit-qty，且**无「名称」表头** → `material_header_implicit`）、**无价类信息** → P5/P6 均 `none`（同 bj）。

下面给每个 feature 一段**文字描述**（用户拿去截图的依据）。

### B1 · `composite_label_keywords`（BASELINE）

> **是什么**：PDF 中"定额表上方汇总价格"的标签文字。
>
> **出现在哪**：定额表"项目 | 名称 | 单位 | 单价"上方一行，label + 数字（数值）。
>
> **4 省典型写法**：
> - "综合基价"（四川、广东）
> - "综合单价"（重庆、湖北 2018）
> - "全费用(元)"（湖北 2024 独有）
> - "基价(元)"（广东无"综合"前缀）
>
> **截图建议**：截一张完整的定额表，让"综合基价"或类似 label 跟一个数字一起出现在同一行。

### B2 · `material_fee_unit_modes`（BASELINE）

> **是什么**：定额表中"其他材料费"行的计量单位。
>
> **出现在哪**：材料明细表里，"其他材料费"或"其它材料费"那行的"单位"列。
>
> **两种典型写法**：
> - 单位=元 + 数量=金额数字（默认）
> - 单位=% + 数量=百分比数字（湖北 2024 独有）
>
> **v0.4 修正**：**检测自足，无开关**——`unit == "%"` 即比例行（`price` 置空、emit 时按 基数×%/100 算验证），`unit != "%"` 即金额行（`price=1.000` 兜底）。原 `material_fee_unit_percent` 开关字段从未被读取，已删除。
>
> **截图建议**：截一张定额表的"其他材料费"行，让"单位"列与"数量"列都清晰可见。

### B3 · `special_material_categories`（BASELINE）

> **是什么**：定额表"材料明细"部分，材料分类的 col0 标签。
>
> **出现在哪**：材料表的第一列，标识这一行属于哪一类。
>
> **4 省可能出现的关键字**：
> - "未计价"（四川、重庆、广东、湖北）→ 主材行
> - "附项"（广东独有，园林绿化工程）
> - "人工"（重庆、湖北独有，独立分组）
>
> **v0.4 修正**：分类检测接入配置——`parse_material_row` 读 `ctx.material_categories`（`("未计价","附项","人工")`，去空格后匹配，覆盖 OCR 空格变体）识别特殊分类；基础分类（材/料/机/机具/机械）内嵌 baseline。新省份有新分类标签，加进 `material_categories` 即可。
>
> **截图建议**：截一张定额表，让 col0 的分类标签清晰。

### B4 · `special_cost_rows`（BASELINE） ⚠️ v0.3 修正

> **是什么**：定额表"其中"块附近，识别为"非工/料/机的特殊费类"的 label。
>
> **出现在哪**：定额表"其中"块的下方一行（与人工费/材料费/机械费同级）。
>
> **4 省可能出现的关键字**：
> - "利润"（四川、重庆）
> - "管理费"（全部 4 省，但**被含"管理费"的更长 label 排除**，避免误识别"企业管理费"为"管理费"）
> - "一般风险费"（重庆独有，emit 为"综"行）
> - "增值税"（湖北独有）
> - "费用"（湖北独有，**独立行**，不是管理费/利润的子串）
>
> ⚠️ **v0.3 修正**："费用"必须**排除"全费用"**。
>   - "全费用(元)"是湖北综合基价行的 label（在 B1 路径识别为 composite），不是"费用"独立行。
>   - 如果 B4 检测时不排除，cost_rows["fee"] 会**错指全费用行**，真实"费用"值丢失。
>   - 代码位置参考：hu L449-453 `if ... and "管理费" not in full_norm and "全费用" not in full_norm`
>
> **截图建议**：截 hu 的一张表，让"费用(元)"独立行 + "全费用(元)"综合基价行同时出现。

### B5 · `composite_row_value_check`（BASELINE）

> **是什么**：综合基价 label 行内必须含数字（数值）才能认定为"综合基价行"。
>
> **出现在哪**：与 B1 同一定位。
>
> **典型形态**：label 在某单元格，数字在同行的另一个或多个单元格。
>
> **截图建议**：与 B1 共用。

### B6 · `multi_label_cell_split`（BASELINE）

> **是什么**：OCR 把多个 label 塞进同一 `<td>`（如"人工费(元)材料费(元)机具费(元)管理费(元)"），识别后按顺序拆到多个虚拟行。
>
> **出现在哪**：定额表"其中"块的首个 cell。
>
> **4 省分布**：
> - sc / cq：**不触发**（每个 label 独立 cell）
> - gd A 分册：**触发**（典型：A1-1-119）
> - hu：类似触发
>
> **检测启发式**：首个 cell 文本里 ≥2 个 label 关键字（人工费/材料费/机具费/管理费）→ 拆分；否则 no-op。
>
> **截图建议**：截 gd A 分册的一张表，让"其中"块的 4 个 label 在 1 个 cell 内紧凑相连。

### B7 · `composite_table_split`（BASELINE）

> **是什么**：一张 `<table>` 里**有多张定额表**（多张表的 col0 都标"定额编号"），按"定额编号"边界拆成多张。
>
> **出现在哪**：PDF 单页 OCR 偶尔把同一页内的多张定额表拼成 1 个 `<table>`。
>
> **4 省分布**：
> - sc / cq / hu：**不触发**
> - gd A 分册：**触发**
>
> **检测启发式**：col0="定额编号"的行数 ≥ 2 → 拆；否则 no-op。
>
> **v0.4 修正**：**检测自足，无开关**——col0="定额编号" ≥ 2 行必是复合表，拆分是唯一正确行为。原 `composite_table_split` 开关字段从未被读取，已删除。
>
> **截图建议**：截 gd A 分册的一页，让 2 个"定额编号"在同一 `<table>` 内出现。

### ~~B8~~ · `continuation_table_skip`（BASELINE）→ **v0.4 已并入 P7**

> **v0.4 变更**：从 baseline 移除，并入 `P7 cross_page_strategy`。
>
> **为什么**：跨页续表是**同一现象**——`none`（sc/cq/bj 无此情况）、`join`（gd/hu，拼接续表料/机行到主表对应 PID 段）。实测 gd/hu 走同一套 join 逻辑，原设计的 `skip`（gd 跳过重复表）从未被实现，已删除。**必须由 profile 按省设值。**
>
> **历史语义（保留作参考）**：当前页所有定额项目都已在前一页 emit 过 + 本页无"综合基价"行 → 整张表 skip。
>
> **检测启发式（归入 P7 实现）**：`seen_pids` 非空 AND 当前表所有 PID 都在 `seen_pids` 里 AND 无 composite 行 → 触发跨页判定，具体动作（join/none）由 profile 决定。

### P1 · `project_id_regex`（PROFILE FLAG）

> **是什么**：定额表里每条定额项目的"编号"，通常在 col0 或 col1。
>
> **4 种典型写法**：
> - 4-5 位字母+数字（`MB0082`、`AA0001`）—— 四川、重庆
> - 字母-数字-数字-数字（`C1-5-13`）—— 广东
> - 字母-数字-数字（`G1-1`）—— 湖北
> - 纯数字 章-定额号（`1-2`，`^\d{1,2}-\d{1,3}$`）—— 北京（v0.15.3 收紧）
>
> **截图建议**：截一张定额表，让一列定额编码清晰可见。

### P2 · `material_header_layout`（PROFILE FLAG）

> **是什么**：定额表中"材料明细表"的表头列布局。
>
> **4 种典型**：
> - `名称 | 单位 | 单价 | 消耗量`（四川、湖北）
> - `(空) | 编码 | 名称 | 单位 | 单价 | 消耗量`（重庆）
> - `分类 | 编码 | 名称 | 单位 | 单价 | 消耗量`（广东）
> - `分类 | 编码 | 名称 | 单位 | 消耗量`（**北京，无单价列**——消耗量标准量价分离，只给消耗量不给单价；v0.4 从 `_parse_bj_material_row` 核实）
>
> **截图建议**：截一张定额表的"材料明细"部分，让表头列名清晰可见。bj 样本：材料行左栏 4 列 分类/编码/名称/单位 + 右侧 N 个消耗量列。

### P3 · `section_system`（PROFILE FLAG）

> **是什么**：PDF 的"章/节"标题格式。
>
> **3 种典型**：
> - `## A.1.5 混凝土工程`（四川、重庆、广东）
> - `## 第一章 土石方工程` / `## 一、人工挖土方` / `## 1.挖土方` / `## (1)人工挖一般土方`（湖北）
> - `## 第X章` → `## 第X节` → `## X、`（北京：章→节→小节 3 层混合体系，chinese4 不适用；v0.4 从 `SECTION_ZH_*` 正则核实）
>
> **截图建议**：截 PDF 的目录页或第一页的章标题。

### P4 · `section_depth`（PROFILE FLAG）

> **是什么**：在定额 XLSX 中 emit 多少层级段行。
>
> **3 种典型**：
> - 1 级：只 emit "章"（四川、重庆、广东）
> - 3 级：emit 章/节/小节（北京，与 P3 `mixed-zh` 配套）
> - 4 级：emit 章/节/小节/小小节（湖北）
>
> **截图建议**：截 XLSX 的"定额条目" sheet 的前几行段行（4 级会有多层缩进的章节标题）。

### P5 · `fee_emit_strategy`（PROFILE FLAG） ⚠️ v0.3 行为对比 + v0.4 bj 修正

> **是什么**：定额表上方"人工费/材料费/机械费"行的 emit 策略。
>
> **3 种典型**：
> - `always`：永远 emit（四川）
> - `three-correspondence`：下方有对应分类（人工/材料/机械）则不 emit（重庆、广东、湖北）
> - `none`：**完全不 emit 费行**（北京——消耗量标准无价类信息，extract_table_bj 自闭环只出 定行+资源明细行，基价/验证恒空；v0.4 从 extract_table_bj 核实）
>
> ⚠️ **v0.3 行为对比结论**：
>
> | 费类 | sc (always) | cq/gd/hu (three-correspondence) | 是否一致？ |
> |---|---|---|---|
> | 人工费 | 有就 emit | 有就 emit (sc 无"工"分类，has_labor_below 始终 False) | ✅ 一致 |
> | 材料费 | `has_unpriced` OR (`not has_priced_material` AND `>0`) | `not has_material_below` AND `>0` | ❌ 不一致（sc 即使有材料明细也 emit，cq/gd/hu 不 emit） |
> | 机械费 | 有就 emit | `not has_machine_below` AND `>0` | ❌ 不一致（sc 即使有机械明细也 emit，cq/gd/hu 不 emit） |
>
> → **flag 必须保留**（材料费、机械费两种 emit 条件输出结构不同）。
>
> **截图建议**：截 sc 的机械费/材料费行（同时含"机"分类明细）vs cq/gd/hu 的对应位置，对比 emit 行数差异。

### P6 · `material_fee_auto_emit`（PROFILE FLAG）

> **是什么**：定额条目是否自动 emit 一个"料 材料费"行（用于 verify 列加和 = 综合基价）。
>
> **各 style 精确 emit 条件（v0.4 从实际代码核实）**：
>
> | style | emit 条件 | 代码依据 |
> |---|---|---|
> | `sc-style` | `has_unpriced`（表里有未计价主材）**OR**（`not has_priced_material` AND 材料费值>0）——有未计价主材，或无已计价料行但"其中-材料费(元)>0" | sc `extract_table` 内 `should_emit_material_fee` |
> | `cq-style` | 材料费行存在 AND `not has_material_below`（下方无料/配分类）AND 值>0——**无全局 PID 检查** | cq `extract_table` 料-材料费分支 |
> | `gd-style` | cq 条件 + `not pid_material_global`（该 PID 全局无材料明细） | gd `extract_table` |
> | `hu-style` | 同 gd（含 `pid_material_global` 检查） | hu `extract_table` |
> | **bj** | **= `none`**（无料-材料费自动行）——extract_table_bj 自闭环不 emit 任何自动行，基价/验证恒空 | bj `extract_table_bj` emit 段（仅 定行+资源明细+空行） |
>
> **说明**（v0.4 修正）：bj 走 `extract_table_bj` 自闭环，**不走**通用 `extract_table`（含料-材料费自动行的那份）——所以 bj P6 = `none`，此前"bj=hu 型"的判断有误。gd/hu 条件相同（含 `pid_material_global`），cq 缺该检查，sc 走 `has_unpriced` 驱动。若未来某省出现新条件，再新增 style。
>
> **截图建议**：截一张定额表，让"其中-材料费(元)" + 主材行都清晰。

### P7 · `cross_page_strategy`（PROFILE FLAG）⚠️ v0.4 由 B8 + 原 P7 合并

> **是什么**：定额表被切到下一页时，整张续表怎么处理。v0.4 起合并原 baseline 的 `continuation_table_skip`（B8）与 profile 的 `cross_page_table_join`（原 P7）。
>
> **取值**：
> - `none`：无跨页续表（sc / cq / bj）
> - `join`：跨页拼接续表——extract_table 检测 continuation（seen_pids 全见 + 无基价行）→ 抽续表料/机行 → process_md_file 插入主表对应 PID 段（gd 跨页 + hu p025→p026 都是真实场景）
>
> **v0.4 修正**：删 `skip`。原设计 gd=skip（续表整张跳过）从未被实现——gd 实际也走 join（extract_table 检测后拼接），与 hu 同逻辑。
>
> **截图建议**：截 gd/hu 跨页续表（前一页末尾 + 当前页开头，相同 PID 重复 + 续表料/机明细）。

### P8 · `labor_name_keywords`（PROFILE 的 B 类 override，河南适配）⚠️ v0.4.2 新增

> **是什么**：材料明细行**名称**含指定关键字 → 该行 category="工"（人工明细）且**不计验证**。
> baseline 默认靠 col0 分类标签（B3：col0="人工"）判人工；河南材料区**无 col0 分类标签**（分类列用横线分隔、不写名称），
> 人工行的特征只剩：名称="综合工日" + 单价无效（'-'）+ 数量带括号（(10.37)）。
>
> **为什么不计验证**：综合工日无单价，`数量×单价` 算不出人工费。河南定额的人工费以**费行**（"人工费(元)"）为准——
> 明细行的综合工日只保留消耗量作参考，验证列留空。
>
> **依赖**：与 `force_emit_labor_fee` 成对——明细不计验证，人工费必须由费行承担（否则验证和缺人工费，对不上基价）。
>
> **检测自足性**（features.py `detected["labor_name_keywords"]`）：判别器 = **"综合工日"行 + col0 无 B3 分类标签**。
> 河南 col0=名称直接起（无标签）→ 命中；yn col0="人工" 标签（B3 已处理）→ 不命中；sc/gd/hu 无综合工日明细行 → 不命中。
> 6 省 fixture 实测只有 ha 触发 → 检测自足 → 走 `b_switches`，无冲突。
>
> **河南输出示例**（1-1 人工挖一般土方）：
> ```
> 定 1-1 ... 基价 282.97  验证 282.97
> 工   人工费        182.56
> 工   综合工日      2.10  单价"—" 验证空
> 综   企业管理费     19.87
> ...
> ```

### P9 · `force_emit_labor_fee`（PROFILE FLAG，河南适配）⚠️ v0.4.2 新增

> **是什么**：人工费费行**强制 emit**，绕过 P5 `fee_emit_strategy` 的"下方有人工明细则隐藏"逻辑。
>
> **为什么**：P5=three-correspondence（cq/gd/hu/ha 公共）默认"下方有人工明细 → 不 emit 人工费行"，因为明细行验证和能凑出人工费。
> 河南综合工日**无单价**（`数量×单价` 算不出值），明细验证和为 0，人工费必须写费行，否则验证和缺人工费、对不上基价。
> 材料费/机械费行**不强制**（河南材料/机械明细有单价，能正常凑和 → 仍走 three-correspondence 隐藏）。
>
> **实测**：河南 1-1 人工挖一般土方，`基价 282.97 = 人工费 182.56 + 企业管理费 19.87 + 利润 16.46 + 其他措施费 10.92 + 安文费 23.73 + 规费 29.43`——综合工日无单价（验证空），人工费行强制 emit 后验证和恰好=基价。
>
> **与其他省对比**：cq/gd/hu 人工明细有单价 → three-correspondence 足够，`force_emit_labor_fee=False`。

### P10 · `machine_unit_keywords`（PROFILE 的 B 类 override，河南适配）⚠️ v0.4.2 新增

> **是什么**：材料明细行**单位**含指定关键字 → category="机"（机械）。
> baseline 默认靠 col0 分类标签（B3：col0="机械/机具"）判机械；河南材料区**无 col0 分类标签**，
> 区分材料/机械只能靠**单位="台班"**（机械才有"台班"）。
>
> **检测自足性**（features.py `detected["machine_unit_keywords"]`）：判别器 = **单位 cell 精确等于"台班" + col0 无 B3 分类标签**。
> 河南 col0=名称直接起 → 命中 883 行；gd/hu/yn col0="机具"/"机械" 标签（B3 已处理）→ 不命中；
> sc 无"台班"单位（柴油 L / kg 燃料）→ 不命中。6 省 fixture 实测只有 ha 触发 → 走 `b_switches`，无冲突。
> 注意用**精确匹配**：子串匹配会误伤 gd 调整系数表"人工、机械台班费用调整系数"、hu 材料名"灰浆搅拌机(台班)"。
>
> **河南输出示例**（1-37 推土机推运土方，机械明细区）：
> ```
> 机 履带式推土机功率(kW)105   台班  0.022  988.27  21.74
> ```
> 机械费行"机械使用费(元)"仍按 three-correspondence 隐藏（明细有单价能凑和）。

### P11 · `extra_cost_labels`（PROFILE 的 B 类 override，河南适配）⚠️ v0.4.2 新增

> **是什么**：费行 label → 综行名的映射。河南基价表"其中"区有 **9 项费用**：人工费/材料费/机械使用费/其他措施费/安文费/管理费/利润/规费，
> baseline 默认只识别 管理费/利润/费用/增值税/一般风险费 五种综行 → 河南的 其他措施费/安文费/规费 必须走本字段各自独立成综行。
>
> **为什么每个都独立成行**：河南基价构成含这些费，验证和=基价 必须把它们都计入（实测 2-6：
> `基价 1387.84 = 人工费 370.67 + 材料费 782.50 + 机械 4.26 + 其他措施费 15.24 + 安文费 33.12 + 管理费 85.32 + 利润 55.67 + 规费 41.06`）。
>
> **实现**：`find_cost_rows` 把 label 写进 `cost_rows`（若未被已有费类占用），`extract_table` 综行段按 label 逐个 emit + 计入 verify。
> 默认空 dict → 6 省不受影响。
>
> **检测自足性**（features.py `detected["extra_cost_labels"]`）：判别器 = **label 费行 + 与 管理费/利润 平级同表**。
> 河南 9 费平级（其他措施费/安文费/规费 与 管理费/利润 同层）→ 命中；yn 规费是人工费的**子行**
> （定额人工费+规费=人工费，非平级费行）→ 不命中；hu 规费只在总说明文字（取消规费项目单列）→ 不命中。
> 6 省 fixture 实测只有 ha 触发 → 走 `b_switches`，无冲突。

### P12 · `bracket_qty_is_unpriced` 范围扩到河南（v0.4.2 修正）

> 原为云南适配（§4.2）。河南实测同样语义：**非人工行**的括号数量 + 单价无效（'-'）= 未计价主材。
> 例（3-5 静力压预制钢筋混凝土方桩）：`预制钢筋混凝土方柱 单价'-' 数量(10.10)` → 主材行（桩价值未计入基价，
> 材料费只含 白棕绳/垫木/金属周转材料 等有单价的辅材）。
>
> **河南 46 个非综合工日括号行全部单价'-'**（脚本核实 0 个带数字单价）→ ha-2016 `bracket_qty_is_unpriced=True` 无歧义。
> 综合工日的括号数量由 P8（labor_name_keywords，category 先判为"工"）兜住，不进未计价判定（L709 只对 料/配 生效）。

### P13 · `material_header_implicit`（PROFILE FLAG，上海适配）⚠️ v0.4.3 新增

> **是什么**：材料区**无「名称」表头行**。baseline 默认靠 col0='名称'（sc/hu）/'工料机名称'（bj）定位材料区；
> 上海表结构直接从 定额编号/项目/单位 块跳到 `人工|编码|名称|单位|数量` 分类行，中间无表头行。
>
> **实现**：无 material_header 时，材料起点 = 项目块后第一个 col0 为 `_material_row_no_price` 接受的
> 分类标签（人工/材料/机械/材/料/机/机具）的行。
>
> **上海表结构**（与 bj 同 class-code-name-unit-qty，但无表头）：
> ```
> 定额编号 | 04-1-1-1 | 04-1-1-2 | ...
> 项目 | 单位 | 耕地填前处理 | 人工挖土方 ...
>       |      | $m^3$       | $m^3$
> 人工 | 00070111 | 综合人工(土建) | 工日 | 0.1440 | ...
> 机械 | 99010060 | 履带式单斗液压挖掘机 1m3 | 台班 | 0.0035 | ...
> ```

### P14 · `project_unit_last_line`（PROFILE FLAG，上海适配）⚠️ v0.4.3 新增

> **是什么**：项目单位 = 项目/子目名称 block **最后一行**。上海表**无「计量单位」行**（bj 有），
> 项目单位是项目名称块末行（如 `$m^3$`）。baseline 默认只在该行='见表' 时才用最后一行覆盖；
> 上海无计量单位行且无 '见表' → 需强制用最后一行。
>
> **上海定行示例**：`定 04-1-1-1 耕地填前处理 挖腐植土 m^3 ... 单位列=m3`

### P15 · `dedup_section_ids`（PROFILE FLAG，上海适配）⚠️ v0.4.3 新增

> **是什么**：段行 sec_id **全局去重**。上海 OCR 把 `## 1． 一般土方` 节头在**每一页**重复（一页一个），
> 导致同一节段行 emit 4-6 遍。
>
> **⚠️ 只可对段行 code 全局唯一的省开启**：zh-ce（册.章.节）天然唯一 → sh 可开。
> sc/hu 的段行 code **跨卷合法重复**（如 sc 多卷共用 `A.1.1`）→ 开去重会误删合法段行（v0.4.3
> 实测 sc 从 2173 掉到 1937、hu 687→646 后回滚）。**新省份若开此 flag，先确认其段行 code 无重复。**

### P16 · `section_system=ZH_CE`（上海章节体系，v0.4.3 新增枚举）

> **是什么**：`第X册 → 第X章 → 1．节` 3 层（markdown 全 `##` 平铺）。
> - 册：`第一册`/`第 一 册` → 册号（`cur_vol` 跨表持久，作段行前缀）
> - 章：`第一章` → `{册}.{章}`
> - 节：`1．`/`1.` → `{册}.{章}.{节}`（数字节直接挂章下，跳过 chinese4 的 `一、` 级）
> - 跳过：TOC 点线条目（含 `2+ 连续点` 的拆行变体）、`一、二、` 规则条目（工程量计算规则/册说明正文）
> - 节头去重走 `dedup_section_ids`（P15）
>
> **段行示例**：`2.1 路基处理` → `2.1.1 1． 掺石灰` ... `2.1.14 14． 路基排水`（第二册 道路工程）。

---

## 6. 自动检测阶段（Point 1 细化）

### 6.1 找候选定额表

```python
candidates = []
for each <table> tag in md_text:
    prefix_lines = 5 lines before <table>
    if any("工作内容" in line for line in prefix_lines):
        candidates.append(<table>)
```

### 6.2 从候选表推断 feature

对每张候选表，扫描结构推断：

| 自动推断的 feature | 启发式 |
|---|---|
| B1 `composite_label_keywords` | 综合基价行 hit 哪个 label 关键字 |
| B2 `material_fee_unit_modes` | 看材料行 unit 是否含 `%`（检测自足，无需开关） |
| B3 `special_material_categories` | 看 col0 是否出现 未计价/附项/人工（集合来自 profile/ctx 配置） |
| B4 `special_cost_rows` | 看"其中"块附近是否出现 一般风险费/增值税/费用 |
| B5 `composite_row_value_check` | label + 数字同行 |
| B6 `multi_label_cell_split` | 看 `其中` 块首个 cell 是否含 ≥2 个 label |
| B7 `composite_table_split` | 看 `<table>` 内 col0="定额编号" 行数 ≥ 2（检测自足，无需开关） |
| P1 `project_id_regex` | 候选表里出现的编码命中哪个 regex |
| P2 `material_header_layout` | 看 `名称`/`编码`/`分类` 在哪一列 |
| P3 `section_system` | 看 markdown `## ` 标题命中哪个 regex |
| P4 `section_depth` | 看目录页层级深度（1 vs 4） |
| `bracket_qty_is_unpriced`（**冲突，需 profile**） | 材料行数量带括号：sc/hu=比例行"配" / yn/ha=未计价主材 → features.py 报 p_conflicts，profile 定值 |
| `labor_name_keywords`（检测自足） | 名称含"综合工日" → 人工明细（河南；无 col0 标签）→ b_switches |
| `machine_unit_keywords`（检测自足） | 单位含"台班" → 机械（河南无分类标签，靠单位区分）→ b_switches |
| `extra_cost_labels`（检测自足） | 费行出现 其他措施费/安文费/规费 → 各自综行 → b_switches |

**关键原则**：自动勾选 = 脚本有充分依据；脚本无依据 → 该 feature 不勾选（让人工决定）。

> ⚠️ **v0.4 显式声明：P5 / P6 / P7 不可自动推断。**
> - P5 `fee_emit_strategy`、P6 `material_fee_auto_emit` 是**行为特征**（emit 条件），扫 MD 推不出来；
> - P7 `cross_page_strategy` 虽可检测"存在重复 PID 表"现象，但无法自动判定 join / none——二者是相反行为；
> - 这三项**不显示勾选框**（或 UI 只读），直接由 profile 设值。其余 feature 走"自动勾选 + 人工修正"。

---

## 7. 待讨论 / 未决项

| # | 问题 | 倾向 |
|---|---|---|
| 1 | 规则脚本无能为力的 feature 时，UI 怎么呈现？ | 仅显示图示 + 默认 unchecked，让用户决定 |
| 2 | profile 改名后，关联档案怎么办？ | 历史档案保留旧 profile 引用；新档案用新 profile |
| 3 | feature flag schema 版本化？Pydantic 模型加 version 字段？ | **采纳（v0.4）**：加 version 字段。profile 是持久化数据、关联档案，schema 演进必须有版本号，否则历史档案引用会失效 |

---

## 8. 后续动作

1. 复制所需文件到 `quota-unified/`
2. 列 feature 文本描述 → 用户截图
3. 用 Pydantic 写 feature flag schema（基于 §4 清单）
4. 写 baseline extractor 骨架（合并 §4.1 8 个 BASELINE feature）
5. 写规则脚本（基于 §6 启发式）
6. 在 5 省 fixture（sc/cq/gd/hu/bj）上跑 baseline，对比输出与原 extractor 是否一致
   - **一致性判据**（v0.4 明确）：行数 diff 必须为 0；项目编号 / 材料行 / 段行的关键字段逐行 diff 必须为空；输出写 golden 文件，diff 非空即 fail（自动判定，不靠人眼）
7. Web UI 草图（基于 §2 流程）
8. 测试套件

---

## 9. 新省份验收清单（v0.3 新增）

> **用途**：拿一份新省份定额 PDF，对照下面 15 个 feature 一一打勾，验证：
> 1. 是否所有特征都已包含（不缺）
> 2. 每个特征是否归类正确（不冲突）
>
> **使用流程**：
> 1. OCR 出一份 MD（至少前 20 页 + 一张含定额表的样本页）
> 2. 对照清单打勾（√ = 出现，× = 不出现，⚠️ = 出现但与 5 省写法不同）
> 3. 把结果拿回来，决定哪些新特征需要新增 flag / 调整 baseline

### 9.1 8 个 BASELINE 验收

| # | feature | 检查方法（PDF/MD 上看什么） | 出现的省（已知） |
|---|---|---|---|
| **B1** | `composite_label_keywords` | 在定额表上方一行搜"综合基价"/"综合单价"/"全费用(元)"/"基价(元)" | sc/cq/gd/hu 各有 |
| **B2** | `material_fee_unit_modes` | 在材料表里搜"其他材料费"行，看"单位"列是"元"还是"%"（检测自足，无开关） | hu 用 %，其他用 元 |
| **B3** | `special_material_categories` | 在材料表 col0 搜"未计价"/"附项"/"人工"（集合来自 profile/ctx 配置） | 5 省都可能 |
| **B4** | `special_cost_rows` | 在"其中"块附近搜"利润"/"管理费"/"一般风险费"/"增值税"/"费用" | 各省略不同 |
| **B5** | `composite_row_value_check` | 看 B1 的 label 同行是否有数字 | 全部 |
| **B6** | `multi_label_cell_split` | 看"其中"块首个 cell 是否含 ≥2 个 label 关键字 | gd/hu 有，sc/cq 无 |
| **B7** | `composite_table_split` | 看一张 `<table>` 内 col0="定额编号"的行数是否 ≥2（检测自足，无开关） | gd 有，其他无 |
| ~~B8~~ | ~~`continuation_table_skip`~~ | ~~看跨页时是否所有 PID 都重复且无 composite 行~~ | **v0.4 已并入 P7（none/join）**，见 §9.2 |

### 9.2 7 个 PROFILE FLAG 验收

| # | feature | 必须设的值（候选） | 检查方法 |
|---|---|---|---|
| **P1** | `project_id_regex` | `^[A-Z]{1,2}\d{4}$` / `^[A-Z]\d+-\d+-\d+$` / `^[A-Z]\d+-\d+$` / **`^\d{1,2}-\d{1,3}$`（bj）** / **(待新增)** | 抓 5 个 col0 看匹配哪种；bj=数字-数字（v0.15.3 收紧） |
| **P2** | `material_header_layout` | `name-unit-price-qty` / `code-name-unit-price-qty` / `class-code-name-unit-price-qty` / **`class-code-name-unit-qty`（bj）** / **(待新增)** | 看材料表表头列名；bj=分类/编码/名称/单位/消耗量，**无单价列**（量价分离） |
| **P3** | `section_system` | `alphanumeric` / `chinese4` / **`mixed-zh`（bj：第X章→第X节→X、）** / **(待新增)** | 看 `## ` 标题格式；bj 是中文+数字 3 层混合体系，chinese4 不适用 |
| **P4** | `section_depth` | `1` / **`3`（bj）** / `4`（hu） / **(待新增)** | 看 XLSX 段行层级（运行后看输出）；bj=章→节→小节 3 层 |
| **P5** | `fee_emit_strategy` | `always` / `three-correspondence` / **`none`（bj）** / **(待新增)** | 看材料表是否有"工/料/机"分类 col0；bj=无价类信息，不 emit 费行 |
| **P6** | `material_fee_auto_emit` | `none` / `sc-style` / `cq-style` / `gd-style` / `hu-style` / **(待新增)** | 看"材料费 auto 行"的 emit 条件；**bj=none**（extract_table_bj 无此机制，条件见 §5 P6） |
| **P7** | `cross_page_strategy` | `none` / `join` / **(待新增)** | 看是否有跨页续表场景；gd/hu=join、sc/cq/bj=none（v0.4 删 skip：gd 实测也是 join） |

### 9.3 关键判定问题

带回来这几个问题：

1. **有没有"5 省都没出现"的新特征？**
   - 比如：定额表外有"附注"列、"适用范围"列、"工程量计算规则"列、"施工方法"列等
   - 比如：表格里有"图片说明"或"脚注"列
   - 比如：PDF 双栏排版、定额跨多页纵向拼接、PDF 章节用罗马数字

2. **有没有"5 省写法都不同"的新特征？**
   - 比如：项目编码除了 3 种，是否还有"纯数字""带前缀（如 XJ-123）"等
   - 比如：章节除了 alphanumeric 和 chinese4，是否还有"罗马数字""纯汉字"等

3. **有没有"和现有某特征语义重复但必须分开"的情况？**
   - 比如：B4 现在 5 个 label 都是特殊费类，新省份出现"间接费"算不算？
   - 比如：B3 现在 3 个 label 都是材料分类，新省份出现"周转材料"算不算？

### 9.4 测试 fixture 准备

拿新省份 PDF 后，建议准备（**bj 已是现成样本**：`quota/parser/tests/beijing/` 有 3 本《北京市 2021 预算消耗量标准》（仿古建筑 / 建筑与装饰上 / 建筑与装饰下），可直接跑一遍 §9 清单，验证 B/P 分类对第 5 省是否成立、是否需要新 flag）：
- 至少 3 张定额表样本（开头/中间/结尾）
- 每张表的完整 MD（OCR 后）+ HTML（若有）
- 对照原 5 省输出格式写"预期输出 CSV/XLSX"

跑 baseline extractor 时，对比"实际输出" vs "预期输出"，看哪些 feature 没覆盖。