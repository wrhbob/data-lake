# 形态 A：把每张定额表 → PDF 物理页号写入 CSV 末列

## Context

**问题**：当前抽表产物（`candidate.xlsx` 8 列 + `clean_fields.py` 6 列）只有"定额条目"的物料数据，**没有任何 PDF 页码信息**。用户想定位"这张定额表在 PDF 第几物理页"时，必须手工翻 PDF 全文找。

**目标**：每张定额表产一份独立 CSV，末列追加 **PDF 物理页号**（1-indexed，固定不变，与印刷页码 `· 1 ·` 无关）。

**约束**：
- 只做形态 A（每张表一个 CSV，末列加 PDF页数）
- 不动 `candidate.xlsx` / `clean_fields.py` / 主流水线
- 新增独立脚本作为旁路，验证完再考虑合并

**已验证的可行性（基于 `d:/工程造价学习/定额解析/.../装配式建筑工程` 实测）**：

| 验证项 | 结果 |
|---|---|
| `result.json` 是否保留 `content_list` | ✅ 保留，在 `results.<file>.content_list`（JSON 字符串，二次解析） |
| `content_list` 中 `type=table` 项数 vs md_content 中 `<table>` 数 | ✅ 76/76 完全一致（装配式 PDF） |
| 顺序对齐 | ✅ md 第 i 个 `<table>` ↔ content_list 第 i 个 `type=table` |
| `table_body` 字段值 | ✅ 是 `<table>...</table>` 原始 HTML |
| 每张表的 `page_idx` | ✅ **100% 存在**（无缺失） |

---

## 🔴 关键 bug 发现：page_idx 在跨 chunk 时重置

**Explore agent 在 `sample.pdf`（436 页, 5 chunks × 100 页）实测**：

| 项 | 实际值 |
|---|---|
| PDF 总页数 | 436 |
| chunk 数 | 5（每个 100 页） |
| `content_list` 中 `page_idx` 范围 | **0..99**（不是 0..435） |
| `page_idx` 跳变位置 | 4 处（idx 739, 1178, 1574, 1919 都从 99→0） |
| 每个 chunk 是否独立 0 起算 | ✅ 是 |

**根因**：`parse_chunked.py:222` 的 `merge_chunks()` 直接 `merged_content_list.extend(...)`，**没做全局 page_idx 偏移重编号**。同样 `middle_json` 也是 `merged_middle_pages.extend(...)` 拼接，pdf_info 数组下标也是 chunk 内 0..N-1。

**结论**：

- 装配式 PDF 125 页只分 2 个 chunk → 我之前看到 chunk_001 内的 `page_idx=11` 对应 PDF p.12 **碰巧对**（chunk 起始 = 1）
- **但 chunk_002（PDF p.101-125）的 page_idx 会从 0 重新计**，直接当全局用就错了
- 必须在 `merge_chunks` 收集 `table_pages` 时**同步加全局偏移**

---

## 方案设计

### 数据流

```
PDF 文件 (125 页)
   │
   ▼
[矿工U OCR] → 每页生成:
  - md_content 字符串 (含 <table>, 扁平流)
  - content_list JSON 数组 (每项带 page_idx, 但 page_idx 是 chunk 内偏移)
   │
   ▼
[parse_chunked.py:merge_chunks] 改: 
  1. 解析每个 chunk 文件名得到该 chunk 在 PDF 中的物理页范围
     (例 chunk_002_pages_101-125 → [101, 125])
  2. 从 content_list 抽 type=table 项的 page_idx
  3. 重编号: global_pdf_page = page_idx + (chunk_start - 1) + 1 = page_idx + chunk_start
     (例 chunk_002 内 page_idx=0 → 0 + 101 = 101)
  4. 写进 result.json 新字段 results.<file>.table_pages = [12, 12, 22, 23, 23, 24, ..., 102, 102, ...]
   │
   ▼
[scripts/extract_with_pdf_page.py] 新建: 
  - 读 md_content + result.json.table_pages
  - 按 <table> 出现顺序, 末列追加 table_pages[i] (PDF 物理页)
  - 每张表一个 CSV: out/with_pdf_page/table_001.csv ... table_076.csv
  - 附加 out/with_pdf_page/pdf_page_index.txt (PDF p.X → 表名 索引)
```

### 关键决策点

| # | 决策 | 选定 | 理由 |
|---|------|------|------|
| 1 | 字段名 | `table_pages` | 与 `content_list` / `middle_json` 命名风格一致；语义清晰 |
| 2 | 写入位置 | `results.<file>.table_pages` | 跟 `md_content` / `content_list` 平级；不嵌套方便消费者读 |
| 3 | 1-indexed vs 0-indexed | **1-indexed** | 人眼看 PDF 时说"第 12 页"是 1-indexed |
| 4 | 缺 page_idx 时填什么 | `None` (JSON 序列化后 `null`) | 消费者显式检查 |
| 5 | content_list / middle_json 是否同步重编 | **不重编**（只重编 table_pages） | 用户要求只做形态 A，不动其他消费者 |
| 6 | 旧 result.json（无 table_pages 字段）兼容 | `table_pages = []` 兜底 | 脚本不抛错；CSV 末列填空 |
| 7 | 表格 HTML 解析器复用 | **`extractors/sc/extract_quota.py:160 parse_table()`**（公开） | 已实现的 rowspan/colspan 处理；不重写 |
| 8 | parse_table 是否被外部脚本直接 import | **否**（`external/` 下无 `__init__.py`）→ 用 **importlib** | 仓库现有做法，见 `pipeline.py:64-78 _import_md_extract_module` |
| 9 | province 选择 | **硬编码 `sc`**（sc 四川 / cq 重庆 二选一，本轮用 sc 验证） | sc 版路径短；装配式 PDF 是四川 |
| 10 | CSV 表头第一行 | **不写表头**（复用 parse_table 返回无表头数据行） | 简单；用户手工核对时不依赖表头 |
| 11 | 索引文件格式 | `.txt`（人眼可读） | JSON 反而要多一步操作 |
| 12 | 失败 fallback（OCR 漏表） | **数量不一致时打 warning + 后续表填 None** | 不阻塞；让用户看到 warning 自己判断 |

---

## 改动清单（2 个脚本）

### 改 1: `quota/parser/external/mineru_pdf_parse/scripts/parse_chunked.py`

**位置**：`merge_chunks()` 函数 [line 203-246](../file_asset_service/app/quota/parser/external/mineru_pdf_parse/scripts/parse_chunked.py#L203)

**改前**：直接 `merged_content_list.extend(...)`，无 page_idx 重编号

**改后**：在 extend content_list 的循环里**新增** table_pages 累计逻辑 + page_idx 全局偏移

```python
def merge_chunks(chunks: list[Path], result_paths: list[Path], pdf: Path, out_dir: Path):
    """合并所有 chunk 的 md_content 成总 .md, 并触发合并 render.

    v0.7 新增: 同步收集 results.<file>.table_pages 数组, 给每张表打全局 PDF 物理页号.
    由于矿工U content_list 的 page_idx 是 chunk 内偏移 (跨 chunk 会重置为 0),
    这里按 chunk 文件名解析 PDF 物理页范围, 重新计算全局页号:
        global_pdf_page = page_idx + chunk_start_pdf_page
    """
    print(f"\n📦 合并 {len(result_paths)} 个 chunk ...")
    merged_md: list[str] = []
    merged_content_list: list[Any] = []
    merged_middle_pages: list[Any] = []
    merged_table_pages: list[int | None] = []  # ← 新增
    merged_task_id = "merged-" + "-".join(p.parent.name for p in chunks[:3])

    import re
    _PAGE_RE = re.compile(r"_pages_(\d+)-(\d+)")

    for chunk_pdf, result_path in zip(chunks, result_paths):
        data = json.loads(result_path.read_text(encoding="utf-8"))
        result_key = next(iter(data["results"]))
        content = data["results"][result_key]

        merged_md.append(content["md_content"])

        # 解析 chunk 文件名得到该 chunk 在 PDF 中的物理页范围
        # 例: chunk_002_pages_101-125.pdf → chunk_start_pdf_page = 101
        m = _PAGE_RE.search(chunk_pdf.name)
        chunk_start_pdf_page = int(m.group(1)) if m else 1

        try:
            cl = json.loads(content["content_list"])
            merged_content_list.extend(cl)
            # 新增: 抽 type=table 的 page_idx, 重编号为全局 PDF 物理页
            for item in cl:
                if isinstance(item, dict) and item.get("type") == "table":
                    pidx = item.get("page_idx", -1)
                    if pidx < 0:
                        merged_table_pages.append(None)
                    else:
                        # page_idx 0-indexed + chunk 起始 PDF 页 (1-indexed)
                        global_page = pidx + chunk_start_pdf_page
                        merged_table_pages.append(global_page)
        except Exception:
            pass

        try:
            merged_middle_pages.extend(json.loads(content["middle_json"]).get("pdf_info", []))
        except Exception:
            pass

    md_path = out_dir / f"{pdf.stem}.md"
    md_path.write_text("\n\n".join(merged_md), encoding="utf-8")
    print(f"  ✅ {md_path}  ({md_path.stat().st_size:,} chars)")

    merged = {
        "task_id": merged_task_id,
        "status": "completed",
        "backend": "merged-chunks",
        "version": "1.0",
        "file_names": [pdf.name],
        "results": {
            pdf.name: {
                "md_content": "\n\n".join(merged_md),
                "content_list": json.dumps(merged_content_list, ensure_ascii=False),
                "middle_json": json.dumps({"pdf_info": merged_middle_pages}, ensure_ascii=False),
                "table_pages": merged_table_pages,  # ← 新增字段
            }
        }
    }
    merged_result = out_dir / "result.json"
    merged_result.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✅ {merged_result}  ({merged_result.stat().st_size:,} bytes)")
    print(f"  📑 共 {len(merged_table_pages)} 张表, 物理页范围: {min(p for p in merged_table_pages if p)}-{max(p for p in merged_table_pages if p)}")
```

**改动量**：~25 行新增（不影响原有逻辑，老消费者不读 `table_pages` 无破）

### 新建: `scripts/extract_with_pdf_page.py`

**位置**：`scripts/extract_with_pdf_page.py`（仓库根 `scripts/` 已存在，含 9 个文件）

**输入参数（CLI）**：
- `--md` (required): 合并后的 .md 路径
- `--result-json` (required): 合并后的 result.json 路径
- `--out-dir` (required): CSV 输出目录
- `--province` (optional, default `sc`): 选 sc/cq 决定调哪个 extractors 子模块

**输出**：
```
out-dir/
├── table_001.csv ... table_076.csv   (每张表一份, 末列 PDF页数)
└── pdf_page_index.txt                (PDF p.X → 表名 索引)
```

**核心结构**：
- `load_table_pages(result_json_path)` → `list[int | None]`
- `extract_all_tables(md_text)` → `list[str]` (用 `r"<table[^>]*>.*?</table>"` + `re.S`)
- `parse_table_for_csv(table_html, province="sc")` → `list[list[str]]` (importlib 加载 parse_table)
- `write_table_csv(rows, pdf_page, out_path)`
- `build_page_index(table_meta, out_path)` → 写 `.txt`
- `main()` + `argparse`

**关键代码片段（核心 30 行）**：

```python
"""extract_with_pdf_page — 把 PDF 的每张定额表抽成独立 CSV, 末列加 PDF 物理页号.

不动 candidate.xlsx / clean_fields.py / 主流水线; 纯旁路脚本.
"""
import argparse, csv, json, re, sys
from pathlib import Path

def load_table_pages(rj_path):
    data = json.loads(rj_path.read_text(encoding="utf-8"))
    file_key = next(iter(data["results"]))
    return data["results"][file_key].get("table_pages", [])

def extract_all_tables(md_text):
    return re.findall(r"<table[^>]*>.*?</table>", md_text, re.DOTALL)

def parse_table_for_csv(table_html, province="sc"):
    """importlib 动态加载 extractors/{province}/extract_quota.parse_table.
    返回 (grid, total_cols, raw_rows, cell_cols) 中取 grid (list[list[str]]).
    """
    import importlib.util as _ilu
    proj_root = Path(__file__).resolve().parent.parent
    extract_path = proj_root / "quota" / "parser" / "external" / "quota_md_to_csv_v2" / "extractors" / province / "extract_quota.py"
    # 必须把 quota_md_to_csv_v2/ 加入 sys.path 以解析内部 from extractors._common import ...
    parent_dir = str(extract_path.parent.parent.parent)  # quota_md_to_csv_v2 的父目录 = external/
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)
    spec = _ilu.spec_from_file_location(f"_extract_quota_{province}", str(extract_path))
    mod = _ilu.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    grid, total_cols, raw_rows, cell_cols = mod.parse_table(table_html)
    return grid

def write_table_csv(rows, pdf_page, out_path):
    if not rows:
        return 0
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        for r in rows:
            w.writerow(list(r) + [pdf_page if pdf_page is not None else ""])
    return len(rows)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", required=True)
    ap.add_argument("--result-json", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--province", default="sc", choices=["sc", "cq"])
    args = ap.parse_args()

    md_text = Path(args.md).read_text(encoding="utf-8")
    table_pages = load_table_pages(Path(args.result_json))
    tables = extract_all_tables(md_text)
    out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] md <table>: {len(tables)}, table_pages: {len(table_pages)}")
    if len(tables) != len(table_pages):
        print(f"[WARN] 数量不一致! 后续表的页码可能错位 (短的填 None)")

    meta = []
    for i, html in enumerate(tables, 1):
        pdf_page = table_pages[i-1] if i-1 < len(table_pages) else None
        csv_path = out_dir / f"table_{i:03d}.csv"
        try:
            grid = parse_table_for_csv(html, args.province)
            n_rows = write_table_csv(grid, pdf_page, csv_path)
        except Exception as e:
            print(f"[ERR] table_{i:03d}: {e}")
            n_rows = 0
        meta.append({"idx": i, "pdf_page": pdf_page, "csv": csv_path.name, "rows": n_rows})
        print(f"[OK] table_{i:03d}.csv PDF={pdf_page} rows={n_rows}")

    # 写索引
    by_page = {}
    for m in meta:
        if m["pdf_page"] is None: continue
        by_page.setdefault(m["pdf_page"], []).append(m["csv"])
    lines = [f"总表数: {len(meta)}", f"PDF 物理页(含表): {len(by_page)}", ""]
    for p in sorted(by_page):
        lines.append(f"PDF p.{p:>3d} → {', '.join(by_page[p])}")
    (out_dir / "pdf_page_index.txt").write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[DONE] {len(tables)} CSV → {out_dir}")

if __name__ == "__main__":
    main()
```

**改动量**：~120 行新建（含 importlib 动态加载逻辑）

---

## 不动的清单（明确说）

| 不动 | 理由 |
|------|------|
| `quota_md_to_csv_v2/extractors/sc/extract_quota.py` 的 parse_table | 复用，不改 |
| `quota_md_to_csv_v2/extract_quota.py` (薄壳) | 不需要，本脚本直接 import 子模块 |
| `quota_parser/pipeline.py` | 主流水线不动 |
| `clean_fields.py` | 不动 schema |
| 现有 `candidate.xlsx` / `reviewed.xlsx` | 已有产物不被改动 |
| `parse_pdf.py` | 单 chunk 路径，与 merge_chunks 无关 |
| `render.py` | 只渲染 md_content，不关心页码 |
| API 路由 / 前端 | 与本次讨论无关 |
| `content_list` / `middle_json` 原内容 | **故意不改**，避免破坏其他消费者；只新增 `table_pages` 字段 |

---

## 验证步骤（端到端）

### Step 1: 改 parse_chunked.py

只动 `merge_chunks` 函数（~25 行新增）。

### Step 2: 不重跑 OCR，从已有 chunks 合成 merged result.json（节省 5-10 分钟）

装配式 PDF 已经有 chunk_001 / chunk_002 的 result.json，**不用重跑 OCR**——直接用 Python 模拟 merge_chunks 新逻辑合成：

```bash
PY=/d/miniconda3/envs/file-asset/python.exe

$PY -c "
import json, re
from pathlib import Path

ROOT = Path('d:/工程造价学习/定额解析/《四川省建设工程工程量清单计价定额——装配式建筑工程》')
CHUNKS_DIR = ROOT / 'chunks'

table_pages = []
content_list_all = []
md_parts = []

_PAGE_RE = re.compile(r'_pages_(\d+)-(\d+)')

# 按文件名顺序处理
for chunk_dir in sorted(CHUNKS_DIR.glob('chunk_*')):
    rj_path = chunk_dir / 'result.json'
    pdf_name = next(chunk_dir.glob('*.pdf')).name
    m = _PAGE_RE.search(pdf_name)
    chunk_start = int(m.group(1)) if m else 1
    
    data = json.loads(rj_path.read_text(encoding='utf-8'))
    content = data['results'][next(iter(data['results']))]
    
    md_parts.append(content['md_content'])
    
    cl = json.loads(content['content_list'])
    content_list_all.extend(cl)
    for item in cl:
        if isinstance(item, dict) and item.get('type') == 'table':
            pidx = item.get('page_idx', -1)
            table_pages.append(pidx + chunk_start if pidx >= 0 else None)

# 写新 result.json (覆盖原来的)
print(f'合成 table_pages: {len(table_pages)} 项')
print(f'前 10 项: {table_pages[:10]}')
print(f'后 10 项: {table_pages[-10:]}')
print(f'物理页范围: {min(p for p in table_pages if p)} - {max(p for p in table_pages if p)}')
"
```

预期输出（装配式 PDF）：
- 76 项
- 前几项应在 12-22 范围（chunk_001 内部）
- 物理页最大值应 ≤ 125（PDF 总页数）

### Step 3: 把合成结果写回 result.json

将 Step 2 的 table_pages 列表写进 `results.<file>.table_pages` 字段（不破坏原有字段）：

```python
import json
ROOT = Path('d:/...')
rj_path = ROOT / 'result.json'
data = json.loads(rj_path.read_text(encoding='utf-8'))
file_key = next(iter(data['results']))
data['results'][file_key]['table_pages'] = table_pages
rj_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
```

### Step 4: 跑新建脚本

```bash
$PY scripts/extract_with_pdf_page.py \
    --md "d:/.../《装配式建筑工程》.md" \
    --result-json "d:/.../result.json" \
    --out-dir "d:/.../with_pdf_page"
```

预期：
- `[INFO] md <table>: 76, table_pages: 76`
- 输出 76 个 `table_NNN.csv` + `pdf_page_index.txt`
- 无 `[WARN]` 数量不一致

### Step 5: 人工核对（5 个抽样点）

打开 PDF，翻到指定物理页，验证 CSV 末列的 PDF页数与实际位置一致：

| 抽样 | 期望 CSV 名 | 期望 PDF页数 | 验证方法 |
|---|---|---|---|
| 1 | table_001.csv / table_002.csv | **12** | PDF p.12 翻看海拔系数表 |
| 2 | table_003.csv | **22** | PDF p.22 找 MA0001 |
| 3 | table_004.csv / table_005.csv | **23** | PDF p.23 找 MA0002/MA0003 + MA0004 |
| 4 | table_076.csv | **111 / 125** | PDF 末尾页（看是否有表） |
| 5 | 随机中段表 | 任意 | 翻 PDF 验证 |

**关键验证**：table_pages 数组的最大值是否 ≤ PDF 实际总页数（125）。

### Step 6: 风险点验证

- [ ] **数量一致性**：md `<table>` 数 == table_pages 数（装配式 PDF 应是 76/76）
- [ ] **PDF 物理页范围**：table_pages 最大值 ≤ 125
- [ ] **跨 chunk 重编号**：chunk_002 的表（如果有）page_idx 应正确偏移（不再从 0 重置）
- [ ] **同页多张表**：PDF p.23 应有 2 个 CSV 都标 23
- [ ] **跨页大表**：如果有，被拆成 2 个 CSV 各记各的页码（不合并）

---

## 已知限制 / 后续可优化

1. **content_list / middle_json 原样保留**，page_idx 仍是 chunk 内偏移 → 其他消费者读这两个字段仍会拿到错位页码。本轮**故意不改**（用户要求只做形态 A，不动其他消费者）。后续如果要修，需要单独 PR 重编这两个字段的 page_idx。

2. **跨页大表不合并**：一张表跨 p.30-p.31 → 2 个 CSV 各记 PDF=30 和 PDF=31。人工校对时自己合并。

3. **province 硬编码 sc**：本轮脚本固定加载 `extractors/sc/extract_quota.py`。重庆 PDF 跑要加 `--province cq`。未来可加自动检测。

4. **parse_table 解析失败时整张表跳过**：try/except 包住，单表失败不影响其他表。

5. **CSV 无表头第一行**：本轮简化不做表头；如果用户需要可加 `--with-header` flag。

6. **不重跑 OCR**：本轮验证用现有 chunk_001/002 的 result.json 合成，不触发矿工U。生产环境每次新 PDF 会自然走新 merge_chunks 流程，自动得到 table_pages。

---

## Critical Files

**修改**：
- `D:\工程造价学习\data_lake0714\data_lake0714\quota\parser\external\mineru_pdf_parse\scripts\parse_chunked.py` — `merge_chunks` 函数加 ~25 行

**新建**：
- `D:\工程造价学习\data_lake0714\data_lake0714\scripts\extract_with_pdf_page.py` — 独立 CSV 生成脚本

**不修改**（明确列出）：
- `quota/parser/quota_parser/pipeline.py`
- `quota/parser/quota_md_to_csv_v2/extract_quota.py`（薄壳）
- `quota/parser/quota_md_to_csv_v2/extractors/sc/extract_quota.py`（parse_table 被复用，不改）
- `quota/parser/quota_md_to_csv_v2/extractors/cq/extract_quota.py`
- `quota/parser/external/mineru_pdf_parse/scripts/parse_pdf.py`
- `quota/parser/external/mineru_pdf_parse/scripts/render.py`
- 任何 `candidate.xlsx` / `reviewed.xlsx` / `clean_fields.py`
- API 路由 / 前端

---

## 实施 commit 拆分（建议）

| Commit | 内容 | 验证 |
|---|---|---|
| **Commit 1** | `parse_chunked.py:merge_chunks` 加 `table_pages` 收集 + page_idx 全局偏移 | 跑 parse_chunked.py CLI（不改实际跑 PDF 流程）；手工合成 result.json 看 table_pages 范围 |
| **Commit 2** | 新建 `scripts/extract_with_pdf_page.py` | 跑一次装配式 PDF，对照 `pdf_page_index.txt` 抽查 5 个 PDF 物理页 |

---

## 等用户答复的 5 个问题（实施前必答）

1. **OCR 是否重跑？** 装配式 PDF 已跑过。改 merge_chunks 后**是否需要重跑 OCR** 才能验证？
   - **推荐**：不重跑，用现有 chunk_001/002 合成 merged result.json 验证（节省 5-10 分钟）

2. **CSV 表头要不要写？** parse_table 输出无表头数据行；加 PDF 页数末列后，第 1 行也是数据行。要不要写表头（`["材料名称", "规格型号", ..., "PDF页数"]`）？

3. **`pdf_page_index.txt` 格式**OK 吗？示例：
   ```
   总表数: 76
   PDF 物理页(含表): 14
   
   PDF p. 12 → table_001.csv, table_002.csv
   PDF p. 22 → table_003.csv
   ...
   ```

4. **失败 fallback**：OCR 漏识别某张表（table_pages 比 md `<table>` 短 1 项），你接受"warning + 后续表填 None"吗？还是"立即报错退出"？

5. **是否同步写一个简单的单元测试？** 比如验证 table_pages 重编号逻辑（chunk_002 内 page_idx=0 → 101）。

---

## 关键事实总结（确保实施时不再走弯路）

| 事实 | 来源 |
|---|---|
| `parse_table_html` 不存在，真实函数名 `parse_table` | Explore agent 验证 |
| `parse_table` 在 `extractors/sc/extract_quota.py:160` 和 `extractors/cq/extract_quota.py:167` | Explore agent 验证 |
| `external/` 下无 `__init__.py`，必须 importlib 动态加载 | Explore agent 验证 |
| `content_list` 中 page_idx 是 chunk 内偏移，跨 chunk 重置为 0 | Explore agent 在 sample.pdf 实测（436 页 / 5 chunks） |
| 装配式 PDF 76/76 数量对齐，page_idx 在 chunk_001 内部值 = PDF 物理页 - 1 | 之前对话验证 |
| merge_chunks 必须用 chunk 文件名（`chunk_002_pages_101-125`）反推全局偏移 | 本轮方案 |
| merge_chunks 改后**不动 content_list / middle_json 原值**（避免破坏其他消费者） | 本轮决策 |
| 仅新增 `results.<file>.table_pages` 字段（1-indexed 全局 PDF 物理页） | 本轮决策 |
