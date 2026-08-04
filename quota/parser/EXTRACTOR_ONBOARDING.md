# 新增省份 Extractor 落地指南

> **目的**：让任何省份（31 省 + 深圳市）能在 5 步内接入 pipeline，自动被 worker 解析。
> **范围**：本文档只描述**新增省份**的落点、改动清单、契约、复盘，不讨论既有 sc/cq 的实现细节。
> **读者**：要接入新省份的工程师 / 维护者。

---

## 1. 落地前先做对的事

**双胞胎目录陷阱**：仓库里有两份**视觉相同**的 extractor 框架：

| 目录 | 路径 | 实际被谁加载 |
|---|---|---|
| **underscore 版**（活） | `quota/parser/external/quota_md_to_csv_v2/` | `quota_parser/pipeline.py:117` 走 `QUOTA_MD_TO_CSV_DIR`（`config.py:103`）—— **这就是 worker 真实跑的** |
| **hyphen 版**（薄壳） | `quota/parser/external/quota-md-to-csv-v2/` | 用于 CLI、`subprocess.run` 转发、技能演示入口 |

**两份目前完全双胞胎**：sc/cq 的两份 `extract_quota.py` 内容一致。但**只有 underscore 版有 `_load_province_module`**（line 277），**只有 underscore 版会被 worker 真正 import**。

**铁律**：新增省份 extractor 时，**两份 extractors/ 目录里都要写**。任何一份缺失都会导致：
- 缺 underscore 版 → `_guard_has_parser` False → 误导"未配置解析脚本"
- 缺 hyphen 版 → CLI 跑 `python extract_quota.py <md> --province x` 报 FileNotFoundError（hyphen 版 `subprocess.run` 转发到本地 `extractors/x/extract_quota.py`）

---

## 2. 改动清单（5 步，全文搜索"GLOBAL COUNT"看实际改动量）

### Step 1 — 选 short code

| 规则 | 例 |
|---|---|
| 沿用车牌简称（首选） | 豫=yu / 鄂=hu / 湘=xi / 闽=fj / 鲁=sd |
| 长名拼音做 2 字母 | 重庆=cq / 四川=sc / 陕西=snx（避开山西=sx） |
| 必须在 32 项 `_UPLOAD_PROVINCE_MAP` 里 | 上传入口、worker 守卫、API schema 三方对齐 |

**绝对禁忌**：`sc`（四川）/ `cq`（重庆）已被占用，不要给新省份用。

### Step 2 — 改 3 个注册表

**3 处都改，缺一不可**（顺序无所谓，但**一次 commit**）：

| # | 文件 | 改动 | 位置 |
|---|---|---|---|
| 2.1 | `quota/parser/quota_parser/config.py` | `PROVINCE_KEYWORDS` 加 `"x": ("关键词1", "关键词2")`<br>`PROVINCE_NAMES` 加 `"x": "中文名"` | L68-79 |
| 2.2 | `quota/parser/external/quota_md_to_csv_v2/extract_quota.py` | 同 2.1 改（**双胞胎同步**） | L90-98 |
| 2.3 | `quota/parser/external/quota-md-to-csv-v2/extract_quota.py` | 同 2.1 改（**双胞胎同步**） | L68-77 |

**关键词怎么选**：
- 至少 1 个省份全名（"湖北"、"陕西"）
- 可选 + 1 个省份简称（"鄂"、"陕"）—— 兜底老 PDF 路径
- 关键词必须**互不重叠**（"川建"和"重庆"会撞 → 优先级排序，关键词少的在前）

### Step 3 — 写省份主脚本

两份目录各写一份：

```
quota/parser/external/quota_md_to_csv_v2/extractors/x/extract_quota.py
quota/parser/external/quota-md-to-csv-v2/extractors/x/extract_quota.py   ← 双胞胎副本
```

**契约**（这是 pipeline 唯一会调到的接口）：

```python
# 必备导出
def process_md_file(md_path: str) -> tuple[list[list[str]], list[str]]:
    """
    Args:
        md_path: 含 HTML <table> 的 .md 文件路径
    Returns:
        (rows, issues)
          rows:   list[list[str]]，每行 = 10 列表格
                   (类型|项目编码|名称|项目特征|计量单位|消耗量|基价/单价|验证|标准换算|标准换算来源)
          issues: list[str]，解析过程中的告警（人工 review 用）

    副作用:
        可选：写 CSV / issues.md 到 md_path 同目录（pipeline 不强制要求）

    异常:
        失败抛 ValueError / RuntimeError，pipeline 会捕获并标 job='failed'
    """
```

**复用策略**（推荐新手）：
1. `cp extractors/sc/extract_quota.py extractors/x/extract_quota.py`
2. 改 `SECTION_RE` / `PROJECT_ID_RE`（章节正则：四川用 `A.1.2` / 重庆用 `C.D.E`）
3. 改 `LATEX_MAP`（如果新省份有特殊数学符号）
4. 改数字千分位、小数点处理（部分省份用 `1.234,56` 欧洲格式）
5. 改空白行/空 cell 的 forward-fill 规则

**全新写法**（省份结构差异大）：直接参考 `extractors/_common/narrative_parser.py` 的工具函数，从零写。

### Step 4 — 双胞胎同步

对 `quota/parser/external/quota-md-to-csv-v2/extractors/x/extract_quota.py`：

```bash
# 拷贝 Step 3 的脚本
cp quota/parser/external/quota_md_to_csv_v2/extractors/x/extract_quota.py \
   quota/parser/external/quota-md-to-csv-v2/extractors/x/extract_quota.py
```

**未来更好的解法**：把 `quota-md-to-csv-v2/extractors/` 改成 symlink 指 `quota_md_to_csv_v2/extractors/`，但目前 Windows 下 .git 不跟踪 symlink，**保持双胞胎副本**。

### Step 5 — 验证

#### 5.1 离线 (CLI)

```bash
PY=/d/miniconda3/envs/file-asset/python.exe

# 5.1.1 列出当前省份（必须有 x）
$PY quota/parser/external/quota_md_to_csv_v2/extract_quota.py --list-province

# 5.1.2 跑一个 x 省的 PDF 转出来的 .md
$PY quota/parser/external/quota_md_to_csv_v2/extract_quota.py \
    /path/to/x.pdf.md \
    --province x \
    --keep-csv \
    --no-finalize
#     ───────  -keep-csv 看 CSV 调试
#     ───────────────  -no-finalize 跳过 5 步（先看 CSV 是否对）
```

**期望**：
- CSV 落到 `<md_path>同目录/<stem>_待审核.csv`，**UTF-8 BOM**（Excel 双击不乱码）
- issues.md 也落到同目录
- 无 traceback

#### 5.2 在线 (worker)

```bash
# 5.2.1 重启 worker 让 _guard_has_parser 重新扫目录
bash file_asset_service/scripts/run_quota_parser_worker.sh restart

# 5.2.2 上传一篇 x 省的 PDF
# → 走 POST /api/data-lake/quota/upload?province=x
# → quota_parse_job 入队
# → worker claim → _guard_has_parser('x') 返 True
# → pipeline 调用 extractors/x/extract_quota.py
# → 5 步 finalize → register_parse_artifact 写 parse_candidate_xlsx 到 MinIO

# 5.2.3 验证 DB
$PY -X utf8 -c "
from app.database import get_engine
from sqlalchemy import text
e = get_engine()
row = e.execute(text('''
    SELECT parse_status, parse_phase, parse_parser_version, parse_metrics
    FROM archive WHERE title='<x PDF title>'
''')).first()
print(row)
"

# 期望: parse_status='parsed', parse_phase='stage_a',
#       parse_metrics.chunks_done >= 1, candidate_xlsx_key NOT NULL
```

#### 5.3 端到端 TODO

完成 5.1 + 5.2 后：

- [ ] 在前端 quota 域列表能看到该档案状态徽章从「未解析」→「已完成」
- [ ] 点击「下载 final.xlsx」下载产出的 xlsx（52 列 16 sheet）
- [ ] 抽样 5 行人工核对：项目编码、计量单位、消耗量

---

## 3. 撤销 / 回滚

如果接错了省份：

```bash
# 1. 删 extractors 目录
rm -rf quota/parser/external/quota_md_to_csv_v2/extractors/x/
rm -rf quota/parser/external/quota-md-to-csv-v2/extractors/x/

# 2. 3 个注册表删 entry
#   - config.py:68-79
#   - quota_md_to_csv_v2/extract_quota.py:90-98
#   - quota-md-to-csv-v2/extract_quota.py:68-77

# 3. 重启 worker
bash file_asset_service/scripts/run_quota_parser_worker.sh restart

# 4. 已入库的 x 档案：worker 会重新走 skipped_no_parser 路径
#    详情页右下角 toast 提示「未配置解析脚本」
```

**已入 DB 的 archive.parse_profile='x' 字段保留**，重启 worker 后该档案重新入队时被守卫识别为 skipped_no_parser，不报错。

---

## 4. 关联文档

- `quota/README.md §8` — Profile 字段语义（32 省 `_UPLOAD_PROVINCE_MAP` 权威表）
- `quota/DB_SCHEMA.md` — `ck_quota_parse_job_profile` 同步扩到 32 profile 的自愈 SQL
- `quota/parser/quota_parser/config.py:66-83` — pipeline 顶层 PROVINCE_KEYWORDS / NAMES
- `quota/parser/external/quota_md_to_csv_v2/extract_quota.py:277` — `_load_province_module` 动态 import

## 5. 已知陷阱（FAQ）

**Q: 改了 PROVINCE_KEYWORDS 但 worker 还是 skipped_no_parser？**
A: 99% 是双胞胎目录只改了 1 份。先 `_guard_has_parser` 扫的是 underscore 版；如果只在 hyphen 版加了 extractors 子脚本，worker 看不到。

**Q: worker 报 `ValueError: 未知省份 code: 'x'`？**
A: pipeline.py 顶层 `_resolve_province` 找不到 `x`。检查 `config.py:PROVINCE_KEYWORDS` 是否漏改。

**Q: pipeline 跑通了但 CSV 是空的？**
A: 大概率是 `extractors/x/extract_quota.py` 的 `SECTION_RE` 没匹配上 x 省的章节标题模式。`--no-finalize --keep-csv` 跑一次，看 `issues.md` 里报什么。

**Q: x 省的 extract_quota.py 与 sc 几乎一样，能不能 symlink 复用？**
A: 不行。`extractors._common` 的 `narrative_parser` 假定每个省份独立调解析逻辑；强制复用会让回归测试一团糟。**复制再改**是当前唯一的做法。

**Q: 加了 32 省之外的省份（如香港）？**
A: 不在本文档范围。`_UPLOAD_PROVINCE_MAP` 是 32 项，upload 入口会拒收。`quota_api.py:_UPLOAD_PROVINCE_MAP` 是入参白名单权威源。

---

## 6. 变更历史

| 版本 | 日期 | 改动 | 涉及 commit |
|---|---|---|---|
| v0.8 | 2026-08-04 | 初版：双胞胎目录、5 步流程、契约定义 | 4cbe644 (feat) + f89edba (fix) + 本 SPEC |
