# 算法原理与边界

本文档解释 `outline_grouper.py` 中分组逻辑的工作机制, 以及各种边界 case 的处理。

## 1. 输入契约（硬编码）

| 项 | 取值 | 出处 |
|---|---|---|
| 行类型标记 | `段` / `定` | 第一列 `row[0]` |
| 段 ID 列 | 第 2 列 `row[1]` | 字符串如 `"A.1.1"` |
| 段名称列 | 第 3 列 `row[2]` | 用于显示（**不修改**） |
| 项目特征列 | 第 4 列 `row[3]` | 段行为空（**不修改, 不合并**）|
| 目标 sheet 名 | `"定额条目"` | CLI `--sheet` 可改 |

> 若这些 schema 有任何变化, 本 skill 都需要先更新 `outline_grouper.py` 的常量区。
>
> 本 skill **只**负责 outline 分组; cell 值、字体、合并、对齐一律不碰。

## 2. 自适应层数算法

### 2.1 段 depth

```
get_section_depth(sec_id):
    if sec_id == "": return 1
    return sec_id.count(".") + 1
```

| sec_id | 点号数 | depth |
|---|---|---|
| `A` | 0 | 1 |
| `A.1` | 1 | 2 |
| `A.1.1` | 2 | 3 |
| `A.1.1.1` | 3 | 4 |
| `A.1.1.1.1` | 4 | 5 |
| 任意深度 | n | n+1 |

**关键**：段 ID 中的 `.` 数量直接决定 outline_level, 无需硬编码 4 层。

### 2.2 段 end_row

```
find_end_row(sections, idx, total):
    sec_depth = sections[idx].depth
    for j in range(idx+1, len(sections)):
        if sections[j].depth <= sec_depth:
            return sections[j].row - 1
    return total
```

**语义**：当前段从下一行开始, 到「下一个同级或更高级段出现之前」结束。
- 同级段（`A.1` → `A.2`）会闭合前一个
- 更高级段（`A.1` → `B`）也会闭合前一个（回到章级）
- 更深的段（`A.1` → `A.1.1`）**不**闭合（它属于 `A.1` 的子树）

### 2.3 定额 end_row 与父段

```
compute_quota_end_rows(quotas, sections, total):
    for each quota q:
        1. 默认 end = 下一个定额之前
        2. 找最近父段（向前扫描 sections）
        3. q.end_row = min(默认, 父段.end_row)
        4. q.parent_depth = 父段.depth
```

这保证：
- 定额不会跨段延伸到非所属段的范围
- 定额 group 的 `outline_level = parent_depth + 1`, **始终比父段深一层**, 无论父段是几层深。

### 2.4 分组构造

```
groups = []
for sec in sections:
    groups.append((sec.row+1, sec.end_row, sec.depth))   # 段 group

for q in quotas:
    groups.append((q.row+1, q.end_row, q.parent_depth+1)) # 定额 group

groups.sort(key=lambda x: (x.level, x.start))  # 低 level 先, 同 level 按 start 排
```

注意：
- `start = row + 1`：段/定额**本身不折叠**（标题行可见）
- `end = row`：段/定额范围内**所有内容**行折叠
- 排序：先画大轮廓（段），再画小轮廓（定额）。这是 Excel 嵌套语义的常见顺序。

## 3. 边界 case

### 3.1 中类下无小类

```
段 A       (depth 1)
段 A.1     (depth 2)   ← 中类
  定 H0001            ← 直接挂在 A.1 下, 没有 A.1.x 小类
  定 H0002
段 A.2     (depth 2)
```

✅ `find_end_row(A.1)` 在找到 `A.2`（同 depth 2）时闭合。
✅ `H0001` / `H0002` 的父段是 `A.1`（depth 2），所以 outline_level = 3。
✅ 无报错，定额 group 嵌套在 `A.1` 之下的 group 里。

### 3.2 五层段（自适应核心场景）

```
段 A
段 A.1
段 A.1.1
段 A.1.1.1
段 A.1.1.1.1       (depth 5)
  定 H0001         (parent_depth=5 → level=6)
```

✅ 全程不硬编码 4, 6 层 group 也能正确建立。

### 3.3 跨段定额

```
段 A
  定 H0001
段 B               ← 这里强制闭合 H0001 的 group
  定 H0002
```

- `compute_quota_end_rows(H0001)`: 父段 A 的 end_row = B.row - 1;
- 即便没有 `H0002` 把 `H0001` 截断, 段 B 也会强制截断。

### 3.4 定额后无任何子行

```
段 A
段 A.1
  定 H0001          (row N)
... 后面全是其它段
```

✅ `start = N+1`, `end = 父段.end_row`（若 start ≤ end 则创建 group）。

### 3.5 空 sheet

`read_sheet_rows` 返回空 list → `process_xlsx` 直接 `wb.save(target)` 并返回 `warning: "empty sheet"`。不抛异常。

### 3.6 上游已有 C-D 列合并（"不"处理的 case）

上游（比如 quota-csv-finalize 流水线前几步）可能已经合并过 C:D 列。
本 skill **不解除**已有合并, 也**不写回数据**, 所以 MergedCell 只读问题不会出现 —— `outline_grouper.py` 只调 `ws.row_dimensions.group()`, 不写 cell, 因此兼容任意上游状态。

### 3.7 缺失 sheet

❌ `RuntimeError("xlsx 中没有 '定额条目' sheet")` — 在 entry 显式抛错, CLI 返回 exit code 1。

## 4. 应用顺序

```
1. load_workbook(read_only=False)
2. ws = wb[sheet_name]
3. rows = read_sheet_rows(ws)         ← 仅读值缓存(不需要再写回)
4. groups = build_outline_groups(rows)
5. apply_outline_to_worksheet(ws, groups)   ← 仅设 outline
6. wb.save(target)
```

注意: 因为只设 outline 不写 cell, 步骤比原始 `finalize_last_step.py` 短很多。

## 5. 限制

- **没有硬编码的层数上限**：理论上 Excel 支持到 outline level ~7（旧版本）或更多（新版本 UI 折叠到 level 8）, 超出会被可视化折叠菜单隐藏分组按钮（但数据仍正确）。
- **段 ID 必须有层级语义**：若 OCR 把 `A.1.1.1` 错抄成 `A-1-1-1`（dash 而非 dot），`get_section_depth` 返回 1（兜底）。处理：上游需要保证 ID 用半角点号。
- **定额 ID 含 `.` 也按段语义算 depth**（`own_depth` 字段）。此 skill 不依赖, 但记录到 Quota 对象以便未来扩展。
- **多 sheet 不会触碰**「定额条目」以外的 sheet（册说明 / 章），原样保留。
- **不做格式修改**：cell 值 / 字体 / 合并 / 对齐一律不动。如需段行加粗 / C-D 列合并, 请在 quota-csv-finalize 流水线里做。

## 6. 单元测试建议

纯函数非常易于测试（不需要 openpyxl）：

```python
from outline_grouper import build_outline_groups

def test_3level_with_quotas():
    rows = [
        ["段", "A",   "章 A"],
        ["段", "A.1", "中类 1"],
        ["段", "A.1.1", "小类 1"],
        ["定", "H0001", "定额 1", "工作内容", "100 m³", "1.000"],
        ["工", "", "", "", "", "0.500"],
        ["料", "", "材料", "", "", "1.000"],
    ]
    groups = build_outline_groups(rows)
    # 段 A      → group (2, 6, 1)
    # 段 A.1    → group (3, 6, 2)
    # 段 A.1.1  → group (4, 6, 3)
    # 定 H0001  → group (5, 6, 4) (parent_depth=3 + 1)
    assert groups == [(2, 6, 1), (3, 6, 2), (4, 6, 3), (5, 6, 4)]
```

```python
def test_5level_adaptive():
    rows = [
        ["段", "A",                 "章"],
        ["段", "A.1",               "中类"],
        ["段", "A.1.1",             "小类"],
        ["段", "A.1.1.1",           "小小类"],
        ["段", "A.1.1.1.1",         "微类"],
        ["定", "H0001",             "定额", "", "100", ""],
    ]
    groups = build_outline_groups(rows)
    levels = sorted({lvl for _, _, lvl in groups})
    assert 6 in levels  # 定额 = parent_depth(5) + 1 = 6
```

```python
def test_中类无小类():
    rows = [
        ["段", "A",   "章"],
        ["段", "A.1", "中类"],
        ["定", "H0001", "定额", "", "100", ""],
        ["段", "A.2", "中类 2"],
    ]
    groups = build_outline_groups(rows)
    # A   → (2, 6, 1)
    # A.1 → (3, 4, 2)  ← A.1 区间到下一个同级 A.2 之前
    # H0001 → (4, 4, 3)  ← 定额挂在 A.1 之下
    # A.2 → (6, 6, 2)
    assert (3, 4, 2) in groups
    assert (4, 4, 3) in groups
```
