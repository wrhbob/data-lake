---
name: quota-md-to-csv-v2
description: 把 MinerU 解析出的定额 Markdown（含 HTML <table>）转为 10 列结构化 CSV。本 skill 自动判断省份（路径关键词 + MD 前段关键词），按省份分发到 extractors/sc/ 或 extractors/cq/ 子脚本处理。当用户拿到一份定额 md 想抽表成 CSV、或需要区分四川/重庆抽取规则、或要把多省份的定额 MD 喂给下游 finalize 时调用。
---

# quota-md-to-csv-v2 — 定额 Markdown → 10 列 CSV（多省份）

## 0. 这个 skill 干啥的

把 **含 HTML `<table>` 的定额 Markdown 文件**（典型来源是 `mineru-pdf-parse`）转成 **10 列结构化 CSV**：

- 输入：单文件 `.md`，非分页目录。文件内含 `<table>...</table>` 嵌入 HTML（来源不限，PaddleOCR-VL / MinerU 均可）。
- 输出：10 列 CSV。**每张定额表 → 一段以"定"行开头、后跟工/料/配/机/综明细行的定额条目**，各章节点缀上"段"行做分组骨架。

**v2 多省份**：同一入口 `extract_quota.py`，按省份 `sc` / `cq` 自动分发到 `extractors/<prov>/extract_quota.py` 子脚本。两省规则差异极大（详见 [SPEC.md §12](SPEC.md)），加省份走「**新建子目录 + cp + 改**」模式，不会让 skill 文件夹变乱。

---

## 1. 入口脚本

`extract_quota.py`（skill 根目录下，省份判断 + 路由的薄壳）

```bash
PY=/d/miniconda3/envs/DLSE/python.exe

# 自动判断省份（路径 / MD 前段）
$PY .claude/skills/quota-md-to-csv-v2/extract_quota.py <input.md> [output.csv]

# 手动指定
$PY .claude/skills/quota-md-to-csv-v2/extract_quota.py <input.md> --province sc
$PY .claude/skills/quota-md-to-csv-v2/extract_quota.py <input.md> --province cq
```

**依赖**：`beautifulsoup4` + `lxml`（DLSE 环境已装）。

完整输入输出规范见同目录 **[SPEC.md](SPEC.md)**（11 节四川既有规则 + §12 重庆 2018 差异，权威）。本文件只记「**什么时候调用 + 调用范式 + 何时不要调**」。

---

## 2. 何时调用

收到以下任一信号就触发：

- "把这份定额 md 转成 CSV"
- "抽一下定额表的工料机明细"
- "跑下 quota-md-to-csv / quota-md-to-csv-v2 / 四川定额 / 重庆定额"
- "skill 里有个 quota-…的脚本，跑一下"
- 上一步是 mineru-pdf-parse（或 PaddleOCR-VL），产物是 `<stem>.md`，下一步必然是本 skill
- 后续要做 `quota-csv-finalize`（4 步清洗 + xlsx），那么先走本 skill 拿到 raw CSV

**反向信号**——别调本 skill：

- 用户手里只有 PDF，没有 .md：先去 `mineru-pdf-parse`（不能 OCR 直接调用本 skill）
- 用户手里是 Excel / 已结构化数据：去 `cost-extract` 或直接 `pandas`
- 用户想要「**直接拿到 xlsx**」且**已人工核对过**：可以告知「如果你已经核对过，跳过本 skill 直接 `quota-csv-finalize`；如果还没有 raw CSV，先走本 skill。」

---

## 3. 调用前需要的信息

| 字段 | 必填 | 说明 |
|---|---|---|
| `<input.md>` | ✅ | 含 HTML `<table>` 的 Markdown 文件路径（绝对路径优先） |
| `[output.csv]` | ❌ | 缺省按 `<stem>.csv` 推到 MD 同目录 |
| `--province` | ❌ | `auto`（默认）/ `sc` / `cq`（自动判断失败时手动指定；PROVINCE_KEYWORDS 之外的 code 会被 argparse 拦下） |
| `--list-province` | ❌ | 列当前可用省份（声明 + 子脚本就位） |
| `--stage 数值待审核` | ❌ | 阶段 1 包装：自动落 `<process-root>/数值待审核/<stem>_数值待审核/<stem>_数值待审核.csv` |
| `--src-pdf <PDF>` | `--stage` 时必填 | 原始 PDF 绝对路径；`--stage` 时 cp 到新目录 |
| `--process-root <ROOT>` | ❌ | 流程根目录（默认 `D:\工程造价学习\数值审核流程`） |

**自动判断省份的逻辑**（A+B）：

1. **路径关键词**：MD 路径含 `四川` / `川建` → `sc`；含 `重庆` → `cq`
2. **MD 前 5KB**：路径没匹配则读 MD 文件前 5000 字符找省份关键词
3. **失败报错**：仍未匹配 → stderr 报错（**不静默回退**）+ 打印「复用 vs 新写」决策提示（见 §11）

退出码：

| 码 | 含义 | 触发条件 |
|---|---|---|
| 0 | 成功 | 解析完成 |
| 1 | MD 文件不存在 / 不是文件 | `md_path` 解析失败 |
| **2** | **自动判断省份失败** | 路径 + MD 前 5KB 都没命中关键词 → 报错 + 决策提示 |
| **3** | **省份已声明但子脚本未建** | PROVINCE_KEYWORDS 有该 code，但 `extractors/<prov>/extract_quota.py` 缺失 → 报错 + 决策提示 |
| 4+ | 由各省子脚本返回 | 解析异常（OCR / 列数不匹配等）|

> **决策提示共同点**：第 2 / 第 3 类错误都触发同一份「**复用 vs 新写**」提示文本，包含当前可用省份清单 + cp + 改的步骤 + 强制约束（不允许直接代理调用）。详见 §11。

---

## 4. 目录与调用关系

```
quota-md-to-csv-v2/
├── SKILL.md                     ← 本文件
├── README.md                    ← 上手指南
├── SPEC.md                      ← 完整规范（§1–§11 四川 + §12 重庆）
├── extract_quota.py             ← 入口薄壳（省份判断 + 路由）
└── extractors/                  ← 各省份独立子目录（隔离 + 不易乱）
    ├── __init__.py
    ├── sc/extract_quota.py      ← 四川实现（v3 主材 + 材料费自动行）
    └── cq/extract_quota.py      ← 重庆实现（[三对应决策](SPEC.md) + 人工识别 + 一般风险费）
```

输入 → 出口 → 下游：

```
<stem>.md (OCR/MinerU 产出)
       │
       ▼
[1] extract_quota.py  (省份判断：路径 → MD 前段 → --province)
       │
       ▼
[2] extractors/{sc|cq}/extract_quota.py   (各省份差异规则)
       │
       ▼
<stem>.csv  (+ 可选 <stem>_issues.md)
       │
       ▼  ★人工核对★
[3] quota-csv-finalize/clean_empty_qty.py → fill_work_content.py → space_split_materials.py → to_xlsx.py
       │
       ▼
<stem>_final.xlsx
```

---

## 5. 输出格式（10 列）

| 列序号 | 字段名 | 说明 |
|---|---|---|
| 1 | 类型 | `段` / `定` / `工` / `料` / `配` / `机` / `综` / `主材` |
| 2 | 项目编码 | 如 `MB0082` / `AA0001`；**仅"定"行填写**，其他行一律留空 |
| 3 | 名称 | 定额名称 / 费用名称 / 材料名 / 未计价材料名 |
| 4 | 项目特征 | 工作内容描述；**仅"定"行填写**，其他行一律留空 |
| 5 | 计量单位 | `m3` / `kg` / `t` / `元`（已 LaTeX 归一） |
| 6 | 消耗量 | 数量 |
| 7 | 基价/单价 | 单价或基价 |
| 8 | 验证 | 工/料/机/综的验证值；定行为子项之和 |
| 9-10 | 标准换算 + 来源 | 预留（恒空，给下游 finalize 用） |

完整字段语义见 [SPEC.md §3](SPEC.md)。

---

## 6. 四川 vs 重庆：何时行为不同

| 维度 | 四川（sc） | 重庆（cq） |
|---|---|---|
| **上方"人工费"** | ✅ emit `工 人工费` | 下方有"人工"分类 → **不 emit**；下方无且值 > 0 → emit |
| **上方"材料费"** | ✅ emit `料 材料费` | 下方有"材料"分类 → **不 emit**；下方无且值 > 0 → emit |
| **上方"机械费/施工机具使用费"** | ✅ emit `机 机械费` | 下方有"机械"分类 → **不 emit**；下方无且值 > 0 → emit |
| **"人工"分类行** | 不出现 | ✅ emit 为 `工`（如 `土石方综合工`） |
| **"一般风险费"** | 不出现 | ✅ emit 为 `综 一般风险费`（验证列空） |
| **`主材`（未计价）** | ✅ 已支持 | ❌ 当前未实现 |
| **综合基价/单价标题** | `综合基价` | `综合单价`（自动识别） |
| **材料头表列结构** | `名称 \| 单位 \| 单价 \| …` | 多 1 列 `编码 \| 名称 \| 单位 \| 单价 \| …` |

**三对应原则**（重庆核心）：每个定额有 3 组「费 ↔ 类」对应（人工费 ↔ 人工类 / 材料费 ↔ 材料类 / 施工机具使用费 ↔ 机械类）。下方有 `X 类` → 上方 `X 费` 不 emit（因下方合计已替代）。详见 [SPEC.md §12.1](SPEC.md)。

---

## 7. 调用范本

### 7.1 入口薄壳调用

```bash
PY=/d/miniconda3/envs/DLSE/python.exe
SKILL=".claude/skills/quota-md-to-csv-v2"

# ── 1) 自动判断：路径含"四川" → sc ──
$PY "$SKILL/extract_quota.py" \
  "《四川省建设工程工程量清单计价定额——装配式建筑工程》.md"
# → 输出同目录 .csv
# → stdout: [OK] 自动判断省份: sc (四川)
# →         [OK] 转发到 extract_quota.py

# ── 2) 自动判断：路径含"重庆" → cq ──
$PY "$SKILL/extract_quota.py" \
  "重庆市-房屋建筑与装饰工程计价定额-第一册-建筑工程-2018年版.md"
# → stdout: [OK] 自动判断省份: cq (重庆)

# ── 3) 手动指定（如路径无省份词） ──
$PY "$SKILL/extract_quota.py" \
  "/tmp/unknown.md" --province cq \
  -o /tmp/cq_out.csv
```

### 7.2 失败示例（预期表现）

```bash
# 路径不含省份词 + MD 前 5KB 也不含
$PY "$SKILL/extract_quota.py" "/tmp/no_province.md"
# stderr:
#   [ERROR] 无法自动判断省份：
#     - 路径 '/tmp/no_province.md' 未含 '四川'/'重庆'
#     - MD 前 5KB 也未匹配 '四川'/'重庆'
#     可用省份: sc(四川), cq(重庆)
#     请手动指定: --province sc | cq
# 退出码: 2
```

### 7.3 异常表处理

若某张定额表材料行列数不匹配（缺单位 / 缺单价 / 数量列数 ≠ 项目数），会跳过该表 + 在 CSV 插入全空行占位 + 写 `<stem>_issues.md`。

---

## 8. 人工核对（**强制**，不可跳过）

> **本 skill 的产物 `<stem>.csv` 必须经人工核对后再传入 `quota-csv-finalize`**。
> 自动抽取规则无法 100% 处理 OCR 抖动 / 版面差异 / 单位识别错误，**未核对直接走 finalize 会把错误固化到 xlsx**。

**核对要点**：

| 维度 | 典型问题 | 兜底位置（脚本内已尝试修复） |
|---|---|---|
| 段行 | 是否齐全（特别是小类如 `A.9.2`、编号带空格的 `L. 8`） | — |
| 定额行类型 | 材料标题下 = 料；机械标题下 = 机；cq 标题下的"人工"识别为"工" | `parse_material_row` 按 PDF 版面分类 |
| 利润行 | OCR 经常把"利润"识别成"利"/"润"/"和润" | 已放宽为单字"利"匹配 |
| 单位归一 | `$100m^2$` → `100m2`（不要丢前缀 `100`）；`$m^3$` → `m3` | `normalize_unit` |
| 工作内容多行 | 是否被完整捕获（部分定额的工作内容跨页被切到下一页） | — |
| 料行类型 | 消耗量带括号 `(32.250)` 是「配」 vs 不带括号是「料」 | `is_proportion` 标记 |
| 段行 C 列名称 | 与 xlsx 段行合并 C-D 直接相关，名称错就难看 | — |
| **cq 额外** | 上方"人工费/材料费/施工机具使用费"与下方"人工/材料/机械"分类是否按 [三对应原则](SPEC.md) 决策；AA0024 类 verify ≠ 基价是预期，不是 bug | 三对应 emit 逻辑（[SPEC §12.1](SPEC.md)） |

确认无误后再调用 `quota-csv-finalize/clean_empty_qty.py`（4 步流水线）。

---

## 9. 验收要点（Claude 调完后自查）

把脚本返回告诉用户：

- 输出 CSV 的**完整路径**（用户最容易忽略的）+ 是否写了 `_issues.md`
- 段行数 / 定额条目数 / 异常表数（用户能据此判断结构是否完整）
- 退出码（若非 0，展示 stderr 关键行）
- **判断了哪个省份 + 转发到哪个子脚本**（用户能验证省份对不对）

如果用户接着要做 `quota-csv-finalize`，**主动接力**并强调：「**产物需先人工核对再走 finalize**。」

---

## 10. 不要做的事

- ❌ 不要把本 skill 直接喂给 PDF（先走 `mineru-pdf-parse`）
- ❌ 不要假定省份（用 `--province` 显式指定或确认路径/MD 已含省份词）
- ❌ 不要对 cq 输出期待"verify = 基价"——三对应原则下 AA0024 等样本故意不等（已实测 = xlsx 原档同样不等）
- ❌ 不要跳过人工核对——OCR 误差一旦进 finalize 步骤会固化到 xlsx
- ❌ 不要把 `extract_quota_cq.py` 与 `extract_quota_sc.py` 合并——两省规则差异大，合并后变难维护；按省份分子目录 + 复制 + 改才是正确路径
- ❌ 不要把省份脚本放在 skill 根目录——会让 skill 文件夹随省份增长而变乱；统一放 `extractors/<prov>/`

---

## 11. 新增省份流程

> **触发时机**：运行 `extract_quota.py` 时遇到 §3 中退出码 2 或 3（识别不到 / 已声明但子脚本未建），脚本会**自动**打印本流程。调用者按下列步骤二选一执行。

### 11.1 决策：「复用」还是「新写」

| 决策 | 何时选 | 起点 | 工作量 |
|---|---|---|---|
| **[A] 复用现有省份**（推荐） | 新省份与某省份规则 ≥ 50% 相近 | `cp` 最相似省份 | 改 5-10 处差异点 |
| **[B] 新写提取脚本** | 新省份规则跟任何省份都差很多 | `cp` 任一省份当模板 | 改 80%+ 内容 |

**两种决策**都遵守同一约束：**必须新建 `extractors/<新 prov>/extract_quota.py`**——**不允许**直接调用已有省份的子脚本作为代理（即使规则完全相同）。

### 11.2 复用现有省份（决策 [A]）

1. 在 `extract_quota.py` 的 `PROVINCE_KEYWORDS` 加新省份关键词
2. `mkdir -p extractors/<新省 code>`（如 `extractors/gd`）
3. `cp extractors/sc/extract_quota.py extractors/gd/extract_quota.py`（或 `extractors/cq/`，按业务近似度选起点）
4. 按省份差异改 `extractors/gd/extract_quota.py`（识别别名 / 三对应原则 / 新行类型 等）
5. v2/SPEC.md 加 §X「<省>差异」
6. v2/README.md + 本 SKILL.md 的「四川 vs 重庆」表 → 扩展为「... vs <新省>」
7. 回归：跑 `--list-province` 看新省份已落地 + 用 `<新 code>` 跑样本

### 11.3 新写提取脚本（决策 [B]）

1. 在 `extract_quota.py` 的 `PROVINCE_KEYWORDS` 加新省份关键词
2. `mkdir -p extractors/<新省 code>`
3. `cp extractors/<任一>/extract_quota.py extractors/<新省 code>/extract_quota.py` 当模板
4. **`extractors/<新省 code>/extract_quota.py` 整体改写**（不复用现有识别逻辑，可保留通用工具如 `normalize_unit` / `clean_latex_name`）
5. v2/SPEC.md 加 §X「<省>差异」（无需参考其他省）
6. v2/README.md + 本 SKILL.md 同步
7. 回归：跑 `--list-province` + 用 `<新 code>` 跑样本

### 11.4 验证清单

新省份改完后必须跑：

```bash
# 1. 验证省份已落地
python extract_quota.py --list-province
#   应看到 <新省 code>

# 2. 用样本测试
python extract_quota.py <样本.md> --province <新省 code>

# 3. 重新跑四川装配式 md5 确认入口薄壳的修改没回归
python extract_quota.py <四川.md>
#   md5 应与 v1 skill 一致 (e01eac62f488bfce9b87d3e8c2db99c1)
```

---

## 12. 版本与现状

- **状态**：v2 多省份分支已落地
- **当前省份**：`sc`（四川）+ `cq`（重庆）
- **四川样本**：《四川省建设工程工程量清单计价定额——装配式建筑工程》✅ 3884 行 / 262 定额 / 0 异常表
- **重庆样本**：《重庆市-房屋建筑与装饰工程计价定额-第一册-建筑工程-2018年版》✅ 1634 定额 / 21714 行（v2 路由正确，三对应决策与 xlsx 对齐）
- **取代**：v1 单省份（`quota-md-to-csv/`）保持不变，方便对照回退

---

## 13. 三阶段目录审核工作流（2026-07 起）

> **背景**：防止"半核对"产物混进最终交付，把核对过程物化为**阶段目录**（数值待审核 / 格式待审核 / 最终输出），
> 每阶段独占子目录，前一阶段通过后整目录被清掉，只留当前阶段产物 + 源 PDF。
> **阶段 0（OCR 中间目录）** 2026-07-24 新增，把 MinerU 解析产物从 PDF 同目录隔离到流程根，详见 [CLAUDE.md §8.10](../CLAUDE.md)。
>
> **权威设计**：[`D:/工程造价学习/CLAUDE.md` §8](../CLAUDE.md)。
> 本节只描述本 skill 在三阶段中的角色（步骤 1：抽 → 数值待审核），其余阶段见 `quota-csv-finalize/SPEC.md §10`。

### 13.1 阶段 1：MD → 数值待审核（`extract_quota.py --stage`）

**输入约定**：本 skill 读取的 `<input.md>` 应来自**流程根 OCR 中间目录**（阶段 0 产物），而不是 PDF 同目录：

```bash
MD="D:/工程造价学习/数值审核流程/OCR中间/<stem>_OCR中间/<stem>.md"
```

跑批命令：

```bash
PY=/d/miniconda3/envs/DLSE/python.exe
$PY .claude/skills/quota-md-to-csv-v2/extract_quota.py \
    "D:/工程造价学习/数值审核流程/OCR中间/<stem>_OCR中间/<stem>.md" \
    --stage 数值待审核 --src-pdf <原始 PDF 绝对路径>
# → D:/工程造价学习/数值审核流程/数值待审核/<stem>_数值待审核/
#     ├── <PDF 原名>.pdf               ← cp 自原始位置
#     ├── <stem>_数值待审核.csv        ← extract 产物（后缀必带）
#     └── <stem>_数值待审核_issues.md   ← 异常表存档（可选）
```

**参数**：

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `--stage 数值待审核` | ❌ | — | 阶段 1 包装（落 `数值待审核/<stem>_数值待审核/`） |
| `--src-pdf <PDF>` | `--stage` 时必填 | — | 原始 PDF；cp 到新目录 |
| `--process-root <ROOT>` | ❌ | `D:\工程造价学习\数值审核流程` | 流程根 |

**当前未强制校验 OCR 中间目录存在**（仅文档规范）：用户传 PDF 同目录的 `<stem>.md` 也能跑。计划中：未来 `extract_quota.py --stage` 时校验 `OCR中间/<stem>_OCR中间/<stem>.md` 存在，否则报错。

### 13.2 人工数值核对 + 改文件名

- 在 Excel / VSCode 中打开 `<stem>_数值待审核.csv`，按 §8 核对要点逐项检查
- 核对完毕，**改文件名**：`<stem>_数值待审核.csv` → `<stem>.csv`（去"_数值待审核"后缀）
- 这步是**人工信号**——agent 不主动扫描目录判断阶段位置

### 13.3 当前已走三阶段工作流的样本

| 样本 | 状态 | 备注 |
|---|---|---|
| 《重庆市-房屋建筑与装饰工程计价定额-第二册-装饰工程-2018年版》 | ✅ 走完三阶段 | 214 页 / 12185 数值行 / 9439 格式行 / 最终 `.xlsx` 落到 `最终输出/<stem>/` |

四川样本仍以旧路径交付（PDF 同目录 `<stem>.csv` / `<stem>_final.xlsx`），两种交付形态并存。
