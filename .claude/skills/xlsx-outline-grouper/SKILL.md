---
name: xlsx-outline-grouper
description: 给 quota-csv-finalize 产出的「定额条目」sheet 套上自适应层数的 Excel 大纲分组（纯分组：xlsx 进 xlsx 出，不改任何格式）。当需要给「段 / 定」结构的定额表加可折叠大纲、或需要把现有 to_xlsx.py 的分组逻辑抽成可独立测试的纯函数 + openpyxl adapter 时使用。不修改原 quota_csv_finalize 文件，只重新生成结果 xlsx。
---

# xlsx-outline-grouper

把 `quota-csv-finalize` 流水线产出的 `<stem>_待审核.xlsx` 直接喂进来，输出**带自适应层数分组**的成品 xlsx。其它 sheet（册说明 / 章）原样保留。

**纯分组工具**：不改 cell 值、不改字体、不合并、不居中、不解上游合并——只设置 Excel 大纲分组（`row_dimensions.group()`）。如需段行加粗 / C-D 列合并，请在 quota-csv-finalize 主流水线做。

## 何时用

- 想给任何按「段 / 定」组织的定额表加 Excel 可折叠分组大纲。
- 想验证分组算法的正确性（纯函数可单独 import 跑单测）。
- 原 `finalize_last_step.py` / `to_xlsx.py` 不想动、只想跑这步功能。

## 何时**不要**用

- 输入不是 xlsx（必须先经过 quota-md-to-csv / quota-csv-finalize 流水线产出 xlsx）。
- 想改「段 / 定」以外的行类型标记 — 本 skill **硬编码**了这两个 marker, 是 quota-csv-finalize 的专属工具。
- 想给段行加粗 / 合并 C-D 列 / 段名居中 — 这些已不在本 skill 职责范围内。
- 想修改原 `quota_csv_finalize/` 或 `quota-csv-finalize/` 下的任何源文件 — 本 skill 不依赖它们, 也不动它们。

## 核心性质：自适应层数

| 项 | 计算 | 示例 |
|---|---|---|
| 段行 outline_level | `sec_id 中点号数 + 1` | `A` → 1, `A.1` → 2, `A.1.1` → 3, `A.1.1.1` → 4（任意深度） |
| 定额行 outline_level | **父段 depth + 1** | 父段是 5 层 → 定额 = 6 层 |
| 中类无小类 | 中类 group 直接挂定额, 无报错 | `A.1` 下无 `A.1.x` 时定额直接挂在 `A.1` 之下 |
| 跨段定额 | 定额 group 遇到段行强制闭合 | 不会出现跨段归属 |

详见 [references/algorithm.md](references/algorithm.md)。

## 调用

### CLI

```bash
PY=/d/miniconda3/envs/file-asset/python.exe

# 不传第 2 个参数 → 原地覆盖输入文件
$PY .claude/skills/xlsx-outline-grouper/outline_grouper.py "<stem>_待审核.xlsx"

# 写到新路径
$PY .claude/skills/xlsx-outline-grouper/outline_grouper.py \
    "<stem>_待审核.xlsx" \
    "<stem>_grouper.xlsx"

# 自定义 sheet 名(默认 "定额条目")
$PY .claude/skills/xlsx-outline-grouper/outline_grouper.py input.xlsx --sheet 定额条目
```

### Python API

```python
from pathlib import Path
from outline_grouper import process_xlsx, build_outline_groups

# 高层入口: xlsx → xlsx(只设 outline, 不改格式)
info = process_xlsx(
    input_path=Path("input.xlsx"),
    output_path=Path("output.xlsx"),
    sheet_name="定额条目",
)
# info => {input_path, output_path, sheet, total_rows, sections, quotas, groups, other_sheets}

# 纯函数(无 openpyxl 依赖, 便于单测): 仅算分组
from openpyxl import load_workbook
wb = load_workbook("input.xlsx", read_only=False)
ws = wb["定额条目"]
rows = [[str(v) if v else "" for v in r] for r in ws.iter_rows(values_only=True)]
groups = build_outline_groups(rows)
# groups => [(start_row, end_row, outline_level), ...]
```

### 纯函数清单（无 openpyxl 依赖）

| 函数 | 作用 |
|---|---|
| `get_section_depth(sec_id)` | `"A"` → 1, `"A.1.1"` → 3, 自适应 |
| `scan_sections(rows)` | 提取所有段行 → `list[Section]` |
| `scan_quotas(rows)` | 提取所有定额行 → `list[Quota]` |
| `find_end_row(sections, idx, total)` | 找同级或更高级段的边界 |
| `compute_section_end_rows(...)` | 批量填充段 end_row |
| `compute_quota_end_rows(...)` | 批量填充定额 end_row + 父段 |
| `build_outline_groups(rows)` | 主纯函数, 返回 `[(start, end, level)]` |

### openpyxl adapter 清单

| 函数 | 作用 |
|---|---|
| `read_sheet_rows(ws)` | values_only 读所有数据行 |
| `apply_outline_to_worksheet(ws, groups)` | 仅设置 `summaryBelow` + 各级 group |
| `process_xlsx(input, output, sheet)` | 主入口 |

## 输入 / 输出约束

| 项 | 取值 |
|---|---|
| 输入 | 单个 xlsx 文件（必须含 sheet「定额条目」，默认名） |
| 输出 | 同结构 xlsx + 新增分组；其它 sheet 原样保留 |
| 行类型标记（硬编码）| `段` = row[0], `定` = row[0] |
| 段 ID 规则 | 第 2 列（row[1]）含 `.` 表深度（`A.1.1` 等） |
| 是否修改单元格 | **否** —— 不改值、不改字体、不合并、不解合并 |
| 标题折叠方向 | `summaryBelow = False`（标题在上, 内容在下） |

## 输出日志

```
[OK] 输入: <path>
[OK] 输出: <path>
[OK] sheet: 定额条目 总行数 N, 段数 N, 定额数 N
[OK] 分组区间数: N
[OK] 其它保留 sheet: [...]
```

## 退出码

| Code | 含义 |
|---|---|
| 0 | 成功 |
| 1 | FileNotFoundError / RuntimeError（缺 sheet 等）|

## 与原 finalize_last_step.py / to_xlsx.py 的关系

| 对比项 | 原件 | 本 skill |
|---|---|---|
| 自适应层数 | 横杠版 `to_xlsx.py` 已是; 下划线版 `finalize_last_step.py` 仍是硬编码 4 | ✅ 自适应（统一了两版） |
| 纯函数 / adapter 分离 | 混在一起（不便单测） | ✅ 分两个清单, 纯函数无 openpyxl 依赖 |
| 入口 | 两份实现且行为不一致 | ✅ 单一入口 `process_xlsx` |
| 段行加粗 / C-D 列合并 | 做 | ❌ 不做（本 skill 纯分组, 那是 finalize 流水线的活）|
| 文件改动 | — | **零** — 本 skill 不 import、不修改任何原文件 |

## 依赖

- `openpyxl`（已在 file-asset / DLSE conda 环境中可用）
- 其它一律标准库（`pathlib` / `dataclasses` / `argparse` / `sys`）

---

## 参考

- 算法细节与边界 case：[`references/algorithm.md`](references/algorithm.md)
- 原实现对照：`quota/parser/external/quota_csv_finalize/finalize_last_step.py` 与 `quota/parser/external/quota-csv-finalize/to_xlsx.py`
- 上游约定：`.claude/skills/quota-pdf-extractor/SKILL.md`（解析侧）/ `quota/parser/external/quota_csv_finalize/README.md`（格式侧）
