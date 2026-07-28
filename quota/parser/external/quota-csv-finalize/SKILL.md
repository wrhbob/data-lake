---
name: quota-csv-finalize
description: 定额 CSV 4 步清洗流水线。输入 quota-md-to-csv/extract_quota.py 抽出的 csv，依次跑 clean_empty_qty（删空消耗量+空分隔行）→ fill_work_content（定行工作内容前向传播）→ space_split_materials（材料名边界空格规整，5 条规则含希腊字母）→ to_xlsx（4 级分组 + 段行 C-D 合并 → 带分组的 xlsx）。当用户提到"清洗 csv"、"规整材料名"、"补空格"、"填工作内容"、"转 xlsx"或显式说"按 quota-csv-finalize 跑"时调用此 skill。
---

# quota-csv-finalize — 定额 CSV 清洗与 xlsx 化

## 0. 这个 skill 干啥的

在 `quota-md-to-csv/extract_quota.py` 抽出 csv 并**经人工核对**后，做最终清洗与格式规整，输出带 Excel 4 级分组 + 段行 C-D 合并的 `.xlsx` 文件。

**前置条件**：
- 输入 csv 必须来自 `quota-md-to-csv/extract_quota.py`
- 必须经过人工核对（核对要点见 `quota-md-to-csv/README.md` "人工核对是强制步骤"section）
- 本 skill **不会二次校验数据正确性**——OCR 抖动 / 行类型错配 / 字段污染等错误一旦进入就固化到 xlsx

---

## 1. 4 步流水线

| 步骤 | 脚本 | 处理对象 | 核心功能 | 输出 |
|---|---|---|---|---|
| **1** | `clean_empty_qty.py` | `工/料/配/机/综` 行 + 全空行 | 删空消耗量行 + 删空分隔行 | `<input>_final.csv` |
| **2** | `fill_work_content.py` | `定` 行第 4 列 | 工作内容为空时，从**上方最近一个有内容的定行**前向传播 | 覆盖 `<input>_final.csv` |
| **3** | `space_split_materials.py` | `料`/`配` 行第 3 列 | 5 条规则规整材料名边界空格（汉字/字母/数字/希腊字母/末尾"综合"） | 覆盖 `<input>_final.csv` |
| **4** | `to_xlsx.py` | 整个 csv | 4 级分组 + 段行 C-D 合并 + 段行加粗 → xlsx | `<input>_final.xlsx` |

> 4 步缺一不可，跳过任一步会导致 xlsx 缺失对应处理（空消耗量残留 / 空工作内容残留 / 材料名粘连 / 无 Excel 分组）。

---

## 2. 调用范式

所有脚本统一支持 1 或 2 个位置参数：

```bash
python <script>.py <input.csv>            # 原地覆盖输入
python <script>.py <input.csv> <output>   # 写入新文件，不修改输入
```

### 完整流水线示例

```bash
# Step 1: 删空消耗量 → _final.csv
python clean_empty_qty.py "<input>.csv"

# Step 2: 填工作内容（覆盖 _final.csv）
python fill_work_content.py "<input>_final.csv"

# Step 3: 材料名加空格（覆盖 _final.csv）
python space_split_materials.py "<input>_final.csv"

# Step 4: → xlsx
python to_xlsx.py "<input>_final.csv"
# 产物：<input>_final.xlsx
```

### 完整命名示例

```bash
python clean_empty_qty.py "《四川省建设工程工程量清单计价定额——房屋建筑更新改造工程》.csv"
python fill_work_content.py "《四川省建设工程工程量清单计价定额——房屋建筑更新改造工程》_final.csv"
python space_split_materials.py "《四川省建设工程工程量清单计价定额——房屋建筑更新改造工程》_final.csv"
python to_xlsx.py "《四川省建设工程工程量清单计价定额——房屋建筑更新改造工程》_final.csv"
# 终产物：《...》_final.xlsx
```

---

## 3. 各脚本关键规则

### 3.1 `clean_empty_qty.py`
- 删除 `工/料/配/机/综` 行但消耗量（第 6 列）为空
- 删除完全空白行（所有 cell `strip() == ""`）
- 仅处理 `len(row) >= 6` 的行（少于 6 列视为异常行）
- 编码：`utf-8-sig`

### 3.2 `fill_work_content.py`
- 仅处理 `定` 行第 4 列（项目特征/工作内容）
- **前向传播**（仅从上向下）：维护 `last_wc`，遇到空工作内容则用 `last_wc` 填充
- **不反向传播**：下方出现的非空定行不会回头填充之前已经遍历过的空定行
- 跨段 / 工 / 料 / 配 / 机 / 综 行：`last_wc` 状态**不重置**——同章节下后续定额仍可继承
- 第一个定行就空 → 保持空
- 列数不足 4 列时自动 `row.append("")` 补足
- 编码：先 `utf-8-sig`，兜底 `gbk`

### 3.3 `space_split_materials.py`
仅处理 `料` / `配` 行的第 3 列，其他行类型和其他 9 列完全不动。

5 条规则：

| # | 规则 | 例 |
|---|---|---|
| 1 | 汉字 → 字母/数字：加空格 | `焊条E43` → `焊条 E43` |
| 2 | 字母 → 汉字：加空格 | `EVA高分子` → `EVA 高分子` |
| 3 | 数字 → 汉字：不加空格（且删已有空格） | `2平垫` → `2平垫`；`E43系列` → `E43系列` |
| 4 | 末尾"综合"前：仅当综合在末尾且前面有非空文字时加空格 | `螺栓综合` → `螺栓 综合`；`综合管廊` → `综合管廊`（不变） |
| 5 | 希腊字母（δ/Φ/α 等）混合行为 | 汉字→希腊加空格；希腊→汉字加空格；希腊→数字删空格（不影响英文字母 `EVA 100`） |

**关键定义**：
- "数字" = 含至少一个 `0-9` 字符的串（42.5 / E43 / M20×100 / 2 / 1）
- "字母" = 纯 `A-Za-z` 不含数字（EVA / PVC / SBS / PYII）
- "希腊字母" = Unicode U+0370–U+03FF + U+1F00–U+1FFF（δ/Φ/α/β/γ/λ/μ/π/ω）

**透明标点**：`()[]{}<>（）【】《》「」` 作为 han/alnum 边界的一部分传递，不触发 han↔alnum 边界判定（`塑性聚烯烃(TPO)防水卷材P型` 中 `(TPO)` 紧贴，不加空格）。

### 3.4 `to_xlsx.py`
- 段行单元格格式：`Font(bold=True)` + `Alignment(horizontal="left", vertical="center")`
- 段行 C-D 列：`merge_cells(start_row=r, start_column=3, end_row=r, end_column=4)`
- 4 级分组（`outline_level`）：
  - 1 = 大类（A / B / C，无小数点）
  - 2 = 中类（A.1 / B.2）
  - 3 = 小类（A.1.1 / B.2.1）
  - 4 = 定额（`定` 行及其子项）
- `sheet_properties.outlinePr.summaryBelow = False`（标题在上、内容在下折叠）
- 默认输出 `<input>.xlsx`（与 csv 同名）
- **`--stage 格式待审核`**：自动落到 `D:\工程造价学习\数值审核流程\格式待审核\<stem>_格式待审核\`，产 xlsx + rmtree 数值目录 + cp PDF（2026-07-24 修复关键顺序 bug：先产 xlsx 再 rmtree）
- **`--src-pdf`**： `--stage` 时必填（cp 原始 PDF 到新目录）
- **`--process-root`**： 默认 `D:\工程造价学习\数值审核流程`

---

## 4. 中间产物管理

### 默认行为（清理中间文件）

4 步完成后**应删除中间 csv**，只保留 `_final.xlsx`：

```bash
rm "<input>.csv" "<input>_final.csv"
# 仅保留 _final.xlsx 作为交付物
```

理由：CSV 是过渡形态，最终消费方（造价核对 / 复用）几乎都用 Excel；保留中间 CSV 容易让人误以为是终产物。

### 备选行为（保留中间文件）

如需复核每步清洗过程（典型如 1000+ 定额本，几十条材料名改动），**不删中间文件**——直接跑完 4 步即可，磁盘上会留下：

- `<input>.csv`（原始 extract 产物）
- `<input>_final.csv`（clean + fill + space 三步合并产物）
- `<input>_final.xlsx`（终产物）

---

## 5. 与上游 skill 的关系

```
quota-md-to-csv (extract_quota.py)
    │  ← 输入：<stem>.md（含 <table> HTML，OCR/MinerU 产物）
    ▼
  人工核对（强制）
    │
    ▼
quota-csv-finalize（本 skill，4 步流水线）
    │
    ▼
  最终交付：<stem>_final.xlsx
```

---

## 6. 依赖

| 脚本 | 依赖 |
|---|---|
| `clean_empty_qty.py` | `csv` + `pathlib` + `sys`（标准库） |
| `fill_work_content.py` | `csv` + `pathlib` + `sys`（标准库） |
| `space_split_materials.py` | `csv` + `re` + `pathlib` + `sys`（标准库） |
| `to_xlsx.py` | `csv` + `openpyxl` |

安装：
```bash
pip install openpyxl
```

---

## 7. 详细文档

完整规则、示例对照、边界说明见本目录 [README.md](README.md)。

---

## 8. 三阶段目录审核工作流（2026-07 起）

> **背景**：防止"半核对"产物混进最终交付，把核对过程物化为**阶段目录**（数值待审核 / 格式待审核 / 最终输出），
> 每阶段独占子目录，前一阶段通过后整目录被清掉，只留当前阶段产物 + 源 PDF。
> **阶段 0（OCR 中间目录）** 2026-07-24 新增，把 MinerU 解析产物从 PDF 同目录隔离到流程根，详见 [CLAUDE.md §8.10](../CLAUDE.md)。
>
> **权威设计**：[`D:/工程造价学习/CLAUDE.md` §8](../CLAUDE.md)。
> 本节只描述本 skill 在三阶段中的角色（步骤 4-5），其余阶段见 `quota-md-to-csv-v2/SPEC.md` 和 `mineru-pdf-parse/SKILL.md`。

### 8.1 阶段 2：数值 → 格式（`to_xlsx.py --stage`）

```bash
PY=/d/miniconda3/envs/DLSE/python.exe
$PY .claude/skills/quota-csv-finalize/clean_empty_qty.py \
    "D:/工程造价学习/数值审核流程/数值待审核/<stem>_数值待审核/<stem>.csv"
$PY .claude/skills/quota-csv-finalize/fill_work_content.py \
    "D:/工程造价学习/数值审核流程/数值待审核/<stem>_数值待审核/<stem>_final.csv"
$PY .claude/skills/quota-csv-finalize/space_split_materials.py \
    "D:/工程造价学习/数值审核流程/数值待审核/<stem>_数值待审核/<stem>_final.csv"
$PY .claude/skills/quota-csv-finalize/to_xlsx.py \
    "D:/工程造价学习/数值审核流程/数值待审核/<stem>_数值待审核/<stem>_final.csv" \
    --stage 格式待审核 --src-pdf <原始 PDF 绝对路径>
# → D:/工程造价学习/数值审核流程/格式待审核/<stem>_格式待审核/<stem>_格式待审核.xlsx
# → 数值目录被 rmtree
```

**关键顺序（2026-07-24 bug fix 历史）**：

```
1. mkdir 目标目录（格式待审核/<stem>_格式待审核/）
2. process_csv_to_xlsx(input, output)   ← 先产 xlsx
3. shutil.rmtree(数值目录)                ← 再删数值目录
4. shutil.copy2(src_pdf, 目标目录)        ← 最后 cp PDF
```

旧实现按 `mkdir → rmtree → cp → 产 xlsx` 顺序 → input csv 在被读之前被删 → FileNotFoundError 且 csv 物理丢失（rmtree 不进回收站）。

### 8.2 阶段 3：格式 → 最终（`advance_stage.py`）

```bash
$PY .claude/skills/quota-csv-finalize/advance_stage.py \
    --from-dir "D:/工程造价学习/数值审核流程/格式待审核/<stem>_格式待审核" \
    --to-dir   "D:/工程造价学习/数值审核流程/最终输出/<stem>" \
    --src-pdf  <原始 PDF 绝对路径>
# → D:/工程造价学习/数值审核流程/最终输出/<stem>/<stem>.xlsx + <PDF 原名>.pdf
# → 格式目录被 rmtree
```

**`advance_stage.py`** 只做目录/文件 mv cp 改名（**不调 finalize**），可参数化复用任意阶段包装。

### 8.3 当前已交付

| 样本 | 阶段 | 落点 |
|---|---|---|
| 《四川省建设工程工程量清单计价定额——装配式建筑工程》 | 旧路径 | PDF 同目录 `<stem>_final.xlsx` |
| 《四川省建设工程工程量清单计价定额——房屋建筑更新改造工程》 | 旧路径 | PDF 同目录 `<stem>_final.xlsx` |
| 《四川省建设工程工程量清单计价定额——园林绿化工程》 | 旧路径 | PDF 同目录 `<stem>_final.xlsx` |
| 《重庆市-房屋建筑与装饰工程计价定额-第二册-装饰工程-2018年版》 | **新工作流** | `最终输出/<stem>/<stem>.xlsx`（无后缀） |