# 新增省份 Extractor 落地指南

> **目的**：5 步接入新省份（31 省 + 深圳市），worker 自动识别可解析省份。
> **范围**：本文档只描述新增落地，不讨论 sc/cq 既有实现。

---

## 1. 双胞胎目录陷阱（先看这条）

仓库里有两份**视觉相同**的 extractor 框架：

| 目录 | 实际被谁加载 |
|---|---|
| `quota/parser/external/quota_md_to_csv_v2/` (underscore) | **pipeline 真实 import**（`pipeline.py:117` → `config.py:103`） |
| `quota/parser/external/quota-md-to-csv-v2/` (hyphen) | **CLI / 技能入口**，`subprocess.run` 转发 |

**两份必须同步**：缺 underscore → worker 误判"未配置解析脚本"；缺 hyphen → CLI 报 FileNotFoundError。

---

## 2. 5 步流程

### Step 1 — 选 short code

沿用车牌简称（豫=yu / 鄂=hu / 湘=xi / 陕=snx 区别晋=sx）。**避开 sc/cq**。必须在 32 项 `_UPLOAD_PROVINCE_MAP` 里（与上传入口、worker 守卫、API schema 三方对齐）。

### Step 2 — 改 3 处注册表

| # | 文件 | 改什么 |
|---|---|---|
| 2.1 | `quota/parser/quota_parser/config.py` L68-79 | `PROVINCE_KEYWORDS` + `PROVINCE_NAMES` |
| 2.2 | `quota/parser/external/quota_md_to_csv_v2/extract_quota.py` L90-98 | 同 2.1 |
| 2.3 | `quota/parser/external/quota-md-to-csv-v2/extract_quota.py` L68-77 | 同 2.1 |

```python
# config.py
PROVINCE_KEYWORDS = {
    "sc": ("四川", "川建"),
    "cq": ("重庆",),
    "x":  ("湖北", "鄂"),  # 新增
}
PROVINCE_NAMES = {
    "sc": "四川",
    "cq": "重庆",
    "x":  "湖北",         # 新增
}
```

**关键词至少 1 个全名**（"湖北"），可加 1 个简称（"鄂"）。互不重叠。

### Step 3 — 写省份主脚本

```bash
mkdir -p quota/parser/external/quota_md_to_csv_v2/extractors/x
cp quota/parser/external/quota_md_to_csv_v2/extractors/sc/extract_quota.py \
   quota/parser/external/quota_md_to_csv_v2/extractors/x/extract_quota.py
# 改 SECTION_RE / LATEX_MAP / 数字格式
```

**契约**（pipeline 唯一调到的接口）：

```python
def process_md_file(md_path: str) -> tuple[list[list[str]], list[str]]:
    """
    Returns:
        rows:   list[list[str]]，10 列 (类型|项目编码|名称|项目特征|计量单位|消耗量|基价|验证|标准换算|来源)
        issues: list[str]，解析告警（人工 review 用）

    副作用: 可选写 CSV / issues.md 到 md_path 同目录
    失败:  抛 ValueError / RuntimeError → pipeline 标 job='failed'
    """
```

### Step 4 — 双胞胎同步

```bash
cp quota/parser/external/quota_md_to_csv_v2/extractors/x/extract_quota.py \
   quota/parser/external/quota-md-to-csv-v2/extractors/x/extract_quota.py
```

未来 Windows 下 .git 不跟踪 symlink，保持双胞胎副本。

### Step 5 — 验证

**5.1 离线 CLI**：

```bash
PY=/d/miniconda3/envs/file-asset/python.exe
$PY quota/parser/external/quota_md_to_csv_v2/extract_quota.py --list-province  # 必须有 x
$PY quota/parser/external/quota_md_to_csv_v2/extract_quota.py <md> --province x --keep-csv --no-finalize
# 检查 <md>同目录/<stem>_待审核.csv（UTF-8 BOM，Excel 双击不乱码）
```

**5.2 在线 worker**：

```bash
bash file_asset_service/scripts/run_quota_parser_worker.sh restart
# 上传 x 省 PDF（province=x）→ worker claim → _guard_has_parser('x') 返 True → 跑 pipeline
# 验证 DB：
$PY -c "
from app.database import get_engine
from sqlalchemy import text
print(get_engine().execute(text('''
    SELECT parse_status, parse_phase, candidate_xlsx_key
    FROM archive WHERE title='<x PDF title>'
''')).first())
"
# 期望: parse_status='parsed', candidate_xlsx_key NOT NULL
```

---

## 3. 撤销

```bash
rm -rf quota/parser/external/quota_md_to_csv_v2/extractors/x/
rm -rf quota/parser/external/quota-md-to-csv-v2/extractors/x/
# 3 处注册表删 entry，重启 worker
```

`archive.parse_profile='x'` 字段保留，守卫重新走 skipped_no_parser，不报错。

---

## 4. 常见误区

| 现象 | 原因 |
|---|---|
| 改注册表但 worker 还是 skipped_no_parser | 99% 双胞胎只改 1 份。守卫扫 underscore。 |
| `ValueError: 未知省份 code: 'x'` | `config.py:PROVINCE_KEYWORDS` 漏改 |
| pipeline 跑通但 CSV 空 | `extractors/x/extract_quota.py` 的 `SECTION_RE` 没匹配上 x 省章节格式。看 `--keep-csv` 产出的 `issues.md` |
| 两个省份 extractors 能合并吗 | 不能。`extractors._common` 强制每个省份独立逻辑。**复制再改**是唯一做法 |

---

## 5. 关联

- `quota/README.md §8` — Profile 字段语义
- `quota/parser/quota_parser/config.py:66-83` — 顶层 PROVINCE_KEYWORDS
- `quota/parser/external/quota_md_to_csv_v2/extract_quota.py:277` — `_load_province_module`
- `quota/parser/quota_parser_worker.py:678` — `_guard_has_parser`（与 _load_province_module 走同目录）
