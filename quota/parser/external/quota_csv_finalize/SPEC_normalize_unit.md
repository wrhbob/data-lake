# SPEC: `normalize_unit.py` — 单位文本规范化(autofinalize 第 6 步)

> 状态: **草案 v0.1**,待评审。
> 范围:仅限定额数据湖流水线 autofinalize 阶段的"计量单位"列规范化,**不**涉及其他列 / 其他 sheet / 其他阶段。

---

## 0. 背景与目的

OCR / Markdown 阶段把定额 PDF 中的单位渲染成多种形态(HTML 标签、LaTeX、Unicode 上标、`m^2` 文本标记),前 5 步 finalize(clean_empty_qty / drop_toc_sections / fill_work_content / space_split_materials / finalize_last_step)**完全不动第 5 列(计量单位)**,导致人工审核前的 candidate.xlsx 里仍有 `m²`、`m³`、`<sup>2</sup>`、`$\mathbf{m}^2$` 等噪音。

新增第 6 步 `normalize_unit.py`,把这些字面形态按用户指定的 8 条规则严格替换,使审核 / 导入 DB 时单位字段统一为 `m2` / `m3` / `2` / `3` 形式。

---

## 1. 行为约束(强制)

### 1.1 规则集(**仅这 8 条**,严禁添加)

| # | LHS(严格字面)         | RHS   |
|---|----------------------|-------|
| 1 | `<sup>2</sup>`       | `2`   |
| 2 | `<sup>3</sup>`       | `3`   |
| 3 | `$\mathbf{m}^2$`     | `m2`  |
| 4 | `$\mathbf{m}^3$`     | `m3`  |
| 5 | `m³`                 | `m3`  |
| 6 | `m²`                 | `m2`  |
| 7 | `m^3`                | `m3`  |
| 8 | `m^2`                | `m2`  |

**禁止**新增任何其他规则(用户原话:"严禁自己瞎添加")。
**禁止**用正则、改大小写、做语义归一(如 `m²` → `平方米`)。

### 1.2 替换语义

- 用 `str.replace(old, new, count=-1)` 做字面替换;**不**用 `re`。
- 只替换严格匹配上的子串,**不**触动其他字符;`10m³` → `10m3`、`5m²` → `5m2`,前后缀原样保留(用户原话:"不要替换字符串其他部分,如 10m³ 替换后应该是 10m3")。
- 8 条规则的 LHS 互不重叠(分属 4 种格式:HTML `<sup>`、LaTeX `$\mathbf{m}...$`、Unicode `m²/m³`、纯文本 `m^2/m^3`),所以**规则执行顺序不会影响结果**;代码里就按 1→8 用户给定顺序跑,与规范一致。

### 1.3 作用域:仅「定额条目」sheet 第 5 列

- 只读 / 只写 `wb["定额条目"]`。
- 只改 col_index == 4(1-based 第 5 列 = 「计量单位」);其他 8 列**完全不动**。
- 其他 sheet(`册说明` / `章` / 任何新加 sheet)**完全不动**。

### 1.4 原地写入

`output_path = None` 时,把修改写回 `input_path` 同一文件(与同 pipeline 中其他 5 步的约定一致)。caller 给 `output_path` 则写到那。

---

## 2. 输入 / 输出

- 输入: `<stem>_待审核.xlsx`(前 5 步 finalize 跑完后的产物;Sheet1=`定额条目`,9 列定宽,col 5 = 计量单位)。
- 输出: 同一 xlsx(默认原地覆盖)。

---

## 3. 函数签名(与同 pipeline 其他 5 步对齐)

```python
def process_xlsx(input_path: Path, output_path: Path | None = None) -> dict:
    """对 input xlsx 的「定额条目」sheet 第 5 列跑 8 条字面替换。

    Returns:
        {
            "input_path": str,
            "output_path": str,
            "sheet": "定额条目",
            "total_rows": int,
            "cells_changed": int,                 # 累计改了 N 个 cell
            "rules_applied": [                   # 按 1→8 规则顺序
                {"from": "<sup>2</sup>", "to": "2", "count": N},
                ...
            ],
            "other_sheets": [str, ...],          # 保留未动的 sheet 名
        }
    """
```

辅助:
- `_read_sheet_rows(ws) -> list[list[str]]`:沿用 `finalize_last_step.py` 的实现(`str(v) if v is not None else ""`)。
- 不需要 `_write_sheet_rows`:只改一个列,且只改 value 不动格式,直接用 `ws.cell(row=r, column=5).value = new_val` 即可。

---

## 4. CLI

```bash
python normalize_unit.py <input.xlsx> [output.xlsx]
```

打印:
- 命中规则 / 命中次数(`[OK] rule <sup>2</sup> -> 2  hit 12`)
- 总改了 N 个 cell
- 输出 xlsx 路径

---

## 5. Pipeline 衔接(**必须改 2 处文件**)

### 5.1 wiring 1 — `quota/parser/external/quota_md_to_csv_v2/extract_quota.py`

文件内有 8 处需要同步(都是文案,1 处是列表):

| 位置 | 当前 | 改为 |
|---|---|---|
| **L239-245** `FINALIZE_SCRIPTS` 列表(实质控制) | `[clean_empty_qty.py, drop_toc_sections.py, fill_work_content.py, space_split_materials.py, to_xlsx.py]` | 在 `space_split_materials.py` 与 `to_xlsx.py` 之间插入 `normalize_unit.py`,**变成 6 项**;`to_xlsx.py` 仍由 L264/L456 的 if 改名为 `finalize_last_step.py`,**不要**碰这两处的 if 逻辑 |
| L13 模块 docstring | "自动跑 finalize 5 步流水线" | "自动跑 finalize 6 步流水线" |
| L327 `process_md_file` docstring | "含可选 autofinalize 5 步" | "含可选 autofinalize 6 步" |
| L443 注释 | "autofinalize 5 步" | "autofinalize 6 步" |
| L454 循环头 | `for fname in FINALIZE_SCRIPTS:` | **不改**(循环自动跑新长度) |
| L514 argparse `description` | "…跑 5 步 finalize" | "…跑 6 步 finalize" |
| L536 `--no-finalize` help | "不跑 finalize 5 步" | "不跑 finalize 6 步" |
| L699 注释 | `# ── finalize 5 步流水线 ──` | `# ── finalize 6 步流水线 ──` |
| L708 print | `[PIPELINE] 跑 finalize 5 步` | `[PIPELINE] 跑 finalize 6 步` |

### 5.2 wiring 2 — `quota/parser/quota_parser/config.py`

| 位置 | 当前 | 改为 |
|---|---|---|
| **L96-102** `FINALIZE_STEPS`(文档 / 静态参考源) | `[clean_empty_qty.py, drop_toc_sections.py, fill_work_content.py, space_split_materials.py, finalize_last_step.py]` | 在 `space_split_materials.py` 与 `finalize_last_step.py` 之间插入 `normalize_unit.py`,变成 6 项 |
| L95 注释 | "autofinalize 5 步顺序" | "autofinalize 6 步顺序" |

> **⚠ 单一真源的现实**:`FINALIZE_STEPS`(config.py)与 `FINALIZE_SCRIPTS`(extract_quota.py)内容**当前不一致**(一个含 `to_xlsx.py`、一个含 `finalize_last_step.py`)。**真正控制运行时顺序的是 extract_quota.py L239-245**——pipeline.py 把 `run_finalize=True` 透传给 `extract_quota.process_md_file()`,后者用自己那份列表。
>
> 改的时候**两处都同步**,但万一未来某次只改了 config.py 没改 extract_quota.py,运行时不会爆,只是新增步骤不生效——属于"静默 bug"风险,需要后续整理为单一真源(超出本 SPEC 范围)。

### 5.3 不需要改的文件

| 文件 | 原因 |
|---|---|
| `quota/parser/quota_parser/pipeline.py` | `run_quota_pipeline()` 只调 `extract_mod.process_md_file(...run_finalize=True)`,不直接迭代 `FINALIZE_STEPS`。 |
| `quota/parser/quota_parser_worker.py` | worker 只调 `pipeline.run_quota_pipeline()`,自动透传到最新 6 步。 |
| `file_asset_service/app/quota_parser/service.py` | web 端 stage B 调的是 `pipeline.finalize_reviewed_xlsx()`(reviewed → final),**不**重跑 autofinalize(详见 SPEC.md §5.2)。 |

---

## 6. 网站服务部署

### 6.1 web 代码本身**完全不需要改**

新增 `normalize_unit.py` 对 web 是透明的:

- **阶段 A(触发解析)**: 用户点"解析" → web 写 `parse_status='parsing'` → worker 拉 job → `pipeline.run_quota_pipeline()` → `extract_quota.process_md_file(run_finalize=True)` 自动跑 6 步 → candidate.xlsx 上 MinIO → web 状态 `parsed`。**整条链路 web 无需改动**。
- **阶段 B(上传 reviewed)**: 用户传 reviewed.xlsx → web 调 `pipeline.finalize_reviewed_xlsx()` → final.xlsx → MinIO。**`finalize_reviewed_xlsx` 不重跑 autofinalize**(它只做格式 / 校验和,与本规范无关)。

### 6.2 worker 必须重启

worker 是常驻 daemon(`run_quota_parser_worker.sh`)。它启动时把 `extract_quota.py` 与它依赖的 `FINALIZE_SCRIPTS` 列表一起加载进内存;改了 `FINALIZE_SCRIPTS` / 加了新脚本,**必须重启 worker** 才会生效。

**sweeper / web 不需要重启**(它们不直接 import `extract_quota.FINALIZE_SCRIPTS`)。

重启命令(沿用 CLAUDE.md §5 SOP):
```bash
ROOT="/d/工程造价学习/data_lake0714/data_lake0714"
cd "$ROOT"

bash file_asset_service/scripts/run_quota_parser_worker.sh stop
bash file_asset_service/scripts/run_quota_parser_worker.sh start
# 启动日志会跑 schema 自检;看 logs/worker.log.<TS> 第一行是否 "schema 自检 ✅"
```

### 6.3 清 `__pycache__`

`normalize_unit.py` 落在 `quota/parser/external/quota_csv_finalize/` 下。开发期若曾被 import 缓存过,清掉避免旧 `.pyc` 复活:
```bash
cd "/d/工程造价学习/data_lake0714/data_lake0714"
find quota/parser -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
```
参考 `~/.claude/projects/.../memory/file-asset-conda-env.md` §"彻底重启流程"。

---

## 7. QA / metrics

### 7.1 `process_xlsx` 返回 dict

必含字段(`cells_changed` / `rules_applied` 见 §3):

```json
{
  "input_path": "/tmp/xxx_待审核.xlsx",
  "output_path": "/tmp/xxx_待审核.xlsx",
  "sheet": "定额条目",
  "total_rows": 1523,
  "cells_changed": 287,
  "rules_applied": [
    {"from": "<sup>2</sup>", "to": "2", "count": 8},
    {"from": "<sup>3</sup>", "to": "3", "count": 0},
    {"from": "$\\mathbf{m}^2$", "to": "m2", "count": 12},
    {"from": "$\\mathbf{m}^3$", "to": "m3", "count": 4},
    {"from": "m³", "to": "m3", "count": 156},
    {"from": "m²", "to": "m2", "count": 107},
    {"from": "m^3", "to": "m3", "count": 0},
    {"from": "m^2", "to": "m2", "count": 0}
  ],
  "other_sheets": ["册说明", "章"]
}
```

### 7.2 上报到 Archive.parse_metrics

worker 当前把 `pipeline.run_quota_pipeline()` 的返回 dict 透传到 `Archive.parse_metrics`(沿用现有 metrics key 风格,如 `n_rows_quota` / `sections_count`)。新增子字段:

```
parse_metrics["unit_normalize_changed_cells"]: int = process_xlsx 返回的 cells_changed
parse_metrics["unit_normalize_rules_applied"]: list = process_xlsx 返回的 rules_applied
```

`parse_metrics` 是 JSONB 列(SQLAlchemy dict),直接 set 即可。

### 7.3 前端展示(可选,非阻塞)

audit / review 页如果有"候选产物预览",可以在 metadata 区显示 `unit_normalize_changed_cells` 与命中最高的 3 条规则。**不**阻塞第 6 步上线。

---

## 8. 测试 / 回归

### 8.1 单元测试(脚本自带 `tests/test_normalize_unit.py`)

构造小 xlsx:Sheet1=`定额条目`,9 行 × 9 列,每行 col 5 填不同形状的"脏单位":
- 行 1: `m²`     → 期望 `m2`
- 行 2: `m³`     → 期望 `m3`
- 行 3: `m^2`    → 期望 `m2`
- 行 4: `m^3`    → 期望 `m3`
- 行 5: `$\mathbf{m}^2$` → 期望 `m2`
- 行 6: `$\mathbf{m}^3$` → 期望 `m3`
- 行 7: `<sup>2</sup>`  → 期望 `2`
- 行 8: `<sup>3</sup>`  → 期望 `3`
- 行 9: `10×5m`  → 期望 `10×5m`(不动)

断言:
- 9 行全部正确
- `cells_changed` == 8
- `rules_applied` 长度 == 8,各项 count 加和 == 8

### 8.2 端到端(`quota/parser/tests/run_pipeline_e2e.py`)

复用现有 fixture,挑一个 `sc` 或 `cq` 的样本 MD,跑 `pipeline.run_quota_pipeline()`(默认 `run_finalize=True`)。检查 candidate.xlsx:

```python
from openpyxl import load_workbook
wb = load_workbook(candidate_xlsx_path)
ws = wb["定额条目"]
col5 = [ws.cell(row=r, column=5).value for r in range(1, ws.max_row + 1)]
assert not any(v and any(needle in str(v) for needle in
                         ["m²", "m³", "<sup>", "$\\mathbf"]) for v in col5)
```

外加 `parse_metrics["unit_normalize_changed_cells"]` 在真实样本上 > 0。

### 8.3 反向回归

前 5 步(clean_empty_qty / drop_toc_sections / fill_work_content / space_split_materials / finalize_last_step)**不**碰 col 5;normalize_unit 在第 5 步之后跑,所以也不会反向影响它们。回归测试沿用现有 `tests/run_pipeline_e2e.py` 即可。

---

## 9. 风险 / 副作用

| 风险 | 缓解 |
|---|---|
| 源数据中本就有合法 `m3` / `m2` 单位(非 OCR 噪音),被误以为命中 | 不会——规则只匹配 `m^3` / `m^2`(有 `^`),**不**单独匹配 `m3` / `m2` |
| 多个规则连续作用同一 cell 时,第二次替换因第一次结果误触发 | 不会——8 条规则 LHS 互不重叠,第一次替换后第二个规则 LHS 不存在 |
| 替换后出现 `10m3` 等"奇怪"字符串 | 这是用户期望,不是 bug |
| `process_xlsx` 抛错,worker 整个 job 标 `failed_permanent` | 同其他 5 步行为;若想降级为 warnings,需改 `_run_finalize_step_inproc` 加 try/except——但**不建议**(静默失败会让单位噪音逃过审核) |
| worker 重启遗漏 → 新规则不生效 | 通过 e2e 测试(§8.2)覆盖:重启后跑真实样本,断言 `parse_metrics["unit_normalize_changed_cells"]` > 0 |

---

## 10. 部署 checklist

- [ ] 1) 新建 `quota/parser/external/quota_csv_finalize/normalize_unit.py`(8 条规则常量 + `process_xlsx` + CLI)
- [ ] 2) 改 `quota/parser/external/quota_md_to_csv_v2/extract_quota.py` L239-245(`FINALIZE_SCRIPTS` 插入 `normalize_unit.py`),并同步 L13 / L327 / L443 / L514 / L536 / L699 / L708 文案(`5` → `6`)
- [ ] 3) 改 `quota/parser/quota_parser/config.py` L96-102(`FINALIZE_STEPS` 插入 `normalize_unit.py`),并同步 L95 注释
- [ ] 4) 清 `quota/parser/` 下所有 `__pycache__`(`find quota/parser -name __pycache__ -exec rm -rf {} +`)
- [ ] 5) 重启 worker(`bash file_asset_service/scripts/run_quota_parser_worker.sh stop && start`),看 `logs/worker.log.<TS>` schema 自检通过
- [ ] 6) 跑一个真实 PDF 样本:候选产物 candidate.xlsx 第 5 列 grep `m²|m³|<sup>|$\mathbf` 应**无**命中;`parse_metrics["unit_normalize_changed_cells"] > 0`
- [ ] 7) (可选,非阻塞)audit 页加 unit 字段命中统计展示

---

## 附:实测验证摘要(写草案前已核对)

- **`FINALIZE_SCRIPTS` 真正生效位置**: `extract_quota.py` L454-467,经 `_run_finalize_step_inproc`(L489-506)用 `importlib.util.spec_from_file_location` 动态加载,**每次 finalize 都从磁盘读 `process_xlsx`**,不依赖 worker 启动时缓存。
- **config.py 的 `FINALIZE_STEPS` 现状**:目前 pipeline.py **不**消费它,只是 import(L23);但作为文档 / 静态参考仍然存在,需要同步改。
- **worker / sweeper / web 的代码引用关系**:
  - worker (`quota_parser_worker.py`) 只 import `pipeline.run_quota_pipeline` → 透传。
  - sweeper 不碰解析。
  - web `service.py` stage B 调 `pipeline.finalize_reviewed_xlsx`,**不**重跑 autofinalize。
- **前 5 步对 col 5 的影响范围**: 经核对 `clean_empty_qty.py` / `drop_toc_sections.py` / `fill_work_content.py` / `space_split_materials.py` / `finalize_last_step.py` 全部不动 col 5(只动 col 1-4 / 段行加粗 / 分组),所以 normalize_unit 放在 step 5 之后、step 6(即 finalize_last_step 之前)位置安全。