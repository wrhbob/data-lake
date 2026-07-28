# SPEC.md — quota-csv-finalize

> 定额 xlsx **5 步**清洗流水线的输入输出与行为规范（XLSX 版）。
> 版本：v2.1（2026-07-27：CSV 默认不产 / 5 步原地覆盖同一 `<stem>_待审核.xlsx` / drop_toc_sections 新增）
> 适用：配合 `quota-md-to-csv-v2/extract_quota.py` 产出的多 sheet xlsx，做最终清洗与格式规整。
>
> **本 SPEC 是单一权威**：与本目录的 `SKILL.md` / `README.md` 冲突时，以本 SPEC 为准。
> 本目录的所有脚本均按本 SPEC 实现；改动脚本前请先改本 SPEC。

---

## §0 一句话

把 `extract_quota.py` 产出的多 sheet `<stem>_待审核.xlsx`（**必须先经人工核对「定额条目」sheet**），依次跑 **5 个独立脚本**，全部 in-place 覆盖同一个 xlsx，最终交付带 Excel 4 级分组 + 段行合并的 `<stem>_待审核.xlsx`。5 步**只改写「定额条目」sheet**；「册说明」/ 各章 sheet 全程保留不动。5 步在 `extract_quota.py` 内被默认串联执行（`autofinalize`），无需手工跑 5 条命令。

---

## §1 输入契约：上游 xlsx 格式

> 单一来源：`quota-md-to-csv-v2/extract_quota.py` 的产出物（参见 `quota-md-to-csv-v2/SPEC.md`）。
> finalize **不做**数据正确性校验——本节只是契约说明，不是验收。

### §1.1 物理格式

| 项 | 值 |
|---|---|
| 编码 | xlsx 容器（ZIP），内部 XML UTF-8 |
| sheet 数量 | 多 sheet（定额条目 / 册说明 / 各章；sheet 顺序：定额条目 → 册说明 → 各章） |
| 「定额条目」sheet 数据 | 10 列定额数据（同 v1 CSV 列结构） |
| 「册说明」/ 各章 sheet | 叙述文本；A1 = 章说明、A2 = 工程量计算规则（章 sheet） |
| 行长度 | 固定 10 列；不足 10 列的异常行由 extract_quota 兜底为 10 个空字符串 |

### §1.2 行类型 enum（7 种 + 1 种空行）

| 值 | 含义 | 出现位置 |
|---|---|---|
| `段` | 章节标题（分部 / 分项 / 子目） | 章节首行 |
| `定` | 定额条目首行 | 每个定额第一条 |
| `工` | 人工费 | 定行后 |
| `料` | 材料明细（消耗量无括号） | 定行后 |
| `配` | 材料明细（消耗量带括号，如 `(32.250)`） | 定行后 |
| `机` | 机械费 / 机械标题下的燃料 | 定行后 |
| `综` | 企业管理费 / 利润 | 定行后 |
| （空） | 完全空行（10 个空字符串） | 定额条目之间的分隔 |

注意：finalize 不引入新行类型；行类型 enum 与 `quota-md-to-csv/SPEC.md §3.2` 完全一致。

### §1.3 10 列字段定义

| 索引 | 字段 | finalize 是否修改 |
|---|---|---|
| 0 | 类型 | ❌ 不改 |
| 1 | 项目编码 | ❌ 不改 |
| 2 | 名称 | ⚠️ **仅 `料`/`配` 行**被 `space_split_materials.py` 修改 |
| 3 | 项目特征 | ⚠️ **仅 `定` 行**被 `fill_work_content.py` 修改 |
| 4 | 计量单位 | ❌ 不改（已由 `extract_quota.py` 完成 LaTeX 归一） |
| 5 | 消耗量 | ❌ 不改 |
| 6 | 基价/单价 | ❌ 不改 |
| 7 | 验证 | ❌ 不改 |
| 8 | 标准换算 | ❌ 不改（恒空，预留列） |
| 9 | 标准换算来源 | ❌ 不改（恒空，预留列） |

每行形态：
- **`段` 行**：列 0 = `段`，列 1 = 编号（如 `A` / `A.1`），列 2 = 章节名，其余 7 列为空。
- **`定` 行**：列 0–6 有内容（消耗量列空），列 7–9 空。
- **`工`/`机` 行**：列 4 = `元`，列 6 = `1.00`，列 7 = 验证值。
- **`料`/`配` 行**：列 2 = 材料名（待 `space_split_materials` 规整），列 4 = 单位，列 5 = 消耗量，列 6 = 单价。
- **`综` 行**：列 4 = `元`，列 5 = `100.00`，列 6 = 金额，列 7 = 验证值。
- **空行**：10 个空字符串。

### §1.4 finalize 之前必须做的人工核对（硬前置契约）

> finalize **不做**数据正确性校验；OCR 抖动 / 行类型错配 / 字段污染一旦进入就固化到 xlsx。
> 以下问题**必须在 finalize 之前**由人工在 `<stem>.xlsx` 的「定额条目」sheet 上核对：

| 核对维度 | 典型问题 | 改在 finalize 之前哪个阶段处理 |
|---|---|---|
| 段行是否齐全 | 小类如 `A.9.2` 缺失；编号带空格如 `L. 8` 没识别 | 在「定额条目」sheet 直接补段行 |
| 定额行类型 | 材料标题下的料 vs 机械标题下的机错位 | 修改行类型 enum |
| 利润行 | OCR 切成 `利`/`润`/`和润`（extract_quota 已放宽匹配） | 检查是否已识别 |
| 单位归一 | `$100m^2$` → `100m2`；`$m^3$` → `m3` | 已在 extract_quota 完成 |
| 工作内容多行 | 跨页切到下一页可能被截断 | 由 fill_work_content 前向传播兜底 |
| 异常表 | extract_quota 跳过的表以全空行占位 | 问题详情在 `<stem>_issues.md` |

人工核对通过后，再调用 `clean_empty_qty.py`。

---

## §2 输出契约：最终 xlsx

### §2.1 物理格式

| 项 | 值 |
|---|---|
| 格式 | `.xlsx`（OOXML） |
| 生成工具 | `openpyxl`（≥ 3.1.5） |
| 工作表 | **多 sheet**：`定额条目`（首位，套了 4 级分组 + 段行 C-D 合并）/ `册说明` / 各章（A / B / ...） |
| 文件名 | `<stem>_待审核.xlsx`（5 步全部原地覆盖同一文件，由 `extract_quota.py` 一行命令出） |
| 中间产物 | **无**（v1 的 `<stem>_final.xlsx` 已被取消） |

> 多 sheet 在 finalize 全程保留：5 步**只改写「定额条目」sheet**；`册说明` 与各章 sheet 不动。
> Sheet 顺序硬约束：`定额条目` → `册说明` → 各章（与 v2 抽取器输出一致）。

### §2.2 列序

与输入「定额条目」sheet **完全一致**（10 列）：不做列重排、不删列、不增列。

### §2.3 4 级分组（Excel 大纲，仅作用于「定额条目」sheet）

| 层级 | outline_level | 触发条件（`段` 行第 2 列编号正则） | 折叠方向 |
|---|---|---|---|
| 1（大类） | 1 | `^[A-Z]$`（无点号） | 标题在上 |
| 2（中类） | 2 | `^[A-Z]\.\d+$`（一个点号） | 标题在上 |
| 3（小类） | 3 | `^[A-Z]\.\d+\.\d+$`（两个点号） | 标题在上 |
| 4（定额） | 4 | `定` 行（任何编号） | 标题在上 |

`sheet_properties.outlinePr.summaryBelow = False`：所有层级统一"标题在上、内容在下"折叠。

**段范围规则**：A 段（A 标题 + 下一段之前的所有内容）的范围延伸到下个 depth ≤ 1 的段之前；A.1 的范围延伸到下个 depth ≤ 2 的段之前；以此类推。中类下无小类时，定额直接挂在中类下，不会出错。

**定额范围规则**：定额的范围延伸到下一定额之前，再限制在所属段的 end_row 之内；保证定额不会跨段归属。

### §2.4 段行单元格合并（C-D 列，仅作用于「定额条目」sheet）

所有 `段` 行的第 3 列（名称）与第 4 列（项目特征，本就为空）合并为 1 个 cell。
非 `段` 行（`定`/`工`/`料`/`配`/`机`/`综`/空行）的 C-D 列**保持独立**，不做合并。

### §2.5 段行样式

- 字体：`Font(bold=True)`
- 对齐：`Alignment(horizontal="left", vertical="center")`
- 非 `段` 行不应用此样式（保持默认）。

---

## §3 5 步流水线契约

### §3.1 整体范式（in-place 覆盖 + 多 sheet 保留，立法约定）

```
extract_quota.py
   │
   ▼  <stem>_待审核.xlsx  ←── 多 sheet；必须经人工核对「定额条目」sheet（§1.4）
   │
   │  autofinalize（在 extract_quota.py 内 subprocess 串联跑下面 5 步）：
   │
   ├─ 1) clean_empty_qty.py       →  <stem>_待审核.xlsx（原覆盖）
   ├─ 2) drop_toc_sections.py     →  <stem>_待审核.xlsx（原覆盖）
   ├─ 3) fill_work_content.py     →  <stem>_待审核.xlsx（原覆盖）
   ├─ 4) space_split_materials.py →  <stem>_待审核.xlsx（原覆盖）
   └─ 5) to_xlsx.py               →  <stem>_待审核.xlsx（原覆盖；套 4 级分组 + 多 sheet 保留）
```

**关键简化（2026-07-27）**：v1 的"`<stem>_数值待审核.xlsx` → 用户改名为 `<stem>.xlsx` → clean → fill → space → to_xlsx → `<stem>_final.xlsx`"中转链已被取消。**5 步全部原地覆盖同一个 `<stem>_待审核.xlsx`**，没有中间 xlsx、没有目录流转、没有 advance_stage.py。

### §3.2 5 步缺一不可

跳过任一步会导致 xlsx 缺失对应处理：

| 跳过 | 后果 |
|---|---|
| 1 `clean_empty_qty.py` | 空消耗量残留 + 空分隔行残留 |
| 2 `drop_toc_sections.py` | 目录行误捕残留（如 `A.1 绿地整理 …… (11)`）污染段行统计 |
| 3 `fill_work_content.py` | 空工作内容残留（OCR 跨页丢失的场景没兜底） |
| 4 `space_split_materials.py` | 材料名粘连（汉字+字母数字紧贴，可读性差） |
| 5 `to_xlsx.py` | 没有 Excel 分组 + 段行 C-D 合并 + 段行加粗 |

### §3.3 各步 CLI 签名

| 步骤 | 脚本 | 签名 | 默认输出策略 |
|---|---|---|---|
| 1 | `clean_empty_qty.py` | `<input.xlsx>` 或 `<input.xlsx> <output.xlsx>` | 原地覆盖输入（`output_path = input_path`） |
| 2 | `drop_toc_sections.py` | `<input.xlsx>` 或 `<input.xlsx> <output.xlsx>` | 原地覆盖输入 |
| 3 | `fill_work_content.py` | `<input.xlsx>` 或 `<input.xlsx> <output.xlsx>` | 原地覆盖输入 |
| 4 | `space_split_materials.py` | `<input.xlsx>` 或 `<input.xlsx> <output.xlsx>` | 原地覆盖输入 |
| 5 | `to_xlsx.py` | `<input.xlsx>` 或 `<input.xlsx> <output.xlsx>` | 原地覆盖输入 |

传第 2 个参数 = 写入新文件（不修改输入）；不传 = 原地覆盖。
各步输入/输出文件名都是同一个 `<stem>_待审核.xlsx`（5 步共读写同一文件）。

### §3.4 标准跑批范式（手工模式，强制约定）

> **日常使用**：用 `extract_quota.py` 一行命令即可（autofinalize 5 步默认串联），见 `quota-md-to-csv-v2/SPEC.md` §调用范式。本节是**手工跑 5 步**的退化路径，用于单独重跑某一步。

```bash
PY=/d/miniconda3/envs/DLSE/python.exe
FIN=.claude/skills/quota-csv-finalize
XLSX="<stem>_待审核.xlsx"

# Step 1：clean（原覆盖 XLSX；删空消耗量 + 空分隔行）
$PY $FIN/clean_empty_qty.py "$XLSX"

# Step 2：drop_toc（原覆盖 XLSX；删"目录行误捕"段行）
$PY $FIN/drop_toc_sections.py "$XLSX"

# Step 3：fill（原覆盖 XLSX；定行第 4 列前向传播）
$PY $FIN/fill_work_content.py "$XLSX"

# Step 4：space（原覆盖 XLSX；料/配 行第 3 列汉字↔字母空格）
$PY $FIN/space_split_materials.py "$XLSX"

# Step 5：to_xlsx（原覆盖 XLSX；套 4 级分组 + 段行 C-D 合并 + 多 sheet 保留）
$PY $FIN/to_xlsx.py "$XLSX"
```

注意：5 步全部用同一个 `<stem>_待审核.xlsx` 作为输入；中间无 xlsx 文件。

---

## §4 各步详细规则

### §4.0 drop_toc_sections.py — 删「目录行误捕」的纯段行（2026-07-27 新增）

> **设计动机**：MD 文件的 `## 目录` TOC 区里，章节标题带页码 `……(NN)` 或 `.....(NN)` 后缀
> （如 `A.1 绿地整理 …… (11)`），抽取器按"目录区即正文区"统一解析，把这些带页码的目录行误
> 当成"段行"写入 xlsx。它们**不是**真实章节，且下方不会挂任何 `定/工/料/配/机/综` 子项——
> 因为它们对应的 PDF 页码只指向 TOC 区。
>
> 园林绿化工程样本：112 个 TOC 段行（111 个 `……(NN)` + 1 个 `.....(NN)` ASCII 变体）全部为
> 纯段行（无子项），删后段数 233 → 121（不含目录误捕），定额数 1002 不变。

**触发条件**：`<stem>_待审核.xlsx`（clean 之后、fill 之前）。

**输入 / 输出**：`<stem>_待审核.xlsx` → `<stem>_待审核.xlsx`（原地覆盖；§3.4 Step 2）。仅修改「定额条目」sheet。

**编码**：xlsx 容器（ZIP），内部 XML UTF-8。

**处理逻辑**（3 条规则）：

| # | 场景 | 处理 |
|---|---|---|
| 1 | `段` 行第 3 列（名称）匹配 `_TOC_PATTERN` | 视为"目录行误捕"，进入待删集合 |
| 2 | 待删段行**无**任何 `定/工/料/配/机/综` 子项（直到下个段行之前都是空行） | 删除该段行 |
| 3 | 待删段行**有**子项（防御性：极少出现）| **保留**，stderr 打 `[WARN]` 提醒人工复核 |

**TOC 模式正则**：

```python
_TOC_PATTERN = re.compile(r"[….]{2,}\s*[\(（]\d+[\)）]")
```

- `[….]{2,}`：至少 2 个连续省略号（中文 `…` U+2026 或英文 `.` ASCII 都行；混合也算）
- `\s*`：省略号与括号之间的可选空白
- `[\(（]`：左括号（全角 `（` 或半角 `(`）
- `\d+`：1+ 位页码数字
- `[\)）]`：右括号（全角 `）` 或半角 `)`）

**反向校验**：扫描删除候选时，往下找到下一个 `段` 行（无论是不是 TOC 模式），中间若出现任何
`行类型 ∈ {定, 工, 料, 配, 机, 综}` 即认为该"段行"有子项，**保守保留** + `[WARN]`。

**stdout 报告**：
```
[OK] 输入: <input>
[OK] 输出: <input>
[OK] sheet: 定额条目 总行数 <n1>
[OK] TOC 模式命中段行: <n2>
[OK] 纯段行删除: <n3>
[OK] 有子项保留 + WARN: <n4>
```

**边界**：
- 空「定额条目」sheet：静默通过，统计均为 0
- xlsx 无「定额条目」sheet：stderr 报错并 exit 1
- 没有任何 TOC 模式命中：stdout 打 `[OK] TOC 模式命中段行: 0`，不删任何行

### §4.1 clean_empty_qty.py — 删空消耗量 + 删空分隔行

**触发条件**：上游 `extract_quota.py` 产出 `<stem>_待审核.xlsx` 且已人工核对。

**输入 / 输出**：`<stem>_待审核.xlsx` → `<stem>_待审核.xlsx`（原地覆盖；§3.4 Step 1）。仅修改「定额条目」sheet。

**编码**：xlsx 容器（ZIP），内部 XML UTF-8。

**处理逻辑**：

1. 完全空行（所有 cell `strip() == ""`）→ 删除
2. 长度 `>= 6` 且 `row[0] in {"工","料","配","机","综"}` 且 `row[5].strip() == ""` → 删除
3. 长度 `< 6` 的异常行 → 保留不动（视为异常行，不删除）

**stdout 报告**：
```
[OK] 输入: <input.xlsx>
[OK] 输出: <input.xlsx>（原地覆盖）
[OK] sheet: 定额条目 原行数 <n1>, 删空行 <n2>, 删空消耗量 <n3>, 剩余 <n4>
```

**边界**：
- 空文件：写空文件 + stdout 打印 `[WARN] 空文件`
- 文件不存在：`sys.exit(1)`

### §4.2 fill_work_content.py — 定行工作内容前向传播

**触发条件**：`<stem>_待审核.xlsx`（clean 之后）。

**输入 / 输出**：`<stem>_待审核.xlsx` → `<stem>_待审核.xlsx`（原地覆盖；§3.4 Step 3）。仅修改「定额条目」sheet。

**编码**：xlsx 容器（ZIP），内部 XML UTF-8。

**处理逻辑**（4 条规则）：

| # | 场景 | 处理 |
|---|---|---|
| 1 | `定` 行第 4 列非空 | 保留原值；记为"上一个有工作内容的定行" |
| 2 | `定` 行第 4 列为空（`strip() == ""`） | 从"上方最近一个有工作内容的定行"复制第 4 列（**前向传播**） |
| 3 | 跨段 / 工 / 料 / 配 / 机 / 综 行 | `last_wc` 状态**不重置**——同章节下的后续定额仍可继承 |
| 4 | 第一个 `定` 行就空 | 没有"上一个"，保持空（不报错） |

**附加行为**：
- 列数不足 4 列时自动 `row.append("")` 补足（防御性编程）
- 仅修改 `row[3]`（项目特征 / 工作内容字段），其他 9 列完全不动
- 仅处理 `row[0] == "定"` 的行；其他 6 种行类型完全不动

**stdout 报告**：
```
[OK] 输入: <input>
[OK] 输出: <input>
[OK] sheet: 定额条目
[OK] 总行数: <n>
[OK] 定行数: <n>
[OK] 工作内容填充行数: <n>
```

**边界**：
- 空「定额条目」sheet：静默通过，total_rows=0
- xlsx 无「定额条目」sheet：stderr 报错并 exit 1

### §4.3 space_split_materials.py — 材料名边界空格规整

**触发条件**：`<stem>_待审核.xlsx`（drop_toc + fill 之后）。

**输入 / 输出**：`<stem>_待审核.xlsx` → `<stem>_待审核.xlsx`（原地覆盖；§3.4 Step 4）。仅修改「定额条目」sheet。

**编码**：xlsx 容器（ZIP），内部 XML UTF-8。

**处理范围**：仅处理 `row[0] in ("料","配")` 行的第 3 列（材料名称字段），其他行类型与其他 9 列完全不动。

**5 条规则**：

| # | 规则 | 例 |
|---|---|---|
| 1 | 汉字 → 字母/数字：在字母数字前加空格 | `焊条E43` → `焊条 E43` |
| 2 | 字母 → 汉字：在字母后加空格 | `EVA高分子` → `EVA 高分子` |
| 3 | 数字 → 汉字：不加空格（且删除已有空格） | `2平垫` → `2平垫`；`E43系列` → `E43系列` |
| 4 | 末尾"综合" / "综合规格"前：仅当综合在末尾且前面有非空文字时加空格 | `螺栓综合` → `螺栓 综合`；`综合管廊` → `综合管廊`（不变） |
| 5 | 希腊字母（δ/Φ/α）按混合行为 | 汉字↔希腊加空格；希腊→数字删空格（不影响英文字母 `EVA 100`） |

**关键定义**：
- "数字" = 含至少一个 `0-9` 字符的串（如 `42.5` / `E43` / `M20×100`）
- "字母" = 纯 `A-Za-z` 不含数字（如 `EVA` / `PVC` / `SBS` / `PYII`）
- "希腊字母" = Unicode `U+0370–U+03FF` + `U+1F00–U+1FFF`（δ/Φ/α/β/γ/λ/μ/π/ω）
- "透明标点" = `()[]{}<>（）【】《》「」`（不触发 han↔alnum 边界判定）

**post-processing**：
- `(\d)\s+([一-鿿])` 删除"数字+空格+汉字"模式中的空格
- `([Ͱ-Ͽἀ-῿])\s+(\d)` 删除"希腊字母+空格+数字"中的空格（**仅希腊字母**）

**stdout 报告**：
```
[OK] 输入: <input>
[OK] 输出: <input>
[OK] sheet: 定额条目
[OK] 总行数: <n>
[OK] 处理行数（料/配）: <n>
[OK] 实际改动行数: <n>
```

**边界**：
- 空「定额条目」sheet：静默通过，total_rows=0
- xlsx 无「定额条目」sheet：stderr 报错并 exit 1

### §4.4 to_xlsx.py — xlsx「定额条目」sheet → 带分组 + 合并的 xlsx（多 sheet 保留）

**触发条件**：`<stem>_待审核.xlsx`（space 之后）。

**输入 / 输出**：`<stem>_待审核.xlsx` → `<stem>_待审核.xlsx`（原地覆盖；§3.4 Step 5）。仅修改「定额条目」sheet，册说明/各章 sheet 全程保留。

**编码**：xlsx 容器（ZIP），内部 XML UTF-8。

**CLI 签名**：
```bash
# 默认：原地覆盖（5 步流水线约定）
to_xlsx.py <input.xlsx> [output.xlsx]

# 不传第 2 个参数 → 原地覆盖输入文件
# 传第 2 个参数 → 写入指定输出文件，不修改输入文件
```

**参数**：

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `input_xlsx` | ✅ | — | 输入 xlsx（典型为 `<stem>_待审核.xlsx`） |
| `output_xlsx` | ❌ | `input_xlsx`（原地覆盖） | 输出 xlsx 路径 |

> **2026-07-27 起 `--stage 格式待审核 --src-pdf --process-root` 三个参数已废弃**——v2 单阶段工作流不需要目录流转，`to_xlsx.py` 只剩原地覆盖。历史调用方应改用 `extract_quota.py` 一行命令（autofinalize 5 步串联）。

**处理逻辑**：

1. **加载 xlsx**（多 sheet 保留）：用 `openpyxl.load_workbook(input_xlsx)` 整体读入
2. **写数据 + 段行加粗**（仅在「定额条目」sheet）：遍历每行写回 cell；`段` 行的 cell 应用 `Font(bold=True)` + `Alignment(left, center)`
3. **段行 C-D 列合并**（仅「定额条目」sheet）：`段` 行的第 3 列与第 4 列 merge_cells
4. **收集段与定额位置**：扫「定额条目」sheet 行，把 `段` 行入 `sections`、`定` 行入 `quotas`
5. **段深度计算**：`depth = id.count(".") + 1`（空 id 也算 1，对应"无编号的大段"）
6. **段范围**：每个段的 `end_row` = 下一个 `depth <= 当前 depth` 的段的 row - 1；最后一段 `end_row = total_rows`
7. **定额范围**：每个定额的 `end_row` = 下一定额的 row - 1（最后一定额到 total_rows），再 `min` 所属段的 `end_row` —— 保证定额不跨段
8. **建立分组区间**：段从 `row+1` 到 `end_row`（depth 为 outline_level）；定额从 `row+1` 到 `end_row`（outline_level=4）
9. **分组排序**：按 outline_level 升序 → 小范围（高 level）覆盖大范围（低 level）端点
10. **保存**：调用 `wb.save(output_path)`（整个 workbook，包含 册说明/各章 sheet）

**段分组示意**（仅作用于「定额条目」sheet）：

```
段,A,xxx,............        ← 标题行（level 1）
  段,A.1,xxx,..........      ← 标题行（level 2）
    段,A.1.1,xxx,......      ← 标题行（level 3）
      定,MA0001,...           ← 标题行（level 4）
        工,...                ← 内容行
        料,...                ← 内容行
        ...
      定,MA0002,...           ← 下一定额，闭合上一组
        ...
  段,B,...                   ← depth<=1 的段出现，闭合 A 段
```

**stdout 报告**：
```
[OK] 输入: <input.xlsx>
[OK] 输出: <output.xlsx>
[OK] sheet: 定额条目 总行数 <n>, 段数 <n>, 定额数 <n>
[OK] 分组区间数: <n>
[OK] 其它保留 sheet: ['册说明', 'A', 'B', ...]
```

**边界**：
- 空「定额条目」sheet：写空 xlsx + stdout 打印 `[WARN]`
- 文件不存在：`sys.exit(1)`
- xlsx 无「定额条目」sheet：stderr 报错并 exit 1

---

## §5 退出码（按现状如实记录）

| 步骤 | 脚本 | 退出码 0 | 退出码 1 |
|---|---|---|---|
| 1 | `clean_empty_qty.py` | 成功（隐式） | 参数错误 / 文件不存在 |
| 2 | `fill_work_content.py` | 成功（`return 0`） | 参数错误 / 文件不存在 / 编码无法识别 |
| 3 | `space_split_materials.py` | 成功（`return 0`） | 参数错误 / 文件不存在 / 编码无法识别 |
| 4 | `to_xlsx.py` | 成功（隐式） | 参数错误 / 文件不存在 |

**注**：
- 4 个脚本均**不区分**"参数错误"与"数据错误"——下游若需区分，建议基于 stdout `[OK]` vs `[ERROR]`/`[WARN]` 文本判断，不要依赖退出码。
- `clean_empty_qty.py` 与 `to_xlsx.py` 用 `sys.exit(1)`；`fill_work_content.py` / `space_split_materials.py` 用 `return 1` → `sys.exit()`。实现风格不同但语义等价。

---

## §6 边界与已知限制

1. **不做数据正确性校验**：OCR 抖动 / 行类型错配 / 字段污染会原样传递到 xlsx——这是 §1.4 硬前置契约的根本原因。
2. **clean/to_xlsx 空文件行为**：写空文件 + `[WARN]`。
3. **fill/space 空文件行为**：静默通过，统计均为 0。
4. **clean_empty_qty 无 gbk 兜底**：仅 `utf-8-sig`；fill/space/to_xlsx 都先 utf-8-sig 后 gbk 兜底。
5. **段行 C-D 合并只对 `段` 行**：非段行的 C、D 列保持独立。
6. **fill_work_content 只前向传播**：下方出现的非空定行不会回头填充之前已经遍历过的空定行；第一个定行就空保持空。
7. **space_split_materials 仅处理 `料`/`配` 行第 3 列**：其他行类型与其他 9 列完全不动。
8. **to_xlsx 单 sheet**：名字硬编码 `定额数据`；无多 sheet 扩展，无目录页。
9. **4 步退出码 0/1 两档**：不区分"参数错误"与"数据错误"。
10. **段深度固定为 `id.count(".") + 1`**：空 id（如裸 `段,xxx,...` 编号空）也计为 depth 1，与"无编号的大段"语义一致。
11. **定额分组兜底**：`min(下一定额 row-1, 所属段 end_row)` 保证定额不跨段；若段无下一定额，end_row = total_rows。

---

## §7 依赖

| 脚本 | 依赖 |
|---|---|
| `clean_empty_qty.py` | `csv` + `pathlib` + `sys`（标准库） |
| `fill_work_content.py` | `csv` + `pathlib` + `sys`（标准库） |
| `space_split_materials.py` | `csv` + `re` + `pathlib` + `sys`（标准库） |
| `to_xlsx.py` | `csv` + `openpyxl`（≥ 3.1.5） |

安装：
```bash
pip install openpyxl
```

DLSE 环境（`/d/miniconda3/envs/DLSE/python.exe`）已装 `openpyxl 3.1.5`。

---

## §8 关系

- **上游**：`quota-md-to-csv-v2/extract_quota.py`（多 sheet xlsx 来源；详见 `quota-md-to-csv-v2/SPEC.md`）
- **下游**：Excel 人工复核 / 造价软件导入
- **本目录其他文档**：
  - `SKILL.md`（Skill 入口描述，偏调用节奏）
  - `README.md`（上手指南 + 各脚本规则详解）
  - 4 个 `.py` 脚本（按本 SPEC 实现）
- **优先级**：本 SPEC > `SKILL.md` = `README.md` > 代码注释；冲突时改代码前先改本 SPEC。

---

## §9 当前样本与已交付产物

| 样本 | 状态 | 备注 |
|---|---|---|
| 《四川省建设工程工程量清单计价定额——房屋建筑更新改造工程》 | ✅ 已交付 `_待审核.xlsx` | 436 页 |
| 《四川省建设工程工程量清单计价定额——装配式建筑工程》 | ✅ 已交付 `_待审核.xlsx` | 3884 行 / 262 定额条目 / 0 异常表 |
| 《四川省建设工程工程量清单计价定额——园林绿化工程》 | ✅ 已交付 `_待审核.xlsx` | 7644 行（finalize 后） / 121 段 / 1002 定额 / 112 段删除（drop_toc_sections） / 317 改动（space_split_materials） |

> v2 起所有样本统一交付 `<stem>_待审核.xlsx`（单阶段产物）；旧的 `_final.xlsx` 与三阶段目录产物不再被新流程使用，可手工重跑 `extract_quota.py` 一次性出新版 `<stem>_待审核.xlsx`。

---

## §10 单阶段工作流（2026-07-27 起）

> **背景**：v1 的"数值/格式/最终"三阶段目录审核工作流已被合并。`extract_quota.py` 默认内嵌
> `autofinalize 5 步`（clean → drop_toc → fill → space_split → to_xlsx），原地覆盖写入
> `<stem>_待审核.xlsx`，**不再有目录流转**。
>
> **历史**：
> - `D:\工程造价学习\数值审核流程\`（流程根，含 `数值待审核/格式待审核/最终输出/OCR中间`）
> - `advance_stage.py`（阶段 3 通用包装脚本）
> - `to_xlsx.py --stage 格式待审核 --src-pdf --process-root`（阶段 2 包装）
> - `extract_quota.py --stage 数值待审核 --src-pdf`（阶段 1 包装）
>
> 以上均已**废弃**，保留只为兼容历史脚本引用；不再被新流程使用。

### §10.1 v2 数据流

```
[Step 0] mineru-pdf-parse → <PDF 同目录>/<stem>.md
   (≤100 页 parse_pdf.py；>100 页 parse_chunked.py)

[Step 1] extract_quota.py <stem>.md
   ├─ subprocess → extractors/<prov>/extract_quota.py (CSV 写到 tempfile,atexit 清)
   ├─ xlsx_writer → <stem>_待审核.xlsx (多 sheet:定额条目/册说明/章)
   └─ autofinalize 5 步(in-place 覆盖同一 xlsx):
         1) clean_empty_qty.py       删空消耗量 + 空分隔行
         2) drop_toc_sections.py     删"目录行误捕"(……(NN) / ....(NN))
         3) fill_work_content.py     定行第 4 列前向传播
         4) space_split_materials.py 料/配 行第 3 列汉字↔字母空格
         5) to_xlsx.py               4 级分组 + 段行 C-D 合并 + 段行加粗

★ 人工核对（在 Excel 看 <stem>_待审核.xlsx 的「定额条目」sheet）★

[Done] 流程结束
```

### §10.2 5 步 autofinalize 关键约定

- 5 步全部**原地覆盖同一个 xlsx**（"中转"已被取消）
- 仅 `定额条目` sheet 被改写；`册说明`/`章` sheet 全程保留
- autofinalize 在 `extract_quota.py` 内通过 `subprocess.run([sys.executable, 脚本, xlsx_path])` 串联
- 任一步骤失败 → finalize 整条停、`extract_quota.py` 返回非零退出码、xlsx 保留上一次成功状态

### §10.3 历史样本迁移（一次性，不阻塞新流程）

```bash
# 找到旧 md（要么 PDF 同目录，要么 D:\工程造价学习\数值审核流程\OCR中间\<stem>_OCR中间\）
MD="/d/工程造价学习/数值审核流程/OCR中间/<stem>_OCR中间/<stem>.md"
# 一行命令 = extract + autofinalize 5 步
PY=/d/miniconda3/envs/DLSE/python.exe
$PY "/d/工程造价学习/定额解析/.claude/skills/quota-md-to-csv-v2/extract_quota.py" "$MD"
# → <PDF 同目录 或 OCR目录>/<stem>_待审核.xlsx
```

无需手工 cp / mkdir / 改文件名。如果觉得旧目录碍事，可手工 `rm -rf /d/工程造价学习/数值审核流程/{数值待审核,格式待审核,最终输出}`。

### §10.4 当前状态

| 项 | 状态 |
|---|---|
| `extract_quota.py` autofinalize 5 步（默认） | ✅ 已实现 |
| `drop_toc_sections.py`（step 2） | ✅ 已实现（2026-07-27 新增） |
| `to_xlsx.py --stage 格式待审核` | ❌ 已废弃（脚本内已删除该选项） |
| `advance_stage.py` | ⚠️ 保留（标 deprecated），不调 |
| `extract_quota.py --stage 数值待审核` | ❌ 已废弃（脚本内已删除该选项） |
| 流程根 `D:\工程造价学习\数值审核流程\` | ⚠️ 历史目录，不阻塞新流程，可手工清 |

---

## §11 当前样本与已交付产物

| 样本 | 状态 | 备注 |
|---|---|---|
| 《四川省建设工程工程量清单计价定额——房屋建筑更新改造工程》 | ✅ 已交付 `_待审核.xlsx` | 436 页 |
| 《四川省建设工程工程量清单计价定额——装配式建筑工程》 | ✅ 已交付 `_待审核.xlsx` | 3884 行 / 262 定额条目 / 0 异常表 |
| 《四川省建设工程工程量清单计价定额——园林绿化工程》 | ✅ 已交付 `_待审核.xlsx` | 7644 行 / 121 段 / 1002 定额 / 112 段删除（drop_toc） / 317 改动（space_split） |

> 历史三阶段产物的 `_final.xlsx` 与 `格式待审核/<stem>_格式待审核/` 子目录残留不再被新流程使用；可手工重跑 `extract_quota.py` 一次性出新版 `<stem>_待审核.xlsx`。