# quota-md-to-csv-v2

> **当前状态**：v2 扩展。在 v1 既有行为基础上**新增省份分发**（四川 / 重庆），支持同一份定额表按省份走不同抽取规则。
> **省份差异核心**：详见 [SPEC.md §12](SPEC.md)「重庆 2018 差异」（三对应决策 + 人工识别 + 一般风险费）。
> **规则参考**：四川仍按 [SPEC.md §1–§11](SPEC.md)；重庆在 SPEC.md §12 之上叠加 §12 给出的差异。

将定额 Markdown（含 HTML 表格，来自 OCR/MinerU）转换为 **10 列结构化 CSV**。同一份 Markdown 文件，先按省份自动识别，再转发到对应省份的提取脚本。

---

## 脚本清单

```
quota-md-to-csv-v2/
├── extract_quota.py            ← 入口薄壳（省份判断 + 路由）
├── extractors/                 ← 各省份子目录（隔离 + 不易乱）
│   ├── __init__.py
│   ├── sc/extract_quota.py     ← 四川抽取（v3 完整逻辑：`主材` + `料 材料费`）
│   └── cq/extract_quota.py     ← 重庆抽取（[SPEC §12](SPEC.md) 三对应决策 + 人工识别 + 一般风险费）
├── README.md
├── SPEC.md
└── SKILL.md
```

| 路径 | 作用 | 是否必须 |
|---|---|---|
| `extract_quota.py` | **入口薄壳**：接收 MD、自动判断省份、转发到 `extractors/<prov>/extract_quota.py` | ✅ 唯一入口 |
| `extractors/sc/extract_quota.py` | 四川抽取实现（v3 完整逻辑：`主材` + `料 材料费`） | ✅ |
| `extractors/cq/extract_quota.py` | 重庆抽取实现（[SPEC §12](SPEC.md) 三对应决策 + 人工识别 + 一般风险费） | ✅ |

> **省份分发**：入口脚本 `extract_quota.py` 走 `subprocess.run` 调用 `extractors/<prov>/extract_quota.py`，子脚本保持独立 `main()`，互不污染运行时命名空间。
> **新增省份**：在 `extractors/` 下新建 `<省 code>/` 子目录 + `extract_quota.py` 即可（详见末尾「新增省份流程」）。

---

## 自动判断省份

按以下顺序判断（首个命中即返回）：

1. **路径关键词**：MD 文件路径含 `四川` / `川建` → `sc`；含 `重庆` → `cq`
2. **MD 前 5KB**：若路径未匹配，再读 MD 文件前 5000 字符，找 `四川` / `重庆` 关键词
3. **失败报错**：仍未匹配 → **不静默回退**，直接退出 + 打印「复用 vs 新写」决策提示（见下文）

手动覆盖：
```bash
python extract_quota.py <md_path> --province cq
```

调试入口：
```bash
python extract_quota.py --list-province
# 当前可用省份（2 个）:
#   cq (重庆)
#     关键词: '重庆'
#     脚本:   extractors/cq/extract_quota.py
#   sc (四川)
#     关键词: '四川', '川建'
#     脚本:   extractors/sc/extract_quota.py
```

### 错误处理与新省份决策

错误发生时**统一**打印「复用 vs 新写」决策提示，不会静默回退，**也不会**让 Claude/调用方自己猜：

| 错误场景 | 退出码 |
|---|---|
| MD 文件不存在 / 不是文件 | 1 |
| 自动识别失败（路径 + MD 前 5KB 都没命中关键词） | 2 |
| 自动识别命中关键词但 `extractors/<prov>/extract_quota.py` 子脚本没建（典型：刚加 PROVINCE_KEYWORDS 但忘 cp） | 3 |
| `--province` 手动指定某省份但子脚本没建 | 3 |
| `--province` 传了 PROVINCE_KEYWORDS 里没有的 code | argparse 直接拦（连脚本都没进） |
| 省份路由后由各省 `extract_quota.py` 返回的错误 | 4+ |

**三类错误的共同决策提示**（任意一类触发时打印）：

```
[ERROR] <具体错误描述>

  当前可用省份（声明 + 子脚本就位）:
    - sc (四川): extractors/sc/extract_quota.py
    - cq (重庆): extractors/cq/extract_quota.py

  这是一个新省份吗?你有两条路:

  [A] 复用现有省份实现 → 复制 + 修改
      1) 选最相似的现有省份复制:
         cp extractors/<近>/extract_quota.py extractors/<新 code>/extract_quota.py
      2) 在 PROVINCE_KEYWORDS / PROVINCE_NAMES 加新 entry
      3) 按新省份差异改 extractors/<新 code>/extract_quota.py
      4) v2/SPEC.md 加 §X「<省>差异」; v2/README.md 同步「差异表」

  [B] 新写提取脚本 → 复制当模板, 但做更彻底改写
      步骤同 [A], 但 extractors/<新 code>/extract_quota.py 是从头改起的

  ⚠ 强制约束:
    - 不允许直接调用已有省份的子脚本作为代理（即使规则相同）
    - 每个新省份必须有自己独立的 extractors/<prov>/extract_quota.py

  详细流程见 SKILL.md §11「新增省份流程」

  快速跳过决策（如果当前 MD 本来就属于已有省份）:
    --province cq | sc
```

### 新增省份流程

**两个动作只能二选一，强制「复制 + 改」**：

1. 在 `extract_quota.py` 的 `PROVINCE_KEYWORDS` / `PROVINCE_NAMES` 添加新省份（如 `gd` 广东 → 关键词 `广东` / `粤`）
2. `mkdir -p extractors/gd && cp extractors/sc/extract_quota.py extractors/gd/extract_quota.py`（或 `extractors/cq/`，按业务近似度选起点）
3. 按新省份特有规则改 `extractors/gd/extract_quota.py`
4. v2/SPEC.md §12 之后加 §13「<省>差异」
5. v2/README.md + v2/SKILL.md 的「四川 vs 重庆」差异表扩展为「<省> vs 其他」
6. 跑 `--list-province` 验证新省份已落地；用 `<新 code>` 跑样本回归

---

## 调用顺序

本 Skill 为**单脚本直通**（含省份分发），无多步流水线：

```
输入 .md  ──►  extract_quota.py (判断省份)  ──►  extractors/<sc|cq>/extract_quota.py  ──►  输出 .csv (+ 可选 _issues.md)
```

> ⚠️ **人工核对是强制步骤，不可跳过**：
> 本脚本的产物 `<input>.csv` **必须经人工核对后再传入 `quota-csv-finalize`**。
> 自动抽取规则无法 100% 处理 OCR 抖动 / 版面差异 / 单位识别错误，**未核对直接走 finalize 会把错误固化到 xlsx**。
> 典型核对要点：
> - 段行是否齐全（特别是小类如 `A.9.2`、编号带空格的 `L. 8`）
> - 关键定额行类型是否正确（材料标题下的料 vs 机械标题下的机；cq 标题下的"人工"识别为"工"）
> - 利润行是否被识别（OCR 经常把"利润"识别成"利"/"润"/"和润"，脚本已放宽为单字"利"匹配）
> - 单位归一是否正确（`$100m^2$` → `100m2`，不要丢掉前缀 `100`）
> - 工作内容多行是否被完整捕获
> - **cq 额外**：上方的"人工费/材料费/施工机具使用费"与下方的"人工/材料/机械"分类是否按 [SPEC §12 三对应原则](SPEC.md) 决策
>
> 核对通过后再调用 `quota-csv-finalize/clean_empty_qty.py`。

---

## 环境依赖

- Python ≥ 3.10
- `beautifulsoup4` + `lxml`

安装：
```bash
pip install beautifulsoup4 lxml
```

---

## CLI 用法

```bash
python extract_quota.py <input.md> [output.csv] [--province sc|cq]
```

- `<input.md>`：含 HTML `<table>` 的 Markdown 文件（OCR/MinerU 产物）
- `[output.csv]`：可选，默认与输入同名（改后缀为 `.csv`）
- `[--province]`：可选，默认 `auto`（自动判断）

### 示例

```bash
# 四川省装配式（路径含"四川"，自动判断 → sc）
python extract_quota.py "《四川省建设工程工程量清单计价定额——装配式建筑工程》.md"

# 重庆市 2018（路径含"重庆"，自动判断 → cq）
python extract_quota.py "重庆市-房屋建筑与装饰工程计价定额-第一册-建筑工程-2018年版.md"

# 无法判断时手动指定
python extract_quota.py "/tmp/unknown.md" --province cq
# → [ERROR] 无法自动判断省份；手动指定: --province sc | cq
```

若解析过程中发现异常表（列数不匹配等），会额外生成：
```
<input>_issues.md
```

---

## 输出格式（10 列）

| 列序号 | 字段名 | 说明 |
|---|---|---|
| 1 | 类型 | `段`/`定`/`工`/`料`/`机`/`综`/`主材`（v3 新增 `主材`；cq 无 `主材`） |
| 2 | 项目编码 | 如 `MB0082`；**仅"定"行填写**，其他行一律留空 |
| 3 | 名称 | 定额名称 / 费用名称 / 材料名称 / 未计价材料名 |
| 4 | 项目特征 | 工作内容描述；**仅"定"行填写**，其他行一律留空 |
| 5 | 计量单位 | 如 `m3`、`kg`、`t`（已归一化）；主材行 = 材料原单位；料-材料费行 = `元` |
| 6 | 消耗量 | 数量 |
| 7 | 基价/单价 | 单价或基价；主材行**留空**；料-材料费行固定 `1.00` |
| 8 | 验证 | 工/料/机/综的验证值；定行为子项之和；主材行**留空**（不参与求和） |
| 9 | 标准换算 | 预留 |
| 10 | 标准换算来源 | 预留 |

### 行类型规则（通用）

行类型**完全由 PDF 版面决定**，不再用关键词硬编码：

| 表格分类标题 | 消耗量带括号 | → 行类型 | 计入定行 verify？ |
|---|---|---|---|
| `材料` | 否 | **料** | ✅ |
| `材料` | 是（`(32.250)`） | **配** | ❌ |
| `机械` | 否 | **机** | ✅ |
| `机械` | 是 | **机** | ❌ |
| **`人工`**（cq 特有） | 否 | **工** | ✅ |

**四川 vs 重庆关键差异**：

| 维度 | 四川 | 重庆 |
|---|---|---|
| 上方"人工费"行 | ✅ emit（`工 人工费`） | 下方有"人工"分类时 **❌ 不 emit**；无"人工"分类时 ✅ emit（如其值 > 0） |
| 上方"材料费"行 | ✅ emit（`料 材料费`） | 下方有"材料"分类时 **❌ 不 emit**；无"材料"分类时 ✅ emit（如其值 > 0） |
| 上方"机械费" / "施工机具使用费"行 | ✅ emit（`机 机械费`） | 下方有"机械"分类时 **❌ 不 emit**；无"机械"分类时 ✅ emit（如其值 > 0） |
| "人工"分类行（`工 土石方综合工` 等） | 不出现 | ✅ emit 为"工" |
| "一般风险费" | 不出现 | ✅ emit 为"综"（v2.2 起计入 verify 验证列，**与企业管理费、利润一致**） |
| `主材`（未计价材料） | ✅ v3 新增 | ❌ cq 当前未实现 |
| 综合基价/综合单价标题 | "综合基价" | "综合单价"（自动识别） |
| **材料头表结构** | `名称 \| 单位 \| 单价 \| …` | `编码 \| 名称 \| 单位 \| 单价 \| …`（多 1 列编码） |
| **分类行结构** | `col0=名字 col1=单位 col2=单价` | `col0=分类(材料/机械/人工/未计价) col1=编码 col2=名字 col3=单位 col4=单价` |
| **parse_material_row 跳过 col1** | — | ✅ v2.1 修复：nxt[0] 是 6-12 位数字（编码）→ 自动用 nxt[1] 当 name；else 兜底 nxt[0]，兼容老格式 |

详见 [SPEC.md §12](SPEC.md)。

---

## 特殊处理

| 场景 | 处理 |
|---|---|
| 利润行识别 | OCR 经常把"利润"识别成"利"/"润"/"和润"，匹配放宽为单字"利"，统一映射到"利润"字段 |
| 其他材料费 | 单价为空时默认填 `1.000` |
| 数值括号 | `(9.511)` → `9.511`（同时标记 `is_proportion=True`，影响 verify 计算） |
| LaTeX 符号 | `$\phi$` → `φ`，`$\leqslant$` → `≤` 等 |
| 单位归一 | `$m^3$` / `$\mathrm{m}^{3}$` → `m3`；`$100m^2$` / `$10m^2$` → `100m2`/`10m2`（保留前缀） |
| **未计价材料**（v3 新增，仅 sc） | `未计价` 作为 col0 的**材料分类标签**（与"材料"/"机械"同级）。`parse_material_row` 识别后 `category="未计价"`，下一格 cell 取为真实材料名（如"钢管"）。`extract_table` 输出顺序：**所有未计价主材放在一起** → **`料 材料费` 自动行**（名称固定"材料费"、单位=元、单价=1.00、消耗量=其中-材料费(元)、验证=其中-材料费(元)，**位置在机字段上方**）→ 其他料行（机分类下的料如柴油等）。**N 个未计价材料 → N 个主材 + 1 个材料费**（非 N:1 配对）。多未计价材料场景下 col0 通过 rowspan 跨多行时，由 `parse_material_row(grid, grid_row_idx)` 从 grid 取 col0 正确识别。详见 [SPEC.md §3.3.7 / §3.3.8 / §4.3 / §4.4](SPEC.md) |
| **隐含材料费**（v3 扩展，仅 sc） | 表里 `material_header` 之下**没有任何 `料` / `配` / `未计价` 行**（即只有 `机械` 行，或完全无材料区），但 "其中-材料费(元)" 值 > 0 → 仍 emit 1 个 `料 材料费` 行（消耗量 = 其中-材料费(元)），让定行验证列加和 = 综合基价。典型样本：DE0159（表里完全无材料区）、DE0341 / DE0493 / DE0542（表里只有机械行）。详见 [SPEC.md §3.3.8 Case B / §10.9 / 附录 C.3](SPEC.md) |
| **cq 三对应原则** | 详见 [SPEC.md §12](SPEC.md)。每个定额对应 3 组"费↔类"：人工费↔人工类、材料费↔材料类、施工机具使用费↔机械类。下方有"X 类"分类 → 上方"X 费"自动行 **不 emit**（因下方 X 类合计已替代之）。无 X 类分类 → 上方"X 费"按 sc 规则 emit。所有决策还要求该"费"值 > 0；为 0 或 `-` → 不 emit。 |
| **cq 一般风险费** | 上方"其中-一般风险费"行 → emit 为 `综 一般风险费`，单位 `元`，消耗量 `100.00`，基价即风险费值，**验证列空**（不参与 verify 加和）。 |

---

## 实现要点（供维护参考）

1. **省份分发**：`extract_quota.py` → `subprocess.run([sys.executable, extractors/<prov>/extract_quota.py, ...])`
2. **HTML 解析**：`BeautifulSoup` + 手动展开 `colspan`/`rowspan`，生成统一 grid
3. **项目识别**：正则 `[A-Z]{1,2}\d{4}`，支持单表多项目（如 `MB0082` + `MB0083`）
4. **材料行分类**：根据表格最左列前缀（`材料`/`机械`，cq 加 `人工`）判定输出类型为 `料` / `机` / `工`
5. **cq 三对应**：emit 阶段对每张表的成本行（cost_rows）+ 物料行分类做笛卡尔检查，决定哪些"费"行 emit / 跳过
6. **验证列计算**：
   - 工/机：直接输出金额
   - 料：消耗量 × 单价（单价为空则输出消耗量）
   - 综：基价
   - 定：所有子项验证值之和（cq 版：综的"一般风险费" 行 verify 留空，不参与加和）
7. **异常检测**：材料行列数不匹配时跳过该表，记录到 `_issues.md`

---

## 与 SPEC.md 的关系

- `SPEC.md`：完整规范
  - §1–§11：四川（v1 既有规则）
  - **§12：重庆 2018 差异（v2 新增）**
- `README.md`（本文件）：快速上手与脚本说明
- `extract_quota.py`：入口薄壳（省份分发）
- `extractors/sc/extract_quota.py`：四川实现
- `extractors/cq/extract_quota.py`：重庆实现

---

## 版本

- **适配样本**：
  - 《四川省建设工程工程量清单计价定额——装配式建筑工程》✅ 稳定运行（sc）
  - 《重庆市-房屋建筑与装饰工程计价定额-第一册-建筑工程-2018年版》✅ 实测 cq 决策正确（cq）
- 状态：v2 多省份分支已落地
